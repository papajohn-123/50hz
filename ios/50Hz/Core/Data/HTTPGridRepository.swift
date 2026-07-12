import Foundation

enum GridAPIError: LocalizedError, Sendable {
    case invalidBaseURL
    case invalidPostcode
    case invalidResponse
    case notModifiedWithoutCache
    case responseTooLarge
    case decoding(String)
    case transport(URLError.Code)
    case httpStatus(code: Int, message: String?, retryAfter: String?)
    case cancelled

    var errorDescription: String? {
        switch self {
        case .invalidBaseURL:
            return "The 50Hz API address is invalid."
        case .invalidPostcode:
            return "Enter a valid UK outward or full postcode."
        case .invalidResponse:
            return "The grid service returned an unreadable response."
        case .notModifiedWithoutCache:
            return "The grid service expected cached data that is not available."
        case .responseTooLarge:
            return "The grid service returned more data than the app can safely process."
        case .decoding:
            return "The grid service returned data from an incompatible contract."
        case .transport(let code):
            switch code {
            case .notConnectedToInternet, .networkConnectionLost:
                return "There is no reliable network connection."
            case .timedOut:
                return "The grid service took too long to respond."
            case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed:
                return "The grid service cannot be reached."
            default:
                return "The grid service could not be reached."
            }
        case .httpStatus(let code, let message, let retryAfter):
            if code == 429 {
                if let retryAfter { return "The grid service is busy. Try again in \(retryAfter) seconds." }
                return "The grid service is busy. Try again shortly."
            }
            if code >= 500 { return "The grid service is temporarily unavailable." }
            if code == 404 { return "This grid feed is not available on the server yet." }
            return message ?? "The grid service rejected the request (\(code))."
        case .cancelled:
            return "The grid refresh was cancelled."
        }
    }
}

actor HTTPGridRepository: GridRepository {
    static let productionBaseURL = URL(string: "https://50hz-api-production.up.railway.app")!

    private enum Endpoint {
        case current
        case timeline
        case events
        case dailyGame
        case region(String)
        case localWindows(postcode: String, durationMinutes: Int)

        var cacheKey: GridCacheKey {
            switch self {
            case .current: .current
            case .timeline: .timeline
            case .events: .events
            case .dailyGame: .dailyGame
            case .region(let postcode): .region(postcode)
            case .localWindows(let postcode, let durationMinutes):
                .localWindows(postcode: postcode, durationMinutes: durationMinutes)
            }
        }
    }

    private let baseURL: URL
    private let session: URLSession
    private let cache: GridDiskCache
    private var currentTask: Task<GridSnapshot, Error>?
    private var timelineTask: Task<GridTimeline, Error>?
    private var eventsTask: Task<[GridEvent], Error>?
    private var dailyGameTask: Task<DailyGame, Error>?
    private var regionTasks: [String: Task<RegionalGridContext, Error>] = [:]
    private var localWindowsTasks: [LocalWindowsRequest: Task<LocalWindowsResponse, Error>] = [:]

    init(
        baseURL: URL = HTTPGridRepository.productionBaseURL,
        session: URLSession = HTTPGridRepository.productionSession(),
        cache: GridDiskCache = .production()
    ) {
        self.baseURL = baseURL
        self.session = session
        self.cache = cache
    }

    nonisolated static func productionSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        configuration.timeoutIntervalForResource = 30
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        configuration.waitsForConnectivity = false
        configuration.httpAdditionalHeaders = [
            "Accept": "application/json",
            "User-Agent": "50Hz-iOS/1.0"
        ]
        return URLSession(configuration: configuration)
    }

    func cachedSnapshot() async -> GridSnapshot? {
        guard let entry = await cache.entry(for: .current) else { return nil }
        do {
            return try GridJSON.decoder.decode(GridSnapshot.self, from: entry.data)
        } catch {
            await cache.remove(.current)
            return nil
        }
    }

    func cachedTimeline() async -> GridTimeline? {
        guard let entry = await cache.entry(for: .timeline) else { return nil }
        do {
            return try GridJSON.decoder.decode(GridTimeline.self, from: entry.data)
        } catch {
            await cache.remove(.timeline)
            return nil
        }
    }

    func cachedRegion(postcode: String) async -> RegionalGridContext? {
        guard let outward = PostcodePrivacy.validatedOutwardCode(from: postcode) else { return nil }
        let key = GridCacheKey.region(outward)
        guard let entry = await cache.entry(for: key) else { return nil }
        do {
            return try GridJSON.decoder.decode(RegionalGridContext.self, from: entry.data)
        } catch {
            await cache.remove(key)
            return nil
        }
    }

    func cachedLocalWindows(postcode: String, durationMinutes: Int) async -> LocalWindowsResponse? {
        guard let outward = PostcodePrivacy.validatedOutwardCode(from: postcode) else { return nil }
        let request = LocalWindowsRequest(postcode: outward, durationMinutes: durationMinutes)
        let key = GridCacheKey.localWindows(
            postcode: request.outwardPostcode,
            durationMinutes: request.durationMinutes
        )
        guard let entry = await cache.entry(for: key) else { return nil }
        do {
            let decoded = try GridJSON.decoder.decode(LocalWindowsResponse.self, from: entry.data)
            guard Self.matches(decoded, request: request) else {
                await cache.remove(key)
                return nil
            }
            return decoded
        } catch {
            await cache.remove(key)
            return nil
        }
    }

    func cachedEvents() async -> [GridEvent]? {
        guard let entry = await cache.entry(for: .events) else { return nil }
        do {
            return try GridJSON.decoder.decode([GridEvent].self, from: entry.data)
        } catch {
            await cache.remove(.events)
            return nil
        }
    }

    func cachedDailyGame() async -> DailyGame? {
        guard let entry = await cache.entry(for: .dailyGame) else { return nil }
        do {
            return try GridJSON.decoder.decode(DailyGame.self, from: entry.data)
        } catch {
            await cache.remove(.dailyGame)
            return nil
        }
    }

    func currentSnapshot() async throws -> GridSnapshot {
        if let currentTask { return try await currentTask.value }

        let task = Task<GridSnapshot, Error> {
            try await Self.fetch(
                endpoint: .current,
                requestURL: try Self.currentURL(baseURL: baseURL),
                session: session,
                cache: cache,
                as: GridSnapshot.self
            )
        }
        currentTask = task
        defer { currentTask = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func timeline() async throws -> GridTimeline {
        if let timelineTask { return try await timelineTask.value }

        let task = Task<GridTimeline, Error> {
            try await Self.fetch(
                endpoint: .timeline,
                requestURL: try Self.timelineURL(baseURL: baseURL, now: Date()),
                session: session,
                cache: cache,
                as: GridTimeline.self
            )
        }
        timelineTask = task
        defer { timelineTask = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func region(postcode: String) async throws -> RegionalGridContext {
        guard let normalized = PostcodePrivacy.validatedOutwardCode(from: postcode) else {
            throw GridAPIError.invalidPostcode
        }
        if let task = regionTasks[normalized] { return try await task.value }

        let task = Task<RegionalGridContext, Error> {
            try await Self.fetch(
                endpoint: .region(normalized),
                requestURL: Self.regionURL(baseURL: baseURL, postcode: normalized),
                session: session,
                cache: cache,
                as: RegionalGridContext.self
            )
        }
        regionTasks[normalized] = task
        defer { regionTasks[normalized] = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func localWindows(postcode: String, durationMinutes: Int) async throws -> LocalWindowsResponse {
        guard let outward = PostcodePrivacy.validatedOutwardCode(from: postcode) else {
            throw GridAPIError.invalidPostcode
        }
        let request = LocalWindowsRequest(postcode: outward, durationMinutes: durationMinutes)
        if let task = localWindowsTasks[request] { return try await task.value }

        let task = Task<LocalWindowsResponse, Error> {
            let endpoint = Endpoint.localWindows(
                postcode: request.outwardPostcode,
                durationMinutes: request.durationMinutes
            )
            let response = try await Self.fetch(
                endpoint: endpoint,
                requestURL: try Self.localWindowsURL(baseURL: baseURL, request: request),
                session: session,
                cache: cache,
                as: LocalWindowsResponse.self
            )
            guard Self.matches(response, request: request) else {
                await cache.remove(endpoint.cacheKey)
                throw GridAPIError.decoding("Local window response did not match its request.")
            }
            return response
        }
        localWindowsTasks[request] = task
        defer { localWindowsTasks[request] = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func events() async throws -> [GridEvent] {
        if let eventsTask { return try await eventsTask.value }

        let task = Task<[GridEvent], Error> {
            try await Self.fetch(
                endpoint: .events,
                requestURL: Self.eventsURL(baseURL: baseURL),
                session: session,
                cache: cache,
                as: [GridEvent].self
            )
        }
        eventsTask = task
        defer { eventsTask = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func dailyGame() async throws -> DailyGame {
        if let dailyGameTask { return try await dailyGameTask.value }

        let task = Task<DailyGame, Error> {
            try await Self.fetch(
                endpoint: .dailyGame,
                requestURL: Self.dailyGameURL(baseURL: baseURL),
                session: session,
                cache: cache,
                as: DailyGame.self
            )
        }
        dailyGameTask = task
        defer { dailyGameTask = nil }

        return try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    func event(id: String) async throws -> GridEvent {
        try await Self.send(
            request: URLRequest(url: Self.eventURL(baseURL: baseURL, id: id)),
            session: session,
            as: GridEvent.self
        )
    }

    func eventExplanation(id: String) async throws -> EventExplanationResponse {
        try await Self.send(
            request: URLRequest(url: Self.eventExplanationURL(baseURL: baseURL, id: id)),
            session: session,
            as: EventExplanationResponse.self
        )
    }

    func ask(_ request: AskGridRequest) async throws -> AskGridAnswer {
        var urlRequest = URLRequest(url: baseURL.appendingPathComponent("v1/ask"))
        urlRequest.httpMethod = "POST"
        urlRequest.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlRequest.httpBody = try GridJSON.encoder.encode(request)
        return try await Self.send(request: urlRequest, session: session, as: AskGridAnswer.self)
    }

    private nonisolated static func currentURL(baseURL: URL) throws -> URL {
        baseURL.appendingPathComponent("v1/grid/current")
    }

    private nonisolated static func timelineURL(baseURL: URL, now: Date) throws -> URL {
        let endpoint = baseURL.appendingPathComponent("v1/grid/timeline")
        guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
            throw GridAPIError.invalidBaseURL
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        components.queryItems = [
            URLQueryItem(name: "from", value: formatter.string(from: now.addingTimeInterval(-24 * 3_600))),
            URLQueryItem(name: "to", value: formatter.string(from: now.addingTimeInterval(24 * 3_600))),
            URLQueryItem(name: "resolution", value: "1800")
        ]
        guard let url = components.url else { throw GridAPIError.invalidBaseURL }
        return url
    }

    private nonisolated static func regionURL(baseURL: URL, postcode: String) -> URL {
        baseURL.appendingPathComponent("v1/regions").appendingPathComponent(postcode)
    }

    private nonisolated static func localWindowsURL(
        baseURL: URL,
        request: LocalWindowsRequest
    ) throws -> URL {
        let endpoint = regionURL(
            baseURL: baseURL,
            postcode: request.outwardPostcode
        ).appendingPathComponent("windows")
        guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
            throw GridAPIError.invalidBaseURL
        }
        components.queryItems = [
            URLQueryItem(name: "durationMinutes", value: String(request.durationMinutes))
        ]
        guard let url = components.url else { throw GridAPIError.invalidBaseURL }
        return url
    }

    private nonisolated static func matches(
        _ response: LocalWindowsResponse,
        request: LocalWindowsRequest
    ) -> Bool {
        response.matches(request)
    }

    private nonisolated static func eventsURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/events")
    }

    private nonisolated static func dailyGameURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/game/today")
    }

    private nonisolated static func eventURL(baseURL: URL, id: String) -> URL {
        baseURL.appendingPathComponent("v1/events").appendingPathComponent(id)
    }

    private nonisolated static func eventExplanationURL(baseURL: URL, id: String) -> URL {
        eventURL(baseURL: baseURL, id: id).appendingPathComponent("explanation")
    }

    private nonisolated static func send<Value: Decodable & Sendable>(
        request: URLRequest,
        session: URLSession,
        as type: Value.Type
    ) async throws -> Value {
        var request = request
        request.timeoutInterval = 30
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            try Task.checkCancellation()
            let (data, response) = try await session.data(for: request)
            try Task.checkCancellation()
            guard let response = response as? HTTPURLResponse else { throw GridAPIError.invalidResponse }
            guard (200..<300).contains(response.statusCode) else {
                let body = try? JSONDecoder().decode(APIErrorBody.self, from: data)
                throw GridAPIError.httpStatus(
                    code: response.statusCode,
                    message: body?.safeMessage,
                    retryAfter: response.value(forHTTPHeaderField: "Retry-After")
                )
            }
            guard !data.isEmpty else { throw GridAPIError.invalidResponse }
            guard data.count <= 2_000_000 else { throw GridAPIError.responseTooLarge }
            do {
                return try GridJSON.decoder.decode(type, from: data)
            } catch {
#if DEBUG
                let endpoint = request.url?.path ?? "unknown endpoint"
                print("50Hz decode failure for \(endpoint): \(String(reflecting: error))")
#endif
                throw GridAPIError.decoding(String(describing: error))
            }
        } catch is CancellationError {
            throw GridAPIError.cancelled
        } catch let error as URLError {
            if error.code == .cancelled { throw GridAPIError.cancelled }
            throw GridAPIError.transport(error.code)
        } catch let error as GridAPIError {
            throw error
        } catch {
            throw GridAPIError.invalidResponse
        }
    }

    private nonisolated static func fetch<Value: Decodable & Sendable>(
        endpoint: Endpoint,
        requestURL: URL,
        session: URLSession,
        cache: GridDiskCache,
        as type: Value.Type
    ) async throws -> Value {
        let cached = await cache.entry(for: endpoint.cacheKey)
        var request = URLRequest(url: requestURL)
        request.httpMethod = "GET"
        request.timeoutInterval = 15
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let etag = cached?.etag { request.setValue(etag, forHTTPHeaderField: "If-None-Match") }
        if let lastModified = cached?.lastModified { request.setValue(lastModified, forHTTPHeaderField: "If-Modified-Since") }

        do {
            try Task.checkCancellation()
            let (data, response) = try await session.data(for: request)
            try Task.checkCancellation()
            guard let response = response as? HTTPURLResponse else { throw GridAPIError.invalidResponse }

            if response.statusCode == 304 {
                guard let cached else { throw GridAPIError.notModifiedWithoutCache }
                do {
                    return try GridJSON.decoder.decode(type, from: cached.data)
                } catch {
                    await cache.remove(endpoint.cacheKey)
                    throw GridAPIError.decoding(String(describing: error))
                }
            }

            guard (200..<300).contains(response.statusCode) else {
                let body = try? JSONDecoder().decode(APIErrorBody.self, from: data)
                throw GridAPIError.httpStatus(
                    code: response.statusCode,
                    message: body?.safeMessage,
                    retryAfter: response.value(forHTTPHeaderField: "Retry-After")
                )
            }

            guard !data.isEmpty else { throw GridAPIError.invalidResponse }
            guard data.count <= 2_000_000 else { throw GridAPIError.responseTooLarge }

            let decoded: Value
            do {
                decoded = try GridJSON.decoder.decode(type, from: data)
            } catch {
#if DEBUG
                let endpoint = request.url?.path ?? "unknown endpoint"
                print("50Hz decode failure for \(endpoint): \(String(reflecting: error))")
#endif
                throw GridAPIError.decoding(String(describing: error))
            }

            try await cache.store(
                data,
                for: endpoint.cacheKey,
                etag: response.value(forHTTPHeaderField: "ETag"),
                lastModified: response.value(forHTTPHeaderField: "Last-Modified")
            )
            return decoded
        } catch is CancellationError {
            throw GridAPIError.cancelled
        } catch let error as URLError {
            if error.code == .cancelled { throw GridAPIError.cancelled }
            throw GridAPIError.transport(error.code)
        } catch let error as GridAPIError {
            throw error
        } catch {
            throw GridAPIError.invalidResponse
        }
    }
}

private struct APIErrorBody: Decodable {
    let detail: String?
    let message: String?

    private struct ValidationIssue: Decodable {
        let msg: String?
    }

    private enum CodingKeys: String, CodingKey {
        case detail, message
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        message = try? container.decode(String.self, forKey: .message)
        if let text = try? container.decode(String.self, forKey: .detail) {
            detail = text
        } else if let issues = try? container.decode([ValidationIssue].self, forKey: .detail) {
            let messages = issues
                .compactMap(\.msg)
                .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                .filter { !$0.isEmpty }
                .prefix(2)
            detail = messages.isEmpty ? nil : messages.joined(separator: " ")
        } else {
            detail = nil
        }
    }

    var safeMessage: String? {
        let raw = detail ?? message
        guard let raw else { return nil }
        let cleaned = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return nil }
        return String(cleaned.prefix(160))
    }
}
