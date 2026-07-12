import Foundation

protocol InspectionDataProviding: Sendable {
    func cachedSourceStatus() async -> SourceStatusResponse?
    func sourceStatus() async throws -> SourceStatusResponse
    func cachedEventHistory(eventID: String) async -> EventHistoryResponse?
    func eventHistory(eventID: String) async throws -> EventHistoryResponse
    func cachedExportSchema() async -> ExportSchemaResponse?
    func exportSchema() async throws -> ExportSchemaResponse
    func prepareExport(_ request: ExportRequestSpec) async throws -> PreparedExport
}

struct InspectionCacheKey: Hashable, Sendable {
    let rawValue: String

    static let sourceStatus = Self(rawValue: "source-status")
    static let exportSchema = Self(rawValue: "export-schema")

    static func eventHistory(_ eventID: String) -> Self? {
        guard InspectionEndpoint.isValidEventID(eventID) else { return nil }
        return Self(rawValue: "event-history-\(eventID)")
    }
}

struct InspectionCacheEntry: Sendable {
    let data: Data
    let etag: String?
    let lastModified: String?
    let savedAt: Date
}

actor InspectionDiskCache {
    private struct Metadata: Codable {
        let etag: String?
        let lastModified: String?
        let savedAt: Date
    }

    private let directory: URL
    private let fileManager: FileManager
    private let maximumEntryBytes: Int

    init(
        directory: URL,
        fileManager: FileManager = .default,
        maximumEntryBytes: Int = 1_000_000
    ) {
        self.directory = directory
        self.fileManager = fileManager
        self.maximumEntryBytes = maximumEntryBytes
    }

    static func production() -> InspectionDiskCache {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return InspectionDiskCache(
            directory: base.appendingPathComponent("50Hz/Inspection", isDirectory: true)
        )
    }

    func entry(for key: InspectionCacheKey) -> InspectionCacheEntry? {
        do {
            let data = try Data(contentsOf: dataURL(for: key), options: [.mappedIfSafe])
            guard !data.isEmpty, data.count <= maximumEntryBytes else {
                remove(key)
                return nil
            }
            let metadataData = try Data(contentsOf: metadataURL(for: key))
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            let metadata = try decoder.decode(Metadata.self, from: metadataData)
            return InspectionCacheEntry(
                data: data,
                etag: metadata.etag,
                lastModified: metadata.lastModified,
                savedAt: metadata.savedAt
            )
        } catch {
            return nil
        }
    }

    func store(
        _ data: Data,
        for key: InspectionCacheKey,
        etag: String?,
        lastModified: String?,
        savedAt: Date = Date()
    ) throws {
        guard !data.isEmpty, data.count <= maximumEntryBytes else {
            throw GridAPIError.responseTooLarge
        }
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
        )
        try data.write(
            to: dataURL(for: key),
            options: [.atomic, .completeFileProtectionUnlessOpen]
        )

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let metadata = Metadata(etag: etag, lastModified: lastModified, savedAt: savedAt)
        try encoder.encode(metadata).write(
            to: metadataURL(for: key),
            options: [.atomic, .completeFileProtectionUnlessOpen]
        )
    }

    func remove(_ key: InspectionCacheKey) {
        try? fileManager.removeItem(at: dataURL(for: key))
        try? fileManager.removeItem(at: metadataURL(for: key))
    }

    private func dataURL(for key: InspectionCacheKey) -> URL {
        directory.appendingPathComponent("inspection-\(key.rawValue).json")
    }

    private func metadataURL(for key: InspectionCacheKey) -> URL {
        directory.appendingPathComponent("inspection-\(key.rawValue).metadata.json")
    }
}

actor ExportArtifactStore {
    private let directory: URL
    private let fileManager: FileManager
    private let maximumArtifactBytes: Int
    private let maximumRetainedFiles: Int

    init(
        directory: URL,
        fileManager: FileManager = .default,
        maximumArtifactBytes: Int = 6_000_000,
        maximumRetainedFiles: Int = 6
    ) {
        self.directory = directory
        self.fileManager = fileManager
        self.maximumArtifactBytes = maximumArtifactBytes
        self.maximumRetainedFiles = maximumRetainedFiles
    }

    static func production() -> ExportArtifactStore {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return ExportArtifactStore(
            directory: base.appendingPathComponent("50Hz/Exports", isDirectory: true)
        )
    }

    func store(_ data: Data, filename: String) throws -> URL {
        guard !data.isEmpty, data.count <= maximumArtifactBytes else {
            throw GridAPIError.responseTooLarge
        }
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
        )
        try prune()
        let url = directory.appendingPathComponent(filename, isDirectory: false)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUnlessOpen])
        return url
    }

    private func prune() throws {
        let keys: Set<URLResourceKey> = [.contentModificationDateKey, .isRegularFileKey]
        let files = try fileManager.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: Array(keys),
            options: [.skipsHiddenFiles]
        )
        let regularFiles = files.compactMap { url -> (URL, Date)? in
            guard let values = try? url.resourceValues(forKeys: keys), values.isRegularFile == true else {
                return nil
            }
            return (url, values.contentModificationDate ?? .distantPast)
        }
        for (url, _) in regularFiles.sorted(by: { $0.1 > $1.1 }).dropFirst(maximumRetainedFiles - 1) {
            try? fileManager.removeItem(at: url)
        }
    }
}

actor HTTPInspectionClient: InspectionDataProviding {
    static let productionBaseURL = URL(string: "https://50hz-api-production.up.railway.app")!

    private enum Endpoint: Hashable {
        case sourceStatus
        case eventHistory(String)
        case exportSchema

        var cacheKey: InspectionCacheKey? {
            switch self {
            case .sourceStatus: .sourceStatus
            case .eventHistory(let eventID): .eventHistory(eventID)
            case .exportSchema: .exportSchema
            }
        }
    }

    private let baseURL: URL
    private let session: URLSession
    private let cache: InspectionDiskCache
    private let artifactStore: ExportArtifactStore
    private var sourceStatusTask: Task<SourceStatusResponse, Error>?
    private var eventHistoryTasks: [String: Task<EventHistoryResponse, Error>] = [:]
    private var exportSchemaTask: Task<ExportSchemaResponse, Error>?
    private var exportTasks: [ExportRequestSpec: Task<PreparedExport, Error>] = [:]

    init(
        baseURL: URL = HTTPInspectionClient.productionBaseURL,
        session: URLSession = HTTPInspectionClient.productionSession(),
        cache: InspectionDiskCache = .production(),
        artifactStore: ExportArtifactStore = .production()
    ) {
        self.baseURL = baseURL
        self.session = session
        self.cache = cache
        self.artifactStore = artifactStore
    }

    nonisolated static func productionSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 20
        configuration.timeoutIntervalForResource = 40
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        configuration.waitsForConnectivity = false
        configuration.httpAdditionalHeaders = [
            "Accept": "application/json",
            "User-Agent": "50Hz-iOS/1.0"
        ]
        return URLSession(configuration: configuration)
    }

    func cachedSourceStatus() async -> SourceStatusResponse? {
        await decodeCached(.sourceStatus, as: SourceStatusResponse.self)
    }

    func cachedEventHistory(eventID: String) async -> EventHistoryResponse? {
        guard InspectionEndpoint.isValidEventID(eventID) else { return nil }
        let result = await decodeCached(.eventHistory(eventID), as: EventHistoryResponse.self)
        guard result?.eventID == eventID else {
            if let key = InspectionCacheKey.eventHistory(eventID) { await cache.remove(key) }
            return nil
        }
        return result
    }

    func cachedExportSchema() async -> ExportSchemaResponse? {
        await decodeCached(.exportSchema, as: ExportSchemaResponse.self)
    }

    func sourceStatus() async throws -> SourceStatusResponse {
        if let sourceStatusTask { return try await sourceStatusTask.value }
        let task = Task<SourceStatusResponse, Error> {
            try await Self.fetch(
                endpoint: .sourceStatus,
                url: Self.sourceStatusURL(baseURL: baseURL),
                session: session,
                cache: cache,
                as: SourceStatusResponse.self
            )
        }
        sourceStatusTask = task
        defer { sourceStatusTask = nil }
        return try await awaitValue(of: task)
    }

    func eventHistory(eventID: String) async throws -> EventHistoryResponse {
        guard InspectionEndpoint.isValidEventID(eventID) else {
            throw InspectionRequestError.invalidEventID
        }
        if let task = eventHistoryTasks[eventID] { return try await task.value }
        let task = Task<EventHistoryResponse, Error> {
            let response = try await Self.fetch(
                endpoint: .eventHistory(eventID),
                url: Self.eventHistoryURL(baseURL: baseURL, eventID: eventID),
                session: session,
                cache: cache,
                as: EventHistoryResponse.self
            )
            guard response.eventID == eventID else {
                if let key = InspectionCacheKey.eventHistory(eventID) { await cache.remove(key) }
                throw GridAPIError.invalidResponse
            }
            return response
        }
        eventHistoryTasks[eventID] = task
        defer { eventHistoryTasks[eventID] = nil }
        return try await awaitValue(of: task)
    }

    func exportSchema() async throws -> ExportSchemaResponse {
        if let exportSchemaTask { return try await exportSchemaTask.value }
        let task = Task<ExportSchemaResponse, Error> {
            try await Self.fetch(
                endpoint: .exportSchema,
                url: Self.exportSchemaURL(baseURL: baseURL),
                session: session,
                cache: cache,
                as: ExportSchemaResponse.self
            )
        }
        exportSchemaTask = task
        defer { exportSchemaTask = nil }
        return try await awaitValue(of: task)
    }

    func prepareExport(_ request: ExportRequestSpec) async throws -> PreparedExport {
        if let task = exportTasks[request] { return try await task.value }
        let task = Task<PreparedExport, Error> {
            let schema = try await exportSchema()
            try Self.validate(request, against: schema)
            let url = try Self.exportURL(baseURL: baseURL, request: request)
            let (data, response) = try await Self.download(url: url, session: session)
            let summary = try Self.validateExport(data: data, response: response, request: request)
            let filename = Self.exportFilename(for: request, preparedAt: summary.preparedAt)
            let fileURL: URL
            do {
                fileURL = try await artifactStore.store(data, filename: filename)
            } catch let error as GridAPIError {
                throw error
            } catch {
                throw InspectionRequestError.filePreparationFailed
            }
            return PreparedExport(
                url: fileURL,
                format: request.format,
                metric: request.metric,
                requestedFrom: request.from,
                requestedTo: request.to,
                expectedRows: summary.expectedRows,
                missingRows: summary.missingRows,
                preparedAt: summary.preparedAt
            )
        }
        exportTasks[request] = task
        defer { exportTasks[request] = nil }
        return try await awaitValue(of: task)
    }

    private func decodeCached<Value: Decodable & Sendable>(
        _ endpoint: Endpoint,
        as type: Value.Type
    ) async -> Value? {
        guard let key = endpoint.cacheKey, let entry = await cache.entry(for: key) else {
            return nil
        }
        do {
            return try GridJSON.decoder.decode(type, from: entry.data)
        } catch {
            await cache.remove(key)
            return nil
        }
    }

    private func awaitValue<Value>(of task: Task<Value, Error>) async throws -> Value {
        try await withTaskCancellationHandler {
            try await task.value
        } onCancel: {
            task.cancel()
        }
    }

    nonisolated static func sourceStatusURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/sources/status")
    }

    nonisolated static func eventHistoryURL(baseURL: URL, eventID: String) -> URL {
        baseURL
            .appendingPathComponent("v1/events")
            .appendingPathComponent(eventID)
            .appendingPathComponent("history")
    }

    nonisolated static func exportSchemaURL(baseURL: URL) -> URL {
        baseURL.appendingPathComponent("v1/metadata/export-schema")
    }

    nonisolated static func exportURL(baseURL: URL, request: ExportRequestSpec) throws -> URL {
        let endpoint = baseURL.appendingPathComponent("v1/export")
        guard var components = URLComponents(url: endpoint, resolvingAgainstBaseURL: false) else {
            throw GridAPIError.invalidBaseURL
        }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        var items = [
            URLQueryItem(name: "metric", value: request.metric.rawValue),
            URLQueryItem(name: "from", value: formatter.string(from: request.from)),
            URLQueryItem(name: "to", value: formatter.string(from: request.to)),
            URLQueryItem(name: "resolution", value: String(ExportRequestSpec.resolutionSeconds)),
            URLQueryItem(name: "format", value: request.format.rawValue)
        ]
        if let selector = request.selector {
            items.append(URLQueryItem(name: "selector", value: selector))
        }
        components.queryItems = items
        guard let url = components.url else { throw GridAPIError.invalidBaseURL }
        return url
    }

    private nonisolated static func validate(
        _ request: ExportRequestSpec,
        against schema: ExportSchemaResponse
    ) throws {
        guard schema.maxWindowDays == 31,
              schema.maxRowCount == ExportRequestSpec.maximumRows,
              schema.resolutionsSeconds.contains(ExportRequestSpec.resolutionSeconds),
              [.json, .csv].contains(request.format),
              request.metric != .unknown,
              schema.formats.contains(request.format),
              let metric = schema.metrics.first(where: { $0.metric == request.metric }) else {
            throw InspectionRequestError.unsupportedSelection
        }
        if metric.selectorRequired {
            guard let selector = request.selector else { throw InspectionRequestError.selectorRequired }
            guard metric.allowedSelectors.contains(selector) else {
                throw InspectionRequestError.unsupportedSelection
            }
        } else if request.selector != nil {
            throw InspectionRequestError.unsupportedSelection
        }
    }

    private struct ExportSummary {
        let expectedRows: Int
        let missingRows: Int
        let preparedAt: Date
    }

    private nonisolated static func validateExport(
        data: Data,
        response: HTTPURLResponse,
        request: ExportRequestSpec
    ) throws -> ExportSummary {
        if request.format == .json {
            let decoded: ExportResponse
            do {
                decoded = try GridJSON.decoder.decode(ExportResponse.self, from: data)
            } catch {
                throw GridAPIError.decoding(String(describing: error))
            }
            guard decoded.metricID == request.metric.rawValue,
                  decoded.requestedFrom == request.from,
                  decoded.requestedTo == request.to,
                  decoded.resolutionSeconds == ExportRequestSpec.resolutionSeconds,
                  decoded.coverage.expectedIntervalCount == request.expectedRowCount,
                  decoded.rows.count == request.expectedRowCount,
                  decoded.coverage.availableIntervalCount + decoded.coverage.missingIntervalCount == request.expectedRowCount else {
                throw GridAPIError.invalidResponse
            }
            return ExportSummary(
                expectedRows: decoded.coverage.expectedIntervalCount,
                missingRows: decoded.coverage.missingIntervalCount,
                preparedAt: Date()
            )
        }

        guard let text = String(data: data.prefix(1_024), encoding: .utf8),
              text.hasPrefix("start,end,status,value,unit,classification,metric_id,geography,source_id,source_record_ids,source_methodology_version,materialization_methodology_version,coverage_fraction"),
              let expectedText = response.value(forHTTPHeaderField: "X-50Hz-Expected-Rows"),
              let expected = Int(expectedText),
              let missingText = response.value(forHTTPHeaderField: "X-50Hz-Missing-Rows"),
              let missing = Int(missingText),
              expected == request.expectedRowCount,
              data.split(separator: 0x0A, omittingEmptySubsequences: true).count == expected + 1,
              (0...expected).contains(missing) else {
            throw GridAPIError.invalidResponse
        }
        return ExportSummary(expectedRows: expected, missingRows: missing, preparedAt: Date())
    }

    private nonisolated static func exportFilename(
        for request: ExportRequestSpec,
        preparedAt: Date
    ) -> String {
        let safeMetric = request.metric.rawValue
            .map { $0.isLetter || $0.isNumber ? String($0).lowercased() : "-" }
            .joined()
            .replacingOccurrences(of: "--", with: "-")
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "yyyyMMdd-HHmmss'Z'"
        return "50Hz-\(safeMetric)-\(formatter.string(from: preparedAt)).\(request.format.rawValue)"
    }

    private nonisolated static func download(
        url: URL,
        session: URLSession
    ) async throws -> (Data, HTTPURLResponse) {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 40
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            try Task.checkCancellation()
            let (data, response) = try await session.data(for: request)
            try Task.checkCancellation()
            guard let response = response as? HTTPURLResponse else { throw GridAPIError.invalidResponse }
            guard (200..<300).contains(response.statusCode) else {
                throw inspectionHTTPError(response: response, data: data)
            }
            guard !data.isEmpty else { throw GridAPIError.invalidResponse }
            guard data.count <= 6_000_000 else { throw GridAPIError.responseTooLarge }
            return (data, response)
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
        url: URL,
        session: URLSession,
        cache: InspectionDiskCache,
        as type: Value.Type
    ) async throws -> Value {
        guard let key = endpoint.cacheKey else { throw GridAPIError.invalidResponse }
        let cached = await cache.entry(for: key)
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 20
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let etag = cached?.etag {
            request.setValue(etag, forHTTPHeaderField: "If-None-Match")
        }
        if let lastModified = cached?.lastModified {
            request.setValue(lastModified, forHTTPHeaderField: "If-Modified-Since")
        }

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
                    await cache.remove(key)
                    throw GridAPIError.decoding(String(describing: error))
                }
            }

            guard (200..<300).contains(response.statusCode) else {
                throw inspectionHTTPError(response: response, data: data)
            }
            guard !data.isEmpty else { throw GridAPIError.invalidResponse }
            guard data.count <= 1_000_000 else { throw GridAPIError.responseTooLarge }

            let decoded: Value
            do {
                decoded = try GridJSON.decoder.decode(type, from: data)
            } catch {
                throw GridAPIError.decoding(String(describing: error))
            }
            try? await cache.store(
                data,
                for: key,
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

    private nonisolated static func inspectionHTTPError(
        response: HTTPURLResponse,
        data: Data
    ) -> GridAPIError {
        let body = try? JSONDecoder().decode(InspectionErrorBody.self, from: data)
        return GridAPIError.httpStatus(
            code: response.statusCode,
            message: body?.safeMessage,
            retryAfter: response.value(forHTTPHeaderField: "Retry-After")
        )
    }
}

private struct InspectionErrorBody: Decodable {
    let detail: String?
    let message: String?

    private struct Issue: Decodable { let msg: String? }
    private enum CodingKeys: String, CodingKey { case detail, message }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        message = try? container.decode(String.self, forKey: .message)
        if let value = try? container.decode(String.self, forKey: .detail) {
            detail = value
        } else if let issues = try? container.decode([Issue].self, forKey: .detail) {
            detail = issues.compactMap(\.msg).prefix(2).joined(separator: " ")
        } else {
            detail = nil
        }
    }

    var safeMessage: String? {
        guard let value = (detail ?? message)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !value.isEmpty else { return nil }
        return String(value.prefix(160))
    }
}
