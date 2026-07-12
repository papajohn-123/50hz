import Foundation

// Unknown server values remain decodable but collapse to a finite, non-actionable
// state until this client explicitly understands them.
enum SourceDeliveryHealth: String, UnknownStringCodable, Hashable, Sendable {
    case healthy, delayed, stale, unavailable, unknown
    static let unknownValue = Self.unknown
}

enum SourceFactHealth: String, UnknownStringCodable, Hashable, Sendable {
    case live, delayed, stale, unavailable
    case notApplicable = "not_applicable"
    case unknown
    static let unknownValue = Self.unknown
}

enum EventLifecycleState: String, UnknownStringCodable, Hashable, Sendable {
    case open, updated, resolved, superseded, withdrawn, unknown
    static let unknownValue = Self.unknown
}

enum EventRevisionAuthority: String, UnknownStringCodable, Hashable, Sendable {
    case systemWarning = "system_warning"
    case authoritativeNotice = "authoritative_notice"
    case otherReported = "other_reported"
    case unknown
    static let unknownValue = Self.unknown
}

enum EventChangedField: String, UnknownStringCodable, Hashable, Sendable {
    case unavailableMW, normalCapacityMW, effectiveStart, effectiveEnd, status
    case reportedCause, evidenceChecksum, materialReason, unknown
    static let unknownValue = Self.unknown
}

enum ExportMetricID: String, UnknownStringCodable, Hashable, Sendable, Identifiable {
    case nationalCarbon = "carbon.intensity.national"
    case nationalDemand = "demand.national_outturn"
    case generationFuel = "generation.transmission_visible_by_fuel"
    case interconnectorFlow = "interconnector.flow"
    case unknown
    static let unknownValue = Self.unknown
    var id: String { rawValue }
}

enum InspectionExportFormat: String, UnknownStringCodable, Hashable, Sendable, Identifiable {
    case json, csv, unknown
    static let unknownValue = Self.unknown
    var id: String { rawValue }
}

// MARK: - Source health

struct SourceStatusResponse: Decodable, Equatable, Sendable {
    let schemaVersion: String
    let evaluatedAt: Date
    let sourceCount: Int
    let sources: [InspectedSourceStatus]
    let definitions: [String: String]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, evaluatedAt, sourceCount, sources, definitions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(String.self, forKey: .schemaVersion)
        evaluatedAt = try container.decode(Date.self, forKey: .evaluatedAt)
        sourceCount = try container.decode(Int.self, forKey: .sourceCount)
        sources = try container.decode([InspectedSourceStatus].self, forKey: .sources)
        definitions = try container.decodeIfPresent([String: String].self, forKey: .definitions) ?? [:]

        guard sourceCount == sources.count, (0...64).contains(sourceCount) else {
            throw DecodingError.dataCorruptedError(
                forKey: .sourceCount,
                in: container,
                debugDescription: "Source count must match a bounded source list."
            )
        }
    }
}

struct InspectedSourceStatus: Decodable, Equatable, Identifiable, Sendable {
    let sourceID: String
    let publisher: String
    let dataset: String
    let displayName: String
    let documentationURL: String?
    let licenceURL: String?
    let attribution: String
    let expectedFactCadenceSeconds: Int
    let deliveryState: SourceDeliveryHealth
    let deliveryLagSeconds: Int?
    let lastAttemptedAt: Date?
    let lastAttemptState: String?
    let lastSucceededAt: Date?
    let factState: SourceFactHealth
    let factFamilies: [String]
    let observedAt: Date?
    let validTo: Date?
    let factAgeSeconds: Int?
    let note: String

    var id: String { sourceID }

    private enum CodingKeys: String, CodingKey {
        case sourceID, publisher, dataset, displayName, documentationURL, licenceURL
        case attribution, expectedFactCadenceSeconds, deliveryState, deliveryLagSeconds
        case lastAttemptedAt, lastAttemptState, lastSucceededAt, factState, factFamilies
        case observedAt, validTo, factAgeSeconds, note
    }
}

// MARK: - Event revision history

struct EventHistoryResponse: Decodable, Equatable, Sendable {
    let schemaVersion: String
    let eventID: String
    let lifecycleStatus: EventLifecycleState
    let revisionOrder: String
    let revisionCount: Int
    let returnedRevisionCount: Int
    let isTruncated: Bool
    let firstPublishedAt: Date
    let latestPublishedAt: Date
    let revisions: [EventHistoryRevision]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, eventID, lifecycleStatus, revisionOrder, revisionCount
        case returnedRevisionCount, isTruncated, firstPublishedAt, latestPublishedAt, revisions
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decode(String.self, forKey: .schemaVersion)
        eventID = try container.decode(String.self, forKey: .eventID)
        lifecycleStatus = try container.decode(EventLifecycleState.self, forKey: .lifecycleStatus)
        revisionOrder = try container.decode(String.self, forKey: .revisionOrder)
        revisionCount = try container.decode(Int.self, forKey: .revisionCount)
        returnedRevisionCount = try container.decode(Int.self, forKey: .returnedRevisionCount)
        isTruncated = try container.decode(Bool.self, forKey: .isTruncated)
        firstPublishedAt = try container.decode(Date.self, forKey: .firstPublishedAt)
        latestPublishedAt = try container.decode(Date.self, forKey: .latestPublishedAt)
        revisions = try container.decode([EventHistoryRevision].self, forKey: .revisions)

        let numbers = revisions.map(\.revisionNumber)
        guard InspectionEndpoint.isValidEventID(eventID),
              revisionOrder == "newestFirst",
              (1...100).contains(returnedRevisionCount),
              returnedRevisionCount == revisions.count,
              revisionCount >= returnedRevisionCount,
              isTruncated == (revisionCount > returnedRevisionCount),
              numbers == numbers.sorted(by: >),
              Set(numbers).count == numbers.count,
              revisions.first?.status == lifecycleStatus,
              latestPublishedAt >= firstPublishedAt else {
            throw DecodingError.dataCorruptedError(
                forKey: .revisions,
                in: container,
                debugDescription: "Event history summary and newest-first revision list must agree."
            )
        }
    }
}

struct EventHistoryRevision: Decodable, Equatable, Identifiable, Sendable {
    let revisionNumber: Int
    let status: EventLifecycleState
    let authority: EventRevisionAuthority
    let evidenceClass: String
    let publishedAt: Date
    let effectiveWindow: EventEffectiveWindow?
    let reportedAsset: EventReportedAsset?
    let reportedCapacity: EventReportedCapacity?
    let planned: Bool?
    let reportedCause: String?
    let materialReason: String?
    let supersededByEventID: String?
    let sourceIDs: [String]
    let sourceRecordIDs: [String]
    let evidenceChecksum: String
    let changes: [EventHistoryChange]

    var id: Int { revisionNumber }

    private enum CodingKeys: String, CodingKey {
        case revisionNumber, status, authority, evidenceClass, publishedAt, effectiveWindow
        case reportedAsset, reportedCapacity, planned, reportedCause, materialReason
        case supersededByEventID, sourceIDs, sourceRecordIDs, evidenceChecksum, changes
    }
}

struct EventEffectiveWindow: Decodable, Equatable, Sendable {
    let start: Date?
    let end: Date?
}

struct EventReportedAsset: Decodable, Equatable, Sendable {
    let assetID: String?
    let name: String?
    let identityReliable: Bool
}

struct EventReportedCapacity: Decodable, Equatable, Sendable {
    let unavailableMW: Double?
    let normalCapacityMW: Double?
}

struct EventHistoryChange: Decodable, Equatable, Identifiable, Sendable {
    let field: EventChangedField
    let before: InspectionScalar
    let after: InspectionScalar

    var id: String { "\(field.rawValue)-\(before.description)-\(after.description)" }
}

enum InspectionScalar: Decodable, Equatable, Sendable, CustomStringConvertible {
    case number(Double)
    case text(String)
    case boolean(Bool)
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(Bool.self) {
            self = .boolean(value)
        } else {
            self = .text(try container.decode(String.self))
        }
    }

    var description: String {
        switch self {
        case .number(let value): value.formatted(.number.precision(.fractionLength(0...2)))
        case .text(let value): value
        case .boolean(let value): value ? "true" : "false"
        case .null: "Not supplied"
        }
    }
}

// MARK: - Export schema and response

struct ExportSchemaResponse: Decodable, Equatable, Sendable {
    let schemaVersion: String
    let maxWindowDays: Int
    let maxRowCount: Int
    let resolutionsSeconds: [Int]
    let formats: [InspectionExportFormat]
    let timestampPolicy: String
    let missingDataPolicy: String
    let metrics: [ExportMetricSchema]
}

struct ExportMetricSchema: Decodable, Equatable, Identifiable, Sendable {
    let metric: ExportMetricID
    let selectorRequired: Bool
    let allowedSelectors: [String]

    var id: String { metric.rawValue }
}

struct ExportResponse: Decodable, Equatable, Sendable {
    let schemaVersion: String
    let generatedAt: Date
    let requestedFrom: Date
    let requestedTo: Date
    let resolutionSeconds: Int
    let metricID: String
    let geography: String
    let unit: String
    let classification: String
    let sourceID: String
    let sourceMethodologyVersion: String
    let materializationMethodologyVersion: String
    let coverage: ExportCoverage
    let rows: [ExportRow]
}

struct ExportCoverage: Decodable, Equatable, Sendable {
    let expectedIntervalCount: Int
    let availableIntervalCount: Int
    let missingIntervalCount: Int
    let coverageFraction: Double
    let isComplete: Bool
}

struct ExportRow: Decodable, Equatable, Sendable {
    let start: Date
    let end: Date
    let status: String
    let value: Double?
    let unit: String
    let classification: String
    let metricID: String
    let geography: String
    let sourceID: String
    let sourceRecordIDs: [String]
    let sourceMethodologyVersion: String
    let materializationMethodologyVersion: String
    let coverageFraction: Double
}

struct ExportRequestSpec: Hashable, Sendable {
    static let resolutionSeconds = 1_800
    static let maximumRows = 1_488

    let metric: ExportMetricID
    let selector: String?
    let from: Date
    let to: Date
    let format: InspectionExportFormat

    init(
        metric: ExportMetricID,
        selector: String?,
        from: Date,
        to: Date,
        format: InspectionExportFormat
    ) throws {
        let normalizedSelector = selector?.trimmingCharacters(in: .whitespacesAndNewlines)
        guard Self.isHalfHourBoundary(from), Self.isHalfHourBoundary(to), to > from else {
            throw InspectionRequestError.invalidTimeBounds
        }
        let seconds = to.timeIntervalSince(from)
        guard seconds <= 31 * 24 * 3_600,
              seconds.truncatingRemainder(dividingBy: 1_800) == 0,
              Int(seconds / 1_800) <= Self.maximumRows else {
            throw InspectionRequestError.windowTooLarge
        }
        self.metric = metric
        self.selector = normalizedSelector?.isEmpty == false ? normalizedSelector : nil
        self.from = from
        self.to = to
        self.format = format
    }

    var expectedRowCount: Int { Int(to.timeIntervalSince(from) / 1_800) }

    static func recent(
        metric: ExportMetricID,
        selector: String?,
        days: Int,
        format: InspectionExportFormat,
        now: Date = Date()
    ) throws -> ExportRequestSpec {
        guard (1...31).contains(days) else { throw InspectionRequestError.windowTooLarge }
        let end = mostRecentHalfHour(at: now)
        return try ExportRequestSpec(
            metric: metric,
            selector: selector,
            from: end.addingTimeInterval(TimeInterval(-days * 24 * 3_600)),
            to: end,
            format: format
        )
    }

    static func mostRecentHalfHour(at date: Date) -> Date {
        let seconds = floor(date.timeIntervalSince1970 / 1_800) * 1_800
        return Date(timeIntervalSince1970: seconds)
    }

    static func isHalfHourBoundary(_ date: Date) -> Bool {
        date.timeIntervalSince1970.truncatingRemainder(dividingBy: 1_800) == 0
    }
}

enum InspectionRequestError: LocalizedError, Equatable {
    case invalidTimeBounds
    case windowTooLarge
    case selectorRequired
    case unsupportedSelection
    case invalidEventID
    case filePreparationFailed

    var errorDescription: String? {
        switch self {
        case .invalidTimeBounds:
            "Export bounds must be exact UTC half-hours, with the end after the start."
        case .windowTooLarge:
            "Exports are limited to 31 days and 1,488 half-hour rows."
        case .selectorRequired:
            "Choose a source category for this metric."
        case .unsupportedSelection:
            "That export option is not in the server’s published allowlist."
        case .invalidEventID:
            "Revision history is unavailable for this event identity."
        case .filePreparationFailed:
            "The export was received, but its protected share file could not be prepared."
        }
    }
}

struct PreparedExport: Equatable, Identifiable, Sendable {
    let url: URL
    let format: InspectionExportFormat
    let metric: ExportMetricID
    let requestedFrom: Date
    let requestedTo: Date
    let expectedRows: Int
    let missingRows: Int
    let preparedAt: Date

    var id: URL { url }
}

enum InspectionEndpoint {
    static func isValidEventID(_ value: String) -> Bool {
        guard value.count == 24, value.hasPrefix("evt_") else { return false }
        return value.dropFirst(4).allSatisfy { "0123456789abcdef".contains($0) }
    }
}
