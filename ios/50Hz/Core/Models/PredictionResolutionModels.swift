import Foundation

enum PredictionResolutionState: String, UnknownStringCodable, Hashable, Sendable {
    case pending
    case resolved
    case void
    case unknown

    static let unknownValue = Self.unknown
}

enum PredictionResolutionOutcome: String, UnknownStringCodable, Hashable, Sendable {
    case importing
    case exporting
    case unknown

    static let unknownValue = Self.unknown

    var predictionChoice: GamePredictionChoice? {
        switch self {
        case .importing: .importing
        case .exporting: .exporting
        case .unknown: nil
        }
    }
}

struct PredictionResolutionRequest: Hashable, Sendable {
    let localDate: String

    init(localDate: String) {
        self.localDate = LondonDay.isValidLocalDateKey(localDate) ? localDate : "unknown-date"
    }

    init(at date: Date = Date()) {
        localDate = LondonDay.localDateKey(at: date)
    }
}

struct PredictionEvidenceCoverage: Codable, Hashable, Sendable {
    let expectedConnectorCount: Int
    let observedConnectorCount: Int
    let coverageFraction: Double
    let complete: Bool

    private enum CodingKeys: String, CodingKey {
        case expectedConnectorCount
        case observedConnectorCount
        case coverageFraction
        case complete
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        expectedConnectorCount = try container.decodeIfPresent(Int.self, forKey: .expectedConnectorCount) ?? 0
        observedConnectorCount = try container.decodeIfPresent(Int.self, forKey: .observedConnectorCount) ?? 0
        coverageFraction = try container.decodeIfPresent(Double.self, forKey: .coverageFraction) ?? 0
        complete = try container.decodeIfPresent(Bool.self, forKey: .complete) ?? false
    }

    var isInternallyConsistent: Bool {
        guard expectedConnectorCount >= 0,
              observedConnectorCount >= 0,
              observedConnectorCount <= expectedConnectorCount,
              coverageFraction.isFinite,
              (0...1).contains(coverageFraction) else { return false }
        let expectedFraction = expectedConnectorCount == 0
            ? 0
            : Double(observedConnectorCount) / Double(expectedConnectorCount)
        guard abs(coverageFraction - expectedFraction) < 0.000_001 else { return false }
        return complete == (expectedConnectorCount > 0 && observedConnectorCount == expectedConnectorCount)
    }
}

struct PredictionResolution: Codable, Hashable, Sendable {
    let schemaVersion: String
    let predictionID: String
    let date: String
    let question: String
    let choices: [GamePredictionChoice]
    let metric: String
    let ruleVersion: Int
    let connectorRegistryVersion: String
    let rule: String
    let locksAt: Date
    let evidenceFrom: Date
    let evidenceTo: Date
    let targetAt: Date
    let state: PredictionResolutionState
    let outcome: PredictionResolutionOutcome?
    let observedValueMW: Double?
    let observedAt: Date?
    let nearBalancedThresholdMW: Double
    let coverage: PredictionEvidenceCoverage
    let sourceIDs: [String]
    let sourceRecordIDs: [String]
    let sourceRevisionKeys: [String]
    let revisionWatermarkAt: Date?
    let evidenceChecksum: String
    let resolutionRevision: Int
    let isCorrection: Bool
    let computedAt: Date
    let reason: String

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case predictionID
        case date
        case question
        case choices
        case metric
        case ruleVersion
        case connectorRegistryVersion
        case rule
        case locksAt
        case evidenceFrom
        case evidenceTo
        case targetAt
        case state
        case outcome
        case observedValueMW
        case observedAt
        case nearBalancedThresholdMW
        case coverage
        case sourceIDs
        case sourceRecordIDs
        case sourceRevisionKeys
        case revisionWatermarkAt
        case evidenceChecksum
        case resolutionRevision
        case isCorrection
        case computedAt
        case reason
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "1.1"
        predictionID = try container.decode(String.self, forKey: .predictionID)
        date = try container.decode(String.self, forKey: .date)
        question = try container.decode(String.self, forKey: .question)
        choices = try container.decode([GamePredictionChoice].self, forKey: .choices)
        metric = try container.decode(String.self, forKey: .metric)
        ruleVersion = try container.decode(Int.self, forKey: .ruleVersion)
        connectorRegistryVersion = try container.decodeIfPresent(String.self, forKey: .connectorRegistryVersion)
            ?? "legacy-observed-set"
        rule = try container.decode(String.self, forKey: .rule)
        locksAt = try container.decode(Date.self, forKey: .locksAt)
        evidenceFrom = try container.decode(Date.self, forKey: .evidenceFrom)
        evidenceTo = try container.decode(Date.self, forKey: .evidenceTo)
        targetAt = try container.decode(Date.self, forKey: .targetAt)
        state = try container.decode(PredictionResolutionState.self, forKey: .state)
        outcome = try container.decodeIfPresent(PredictionResolutionOutcome.self, forKey: .outcome)
        observedValueMW = try container.decodeIfPresent(Double.self, forKey: .observedValueMW)
        observedAt = try container.decodeIfPresent(Date.self, forKey: .observedAt)
        nearBalancedThresholdMW = try container.decode(Double.self, forKey: .nearBalancedThresholdMW)
        coverage = try container.decode(PredictionEvidenceCoverage.self, forKey: .coverage)
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
        sourceRecordIDs = try container.decodeIfPresent([String].self, forKey: .sourceRecordIDs) ?? []
        sourceRevisionKeys = try container.decodeIfPresent([String].self, forKey: .sourceRevisionKeys) ?? []
        revisionWatermarkAt = try container.decodeIfPresent(Date.self, forKey: .revisionWatermarkAt)
        evidenceChecksum = try container.decode(String.self, forKey: .evidenceChecksum)
        resolutionRevision = try container.decodeIfPresent(Int.self, forKey: .resolutionRevision) ?? 0
        isCorrection = try container.decodeIfPresent(Bool.self, forKey: .isCorrection) ?? false
        computedAt = try container.decode(Date.self, forKey: .computedAt)
        reason = try container.decode(String.self, forKey: .reason)
    }

    func matches(_ request: PredictionResolutionRequest) -> Bool {
        guard request.localDate != "unknown-date",
              date == request.localDate,
              LondonDay.isValidLocalDateKey(date),
              !predictionID.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !metric.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !rule.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !choices.isEmpty,
              ruleVersion >= 1,
              resolutionRevision >= 0,
              nearBalancedThresholdMW.isFinite,
              nearBalancedThresholdMW >= 0,
              locksAt <= evidenceFrom,
              evidenceFrom <= targetAt,
              targetAt <= evidenceTo,
              LondonDay.localDateKey(at: locksAt) == date,
              LondonDay.localDateKey(at: targetAt) == date,
              coverage.isInternallyConsistent,
              Self.isSHA256Hex(evidenceChecksum),
              isCorrection == (resolutionRevision > 1) else { return false }

        // Unknown future contracts remain cacheable and inspectable, but their
        // familiar-looking values must never silently become a local game rule.
        if !supportsLocalChoiceContract {
            return state == .unknown || structurallyMatchesState
        }

        switch state {
        case .pending:
            return outcome == nil
                && observedValueMW == nil
                && observedAt == nil
                && coverage.observedConnectorCount == 0
                && !coverage.complete
                && sourceIDs.isEmpty
                && sourceRecordIDs.isEmpty
                && sourceRevisionKeys.isEmpty
                && revisionWatermarkAt == nil
                && resolutionRevision == 0
                && !isCorrection
        case .resolved:
            guard outcome != nil,
                  let value = observedValueMW,
                  value.isFinite,
                  observedAt.map({ evidenceFrom <= $0 && $0 <= evidenceTo }) == true,
                  coverage.complete,
                  sourceIDs.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }),
                  resolutionRevision >= 1 else { return false }
            switch outcome {
            case .importing:
                return value > nearBalancedThresholdMW
            case .exporting:
                return value < -nearBalancedThresholdMW
            case .unknown:
                // Preserve the payload for inspection, but the scoring gate
                // below remains closed for an unknown outcome.
                return abs(value) > nearBalancedThresholdMW
            case nil:
                return false
            }
        case .void:
            guard outcome == nil,
                  observedValueMW?.isFinite ?? true,
                  resolutionRevision >= 1 else { return false }
            if let value = observedValueMW {
                return observedAt.map { evidenceFrom <= $0 && $0 <= evidenceTo } == true
                    && coverage.complete
                    && abs(value) <= nearBalancedThresholdMW
            }
            return observedAt == nil
                && !coverage.complete
                && sourceIDs.isEmpty
                && sourceRecordIDs.isEmpty
                && sourceRevisionKeys.isEmpty
                && revisionWatermarkAt == nil
        case .unknown:
            // Preserve additive compatibility without ever treating an unknown
            // server state as a scored result.
            return true
        }
    }

    var supportsLocalChoiceContract: Bool {
        (schemaVersion == "1.0" || schemaVersion == "1.1")
            && metric == "net_interconnector_flow_mw"
            && ruleVersion == 1
            && choices.count == 2
            && Set(choices) == Set([.importing, .exporting])
    }

    var supportsLocalScoringContract: Bool {
        guard supportsLocalChoiceContract else { return false }
        switch state {
        case .resolved:
            guard let outcome, outcome.predictionChoice != nil,
                  let value = observedValueMW,
                  observedAt.map({ evidenceFrom <= $0 && $0 <= evidenceTo }) == true,
                  coverage.complete,
                  !sourceIDs.isEmpty,
                  !sourceRevisionKeys.isEmpty,
                  resolutionRevision >= 1 else { return false }
            return switch outcome {
            case .importing: value > nearBalancedThresholdMW
            case .exporting: value < -nearBalancedThresholdMW
            case .unknown: false
            }
        case .void:
            if let value = observedValueMW {
                return coverage.complete
                    && abs(value) <= nearBalancedThresholdMW
                    && !sourceIDs.isEmpty
                    && !sourceRevisionKeys.isEmpty
                    && observedAt.map { evidenceFrom <= $0 && $0 <= evidenceTo } == true
                    && resolutionRevision >= 1
            }
            return !coverage.complete
                && observedAt == nil
                && sourceIDs.isEmpty
                && sourceRecordIDs.isEmpty
                && sourceRevisionKeys.isEmpty
                && revisionWatermarkAt == nil
        case .pending, .unknown:
            return false
        }
    }

    private var structurallyMatchesState: Bool {
        switch state {
        case .pending:
            outcome == nil && observedValueMW == nil && observedAt == nil && resolutionRevision == 0
        case .resolved:
            outcome != nil
                && observedValueMW?.isFinite == true
                && observedAt.map { evidenceFrom <= $0 && $0 <= evidenceTo } == true
                && coverage.complete
                && resolutionRevision >= 1
        case .void:
            outcome == nil
                && (observedValueMW?.isFinite ?? true)
                && resolutionRevision >= 1
        case .unknown:
            true
        }
    }

    private static func isSHA256Hex(_ value: String) -> Bool {
        value.count == 64 && value.unicodeScalars.allSatisfy { scalar in
            (48...57).contains(scalar.value)
                || (65...70).contains(scalar.value)
                || (97...102).contains(scalar.value)
        }
    }
}
