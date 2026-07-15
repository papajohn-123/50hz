import Foundation
import XCTest
@testable import FiftyHz

final class LocalWindowsContractTests: XCTestCase {
    override func tearDown() {
        LocalWindowsURLProtocol.handler = nil
        super.tearDown()
    }

    func testSuccessContractDecodesCaptureCoverageAndCompatibleComparison() throws {
        let response = try LocalWindowsFixture.response(durationMinutes: 120)

        XCTAssertEqual(response.schemaVersion, "1.0")
        XCTAssertEqual(response.postcode, "SW1A")
        XCTAssertEqual(response.forecast.geographyCode, "GB")
        XCTAssertEqual(response.forecast.geographyScope, "national")
        XCTAssertEqual(response.forecast.factClass, "forecast")
        XCTAssertNil(response.forecast.sourceIssuedAt)
        XCTAssertNotNil(response.forecast.capturedAt)
        XCTAssertEqual(response.plan.status, .lowerCarbonWindow)
        XCTAssertEqual(response.plan.requestedDurationMinutes, 120)
        XCTAssertEqual(response.plan.recommendedWindow?.averageIntensityGCO2KWh, 35)
        XCTAssertEqual(response.plan.coverage.gapStarts, [])
        XCTAssertEqual(response.plan.comparison?.status, .compatible)
        XCTAssertEqual(response.plan.comparison?.startNowMinusRecommendedGCO2KWh, 57.67)
        XCTAssertEqual(response.plan.comparison?.isMeaningful, true)
        XCTAssertEqual(LocalPlannerCopy.nationalScope, "GB national forecast · not regional or postcode-level")
        XCTAssertTrue(LocalPlannerCopy.support.contains("GB national forecast"))
    }

    func testLegacyCacheContractSuppliesDocumentedDefaultsAndNullableFields() throws {
        let response = try GridJSON.decoder.decode(
            LocalWindowsResponse.self,
            from: LocalWindowsFixture.legacyData(durationMinutes: 120)
        )

        XCTAssertEqual(response.schemaVersion, "1.0")
        XCTAssertNil(response.bounds)
        XCTAssertEqual(response.forecast.geographyCode, "GB")
        XCTAssertEqual(response.forecast.geographyScope, "national")
        XCTAssertEqual(response.forecast.factClass, "forecast")
        XCTAssertEqual(response.forecast.captureTimeBasis, "retrieved_at")
        XCTAssertEqual(response.forecast.captureState, "live")
        XCTAssertNil(response.forecast.sourceIssuedAt)
        XCTAssertEqual(response.forecast.sourceRecordIDs, [])
        XCTAssertEqual(response.plan.status, .insufficientCoverage)
        XCTAssertNil(response.plan.recommendedWindow)
        XCTAssertEqual(response.plan.coverage.intervalMinutes, 30)
        XCTAssertEqual(response.plan.comparison?.incompatibilityFields, [])
        XCTAssertNil(response.plan.comparison?.isMeaningful)
        XCTAssertEqual(response.limitations, [])
    }

    func testFutureStatusesDecodeWithoutBreakingTheLocalScreen() throws {
        let data = Data(
            String(decoding: LocalWindowsFixture.successData(durationMinutes: 120), as: UTF8.self)
                .replacingOccurrences(of: "lower_carbon_window", with: "future_plan_status")
                .replacingOccurrences(of: "compatible", with: "future_comparison_status")
                .utf8
        )

        let response = try GridJSON.decoder.decode(LocalWindowsResponse.self, from: data)

        XCTAssertEqual(response.plan.status, .unknown)
        XCTAssertEqual(response.plan.comparison?.status, .unknown)
        XCTAssertEqual(LocalPlannerCopy.resultTitle(for: response), "Lowest complete window found.")
    }

    func testPostcodeAndCacheKeysNeverRetainTheInwardCode() {
        XCTAssertEqual(PostcodePrivacy.outwardCode(from: " sw1a 1aa "), "SW1A")
        XCTAssertEqual(PostcodePrivacy.outwardCode(from: "GIR 0AA"), "GIR")
        XCTAssertEqual(PostcodePrivacy.outwardCode(from: ""), "SW1A")
        XCTAssertNil(PostcodePrivacy.validatedOutwardCode(from: "SW1A1A"))
        XCTAssertNil(PostcodePrivacy.validatedOutwardCode(from: "ARBITRARY123456"))
        XCTAssertNil(PostcodePrivacy.validatedOutwardCode(from: "SW1A/1AA"))
        XCTAssertEqual(PostcodePrivacy.outwardCode(from: "ARBITRARY123456"), "SW1A")

        let twoHours = GridCacheKey.localWindows(postcode: "SW1A 1AA", durationMinutes: 120)
        let fourHours = GridCacheKey.localWindows(postcode: "sw1a", durationMinutes: 240)
        let malformed = GridCacheKey.localWindows(postcode: "ARBITRARY123456", durationMinutes: 120)
        let earliest = Date(timeIntervalSince1970: 1_783_798_200)
        let latest = earliest.addingTimeInterval(24 * 60 * 60)
        let bounded = GridCacheKey.localWindows(
            postcode: "SW1A 1AA",
            durationMinutes: 120,
            earliest: earliest,
            latest: latest
        )
        let shifted = GridCacheKey.localWindows(
            postcode: "SW1A",
            durationMinutes: 120,
            earliest: earliest.addingTimeInterval(30 * 60),
            latest: latest
        )

        XCTAssertEqual(twoHours.rawValue, "local-windows-sw1a-120")
        XCTAssertEqual(fourHours.rawValue, "local-windows-sw1a-240")
        XCTAssertFalse(twoHours.rawValue.localizedCaseInsensitiveContains("1aa"))
        XCTAssertFalse(malformed.rawValue.localizedCaseInsensitiveContains("arbitrary"))
        XCTAssertNotEqual(twoHours, fourHours)
        XCTAssertNotEqual(twoHours, bounded)
        XCTAssertNotEqual(bounded, shifted)
        XCTAssertFalse(bounded.rawValue.localizedCaseInsensitiveContains("1aa"))
    }

    func testMalformedPostcodeFailsBeforeAnyRequestOrCacheLookup() async {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = URLRecorder()
        LocalWindowsURLProtocol.handler = { request in
            recorder.record(request.url)
            return (
                HTTPURLResponse(url: request.url!, statusCode: 500, httpVersion: nil, headerFields: nil)!,
                Data()
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        do {
            _ = try await repository.localWindows(
                postcode: "ARBITRARY123456",
                durationMinutes: 120
            )
            XCTFail("Expected local postcode validation")
        } catch let error as GridAPIError {
            guard case .invalidPostcode = error else {
                return XCTFail("Unexpected error: \(error)")
            }
        } catch {
            XCTFail("Unexpected error: \(error)")
        }

        XCTAssertNil(recorder.url)
        let malformedCache = await repository.cachedLocalWindows(
            postcode: "SW1A1A",
            durationMinutes: 120
        )
        XCTAssertNil(malformedCache)
        XCTAssertFalse(FileManager.default.fileExists(atPath: directory.path))
    }

    func testRepositoryUsesOutwardOnlyURLAndDurationSpecificProtectedCache() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let recorder = URLRecorder()
        LocalWindowsURLProtocol.handler = { request in
            recorder.record(request.url)
            return (
                HTTPURLResponse(
                    url: request.url!,
                    statusCode: 200,
                    httpVersion: "HTTP/1.1",
                    headerFields: ["ETag": "\"windows-v1\""]
                )!,
                LocalWindowsFixture.successData(durationMinutes: 120)
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        let response = try await repository.localWindows(
            postcode: "sw1a 1aa",
            durationMinutes: 120
        )
        let cached = await repository.cachedLocalWindows(
            postcode: "SW1A 9ZZ",
            durationMinutes: 120
        )
        let url = try XCTUnwrap(recorder.url)
        let components = try XCTUnwrap(URLComponents(url: url, resolvingAgainstBaseURL: false))

        XCTAssertEqual(url.path, "/v1/regions/SW1A/windows")
        XCTAssertEqual(components.queryItems, [URLQueryItem(name: "durationMinutes", value: "120")])
        XCTAssertFalse(url.absoluteString.localizedCaseInsensitiveContains("1aa"))
        XCTAssertEqual(response.plan.requestedDurationMinutes, 120)
        XCTAssertEqual(cached?.plan.requestedDurationMinutes, 120)
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("grid-local-windows-sw1a-120.json").path
            )
        )
    }

    func testRepositorySendsExactCustomBoundsAndCachesOnlyThatRequest() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let parser = ISO8601DateFormatter()
        let earliest = try XCTUnwrap(parser.date(from: "2026-07-11T19:30:00Z"))
        let latest = try XCTUnwrap(parser.date(from: "2026-07-12T19:30:00Z"))
        let request = LocalWindowsRequest(
            postcode: "SW1A 1AA",
            durationMinutes: 120,
            earliest: earliest,
            latest: latest
        )
        let recorder = URLRecorder()
        LocalWindowsURLProtocol.handler = { urlRequest in
            recorder.record(urlRequest.url)
            return (
                HTTPURLResponse(url: urlRequest.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                LocalWindowsFixture.successData(durationMinutes: 120)
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        let response = try await repository.localWindows(request: request)
        let boundedCache = await repository.cachedLocalWindows(request: request)
        let defaultCache = await repository.cachedLocalWindows(
            postcode: "SW1A",
            durationMinutes: 120
        )
        let url = try XCTUnwrap(recorder.url)
        let items = try XCTUnwrap(
            URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems
        )
        let values = Dictionary(
            uniqueKeysWithValues: items.compactMap { item in
                item.value.map { (item.name, $0) }
            }
        )
        let key = GridCacheKey.localWindows(
            postcode: request.outwardPostcode,
            durationMinutes: request.durationMinutes,
            earliest: earliest,
            latest: latest
        )

        XCTAssertEqual(url.path, "/v1/regions/SW1A/windows")
        XCTAssertEqual(values["durationMinutes"], "120")
        XCTAssertEqual(values["earliest"], "2026-07-11T19:30:00Z")
        XCTAssertEqual(values["latest"], "2026-07-12T19:30:00Z")
        XCTAssertTrue(response.matches(request))
        XCTAssertNotNil(boundedCache)
        XCTAssertNil(defaultCache)
        XCTAssertTrue(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("grid-\(key.rawValue).json").path
            )
        )
    }

    func testMismatchedCustomBoundsAreRejectedAndPurged() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let parser = ISO8601DateFormatter()
        let earliest = try XCTUnwrap(parser.date(from: "2026-07-11T19:30:00Z"))
        let latest = try XCTUnwrap(parser.date(from: "2026-07-12T20:00:00Z"))
        let request = LocalWindowsRequest(
            postcode: "SW1A",
            durationMinutes: 120,
            earliest: earliest,
            latest: latest
        )
        LocalWindowsURLProtocol.handler = { urlRequest in
            (
                HTTPURLResponse(url: urlRequest.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                LocalWindowsFixture.successData(durationMinutes: 120)
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        do {
            _ = try await repository.localWindows(request: request)
            XCTFail("Expected mismatched response bounds to be rejected")
        } catch {
            XCTAssertEqual(
                error.localizedDescription,
                "The grid service returned data from an incompatible contract."
            )
        }

        let cached = await repository.cachedLocalWindows(request: request)
        XCTAssertNil(cached)
        let key = GridCacheKey.localWindows(
            postcode: request.outwardPostcode,
            durationMinutes: request.durationMinutes,
            earliest: earliest,
            latest: latest
        )
        XCTAssertFalse(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("grid-\(key.rawValue).json").path
            )
        )
    }

    func testLegacyDiskCacheCanStillBeReadForTheMatchingRequest() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let cache = GridDiskCache(directory: directory)
        try await cache.store(
            LocalWindowsFixture.legacyData(durationMinutes: 120),
            for: .localWindows(postcode: "SW1A 1AA", durationMinutes: 120),
            etag: nil,
            lastModified: nil
        )
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: cache
        )

        let cached = await repository.cachedLocalWindows(
            postcode: "sw1a 9zz",
            durationMinutes: 120
        )

        XCTAssertEqual(cached?.postcode, "SW1A")
        XCTAssertEqual(cached?.plan.status, .insufficientCoverage)
        XCTAssertEqual(cached?.forecast.geographyScope, "national")
    }

    func testNonNationalScopeIsRejectedAndRemovedFromCache() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let unsafeData = Data(
            String(decoding: LocalWindowsFixture.successData(durationMinutes: 120), as: UTF8.self)
                .replacingOccurrences(of: #""geographyScope":"national""#, with: #""geographyScope":"regional""#)
                .utf8
        )
        let decoded = try GridJSON.decoder.decode(LocalWindowsResponse.self, from: unsafeData)
        XCTAssertFalse(decoded.hasSafeNationalForecastScope)

        LocalWindowsURLProtocol.handler = { request in
            (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!,
                unsafeData
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        do {
            _ = try await repository.localWindows(postcode: "SW1A", durationMinutes: 120)
            XCTFail("Expected the regional response to be rejected")
        } catch {
            XCTAssertEqual(error.localizedDescription, "The grid service returned data from an incompatible contract.")
        }

        let cached = await repository.cachedLocalWindows(postcode: "SW1A", durationMinutes: 120)
        XCTAssertNil(cached)
        XCTAssertFalse(
            FileManager.default.fileExists(
                atPath: directory.appendingPathComponent("grid-local-windows-sw1a-120.json").path
            )
        )
    }

    func testFastAPIValidationArrayProducesABoundedUsefulError() async {
        LocalWindowsURLProtocol.handler = { request in
            (
                HTTPURLResponse(url: request.url!, statusCode: 422, httpVersion: nil, headerFields: nil)!,
                Data(
                    """
                    {"detail":[
                      {"type":"multiple_of","loc":["query","durationMinutes"],"msg":"Input should be a multiple of 30"},
                      {"type":"value_error","loc":["query","continuous"],"msg":"Continuous use is required"}
                    ]}
                    """.utf8
                )
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: temporaryDirectory())
        )

        do {
            _ = try await repository.localWindows(postcode: "SW1A", durationMinutes: 120)
            XCTFail("Expected a validation error")
        } catch {
            XCTAssertEqual(
                error.localizedDescription,
                "Input should be a multiple of 30 Continuous use is required"
            )
            XCTAssertLessThanOrEqual(error.localizedDescription.count, 160)
        }
    }

    func testPlannerCopyShowsOnlyCompatibleStartNowEvidence() throws {
        let compatible = try LocalWindowsFixture.response(durationMinutes: 120)
        let window = try XCTUnwrap(compatible.plan.recommendedWindow)
        let compatibleCopy = try XCTUnwrap(
            LocalPlannerCopy.comparisonSummary(compatible.plan.comparison, recommended: window)
        )

        XCTAssertTrue(compatibleCopy.contains("lower forecast intensity"))
        XCTAssertTrue(compatibleCopy.contains("Starting now averages"))
        XCTAssertFalse(compatibleCopy.localizedCaseInsensitiveContains("saving"))
        XCTAssertTrue(LocalPlannerCopy.nationalScope.contains("not regional"))
        XCTAssertFalse(LocalPlannerCopy.nationalScope.localizedCaseInsensitiveContains("London"))

        let incompatibleData = Data(
            String(decoding: LocalWindowsFixture.successData(durationMinutes: 120), as: UTF8.self)
                .replacingOccurrences(of: #""status":"compatible""#, with: #""status":"incompatible_series""#)
                .utf8
        )
        let incompatible = try GridJSON.decoder.decode(LocalWindowsResponse.self, from: incompatibleData)

        XCTAssertNil(
            LocalPlannerCopy.comparisonSummary(
                incompatible.plan.comparison,
                recommended: try XCTUnwrap(incompatible.plan.recommendedWindow)
            )
        )
    }

    func testNoMeaningfulDifferenceHasExplicitNeutralCopy() throws {
        let data = Data(
            String(decoding: LocalWindowsFixture.successData(durationMinutes: 120), as: UTF8.self)
                .replacingOccurrences(of: "lower_carbon_window", with: "no_meaningful_difference")
                .replacingOccurrences(of: #""isMeaningful":true"#, with: #""isMeaningful":false"#)
                .utf8
        )
        let response = try GridJSON.decoder.decode(LocalWindowsResponse.self, from: data)
        let window = try XCTUnwrap(response.plan.recommendedWindow)

        XCTAssertEqual(LocalPlannerCopy.resultTitle(for: response), "No meaningful difference.")
        XCTAssertTrue(
            LocalPlannerCopy.comparisonSummary(response.plan.comparison, recommended: window)?
                .hasPrefix("No meaningful difference.") == true
        )
    }

    func testActivityPresetsAndCustomBoundsMatchThePlannerContract() {
        XCTAssertEqual(LocalActivityPreset.laundry.presetDurationMinutes, 120)
        XCTAssertEqual(LocalActivityPreset.dishwasher.presetDurationMinutes, 120)
        XCTAssertEqual(LocalActivityPreset.tumbleDryer.presetDurationMinutes, 90)
        XCTAssertEqual(LocalActivityPreset.evTopUp.presetDurationMinutes, 240)
        XCTAssertEqual(LocalActivityPreset.homeBattery.presetDurationMinutes, 180)
        XCTAssertEqual(LocalActivityPreset.heatPump.presetDurationMinutes, 120)
        XCTAssertNil(LocalActivityPreset.custom.presetDurationMinutes)
        XCTAssertEqual(LocalActivityPreset.custom.durationMinutes(customDurationMinutes: 0), 30)
        XCTAssertEqual(LocalActivityPreset.custom.durationMinutes(customDurationMinutes: 330), 330)
        XCTAssertEqual(LocalActivityPreset.custom.durationMinutes(customDurationMinutes: 900), 720)
        XCTAssertEqual(LocalPlannerCopy.durationLabel(minutes: 30), "30 min")
        XCTAssertEqual(LocalPlannerCopy.durationLabel(minutes: 720), "12 hr")
    }

    private func stubSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LocalWindowsURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzLocalTests-\(UUID().uuidString)", isDirectory: true)
    }
}

@MainActor
final class LocalWindowsAppModelTests: XCTestCase {
    func testCachedPlanIsRetainedWithBoundedRefreshError() async throws {
        let response = try LocalWindowsFixture.response(durationMinutes: 120)
        let model = AppModel(repository: LocalCacheThenFailureRepository(response: response))

        await model.loadLocalWindows(postcode: "SW1A 1AA", durationMinutes: 120)

        XCTAssertEqual(model.localWindows, response)
        XCTAssertEqual(model.localWindowsLoadPhase, .loaded)
        XCTAssertEqual(model.localWindowsRequest?.outwardPostcode, "SW1A")
        XCTAssertEqual(model.localWindowsRequest?.durationMinutes, 120)
        XCTAssertTrue(model.localWindowsIsFromCache)
        XCTAssertFalse(model.isRefreshingLocalWindows)
        XCTAssertEqual(model.localWindowsError, "There is no reliable network connection.")
    }

    func testFailureWithoutCacheHasExplicitFailedState() async {
        let model = AppModel(repository: LocalFailureRepository())

        await model.loadLocalWindows(postcode: "SW1A", durationMinutes: 120)

        XCTAssertNil(model.localWindows)
        XCTAssertEqual(model.localWindowsLoadPhase, .failed("The grid service is temporarily unavailable."))
        XCTAssertEqual(model.localWindowsError, "The grid service is temporarily unavailable.")
        XCTAssertFalse(model.isRefreshingLocalWindows)
    }

    func testLatestRequestWinsWhenResponsesCompleteOutOfOrder() async throws {
        let twoHours = try LocalWindowsFixture.response(durationMinutes: 120)
        let fourHours = try LocalWindowsFixture.response(durationMinutes: 240)
        let repository = OutOfOrderLocalRepository(twoHours: twoHours, fourHours: fourHours)
        let model = AppModel(repository: repository)

        let first = Task { @MainActor in
            await model.loadLocalWindows(postcode: "SW1A", durationMinutes: 120)
        }
        try await Task.sleep(for: .milliseconds(20))
        await model.loadLocalWindows(postcode: "SW1A", durationMinutes: 240)
        await first.value

        XCTAssertEqual(model.localWindowsRequest?.durationMinutes, 240)
        XCTAssertEqual(model.localWindows?.plan.requestedDurationMinutes, 240)
        XCTAssertNil(model.localWindowsError)
        XCTAssertEqual(model.localWindowsLoadPhase, .loaded)
    }

    func testInvalidDurationFailsBeforeRepositoryAccess() async {
        let repository = CountingLocalRepository()
        let model = AppModel(repository: repository)

        await model.loadLocalWindows(postcode: "SW1A", durationMinutes: 45)
        let callCount = await repository.callCount

        XCTAssertEqual(model.localWindowsLoadPhase, .failed("Choose a duration from 30 minutes to 12 hours, in 30-minute steps."))
        XCTAssertEqual(callCount, 0)
    }

    func testInvalidPostcodeFailsBeforeRepositoryAccess() async {
        let repository = CountingLocalRepository()
        let model = AppModel(repository: repository)

        await model.loadLocalWindows(postcode: "SW1A1A", durationMinutes: 120)
        let callCount = await repository.callCount

        XCTAssertEqual(model.localWindowsLoadPhase, .failed("Enter a valid UK outward or full postcode."))
        XCTAssertEqual(model.localWindowsError, "Enter a valid UK outward or full postcode.")
        XCTAssertEqual(callCount, 0)
    }

    func testInvalidCustomBoundsFailBeforeRepositoryAccess() async {
        let repository = CountingLocalRepository()
        let model = AppModel(repository: repository)
        let earliest = LocalPlanningBoundsPolicy.nextHalfHour(atOrAfter: Date())

        await model.loadLocalWindows(
            postcode: "SW1A",
            durationMinutes: 120,
            earliest: earliest,
            latest: earliest.addingTimeInterval(60 * 60)
        )
        let callCount = await repository.callCount

        XCTAssertEqual(
            model.localWindowsLoadPhase,
            .failed("The selected time range is too short for this activity.")
        )
        XCTAssertEqual(callCount, 0)
    }

    func testPartialCustomBoundsFailBeforeRepositoryAccess() async {
        let repository = CountingLocalRepository()
        let model = AppModel(repository: repository)
        let earliest = LocalPlanningBoundsPolicy.nextHalfHour(atOrAfter: Date())

        await model.loadLocalWindows(
            postcode: "SW1A",
            durationMinutes: 120,
            earliest: earliest,
            latest: nil
        )
        let callCount = await repository.callCount

        XCTAssertEqual(
            model.localWindowsLoadPhase,
            .failed("Choose both an earliest start and a latest finish.")
        )
        XCTAssertEqual(callCount, 0)
    }
}

private enum LocalWindowsFixture {
    static func response(durationMinutes: Int) throws -> LocalWindowsResponse {
        try GridJSON.decoder.decode(
            LocalWindowsResponse.self,
            from: successData(durationMinutes: durationMinutes)
        )
    }

    static func successData(durationMinutes: Int) -> Data {
        Data(
            """
            {
              "schemaVersion":"1.0",
              "postcode":"SW1A",
              "evaluatedAt":"2026-07-11T19:07:00.123456Z",
              "bounds":{
                "earliestStart":"2026-07-11T19:30:00Z","latestFinish":"2026-07-12T19:30:00Z",
                "earliestWasDefaulted":true,"latestWasDefaulted":true,"defaultRule":"Next UTC half-hour."
              },
              "forecast":{
                "geographyCode":"GB","geographyScope":"national","factClass":"forecast",
                "seriesID":"gb-series","sourceID":"neso.carbon-intensity-national","methodologyVersion":"national-v1",
                "sourceIssuedAt":null,"capturedAt":"2026-07-11T18:57:00Z","vintageAt":"2026-07-11T18:57:00Z",
                "vintageBasis":"captured_at","issueTimeBasis":"source_does_not_publish_issue_time",
                "captureTimeBasis":"retrieved_at","captureAgeSeconds":600,"captureStaleAfterSeconds":5400,
                "captureState":"live","sourceRecordIDs":["fc:0","fc:1","fc:2","fc:3"]
              },
              "plan":{
                "resultVersion":"1",
                "methodology":{
                  "version":"50hz.local.flexible-use.v1","intervalMinutes":30,"requiredWindowCoveragePercent":100,
                  "selectionRule":"Lowest complete continuous window.","tieBreakRule":"Earliest start wins.",
                  "meaningfulAbsoluteDeltaGCO2KWh":5.0,"meaningfulPercentDelta":5.0
                },
                "status":"lower_carbon_window","summary":"A lower-intensity window is available.","continuous":true,
                "requestedDurationMinutes":\(durationMinutes),"earliestStart":"2026-07-11T19:30:00Z","latestFinish":"2026-07-12T19:30:00Z",
                "recommendedWindow":{
                  "start":"2026-07-11T20:30:00Z","end":"2026-07-11T22:30:00Z",
                  "averageIntensityGCO2KWh":35.0,"sourceRecordIDs":["fc:2","fc:3"],"coverageFraction":1.0
                },
                "coverage":{
                  "intervalMinutes":30,"requiredIntervalCount":4,"expectedIntervalCount":48,"availableIntervalCount":48,
                  "coverageFraction":1.0,"gapStarts":[],"candidateStartCount":45,"completeCandidateCount":45
                },
                "comparison":{
                  "status":"compatible",
                  "startNowWindow":{
                    "start":"2026-07-11T19:07:00.123456Z","end":"2026-07-11T21:07:00.123456Z",
                    "averageIntensityGCO2KWh":92.67,"sourceRecordIDs":["fc:0","fc:1"],"coverageFraction":1.0
                  },
                  "incompatibilityFields":[],"startNowMinusRecommendedGCO2KWh":57.67,
                  "percentLowerThanStartNow":62.23,"isMeaningful":true
                }
              },
              "limitations":[]
            }
            """.utf8
        )
    }

    static func legacyData(durationMinutes: Int) -> Data {
        Data(
            """
            {
              "postcode":"SW1A",
              "evaluatedAt":"2026-07-11T19:07:00Z",
              "forecast":{"capturedAt":"2026-07-11T18:57:00Z"},
              "plan":{
                "requestedDurationMinutes":\(durationMinutes),
                "status":"insufficient_coverage",
                "coverage":{},
                "comparison":{"status":"insufficient_coverage"}
              }
            }
            """.utf8
        )
    }
}

private actor LocalCacheThenFailureRepository: GridRepository {
    let response: LocalWindowsResponse

    init(response: LocalWindowsResponse) {
        self.response = response
    }

    func cachedLocalWindows(postcode: String, durationMinutes: Int) async -> LocalWindowsResponse? {
        response
    }

    func currentSnapshot() async throws -> GridSnapshot {
        throw GridRepositoryError.unsupportedFeature("Snapshot")
    }

    func timeline() async throws -> GridTimeline {
        throw GridRepositoryError.unsupportedFeature("Timeline")
    }

    func localWindows(postcode: String, durationMinutes: Int) async throws -> LocalWindowsResponse {
        throw GridAPIError.transport(.notConnectedToInternet)
    }
}

private actor LocalFailureRepository: GridRepository {
    func currentSnapshot() async throws -> GridSnapshot {
        throw GridRepositoryError.unsupportedFeature("Snapshot")
    }

    func timeline() async throws -> GridTimeline {
        throw GridRepositoryError.unsupportedFeature("Timeline")
    }

    func localWindows(postcode: String, durationMinutes: Int) async throws -> LocalWindowsResponse {
        throw GridAPIError.httpStatus(code: 503, message: nil, retryAfter: "300")
    }
}

private actor OutOfOrderLocalRepository: GridRepository {
    let twoHours: LocalWindowsResponse
    let fourHours: LocalWindowsResponse

    init(twoHours: LocalWindowsResponse, fourHours: LocalWindowsResponse) {
        self.twoHours = twoHours
        self.fourHours = fourHours
    }

    func currentSnapshot() async throws -> GridSnapshot {
        throw GridRepositoryError.unsupportedFeature("Snapshot")
    }

    func timeline() async throws -> GridTimeline {
        throw GridRepositoryError.unsupportedFeature("Timeline")
    }

    func localWindows(postcode: String, durationMinutes: Int) async throws -> LocalWindowsResponse {
        if durationMinutes == 120 {
            try await Task.sleep(for: .milliseconds(150))
            return twoHours
        }
        try await Task.sleep(for: .milliseconds(10))
        return fourHours
    }
}

private actor CountingLocalRepository: GridRepository {
    private(set) var callCount = 0

    func currentSnapshot() async throws -> GridSnapshot {
        throw GridRepositoryError.unsupportedFeature("Snapshot")
    }

    func timeline() async throws -> GridTimeline {
        throw GridRepositoryError.unsupportedFeature("Timeline")
    }

    func localWindows(postcode: String, durationMinutes: Int) async throws -> LocalWindowsResponse {
        callCount += 1
        throw GridRepositoryError.unsupportedFeature("Local windows")
    }
}

private final class URLRecorder: @unchecked Sendable {
    private let lock = NSLock()
    private var storedURL: URL?

    var url: URL? { lock.withLock { storedURL } }

    func record(_ url: URL?) {
        lock.withLock { storedURL = url }
    }
}

private final class LocalWindowsURLProtocol: URLProtocol {
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
