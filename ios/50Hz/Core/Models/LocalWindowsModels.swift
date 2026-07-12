import Foundation

enum PostcodePrivacy {
    static let defaultOutwardCode = "SW1A"

    static func validatedOutwardCode(
        from postcode: String,
        defaultWhenEmpty: Bool = true
    ) -> String? {
        let compact = postcode
            .uppercased()
            .filter { !$0.isWhitespace }
        if compact.isEmpty { return defaultWhenEmpty ? defaultOutwardCode : nil }
        if compact == "GIR0AA" || compact == "GIR" { return "GIR" }
        if isOutwardCode(compact) { return compact }

        if compact.count >= 5 {
            let suffix = Array(compact.suffix(3))
            if suffix[0].isNumber,
               suffix[1].isLetter,
               suffix[2].isLetter {
                let outward = String(compact.dropLast(3))
                if isOutwardCode(outward) { return outward }
            }
        }
        return nil
    }

    /// Returns only the UK outward code. A valid inward suffix is discarded
    /// before the value can enter a request URL, cache filename, or server log.
    static func outwardCode(from postcode: String) -> String {
        validatedOutwardCode(from: postcode) ?? defaultOutwardCode
    }

    private static func isOutwardCode(_ value: String) -> Bool {
        value.range(
            of: #"^[A-Z]{1,2}[0-9][A-Z0-9]?$"#,
            options: .regularExpression
        ) != nil
    }
}

struct LocalWindowsRequest: Hashable, Sendable {
    let outwardPostcode: String
    let durationMinutes: Int

    init(postcode: String, durationMinutes: Int) {
        outwardPostcode = PostcodePrivacy.outwardCode(from: postcode)
        self.durationMinutes = durationMinutes
    }
}

enum LocalFlexibleUseStatus: String, Codable, Hashable, Sendable {
    case lowerCarbonWindow = "lower_carbon_window"
    case noMeaningfulDifference = "no_meaningful_difference"
    case windowFound = "window_found"
    case insufficientCoverage = "insufficient_coverage"
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = Self(rawValue: try container.decode(String.self)) ?? .unknown
    }
}

enum LocalComparisonStatus: String, Codable, Hashable, Sendable {
    case compatible
    case incompatibleSeries = "incompatible_series"
    case insufficientCoverage = "insufficient_coverage"
    case unknown

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = Self(rawValue: try container.decode(String.self)) ?? .unknown
    }
}

struct LocalChargingWindow: Codable, Hashable, Sendable {
    let start: Date
    let end: Date
    let averageIntensityGCO2KWh: Double
    let sourceRecordIDs: [String]
    let coverageFraction: Double

    private enum CodingKeys: String, CodingKey {
        case start, end, averageIntensityGCO2KWh, sourceRecordIDs, coverageFraction
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        start = try container.decode(Date.self, forKey: .start)
        end = try container.decode(Date.self, forKey: .end)
        averageIntensityGCO2KWh = try container.decode(Double.self, forKey: .averageIntensityGCO2KWh)
        sourceRecordIDs = try container.decodeIfPresent([String].self, forKey: .sourceRecordIDs) ?? []
        coverageFraction = try container.decodeIfPresent(Double.self, forKey: .coverageFraction) ?? 1
    }
}

struct LocalForecastCoverage: Codable, Hashable, Sendable {
    let intervalMinutes: Int
    let requiredIntervalCount: Int
    let expectedIntervalCount: Int
    let availableIntervalCount: Int
    let coverageFraction: Double
    let gapStarts: [Date]
    let candidateStartCount: Int
    let completeCandidateCount: Int

    private enum CodingKeys: String, CodingKey {
        case intervalMinutes, requiredIntervalCount, expectedIntervalCount
        case availableIntervalCount, coverageFraction, gapStarts
        case candidateStartCount, completeCandidateCount
    }

    init(
        intervalMinutes: Int = 30,
        requiredIntervalCount: Int = 0,
        expectedIntervalCount: Int = 0,
        availableIntervalCount: Int = 0,
        coverageFraction: Double = 0,
        gapStarts: [Date] = [],
        candidateStartCount: Int = 0,
        completeCandidateCount: Int = 0
    ) {
        self.intervalMinutes = intervalMinutes
        self.requiredIntervalCount = requiredIntervalCount
        self.expectedIntervalCount = expectedIntervalCount
        self.availableIntervalCount = availableIntervalCount
        self.coverageFraction = coverageFraction
        self.gapStarts = gapStarts
        self.candidateStartCount = candidateStartCount
        self.completeCandidateCount = completeCandidateCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        intervalMinutes = try container.decodeIfPresent(Int.self, forKey: .intervalMinutes) ?? 30
        requiredIntervalCount = try container.decodeIfPresent(Int.self, forKey: .requiredIntervalCount) ?? 0
        expectedIntervalCount = try container.decodeIfPresent(Int.self, forKey: .expectedIntervalCount) ?? 0
        availableIntervalCount = try container.decodeIfPresent(Int.self, forKey: .availableIntervalCount) ?? 0
        coverageFraction = try container.decodeIfPresent(Double.self, forKey: .coverageFraction) ?? 0
        gapStarts = try container.decodeIfPresent([Date].self, forKey: .gapStarts) ?? []
        candidateStartCount = try container.decodeIfPresent(Int.self, forKey: .candidateStartCount) ?? 0
        completeCandidateCount = try container.decodeIfPresent(Int.self, forKey: .completeCandidateCount) ?? 0
    }
}

struct LocalFlexibleUseComparison: Codable, Hashable, Sendable {
    let status: LocalComparisonStatus
    let startNowWindow: LocalChargingWindow?
    let incompatibilityFields: [String]
    let startNowMinusRecommendedGCO2KWh: Double?
    let percentLowerThanStartNow: Double?
    let isMeaningful: Bool?

    private enum CodingKeys: String, CodingKey {
        case status, startNowWindow, incompatibilityFields
        case startNowMinusRecommendedGCO2KWh, percentLowerThanStartNow, isMeaningful
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(LocalComparisonStatus.self, forKey: .status) ?? .unknown
        startNowWindow = try container.decodeIfPresent(LocalChargingWindow.self, forKey: .startNowWindow)
        incompatibilityFields = try container.decodeIfPresent([String].self, forKey: .incompatibilityFields) ?? []
        startNowMinusRecommendedGCO2KWh = try container.decodeIfPresent(Double.self, forKey: .startNowMinusRecommendedGCO2KWh)
        percentLowerThanStartNow = try container.decodeIfPresent(Double.self, forKey: .percentLowerThanStartNow)
        isMeaningful = try container.decodeIfPresent(Bool.self, forKey: .isMeaningful)
    }
}

struct LocalFlexibleUseMethodology: Codable, Hashable, Sendable {
    let version: String?
    let intervalMinutes: Int?
    let requiredWindowCoveragePercent: Int?
    let selectionRule: String?
    let tieBreakRule: String?
    let meaningfulAbsoluteDeltaGCO2KWh: Double?
    let meaningfulPercentDelta: Double?

    private enum CodingKeys: String, CodingKey {
        case version, intervalMinutes, requiredWindowCoveragePercent
        case selectionRule, tieBreakRule, meaningfulAbsoluteDeltaGCO2KWh
        case meaningfulPercentDelta
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decodeIfPresent(String.self, forKey: .version)
        intervalMinutes = try container.decodeIfPresent(Int.self, forKey: .intervalMinutes)
        requiredWindowCoveragePercent = try container.decodeIfPresent(Int.self, forKey: .requiredWindowCoveragePercent)
        selectionRule = try container.decodeIfPresent(String.self, forKey: .selectionRule)
        tieBreakRule = try container.decodeIfPresent(String.self, forKey: .tieBreakRule)
        meaningfulAbsoluteDeltaGCO2KWh = try container.decodeIfPresent(Double.self, forKey: .meaningfulAbsoluteDeltaGCO2KWh)
        meaningfulPercentDelta = try container.decodeIfPresent(Double.self, forKey: .meaningfulPercentDelta)
    }
}

struct LocalFlexibleUsePlan: Codable, Hashable, Sendable {
    let resultVersion: String
    let methodology: LocalFlexibleUseMethodology?
    let status: LocalFlexibleUseStatus
    let summary: String
    let continuous: Bool
    let requestedDurationMinutes: Int
    let earliestStart: Date?
    let latestFinish: Date?
    let recommendedWindow: LocalChargingWindow?
    let coverage: LocalForecastCoverage
    let comparison: LocalFlexibleUseComparison?

    private enum CodingKeys: String, CodingKey {
        case resultVersion, methodology, status, summary, continuous
        case requestedDurationMinutes, earliestStart, latestFinish
        case recommendedWindow, coverage, comparison
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        resultVersion = try container.decodeIfPresent(String.self, forKey: .resultVersion) ?? "unknown"
        methodology = try container.decodeIfPresent(LocalFlexibleUseMethodology.self, forKey: .methodology)
        status = try container.decodeIfPresent(LocalFlexibleUseStatus.self, forKey: .status) ?? .unknown
        summary = try container.decodeIfPresent(String.self, forKey: .summary) ?? ""
        continuous = try container.decodeIfPresent(Bool.self, forKey: .continuous) ?? true
        requestedDurationMinutes = try container.decode(Int.self, forKey: .requestedDurationMinutes)
        earliestStart = try container.decodeIfPresent(Date.self, forKey: .earliestStart)
        latestFinish = try container.decodeIfPresent(Date.self, forKey: .latestFinish)
        recommendedWindow = try container.decodeIfPresent(LocalChargingWindow.self, forKey: .recommendedWindow)
        coverage = try container.decodeIfPresent(LocalForecastCoverage.self, forKey: .coverage) ?? LocalForecastCoverage()
        comparison = try container.decodeIfPresent(LocalFlexibleUseComparison.self, forKey: .comparison)
    }
}

struct LocalForecastCapture: Codable, Hashable, Sendable {
    let geographyCode: String
    let geographyScope: String
    let factClass: String
    let seriesID: String?
    let sourceID: String?
    let methodologyVersion: String?
    let sourceIssuedAt: Date?
    let capturedAt: Date?
    let vintageAt: Date?
    let vintageBasis: String?
    let issueTimeBasis: String?
    let captureTimeBasis: String
    let captureAgeSeconds: Int?
    let captureStaleAfterSeconds: Int?
    let captureState: String
    let sourceRecordIDs: [String]

    private enum CodingKeys: String, CodingKey {
        case geographyCode, geographyScope, factClass, seriesID, sourceID
        case methodologyVersion, sourceIssuedAt, capturedAt, vintageAt
        case vintageBasis, issueTimeBasis, captureTimeBasis, captureAgeSeconds
        case captureStaleAfterSeconds, captureState, sourceRecordIDs
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        geographyCode = try container.decodeIfPresent(String.self, forKey: .geographyCode) ?? "GB"
        geographyScope = try container.decodeIfPresent(String.self, forKey: .geographyScope) ?? "national"
        factClass = try container.decodeIfPresent(String.self, forKey: .factClass) ?? "forecast"
        seriesID = try container.decodeIfPresent(String.self, forKey: .seriesID)
        sourceID = try container.decodeIfPresent(String.self, forKey: .sourceID)
        methodologyVersion = try container.decodeIfPresent(String.self, forKey: .methodologyVersion)
        sourceIssuedAt = try container.decodeIfPresent(Date.self, forKey: .sourceIssuedAt)
        capturedAt = try container.decodeIfPresent(Date.self, forKey: .capturedAt)
        vintageAt = try container.decodeIfPresent(Date.self, forKey: .vintageAt)
        vintageBasis = try container.decodeIfPresent(String.self, forKey: .vintageBasis)
        issueTimeBasis = try container.decodeIfPresent(String.self, forKey: .issueTimeBasis)
        captureTimeBasis = try container.decodeIfPresent(String.self, forKey: .captureTimeBasis) ?? "retrieved_at"
        captureAgeSeconds = try container.decodeIfPresent(Int.self, forKey: .captureAgeSeconds)
        captureStaleAfterSeconds = try container.decodeIfPresent(Int.self, forKey: .captureStaleAfterSeconds)
        captureState = try container.decodeIfPresent(String.self, forKey: .captureState) ?? "live"
        sourceRecordIDs = try container.decodeIfPresent([String].self, forKey: .sourceRecordIDs) ?? []
    }
}

struct LocalSearchBounds: Codable, Hashable, Sendable {
    let earliestStart: Date?
    let latestFinish: Date?
    let earliestWasDefaulted: Bool
    let latestWasDefaulted: Bool
    let defaultRule: String?

    private enum CodingKeys: String, CodingKey {
        case earliestStart, latestFinish, earliestWasDefaulted, latestWasDefaulted, defaultRule
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        earliestStart = try container.decodeIfPresent(Date.self, forKey: .earliestStart)
        latestFinish = try container.decodeIfPresent(Date.self, forKey: .latestFinish)
        earliestWasDefaulted = try container.decodeIfPresent(Bool.self, forKey: .earliestWasDefaulted) ?? false
        latestWasDefaulted = try container.decodeIfPresent(Bool.self, forKey: .latestWasDefaulted) ?? false
        defaultRule = try container.decodeIfPresent(String.self, forKey: .defaultRule)
    }
}

struct LocalWindowsResponse: Codable, Hashable, Sendable {
    let schemaVersion: String
    let postcode: String
    let evaluatedAt: Date
    let bounds: LocalSearchBounds?
    let forecast: LocalForecastCapture
    let plan: LocalFlexibleUsePlan
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, postcode, evaluatedAt, bounds, forecast, plan, limitations
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "1.0"
        postcode = try container.decode(String.self, forKey: .postcode)
        evaluatedAt = try container.decode(Date.self, forKey: .evaluatedAt)
        bounds = try container.decodeIfPresent(LocalSearchBounds.self, forKey: .bounds)
        forecast = try container.decode(LocalForecastCapture.self, forKey: .forecast)
        plan = try container.decode(LocalFlexibleUsePlan.self, forKey: .plan)
        limitations = try container.decodeIfPresent([String].self, forKey: .limitations) ?? []
    }
}

extension LocalWindowsResponse {
    var hasSafeNationalForecastScope: Bool {
        forecast.geographyCode.caseInsensitiveCompare("GB") == .orderedSame
            && forecast.geographyScope.caseInsensitiveCompare("national") == .orderedSame
            && forecast.factClass.caseInsensitiveCompare("forecast") == .orderedSame
            && plan.continuous
    }

    func matches(_ request: LocalWindowsRequest) -> Bool {
        guard let responseOutward = PostcodePrivacy.validatedOutwardCode(
            from: postcode,
            defaultWhenEmpty: false
        ) else { return false }
        return responseOutward == request.outwardPostcode
            && plan.requestedDurationMinutes == request.durationMinutes
            && hasSafeNationalForecastScope
    }
}
