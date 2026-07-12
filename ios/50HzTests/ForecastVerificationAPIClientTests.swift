import Foundation
import XCTest
@testable import FiftyHz

final class ForecastVerificationAPIClientTests: XCTestCase {
    override func tearDown() {
        ForecastVerificationURLProtocol.handler = nil
        super.tearDown()
    }

    func testVerificationUsesFixedRouteDeduplicatesAndRevalidatesProtectedCache() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = ForecastVerificationRequestRecorder()
        let payload = Data(ForecastVerificationFixture.data().utf8)
        ForecastVerificationURLProtocol.handler = { request in
            let count = recorder.record(request)
            if count == 1 {
                return (
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["ETag": "\"verification-v1\""]
                    )!,
                    payload
                )
            }
            return (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 304,
                    httpVersion: "HTTP/1.1",
                    headerFields: nil
                )!,
                Data()
            )
        }
        let client = HTTPInspectionClient(
            baseURL: URL(string: "https://unit.test")!,
            session: session(),
            cache: InspectionDiskCache(directory: directory),
            artifactStore: ExportArtifactStore(directory: temporaryDirectory())
        )

        async let first = client.forecastVerification()
        async let second = client.forecastVerification()
        let pair = try await (first, second)

        XCTAssertEqual(pair.0, pair.1)
        XCTAssertEqual(recorder.requestCount, 1)
        let cached = await client.cachedForecastVerification()
        XCTAssertEqual(cached, pair.0)

        let revalidated = try await client.forecastVerification()

        XCTAssertEqual(revalidated, pair.0)
        XCTAssertEqual(recorder.requestCount, 2)
        XCTAssertEqual(recorder.paths, ["/v1/forecasts/verification", "/v1/forecasts/verification"])
        XCTAssertEqual(recorder.queryCounts, [0, 0])
        XCTAssertEqual(recorder.lastETag, "\"verification-v1\"")
    }

    func testCorruptVerificationCacheIsPurgedBeforePresentation() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = InspectionDiskCache(directory: directory)
        try await cache.store(
            Data(#"{"schemaVersion":"1.0","results":"not-an-array"}"#.utf8),
            for: .forecastVerification,
            etag: "\"broken\"",
            lastModified: nil
        )
        let client = HTTPInspectionClient(
            baseURL: URL(string: "https://unit.test")!,
            session: session(),
            cache: cache,
            artifactStore: ExportArtifactStore(directory: temporaryDirectory())
        )

        let decoded = await client.cachedForecastVerification()
        let remainingEntry = await cache.entry(for: .forecastVerification)
        XCTAssertNil(decoded)
        XCTAssertNil(remainingEntry)
    }

    func testForecastVerificationURLNeverAcceptsUserControlledPathOrQuery() throws {
        let url = HTTPInspectionClient.forecastVerificationURL(
            baseURL: URL(string: "https://unit.test/base")!
        )
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))

        XCTAssertEqual(components.path, "/base/v1/forecasts/verification")
        XCTAssertNil(components.query)
    }

    private func session() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ForecastVerificationURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzForecastVerification-\(UUID().uuidString)", isDirectory: true)
    }
}

private final class ForecastVerificationURLProtocol: URLProtocol {
    nonisolated(unsafe) static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        guard let handler = Self.handler else {
            client?.urlProtocol(self, didFailWithError: URLError(.badServerResponse))
            return
        }
        do {
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            if !data.isEmpty { client?.urlProtocol(self, didLoad: data) }
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

private final class ForecastVerificationRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests: [URLRequest] = []

    func record(_ request: URLRequest) -> Int {
        lock.withLock {
            requests.append(request)
            return requests.count
        }
    }

    var requestCount: Int { lock.withLock { requests.count } }
    var paths: [String] { lock.withLock { requests.compactMap(\.url?.path) } }
    var queryCounts: [Int] {
        lock.withLock {
            requests.map {
                URLComponents(url: $0.url!, resolvingAgainstBaseURL: false)?.queryItems?.count ?? 0
            }
        }
    }
    var lastETag: String? {
        lock.withLock { requests.last?.value(forHTTPHeaderField: "If-None-Match") }
    }
}
