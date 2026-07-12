import Foundation
import XCTest
@testable import FiftyHz

@MainActor
final class LocalReminderCoordinatorTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testInitAndRefreshDoNotPromptOrSchedule() async {
        let center = CoordinatorNotificationCenter(status: .notDetermined)
        let store = CoordinatorReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)

        await coordinator.refresh(for: plan())

        XCTAssertNil(coordinator.scheduledMetadata)
        XCTAssertFalse(coordinator.hasMaterialChange)
        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
        XCTAssertEqual(center.addCalls, 0)
    }

    func testExplicitSchedulePersistsThenRefreshOnlyReportsMaterialChange() async {
        let center = CoordinatorNotificationCenter(status: .authorized)
        let store = CoordinatorReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)
        let original = plan()

        let scheduled = await coordinator.schedule(original)
        XCTAssertTrue(scheduled)
        XCTAssertEqual(coordinator.feedback, .scheduled)
        XCTAssertNotNil(coordinator.scheduledMetadata)
        XCTAssertEqual(center.addCalls, 1)

        let moved = plan(
            start: original.start.addingTimeInterval(30 * 60),
            end: original.end.addingTimeInterval(30 * 60),
            intensity: 120
        )
        await coordinator.refresh(for: moved)

        XCTAssertTrue(coordinator.hasMaterialChange)
        XCTAssertEqual(center.addCalls, 1, "Forecast refresh must not move a reminder")
        XCTAssertEqual(coordinator.scheduledMetadata?.start, original.start)
    }

    func testExplicitUpdateReplacesAndCancelRemovesReminder() async {
        let center = CoordinatorNotificationCenter(status: .authorized)
        let store = CoordinatorReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)
        let original = plan()
        _ = await coordinator.schedule(original)
        let updated = plan(
            start: original.start.addingTimeInterval(60 * 60),
            end: original.end.addingTimeInterval(60 * 60),
            intensity: 80
        )

        let rescheduled = await coordinator.schedule(updated)
        XCTAssertTrue(rescheduled)
        XCTAssertEqual(center.addCalls, 2)
        XCTAssertEqual(coordinator.scheduledMetadata?.start, updated.start)

        let cancelled = await coordinator.cancel()
        XCTAssertTrue(cancelled)
        XCTAssertEqual(coordinator.feedback, .cancelled)
        XCTAssertNil(coordinator.scheduledMetadata)
        XCTAssertEqual(center.removed, ["50hz.local.flexible-use"])
        XCTAssertTrue(store.values.isEmpty)
    }

    func testDeniedPermissionProducesBoundedStateAndNoDelivery() async {
        let center = CoordinatorNotificationCenter(status: .denied)
        let coordinator = makeCoordinator(center: center, store: CoordinatorReminderStore())

        let scheduled = await coordinator.schedule(plan())
        XCTAssertFalse(scheduled)

        XCTAssertEqual(coordinator.feedback, .denied)
        XCTAssertEqual(center.authorizationRequests, 0)
        XCTAssertEqual(center.addCalls, 0)
    }

    func testExpiredMetadataIsCleanedWithoutPermissionAccess() async {
        let center = CoordinatorNotificationCenter(status: .notDetermined)
        let store = CoordinatorReminderStore()
        store.values["50hz.local.flexible-use"] = LocalReminderMetadata(
            identifier: "50hz.local.flexible-use",
            outwardRegion: "SW1A",
            scope: .gbNational,
            start: now.addingTimeInterval(-60),
            durationMinutes: 120,
            averageIntensityGCO2KWh: 100
        )
        let coordinator = makeCoordinator(center: center, store: store)

        await coordinator.refresh(for: nil)

        XCTAssertNil(coordinator.scheduledMetadata)
        XCTAssertTrue(store.values.isEmpty)
        XCTAssertEqual(center.removed, ["50hz.local.flexible-use"])
        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
    }

    private func makeCoordinator(
        center: CoordinatorNotificationCenter,
        store: CoordinatorReminderStore
    ) -> LocalReminderCoordinator {
        let scheduler = LocalReminderScheduler(
            center: center,
            store: store,
            now: { self.now },
            calendar: Calendar(identifier: .gregorian)
        )
        return LocalReminderCoordinator(scheduler: scheduler, now: { self.now })
    }

    private func plan(
        start: Date? = nil,
        end: Date? = nil,
        intensity: Double = 100
    ) -> LocalReminderPlan {
        let resolvedStart = start ?? now.addingTimeInterval(3_600)
        return LocalReminderPlan(
            localIdentifier: LocalReminderCoordinator.localIdentifier,
            activityLabel: "Laundry",
            outwardRegion: "SW1A",
            scope: .gbNational,
            forecastCapturedAt: now.addingTimeInterval(-300),
            start: resolvedStart,
            end: end ?? resolvedStart.addingTimeInterval(7_200),
            averageIntensityGCO2KWh: intensity
        )
    }
}

private final class CoordinatorNotificationCenter: LocalNotificationCenter {
    var status: LocalNotificationAuthorizationState
    var statusReads = 0
    var authorizationRequests = 0
    var addCalls = 0
    var removed: [String] = []

    init(status: LocalNotificationAuthorizationState) {
        self.status = status
    }

    func authorizationState() async -> LocalNotificationAuthorizationState {
        statusReads += 1
        return status
    }

    func requestAuthorization() async throws -> Bool {
        authorizationRequests += 1
        return true
    }

    func add(_ delivery: LocalNotificationDelivery) async throws {
        addCalls += 1
    }

    func removePending(identifier: String) async {
        removed.append(identifier)
    }
}

private final class CoordinatorReminderStore: LocalReminderMetadataStore {
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
