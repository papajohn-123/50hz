import Foundation

protocol UnknownStringCodable: RawRepresentable, Codable where RawValue == String {
    static var unknownValue: Self { get }
}

extension UnknownStringCodable {
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self = Self(rawValue: try container.decode(String.self)) ?? Self.unknownValue
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        try container.encode(rawValue)
    }
}

enum TodayBriefingStatus: String, UnknownStringCodable, Hashable, Sendable {
    case complete
    case partial
    case offline
    case observedOnly = "observed_only"
    case empty
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayBriefingSection: String, UnknownStringCodable, Hashable, Sendable {
    case now
    case changes
    case next
    case reportedEvents = "reported_events"
    case bestWindow = "best_window"
    case unknown

    static let unknownValue = Self.unknown
}

enum TodaySourceState: String, UnknownStringCodable, Hashable, Sendable {
    case live
    case delayed
    case stale
    case unavailable
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayChangeDirection: String, UnknownStringCodable, Hashable, Sendable {
    case up
    case down
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayFutureFactClass: String, UnknownStringCodable, Hashable, Sendable {
    case forecast
    case reported
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayCurrentFactClass: String, UnknownStringCodable, Hashable, Sendable {
    case observed
    case estimated
    case derived
    case reported
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayCurrentPositionStatus: String, UnknownStringCodable, Hashable, Sendable {
    case complete
    case partial
    case unavailable
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayReportedEventTiming: String, UnknownStringCodable, Hashable, Sendable {
    case active
    case upcoming
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayReportedEventSeverity: String, UnknownStringCodable, Hashable, Sendable {
    case info
    case notable
    case material
    case critical
    case unknown

    static let unknownValue = Self.unknown
}

enum TodayDisplayPeriodName: String, UnknownStringCodable, Hashable, Sendable {
    case overnight
    case morning
    case afternoon
    case evening
    case unknown

    static let unknownValue = Self.unknown
}

enum LondonDay {
    static let timeZone = TimeZone(identifier: "Europe/London") ?? TimeZone(secondsFromGMT: 0)!

    static func localDateKey(at date: Date = Date()) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let components = calendar.dateComponents([.year, .month, .day], from: date)
        guard let year = components.year,
              let month = components.month,
              let day = components.day else { return "unknown-date" }
        return String(format: "%04d-%02d-%02d", year, month, day)
    }

    static func isValidLocalDateKey(_ value: String) -> Bool {
        guard value.range(
            of: #"^[0-9]{4}-(0[1-9]|1[0-2])-([0-2][0-9]|3[0-1])$"#,
            options: .regularExpression
        ) != nil else { return false }
        let parts = value.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return false }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = timeZone
        let components = DateComponents(
            timeZone: timeZone,
            year: parts[0],
            month: parts[1],
            day: parts[2]
        )
        guard let date = calendar.date(from: components) else { return false }
        let resolved = calendar.dateComponents([.year, .month, .day], from: date)
        return resolved.year == parts[0]
            && resolved.month == parts[1]
            && resolved.day == parts[2]
    }
}

struct TodayBriefingRequest: Hashable, Sendable {
    let localDate: String

    init(localDate: String) {
        self.localDate = LondonDay.isValidLocalDateKey(localDate) ? localDate : "unknown-date"
    }

    init(at date: Date = Date()) {
        localDate = LondonDay.localDateKey(at: date)
    }
}

struct TodayComparisonPeriod: Codable, Hashable, Sendable {
    let id: String
    let label: String
    let start: Date?
    let end: Date?

    private enum CodingKeys: String, CodingKey { case id, label, start, end }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(String.self, forKey: .id) ?? ""
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Comparison period"
        start = try container.decodeIfPresent(Date.self, forKey: .start)
        end = try container.decodeIfPresent(Date.self, forKey: .end)
    }
}

struct TodayRevisionWatermark: Codable, Hashable, Sendable {
    let revisionToken: String
    let asOf: Date?
    let observedThrough: Date?
    let forecastCapturedThrough: Date?
    let reportedThrough: Date?

    private enum CodingKeys: String, CodingKey {
        case revisionToken, asOf, observedThrough, forecastCapturedThrough, reportedThrough
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        revisionToken = try container.decodeIfPresent(String.self, forKey: .revisionToken) ?? ""
        asOf = try container.decodeIfPresent(Date.self, forKey: .asOf)
        observedThrough = try container.decodeIfPresent(Date.self, forKey: .observedThrough)
        forecastCapturedThrough = try container.decodeIfPresent(Date.self, forKey: .forecastCapturedThrough)
        reportedThrough = try container.decodeIfPresent(Date.self, forKey: .reportedThrough)
    }
}

struct TodayBriefingSourceStatus: Codable, Hashable, Sendable, Identifiable {
    var id: String { sourceID }
    let sourceID: String
    let dataset: String
    let state: TodaySourceState
    let revision: Int
    let observedAt: Date?
    let retrievedAt: Date?
    let detail: String?

    private enum CodingKeys: String, CodingKey {
        case sourceID = "sourceId"
        case dataset, state, revision, observedAt, retrievedAt, detail
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        sourceID = try container.decodeIfPresent(String.self, forKey: .sourceID) ?? ""
        dataset = try container.decodeIfPresent(String.self, forKey: .dataset) ?? "Unknown dataset"
        state = try container.decodeIfPresent(TodaySourceState.self, forKey: .state) ?? .unknown
        revision = try container.decodeIfPresent(Int.self, forKey: .revision) ?? 0
        observedAt = try container.decodeIfPresent(Date.self, forKey: .observedAt)
        retrievedAt = try container.decodeIfPresent(Date.self, forKey: .retrievedAt)
        detail = try container.decodeIfPresent(String.self, forKey: .detail)
    }
}

struct TodayCurrentValue: Codable, Hashable, Sendable, Identifiable {
    var id: String { stableID.isEmpty ? metricID : stableID }
    let stableID: String
    let metricID: String
    let label: String
    let value: Double?
    let unit: String
    let factClass: TodayCurrentFactClass
    let observedAt: Date?
    let sourceIDs: [String]

    private enum CodingKeys: String, CodingKey {
        case stableID = "stableId"
        case metricID = "metricId"
        case sourceIDs = "sourceIds"
        case label, value, unit, factClass, observedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        stableID = try container.decodeIfPresent(String.self, forKey: .stableID) ?? ""
        metricID = try container.decodeIfPresent(String.self, forKey: .metricID) ?? "unknown"
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Current value"
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        unit = try container.decodeIfPresent(String.self, forKey: .unit) ?? ""
        factClass = try container.decodeIfPresent(TodayCurrentFactClass.self, forKey: .factClass) ?? .unknown
        observedAt = try container.decodeIfPresent(Date.self, forKey: .observedAt)
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
    }
}

struct TodayCurrentPosition: Codable, Hashable, Sendable {
    let status: TodayCurrentPositionStatus
    let asOf: Date?
    let values: [TodayCurrentValue]
    let missingMetricIDs: [String]
    let text: String

    private enum CodingKeys: String, CodingKey {
        case status, asOf, values, text
        case missingMetricIDs = "missingMetricIds"
    }

    init(
        status: TodayCurrentPositionStatus = .unknown,
        asOf: Date? = nil,
        values: [TodayCurrentValue] = [],
        missingMetricIDs: [String] = [],
        text: String = "No validated current values are available."
    ) {
        self.status = status
        self.asOf = asOf
        self.values = values
        self.missingMetricIDs = missingMetricIDs
        self.text = text
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(TodayCurrentPositionStatus.self, forKey: .status) ?? .unknown
        asOf = try container.decodeIfPresent(Date.self, forKey: .asOf)
        values = try container.decodeIfPresent([TodayCurrentValue].self, forKey: .values) ?? []
        missingMetricIDs = try container.decodeIfPresent([String].self, forKey: .missingMetricIDs) ?? []
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? "No validated current values are available."
    }
}

struct TodayObservedChange: Codable, Hashable, Sendable, Identifiable {
    var id: String { stableID.isEmpty ? "\(metricID)-\(observedAt?.timeIntervalSince1970 ?? 0)" : stableID }
    let stableID: String
    let metricID: String
    let label: String
    let direction: TodayChangeDirection
    let currentValue: Double?
    let previousValue: Double?
    let delta: Double?
    let unit: String
    let observedAt: Date?
    let comparisonPeriodID: String
    let significance: Double?
    let sourceIDs: [String]
    let text: String

    private enum CodingKeys: String, CodingKey {
        case stableID = "stableId"
        case metricID = "metricId"
        case comparisonPeriodID = "comparisonPeriodId"
        case sourceIDs = "sourceIds"
        case label, direction, currentValue, previousValue
        case delta, unit, observedAt, significance, text
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        stableID = try container.decodeIfPresent(String.self, forKey: .stableID) ?? ""
        metricID = try container.decodeIfPresent(String.self, forKey: .metricID) ?? "unknown"
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Observed change"
        direction = try container.decodeIfPresent(TodayChangeDirection.self, forKey: .direction) ?? .unknown
        currentValue = try container.decodeIfPresent(Double.self, forKey: .currentValue)
        previousValue = try container.decodeIfPresent(Double.self, forKey: .previousValue)
        delta = try container.decodeIfPresent(Double.self, forKey: .delta)
        unit = try container.decodeIfPresent(String.self, forKey: .unit) ?? ""
        observedAt = try container.decodeIfPresent(Date.self, forKey: .observedAt)
        comparisonPeriodID = try container.decodeIfPresent(String.self, forKey: .comparisonPeriodID) ?? ""
        significance = try container.decodeIfPresent(Double.self, forKey: .significance)
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? label
    }
}

struct TodayFutureMoment: Codable, Hashable, Sendable, Identifiable {
    var id: String { stableID.isEmpty ? "\(label)-\(startsAt?.timeIntervalSince1970 ?? 0)" : stableID }
    let stableID: String
    let label: String
    let startsAt: Date?
    let endsAt: Date?
    let factClass: TodayFutureFactClass
    let importance: Double?
    let sourceIDs: [String]
    let value: Double?
    let unit: String?
    let text: String

    private enum CodingKeys: String, CodingKey {
        case stableID = "stableId"
        case sourceIDs = "sourceIds"
        case label, startsAt, endsAt, factClass, importance, value, unit, text
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        stableID = try container.decodeIfPresent(String.self, forKey: .stableID) ?? ""
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Future moment"
        startsAt = try container.decodeIfPresent(Date.self, forKey: .startsAt)
        endsAt = try container.decodeIfPresent(Date.self, forKey: .endsAt)
        factClass = try container.decodeIfPresent(TodayFutureFactClass.self, forKey: .factClass) ?? .unknown
        importance = try container.decodeIfPresent(Double.self, forKey: .importance)
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
        value = try container.decodeIfPresent(Double.self, forKey: .value)
        unit = try container.decodeIfPresent(String.self, forKey: .unit)
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? label
    }
}

struct TodayReportedEvent: Codable, Hashable, Sendable, Identifiable {
    var id: String { stableID.isEmpty ? revisionID : stableID }
    let stableID: String
    let revisionID: String
    let revisionNumber: Int
    let title: String
    let severity: TodayReportedEventSeverity
    let timing: TodayReportedEventTiming
    let publishedAt: Date?
    let startsAt: Date?
    let endsAt: Date?
    let sourceIDs: [String]
    let evidenceClass: String
    let text: String

    private enum CodingKeys: String, CodingKey {
        case stableID = "stableId"
        case revisionID = "revisionId"
        case sourceIDs = "sourceIds"
        case revisionNumber, title, severity, timing
        case publishedAt, startsAt, endsAt, evidenceClass, text
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        stableID = try container.decodeIfPresent(String.self, forKey: .stableID) ?? ""
        revisionID = try container.decodeIfPresent(String.self, forKey: .revisionID) ?? ""
        revisionNumber = try container.decodeIfPresent(Int.self, forKey: .revisionNumber) ?? 0
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? "Reported event"
        severity = try container.decodeIfPresent(TodayReportedEventSeverity.self, forKey: .severity) ?? .unknown
        timing = try container.decodeIfPresent(TodayReportedEventTiming.self, forKey: .timing) ?? .unknown
        publishedAt = try container.decodeIfPresent(Date.self, forKey: .publishedAt)
        startsAt = try container.decodeIfPresent(Date.self, forKey: .startsAt)
        endsAt = try container.decodeIfPresent(Date.self, forKey: .endsAt)
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
        evidenceClass = try container.decodeIfPresent(String.self, forKey: .evidenceClass) ?? "reported"
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? title
    }
}

struct TodayReportedEvents: Codable, Hashable, Sendable {
    let items: [TodayReportedEvent]
    let totalCount: Int

    private enum CodingKeys: String, CodingKey { case items, totalCount }

    init(items: [TodayReportedEvent] = [], totalCount: Int = 0) {
        self.items = items
        self.totalCount = totalCount
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        items = try container.decodeIfPresent([TodayReportedEvent].self, forKey: .items) ?? []
        totalCount = try container.decodeIfPresent(Int.self, forKey: .totalCount) ?? items.count
    }
}

struct TodayBestWindow: Codable, Hashable, Sendable, Identifiable {
    var id: String { stableID }
    let stableID: String
    let label: String
    let start: Date?
    let end: Date?
    let averageValue: Double?
    let unit: String
    let sourceIDs: [String]
    let coverageFraction: Double?
    let factClass: String
    let methodologyVersion: String
    let capturedAt: Date?
    let text: String

    private enum CodingKeys: String, CodingKey {
        case stableID = "stableId"
        case sourceIDs = "sourceIds"
        case label, start, end, averageValue, unit
        case coverageFraction, factClass, methodologyVersion, capturedAt, text
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        stableID = try container.decodeIfPresent(String.self, forKey: .stableID) ?? ""
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Lowest national carbon forecast window"
        start = try container.decodeIfPresent(Date.self, forKey: .start)
        end = try container.decodeIfPresent(Date.self, forKey: .end)
        averageValue = try container.decodeIfPresent(Double.self, forKey: .averageValue)
        unit = try container.decodeIfPresent(String.self, forKey: .unit) ?? "gCO2/kWh"
        sourceIDs = try container.decodeIfPresent([String].self, forKey: .sourceIDs) ?? []
        coverageFraction = try container.decodeIfPresent(Double.self, forKey: .coverageFraction)
        factClass = try container.decodeIfPresent(String.self, forKey: .factClass) ?? "forecast"
        methodologyVersion = try container.decodeIfPresent(String.self, forKey: .methodologyVersion) ?? ""
        capturedAt = try container.decodeIfPresent(Date.self, forKey: .capturedAt)
        text = try container.decodeIfPresent(String.self, forKey: .text) ?? label
    }

    var isCompleteNationalForecastWindow: Bool {
        factClass.caseInsensitiveCompare("forecast") == .orderedSame
            && coverageFraction == 1
            && start != nil
            && end != nil
            && averageValue != nil
            && capturedAt != nil
    }
}

struct TodayBriefingCoverage: Codable, Hashable, Sendable {
    let status: TodayBriefingStatus
    let availableSections: [TodayBriefingSection]
    let missingFamilies: [String]
    let sourceCountsByState: [String: Int]
    let notes: [String]

    private enum CodingKeys: String, CodingKey {
        case status, availableSections, missingFamilies, sourceCountsByState, notes
    }

    init(
        status: TodayBriefingStatus = .unknown,
        availableSections: [TodayBriefingSection] = [],
        missingFamilies: [String] = [],
        sourceCountsByState: [String: Int] = [:],
        notes: [String] = []
    ) {
        self.status = status
        self.availableSections = availableSections
        self.missingFamilies = missingFamilies
        self.sourceCountsByState = sourceCountsByState
        self.notes = notes
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        status = try container.decodeIfPresent(TodayBriefingStatus.self, forKey: .status) ?? .unknown
        availableSections = try container.decodeIfPresent([TodayBriefingSection].self, forKey: .availableSections) ?? []
        missingFamilies = try container.decodeIfPresent([String].self, forKey: .missingFamilies) ?? []
        sourceCountsByState = try container.decodeIfPresent([String: Int].self, forKey: .sourceCountsByState) ?? [:]
        notes = try container.decodeIfPresent([String].self, forKey: .notes) ?? []
    }
}

struct TodayDisplayPeriod: Codable, Hashable, Sendable {
    let timezone: String
    let localDate: String
    let name: TodayDisplayPeriodName
    let label: String
    let startsAt: Date?
    let endsAt: Date?

    private enum CodingKeys: String, CodingKey { case timezone, localDate, name, label, startsAt, endsAt }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone) ?? "Europe/London"
        localDate = try container.decode(String.self, forKey: .localDate)
        name = try container.decodeIfPresent(TodayDisplayPeriodName.self, forKey: .name) ?? .unknown
        label = try container.decodeIfPresent(String.self, forKey: .label) ?? "Today"
        startsAt = try container.decodeIfPresent(Date.self, forKey: .startsAt)
        endsAt = try container.decodeIfPresent(Date.self, forKey: .endsAt)
    }
}

struct TodayBriefingMethodology: Codable, Hashable, Sendable {
    let version: String
    let timezone: String
    let maxCurrentValues: Int
    let maxChanges: Int
    let maxNextMoments: Int
    let maxReportedEvents: Int
    let meaningfulChangeRule: String?
    let currentRanking: String?
    let changeRanking: String?
    let nextRanking: String?
    let eventRanking: String?
    let revisionRule: String?
    let causalAttribution: Bool

    private enum CodingKeys: String, CodingKey {
        case version, timezone, maxCurrentValues, maxChanges, maxNextMoments
        case maxReportedEvents, meaningfulChangeRule, currentRanking, changeRanking
        case nextRanking, eventRanking, revisionRule, causalAttribution
    }

    init(
        version: String = "50hz.briefing.v1",
        timezone: String = "Europe/London",
        maxCurrentValues: Int = 3,
        maxChanges: Int = 3,
        maxNextMoments: Int = 3,
        maxReportedEvents: Int = 3,
        meaningfulChangeRule: String? = nil,
        currentRanking: String? = nil,
        changeRanking: String? = nil,
        nextRanking: String? = nil,
        eventRanking: String? = nil,
        revisionRule: String? = nil,
        causalAttribution: Bool = false
    ) {
        self.version = version
        self.timezone = timezone
        self.maxCurrentValues = maxCurrentValues
        self.maxChanges = maxChanges
        self.maxNextMoments = maxNextMoments
        self.maxReportedEvents = maxReportedEvents
        self.meaningfulChangeRule = meaningfulChangeRule
        self.currentRanking = currentRanking
        self.changeRanking = changeRanking
        self.nextRanking = nextRanking
        self.eventRanking = eventRanking
        self.revisionRule = revisionRule
        self.causalAttribution = causalAttribution
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        version = try container.decodeIfPresent(String.self, forKey: .version) ?? "50hz.briefing.v1"
        timezone = try container.decodeIfPresent(String.self, forKey: .timezone) ?? "Europe/London"
        maxCurrentValues = try container.decodeIfPresent(Int.self, forKey: .maxCurrentValues) ?? 3
        maxChanges = try container.decodeIfPresent(Int.self, forKey: .maxChanges) ?? 3
        maxNextMoments = try container.decodeIfPresent(Int.self, forKey: .maxNextMoments) ?? 3
        maxReportedEvents = try container.decodeIfPresent(Int.self, forKey: .maxReportedEvents) ?? 3
        meaningfulChangeRule = try container.decodeIfPresent(String.self, forKey: .meaningfulChangeRule)
        currentRanking = try container.decodeIfPresent(String.self, forKey: .currentRanking)
        changeRanking = try container.decodeIfPresent(String.self, forKey: .changeRanking)
        nextRanking = try container.decodeIfPresent(String.self, forKey: .nextRanking)
        eventRanking = try container.decodeIfPresent(String.self, forKey: .eventRanking)
        revisionRule = try container.decodeIfPresent(String.self, forKey: .revisionRule)
        causalAttribution = try container.decodeIfPresent(Bool.self, forKey: .causalAttribution) ?? false
    }
}

struct TodayBriefing: Codable, Hashable, Sendable {
    let schemaVersion: String
    let methodology: TodayBriefingMethodology
    let generatedAt: Date?
    let asOf: Date
    let now: TodayCurrentPosition
    let displayPeriod: TodayDisplayPeriod
    let headline: String
    let summary: String
    let changes: [TodayObservedChange]
    let nextMoments: [TodayFutureMoment]
    let reportedEvents: TodayReportedEvents
    let bestWindow: TodayBestWindow?
    let coverage: TodayBriefingCoverage
    let sourceStatuses: [TodayBriefingSourceStatus]
    let comparisonPeriods: [TodayComparisonPeriod]
    let revisionWatermark: TodayRevisionWatermark?
    let limitations: [String]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, methodology, generatedAt, asOf, now, displayPeriod
        case headline, summary, changes, nextMoments, reportedEvents, bestWindow
        case coverage, sourceStatuses, comparisonPeriods, revisionWatermark, limitations
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "1.0"
        methodology = try container.decodeIfPresent(TodayBriefingMethodology.self, forKey: .methodology) ?? TodayBriefingMethodology()
        generatedAt = try container.decodeIfPresent(Date.self, forKey: .generatedAt)
        asOf = try container.decode(Date.self, forKey: .asOf)
        now = try container.decodeIfPresent(TodayCurrentPosition.self, forKey: .now) ?? TodayCurrentPosition()
        displayPeriod = try container.decode(TodayDisplayPeriod.self, forKey: .displayPeriod)
        headline = try container.decodeIfPresent(String.self, forKey: .headline) ?? "Today’s grid briefing"
        summary = try container.decodeIfPresent(String.self, forKey: .summary) ?? "Briefing coverage is available below."
        changes = try container.decodeIfPresent([TodayObservedChange].self, forKey: .changes) ?? []
        nextMoments = try container.decodeIfPresent([TodayFutureMoment].self, forKey: .nextMoments) ?? []
        reportedEvents = try container.decodeIfPresent(TodayReportedEvents.self, forKey: .reportedEvents) ?? TodayReportedEvents()
        bestWindow = try container.decodeIfPresent(TodayBestWindow.self, forKey: .bestWindow)
        coverage = try container.decodeIfPresent(TodayBriefingCoverage.self, forKey: .coverage) ?? TodayBriefingCoverage()
        sourceStatuses = try container.decodeIfPresent([TodayBriefingSourceStatus].self, forKey: .sourceStatuses) ?? []
        comparisonPeriods = try container.decodeIfPresent([TodayComparisonPeriod].self, forKey: .comparisonPeriods) ?? []
        revisionWatermark = try container.decodeIfPresent(TodayRevisionWatermark.self, forKey: .revisionWatermark)
        limitations = try container.decodeIfPresent([String].self, forKey: .limitations) ?? []
    }

    func matches(_ request: TodayBriefingRequest) -> Bool {
        displayPeriod.timezone == "Europe/London"
            && displayPeriod.localDate == request.localDate
            && methodology.timezone == "Europe/London"
            && methodology.causalAttribution == false
    }
}
