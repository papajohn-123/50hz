import XCTest
@testable import FiftyHz

final class SnapshotTruthContractTests: XCTestCase {
    func testSnapshotDecodesMixedFreshnessWithoutClaimingOneInstant() throws {
        let snapshot = try GridJSON.decoder.decode(
            GridSnapshot.self,
            from: Data(snapshotJSON.utf8)
        )

        let summary = try XCTUnwrap(snapshot.freshnessSummary)
        XCTAssertEqual(summary.state, .mixed)
        XCTAssertEqual(summary.requiredFamilyCount, 3)
        XCTAssertEqual(summary.classifiedFamilyCount, 3)
        XCTAssertEqual(summary.observationSpreadSeconds, 420)
        XCTAssertFalse(summary.representsSingleInstant)
    }

    func testEventDecodesStructuredReportedFacts() throws {
        let snapshot = try GridJSON.decoder.decode(
            GridSnapshot.self,
            from: Data(snapshotJSON.utf8)
        )

        let event = try XCTUnwrap(snapshot.activeEvent)
        XCTAssertEqual(event.eventKind, "generation_unavailability")
        XCTAssertEqual(event.assetID, "DRAXX-4")
        XCTAssertEqual(event.assetName, "DRAXX-4")
        XCTAssertEqual(event.normalCapacityMW, 645)
        XCTAssertEqual(event.unavailableMW, 504)
        XCTAssertEqual(event.planned, false)
        XCTAssertEqual(event.locationStatus, "not_provided")
        XCTAssertEqual(event.scope, "national_grid_context")
        XCTAssertEqual(event.consumerImpact, "not_a_local_power_cut")
    }

    func testUnknownFreshnessSummaryStateRemainsForwardCompatible() throws {
        let json = """
        {
          "state": "future_state",
          "label": "New state",
          "detail": "A future server state.",
          "evaluatedAt": "2026-07-15T10:30:00Z",
          "requiredFamilyCount": 1,
          "currentFamilyCount": 1,
          "delayedFamilyCount": 0,
          "staleFamilyCount": 0,
          "unavailableFamilyCount": 0,
          "oldestRequiredObservedAt": "2026-07-15T10:25:00Z",
          "newestRequiredObservedAt": "2026-07-15T10:25:00Z",
          "observationSpreadSeconds": 0,
          "representsSingleInstant": false
        }
        """

        let summary = try GridJSON.decoder.decode(FreshnessSummary.self, from: Data(json.utf8))
        XCTAssertEqual(summary.state, .unknown)
    }

    private let snapshotJSON = """
    {
      "timestamp": "2026-07-15T10:29:00Z",
      "retrievedAt": "2026-07-15T10:30:00Z",
      "freshness": "critical",
      "freshnessAgeSeconds": 60,
      "headline": {
        "cleanliness": "Typical carbon",
        "balance": "Reported event",
        "energyPosition": "Generation constrained",
        "interpretation": "A reported generation unit is unavailable."
      },
      "frequency": null,
      "demand": {"value": 28100, "unit": "MW", "factClass": "observed", "sourceID": "elexon.indo"},
      "carbonIntensity": {"value": 132, "unit": "gCO2/kWh", "factClass": "estimated", "sourceID": "neso.carbon-intensity-national"},
      "generation": [],
      "interconnectors": [],
      "activeEvent": {
        "id": "evt_1234567890abcdef1234",
        "title": "DRAXX-4 unavailable · 504 MW",
        "summary": "The participant reports reduced available capacity.",
        "severity": "major",
        "evidenceClass": "reported",
        "startedAt": "2026-07-15T09:00:00Z",
        "sourceIDs": ["elexon.remit"],
        "isAuthoritativelyReported": true,
        "eventKind": "generation_unavailability",
        "status": "active",
        "endedAt": "2026-07-15T14:00:00Z",
        "updatedAt": "2026-07-15T10:20:00Z",
        "sourcePublishedAt": "2026-07-15T10:18:00Z",
        "assetID": "DRAXX-4",
        "assetName": "DRAXX-4",
        "fuelType": "biomass",
        "normalCapacityMW": 645,
        "unavailableMW": 504,
        "planned": false,
        "reportedCause": "Technical fault",
        "locationStatus": "not_provided",
        "scope": "national_grid_context",
        "consumerImpact": "not_a_local_power_cut"
      },
      "sources": [],
      "freshnessSummary": {
        "state": "mixed",
        "label": "Mixed timing",
        "detail": "Required inputs were observed at different times.",
        "evaluatedAt": "2026-07-15T10:30:00Z",
        "requiredFamilyCount": 3,
        "currentFamilyCount": 2,
        "delayedFamilyCount": 1,
        "staleFamilyCount": 0,
        "unavailableFamilyCount": 0,
        "oldestRequiredObservedAt": "2026-07-15T10:22:00Z",
        "newestRequiredObservedAt": "2026-07-15T10:29:00Z",
        "observationSpreadSeconds": 420,
        "representsSingleInstant": false
      }
    }
    """
}
