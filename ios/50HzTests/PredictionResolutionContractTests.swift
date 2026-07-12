import Foundation
import XCTest
@testable import FiftyHz

final class PredictionResolutionContractTests: XCTestCase {
    override func tearDown() {
        ResolutionStubURLProtocol.handler = nil
        super.tearDown()
    }

    func testPendingResolutionDecodesCamelCaseDefaultsAndAdditiveFields() throws {
        let resolution = try decodeResolution(
            date: "2026-07-11",
            stateFields: pendingFields,
            extraTopLevel: ",\"futureEvidenceLabel\":\"publisher-audited\"",
            omitLegacyDefaults: true
        )

        XCTAssertEqual(resolution.schemaVersion, "1.1")
        XCTAssertEqual(resolution.connectorRegistryVersion, "legacy-observed-set")
        XCTAssertEqual(resolution.state, .pending)
        XCTAssertNil(resolution.outcome)
        XCTAssertEqual(resolution.sourceIDs, [])
        XCTAssertTrue(resolution.matches(PredictionResolutionRequest(localDate: "2026-07-11")))
        XCTAssertFalse(resolution.matches(PredictionResolutionRequest(localDate: "2026-07-12")))
    }

    func testResolvedOutcomeIsComparedOnlyWithMatchingLocalPrediction() throws {
        let resolution = try decodeResolution(date: "2026-07-11", stateFields: resolvedFields)
        let correct = SavedPrediction(
            predictionID: resolution.predictionID,
            date: resolution.date,
            choice: .importing,
            selectedAt: Date()
        )
        let incorrect = SavedPrediction(
            predictionID: resolution.predictionID,
            date: resolution.date,
            choice: .exporting,
            selectedAt: Date()
        )
        let wrongDate = SavedPrediction(
            predictionID: resolution.predictionID,
            date: "2026-07-10",
            choice: .importing,
            selectedAt: Date()
        )

        XCTAssertTrue(resolution.matches(PredictionResolutionRequest(localDate: "2026-07-11")))
        XCTAssertEqual(LocalPredictionResult.derive(saved: correct, resolution: resolution)?.status, .correct)
        XCTAssertEqual(LocalPredictionResult.derive(saved: incorrect, resolution: resolution)?.status, .incorrect)
        XCTAssertNil(LocalPredictionResult.derive(saved: wrongDate, resolution: resolution))
    }

    func testVoidCanCarryCompleteNearBalancedEvidenceWithoutAWinningChoice() throws {
        let resolution = try decodeResolution(date: "2026-07-11", stateFields: voidFields)
        let saved = SavedPrediction(
            predictionID: resolution.predictionID,
            date: resolution.date,
            choice: .importing,
            selectedAt: Date()
        )

        XCTAssertEqual(resolution.state, .void)
        XCTAssertEqual(resolution.observedValueMW, 25)
        XCTAssertTrue(resolution.coverage.complete)
        XCTAssertEqual(LocalPredictionResult.derive(saved: saved, resolution: resolution)?.status, .void)
    }

    func testCorrectionAndCoverageInvariantsRejectMalformedPayloads() throws {
        let correction = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields
                .replacingOccurrences(of: "\"resolutionRevision\":1", with: "\"resolutionRevision\":2")
                .replacingOccurrences(of: "\"isCorrection\":false", with: "\"isCorrection\":true")
        )
        XCTAssertTrue(correction.isCorrection)
        XCTAssertTrue(correction.matches(PredictionResolutionRequest(localDate: correction.date)))

        let badCorrection = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields
                .replacingOccurrences(of: "\"resolutionRevision\":1", with: "\"resolutionRevision\":2")
        )
        XCTAssertFalse(badCorrection.matches(PredictionResolutionRequest(localDate: badCorrection.date)))

        let badCoverage = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields.replacingOccurrences(
                of: "\"coverageFraction\":1.0",
                with: "\"coverageFraction\":0.5"
            )
        )
        XCTAssertFalse(badCoverage.matches(PredictionResolutionRequest(localDate: badCoverage.date)))
    }

    func testKnownContractRejectsImpossibleStateEvidenceCombinations() throws {
        let wrongSign = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields.replacingOccurrences(of: "\"observedValueMW\":140.0", with: "\"observedValueMW\":-140.0")
        )
        XCTAssertFalse(wrongSign.matches(PredictionResolutionRequest(localDate: wrongSign.date)))

        let insideVoidBand = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields.replacingOccurrences(of: "\"observedValueMW\":140.0", with: "\"observedValueMW\":25.0")
        )
        XCTAssertFalse(insideVoidBand.matches(PredictionResolutionRequest(localDate: insideVoidBand.date)))

        let missingSource = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields.replacingOccurrences(of: "\"sourceIDs\":[\"elexon.fuelinst\"]", with: "\"sourceIDs\":[]")
        )
        XCTAssertTrue(missingSource.matches(PredictionResolutionRequest(localDate: missingSource.date)))
        XCTAssertFalse(missingSource.supportsLocalScoringContract)

        let missingRevisionIdentity = try decodeResolution(
            date: "2026-07-11",
            stateFields: resolvedFields.replacingOccurrences(
                of: "\"sourceRevisionKeys\":[\"elexon.fuelinst:INTFR:2026-07-11T17:00:00+00:00:r0\"]",
                with: "\"sourceRevisionKeys\":[]"
            )
        )
        XCTAssertTrue(missingRevisionIdentity.matches(PredictionResolutionRequest(localDate: missingRevisionIdentity.date)))
        XCTAssertFalse(missingRevisionIdentity.supportsLocalScoringContract)

        let pendingWithEvidence = try decodeResolution(
            date: "2026-07-11",
            stateFields: pendingFields.replacingOccurrences(of: "\"sourceIDs\":[]", with: "\"sourceIDs\":[\"elexon.fuelinst\"]")
        )
        XCTAssertFalse(pendingWithEvidence.matches(PredictionResolutionRequest(localDate: pendingWithEvidence.date)))

        let voidOutsideBand = try decodeResolution(
            date: "2026-07-11",
            stateFields: voidFields.replacingOccurrences(of: "\"observedValueMW\":25.0", with: "\"observedValueMW\":75.0")
        )
        XCTAssertFalse(voidOutsideBand.matches(PredictionResolutionRequest(localDate: voidOutsideBand.date)))
    }

    func testIncompleteEvidenceVoidRemainsSupportedWithoutInventingAnOutcome() throws {
        let fields = """
        \"state\":\"void\",\"outcome\":null,\"observedValueMW\":null,\"observedAt\":null,
        \"coverage\":{\"expectedConnectorCount\":10,\"observedConnectorCount\":4,\"coverageFraction\":0.4,\"complete\":false},
        \"sourceIDs\":[],\"sourceRecordIDs\":[],\"sourceRevisionKeys\":[],\"revisionWatermarkAt\":null,
        \"resolutionRevision\":1,\"isCorrection\":false
        """
        let resolution = try decodeResolution(date: "2026-07-11", stateFields: fields)
        let saved = SavedPrediction(
            predictionID: resolution.predictionID,
            date: resolution.date,
            choice: .exporting,
            selectedAt: Date()
        )

        XCTAssertTrue(resolution.matches(PredictionResolutionRequest(localDate: resolution.date)))
        XCTAssertTrue(resolution.supportsLocalScoringContract)
        XCTAssertEqual(LocalPredictionResult.derive(saved: saved, resolution: resolution)?.status, .void)
    }

    func testUnknownStateIsPreservedAndNeverScored() throws {
        let fields = pendingFields.replacingOccurrences(of: "\"state\":\"pending\"", with: "\"state\":\"reviewing\"")
        let resolution = try decodeResolution(date: "2026-07-11", stateFields: fields)
        let saved = SavedPrediction(
            predictionID: resolution.predictionID,
            date: resolution.date,
            choice: .importing,
            selectedAt: Date()
        )

        XCTAssertEqual(resolution.state, .unknown)
        XCTAssertTrue(resolution.matches(PredictionResolutionRequest(localDate: resolution.date)))
        XCTAssertEqual(LocalPredictionResult.derive(saved: saved, resolution: resolution)?.status, .unsupported)
    }

    func testUnknownOutcomeAndFutureContractAreRetainedButNeverScoredOrSelectable() throws {
        let unknownOutcomeFields = resolvedFields.replacingOccurrences(of: "\"outcome\":\"importing\"", with: "\"outcome\":\"settled_elsewhere\"")
        let unknownOutcome = try decodeResolution(date: "2026-07-11", stateFields: unknownOutcomeFields)
        let saved = SavedPrediction(
            predictionID: unknownOutcome.predictionID,
            date: unknownOutcome.date,
            choice: .importing,
            selectedAt: Date()
        )
        XCTAssertTrue(unknownOutcome.matches(PredictionResolutionRequest(localDate: unknownOutcome.date)))
        XCTAssertFalse(unknownOutcome.supportsLocalScoringContract)
        XCTAssertEqual(LocalPredictionResult.derive(saved: saved, resolution: unknownOutcome)?.status, .unsupported)

        let futureJSON = resolutionJSON(date: "2026-07-11", stateFields: pendingFields)
            .replacingOccurrences(of: "\"schemaVersion\":\"1.1\"", with: "\"schemaVersion\":\"2.0\"")
        let future = try GridJSON.decoder.decode(PredictionResolution.self, from: Data(futureJSON.utf8))
        XCTAssertTrue(future.matches(PredictionResolutionRequest(localDate: future.date)))
        XCTAssertFalse(future.supportsLocalChoiceContract)
        XCTAssertFalse(
            PredictionInteractionPolicy.canSelect(
                now: future.locksAt.addingTimeInterval(-60),
                locksAt: future.locksAt,
                state: future.state,
                supportsLocalChoiceContract: future.supportsLocalChoiceContract
            )
        )
    }

    func testPredictionInteractionLocksAtTheExactBackendInstant() throws {
        let resolution = try decodeResolution(date: "2026-07-11", stateFields: pendingFields)
        XCTAssertTrue(
            PredictionInteractionPolicy.canSelect(
                now: resolution.locksAt.addingTimeInterval(-0.001),
                locksAt: resolution.locksAt,
                state: .pending,
                supportsLocalChoiceContract: true
            )
        )
        XCTAssertFalse(
            PredictionInteractionPolicy.canSelect(
                now: resolution.locksAt,
                locksAt: resolution.locksAt,
                state: .pending,
                supportsLocalChoiceContract: true
            )
        )
        XCTAssertFalse(
            PredictionInteractionPolicy.canSelect(
                now: resolution.locksAt.addingTimeInterval(-60),
                locksAt: resolution.locksAt,
                state: .resolved,
                supportsLocalChoiceContract: true
            )
        )
    }

    func testLondonDateValidationRejectsImpossibleAndCrossDateKeys() {
        XCTAssertTrue(LondonDay.isValidLocalDateKey("2024-02-29"))
        XCTAssertFalse(LondonDay.isValidLocalDateKey("2026-02-29"))
        XCTAssertFalse(LondonDay.isValidLocalDateKey("2026-13-01"))
        XCTAssertEqual(PredictionResolutionRequest(localDate: "2026-02-29").localDate, "unknown-date")
        XCTAssertNotEqual(
            GridCacheKey.predictionResolution(localDate: "2026-07-11"),
            GridCacheKey.predictionResolution(localDate: "2026-07-12")
        )
    }

    func testLondonDateKeyRollsAtLondonMidnightDuringBritishSummerTime() throws {
        let before = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-07-11T22:59:59Z"))
        let after = try XCTUnwrap(ISO8601DateFormatter().date(from: "2026-07-11T23:00:00Z"))

        XCTAssertEqual(LondonDay.localDateKey(at: before), "2026-07-11")
        XCTAssertEqual(LondonDay.localDateKey(at: after), "2026-07-12")
        XCTAssertNotEqual(
            GridCacheKey.predictionResolution(localDate: LondonDay.localDateKey(at: before)),
            GridCacheKey.predictionResolution(localDate: LondonDay.localDateKey(at: after))
        )
    }

    func testRepositoryUsesDateScopedETagCacheAndDeduplicatesRequests() async throws {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let body = Data(resolutionJSON(date: "2026-07-11", stateFields: resolvedFields).utf8)
        let lock = NSLock()
        var count = 0
        var requestedPath: String?
        var conditionalETag: String?
        ResolutionStubURLProtocol.handler = { request in
            lock.withLock {
                count += 1
                requestedPath = request.url?.path
                if count == 2 { conditionalETag = request.value(forHTTPHeaderField: "If-None-Match") }
            }
            if lock.withLock({ count }) == 1 {
                Thread.sleep(forTimeInterval: 0.05)
                return (
                    HTTPURLResponse(
                        url: request.url!,
                        statusCode: 200,
                        httpVersion: "HTTP/1.1",
                        headerFields: ["ETag": "\"resolution-v1\""]
                    )!,
                    body
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

        async let first = repository.predictionResolution(localDate: "2026-07-11")
        async let duplicate = repository.predictionResolution(localDate: "2026-07-11")
        let (one, two) = try await (first, duplicate)
        let third = try await repository.predictionResolution(localDate: "2026-07-11")
        let cached = await repository.cachedPredictionResolution(localDate: "2026-07-11")

        XCTAssertEqual(one, two)
        XCTAssertEqual(two, third)
        XCTAssertEqual(cached, one)
        XCTAssertEqual(lock.withLock { count }, 2)
        XCTAssertEqual(lock.withLock { requestedPath }, "/v1/game/2026-07-11/resolution")
        XCTAssertEqual(lock.withLock { conditionalETag }, "\"resolution-v1\"")
    }

    func testRepositoryRejectsAndPurgesCrossDateResponse() async {
        let directory = temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let body = Data(resolutionJSON(date: "2026-07-12", stateFields: resolvedFields).utf8)
        ResolutionStubURLProtocol.handler = { request in
            (
                HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: "HTTP/1.1", headerFields: ["ETag": "\"wrong-date\""])!,
                body
            )
        }
        let repository = HTTPGridRepository(
            baseURL: URL(string: "https://unit.test")!,
            session: stubSession(),
            cache: GridDiskCache(directory: directory)
        )

        do {
            _ = try await repository.predictionResolution(localDate: "2026-07-11")
            XCTFail("Expected cross-date response rejection")
        } catch {
            XCTAssertEqual(error.localizedDescription, "The grid service returned data from an incompatible contract.")
        }
        let cached = await repository.cachedPredictionResolution(localDate: "2026-07-11")
        XCTAssertNil(cached)
    }

    private var pendingFields: String {
        """
        \"state\":\"pending\",\"outcome\":null,\"observedValueMW\":null,\"observedAt\":null,
        \"coverage\":{\"expectedConnectorCount\":10,\"observedConnectorCount\":0,\"coverageFraction\":0.0,\"complete\":false},
        \"sourceIDs\":[],\"sourceRecordIDs\":[],\"sourceRevisionKeys\":[],\"revisionWatermarkAt\":null,
        \"resolutionRevision\":0,\"isCorrection\":false
        """
    }

    private var resolvedFields: String {
        """
        \"state\":\"resolved\",\"outcome\":\"importing\",\"observedValueMW\":140.0,\"observedAt\":\"2026-07-11T17:00:00Z\",
        \"coverage\":{\"expectedConnectorCount\":10,\"observedConnectorCount\":10,\"coverageFraction\":1.0,\"complete\":true},
        \"sourceIDs\":[\"elexon.fuelinst\"],\"sourceRecordIDs\":[\"INTFR:140\"],
        \"sourceRevisionKeys\":[\"elexon.fuelinst:INTFR:2026-07-11T17:00:00+00:00:r0\"],
        \"revisionWatermarkAt\":\"2026-07-11T17:02:00Z\",\"resolutionRevision\":1,\"isCorrection\":false
        """
    }

    private var voidFields: String {
        """
        \"state\":\"void\",\"outcome\":null,\"observedValueMW\":25.0,\"observedAt\":\"2026-07-11T17:00:00Z\",
        \"coverage\":{\"expectedConnectorCount\":10,\"observedConnectorCount\":10,\"coverageFraction\":1.0,\"complete\":true},
        \"sourceIDs\":[\"elexon.fuelinst\"],\"sourceRecordIDs\":[],
        \"sourceRevisionKeys\":[\"elexon.fuelinst:INTFR:2026-07-11T17:00:00+00:00:r0\"],
        \"revisionWatermarkAt\":\"2026-07-11T17:02:00Z\",\"resolutionRevision\":1,\"isCorrection\":false
        """
    }

    private func decodeResolution(
        date: String,
        stateFields: String,
        extraTopLevel: String = "",
        omitLegacyDefaults: Bool = false
    ) throws -> PredictionResolution {
        try GridJSON.decoder.decode(
            PredictionResolution.self,
            from: Data(
                resolutionJSON(
                    date: date,
                    stateFields: stateFields,
                    extraTopLevel: extraTopLevel,
                    omitLegacyDefaults: omitLegacyDefaults
                ).utf8
            )
        )
    }

    private func resolutionJSON(
        date: String,
        stateFields: String,
        extraTopLevel: String = "",
        omitLegacyDefaults: Bool = false
    ) -> String {
        let defaults = omitLegacyDefaults
            ? ""
            : "\"schemaVersion\":\"1.1\",\"connectorRegistryVersion\":\"fixture-connectors-v1\","
        return """
        {
          \(defaults)
          \"predictionID\":\"\(date):energy-position-1800\",\"date\":\"\(date)\",
          \"question\":\"Will Britain be importing or exporting at 18:00?\",\"choices\":[\"importing\",\"exporting\"],
          \"metric\":\"net_interconnector_flow_mw\",\"ruleVersion\":1,
          \"rule\":\"Complete evidence nearest 18:00 Europe/London.\",
          \"locksAt\":\"2026-07-11T16:45:00Z\",\"evidenceFrom\":\"2026-07-11T16:55:00Z\",
          \"evidenceTo\":\"2026-07-11T17:05:00Z\",\"targetAt\":\"2026-07-11T17:00:00Z\",
          \"nearBalancedThresholdMW\":50.0,
          \(stateFields),
          \"evidenceChecksum\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",
          \"computedAt\":\"2026-07-11T17:06:00Z\",\"reason\":\"Deterministic resolution reason.\"
          \(extraTopLevel)
        }
        """
    }

    private func stubSession() -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ResolutionStubURLProtocol.self]
        configuration.urlCache = nil
        return URLSession(configuration: configuration)
    }

    private func temporaryDirectory() -> URL {
        URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("50HzResolutionTests-\(UUID().uuidString)", isDirectory: true)
    }
}

final class NotebookPersistenceTests: XCTestCase {
    private var suiteName = ""
    private var defaults: UserDefaults!

    override func setUp() {
        super.setUp()
        suiteName = "NotebookPersistenceTests.\(UUID().uuidString)"
        defaults = UserDefaults(suiteName: suiteName)
        defaults.removePersistentDomain(forName: suiteName)
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suiteName)
        defaults = nil
        super.tearDown()
    }

    func testPredictionJournalPreservesHistoryByPredictionAndDate() {
        let store = PredictionJournalStore(defaults: defaults)
        store.save(predictionID: "day-1", date: "2026-07-10", choice: .importing, selectedAt: Date(timeIntervalSince1970: 1))
        store.save(predictionID: "day-2", date: "2026-07-11", choice: .exporting, selectedAt: Date(timeIntervalSince1970: 2))
        store.save(predictionID: "day-2", date: "2026-07-11", choice: .importing, selectedAt: Date(timeIntervalSince1970: 3))

        XCTAssertEqual(store.predictions().count, 2)
        XCTAssertEqual(store.prediction(predictionID: "day-1", date: "2026-07-10")?.choice, .importing)
        XCTAssertEqual(store.prediction(predictionID: "day-2", date: "2026-07-11")?.choice, .importing)
    }

    func testMissionCannotCompleteUntilContextWasVisitedAndPersistsLearningNote() throws {
        let mission = try makeMission(kind: "inspect_interconnector")
        let store = MissionProgressStore(defaults: defaults)

        XCTAssertFalse(store.markCompleted(mission, date: "2026-07-11"))
        store.markVisited(mission, date: "2026-07-11", at: Date(timeIntervalSince1970: 1))
        XCTAssertTrue(store.markCompleted(mission, date: "2026-07-11", at: Date(timeIntervalSince1970: 2)))

        let record = store.record(missionID: mission.missionID, date: "2026-07-11")
        XCTAssertTrue(record?.isCompleted == true)
        XCTAssertEqual(
            record?.learnedNote,
            "Positive signed net interconnector flow means importing under 50Hz’s published convention."
        )
    }

    func testMissionNavigationUsesRealProductContextsAndUnknownKindHasNoFakeChevron() throws {
        XCTAssertEqual(try target(kind: "find_clean_window"), .local)
        XCTAssertEqual(try target(kind: "identify_largest_source"), .live)
        XCTAssertEqual(try target(kind: "inspect_interconnector"), .live)
        XCTAssertEqual(try target(kind: "open_event_evidence"), .today)
        XCTAssertNil(try target(kind: "future_mission"))
    }

    private func target(kind: String) throws -> MissionNavigationTarget? {
        MissionNavigationTarget.resolve(try makeMission(kind: kind), events: [])
    }

    private func makeMission(kind: String) throws -> GameMission {
        let json = """
        {"mission_id":"mission-1","kind":"\(kind)","title":"Inspect evidence","available":true,"completion_payload":{}}
        """
        return try GridJSON.decoder.decode(GameMission.self, from: Data(json.utf8))
    }
}

@MainActor
final class PredictionResolutionAppModelTests: XCTestCase {
    func testDifferentDateRequestsCannotOverwriteEachOtherDuringRolloverRace() async throws {
        let older = try makeResolution(date: "2026-07-10")
        let newer = try makeResolution(date: "2026-07-11")
        let repository = ResolutionRaceRepository(resolutions: [older.date: older, newer.date: newer])
        let model = AppModel(repository: repository)

        let slow = Task { await model.loadPredictionResolution(localDate: older.date) }
        let fast = Task { await model.loadPredictionResolution(localDate: newer.date) }
        await slow.value
        await fast.value

        XCTAssertEqual(model.predictionResolutions[older.date]?.predictionID, older.predictionID)
        XCTAssertEqual(model.predictionResolutions[newer.date]?.predictionID, newer.predictionID)
        XCTAssertEqual(model.predictionResolutionErrors, [String: String]())
    }

    func testOfflineRefreshRetainsProtectedCachedResultAndExposesHonestState() async throws {
        let resolution = try makeResolution(date: "2026-07-11")
        let repository = CachedResolutionThenFailureRepository(resolution: resolution)
        let model = AppModel(repository: repository)

        await model.loadPredictionResolution(localDate: resolution.date)

        XCTAssertEqual(model.predictionResolutions[resolution.date], resolution)
        XCTAssertTrue(model.predictionResolutionCacheDates.contains(resolution.date))
        XCTAssertEqual(model.predictionResolutionErrors[resolution.date], "There is no reliable network connection.")
        XCTAssertFalse(model.predictionResolutionLoadingDates.contains(resolution.date))
    }

    private func makeResolution(date: String) throws -> PredictionResolution {
        let json = """
        {
          "schemaVersion":"1.1","predictionID":"\(date):energy-position-1800","date":"\(date)",
          "question":"Will Britain be importing or exporting?","choices":["importing","exporting"],
          "metric":"net_interconnector_flow_mw","ruleVersion":1,"connectorRegistryVersion":"fixture-v1","rule":"Fixture rule",
          "locksAt":"\(date)T16:45:00Z","evidenceFrom":"\(date)T16:55:00Z","evidenceTo":"\(date)T17:05:00Z","targetAt":"\(date)T17:00:00Z",
          "state":"resolved","outcome":"importing","observedValueMW":140,"observedAt":"\(date)T17:00:00Z","nearBalancedThresholdMW":50,
          "coverage":{"expectedConnectorCount":1,"observedConnectorCount":1,"coverageFraction":1,"complete":true},
          "sourceIDs":["elexon.fuelinst"],"sourceRecordIDs":[],"sourceRevisionKeys":[],"revisionWatermarkAt":"\(date)T17:02:00Z",
          "evidenceChecksum":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","resolutionRevision":1,"isCorrection":false,
          "computedAt":"\(date)T17:06:00Z","reason":"Resolved from complete evidence."
        }
        """
        return try GridJSON.decoder.decode(PredictionResolution.self, from: Data(json.utf8))
    }
}

private final class ResolutionStubURLProtocol: URLProtocol {
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

private actor ResolutionRaceRepository: GridRepository {
    let resolutions: [String: PredictionResolution]

    init(resolutions: [String: PredictionResolution]) {
        self.resolutions = resolutions
    }

    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }

    func predictionResolution(localDate: String) async throws -> PredictionResolution {
        if localDate == resolutions.keys.sorted().first { try await Task.sleep(for: .milliseconds(50)) }
        guard let resolution = resolutions[localDate] else { throw GridAPIError.invalidResponse }
        return resolution
    }
}

private actor CachedResolutionThenFailureRepository: GridRepository {
    let resolution: PredictionResolution

    init(resolution: PredictionResolution) {
        self.resolution = resolution
    }

    func currentSnapshot() async throws -> GridSnapshot { throw GridRepositoryError.unsupportedFeature("Snapshot") }
    func timeline() async throws -> GridTimeline { throw GridRepositoryError.unsupportedFeature("Timeline") }
    func cachedPredictionResolution(localDate: String) async -> PredictionResolution? {
        resolution.date == localDate ? resolution : nil
    }
    func predictionResolution(localDate: String) async throws -> PredictionResolution {
        throw GridAPIError.transport(.notConnectedToInternet)
    }
}
