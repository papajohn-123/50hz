import Foundation

struct SavedPrediction: Codable, Hashable, Identifiable, Sendable {
    var id: String { "\(date)|\(predictionID)" }
    let predictionID: String
    let date: String
    let choice: GamePredictionChoice
    let selectedAt: Date

    var isUsable: Bool {
        LondonDay.isValidLocalDateKey(date)
            && !predictionID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && choice != .other
    }
}

struct PredictionJournalStore {
    private static let storageKey = "notebook.predictions.v1"
    private static let migrationKey = "notebook.predictions.legacy-migrated"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func predictions() -> [SavedPrediction] {
        guard let data = defaults.data(forKey: Self.storageKey),
              let decoded = try? JSONDecoder().decode([SavedPrediction].self, from: data) else { return [] }

        var newestByID: [String: SavedPrediction] = [:]
        for prediction in decoded where prediction.isUsable {
            if prediction.selectedAt > (newestByID[prediction.id]?.selectedAt ?? .distantPast) {
                newestByID[prediction.id] = prediction
            }
        }
        return newestByID.values.sorted { left, right in
            if left.date != right.date { return left.date > right.date }
            return left.selectedAt > right.selectedAt
        }
    }

    func prediction(predictionID: String, date: String) -> SavedPrediction? {
        predictions().first { $0.predictionID == predictionID && $0.date == date }
    }

    func save(
        predictionID: String,
        date: String,
        choice: GamePredictionChoice,
        selectedAt: Date = Date()
    ) {
        let prediction = SavedPrediction(
            predictionID: predictionID,
            date: date,
            choice: choice,
            selectedAt: selectedAt
        )
        guard prediction.isUsable else { return }
        var retained = predictions().filter { $0.id != prediction.id }
        retained.append(prediction)
        persist(Array(retained.sorted { $0.selectedAt < $1.selectedAt }.suffix(180)))
    }

    func migrateLegacyPredictionIfNeeded(now: Date = Date()) {
        guard !defaults.bool(forKey: Self.migrationKey) else { return }
        defer { defaults.set(true, forKey: Self.migrationKey) }
        let predictionID = defaults.string(forKey: "log.prediction.id") ?? ""
        let date = defaults.string(forKey: "log.prediction.date") ?? ""
        let rawChoice = (defaults.string(forKey: "log.prediction") ?? "").lowercased()
        guard let choice = GamePredictionChoice(rawValue: rawChoice), choice != .other else { return }
        save(predictionID: predictionID, date: date, choice: choice, selectedAt: now)
    }

    private func persist(_ predictions: [SavedPrediction]) {
        guard let data = try? JSONEncoder().encode(predictions) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}

struct MissionProgress: Codable, Hashable, Identifiable, Sendable {
    var id: String { "\(date)|\(missionID)" }
    let missionID: String
    let date: String
    let kind: GameMissionKind
    let visitedAt: Date
    let completedAt: Date?
    let learnedNote: String?

    var isCompleted: Bool { completedAt != nil }
}

struct MissionProgressStore {
    private static let storageKey = "notebook.missions.v1"
    private let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    func progress() -> [MissionProgress] {
        guard let data = defaults.data(forKey: Self.storageKey),
              let decoded = try? JSONDecoder().decode([MissionProgress].self, from: data) else { return [] }
        return decoded
            .filter {
                LondonDay.isValidLocalDateKey($0.date)
                    && !$0.missionID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            }
            .sorted { $0.visitedAt > $1.visitedAt }
    }

    func record(missionID: String, date: String) -> MissionProgress? {
        progress().first { $0.missionID == missionID && $0.date == date }
    }

    func markVisited(_ mission: GameMission, date: String, at instant: Date = Date()) {
        guard LondonDay.isValidLocalDateKey(date), mission.available else { return }
        guard record(missionID: mission.missionID, date: date) == nil else { return }
        upsert(
            MissionProgress(
                missionID: mission.missionID,
                date: date,
                kind: mission.kind,
                visitedAt: instant,
                completedAt: nil,
                learnedNote: nil
            )
        )
    }

    @discardableResult
    func markCompleted(_ mission: GameMission, date: String, at instant: Date = Date()) -> Bool {
        guard let existing = record(missionID: mission.missionID, date: date),
              !existing.isCompleted else { return false }
        upsert(
            MissionProgress(
                missionID: existing.missionID,
                date: existing.date,
                kind: existing.kind,
                visitedAt: existing.visitedAt,
                completedAt: instant,
                learnedNote: Self.learnedNote(for: existing.kind)
            )
        )
        return true
    }

    static func learnedNote(for kind: GameMissionKind) -> String {
        switch kind {
        case .findCleanWindow:
            "Forecast clean windows are national estimates and can move as new data arrives."
        case .identifyLargestSource:
            "The leading displayed source is one observed part of Britain’s wider electricity system."
        case .inspectInterconnector:
            "Positive signed net interconnector flow means importing under 50Hz’s published convention."
        case .openEventEvidence:
            "A reported event is a publisher-backed claim; it does not by itself prove the cause of every grid change."
        case .other:
            "A useful grid observation keeps its time, evidence class and limitations attached."
        }
    }

    private func upsert(_ record: MissionProgress) {
        var retained = progress().filter { $0.id != record.id }
        retained.append(record)
        retained = Array(retained.sorted { $0.visitedAt < $1.visitedAt }.suffix(365))
        guard let data = try? JSONEncoder().encode(retained) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}

enum MissionNavigationTarget: Hashable, Sendable {
    case live
    case today
    case local
    case event(String)

    var label: String {
        switch self {
        case .live: "Open Live"
        case .today: "Open Today"
        case .local: "Open Local"
        case .event: "Open evidence"
        }
    }

    static func resolve(_ mission: GameMission, events: [GridEvent]) -> MissionNavigationTarget? {
        guard mission.available else { return nil }
        switch mission.kind {
        case .findCleanWindow:
            return .local
        case .identifyLargestSource, .inspectInterconnector:
            return .live
        case .openEventEvidence:
            let explicitID = ["event_id", "eventID", "event"]
                .compactMap { mission.completionPayload[$0]?.stringValue }
                .first { !$0.isEmpty }
            if let eventID = explicitID ?? events.first?.id { return .event(eventID) }
            return .today
        case .other:
            return nil
        }
    }
}

enum LocalPredictionResultStatus: Hashable, Sendable {
    case correct
    case incorrect
    case void
    case pending
    case unsupported
}

struct LocalPredictionResult: Hashable, Sendable {
    let status: LocalPredictionResultStatus
    let savedChoice: GamePredictionChoice
    let publishedOutcome: PredictionResolutionOutcome?

    static func derive(
        saved prediction: SavedPrediction,
        resolution: PredictionResolution
    ) -> LocalPredictionResult? {
        guard prediction.date == resolution.date,
              prediction.predictionID == resolution.predictionID else { return nil }
        switch resolution.state {
        case .resolved:
            guard resolution.supportsLocalScoringContract else {
                return LocalPredictionResult(status: .unsupported, savedChoice: prediction.choice, publishedOutcome: resolution.outcome)
            }
            guard let outcome = resolution.outcome?.predictionChoice else {
                return LocalPredictionResult(status: .unsupported, savedChoice: prediction.choice, publishedOutcome: resolution.outcome)
            }
            return LocalPredictionResult(
                status: outcome == prediction.choice ? .correct : .incorrect,
                savedChoice: prediction.choice,
                publishedOutcome: resolution.outcome
            )
        case .void:
            guard resolution.supportsLocalScoringContract else {
                return LocalPredictionResult(status: .unsupported, savedChoice: prediction.choice, publishedOutcome: nil)
            }
            return LocalPredictionResult(status: .void, savedChoice: prediction.choice, publishedOutcome: nil)
        case .pending:
            return LocalPredictionResult(
                status: resolution.supportsLocalChoiceContract ? .pending : .unsupported,
                savedChoice: prediction.choice,
                publishedOutcome: nil
            )
        case .unknown:
            return LocalPredictionResult(status: .unsupported, savedChoice: prediction.choice, publishedOutcome: resolution.outcome)
        }
    }
}

enum PredictionInteractionPolicy {
    static func canSelect(
        now: Date,
        locksAt: Date,
        state: PredictionResolutionState?,
        supportsLocalChoiceContract: Bool
    ) -> Bool {
        supportsLocalChoiceContract
            && now < locksAt
            && (state == nil || state == .pending)
    }
}

private extension GameCompletionValue {
    var stringValue: String? {
        guard case .string(let value) = self else { return nil }
        return value.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
