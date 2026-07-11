import Foundation
import XCTest
@testable import FiftyHz

final class HTTPGridRepositoryTests: XCTestCase {
    override func tearDown() {
        StubURLProtocol.handler = nil
        super.tearDown()
    }

    func testETagConditionalRequestUsesSmallDiskCacheOnNotModified() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        let responseData = try GridJSON.encoder.encode(makeSnapshot())
        let lock = NSLock()
        var requestCount = 0
        var conditionalETag: String?

        StubURLProtocol.handler = { request in
            lock.lock()
            requestCount += 1
            let count = requestCount
            if count == 2 { conditionalETag = request.value(forHTTPHeaderField: "If-None-Match") }
            lock.unlock()

            if count == 1 {
                Thread.sleep(forTimeInterval: 0.05)
                return (
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["ETag": "\"snapshot-v1\""]
                    )!,
                    responseData
                )
            }
            return (
                HTTPURLResponse(url: request.url!, statusCode: 304, httpVersion: "HTTP/1.1", headerFields: nil)!,
                Data()
            )
        }

        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        async let firstRequest = repository.currentSnapshot()
        async let deduplicatedRequest = repository.currentSnapshot()
        let (first, duplicate) = try await (firstRequest, deduplicatedRequest)
        let second = try await repository.currentSnapshot()
        let cached = await repository.cachedSnapshot()

        XCTAssertEqual(first.timestamp, duplicate.timestamp)
        XCTAssertEqual(first.timestamp, second.timestamp)
        XCTAssertEqual(cached?.demand.value, 29_800)
        XCTAssertEqual(conditionalETag, "\"snapshot-v1\"")
        XCTAssertEqual(requestCount, 2)
    }

    func testServerFailureHasBoundedUserFacingError() async {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }

        StubURLProtocol.handler = { request in
            (
                HTTPURLResponse(url: request.url!, statusCode: 503, httpVersion: "HTTP/1.1", headerFields: nil)!,
                Data("{\"detail\":\"internal details are not surfaced\"}".utf8)
            )
        }

        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        do {
            _ = try await repository.currentSnapshot()
            XCTFail("Expected a server error")
        } catch {
            XCTAssertEqual(error.localizedDescription, "The grid service is temporarily unavailable.")
        }
    }

    @MainActor
    func testSharePayloadUsesFractionalFuelContractAndLocalFacts() {
        let snapshot = makeSnapshot()
        let payload = GridShareCardPayload.current(snapshot)

        XCTAssertEqual(snapshot.generation.first?.share, 0.35)
        XCTAssertEqual(payload.metrics.first?.value, "50.01")
        XCTAssertTrue(payload.sourceLine.contains("Elexon"))
    }

    private func stubSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [StubURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzTests-\(UUID().uuidString)", isDirectory: true)
    }

    private func makeSnapshot() -> GridSnapshot {
        let timestamp = Date(timeIntervalSince1970: 1_783_779_600)
        let source = SourceReference(
            id: "elexon-freq",
            name: "Elexon Insights",
            dataset: "FREQ",
            observedAt: timestamp,
            retrievedAt: timestamp.addingTimeInterval(30),
            cadenceSeconds: 60
        )
        return GridSnapshot(
            timestamp: timestamp,
            retrievedAt: timestamp.addingTimeInterval(30),
            freshness: .live,
            freshnessAgeSeconds: 30,
            headline: ConditionHeadline(
                cleanliness: "Clean",
                balance: "Comfortable",
                energyPosition: "Exporting",
                interpretation: "Wind is leading while Britain exports."
            ),
            frequency: GridMetric(value: 50.01, unit: "Hz", factClass: .observed, sourceID: source.id),
            demand: GridMetric(value: 29_800, unit: "MW", factClass: .observed, sourceID: source.id),
            carbonIntensity: GridMetric(value: 118, unit: "gCO2/kWh", factClass: .estimated, sourceID: source.id),
            generation: [
                FuelReading(fuel: .wind, megawatts: 10_500, share: 0.35, changeOneHour: 600, rank: 1, factClass: .observed)
            ],
            interconnectors: [],
            activeEvent: nil,
            sources: [source]
        )
    }
}

private final class StubURLProtocol: URLProtocol {
    static var handler: ((URLRequest) throws -> (HTTPURLResponse, Data))?

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
