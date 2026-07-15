import Foundation

protocol GridAssetProviding: Sendable {
    func mapAssets() async throws -> GridAssetMapResponse
    func assetDetail(id: String) async throws -> GridAssetDetailResponse
}

actor HTTPGridAssetClient: GridAssetProviding {
    private let baseURL: URL
    private let session: URLSession
    private var mapTask: Task<GridAssetMapResponse, Error>?

    init(
        baseURL: URL = HTTPGridRepository.productionBaseURL,
        session: URLSession = HTTPGridRepository.productionSession()
    ) {
        self.baseURL = baseURL
        self.session = session
    }

    func mapAssets() async throws -> GridAssetMapResponse {
        if let mapTask { return try await mapTask.value }
        let task = Task<GridAssetMapResponse, Error> {
            var components = URLComponents(
                url: baseURL.appendingPathComponent("v1/assets/map"),
                resolvingAgainstBaseURL: false
            )
            components?.queryItems = [
                URLQueryItem(name: "lifecycle", value: "operational"),
                URLQueryItem(name: "limit", value: "5000")
            ]
            guard let url = components?.url else { throw GridAPIError.invalidBaseURL }
            return try await Self.fetch(
                GridAssetMapResponse.self,
                url: url,
                session: session
            )
        }
        mapTask = task
        defer { mapTask = nil }
        return try await task.value
    }

    func assetDetail(id: String) async throws -> GridAssetDetailResponse {
        let url = baseURL
            .appendingPathComponent("v1/assets")
            .appendingPathComponent(id)
        return try await Self.fetch(
            GridAssetDetailResponse.self,
            url: url,
            session: session
        )
    }

    private nonisolated static func fetch<Value: Decodable & Sendable>(
        _ type: Value.Type,
        url: URL,
        session: URLSession
    ) async throws -> Value {
        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        do {
            let (data, response) = try await session.data(for: request)
            guard let response = response as? HTTPURLResponse else {
                throw GridAPIError.invalidResponse
            }
            guard (200..<300).contains(response.statusCode) else {
                let message = (try? JSONDecoder().decode(AssetServerErrorEnvelope.self, from: data))?.detail
                throw GridAPIError.httpStatus(
                    code: response.statusCode,
                    message: message,
                    retryAfter: response.value(forHTTPHeaderField: "Retry-After")
                )
            }
            do {
                return try GridJSON.decoder.decode(type, from: data)
            } catch {
                throw GridAPIError.decoding(error.localizedDescription)
            }
        } catch is CancellationError {
            throw GridAPIError.cancelled
        } catch let error as URLError {
            if error.code == .cancelled { throw GridAPIError.cancelled }
            throw GridAPIError.transport(error.code)
        }
    }
}

private struct AssetServerErrorEnvelope: Decodable {
    let detail: String?
}
