import Foundation
import XCTest
@testable import FiftyHz

@MainActor
final class PredictionReminderTests: XCTestCase {
    private let now = Date(timeIntervalSince1970: 1_800_000_000)

    func testRefreshDoesNotReadOrRequestNotificationPermission() async {
        let center = PredictionNotificationCenter(status: .notDetermined)
        let store = PredictionReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)
        let plan = makePlan(kind: .lock)

        await coordinator.refresh(predictionID: plan.predictionID, date: plan.date)

        XCTAssertTrue(coordinator.scheduled.isEmpty)
        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
        XCTAssertTrue(center.deliveries.isEmpty)
    }

    func testExplicitLockScheduleRequestsPermissionAndCreatesTruthfulDeepLink() async throws {
        let center = PredictionNotificationCenter(status: .notDetermined)
        let store = PredictionReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)
        let plan = makePlan(kind: .lock)

        let didSchedule = await coordinator.schedule(plan)

        XCTAssertTrue(didSchedule)
        XCTAssertEqual(center.statusReads, 1)
        XCTAssertEqual(center.authorizationRequests, 1)
        let identifier = try XCTUnwrap(
            PredictionReminderValidation.stableIdentifier(kind: .lock, date: plan.date)
        )
        let delivery = try XCTUnwrap(center.deliveries[identifier])
        XCTAssertEqual(delivery.fireDate, plan.locksAt.addingTimeInterval(-15 * 60))
        XCTAssertEqual(delivery.destination, .notebook)
        XCTAssertEqual(delivery.title, "Prediction locks in 15 minutes")
        XCTAssertTrue(delivery.body.contains("local choice"))
        XCTAssertTrue(delivery.body.contains("stay on this device"))
        XCTAssertFalse(delivery.body.contains(plan.predictionID))
        XCTAssertEqual(coordinator.scheduled[.lock]?.predictionID, plan.predictionID)
    }

    func testResultReminderDoesNotClaimAResultAlreadyExists() async throws {
        let center = PredictionNotificationCenter(status: .authorized)
        let scheduler = PredictionReminderScheduler(
            center: center,
            store: PredictionReminderStore(),
            now: { self.now }
        )
        let plan = makePlan(kind: .result)

        let state = await scheduler.schedule(plan)

        guard case .scheduled(let identifier) = state else {
            return XCTFail("Expected the result check reminder to schedule")
        }
        let delivery = try XCTUnwrap(center.deliveries[identifier])
        XCTAssertEqual(delivery.fireDate, plan.evidenceTo.addingTimeInterval(5 * 60))
        XCTAssertEqual(delivery.destination, .notebook)
        XCTAssertTrue(delivery.title.contains("evidence window closed"))
        XCTAssertTrue(delivery.body.contains("check for a published result"))
        XCTAssertTrue(delivery.body.contains("may still be pending"))
        XCTAssertFalse(delivery.body.localizedCaseInsensitiveContains("correct"))
        XCTAssertFalse(delivery.body.localizedCaseInsensitiveContains("won"))
    }

    func testCanonicalPolicyRejectsPastWrongDateAndInventedTimingBeforePermission() async {
        let center = PredictionNotificationCenter(status: .notDetermined)
        let scheduler = PredictionReminderScheduler(
            center: center,
            store: PredictionReminderStore(),
            now: { self.now }
        )
        let valid = makePlan(kind: .lock)
        let wrongDate = PredictionReminderPlan(
            predictionID: valid.predictionID,
            date: "2099-01-01",
            kind: .lock,
            fireDate: valid.fireDate,
            locksAt: valid.locksAt,
            evidenceTo: valid.evidenceTo
        )
        let inventedTiming = PredictionReminderPlan(
            predictionID: valid.predictionID,
            date: valid.date,
            kind: .lock,
            fireDate: valid.fireDate.addingTimeInterval(60),
            locksAt: valid.locksAt,
            evidenceTo: valid.evidenceTo
        )
        let past = PredictionReminderPlan(
            predictionID: valid.predictionID,
            date: LondonDay.localDateKey(at: now.addingTimeInterval(60)),
            kind: .lock,
            fireDate: now.addingTimeInterval(-60),
            locksAt: now.addingTimeInterval(14 * 60),
            evidenceTo: now.addingTimeInterval(60 * 60)
        )

        let wrongDateState = await scheduler.schedule(wrongDate)
        let inventedTimingState = await scheduler.schedule(inventedTiming)
        let pastState = await scheduler.schedule(past)

        XCTAssertEqual(wrongDateState, .invalid)
        XCTAssertEqual(inventedTimingState, .invalid)
        XCTAssertEqual(pastState, .past)
        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
        XCTAssertTrue(center.deliveries.isEmpty)
    }

    func testRefreshPurgesMismatchedStoredPredictionWithoutPermissionAccess() async throws {
        let center = PredictionNotificationCenter(status: .notDetermined)
        let store = PredictionReminderStore()
        let plan = makePlan(kind: .result)
        let identifier = try XCTUnwrap(
            PredictionReminderValidation.stableIdentifier(kind: .result, date: plan.date)
        )
        store.values[identifier] = PredictionReminderMetadata(
            identifier: identifier,
            predictionID: "superseded-prediction",
            date: plan.date,
            kind: .result,
            fireDate: plan.fireDate
        )
        let coordinator = makeCoordinator(center: center, store: store)

        await coordinator.refresh(predictionID: plan.predictionID, date: plan.date)

        XCTAssertNil(coordinator.scheduled[.result])
        XCTAssertNil(store.values[identifier])
        XCTAssertEqual(center.removed, [identifier])
        XCTAssertEqual(center.statusReads, 0)
        XCTAssertEqual(center.authorizationRequests, 0)
    }

    func testCancelRemovesOnlyTheSelectedStableReminder() async throws {
        let center = PredictionNotificationCenter(status: .authorized)
        let store = PredictionReminderStore()
        let coordinator = makeCoordinator(center: center, store: store)
        let lock = makePlan(kind: .lock)
        let result = makePlan(kind: .result)
        _ = await coordinator.schedule(lock)
        _ = await coordinator.schedule(result)

        let cancelled = await coordinator.cancel(kind: .lock, date: lock.date)

        XCTAssertTrue(cancelled)
        XCTAssertNil(coordinator.scheduled[.lock])
        XCTAssertNotNil(coordinator.scheduled[.result])
        let lockID = try XCTUnwrap(
            PredictionReminderValidation.stableIdentifier(kind: .lock, date: lock.date)
        )
        let resultID = try XCTUnwrap(
            PredictionReminderValidation.stableIdentifier(kind: .result, date: result.date)
        )
        XCTAssertNil(store.values[lockID])
        XCTAssertNotNil(store.values[resultID])
        XCTAssertEqual(center.removed, [lockID])
    }

    func testNotificationDestinationsMapOnlyToExpectedTabs() {
        XCTAssertEqual(
            NotificationNavigation.tab(
                from: [NotificationNavigation.userInfoKey: LocalNotificationDestination.local.rawValue]
            ),
            .mine
        )
        XCTAssertEqual(
            NotificationNavigation.tab(
                from: [NotificationNavigation.userInfoKey: LocalNotificationDestination.notebook.rawValue]
            ),
            .log
        )
        XCTAssertNil(NotificationNavigation.tab(from: [:]))
        XCTAssertNil(
            NotificationNavigation.tab(
                from: [NotificationNavigation.userInfoKey: "untrusted-destination"]
            )
        )
    }

    private func makeCoordinator(
        center: PredictionNotificationCenter,
        store: PredictionReminderStore
    ) -> PredictionReminderCoordinator {
        let scheduler = PredictionReminderScheduler(
            center: center,
            store: store,
            now: { self.now }
        )
        return PredictionReminderCoordinator(scheduler: scheduler, now: { self.now })
    }

    private func makePlan(kind: PredictionReminderKind) -> PredictionReminderPlan {
        let locksAt = now.addingTimeInterval(60 * 60)
        let evidenceTo = locksAt.addingTimeInterval(2 * 60 * 60)
        let fireDate = switch kind {
        case .lock: locksAt.addingTimeInterval(-PredictionReminderValidation.lockLeadTime)
        case .result: evidenceTo.addingTimeInterval(PredictionReminderValidation.resultDelay)
        }
        return PredictionReminderPlan(
            predictionID: "net-position-\(LondonDay.localDateKey(at: locksAt))",
            date: LondonDay.localDateKey(at: locksAt),
            kind: kind,
            fireDate: fireDate,
            locksAt: locksAt,
            evidenceTo: evidenceTo
        )
    }
}

private final class PredictionNotificationCenter: LocalNotificationCenter {
    var status: LocalNotificationAuthorizationState
    var statusReads = 0
    var authorizationRequests = 0
    var deliveries: [String: LocalNotificationDelivery] = [:]
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
        deliveries[delivery.identifier] = delivery
    }

    func removePending(identifier: String) async {
        removed.append(identifier)
        deliveries[identifier] = nil
    }
}

private final class PredictionReminderStore: PredictionReminderMetadataStore {
    var values: [String: PredictionReminderMetadata] = [:]

    func metadata(identifier: String) -> PredictionReminderMetadata? {
        values[identifier]
    }

    func save(_ metadata: PredictionReminderMetadata) throws {
        values[metadata.identifier] = metadata
    }

    func remove(identifier: String) throws {
        values[identifier] = nil
    }
}
