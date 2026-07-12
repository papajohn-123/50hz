import Foundation

enum FactClass: String, Codable, Sendable {
    case observed
    case derived
    case estimated
    case forecast
}

enum CarbonIntensityWording {
    static func label(for gramsPerKilowattHour: Double) -> String {
        if gramsPerKilowattHour < 100 { return "Lower carbon" }
        if gramsPerKilowattHour < 200 { return "Typical carbon" }
        return "Higher carbon"
    }
}

enum FreshnessState: String, Codable, Sendable, CaseIterable {
    case live
    case stale
    case offline
    case critical
}

enum FuelKind: String, Codable, Sendable, CaseIterable, Identifiable {
    case wind
    case solar
    case nuclear
    case gas
    case biomass
    case hydro
    case imports
    case storage
    case other

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .wind: "Wind"
        case .solar: "Solar"
        case .nuclear: "Nuclear"
        case .gas: "Gas"
        case .biomass: "Biomass"
        case .hydro: "Hydro"
        case .imports: "Imports"
        case .storage: "Storage"
        case .other: "Other"
        }
    }

    var shortName: String {
        switch self {
        case .nuclear: "Nuclear"
        case .biomass: "Bio"
        case .imports: "Imports"
        case .storage: "Store"
        case .other: "Other"
        default: displayName
        }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        let rawValue = try container.decode(String.self)
        self = FuelKind(rawValue: rawValue) ?? .other
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

struct SourceReference: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let dataset: String
    let observedAt: Date
    let retrievedAt: Date
    let cadenceSeconds: Int
}

struct GridMetric: Codable, Hashable, Sendable {
    let value: Double
    let unit: String
    let factClass: FactClass
    let sourceID: String

    func formatted(decimals: Int = 0) -> String {
        value.formatted(.number.precision(.fractionLength(decimals)))
    }
}

struct FuelReading: Codable, Hashable, Sendable, Identifiable {
    var id: FuelKind { fuel }
    let fuel: FuelKind
    let megawatts: Double
    let share: Double
    let changeOneHour: Double
    let rank: Int
    let factClass: FactClass
}

struct InterconnectorFlow: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let countryCode: String
    /// Positive values mean import into Great Britain; negative values mean export.
    let megawatts: Double
    let factClass: FactClass

    var directionLabel: String { megawatts >= 0 ? "Importing" : "Exporting" }
}

struct ConditionHeadline: Codable, Hashable, Sendable {
    let cleanliness: String
    let balance: String
    let energyPosition: String
    let interpretation: String

    /// Older snapshots could describe a displayed mix containing imports as
    /// generation. Qualify only those legacy phrases and preserve generation
    /// language when the snapshot contains generation alone.
    func publicInterpretation(for readings: [FuelReading]) -> String {
        guard readings.contains(where: { $0.fuel == .imports }) else { return interpretation }

        return interpretation
            .replacingOccurrences(
                of: "largest source",
                with: "largest displayed supply component",
                options: .caseInsensitive
            )
            .replacingOccurrences(
                of: "% of generation",
                with: "% of this partial supply mix",
                options: .caseInsensitive
            )
            .replacingOccurrences(
                of: "generation mix",
                with: "displayed supply mix",
                options: .caseInsensitive
            )
    }
}

struct GridEvent: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let title: String
    let summary: String
    let severity: String
    let evidenceClass: String
    let startedAt: Date
    let sourceIDs: [String]
    let isAuthoritativelyReported: Bool
}

struct GridSnapshot: Codable, Hashable, Sendable {
    var timestamp: Date
    let retrievedAt: Date
    var freshness: FreshnessState
    var freshnessAgeSeconds: Int
    var headline: ConditionHeadline
    var frequency: GridMetric?
    var demand: GridMetric
    var carbonIntensity: GridMetric
    var generation: [FuelReading]
    var interconnectors: [InterconnectorFlow]
    var activeEvent: GridEvent?
    let sources: [SourceReference]

    var totalGenerationMW: Double {
        generation.reduce(0) { $0 + $1.megawatts }
    }

    var lowCarbonShare: Double {
        generation
            .filter { [.wind, .solar, .nuclear, .biomass, .hydro].contains($0.fuel) }
            .reduce(0) { $0 + $1.share }
    }

    func reading(for fuel: FuelKind) -> FuelReading? {
        generation.first { $0.fuel == fuel }
    }
}

struct GridTimeline: Codable, Hashable, Sendable {
    let sourceResolutionSeconds: Int
    let materialGapSeconds: Int
    let nowBoundary: Date
    let samples: [GridTimelineSample]
}

struct GridTimelineSample: Codable, Hashable, Sendable, Identifiable {
    var id: Date { timestamp }
    let timestamp: Date
    let factClass: FactClass
    let demandMW: Double
    let carbonIntensity: Double
    let frequencyHz: Double?
    let generation: [FuelReading]

    var isForecast: Bool { factClass == .forecast }
}

struct RegionalGridContext: Codable, Hashable, Sendable {
    let name: String
    let postcode: String
    let carbonIntensity: Double
    let nationalCarbonIntensity: Double
    let rating: String
    let regionalPeriodEnd: Date?
    let regionalIsDelayed: Bool?
    let cleanestWindowStart: Date
    let cleanestWindowEnd: Date
    let chargingWindowStart: Date
    let chargingWindowEnd: Date
    let forecastIssuedAt: Date
    let source: SourceReference
}

struct GridSourceCitation: Codable, Hashable, Sendable, Identifiable {
    var id: String { sourceID }
    let sourceID: String
    let publisher: String
    let title: String
    let canonicalURL: URL
    let publishedAt: Date?
}

struct AskGridRequest: Codable, Hashable, Sendable {
    let question: String
    let mapTime: Date?
    let regionCode: String?
}

struct AskGridAnswer: Codable, Hashable, Sendable {
    let answer: String
    let asOf: Date
    let freshness: String
    let evidenceRefs: [String]
    let citations: [GridSourceCitation]
    let limitations: [String]
    let suggestedQuestions: [String]

    init(
        answer: String,
        asOf: Date,
        freshness: String,
        evidenceRefs: [String],
        citations: [GridSourceCitation] = [],
        limitations: [String] = [],
        suggestedQuestions: [String] = []
    ) {
        self.answer = answer
        self.asOf = asOf
        self.freshness = freshness
        self.evidenceRefs = evidenceRefs
        self.citations = citations
        self.limitations = limitations
        self.suggestedQuestions = suggestedQuestions
    }

    private enum CodingKeys: String, CodingKey {
        case answer, asOf, freshness, evidenceRefs, citations, limitations, suggestedQuestions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        answer = try container.decode(String.self, forKey: .answer)
        asOf = try container.decode(Date.self, forKey: .asOf)
        freshness = try container.decode(String.self, forKey: .freshness)
        evidenceRefs = try container.decodeIfPresent([String].self, forKey: .evidenceRefs) ?? []
        citations = try container.decodeIfPresent([GridSourceCitation].self, forKey: .citations) ?? []
        limitations = try container.decodeIfPresent([String].self, forKey: .limitations) ?? []
        suggestedQuestions = try container.decodeIfPresent([String].self, forKey: .suggestedQuestions) ?? []
    }
}

struct EventExplanation: Codable, Hashable, Sendable {
    let headline: String
    let plainLanguage: String
    let whyItMatters: String?
    let caveat: String?
    let evidenceRefs: [String]
    let suggestedQuestions: [String]

    private enum CodingKeys: String, CodingKey {
        case headline, plainLanguage, whyItMatters, caveat, evidenceRefs, suggestedQuestions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        headline = try container.decode(String.self, forKey: .headline)
        plainLanguage = try container.decode(String.self, forKey: .plainLanguage)
        whyItMatters = try container.decodeIfPresent(String.self, forKey: .whyItMatters)
        caveat = try container.decodeIfPresent(String.self, forKey: .caveat)
        evidenceRefs = try container.decodeIfPresent([String].self, forKey: .evidenceRefs) ?? []
        suggestedQuestions = try container.decodeIfPresent([String].self, forKey: .suggestedQuestions) ?? []
    }
}

struct EventExplanationResponse: Codable, Hashable, Sendable {
    let eventID: String
    let revision: Int
    let explanation: EventExplanation
    let citations: [GridSourceCitation]
    let model: String?
    let usedFallback: Bool

    private enum CodingKeys: String, CodingKey {
        case eventID, revision, explanation, citations, model, usedFallback
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        eventID = try container.decode(String.self, forKey: .eventID)
        revision = try container.decodeIfPresent(Int.self, forKey: .revision) ?? 1
        explanation = try container.decode(EventExplanation.self, forKey: .explanation)
        citations = try container.decodeIfPresent([GridSourceCitation].self, forKey: .citations) ?? []
        model = try container.decodeIfPresent(String.self, forKey: .model)
        usedFallback = try container.decodeIfPresent(Bool.self, forKey: .usedFallback) ?? false
    }
}

enum LoadPhase: Equatable, Sendable {
    case loading
    case loaded
    case failed(String)
}

enum AppTab: Hashable, Sendable {
    case live
    case today
    case mine
    case log
}
