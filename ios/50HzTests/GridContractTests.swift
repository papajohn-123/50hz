import XCTest
@testable import FiftyHz

final class GridContractTests: XCTestCase {
    func testWelcomePresentationIsFirstRunOnlyAndSkippable() {
        XCTAssertTrue(WelcomePresentationPolicy.shouldPresent(hasCompletedWelcome: false))
        XCTAssertFalse(WelcomePresentationPolicy.shouldPresent(hasCompletedWelcome: true))
        XCTAssertEqual(AppVersionText.make(version: "1.0", build: "7"), "Version 1.0 (7)")
    }

    func testCarbonIntensityWordingUsesComparativeBands() {
        XCTAssertEqual(CarbonIntensityWording.label(for: 99.9), "Lower carbon")
        XCTAssertEqual(CarbonIntensityWording.label(for: 100), "Typical carbon")
        XCTAssertEqual(CarbonIntensityWording.label(for: 199.9), "Typical carbon")
        XCTAssertEqual(CarbonIntensityWording.label(for: 200), "Higher carbon")
    }

    func testRegionalMethodologyUsesForecastAndCaptureSemantics() {
        let source = SourceReference(
            id: "neso-region",
            name: "NESO Carbon Intensity",
            dataset: "REGIONAL",
            observedAt: Date(timeIntervalSince1970: 1_000),
            retrievedAt: Date(timeIntervalSince1970: 1_060),
            cadenceSeconds: 1_800
        )

        let copy = RegionalGridCopy.methodology(source: source)
        XCTAssertTrue(copy.contains("current half-hour carbon value is a regional forecast"))
        XCTAssertTrue(copy.contains("forecast period"))
        XCTAssertTrue(copy.contains("captured"))
        XCTAssertFalse(copy.localizedCaseInsensitiveContains("observed"))
    }

    func testGridJSONDecodesProductionFractionalAndWholeSecondDates() throws {
        struct DateEnvelope: Decodable {
            let timestamp: Date
        }

        let fractional = Data(#"{"timestamp":"2026-07-11T15:06:28.937491Z"}"#.utf8)
        let wholeSecond = Data(#"{"timestamp":"2026-07-11T15:06:28Z"}"#.utf8)

        let fractionalDate = try GridJSON.decoder.decode(DateEnvelope.self, from: fractional).timestamp
        let wholeSecondDate = try GridJSON.decoder.decode(DateEnvelope.self, from: wholeSecond).timestamp

        XCTAssertEqual(fractionalDate.timeIntervalSince1970, 1_783_782_388.937491, accuracy: 0.000_001)
        XCTAssertEqual(wholeSecondDate.timeIntervalSince1970, 1_783_782_388, accuracy: 0.001)
    }

    func testUnknownFuelSafelyMapsToOther() throws {
        let json = """
        {"fuel":"future_source","megawatts":25,"share":0.01,"changeOneHour":0,"rank":9,"factClass":"observed"}
        """
        let reading = try GridJSON.decoder.decode(FuelReading.self, from: Data(json.utf8))
        XCTAssertEqual(reading.fuel, .other)
        XCTAssertEqual(reading.share, 0.01)
    }

    func testConditionHeadlineCorrectsLegacyPublicSupplyWording() {
        let headline = ConditionHeadline(
            cleanliness: "Typical",
            balance: "Balanced",
            energyPosition: "Import-dependent",
            interpretation: "Wind is the largest source at 31% of generation. The generation mix includes imports."
        )
        let imports = FuelReading(
            fuel: .imports,
            megawatts: 3_500,
            share: 0.16,
            changeOneHour: 0,
            rank: 3,
            factClass: .observed
        )

        XCTAssertEqual(
            headline.publicInterpretation(for: [imports]),
            "Wind is the largest displayed supply component at 31% of this partial supply mix. The displayed supply mix includes imports."
        )
    }

    func testConditionHeadlinePreservesGenerationWordingWithoutImports() {
        let headline = ConditionHeadline(
            cleanliness: "Clean",
            balance: "Balanced",
            energyPosition: "Self-supplied",
            interpretation: "Wind is the largest source at 45% of generation."
        )
        let wind = FuelReading(
            fuel: .wind,
            megawatts: 10_000,
            share: 0.45,
            changeOneHour: 0,
            rank: 1,
            factClass: .observed
        )

        XCTAssertEqual(headline.publicInterpretation(for: [wind]), headline.interpretation)
    }

    func testDailyGameDecodesBackendSnakeCaseContract() throws {
        let json = """
        {
          "date":"2026-07-11",
          "missions":[
            {
              "mission_id":"2026-07-11:clean-window",
              "kind":"find_clean_window",
              "title":"Find tonight's cleanest half-hour",
              "available":false,
              "unavailable_reason":"Carbon forecast unavailable",
              "completion_payload":{"window_hours":1.5,"sample_count":4,"region":"GB"}
            }
          ],
          "prediction":{
            "prediction_id":"2026-07-11:energy-position-1800",
            "question":"Will Britain be importing or exporting at 18:00?",
            "choices":["importing","exporting"],
            "locks_at":"2026-07-11T16:45:00Z",
            "metric":"net_interconnector_flow_mw",
            "resolves_from":"2026-07-11T16:55:00Z",
            "resolves_to":"2026-07-11T17:05:00Z",
            "rule_version":1
          },
          "source_fresh":true
        }
        """

        let game = try GridJSON.decoder.decode(DailyGame.self, from: Data(json.utf8))

        XCTAssertEqual(game.date, "2026-07-11")
        XCTAssertTrue(game.sourceFresh)
        XCTAssertEqual(game.missions.first?.kind, .findCleanWindow)
        XCTAssertEqual(game.missions.first?.available, false)
        XCTAssertEqual(game.missions.first?.unavailableReason, "Carbon forecast unavailable")
        XCTAssertEqual(game.missions.first?.completionPayload["region"], .string("GB"))
        XCTAssertEqual(game.missions.first?.completionPayload["sample_count"], .integer(4))
        XCTAssertEqual(game.prediction?.choices, [.importing, .exporting])
        XCTAssertEqual(game.prediction?.ruleVersion, 1)
    }

    func testDailyGameToleratesNewMissionKindsAndMissingOptionalPayload() throws {
        let json = """
        {
          "date":"2026-07-11",
          "missions":[{"mission_id":"future","kind":"future_kind","title":"Explore","available":true}],
          "prediction":null,
          "source_fresh":false
        }
        """

        let game = try GridJSON.decoder.decode(DailyGame.self, from: Data(json.utf8))

        XCTAssertEqual(game.missions.first?.kind, .other)
        XCTAssertEqual(game.missions.first?.completionPayload, [:])
        XCTAssertNil(game.prediction)
    }

    func testSnapshotContractDecodesProvenanceAndSignConvention() throws {
        let json = """
        {
          "timestamp":"2026-07-11T14:00:00Z",
          "retrievedAt":"2026-07-11T14:01:00Z",
          "freshness":"live",
          "freshnessAgeSeconds":60,
          "headline":{"cleanliness":"Clean","balance":"Comfortable","energyPosition":"Exporting","interpretation":"Wind is leading."},
          "frequency":{"value":50.02,"unit":"Hz","factClass":"observed","sourceID":"frequency"},
          "demand":{"value":29800,"unit":"MW","factClass":"observed","sourceID":"demand"},
          "carbonIntensity":{"value":118,"unit":"gCO2/kWh","factClass":"estimated","sourceID":"carbon"},
          "generation":[{"fuel":"wind","megawatts":10500,"share":0.35,"changeOneHour":610,"rank":1,"factClass":"observed"}],
          "interconnectors":[{"id":"ifa","name":"IFA","countryCode":"FR","megawatts":-800,"factClass":"observed"}],
          "activeEvent":null,
          "sources":[{"id":"frequency","name":"Elexon","dataset":"FREQ","observedAt":"2026-07-11T14:00:00Z","retrievedAt":"2026-07-11T14:01:00Z","cadenceSeconds":60}]
        }
        """

        let snapshot = try GridJSON.decoder.decode(GridSnapshot.self, from: Data(json.utf8))
        XCTAssertEqual(snapshot.frequency?.value, 50.02)
        XCTAssertEqual(snapshot.sources.first?.cadenceSeconds, 60)
        XCTAssertEqual(snapshot.interconnectors.first?.directionLabel, "Exporting")
        XCTAssertEqual(snapshot.generation.first?.factClass, .observed)
        XCTAssertNil(snapshot.dataStatus)
        XCTAssertNil(snapshot.supply)
    }

    func testSnapshotDecodesAdditiveDataStatusAndPartialSupplyAccounting() throws {
        let json = """
        {
          "timestamp":"2026-07-11T14:00:00Z",
          "retrievedAt":"2026-07-11T14:01:00Z",
          "freshness":"live",
          "freshnessAgeSeconds":300,
          "headline":{"cleanliness":"Typical carbon","balance":"Comfortable","energyPosition":"Import-dependent","interpretation":"Wind leads."},
          "frequency":{"value":50.02,"unit":"Hz","factClass":"observed","sourceID":"elexon.fuelinst"},
          "demand":{"value":29800,"unit":"MW","factClass":"observed","sourceID":"elexon.fuelinst"},
          "carbonIntensity":{"value":118,"unit":"gCO2/kWh","factClass":"estimated","sourceID":"elexon.fuelinst"},
          "generation":[{"fuel":"wind","megawatts":10500,"share":0.70,"changeOneHour":610,"rank":1,"factClass":"observed"},{"fuel":"imports","megawatts":4500,"share":0.30,"changeOneHour":100,"rank":2,"factClass":"observed"}],
          "interconnectors":[],
          "activeEvent":null,
          "sources":[{"id":"elexon.fuelinst","name":"Elexon Insights","dataset":"FUELINST","observedAt":"2026-07-11T13:55:00Z","retrievedAt":"2026-07-11T14:00:00Z","cadenceSeconds":300}],
          "dataStatus":[{
            "family":"generation",
            "metricIDs":["generation.transmission_visible_by_fuel"],
            "sourceIDs":["elexon.fuelinst"],
            "sourceRecordIDs":["fuel:1"],
            "requiredForSnapshot":true,
            "evaluatedAt":"2026-07-11T14:00:00Z",
            "deliveryState":"healthy",
            "factState":"live",
            "observedAt":"2026-07-11T13:55:00Z",
            "publishedAt":"2026-07-11T13:55:30Z",
            "retrievedAt":"2026-07-11T14:00:00Z",
            "validTo":"2026-07-11T14:00:00Z",
            "observationAgeSeconds":300,
            "retrievalAgeSeconds":0,
            "expectedCadenceSeconds":300,
            "deliveryHealthySeconds":300,
            "deliveryStaleSeconds":600,
            "factLiveSeconds":600,
            "factStaleSeconds":900,
            "seriesCount":8
          }],
          "supply":{
            "methodologyVersion":"supply-accounting-v1",
            "boundary":"Transmission-visible generation and represented interconnectors.",
            "isComplete":false,
            "generationDataAvailable":true,
            "interconnectorDataAvailable":true,
            "domesticGenerationMW":10500,
            "grossImportsMW":1200,
            "grossExportsMW":1700,
            "netImportsMW":-500,
            "storageGenerationMW":200,
            "storageChargingMW":null,
            "legacyDisplayedGenerationMW":10500,
            "legacyMixBasis":"positive generation plus positive net imports",
            "note":"Not a complete Great Britain supply balance."
          }
        }
        """

        let snapshot = try GridJSON.decoder.decode(GridSnapshot.self, from: Data(json.utf8))

        XCTAssertEqual(snapshot.dataStatus?.first?.family, .generation)
        XCTAssertEqual(snapshot.dataStatus?.first?.deliveryState, .healthy)
        XCTAssertEqual(snapshot.dataStatus?.first?.factState, .live)
        XCTAssertEqual(snapshot.dataStatus?.first?.publishedAt, Date(timeIntervalSince1970: 1_783_778_130))
        XCTAssertEqual(snapshot.supply?.isComplete, false)
        XCTAssertEqual(snapshot.supply?.generationDataAvailable, true)
        XCTAssertEqual(snapshot.supply?.interconnectorDataAvailable, true)
        XCTAssertEqual(snapshot.supply?.grossExportsMW, 1_700)
        XCTAssertEqual(snapshot.supply?.netImportsMW, -500)
        XCTAssertNil(snapshot.supply?.storageChargingMW)
    }

    func testDataStatusToleratesFutureEnumValues() throws {
        let json = """
        {
          "family":"weather",
          "metricIDs":["future.metric"],
          "sourceIDs":[],
          "sourceRecordIDs":[],
          "requiredForSnapshot":false,
          "evaluatedAt":"2026-07-11T14:00:00Z",
          "deliveryState":"paused",
          "factState":"revised",
          "observedAt":null,
          "publishedAt":null,
          "retrievedAt":null,
          "validTo":null,
          "observationAgeSeconds":null,
          "retrievalAgeSeconds":null,
          "expectedCadenceSeconds":300,
          "deliveryHealthySeconds":300,
          "deliveryStaleSeconds":600,
          "factLiveSeconds":600,
          "factStaleSeconds":900,
          "seriesCount":0
        }
        """

        let status = try GridJSON.decoder.decode(DataFamilyStatus.self, from: Data(json.utf8))
        XCTAssertEqual(status.family, .other)
        XCTAssertEqual(status.deliveryState, .unknown)
        XCTAssertEqual(status.factState, .unknown)
    }

    func testCurrentDataSummarySeparatesCurrentDelayedAndOfflineStates() {
        let current = CurrentDataSummary.resolve(
            freshness: .critical,
            fallbackAgeSeconds: 30,
            statuses: [familyStatus(delivery: .healthy, fact: .live, observationAgeSeconds: 240)]
        )
        XCTAssertEqual(current, CurrentDataSummary(state: .current, observationAgeSeconds: 240))

        let delayed = CurrentDataSummary.resolve(
            freshness: .live,
            fallbackAgeSeconds: 30,
            statuses: [familyStatus(delivery: .delayed, fact: .live, observationAgeSeconds: 360)]
        )
        XCTAssertEqual(delayed, CurrentDataSummary(state: .delayed, observationAgeSeconds: 360))

        let offline = CurrentDataSummary.resolve(
            freshness: .live,
            fallbackAgeSeconds: 30,
            statuses: [familyStatus(delivery: .unavailable, fact: .unavailable, observationAgeSeconds: nil)]
        )
        XCTAssertEqual(offline, CurrentDataSummary(state: .offline, observationAgeSeconds: 30))

        let legacy = CurrentDataSummary.resolve(freshness: .stale, fallbackAgeSeconds: 1_200, statuses: nil)
        XCTAssertEqual(legacy, CurrentDataSummary(state: .delayed, observationAgeSeconds: 1_200))

        let agedCache = CurrentDataSummary.resolve(
            freshness: .offline,
            fallbackAgeSeconds: 7_200,
            statuses: [familyStatus(delivery: .healthy, fact: .live, observationAgeSeconds: 240)]
        )
        XCTAssertEqual(agedCache, CurrentDataSummary(state: .offline, observationAgeSeconds: 7_200))
    }

    func testFamilyStatusRecomputesDeliveryAndFactStateAtInspectionTime() {
        let captured = familyStatus(delivery: .healthy, fact: .live, observationAgeSeconds: 60)
        let inspectedAt = Date(timeIntervalSince1970: 1_700)

        let resolved = captured.resolved(at: inspectedAt)
        XCTAssertEqual(resolved.deliveryState, .stale)
        XCTAssertEqual(resolved.factState, .delayed)
        XCTAssertEqual(resolved.observationAgeSeconds, 700)
        XCTAssertEqual(resolved.retrievalAgeSeconds, 700)

        let summary = CurrentDataSummary.resolve(
            freshness: .live,
            fallbackAgeSeconds: 60,
            statuses: [captured],
            evaluatedAt: inspectedAt
        )
        XCTAssertEqual(summary, CurrentDataSummary(state: .delayed, observationAgeSeconds: 700))
    }

    func testTimelineInterpolatesContinuousValues() {
        let start = Date(timeIntervalSince1970: 1_000)
        let end = start.addingTimeInterval(600)
        let timeline = GridTimeline(
            sourceResolutionSeconds: 600,
            materialGapSeconds: 900,
            nowBoundary: end,
            samples: [
                sample(at: start, demand: 20_000, carbon: 100, frequency: 49.98, wind: 8_000),
                sample(at: end, demand: 22_000, carbon: 120, frequency: 50.02, wind: 10_000)
            ]
        )

        let midpoint = GridTimelineSampler(timeline: timeline).sample(at: start.addingTimeInterval(300))
        XCTAssertEqual(midpoint?.demandMW ?? 0, 21_000, accuracy: 0.001)
        XCTAssertEqual(midpoint?.carbonIntensity ?? 0, 110, accuracy: 0.001)
        XCTAssertEqual(midpoint?.frequencyHz ?? 0, 50.0, accuracy: 0.001)
        XCTAssertEqual(midpoint?.generation.first?.megawatts ?? 0, 9_000, accuracy: 0.001)
    }

    func testTimelineDoesNotInterpolateAcrossMaterialGap() {
        let start = Date(timeIntervalSince1970: 1_000)
        let end = start.addingTimeInterval(3_600)
        let timeline = GridTimeline(
            sourceResolutionSeconds: 600,
            materialGapSeconds: 900,
            nowBoundary: end,
            samples: [
                sample(at: start, demand: 20_000, carbon: 100, frequency: 50, wind: 8_000),
                sample(at: end, demand: 30_000, carbon: 200, frequency: 50, wind: 12_000)
            ]
        )

        let held = GridTimelineSampler(timeline: timeline).sample(at: start.addingTimeInterval(600))
        XCTAssertEqual(held?.demandMW, 20_000)
        XCTAssertEqual(held?.timestamp, start)
    }

    func testObservedForecastBoundaryStaysStepwise() {
        let start = Date(timeIntervalSince1970: 1_000)
        let boundary = start.addingTimeInterval(600)
        let observed = sample(at: start, demand: 20_000, carbon: 100, frequency: 50, wind: 8_000)
        let forecast = GridTimelineSample(
            timestamp: boundary,
            factClass: .forecast,
            demandMW: 24_000,
            carbonIntensity: 80,
            frequencyHz: nil,
            generation: [FuelReading(fuel: .wind, megawatts: 12_000, share: 0.5, changeOneHour: 0, rank: 1, factClass: .forecast)]
        )
        let timeline = GridTimeline(sourceResolutionSeconds: 600, materialGapSeconds: 900, nowBoundary: boundary, samples: [observed, forecast])

        let before = GridTimelineSampler(timeline: timeline).sample(at: boundary.addingTimeInterval(-1))
        XCTAssertEqual(before?.factClass, .observed)
        XCTAssertEqual(before?.demandMW, 20_000)
    }

    func testTimelineScaleNeverProducesNonFiniteAccessibilityPositions() {
        let instant = Date(timeIntervalSince1970: 1_000)
        let scale = GridTimelineScale(firstDate: instant, lastDate: instant)

        XCTAssertTrue(scale.ratio(for: instant).isFinite)
        XCTAssertEqual(scale.ratio(for: instant), 0)
        XCTAssertEqual(scale.date(at: .nan), instant)
    }

    func testTimelineScaleClampsAccessibleDatesToBounds() {
        let first = Date(timeIntervalSince1970: 1_000)
        let last = Date(timeIntervalSince1970: 1_600)
        let scale = GridTimelineScale(firstDate: first, lastDate: last)

        XCTAssertEqual(scale.ratio(for: first.addingTimeInterval(-60)), 0)
        XCTAssertEqual(scale.ratio(for: last.addingTimeInterval(60)), 1)
        XCTAssertEqual(scale.date(at: -1), first)
        XCTAssertEqual(scale.date(at: 2), last)
        XCTAssertEqual(scale.date(at: 0.5), Date(timeIntervalSince1970: 1_300))
    }

    private func sample(at date: Date, demand: Double, carbon: Double, frequency: Double, wind: Double) -> GridTimelineSample {
        GridTimelineSample(
            timestamp: date,
            factClass: .observed,
            demandMW: demand,
            carbonIntensity: carbon,
            frequencyHz: frequency,
            generation: [FuelReading(fuel: .wind, megawatts: wind, share: 0.4, changeOneHour: 0, rank: 1, factClass: .observed)]
        )
    }

    private func familyStatus(
        delivery: DataDeliveryState,
        fact: DataFactState,
        observationAgeSeconds: Int?
    ) -> DataFamilyStatus {
        let available = delivery != .unavailable && fact != .unavailable
        let date = Date(timeIntervalSince1970: 1_000)
        return DataFamilyStatus(
            family: .generation,
            metricIDs: ["generation.transmission_visible_by_fuel"],
            sourceIDs: available ? ["elexon.fuelinst"] : [],
            sourceRecordIDs: available ? ["fuel:1"] : [],
            requiredForSnapshot: true,
            evaluatedAt: date,
            deliveryState: delivery,
            factState: fact,
            observedAt: available ? date : nil,
            publishedAt: available ? date : nil,
            retrievedAt: available ? date : nil,
            validTo: available ? date.addingTimeInterval(300) : nil,
            observationAgeSeconds: observationAgeSeconds,
            retrievalAgeSeconds: available ? 0 : nil,
            expectedCadenceSeconds: 300,
            deliveryHealthySeconds: 300,
            deliveryStaleSeconds: 600,
            factLiveSeconds: 600,
            factStaleSeconds: 900,
            seriesCount: available ? 1 : 0
        )
    }
}
