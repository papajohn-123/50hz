import Foundation
import SwiftUI

enum PredictionReminderFeedback: Equatable {
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
final class PredictionReminderCoordinator: ObservableObject {
    @Published private(set) var scheduled: [PredictionReminderKind: PredictionReminderMetadata] = [:]
    @Published private(set) var feedback: [PredictionReminderKind: PredictionReminderFeedback] = [:]
    @Published private(set) var workingKinds: Set<PredictionReminderKind> = []

    private let scheduler: PredictionReminderScheduler
    private let now: () -> Date

    init(
        scheduler: PredictionReminderScheduler? = nil,
        now: @escaping () -> Date = Date.init
    ) {
        self.scheduler = scheduler ?? PredictionReminderScheduler(
            center: SystemLocalNotificationCenter(),
            store: UserDefaultsPredictionReminderMetadataStore(),
            now: now
        )
        self.now = now
    }

    /// Reconciles stored state without reading or requesting notification permission.
    func refresh(predictionID: String, date: String) async {
        for kind in PredictionReminderKind.allCases {
            guard let metadata = scheduler.storedMetadata(kind: kind, date: date) else {
                scheduled[kind] = nil
                continue
            }
            if metadata.predictionID != predictionID || metadata.fireDate <= now() {
                _ = await scheduler.cancel(kind: kind, date: date)
                scheduled[kind] = nil
            } else {
                scheduled[kind] = metadata
            }
        }
    }

    @discardableResult
    func schedule(_ plan: PredictionReminderPlan) async -> Bool {
        guard !workingKinds.contains(plan.kind) else { return false }
        workingKinds.insert(plan.kind)
        defer { workingKinds.remove(plan.kind) }

        switch await scheduler.schedule(plan) {
        case .scheduled:
            scheduled[plan.kind] = scheduler.storedMetadata(
                kind: plan.kind,
                date: plan.date
            )
            feedback[plan.kind] = .scheduled
            return true
        case .denied:
            feedback[plan.kind] = .denied
        case .notAvailable:
            feedback[plan.kind] = .notAvailable
        case .invalid:
            feedback[plan.kind] = .invalid
        case .past:
            feedback[plan.kind] = .past
        case .error:
            feedback[plan.kind] = .error
        }
        return false
    }

    @discardableResult
    func cancel(kind: PredictionReminderKind, date: String) async -> Bool {
        guard !workingKinds.contains(kind) else { return false }
        workingKinds.insert(kind)
        defer { workingKinds.remove(kind) }

        switch await scheduler.cancel(kind: kind, date: date) {
        case .cancelled:
            scheduled[kind] = nil
            feedback[kind] = .cancelled
            return true
        case .invalid, .error:
            feedback[kind] = .error
            return false
        }
    }
}
