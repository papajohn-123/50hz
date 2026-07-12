import Foundation
import XCTest
@testable import FiftyHz

final class ForecastVerificationContractTests: XCTestCase {
    func testContractDecodesReviewedNationalPairAndIgnoresAdditiveFields() throws {
        let response = try ForecastVerificationFixture.response(additiveFields: true)
        let item = try XCTUnwrap(
            response.uniqueItem(
                metric: .nationalCarbonIntensity,
                horizon: .threeToTwelveHours
            )
        )

        XCTAssertEqual(response.schemaVersion, "1.0")
        XCTAssertEqual(response.minimumVerifiedSamples, 100)
        XCTAssertEqual(response.minimumCoverage, 0.90)
        XCTAssertEqual(item.status, .available)
        XCTAssertEqual(item.issueTimeBasis, "source_does_not_publish_issue_time")
        XCTAssertEqual(item.effectiveVintageTimeBasis, "retrieved_at")
        XCTAssertEqual(item.forecast.factClass, .forecast)
        XCTAssertEqual(item.outturn.factClass, .estimated)
        XCTAssertTrue(item.isDisplayEligible(in: response))
    }

    func testUnknownContractValuesRemainFiniteAndNonDisplayable() throws {
        let data = ForecastVerificationFixture.data()
            .replacingOccurrences(of: #""metric":"national_carbon_intensity""#, with: #""metric":"regional_carbon""#)
            .replacingOccurrences(of: #""status":"available""#, with: #""status":"provisional""#)
            .replacingOccurrences(of: #""factClass":"estimated""#, with: #""factClass":"modelled""#)
        let response = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(data.utf8)
        )
        let item = try XCTUnwrap(response.results.first)

        XCTAssertEqual(item.metric, .unknown)
        XCTAssertEqual(item.status, .unknown)
        XCTAssertEqual(item.outturn.factClass, .unknown)
        XCTAssertFalse(item.isDisplayEligible(in: response))
    }

    func testClientGateWithholdsNumbersWhenServerStatusOrThresholdIsNotEligible() throws {
        let statusData = ForecastVerificationFixture.data()
            .replacingOccurrences(of: #""status":"available","reason":"eligible""#, with: #""status":"insufficient_data","reason":"fewer_than_100_verified_samples""#)
        let statusResponse = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(statusData.utf8)
        )
        let statusItem = try XCTUnwrap(statusResponse.results.first)
        XCTAssertFalse(statusItem.isDisplayEligible(in: statusResponse))

        let thresholdData = ForecastVerificationFixture.data()
            .replacingOccurrences(of: #""minimumVerifiedSamples":100,"minimumCoverage":0.9"#, with: #""minimumVerifiedSamples":50,"minimumCoverage":0.5"#)
        let thresholdResponse = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(thresholdData.utf8)
        )
        let thresholdItem = try XCTUnwrap(thresholdResponse.results.first)
        XCTAssertFalse(thresholdItem.isDisplayEligible(in: thresholdResponse))
        XCTAssertTrue(
            ForecastVerificationPresentation.thresholdCopy(thresholdResponse)
                .contains("withheld")
        )
    }

    func testDuplicateReviewedPairIsAmbiguousAndNeverDisplayable() throws {
        let original = ForecastVerificationFixture.itemJSON
        let data = ForecastVerificationFixture.data(resultsJSON: "\(original),\(original)")
        let response = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(data.utf8)
        )

        XCTAssertNil(
            response.uniqueItem(
                metric: .nationalCarbonIntensity,
                horizon: .threeToTwelveHours
            )
        )
        XCTAssertTrue(response.results.allSatisfy { !$0.isDisplayEligible(in: response) })
    }

    func testLocalQualificationRequiresExactNationalPairAndWholeWindowHorizon() throws {
        let verification = try ForecastVerificationFixture.response()
        let local = try ForecastVerificationFixture.localResponse()
        let window = try XCTUnwrap(local.plan.recommendedWindow)
        let qualification = try XCTUnwrap(
            ForecastVerificationSelection.localCarbonQualification(
                response: verification,
                local: local,
                window: window
            )
        )

        XCTAssertEqual(qualification.mae, 12.4)
        XCTAssertEqual(qualification.horizon.id, .threeToTwelveHours)
        XCTAssertEqual(qualification.verifiedSamples, 180)
        XCTAssertLessThan(abs(qualification.coverage - (180.0 / 190.0)), 0.000_001)
        XCTAssertTrue(
            ForecastVerificationPresentation.localQualificationCopy(qualification)
                .contains("MAE")
        )
    }

    func testLocalQualificationIsWithheldForMethodMismatchRegionalScopeOrCrossedHorizon() throws {
        let verification = try ForecastVerificationFixture.response()

        let wrongMethod = try ForecastVerificationFixture.localResponse(
            replacing: (#"50hz.neso-carbon-intensity.national-forecast.v1"#, #"different-method"#)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: verification,
                local: wrongMethod,
                window: try XCTUnwrap(wrongMethod.plan.recommendedWindow)
            )
        )

        let regional = try ForecastVerificationFixture.localResponse(
            replacing: (#""geographyScope":"national""#, #""geographyScope":"regional""#)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: verification,
                local: regional,
                window: try XCTUnwrap(regional.plan.recommendedWindow)
            )
        )

        let crossed = try ForecastVerificationFixture.localResponse(
            replacing: (#""start":"2026-07-11T22:30:00Z","end":"2026-07-12T00:00:00Z""#, #""start":"2026-07-12T05:30:00Z","end":"2026-07-12T07:30:00Z""#)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: verification,
                local: crossed,
                window: try XCTUnwrap(crossed.plan.recommendedWindow)
            )
        )
    }

    func testLocalQualificationRejectsChangedCarbonOutturnBoundary() throws {
        let local = try ForecastVerificationFixture.localResponse()
        let window = try XCTUnwrap(local.plan.recommendedWindow)

        let observedData = ForecastVerificationFixture.data()
            .replacingOccurrences(of: #""factClass":"estimated""#, with: #""factClass":"observed""#)
        let observed = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(observedData.utf8)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: observed,
                local: local,
                window: window
            )
        )

        let futureMethodData = ForecastVerificationFixture.data()
            .replacingOccurrences(
                of: #""methodologyVersion":"neso-national-carbon-v1","factClass":"estimated""#,
                with: #""methodologyVersion":"neso-national-carbon-v2","factClass":"estimated""#
            )
        let futureMethod = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(futureMethodData.utf8)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: futureMethod,
                local: local,
                window: window
            )
        )

        let futureSourceData = ForecastVerificationFixture.data()
            .replacingOccurrences(
                of: #""sourceID":"neso.carbon-intensity-national""#,
                with: #""sourceID":"future.carbon""#
            )
        let futureSource = try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(futureSourceData.utf8)
        )
        XCTAssertNil(
            ForecastVerificationSelection.localCarbonQualification(
                response: futureSource,
                local: local,
                window: window
            )
        )
    }

    func testBoundedResponseRejectsUnboundedRows() {
        let rows = Array(repeating: ForecastVerificationFixture.itemJSON, count: 33)
            .joined(separator: ",")
        XCTAssertThrowsError(
            try GridJSON.decoder.decode(
                ForecastVerificationResponse.self,
                from: Data(ForecastVerificationFixture.data(resultsJSON: rows).utf8)
            )
        )
    }
}

@MainActor
final class ForecastVerificationViewModelTests: XCTestCase {
    func testCacheRemainsVisibleWhenRefreshFails() async throws {
        let cached = try ForecastVerificationFixture.response()
        let client = ForecastInspectionFakeClient(
            cached: cached,
            result: .failure(GridAPIError.transport(.notConnectedToInternet))
        )
        let model = ForecastVerificationViewModel(client: client)

        await model.load()

        XCTAssertEqual(model.response, cached)
        XCTAssertTrue(model.isFromCache)
        XCTAssertNotNil(model.errorMessage)
        XCTAssertFalse(model.isLoading)
    }

    func testRefreshReplacesCacheWithoutExposingCoreScreenFailure() async throws {
        let refreshed = try ForecastVerificationFixture.response()
        let client = ForecastInspectionFakeClient(cached: nil, result: .success(refreshed))
        let model = ForecastVerificationViewModel(client: client)

        await model.load()

        XCTAssertEqual(model.response, refreshed)
        XCTAssertFalse(model.isFromCache)
        XCTAssertNil(model.errorMessage)
    }
}

private actor ForecastInspectionFakeClient: InspectionDataProviding {
    let cached: ForecastVerificationResponse?
    let result: Result<ForecastVerificationResponse, Error>

    init(
        cached: ForecastVerificationResponse?,
        result: Result<ForecastVerificationResponse, Error>
    ) {
        self.cached = cached
        self.result = result
    }

    func cachedForecastVerification() -> ForecastVerificationResponse? { cached }
    func forecastVerification() throws -> ForecastVerificationResponse { try result.get() }
    func cachedSourceStatus() -> SourceStatusResponse? { nil }
    func sourceStatus() throws -> SourceStatusResponse { throw GridAPIError.invalidResponse }
    func cachedEventHistory(eventID: String) -> EventHistoryResponse? { nil }
    func eventHistory(eventID: String) throws -> EventHistoryResponse { throw GridAPIError.invalidResponse }
    func cachedExportSchema() -> ExportSchemaResponse? { nil }
    func exportSchema() throws -> ExportSchemaResponse { throw GridAPIError.invalidResponse }
    func prepareExport(_ request: ExportRequestSpec) throws -> PreparedExport { throw GridAPIError.invalidResponse }
}

enum ForecastVerificationFixture {
    static let itemJSON =
        """
        {
          "metric":"national_carbon_intensity","displayName":"National carbon-intensity forecast","unit":"gCO2/kWh","expectedIntervalMinutes":30,
          "horizon":{"id":"3_12h","minimumHours":3,"maximumHours":12},
          "status":"available","reason":"eligible","mae":12.4,"bias":-2.1,"wapePercent":8.7,
          "verifiedSamples":180,"expectedSamples":190,"coverage":0.9473684210526315,
          "verificationWindow":{"start":"2026-06-11T00:00:00Z","end":"2026-07-11T00:00:00Z"},
          "issueTimeBasis":"source_does_not_publish_issue_time","effectiveVintageTimeBasis":"retrieved_at",
          "forecast":{"sourceID":"neso.carbon-intensity-national","dataset":"NESO national carbon forecast","methodologyVersion":"50hz.neso-carbon-intensity.national-forecast.v1","factClass":"forecast"},
          "outturn":{"sourceID":"neso.carbon-intensity-national","dataset":"NESO national carbon estimate","methodologyVersion":"neso-national-carbon-v1","factClass":"estimated"},
          "registryVersion":"2026-07-12.forecast-verification.1","verificationMethodologyVersion":"50hz.forecast-verification.exact-vintage.v1",
          "evidenceChecksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","revision":2,
          "computedAt":"2026-07-12T04:00:00Z","sourceWatermarkAt":"2026-07-12T03:30:00Z"
        }
        """

    static func response(additiveFields: Bool = false) throws -> ForecastVerificationResponse {
        try GridJSON.decoder.decode(
            ForecastVerificationResponse.self,
            from: Data(data(additiveFields: additiveFields).utf8)
        )
    }

    static func data(
        resultsJSON: String = itemJSON,
        additiveFields: Bool = false
    ) -> String {
        let additive = additiveFields ? #", "futurePolicy":{"version":2}"# : ""
        return
            """
            {
              "schemaVersion":"1.0","generatedAt":"2026-07-12T04:00:00Z","minimumVerifiedSamples":100,"minimumCoverage":0.9,
              "results":[\(resultsJSON)],
              "methodology":{"pairing":"Pair exact stored vintages with compatible outturns.","scope":"Every result is national."}\(additive)
            }
            """
    }

    static func localResponse(
        replacing replacement: (String, String)? = nil
    ) throws -> LocalWindowsResponse {
        var json = localJSON
        if let replacement {
            json = json.replacingOccurrences(of: replacement.0, with: replacement.1)
        }
        return try GridJSON.decoder.decode(LocalWindowsResponse.self, from: Data(json.utf8))
    }

    private static let localJSON =
        """
        {
          "schemaVersion":"1.0","postcode":"SW1A","evaluatedAt":"2026-07-11T19:07:00Z",
          "forecast":{
            "geographyCode":"GB","geographyScope":"national","factClass":"forecast","seriesID":"gb-series",
            "sourceID":"neso.carbon-intensity-national","methodologyVersion":"50hz.neso-carbon-intensity.national-forecast.v1",
            "capturedAt":"2026-07-11T18:57:00Z","vintageAt":"2026-07-11T18:57:00Z","vintageBasis":"captured_at",
            "issueTimeBasis":"source_does_not_publish_issue_time","captureTimeBasis":"retrieved_at","captureState":"live","sourceRecordIDs":["fc:1"]
          },
          "plan":{
            "resultVersion":"1","status":"window_found","summary":"Window found.","continuous":true,"requestedDurationMinutes":90,
            "earliestStart":"2026-07-11T19:30:00Z","latestFinish":"2026-07-12T19:30:00Z",
            "recommendedWindow":{"start":"2026-07-11T22:30:00Z","end":"2026-07-12T00:00:00Z","averageIntensityGCO2KWh":35,"sourceRecordIDs":["fc:1"],"coverageFraction":1},
            "coverage":{"intervalMinutes":30,"requiredIntervalCount":3,"expectedIntervalCount":48,"availableIntervalCount":48,"coverageFraction":1,"gapStarts":[],"candidateStartCount":46,"completeCandidateCount":46}
          },
          "limitations":[]
        }
        """
}
