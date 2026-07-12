import Foundation
import SwiftUI

enum LocalReminderFeedback: Equatable {
    case none
    case scheduled
    case cancelled
    case denied
    case notAvailable
    case invalid
    case past
    case error
}

@MainActor
final class LocalReminderCoordinator: ObservableObject {
    static let localIdentifier = "flexible-use"

    @Published private(set) var scheduledMetadata: LocalReminderMetadata?
    @Published private(set) var hasMaterialChange = false
    @Published private(set) var feedback: LocalReminderFeedback = .none
    @Published private(set) var isWorking = false

    private let scheduler: LocalReminderScheduler
    private let now: () -> Date

    init(
        scheduler: LocalReminderScheduler? = nil,
        now: @escaping () -> Date = Date.init
    ) {
        self.scheduler = scheduler ?? LocalReminderScheduler(
            center: SystemLocalNotificationCenter(),
            store: UserDefaultsLocalReminderMetadataStore(),
            now: now
        )
        self.now = now
        scheduledMetadata = self.scheduler.storedMetadata(
            localIdentifier: Self.localIdentifier
        )
    }

    /// Reconciles local display state without requesting notification permission
    /// or changing the scheduled delivery time.
    func refresh(for plan: LocalReminderPlan?) async {
        guard !isWorking else { return }
        guard let stored = scheduler.storedMetadata(localIdentifier: Self.localIdentifier) else {
            scheduledMetadata = nil
            hasMaterialChange = false
            return
        }

        if stored.start <= now() {
            _ = await scheduler.cancel(localIdentifier: Self.localIdentifier)
            scheduledMetadata = nil
            hasMaterialChange = false
            return
        }

        scheduledMetadata = stored
        guard let plan,
              let refreshed = LocalReminderValidation.validate(plan, now: now())?.metadata
        else {
            hasMaterialChange = false
            return
        }
        hasMaterialChange = localReminderPlanHasMaterialChange(
            scheduled: stored,
            refreshed: refreshed
        )
    }

    @discardableResult
    func schedule(_ plan: LocalReminderPlan) async -> Bool {
        guard !isWorking else { return false }
        isWorking = true
        defer { isWorking = false }

        switch await scheduler.schedule(plan) {
        case .scheduled:
            scheduledMetadata = scheduler.storedMetadata(localIdentifier: Self.localIdentifier)
            hasMaterialChange = false
            feedback = .scheduled
            return true
        case .denied:
            feedback = .denied
        case .notAvailable:
            feedback = .notAvailable
        case .invalid:
            feedback = .invalid
        case .past:
            feedback = .past
        case .error:
            feedback = .error
        }
        return false
    }

    @discardableResult
    func cancel() async -> Bool {
        guard !isWorking else { return false }
        isWorking = true
        defer { isWorking = false }

        switch await scheduler.cancel(localIdentifier: Self.localIdentifier) {
        case .cancelled:
            scheduledMetadata = nil
            hasMaterialChange = false
            feedback = .cancelled
            return true
        case .invalid, .error:
            feedback = .error
            return false
        }
    }
}
