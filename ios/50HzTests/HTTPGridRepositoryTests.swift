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

    func testRegionUsesEncodedPostcodeAndPersistsCacheFirstResponse() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let response = Data(
            """
            {
              "name":"London","postcode":"SW1A","carbonIntensity":82,"nationalCarbonIntensity":106,"rating":"low",
              "regionalPeriodEnd":"2026-07-11T13:30:00Z","regionalIsDelayed":true,
              "cleanestWindowStart":"2026-07-12T01:00:00Z","cleanestWindowEnd":"2026-07-12T02:00:00Z",
              "chargingWindowStart":"2026-07-12T01:00:00Z","chargingWindowEnd":"2026-07-12T02:00:00Z",
              "forecastIssuedAt":"2026-07-11T14:00:00Z",
              "source":{"id":"neso-regional","name":"NESO Carbon Intensity","dataset":"regional","observedAt":"2026-07-11T14:00:00Z","retrievedAt":"2026-07-11T14:01:00Z","cadenceSeconds":1800}
            }
            """.utf8
        )
        let lock = NSLock()
        var requestedURL: URL?
        StubURLProtocol.handler = { request in
            lock.lock(); requestedURL = request.url; lock.unlock()
            return (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["ETag": "\"region-v1\""])!,
                response
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        let region = try await repository.region(postcode: "sw1a 1aa")
        let cached = await repository.cachedRegion(postcode: "SW1A 1AA")

        XCTAssertEqual(region.name, "London")
        XCTAssertEqual(region.regionalIsDelayed, true)
        XCTAssertNotNil(region.regionalPeriodEnd)
        XCTAssertEqual(cached?.carbonIntensity, 82)
        let absoluteURL = lock.withLock { requestedURL?.absoluteString }
        XCTAssertTrue(absoluteURL?.hasSuffix("/v1/regions/SW1A") == true)
        XCTAssertFalse(absoluteURL?.contains("1AA") == true)
    }

    func testAskSendsCamelCaseMapContextAndDecodesResolvedCitations() async throws {
        let response = Data(
            """
            {
              "answer":"Britain is importing 420 MW in the validated snapshot.",
              "asOf":"2026-07-11T14:00:00Z","freshness":"fresh","evidenceRefs":["elexon-interconnectors"],
              "citations":[{"sourceID":"elexon-interconnectors","publisher":"Elexon","title":"Interconnector flows","canonicalURL":"https://bmrs.elexon.co.uk/","publishedAt":"2026-07-11T14:00:00Z"}],
              "limitations":["Flows can change between published observations."],"suggestedQuestions":["Which link is largest?"]
            }
            """.utf8
        )
        let lock = NSLock()
        var body: Data?
        StubURLProtocol.handler = { request in
            lock.lock(); body = request.httpBody; lock.unlock()
            return (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: nil)!, response)
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: temporaryDirectory())
        )
        let mapTime = Date(timeIntervalSince1970: 1_783_779_600)

        let answer = try await repository.ask(AskGridRequest(question: "Are we importing?", mapTime: mapTime, regionCode: nil))

        let sentBody = lock.withLock { body }
        let sent = try XCTUnwrap(sentBody)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: sent) as? [String: Any])
        XCTAssertEqual(payload["question"] as? String, "Are we importing?")
        XCTAssertNotNil(payload["mapTime"])
        XCTAssertNil(payload["map_time"])
        XCTAssertEqual(answer.citations.first?.publisher, "Elexon")
        XCTAssertEqual(answer.suggestedQuestions, ["Which link is largest?"])
    }

    func testDailyGameUsesBackendRouteAndPersistsCache() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let response = Data(
            """
            {
              "date":"2026-07-11",
              "missions":[{"mission_id":"2026-07-11:largest-source","kind":"identify_largest_source","title":"Identify Britain's largest source","available":true,"unavailable_reason":null,"completion_payload":{}}],
              "prediction":null,
              "source_fresh":true
            }
            """.utf8
        )
        let lock = NSLock()
        var requestedPath: String?
        StubURLProtocol.handler = { request in
            lock.withLock { requestedPath = request.url?.path }
            return (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["ETag": "\"game-v1\""]
                )!,
                response
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        let game = try await repository.dailyGame()
        let cached = await repository.cachedDailyGame()

        XCTAssertEqual(lock.withLock { requestedPath }, "/v1/game/today")
        XCTAssertEqual(game.missions.first?.title, "Identify Britain's largest source")
        XCTAssertEqual(cached, game)
    }

    func testEventExplanationDecodesOptionalGroundedFields() async throws {
        let response = Data(
            """
            {
              "eventID":"evt_123","revision":2,
              "explanation":{"headline":"A unit reported unavailable","plainLanguage":"The operator reported a reduction.","whyItMatters":null,"caveat":"This does not prove another source responded.","evidenceRefs":["remit"],"suggestedQuestions":[]},
              "citations":[],"model":"openai/test","usedFallback":false
            }
            """.utf8
        )
        StubURLProtocol.handler = { request in
            (HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: nil)!, response)
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: temporaryDirectory())
        )

        let result = try await repository.eventExplanation(id: "evt_123")

        XCTAssertEqual(result.revision, 2)
        XCTAssertNil(result.explanation.whyItMatters)
        XCTAssertEqual(result.explanation.evidenceRefs, ["remit"])
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
