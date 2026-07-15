import XCTest
@testable import FiftyHz

final class GridPrimaryInsightTests: XCTestCase {
    func testAuthoritativeMaterialEventWinsEveryOtherSignal() {
        var snapshot = makeSnapshot(
            frequency: metric(49.7, factClass: .observed, sourceID: "frequency"),
            flows: [flow("ifa", megawatts: 4_000)],
            carbon: metric(250, factClass: .estimated, sourceID: "carbon")
        )
        snapshot.activeEvent = GridEvent(
            id: "event-1",
            title: "Interconnector restriction",
            summary: "A publisher notice reports restricted capacity; it does not assign a wider system cause.\nOpen the evidence.",
            severity: "material",
            evidenceClass: "reported",
            startedAt: .distantPast,
            sourceIDs: ["remit"],
            isAuthoritativelyReported: true
        )

        let insight = GridPrimaryInsight.make(snapshot: snapshot)

        XCTAssertEqual(insight.kind, .reportedEvent)
        XCTAssertEqual(insight.accent, .warning)
        XCTAssertTrue(insight.title.hasPrefix("Reported:"))
        XCTAssertFalse(insight.detail.contains("\n"))
        XCTAssertEqual(insight.contextualQuestion, "What has the publisher reported?")
    }

    func testUnverifiedOrNonMaterialEventCannotDisplaceAbnormalFrequency() {
        for event in [
            event(severity: "material", authoritative: false),
            event(severity: "info", authoritative: true)
        ] {
            var snapshot = makeSnapshot(
                frequency: metric(50.23, factClass: .observed, sourceID: "frequency")
            )
            snapshot.activeEvent = event

            let insight = GridPrimaryInsight.make(snapshot: snapshot)

            XCTAssertEqual(insight.kind, .frequency)
            XCTAssertTrue(insight.title.contains("above"))
            XCTAssertTrue(insight.detail.contains("does not identify a cause"))
        }
    }

    func testForecastFrequencyIsNeverPresentedAsAnAbnormalObservation() {
        let snapshot = makeSnapshot(
            frequency: metric(49.6, factClass: .forecast, sourceID: "frequency"),
            flows: [flow("ifa", megawatts: -2_500)]
        )

        let insight = GridPrimaryInsight.make(snapshot: snapshot)

        XCTAssertEqual(insight.kind, .crossBorderFlow)
        XCTAssertTrue(insight.title.contains("exporting"))
    }

    func testMaterialFlowRequiresCompleteObservedVisibleSet() {
        let observed = makeSnapshot(
            flows: [
                flow("ifa", megawatts: 1_600),
                flow("nsl", megawatts: 1_200)
            ]
        )
        let mixed = makeSnapshot(
            flows: [
                flow("ifa", megawatts: 2_500),
                flow("nsl", megawatts: 600, factClass: .forecast)
            ],
            carbon: metric(80, factClass: .estimated, sourceID: "carbon")
        )

        let observedInsight = GridPrimaryInsight.make(snapshot: observed)
        let mixedInsight = GridPrimaryInsight.make(snapshot: mixed)

        XCTAssertEqual(observedInsight.kind, .crossBorderFlow)
        XCTAssertTrue(observedInsight.detail.contains("2 observed connector readings"))
        XCTAssertFalse(observedInsight.detail.localizedCaseInsensitiveContains("total supply"))
        XCTAssertEqual(mixedInsight.kind, .carbon)
    }

    func testCarbonUsesConditionOnlyAndKeepsForecastExplicit() {
        let estimate = GridPrimaryInsight.make(
            snapshot: makeSnapshot(carbon: metric(82, factClass: .estimated, sourceID: "carbon"))
        )
        let forecast = GridPrimaryInsight.make(
            snapshot: makeSnapshot(carbon: metric(230, factClass: .forecast, sourceID: "carbon"))
        )

        XCTAssertEqual(estimate.kind, .carbon)
        XCTAssertEqual(estimate.title, "Lower-carbon period")
        XCTAssertTrue(estimate.detail.contains("GB estimate"))
        XCTAssertEqual(forecast.kind, .carbon)
        XCTAssertEqual(forecast.title, "Higher-carbon forecast")
        XCTAssertTrue(forecast.detail.contains("not an observation"))
        XCTAssertEqual(forecast.accent, .forecast)
    }

    func testTypicalCarbonFallsBackToLeadingPartialVisibleSupply() {
        let snapshot = makeSnapshot(
            carbon: metric(150, factClass: .estimated, sourceID: "carbon"),
            generation: [
                fuel(.gas, megawatts: 8_000, share: 0.26, rank: 2),
                fuel(.wind, megawatts: 10_500, share: 0.32, rank: 1)
            ]
        )

        let insight = GridPrimaryInsight.make(snapshot: snapshot)

        XCTAssertEqual(insight.kind, .visibleSupply)
        XCTAssertEqual(insight.title, "Wind leads this view")
        XCTAssertTrue(insight.detail.contains("partial transmission-visible view"))
        XCTAssertFalse(insight.detail.contains("total GB generation"))
    }

    func testForecastLeadingSupplyAndEmptyFallbackStayTruthful() {
        let forecast = GridPrimaryInsight.make(
            snapshot: makeSnapshot(
                generation: [fuel(.wind, megawatts: 11_000, share: 0.35, rank: 1, factClass: .forecast)]
            )
        )
        let empty = GridPrimaryInsight.make(snapshot: makeSnapshot(generation: []))

        XCTAssertEqual(forecast.kind, .visibleSupply)
        XCTAssertTrue(forecast.title.contains("forecast view"))
        XCTAssertTrue(forecast.detail.hasPrefix("Forecast"))
        XCTAssertEqual(forecast.accent, .forecast)
        XCTAssertEqual(empty.kind, .limitedData)
        XCTAssertEqual(empty.accent, .neutral)
    }

    private func makeSnapshot(
        frequency: GridMetric? = GridMetric(
            value: 50,
            unit: "Hz",
            factClass: .observed,
            sourceID: "frequency"
        ),
        flows: [InterconnectorFlow] = [],
        carbon: GridMetric = GridMetric(
            value: 150,
            unit: "gCO2/kWh",
            factClass: .estimated,
            sourceID: "carbon"
        ),
        generation: [FuelReading] = []
    ) -> GridSnapshot {
        GridSnapshot(
            timestamp: Date(timeIntervalSince1970: 1_800_000_000),
            retrievedAt: Date(timeIntervalSince1970: 1_800_000_010),
            freshness: .live,
            freshnessAgeSeconds: 10,
            headline: ConditionHeadline(
                cleanliness: "Typical",
                balance: "Balanced",
                energyPosition: "Stable",
                interpretation: "A bounded grid snapshot."
            ),
            frequency: frequency,
            demand: GridMetric(
                value: 30_000,
                unit: "MW",
                factClass: .observed,
                sourceID: "demand"
            ),
            carbonIntensity: carbon,
            generation: generation,
            interconnectors: flows,
            activeEvent: nil,
            sources: []
        )
    }

    private func metric(_ value: Double, factClass: FactClass, sourceID: String) -> GridMetric {
        GridMetric(value: value, unit: "", factClass: factClass, sourceID: sourceID)
    }

    private func flow(
        _ id: String,
        megawatts: Double,
        factClass: FactClass = .observed
    ) -> InterconnectorFlow {
        InterconnectorFlow(
            id: id,
            name: id.uppercased(),
            countryCode: "FR",
            megawatts: megawatts,
            factClass: factClass
        )
    }

    private func fuel(
        _ kind: FuelKind,
        megawatts: Double,
        share: Double,
        rank: Int,
        factClass: FactClass = .observed
    ) -> FuelReading {
        FuelReading(
            fuel: kind,
            megawatts: megawatts,
            share: share,
            changeOneHour: 0,
            rank: rank,
            factClass: factClass
        )
    }

    private func event(severity: String, authoritative: Bool) -> GridEvent {
        GridEvent(
            id: "event",
            title: "Publisher notice",
            summary: "Reported notice.",
            severity: severity,
            evidenceClass: "reported",
            startedAt: .distantPast,
            sourceIDs: ["source"],
            isAuthoritativelyReported: authoritative
        )
    }
}
