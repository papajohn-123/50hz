import Foundation
import XCTest
@testable import FiftyHz

@MainActor
final class LocalReminderSchedulerTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testInitAndStatusReadNeverRequestPermission() async {
        let center = ReminderCenterDouble(status: .notDetermined)
        let scheduler = makeScheduler(center: center)

        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
        let state = await scheduler.authorizationState()
        XCTAssertEqual(state, .notDetermined)
        XCTAssertEqual(center.statusReads, 1)
        XCTAssertEqual(center.authorizationRequests, 0)
        XCTAssertTrue(center.deliveries.isEmpty)
    }

    func testAuthorizedAndProvisionalScheduleWithoutPrompting() async {
        for status: LocalNotificationAuthorizationState in [.authorized, .provisional] {
            let center = ReminderCenterDouble(status: status)
            let scheduler = makeScheduler(center: center)

            let result = await scheduler.schedule(plan())

            XCTAssertEqual(result, .scheduled(identifier: "50hz.local.laundry"))
            XCTAssertEqual(center.authorizationRequests, 0)
            XCTAssertEqual(center.deliveries.count, 1)
        }
    }

    func testExplicitScheduleActionRequestsUndeterminedPermissionOnce() async {
        let center = ReminderCenterDouble(status: .notDetermined)
        center.authorizationResult = true
        let scheduler = makeScheduler(center: center)

        let result = await scheduler.schedule(plan())

        XCTAssertEqual(result, .scheduled(identifier: "50hz.local.laundry"))
        XCTAssertEqual(center.authorizationRequests, 1)
        XCTAssertEqual(center.deliveries.count, 1)
    }

    func testDeniedAndUnavailableReturnTypedStatesWithoutScheduling() async {
        let denied = ReminderCenterDouble(status: .denied)
        let unavailable = ReminderCenterDouble(status: .notAvailable)

        let deniedResult = await makeScheduler(center: denied).schedule(plan())
        let unavailableResult = await makeScheduler(center: unavailable).schedule(plan())
        XCTAssertEqual(deniedResult, .denied)
        XCTAssertEqual(unavailableResult, .notAvailable)
        XCTAssertEqual(denied.authorizationRequests, 0)
        XCTAssertEqual(unavailable.authorizationRequests, 0)
        XCTAssertTrue(denied.deliveries.isEmpty)
        XCTAssertTrue(unavailable.deliveries.isEmpty)
    }

    func testPromptDenialAndSystemFailuresNeverExposeRawErrors() async {
        let promptDenied = ReminderCenterDouble(status: .notDetermined)
        promptDenied.authorizationResult = false
        let deniedResult = await makeScheduler(center: promptDenied).schedule(plan())
        XCTAssertEqual(deniedResult, .denied)

        let permissionError = ReminderCenterDouble(status: .notDetermined)
        permissionError.authorizationError = ReminderStubError.secret("private system text")
        let permissionResult = await makeScheduler(center: permissionError).schedule(plan())
        XCTAssertEqual(permissionResult, .error)

        let addError = ReminderCenterDouble(status: .authorized)
        addError.addError = ReminderStubError.secret("private notification text")
        let addResult = await makeScheduler(center: addError).schedule(plan())
        XCTAssertEqual(addResult, .error)
    }

    func testPastAndInvalidPlansFailBeforeAnyPermissionRead() async {
        let pastCenter = ReminderCenterDouble(status: .authorized)
        let past = plan(start: now, end: now.addingTimeInterval(3_600))
        let pastResult = await makeScheduler(center: pastCenter).schedule(past)
        XCTAssertEqual(pastResult, .past)
        XCTAssertEqual(pastCenter.statusReads, 0)

        let invalidCenter = ReminderCenterDouble(status: .authorized)
        let invalid = plan(
            start: now.addingTimeInterval(3_600),
            end: now.addingTimeInterval(3_601)
        )
        let invalidResult = await makeScheduler(center: invalidCenter).schedule(invalid)
        XCTAssertEqual(invalidResult, .invalid)
        XCTAssertEqual(invalidCenter.statusReads, 0)

        let clockSkewCenter = ReminderCenterDouble(status: .authorized)
        let skewed = LocalReminderPlan(
            localIdentifier: "Laundry",
            activityLabel: "Laundry",
            outwardRegion: "SW1A",
            scope: .gbNational,
            forecastCapturedAt: now.addingTimeInterval(5 * 60 + 1),
            start: now.addingTimeInterval(3_600),
            end: now.addingTimeInterval(10_800),
            averageIntensityGCO2KWh: 100
        )
        let skewedResult = await makeScheduler(center: clockSkewCenter).schedule(skewed)
        XCTAssertEqual(skewedResult, .invalid)
        XCTAssertEqual(clockSkewCenter.statusReads, 0)
    }

    func testPayloadFiresAtExactStartAndExplainsForecastScopeAndCapture() async throws {
        let center = ReminderCenterDouble(status: .authorized)
        let scheduler = makeScheduler(center: center)
        let requested = plan()

        let result = await scheduler.schedule(requested)
        XCTAssertEqual(result, .scheduled(identifier: "50hz.local.laundry"))
        let delivery = try XCTUnwrap(center.deliveries["50hz.local.laundry"])
        XCTAssertEqual(delivery.fireDate, requested.start)
        XCTAssertEqual(delivery.destination, .local)
        XCTAssertTrue(delivery.title.contains("Forecast reminder"))
        XCTAssertTrue(delivery.title.contains("Laundry"))
        XCTAssertTrue(delivery.body.contains("Laundry"))
        XCTAssertTrue(delivery.body.contains("GB national forecast"))
        XCTAssertTrue(delivery.body.contains("saved area SW1A"))
        XCTAssertTrue(delivery.body.contains("SW1A"))
        XCTAssertTrue(delivery.body.contains("forecast captured"))
        XCTAssertTrue(delivery.body.contains("Average forecast intensity"))
        XCTAssertFalse(delivery.body.localizedCaseInsensitiveContains("SW1A 1AA"))
        XCTAssertFalse(delivery.body.localizedCaseInsensitiveContains("actual"))
    }

    func testFullPostcodeCannotEnterRegionOrActivityCopyOrPersistence() async {
        let center = ReminderCenterDouble(status: .authorized)
        let store = ReminderStoreDouble()
        let scheduler = makeScheduler(center: center, store: store)

        let fullRegion = plan(outwardRegion: "SW1A 1AA")
        let labelWithPostcode = plan(activityLabel: "Laundry at SW1A 1AA")

        let fullRegionResult = await scheduler.schedule(fullRegion)
        let labelResult = await scheduler.schedule(labelWithPostcode)
        XCTAssertEqual(fullRegionResult, .invalid)
        XCTAssertEqual(labelResult, .invalid)
        XCTAssertTrue(center.deliveries.isEmpty)
        XCTAssertTrue(store.values.isEmpty)
        XCTAssertEqual(center.statusReads, 0)
    }

    func testStableIdentifierReplacesOnlyAfterExplicitScheduleAndCancelRemovesBoth() async {
        let center = ReminderCenterDouble(status: .authorized)
        let store = ReminderStoreDouble()
        let scheduler = makeScheduler(center: center, store: store)
        let first = plan()
        let refreshed = plan(
            start: first.start.addingTimeInterval(3_600),
            end: first.end.addingTimeInterval(3_600),
            intensity: 40
        )

        let firstResult = await scheduler.schedule(first)
        XCTAssertEqual(firstResult, .scheduled(identifier: "50hz.local.laundry"))
        let scheduled = try! XCTUnwrap(store.values["50hz.local.laundry"])
        XCTAssertTrue(
            localReminderPlanHasMaterialChange(
                scheduled: scheduled,
                refreshed: metadata(from: refreshed)
            )
        )
        XCTAssertEqual(center.addCalls, 1, "Comparison must never silently reschedule")

        let refreshedResult = await scheduler.schedule(refreshed)
        XCTAssertEqual(refreshedResult, .scheduled(identifier: "50hz.local.laundry"))
        XCTAssertEqual(center.addCalls, 2)
        XCTAssertEqual(center.deliveries.count, 1)
        XCTAssertEqual(store.values["50hz.local.laundry"]?.start, refreshed.start)

        let cancelResult = await scheduler.cancel(localIdentifier: " Laundry ")
        XCTAssertEqual(cancelResult, .cancelled)
        XCTAssertNil(center.deliveries["50hz.local.laundry"])
        XCTAssertNil(store.values["50hz.local.laundry"])
        XCTAssertEqual(center.removedIdentifiers, ["50hz.local.laundry"])
    }

    func testStableIdentifierAndMinimalMetadataArePrivacyBounded() async throws {
        XCTAssertEqual(
            LocalReminderValidation.stableIdentifier(" EV-Top_Up "),
            "50hz.local.ev-top_up"
        )
        XCTAssertNil(LocalReminderValidation.stableIdentifier("private reminder / SW1A 1AA"))

        let suite = "50Hz.LocalReminderTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let store = UserDefaultsLocalReminderMetadataStore(defaults: defaults)
        let value = metadata(from: plan())

        try store.save(value)

        XCTAssertEqual(store.metadata(identifier: value.identifier), value)
        let persisted = try XCTUnwrap(defaults.data(forKey: "50hz.local-reminder-metadata.v1"))
        let text = String(decoding: persisted, as: UTF8.self)
        XCTAssertFalse(text.localizedCaseInsensitiveContains("activityLabel"))
        XCTAssertFalse(text.localizedCaseInsensitiveContains("1AA"))
        XCTAssertFalse(text.localizedCaseInsensitiveContains("captured"))
        XCTAssertFalse(text.localizedCaseInsensitiveContains("body"))
    }

    func testMaterialChangeThresholdsAreExactAndOptionalIntensityIsConservative() {
        let baseline = metadata(from: plan())
        XCTAssertFalse(material(baseline, startShift: 29 * 60 + 59))
        XCTAssertTrue(material(baseline, startShift: 30 * 60))
        XCTAssertTrue(material(baseline, durationMinutes: 150))
        XCTAssertTrue(material(baseline, scope: .regional))
        XCTAssertTrue(material(baseline, outwardRegion: "M1"))
        XCTAssertFalse(material(baseline, intensity: 114.99))
        XCTAssertTrue(material(baseline, intensity: 115))
        XCTAssertFalse(material(baseline, intensity: nil))

        let zero = LocalReminderMetadata(
            identifier: baseline.identifier,
            outwardRegion: baseline.outwardRegion,
            scope: baseline.scope,
            start: baseline.start,
            durationMinutes: baseline.durationMinutes,
            averageIntensityGCO2KWh: 0
        )
        XCTAssertTrue(material(zero, intensity: 1))
        XCTAssertFalse(material(zero, intensity: 0))

        let differentActivity = LocalReminderMetadata(
            identifier: "50hz.local.ev",
            outwardRegion: baseline.outwardRegion,
            scope: baseline.scope,
            start: baseline.start,
            durationMinutes: baseline.durationMinutes,
            averageIntensityGCO2KWh: baseline.averageIntensityGCO2KWh
        )
        XCTAssertTrue(
            localReminderPlanHasMaterialChange(
                scheduled: baseline,
                refreshed: differentActivity
            )
        )
    }

    private func makeScheduler(
        center: ReminderCenterDouble,
        store: ReminderStoreDouble = ReminderStoreDouble()
    ) -> LocalReminderScheduler {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(secondsFromGMT: 0)!
        return LocalReminderScheduler(
            center: center,
            store: store,
            now: { self.now },
            calendar: calendar
        )
    }

    private func plan(
        activityLabel: String = "Laundry",
        outwardRegion: String = "SW1A",
        start: Date? = nil,
        end: Date? = nil,
        intensity: Double? = 100
    ) -> LocalReminderPlan {
        let resolvedStart = start ?? now.addingTimeInterval(3_600)
        return LocalReminderPlan(
            localIdentifier: "Laundry",
            activityLabel: activityLabel,
            outwardRegion: outwardRegion,
            scope: .gbNational,
            forecastCapturedAt: now.addingTimeInterval(-600),
            start: resolvedStart,
            end: end ?? resolvedStart.addingTimeInterval(7_200),
            averageIntensityGCO2KWh: intensity
        )
    }

    private func metadata(from plan: LocalReminderPlan) -> LocalReminderMetadata {
        LocalReminderMetadata(
            identifier: LocalReminderValidation.stableIdentifier(plan.localIdentifier)!,
            outwardRegion: plan.outwardRegion,
            scope: plan.scope,
            start: plan.start,
            durationMinutes: Int(plan.end.timeIntervalSince(plan.start) / 60),
            averageIntensityGCO2KWh: plan.averageIntensityGCO2KWh
        )
    }

    private func material(
        _ baseline: LocalReminderMetadata,
        startShift: TimeInterval = 0,
        durationMinutes: Int? = nil,
        scope: LocalReminderForecastScope? = nil,
        outwardRegion: String? = nil,
        intensity: Double? = 100
    ) -> Bool {
        localReminderPlanHasMaterialChange(
            scheduled: baseline,
            refreshed: LocalReminderMetadata(
                identifier: baseline.identifier,
                outwardRegion: outwardRegion ?? baseline.outwardRegion,
                scope: scope ?? baseline.scope,
                start: baseline.start.addingTimeInterval(startShift),
                durationMinutes: durationMinutes ?? baseline.durationMinutes,
                averageIntensityGCO2KWh: intensity
            )
        )
    }
}

private final class ReminderCenterDouble: LocalNotificationCenter {
    var status: LocalNotificationAuthorizationState
    var statusReads = 0
    var authorizationRequests = 0
    var authorizationResult = true
    var authorizationError: Error?
    var addError: Error?
    var addCalls = 0
    var deliveries: [String: LocalNotificationDelivery] = [:]
    var removedIdentifiers: [String] = []

    init(status: LocalNotificationAuthorizationState) {
        self.status = status
    }

    func authorizationState() async -> LocalNotificationAuthorizationState {
        statusReads += 1
        return status
    }

    func requestAuthorization() async throws -> Bool {
        authorizationRequests += 1
        if let authorizationError { throw authorizationError }
        return authorizationResult
    }

    func add(_ delivery: LocalNotificationDelivery) async throws {
        addCalls += 1
        if let addError { throw addError }
        deliveries[delivery.identifier] = delivery
    }

    func removePending(identifier: String) async {
        removedIdentifiers.append(identifier)
        deliveries.removeValue(forKey: identifier)
    }
}

private final class ReminderStoreDouble: LocalReminderMetadataStore {
    var values: [String: LocalReminderMetadata] = [:]

    func metadata(identifier: String) -> LocalReminderMetadata? {
        values[identifier]
    }

    func save(_ metadata: LocalReminderMetadata) throws {
        values[metadata.identifier] = metadata
    }

    func remove(identifier: String) throws {
        values.removeValue(forKey: identifier)
    }
}

private enum ReminderStubError: Error {
    case secret(String)
}
