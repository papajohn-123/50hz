import Foundation
import XCTest
@testable import FiftyHz

final class TodayBriefingContractTests: XCTestCase {
    override func tearDown() {
        BriefingURLProtocol.handler = nil
        super.tearDown()
    }

    func testCompleteCamelCaseContractDecodesWithoutClientCuration() throws {
        let briefing = try TodayBriefingFixture.complete()

        XCTAssertEqual(briefing.schemaVersion, "1.0")
        XCTAssertEqual(briefing.displayPeriod.localDate, "2026-07-12")
        XCTAssertEqual(briefing.displayPeriod.timezone, "Europe/London")
        XCTAssertEqual(briefing.coverage.status, .complete)
        XCTAssertEqual(briefing.now.status, .complete)
        XCTAssertEqual(briefing.now.values.map(\.metricID), ["demand", "carbon", "frequency"])
        XCTAssertEqual(briefing.now.values[1].factClass, .estimated)
        XCTAssertEqual(briefing.changes.map(\.stableID), ["change-demand", "change-carbon", "change-frequency", "legacy-extra"])
        XCTAssertEqual(TodayBriefingPresentation.displayedChanges(briefing).map(\.stableID), ["change-demand", "change-carbon", "change-frequency"])
        XCTAssertEqual(TodayBriefingPresentation.displayedNextMoments(briefing).count, 3)
        XCTAssertEqual(briefing.reportedEvents.totalCount, 5)
        XCTAssertEqual(TodayBriefingPresentation.displayedEvents(briefing).map(\.stableID), ["evt-critical", "evt-upcoming", "evt-material"])
        XCTAssertTrue(briefing.bestWindow?.isCompleteNationalForecastWindow == true)
        XCTAssertEqual(briefing.methodology.causalAttribution, false)
        XCTAssertTrue(briefing.matches(TodayBriefingRequest(localDate: "2026-07-12")))
    }

    func testPartialAndOfflineContractsRetainSuppliedSections() throws {
        let partial = try TodayBriefingFixture.partial()
        let offline = try TodayBriefingFixture.offline()

        XCTAssertEqual(partial.coverage.status, .partial)
        XCTAssertEqual(partial.now.values.first?.metricID, "demand")
        XCTAssertEqual(partial.changes.count, 1)
        XCTAssertEqual(partial.reportedEvents.items.count, 1)
        XCTAssertEqual(partial.nextMoments, [])
        XCTAssertNil(partial.bestWindow)
        XCTAssertTrue(partial.coverage.missingFamilies.contains("forecast.carbon"))

        XCTAssertEqual(offline.coverage.status, .offline)
        XCTAssertEqual(offline.now.values.first?.metricID, "frequency")
        XCTAssertEqual(offline.reportedEvents.totalCount, 1)
        XCTAssertEqual(offline.reportedEvents.items.first?.evidenceClass, "reported")
        XCTAssertEqual(offline.sourceStatuses.first?.state, .unavailable)
    }

    func testLegacyDefaultsAndNullableFieldsRemainDecodable() throws {
        let briefing = try GridJSON.decoder.decode(
            TodayBriefing.self,
            from: TodayBriefingFixture.legacyData()
        )

        XCTAssertEqual(briefing.schemaVersion, "1.0")
        XCTAssertEqual(briefing.methodology.version, "50hz.briefing.v1")
        XCTAssertEqual(briefing.methodology.timezone, "Europe/London")
        XCTAssertFalse(briefing.methodology.causalAttribution)
        XCTAssertNil(briefing.generatedAt)
        XCTAssertEqual(briefing.now.status, .unknown)
        XCTAssertEqual(briefing.now.values, [])
        XCTAssertEqual(briefing.changes, [])
        XCTAssertEqual(briefing.nextMoments, [])
        XCTAssertEqual(briefing.reportedEvents.totalCount, 0)
        XCTAssertNil(briefing.bestWindow)
        XCTAssertEqual(briefing.coverage.status, .unknown)
        XCTAssertEqual(briefing.limitations, [])
        XCTAssertTrue(briefing.matches(TodayBriefingRequest(localDate: "2026-07-12")))
    }

    func testUnknownEnumsAreBoundedInsteadOfBreakingTheBriefing() throws {
        let json = String(decoding: TodayBriefingFixture.completeData(), as: UTF8.self)
            .replacingOccurrences(of: #""status":"complete""#, with: #""status":"future_status""#)
            .replacingOccurrences(of: #""factClass":"estimated""#, with: #""factClass":"modelled_future""#)
            .replacingOccurrences(of: #""severity":"critical""#, with: #""severity":"urgent_future""#)
            .replacingOccurrences(of: #""timing":"active""#, with: #""timing":"recent_future""#)
            .replacingOccurrences(of: #""state":"live""#, with: #""state":"recovering_future""#)
            .replacingOccurrences(of: #""name":"overnight""#, with: #""name":"late_night_future""#)
            .replacingOccurrences(of: #""best_window""#, with: #""future_section""#)
        let briefing = try GridJSON.decoder.decode(TodayBriefing.self, from: Data(json.utf8))

        XCTAssertEqual(briefing.coverage.status, .unknown)
        XCTAssertEqual(briefing.now.values[1].factClass, .unknown)
        XCTAssertEqual(briefing.reportedEvents.items[0].severity, .unknown)
        XCTAssertEqual(briefing.reportedEvents.items[0].timing, .unknown)
        XCTAssertEqual(briefing.sourceStatuses[0].state, .unknown)
        XCTAssertEqual(briefing.displayPeriod.name, .unknown)
        XCTAssertTrue(briefing.coverage.availableSections.contains(.unknown))
        XCTAssertEqual(TodayBriefingPresentation.statusTitle(.unknown), "Coverage not confirmed")
    }

    func testLondonDateRolloverIsIndependentOfDeviceTimezone() {
        let beforeMidnight = Date(timeIntervalSince1970: 1_783_810_740) // 2026-07-11 22:59 UTC
        let afterMidnight = Date(timeIntervalSince1970: 1_783_810_860)  // 2026-07-11 23:01 UTC

        XCTAssertEqual(LondonDay.localDateKey(at: beforeMidnight), "2026-07-11")
        XCTAssertEqual(LondonDay.localDateKey(at: afterMidnight), "2026-07-12")
        XCTAssertTrue(LondonDay.isValidLocalDateKey("2026-10-25"))
        XCTAssertTrue(LondonDay.isValidLocalDateKey("2028-02-29"))
        XCTAssertFalse(LondonDay.isValidLocalDateKey("2026-02-29"))
        XCTAssertFalse(LondonDay.isValidLocalDateKey("2026-02-30"))
        XCTAssertFalse(LondonDay.isValidLocalDateKey("2026/10/25"))
        XCTAssertNotEqual(
            GridCacheKey.todayBriefing(localDate: "2026-07-11"),
            GridCacheKey.todayBriefing(localDate: "2026-07-12")
        )
        XCTAssertEqual(
            GridCacheKey.todayBriefing(localDate: "2026-07-12").rawValue,
            "briefing-2026-07-12"
        )
    }

    func testCrossDayLondonLabelsAndCopyRemainTruthful() throws {
        let briefing = try TodayBriefingFixture.complete()
        let window = try XCTUnwrap(
            TodayBriefingPresentation.visibleBestWindow(
                briefing,
                now: TodayBriefingFixture.asOf.addingTimeInterval(-60)
            )
        )
        let windowText = TodayBriefingPresentation.windowLabel(
            window,
            relativeTo: briefing.displayPeriod.localDate
        )
        let nextText = TodayBriefingPresentation.timeLabel(
            briefing.nextMoments.last?.startsAt,
            relativeTo: briefing.displayPeriod.localDate
        )
        let allProductCopy = [
            TodayBriefingPresentation.methodologyCopy,
            TodayBriefingPresentation.nationalForecastScope,
            windowText,
            nextText
        ].joined(separator: " ").lowercased()

        XCTAssertTrue(windowText.contains("13 Jul"))
        XCTAssertTrue(nextText.contains("13 Jul"))
        XCTAssertFalse(allProductCopy.contains("because"))
        XCTAssertFalse(allProductCopy.contains("saving"))
        XCTAssertFalse(allProductCopy.contains("cost"))
        XCTAssertTrue(TodayBriefingPresentation.nationalForecastScope.contains("national"))
        XCTAssertTrue(TodayBriefingPresentation.shouldShowAllEvents(briefing.reportedEvents))
        XCTAssertFalse(
            TodayBriefingPresentation.shouldShowAllEvents(
                TodayReportedEvents(items: briefing.reportedEvents.items, totalCount: 3)
            )
        )
    }

    func testETagRequestIsDeduplicatedAndCachedByLondonDate() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = BriefingRequestRecorder()
        BriefingURLProtocol.handler = { request in
            let count = recorder.record(request)
            if count == 1 {
                Thread.sleep(forTimeInterval: 0.05)
                return (
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["ETag": "\"briefing-v1\""]
                    )!,
                    TodayBriefingFixture.completeData()
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

        async let first = repository.todayBriefing(localDate: "2026-07-12")
        async let duplicate = repository.todayBriefing(localDate: "2026-07-12")
        let (one, two) = try await (first, duplicate)
        let notModified = try await repository.todayBriefing(localDate: "2026-07-12")
        let cached = await repository.cachedTodayBriefing(localDate: "2026-07-12")

        XCTAssertEqual(one, two)
        XCTAssertEqual(one, notModified)
        XCTAssertEqual(cached, one)
        XCTAssertEqual(recorder.requestCount, 2)
        XCTAssertEqual(recorder.urls.first?.path, "/v1/briefing/today")
        XCTAssertNil(recorder.urls.first?.query)
        XCTAssertEqual(recorder.lastConditionalETag, "\"briefing-v1\"")
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("grid-briefing-2026-07-12.json").path
            )
        )
    }

    func testWrongDayCacheAndResponseAreRejected() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = GridDiskCache(directory: directory)
        try await cache.store(
            TodayBriefingFixture.completeData(localDate: "2026-07-12"),
            for: .todayBriefing(localDate: "2026-07-13"),
            etag: nil,
            lastModified: nil
        )
        BriefingURLProtocol.handler = { request in
            (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                TodayBriefingFixture.completeData(localDate: "2026-07-12")
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: cache
        )

        let wrongDayCache = await repository.cachedTodayBriefing(localDate: "2026-07-13")
        XCTAssertNil(wrongDayCache)
        do {
            _ = try await repository.todayBriefing(localDate: "2026-07-13")
            XCTFail("Expected a date mismatch")
        } catch {
            XCTAssertEqual(error.localizedDescription, "The grid service returned data from an incompatible contract.")
        }
        let held = await repository.cachedTodayBriefing(localDate: "2026-07-13")
        XCTAssertNil(held)
    }

    private func stubSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [BriefingURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzBriefingTests-\(UUID().uuidString)", isDirectory: true)
    }

}

@MainActor
final class TodayBriefingAppModelTests: XCTestCase {
    func testCacheFirstFailureRetainsSavedBriefingAndError() async throws {
        let briefing = try TodayBriefingFixture.complete()
        let model = AppModel(repository: BriefingCacheFailureRepository(briefing: briefing))

        await model.loadTodayBriefing(localDate: "2026-07-12")

        XCTAssertEqual(model.todayBriefing, briefing)
        XCTAssertEqual(model.briefingLoadPhase, .loaded)
        XCTAssertEqual(model.briefingRequestDate, "2026-07-12")
        XCTAssertTrue(model.briefingIsFromCache)
        XCTAssertFalse(model.isRefreshingBriefing)
        XCTAssertEqual(model.briefingError, "There is no reliable network connection.")
    }

    func testPartialAndOfflineResponsesAreLoadedDataNotTransportFailures() async throws {
        let partial = try TodayBriefingFixture.partial()
        let offline = try TodayBriefingFixture.offline()
        let repository = MutableBriefingRepository(response: partial)
        let model = AppModel(repository: repository)

        await model.loadTodayBriefing(localDate: "2026-07-12")
        XCTAssertEqual(model.todayBriefing?.coverage.status, .partial)
        XCTAssertEqual(model.briefingLoadPhase, .loaded)
        XCTAssertNil(model.briefingError)

        await repository.setResponse(offline)
        await model.loadTodayBriefing(localDate: "2026-07-12")
        XCTAssertEqual(model.todayBriefing?.coverage.status, .offline)
        XCTAssertEqual(model.briefingLoadPhase, .loaded)
        XCTAssertNil(model.briefingError)
    }

    func testFailureWithoutCacheHasFiniteErrorState() async {
        let model = AppModel(repository: BriefingFailureRepository())

        await model.loadTodayBriefing(localDate: "2026-07-12")

        XCTAssertNil(model.todayBriefing)
        XCTAssertEqual(model.briefingLoadPhase, .failed("The grid service is temporarily unavailable."))
        XCTAssertEqual(model.briefingError, "The grid service is temporarily unavailable.")
        XCTAssertFalse(model.isRefreshingBriefing)
    }

    func testLatestLondonDateWinsWhenResponsesCompleteOutOfOrder() async throws {
        let firstDay = try TodayBriefingFixture.complete(localDate: "2026-07-12")
        let nextDay = try TodayBriefingFixture.complete(localDate: "2026-07-13")
        let repository = RacingBriefingRepository(firstDay: firstDay, nextDay: nextDay)
        let model = AppModel(repository: repository)

        let first = Task { @MainActor in
            await model.loadTodayBriefing(localDate: "2026-07-12")
        }
        try await Task.sleep(for: .milliseconds(20))
        await model.loadTodayBriefing(localDate: "2026-07-13")
        await first.value

        XCTAssertEqual(model.briefingRequestDate, "2026-07-13")
        XCTAssertEqual(model.todayBriefing?.displayPeriod.localDate, "2026-07-13")
        XCTAssertEqual(model.briefingLoadPhase, .loaded)
        XCTAssertNil(model.briefingError)
    }
}

private enum TodayBriefingFixture {
    static let asOf = Date(timeIntervalSince1970: 1_783_825_200) // 2026-07-12 03:00 UTC

    static func complete(localDate: String = "2026-07-12") throws -> TodayBriefing {
        try GridJSON.decoder.decode(
            TodayBriefing.self,
            from: completeData(localDate: localDate)
        )
    }

    static func partial() throws -> TodayBriefing {
        try GridJSON.decoder.decode(TodayBriefing.self, from: partialData())
    }

    static func offline() throws -> TodayBriefing {
        try GridJSON.decoder.decode(TodayBriefing.self, from: offlineData())
    }

    static func completeData(localDate: String = "2026-07-12") -> Data {
        Data(
            """
            {
              "schemaVersion":"1.0",
              "methodology":{
                "version":"50hz.briefing.v1","timezone":"Europe/London",
                "maxCurrentValues":3,"maxChanges":3,"maxNextMoments":3,"maxReportedEvents":3,
                "meaningfulChangeRule":"Only supplied threshold-qualified changes are included.",
                "currentRanking":"Server ranked.","changeRanking":"Server ranked.","nextRanking":"Server ranked.",
                "eventRanking":"Server ranked.","revisionRule":"Highest revision wins.","causalAttribution":false
              },
              "generatedAt":"2026-07-12T03:00:00.123456Z","asOf":"2026-07-12T03:00:00Z",
              "now":{
                "status":"complete","asOf":"2026-07-12T02:59:00Z","missingMetricIds":[],
                "text":"Current position: Demand is 28100 MW (observed); Carbon intensity is 84 gCO2/kWh (estimated); Frequency is 50.01 Hz (observed).",
                "values":[
                  {"stableId":"current-demand","metricId":"demand","label":"Demand","value":28100,"unit":"MW","factClass":"observed","observedAt":"2026-07-12T02:59:00Z","sourceIds":["elexon.indo"]},
                  {"stableId":"current-carbon","metricId":"carbon","label":"Carbon intensity","value":84,"unit":"gCO2/kWh","factClass":"estimated","observedAt":"2026-07-12T02:30:00Z","sourceIds":["neso.carbon"]},
                  {"stableId":"current-frequency","metricId":"frequency","label":"Frequency","value":50.01,"unit":"Hz","factClass":"observed","observedAt":"2026-07-12T02:59:00Z","sourceIds":["elexon.freq"]}
                ]
              },
              "displayPeriod":{"timezone":"Europe/London","localDate":"\(localDate)","name":"overnight","label":"Sunday overnight","startsAt":"2026-07-11T23:00:00Z","endsAt":"2026-07-12T05:00:00Z"},
              "headline":"Sunday overnight grid briefing",
              "summary":"This briefing includes three validated current values, three meaningful observed changes, three future moments, three of five active or upcoming reported events and one complete forecast best window.",
              "changes":[
                {"stableId":"change-demand","metricId":"demand","label":"Demand","direction":"up","currentValue":28100,"previousValue":27800,"delta":300,"unit":"MW","observedAt":"2026-07-12T02:59:00Z","comparisonPeriodId":"last-hour","significance":0.9,"sourceIds":["elexon.indo"],"text":"Demand rose by 300 MW over the previous hour."},
                {"stableId":"change-carbon","metricId":"carbon","label":"Carbon intensity","direction":"down","currentValue":84,"previousValue":101,"delta":-17,"unit":"gCO2/kWh","observedAt":"2026-07-12T02:30:00Z","comparisonPeriodId":"last-hour","significance":0.8,"sourceIds":["neso.carbon"],"text":"Carbon intensity fell by 17 gCO2/kWh over the previous hour."},
                {"stableId":"change-frequency","metricId":"frequency","label":"Frequency","direction":"up","currentValue":50.01,"previousValue":49.99,"delta":0.02,"unit":"Hz","observedAt":"2026-07-12T02:59:00Z","comparisonPeriodId":"last-hour","significance":0.7,"sourceIds":["elexon.freq"],"text":"Frequency rose by 0.02 Hz over the previous hour."},
                {"stableId":"legacy-extra","metricId":"legacy","label":"Legacy extra","direction":"up","currentValue":2,"previousValue":1,"delta":1,"unit":"MW","observedAt":"2026-07-12T02:00:00Z","comparisonPeriodId":"last-hour","significance":0.1,"sourceIds":[],"text":"A legacy fourth item remains bounded by the client."}
              ],
              "nextMoments":[
                {"stableId":"next-carbon","label":"Lowest national carbon forecast","startsAt":"2026-07-12T04:00:00Z","endsAt":"2026-07-12T04:30:00Z","factClass":"forecast","importance":0.9,"sourceIds":["neso.carbon"],"value":42,"unit":"gCO2/kWh","text":"Lowest national carbon forecast is forecast for 05:00 at 42 gCO2/kWh."},
                {"stableId":"next-demand","label":"National demand forecast peak","startsAt":"2026-07-12T17:00:00Z","endsAt":null,"factClass":"forecast","importance":0.8,"sourceIds":["elexon.ndf"],"value":34000,"unit":"MW","text":"National demand forecast peak is forecast for 18:00 at 34000 MW."},
                {"stableId":"next-reported","label":"Reported system action","startsAt":"2026-07-12T23:30:00Z","endsAt":"2026-07-13T00:00:00Z","factClass":"reported","importance":0.7,"sourceIds":["neso.notice"],"value":null,"unit":null,"text":"Reported system action is reported for Mon 13 Jul, 00:30."}
              ],
              "reportedEvents":{
                "totalCount":5,
                "items":[
                  {"stableId":"evt-critical","revisionId":"r2","revisionNumber":2,"title":"Critical active report","severity":"critical","timing":"active","publishedAt":"2026-07-12T02:30:00Z","startsAt":"2026-07-12T02:00:00Z","endsAt":null,"sourceIds":["remit"],"evidenceClass":"reported","text":"Reported: a critical active event."},
                  {"stableId":"evt-upcoming","revisionId":"r1","revisionNumber":1,"title":"Upcoming report","severity":"material","timing":"upcoming","publishedAt":"2026-07-12T02:00:00Z","startsAt":"2026-07-12T05:00:00Z","endsAt":"2026-07-12T06:00:00Z","sourceIds":["remit"],"evidenceClass":"reported","text":"Upcoming reported event at 06:00: planned work."},
                  {"stableId":"evt-material","revisionId":"r1","revisionNumber":1,"title":"Material active report","severity":"material","timing":"active","publishedAt":"2026-07-12T01:30:00Z","startsAt":null,"endsAt":null,"sourceIds":["syswarn"],"evidenceClass":"reported","text":"Reported: a material system warning."}
                ]
              },
              "bestWindow":{"stableId":"best-window","label":"Lowest national carbon forecast window","start":"2026-07-12T23:30:00Z","end":"2026-07-13T00:30:00Z","averageValue":42,"unit":"gCO2/kWh","sourceIds":["neso.carbon"],"coverageFraction":1.0,"factClass":"forecast","methodologyVersion":"50hz.local.flexible-use.v1","capturedAt":"2026-07-12T02:50:00Z","text":"Lowest national carbon forecast window: Mon 13 Jul, 00:30–Mon 13 Jul, 01:30, with an average forecast value of 42 gCO2/kWh."},
              "coverage":{"status":"complete","availableSections":["now","changes","next","reported_events","best_window"],"missingFamilies":[],"sourceCountsByState":{"live":5},"notes":[]},
              "sourceStatuses":[{"sourceId":"elexon.indo","dataset":"INDO","state":"live","revision":1,"observedAt":"2026-07-12T02:59:00Z","retrievedAt":"2026-07-12T03:00:00Z","detail":"Current demand"}],
              "comparisonPeriods":[{"id":"last-hour","label":"the previous hour","start":"2026-07-12T01:00:00Z","end":"2026-07-12T02:00:00Z"}],
              "revisionWatermark":{"revisionToken":"briefing:r1","asOf":"2026-07-12T03:00:00Z","observedThrough":"2026-07-12T02:59:00Z","forecastCapturedThrough":"2026-07-12T02:50:00Z","reportedThrough":"2026-07-12T02:30:00Z"},
              "limitations":["The briefing does not infer why a change occurred.","Omitted sections do not mean a value was zero."]
            }
            """.utf8
        )
    }

    static func partialData() -> Data {
        Data(
            """
            {
              "asOf":"2026-07-12T03:00:00Z",
              "displayPeriod":{"localDate":"2026-07-12","timezone":"Europe/London","name":"overnight","label":"Sunday overnight"},
              "headline":"Sunday overnight grid briefing","summary":"This briefing is partial.",
              "now":{"status":"partial","asOf":"2026-07-12T02:59:00Z","missingMetricIds":["carbon","frequency"],"text":"Current position: Demand is 28100 MW (observed).","values":[{"stableId":"demand","metricId":"demand","label":"Demand","value":28100,"unit":"MW","factClass":"observed","observedAt":"2026-07-12T02:59:00Z","sourceIds":["elexon.indo"]}]},
              "changes":[{"stableId":"change-demand","metricId":"demand","label":"Demand","direction":"up","currentValue":28100,"previousValue":27800,"delta":300,"unit":"MW","observedAt":"2026-07-12T02:59:00Z","comparisonPeriodId":"last-hour","significance":0.9,"sourceIds":["elexon.indo"],"text":"Demand rose by 300 MW over the previous hour."}],
              "nextMoments":[],"bestWindow":null,
              "reportedEvents":{"totalCount":1,"items":[{"stableId":"evt-material","revisionId":"r1","revisionNumber":1,"title":"Material report","severity":"material","timing":"active","publishedAt":"2026-07-12T02:00:00Z","startsAt":null,"endsAt":null,"sourceIds":["remit"],"evidenceClass":"reported","text":"Reported: a material event."}]},
              "coverage":{"status":"partial","availableSections":["now","changes","reported_events"],"missingFamilies":["forecast.carbon","best_window"],"sourceCountsByState":{"live":2,"unavailable":2},"notes":["Available sections were retained."]},
              "sourceStatuses":[],"comparisonPeriods":[],"limitations":["No causal attribution is made."]
            }
            """.utf8
        )
    }

    static func offlineData() -> Data {
        Data(
            """
            {
              "asOf":"2026-07-12T03:00:00Z",
              "displayPeriod":{"localDate":"2026-07-12","timezone":"Europe/London","name":"overnight","label":"Sunday overnight"},
              "headline":"Sunday overnight grid briefing","summary":"Live sources are unavailable; supplied evidence is retained.",
              "now":{"status":"partial","asOf":"2026-07-12T02:45:00Z","missingMetricIds":[],"text":"Current position: Frequency is 49.99 Hz (observed).","values":[{"stableId":"frequency","metricId":"frequency","label":"Frequency","value":49.99,"unit":"Hz","factClass":"observed","observedAt":"2026-07-12T02:45:00Z","sourceIds":["elexon.freq"]}]},
              "changes":[],"nextMoments":[],"bestWindow":null,
              "reportedEvents":{"totalCount":1,"items":[{"stableId":"evt-warning","revisionId":"r1","revisionNumber":1,"title":"System warning","severity":"material","timing":"active","publishedAt":"2026-07-12T02:30:00Z","startsAt":null,"endsAt":null,"sourceIds":["syswarn"],"evidenceClass":"reported","text":"Reported: a system warning."}]},
              "coverage":{"status":"offline","availableSections":["now","reported_events"],"missingFamilies":["forecast"],"sourceCountsByState":{"unavailable":4},"notes":[]},
              "sourceStatuses":[{"sourceId":"elexon.freq","dataset":"FREQ","state":"unavailable","revision":1,"observedAt":"2026-07-12T02:45:00Z","retrievedAt":"2026-07-12T02:46:00Z","detail":"Source unavailable"}],
              "comparisonPeriods":[],"limitations":["No causal attribution is made."]
            }
            """.utf8
        )
    }

    static func legacyData() -> Data {
        Data(
            """
            {"asOf":"2026-07-12T03:00:00Z","displayPeriod":{"localDate":"2026-07-12"}}
            """.utf8
        )
    }
}

private actor BriefingCacheFailureRepository: GridRepository {
    let briefing: TodayBriefing

    init(briefing: TodayBriefing) { self.briefing = briefing }

    func cachedTodayBriefing(localDate: String) async -> TodayBriefing? { briefing }
    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }
    func todayBriefing(localDate: String) async throws -> TodayBriefing {
        throw GridAPIError.transport(.notConnectedToInternet)
    }
}

private actor MutableBriefingRepository: GridRepository {
    var response: TodayBriefing

    init(response: TodayBriefing) { self.response = response }
    func setResponse(_ value: TodayBriefing) { response = value }
    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }
    func todayBriefing(localDate: String) async throws -> TodayBriefing { response }
}

private actor BriefingFailureRepository: GridRepository {
    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }
    func todayBriefing(localDate: String) async throws -> TodayBriefing {
        throw GridAPIError.httpStatus(code: 503, message: nil, retryAfter: "60")
    }
}

private actor RacingBriefingRepository: GridRepository {
    let firstDay: TodayBriefing
    let nextDay: TodayBriefing

    init(firstDay: TodayBriefing, nextDay: TodayBriefing) {
        self.firstDay = firstDay
        self.nextDay = nextDay
    }

    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }
    func todayBriefing(localDate: String) async throws -> TodayBriefing {
        if localDate == "2026-07-12" {
            try await Task.sleep(for: .milliseconds(150))
            return firstDay
        }
        try await Task.sleep(for: .milliseconds(10))
        return nextDay
    }
}

private final class BriefingRequestRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var requests: [URLRequest] = []

    @discardableResult
    func record(_ request: URLRequest) -> Int {
        lock.withLock {
            requests.append(request)
            return requests.count
        }
    }

    var requestCount: Int { lock.withLock { requests.count } }
    var urls: [URL] { lock.withLock { requests.compactMap(\.url) } }
    var lastConditionalETag: String? {
        lock.withLock { requests.last?.value(forHTTPHeaderField: "If-None-Match") }
    }
}

private final class BriefingURLProtocol: URLProtocol {
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
