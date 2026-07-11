import Foundation

enum FactClass: String, Codable, Sendable {
    case observed
    case derived
    case estimated
    case forecast
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
        }
    }

    var shortName: String {
        switch self {
        case .nuclear: "Nuclear"
        case .biomass: "Bio"
        case .imports: "Import"
        case .storage: "Store"
        default: displayName
        }
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
    let cleanestWindowStart: Date
    let cleanestWindowEnd: Date
    let chargingWindowStart: Date
    let chargingWindowEnd: Date
    let forecastIssuedAt: Date
    let source: SourceReference
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
