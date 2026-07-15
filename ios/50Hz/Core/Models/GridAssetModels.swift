import Foundation

enum GridAssetLifecycle: String, Codable, Hashable, Sendable {
    case operational
    case underConstruction = "under_construction"
    case planned
    case decommissioned
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = GridAssetLifecycle(rawValue: try container.decode(String.self)) ?? .unknown
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

enum GridAssetEvidenceKind: String, Codable, Hashable, Sendable {
    case reference
    case reportedPlan = "reported_plan"
    case settledMetered = "settled_metered"
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = GridAssetEvidenceKind(rawValue: try container.decode(String.self)) ?? .unknown
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

struct GridAssetSource: Codable, Hashable, Sendable, Identifiable {
    var id: String { sourceID + ":" + sourceRecordID }
    let sourceID: String
    let publisher: String
    let dataset: String
    let sourceRecordID: String
    let retrievedAt: Date
    let canonicalURL: URL
    let licence: String
    let attribution: String
}

struct GridAssetCoordinate: Codable, Hashable, Sendable {
    let latitude: Double
    let longitude: Double
    let precision: String
    let source: GridAssetSource

    var isWithinGreatBritain: Bool {
        (49.5...61.0).contains(latitude) && (-9.5...3.0).contains(longitude)
    }
}

struct GridAssetPlanEvidence: Codable, Hashable, Sendable {
    let levelMW: Double
    let at: Date
    let direction: String
    let evidenceKind: GridAssetEvidenceKind
    let sourceID: String
    let retrievedAt: Date
    let settlementDate: String
    let settlementPeriod: Int
    let caveat: String
}

struct GridAssetSettledEvidence: Codable, Hashable, Sendable {
    let energyMWh: Double
    let averageMW: Double
    let intervalStart: Date
    let intervalEnd: Date
    let direction: String
    let evidenceKind: GridAssetEvidenceKind
    let sourceID: String
    let retrievedAt: Date
    let settlementDate: String
    let settlementPeriod: Int
    let caveat: String
}

struct GridAssetOperatingEvidence: Codable, Hashable, Sendable {
    let participantSubmittedPlan: GridAssetPlanEvidence?
    let latestSettledMetered: GridAssetSettledEvidence?
    let hasLiveMeteredOutput: Bool
}

struct GridAssetMapItem: Codable, Hashable, Sendable, Identifiable {
    let id: String
    let name: String
    let operatorName: String?
    let technology: String
    let fuelType: String
    let lifecycle: GridAssetLifecycle
    let capacityMW: Double?
    let region: String?
    let country: String?
    let coordinate: GridAssetCoordinate
    let linkedBMUnitCount: Int
    let operatingEvidence: GridAssetOperatingEvidence?

    var fuel: FuelKind? {
        let haystack = "\(fuelType) \(technology)".lowercased()
        if haystack.contains("wind") { return .wind }
        if haystack.contains("solar") || haystack.contains("photovoltaic") { return .solar }
        if haystack.contains("nuclear") { return .nuclear }
        if haystack.contains("gas") || haystack.contains("ccgt") || haystack.contains("ocgt") { return .gas }
        if haystack.contains("biomass") || haystack.contains("energy from waste") || haystack.contains("efw") { return .biomass }
        if haystack.contains("hydro") { return .hydro }
        if haystack.contains("storage") || haystack.contains("battery") || haystack.contains("pumped") { return .storage }
        return .other
    }
}

struct GridAssetFeedStatus: Codable, Hashable, Sendable {
    let state: String
    let lastSuccessfulAt: Date?
    let assetReferenceCount: Int
    let locatedAssetCount: Int
}

struct GridAssetMapResponse: Codable, Hashable, Sendable {
    let schemaVersion: String
    let evaluatedAt: Date
    let sourceStatus: GridAssetFeedStatus
    let totalCount: Int
    let returnedCount: Int
    let isTruncated: Bool
    let assets: [GridAssetMapItem]
    let boundary: String
    let disclaimer: String

    var validLocatedAssets: [GridAssetMapItem] {
        assets.filter { $0.coordinate.isWithinGreatBritain }
    }
}

struct GridBMUnitSummary: Codable, Hashable, Sendable, Identifiable {
    var id: String { nationalGridBMUnit }
    let nationalGridBMUnit: String
    let elexonBMUnit: String?
    let name: String?
    let fuelType: String?
    let leadPartyName: String?
    let generationCapacityMW: Double?
    let demandCapacityMW: Double?
    let gspGroupName: String?
    let eic: String?
    let matchMethod: String
    let matchConfidence: Double
}

struct GridAssetDetailResponse: Codable, Hashable, Sendable {
    let schemaVersion: String
    let evaluatedAt: Date
    let asset: GridAssetMapItem
    let bmUnits: [GridBMUnitSummary]
    let plan: [GridAssetPlanEvidence]
    let settledMetered: [GridAssetSettledEvidence]
    let limitations: [String]
}
