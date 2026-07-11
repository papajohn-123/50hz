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

