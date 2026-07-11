import Foundation

enum GameMissionKind: String, Codable, Hashable, Sendable {
    case findCleanWindow = "find_clean_window"
    case identifyLargestSource = "identify_largest_source"
    case inspectInterconnector = "inspect_interconnector"
    case openEventEvidence = "open_event_evidence"
    case other

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = GameMissionKind(rawValue: try container.decode(String.self)) ?? .other
    }
}

enum GamePredictionChoice: String, Codable, Hashable, Sendable, Identifiable {
    case importing
    case exporting
    case other

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .importing: "Importing"
        case .exporting: "Exporting"
        case .other: "Other"
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = GamePredictionChoice(rawValue: try container.decode(String.self)) ?? .other
    }
}

enum GameCompletionValue: Codable, Hashable, Sendable {
    case string(String)
    case integer(Int)
    case number(Double)

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Int.self) {
            self = .integer(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else {
            self = .string(try container.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .integer(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        }
    }
}

struct GameMission: Codable, Hashable, Sendable, Identifiable {
    var id: String { missionID }
    let missionID: String
    let kind: GameMissionKind
    let title: String
    let available: Bool
    let unavailableReason: String?
    let completionPayload: [String: GameCompletionValue]

    private enum CodingKeys: String, CodingKey {
        case missionID = "mission_id"
        case kind, title, available
        case unavailableReason = "unavailable_reason"
        case completionPayload = "completion_payload"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        missionID = try container.decode(String.self, forKey: .missionID)
        kind = try container.decode(GameMissionKind.self, forKey: .kind)
        title = try container.decode(String.self, forKey: .title)
        available = try container.decode(Bool.self, forKey: .available)
        unavailableReason = try container.decodeIfPresent(String.self, forKey: .unavailableReason)
        completionPayload = try container.decodeIfPresent(
            [String: GameCompletionValue].self,
            forKey: .completionPayload
        ) ?? [:]
    }
}

struct GamePrediction: Codable, Hashable, Sendable {
    let predictionID: String
    let question: String
    let choices: [GamePredictionChoice]
    let locksAt: Date
    let metric: String
    let resolvesFrom: Date
    let resolvesTo: Date
    let ruleVersion: Int

    private enum CodingKeys: String, CodingKey {
        case predictionID = "prediction_id"
        case question, choices
        case locksAt = "locks_at"
        case metric
        case resolvesFrom = "resolves_from"
        case resolvesTo = "resolves_to"
        case ruleVersion = "rule_version"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        predictionID = try container.decode(String.self, forKey: .predictionID)
        question = try container.decode(String.self, forKey: .question)
        choices = try container.decode([GamePredictionChoice].self, forKey: .choices)
        locksAt = try container.decode(Date.self, forKey: .locksAt)
        metric = try container.decode(String.self, forKey: .metric)
        resolvesFrom = try container.decode(Date.self, forKey: .resolvesFrom)
        resolvesTo = try container.decode(Date.self, forKey: .resolvesTo)
        ruleVersion = try container.decodeIfPresent(Int.self, forKey: .ruleVersion) ?? 1
    }
}

struct DailyGame: Codable, Hashable, Sendable {
    let date: String
    let missions: [GameMission]
    let prediction: GamePrediction?
    let sourceFresh: Bool

    private enum CodingKeys: String, CodingKey {
        case date, missions, prediction
        case sourceFresh = "source_fresh"
    }
}
