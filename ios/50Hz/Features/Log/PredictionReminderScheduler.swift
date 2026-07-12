import Foundation

enum PredictionReminderKind: String, Codable, CaseIterable, Hashable, Sendable {
    case lock
    case result
}

struct PredictionReminderPlan: Equatable, Sendable {
    let predictionID: String
    let date: String
    let kind: PredictionReminderKind
    let fireDate: Date
    let locksAt: Date
    let evidenceTo: Date
}

struct PredictionReminderMetadata: Codable, Equatable, Sendable {
    let identifier: String
    let predictionID: String
    let date: String
    let kind: PredictionReminderKind
    let fireDate: Date
}

enum PredictionReminderScheduleState: Equatable, Sendable {
    case scheduled(identifier: String)
    case denied
    case notAvailable
    case invalid
    case past
    case error
}

enum PredictionReminderCancellationState: Equatable, Sendable {
    case cancelled
    case invalid
    case error
}

protocol PredictionReminderMetadataStore: AnyObject {
    func metadata(identifier: String) -> PredictionReminderMetadata?
    func save(_ metadata: PredictionReminderMetadata) throws
    func remove(identifier: String) throws
}

@MainActor
final class PredictionReminderScheduler {
    private let center: LocalNotificationCenter
    private let store: PredictionReminderMetadataStore
    private let now: () -> Date

    init(
        center: LocalNotificationCenter,
        store: PredictionReminderMetadataStore,
        now: @escaping () -> Date = Date.init
    ) {
        self.center = center
        self.store = store
        self.now = now
    }

    func schedule(_ plan: PredictionReminderPlan) async -> PredictionReminderScheduleState {
        let evaluationTime = now()
        guard let metadata = PredictionReminderValidation.validate(plan, now: evaluationTime) else {
            return PredictionReminderValidation.isPast(plan, now: evaluationTime) ? .past : .invalid
        }

        switch await center.authorizationState() {
        case .denied:
            return .denied
        case .notAvailable:
            return .notAvailable
        case .notDetermined:
            do {
                guard try await center.requestAuthorization() else { return .denied }
            } catch {
                return .error
            }
        case .authorized, .provisional:
            break
        }

        guard metadata.fireDate > now() else { return .past }
        let delivery = LocalNotificationDelivery(
            identifier: metadata.identifier,
            title: title(for: metadata.kind),
            body: body(for: metadata.kind),
            fireDate: metadata.fireDate,
            destination: .notebook
        )
        do {
            try await center.add(delivery)
            do {
                try store.save(metadata)
            } catch {
                await center.removePending(identifier: metadata.identifier)
                return .error
            }
            return .scheduled(identifier: metadata.identifier)
        } catch {
            return .error
        }
    }

    func cancel(
        kind: PredictionReminderKind,
        date: String
    ) async -> PredictionReminderCancellationState {
        guard let identifier = PredictionReminderValidation.stableIdentifier(
            kind: kind,
            date: date
        ) else { return .invalid }
        await center.removePending(identifier: identifier)
        do {
            try store.remove(identifier: identifier)
            return .cancelled
        } catch {
            return .error
        }
    }

    func storedMetadata(
        kind: PredictionReminderKind,
        date: String
    ) -> PredictionReminderMetadata? {
        guard let identifier = PredictionReminderValidation.stableIdentifier(
            kind: kind,
            date: date
        ) else { return nil }
        return store.metadata(identifier: identifier)
    }

    private func title(for kind: PredictionReminderKind) -> String {
        switch kind {
        case .lock: "Prediction locks in 15 minutes"
        case .result: "Prediction evidence window closed"
        }
    }

    private func body(for kind: PredictionReminderKind) -> String {
        switch kind {
        case .lock:
            "Open Notebook to make or check your local choice. Choices stay on this device."
        case .result:
            "Open Notebook to check for a published result. It may still be pending while evidence is processed."
        }
    }
}

enum PredictionReminderValidation {
    static let lockLeadTime: TimeInterval = 15 * 60
    static let resultDelay: TimeInterval = 5 * 60

    static func stableIdentifier(
        kind: PredictionReminderKind,
        date: String
    ) -> String? {
        guard LondonDay.isValidLocalDateKey(date) else { return nil }
        return "50hz.prediction.\(kind.rawValue).\(date)"
    }

    static func validate(
        _ plan: PredictionReminderPlan,
        now: Date
    ) -> PredictionReminderMetadata? {
        guard let identifier = stableIdentifier(kind: plan.kind, date: plan.date) else {
            return nil
        }
        let predictionID = plan.predictionID.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !predictionID.isEmpty,
              predictionID.count <= 128,
              predictionID.rangeOfCharacter(from: .controlCharacters) == nil,
              plan.locksAt.timeIntervalSince1970.isFinite,
              plan.evidenceTo.timeIntervalSince1970.isFinite,
              plan.fireDate.timeIntervalSince1970.isFinite,
              plan.fireDate.timeIntervalSince1970.rounded() == plan.fireDate.timeIntervalSince1970,
              LondonDay.localDateKey(at: plan.locksAt) == plan.date,
              plan.evidenceTo >= plan.locksAt,
              plan.fireDate > now else { return nil }

        switch plan.kind {
        case .lock:
            guard plan.fireDate == plan.locksAt.addingTimeInterval(-lockLeadTime),
                  plan.fireDate < plan.locksAt else { return nil }
        case .result:
            guard plan.fireDate == plan.evidenceTo.addingTimeInterval(resultDelay),
                  plan.fireDate > plan.evidenceTo else { return nil }
        }

        return PredictionReminderMetadata(
            identifier: identifier,
            predictionID: predictionID,
            date: plan.date,
            kind: plan.kind,
            fireDate: plan.fireDate
        )
    }

    static func isPast(_ plan: PredictionReminderPlan, now: Date) -> Bool {
        plan.fireDate <= now
    }
}

final class UserDefaultsPredictionReminderMetadataStore: PredictionReminderMetadataStore {
    private let defaults: UserDefaults
    private let storageKey: String

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "50hz.prediction-reminder-metadata.v1"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
    }

    func metadata(identifier: String) -> PredictionReminderMetadata? {
        load()[identifier]
    }

    func save(_ metadata: PredictionReminderMetadata) throws {
        var entries = load()
        entries[metadata.identifier] = metadata
        if entries.count > 32 {
            entries = Dictionary(
                uniqueKeysWithValues: entries.values
                    .sorted { $0.fireDate > $1.fireDate }
                    .prefix(32)
                    .map { ($0.identifier, $0) }
            )
        }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        defaults.set(try encoder.encode(entries), forKey: storageKey)
    }

    func remove(identifier: String) throws {
        var entries = load()
        entries.removeValue(forKey: identifier)
        if entries.isEmpty {
            defaults.removeObject(forKey: storageKey)
        } else {
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            defaults.set(try encoder.encode(entries), forKey: storageKey)
        }
    }

    private func load() -> [String: PredictionReminderMetadata] {
        guard let data = defaults.data(forKey: storageKey), data.count <= 64_000 else {
            return [:]
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode([String: PredictionReminderMetadata].self, from: data)) ?? [:]
    }
}
