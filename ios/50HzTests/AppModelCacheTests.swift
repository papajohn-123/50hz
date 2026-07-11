import Foundation
import XCTest
@testable import FiftyHz

@MainActor
final class AppModelCacheTests: XCTestCase {
    func testCachedSnapshotBecomesOfflineWhenRefreshCannotConnect() async {
        let repository = CacheThenFailureRepository(snapshot: makeSnapshot(), timeline: makeTimeline())
        let model = AppModel(repository: repository)

        await model.bootstrap()

        XCTAssertEqual(model.loadPhase, .loaded)
        XCTAssertEqual(model.snapshot?.freshness, .offline)
        XCTAssertNotNil(model.timeline)
        XCTAssertEqual(model.lastRefreshError, "There is no reliable network connection.")
    }

    func testForecastFrameNeverCarriesCurrentEventOrInterconnectorState() {
        var snapshot = makeSnapshot()
        snapshot.interconnectors = [
            InterconnectorFlow(id: "ifa", name: "IFA", countryCode: "FR", megawatts: 700, factClass: .observed)
        ]
        snapshot.activeEvent = GridEvent(
            id: "evt_current",
            title: "Current event",
            summary: "A current reported event.",
            severity: "important",
            evidenceClass: "reported",
            startedAt: snapshot.timestamp,
            sourceIDs: ["source"],
            isAuthoritativelyReported: true
        )
        let forecastTime = Date().addingTimeInterval(3_600)
        let model = AppModel(repository: CacheThenFailureRepository(snapshot: snapshot, timeline: makeTimeline()))
        model.snapshot = snapshot
        model.timeline = GridTimeline(
            sourceResolutionSeconds: 1_800,
            materialGapSeconds: 2_700,
            nowBoundary: Date(),
            samples: [
                GridTimelineSample(
                    timestamp: forecastTime,
                    factClass: .forecast,
                    demandMW: 24_000,
                    carbonIntensity: 78,
                    frequencyHz: nil,
                    generation: []
                )
            ]
        )
        model.selectedTime = forecastTime

        let presented = model.presentedSnapshot

        XCTAssertEqual(presented?.demand.factClass, .forecast)
        XCTAssertEqual(presented?.generation, [])
        XCTAssertEqual(presented?.interconnectors, [])
        XCTAssertNil(presented?.activeEvent)
        XCTAssertEqual(presented?.headline.energyPosition, "Carbon outlook")
    }

    func testForecastTimelineOlderThanOneHourIsWithheldEverywhere() {
        let model = AppModel(repository: CacheThenFailureRepository(snapshot: makeSnapshot(), timeline: makeTimeline()))
        model.snapshot = makeSnapshot()
        model.timeline = makeForecastTimeline(boundary: Date().addingTimeInterval(-3_601))
        model.selectedTime = Date().addingTimeInterval(3_600)

        XCTAssertFalse(model.isForecastTimelineUsable)
        XCTAssertFalse(model.displayTimeline?.samples.contains(where: { $0.factClass == .forecast }) == true)
        XCTAssertNil(model.selectedSample)
        XCTAssertTrue(model.forecastUnavailableReason.contains("one-hour display limit"))
    }

    func testForecastTimelineRequiresFreshSnapshot() {
        var snapshot = makeSnapshot()
        snapshot.freshness = .offline
        let model = AppModel(repository: CacheThenFailureRepository(snapshot: snapshot, timeline: makeTimeline()))
        model.snapshot = snapshot
        model.timeline = makeForecastTimeline(boundary: Date())

        XCTAssertFalse(model.isForecastTimelineUsable)
        XCTAssertTrue(model.forecastUnavailableReason.contains("delayed or offline"))
    }

    func testForecastTimelineRejectsSnapshotWhoseAgeExceedsOneHour() {
        var snapshot = makeSnapshot()
        snapshot.freshness = .live
        snapshot.freshnessAgeSeconds = 3_601
        let model = AppModel(repository: CacheThenFailureRepository(snapshot: snapshot, timeline: makeTimeline()))
        model.snapshot = snapshot
        model.timeline = makeForecastTimeline(boundary: Date())

        XCTAssertFalse(model.isForecastTimelineUsable)
        XCTAssertTrue(model.forecastUnavailableReason.contains("live snapshot is more than one hour old"))
    }

    func testRecentHeldForecastCanBeShownWithExplicitRefreshWarning() async {
        let snapshot = makeSnapshot()
        let timeline = makeForecastTimeline(boundary: Date().addingTimeInterval(-600))
        let model = AppModel(repository: TimelineRefreshFailureRepository(snapshot: snapshot, timeline: timeline))

        await model.bootstrap()

        XCTAssertEqual(model.snapshot?.freshness, .live)
        XCTAssertTrue(model.isForecastTimelineUsable)
        XCTAssertEqual(model.timelineRefreshError, "The grid service took too long to respond.")
        XCTAssertTrue(model.displayTimeline?.samples.contains(where: { $0.factClass == .forecast }) == true)
    }

    func testDailyGameRefreshRetainsLastConfirmedPlanOffline() async throws {
        let game = try makeDailyGame()
        let repository = ToggleDailyGameRepository(game: game)
        let model = AppModel(repository: repository)

        await model.refreshDailyGame()
        XCTAssertEqual(model.dailyGame, game)
        XCTAssertEqual(model.gameLoadPhase, .loaded)
        XCTAssertNil(model.gameRefreshError)

        await repository.setShouldFail(true)
        await model.refreshDailyGame()

        XCTAssertEqual(model.dailyGame, game)
        XCTAssertEqual(model.gameLoadPhase, .loaded)
        XCTAssertEqual(model.gameRefreshError, "There is no reliable network connection.")
    }

    private func makeSnapshot() -> GridSnapshot {
        let timestamp = Date().addingTimeInterval(-600)
        let source = SourceReference(id: "source", name: "Elexon", dataset: "test", observedAt: timestamp, retrievedAt: timestamp, cadenceSeconds: 60)
        return GridSnapshot(
            timestamp: timestamp,
            retrievedAt: timestamp,
            freshness: .live,
            freshnessAgeSeconds: 0,
            headline: ConditionHeadline(cleanliness: "Clean", balance: "Comfortable", energyPosition: "Balanced", interpretation: "Cached test state."),
            frequency: GridMetric(value: 50, unit: "Hz", factClass: .observed, sourceID: source.id),
            demand: GridMetric(value: 20_000, unit: "MW", factClass: .observed, sourceID: source.id),
            carbonIntensity: GridMetric(value: 100, unit: "gCO2/kWh", factClass: .estimated, sourceID: source.id),
            generation: [FuelReading(fuel: .wind, megawatts: 8_000, share: 0.4, changeOneHour: 0, rank: 1, factClass: .observed)],
            interconnectors: [],
            activeEvent: nil,
            sources: [source]
        )
    }

    private func makeTimeline() -> GridTimeline {
        let now = Date()
        return GridTimeline(
            sourceResolutionSeconds: 1_800,
            materialGapSeconds: 2_700,
            nowBoundary: now,
            samples: [
                GridTimelineSample(
                    timestamp: now,
                    factClass: .observed,
                    demandMW: 20_000,
                    carbonIntensity: 100,
                    frequencyHz: 50,
                    generation: [FuelReading(fuel: .wind, megawatts: 8_000, share: 0.4, changeOneHour: 0, rank: 1, factClass: .observed)]
                )
            ]
        )
    }

    private func makeForecastTimeline(boundary: Date) -> GridTimeline {
        GridTimeline(
            sourceResolutionSeconds: 1_800,
            materialGapSeconds: 2_700,
            nowBoundary: boundary,
            samples: [
                GridTimelineSample(
                    timestamp: boundary.addingTimeInterval(-1_800),
                    factClass: .observed,
                    demandMW: 20_000,
                    carbonIntensity: 100,
                    frequencyHz: 50,
                    generation: []
                ),
                GridTimelineSample(
                    timestamp: Date().addingTimeInterval(3_600),
                    factClass: .forecast,
                    demandMW: 24_000,
                    carbonIntensity: 78,
                    frequencyHz: nil,
                    generation: []
                )
            ]
        )
    }

    private func makeDailyGame() throws -> DailyGame {
        let json = """
        {
          "date":"2026-07-11",
          "missions":[{"mission_id":"mission-1","kind":"identify_largest_source","title":"Identify the largest source","available":true,"completion_payload":{}}],
          "prediction":null,
          "source_fresh":true
        }
        """
        return try GridJSON.decoder.decode(DailyGame.self, from: Data(json.utf8))
    }
}

private actor CacheThenFailureRepository: GridRepository {
    let snapshot: GridSnapshot
    let cachedGridTimeline: GridTimeline

    init(snapshot: GridSnapshot, timeline: GridTimeline) {
        self.snapshot = snapshot
        self.cachedGridTimeline = timeline
    }

    func cachedSnapshot() async -> GridSnapshot? { snapshot }
    func cachedTimeline() async -> GridTimeline? { cachedGridTimeline }
    func currentSnapshot() async throws -> GridSnapshot { throw GridAPIError.transport(.notConnectedToInternet) }
    func timeline() async throws -> GridTimeline { throw GridAPIError.transport(.notConnectedToInternet) }
}

private actor TimelineRefreshFailureRepository: GridRepository {
    let snapshot: GridSnapshot
    let cachedGridTimeline: GridTimeline

    init(snapshot: GridSnapshot, timeline: GridTimeline) {
        self.snapshot = snapshot
        self.cachedGridTimeline = timeline
    }

    func cachedSnapshot() async -> GridSnapshot? { snapshot }
    func cachedTimeline() async -> GridTimeline? { cachedGridTimeline }
    func currentSnapshot() async throws -> GridSnapshot { snapshot }
    func timeline() async throws -> GridTimeline { throw GridAPIError.transport(.timedOut) }
}

private actor ToggleDailyGameRepository: GridRepository {
    let game: DailyGame
    var shouldFail = false

    init(game: DailyGame) {
        self.game = game
    }

    func setShouldFail(_ value: Bool) {
        shouldFail = value
    }

    func currentSnapshot() async throws -> GridSnapshot {
        throw GridRepositoryError.unsupportedFeature("Snapshot")
    }

    func timeline() async throws -> GridTimeline {
        throw GridRepositoryError.unsupportedFeature("Timeline")
    }

    func dailyGame() async throws -> DailyGame {
        if shouldFail { throw GridAPIError.transport(.notConnectedToInternet) }
        return game
    }
}
