import Foundation

// Forecast verification is deliberately separate from the live forecast. These
// values describe error observed across stored national forecast/outturn pairs;
// they do not predict the error of one future interval.
enum ForecastVerificationMetricID: String, UnknownStringCodable, Hashable, Sendable, CaseIterable, Identifiable {
    case nationalDemand = "national_demand"
    case windGeneration = "wind_generation"
    case nationalCarbonIntensity = "national_carbon_intensity"
    case unknown

    static let unknownValue = Self.unknown
    var id: String { rawValue }
}

enum ForecastVerificationStatus: String, UnknownStringCodable, Hashable, Sendable {
    case available
    case insufficientData = "insufficient_data"
    case unknown

    static let unknownValue = Self.unknown
}

enum ForecastVerificationHorizonID: String, UnknownStringCodable, Hashable, Sendable, CaseIterable, Identifiable {
    case zeroToThreeHours = "0_3h"
    case threeToTwelveHours = "3_12h"
    case twelveToTwentyFourHours = "12_24h"
    case twentyFourToFortyEightHours = "24_48h"
    case unknown

    static let unknownValue = Self.unknown
    var id: String { rawValue }

    var reviewedBounds: (minimumHours: Int, maximumHours: Int)? {
        switch self {
        case .zeroToThreeHours: (0, 3)
        case .threeToTwelveHours: (3, 12)
        case .twelveToTwentyFourHours: (12, 24)
        case .twentyFourToFortyEightHours: (24, 48)
        case .unknown: nil
        }
    }
}

enum ForecastVerificationFactClass: String, UnknownStringCodable, Hashable, Sendable {
    case forecast, observed, estimated, unknown
    static let unknownValue = Self.unknown
}

struct ForecastVerificationResponse: Decodable, Equatable, Sendable {
    let schemaVersion: String
    let generatedAt: Date?
    let minimumVerifiedSamples: Int?
    let minimumCoverage: Double?
    let results: [ForecastVerificationItem]
    let methodology: [String: String]

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, generatedAt, minimumVerifiedSamples, minimumCoverage
        case results, methodology
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(String.self, forKey: .schemaVersion) ?? "unknown"
        generatedAt = try container.decodeIfPresent(Date.self, forKey: .generatedAt)
        minimumVerifiedSamples = try container.decodeIfPresent(Int.self, forKey: .minimumVerifiedSamples)
        minimumCoverage = try container.decodeIfPresent(Double.self, forKey: .minimumCoverage)
        results = try container.decodeIfPresent([ForecastVerificationItem].self, forKey: .results) ?? []
        methodology = try container.decodeIfPresent([String: String].self, forKey: .methodology) ?? [:]

        guard results.count <= 32 else {
            throw DecodingError.dataCorruptedError(
                forKey: .results,
                in: container,
                debugDescription: "Forecast verification must remain a bounded result set."
            )
        }
    }

    /// The public contract currently returns one item for each known
    /// metric/horizon pair. A duplicate is treated as ambiguous and is never
    /// eligible for display.
    func uniqueItem(
        metric: ForecastVerificationMetricID,
        horizon: ForecastVerificationHorizonID
    ) -> ForecastVerificationItem? {
        let matches = results.filter { $0.metric == metric && $0.horizon.id == horizon }
        return matches.count == 1 ? matches[0] : nil
    }
}

struct ForecastVerificationItem: Decodable, Equatable, Sendable, Identifiable {
    let metric: ForecastVerificationMetricID
    let displayName: String
    let unit: String
    let expectedIntervalMinutes: Int
    let horizon: ForecastVerificationHorizon
    let status: ForecastVerificationStatus
    let reason: String
    let mae: Double?
    let bias: Double?
    let wapePercent: Double?
    let verifiedSamples: Int
    let expectedSamples: Int
    let coverage: Double
    let verificationWindow: ForecastVerificationWindow?
    let issueTimeBasis: String
    let effectiveVintageTimeBasis: String
    let forecast: ForecastVerificationSource
    let outturn: ForecastVerificationSource
    let registryVersion: String
    let verificationMethodologyVersion: String
    let evidenceChecksum: String?
    let revision: Int?
    let computedAt: Date?
    let sourceWatermarkAt: Date?

    var id: String { "\(metric.rawValue)-\(horizon.id.rawValue)" }

    private enum CodingKeys: String, CodingKey {
        case metric, displayName, unit, expectedIntervalMinutes, horizon, status, reason
        case mae, bias, wapePercent, verifiedSamples, expectedSamples, coverage
        case verificationWindow, issueTimeBasis, forecast, outturn, registryVersion
        case effectiveVintageTimeBasis
        case verificationMethodologyVersion, evidenceChecksum, revision, computedAt
        case sourceWatermarkAt
    }

    /// A second client-side gate prevents malformed, downgraded, duplicated, or
    /// future-contract rows from surfacing numbers as reviewed evidence.
    func isDisplayEligible(in response: ForecastVerificationResponse) -> Bool {
        guard metric != .unknown,
              horizon.hasReviewedBounds,
              status == .available,
              reason == "eligible",
              let publishedMinimumSamples = response.minimumVerifiedSamples,
              publishedMinimumSamples >= 100,
              let publishedMinimumCoverage = response.minimumCoverage,
              publishedMinimumCoverage >= 0.90,
              verifiedSamples >= max(100, publishedMinimumSamples),
              expectedSamples >= verifiedSamples,
              expectedSamples > 0,
              coverage.isFinite,
              coverage >= max(0.90, publishedMinimumCoverage),
              coverage <= 1,
              abs(coverage - (Double(verifiedSamples) / Double(expectedSamples))) < 0.000_001,
              let mae, mae.isFinite, mae >= 0,
              let bias, bias.isFinite,
              wapePercent.map({ $0.isFinite && $0 >= 0 }) ?? true,
              let window = verificationWindow,
              window.end > window.start,
              !issueTimeBasis.isEmpty,
              !effectiveVintageTimeBasis.isEmpty,
              forecast.factClass == .forecast,
              outturn.factClass == .observed || outturn.factClass == .estimated,
              !forecast.sourceID.isEmpty,
              !outturn.sourceID.isEmpty,
              !forecast.methodologyVersion.isEmpty,
              !outturn.methodologyVersion.isEmpty,
              !registryVersion.isEmpty,
              !verificationMethodologyVersion.isEmpty,
              evidenceChecksum?.count == 64,
              let revision, revision >= 0,
              computedAt != nil,
              response.uniqueItem(metric: metric, horizon: horizon.id) != nil,
              hasReviewedMetricUnit else {
            return false
        }
        return true
    }

    private var hasReviewedMetricUnit: Bool {
        switch metric {
        case .nationalDemand, .windGeneration:
            unit == "MW"
        case .nationalCarbonIntensity:
            unit == "gCO2/kWh"
        case .unknown:
            false
        }
    }
}

struct ForecastVerificationHorizon: Decodable, Equatable, Sendable {
    let id: ForecastVerificationHorizonID
    let minimumHours: Int
    let maximumHours: Int

    var hasReviewedBounds: Bool {
        guard let reviewed = id.reviewedBounds else { return false }
        return minimumHours == reviewed.minimumHours
            && maximumHours == reviewed.maximumHours
            && maximumHours > minimumHours
    }

    func contains(startLead: TimeInterval, endLead: TimeInterval) -> Bool {
        guard hasReviewedBounds, startLead >= 0, endLead > startLead else { return false }
        let minimum = TimeInterval(minimumHours * 3_600)
        let maximum = TimeInterval(maximumHours * 3_600)
        // The planned window is half-open. A finish exactly on the upper
        // boundary still contains no interval from the next bucket.
        return startLead >= minimum && endLead <= maximum
    }
}

struct ForecastVerificationWindow: Decodable, Equatable, Sendable {
    let start: Date
    let end: Date
}

struct ForecastVerificationSource: Decodable, Equatable, Sendable {
    let sourceID: String
    let dataset: String
    let methodologyVersion: String
    let factClass: ForecastVerificationFactClass
}

struct LocalForecastErrorQualification: Equatable, Sendable {
    let mae: Double
    let unit: String
    let horizon: ForecastVerificationHorizon
    let verifiedSamples: Int
    let coverage: Double
    let verificationWindow: ForecastVerificationWindow
}

enum ForecastVerificationSelection {
    private static let reviewedNationalCarbonSource = "neso.carbon-intensity-national"
    private static let reviewedNationalCarbonForecastMethod =
        "50hz.neso-carbon-intensity.national-forecast.v1"
    private static let reviewedNationalCarbonOutturnMethod = "neso-national-carbon-v1"
    private static let unavailableSourceIssueTime = "source_does_not_publish_issue_time"
    private static let retrievalVintageTime = "retrieved_at"

    /// Returns a qualification only when the Local plan and the verification
    /// row can be joined by exact national source, methodology and reviewed
    /// issue-time semantics, and the complete planned window remains inside one
    /// reviewed horizon bucket.
    static func localCarbonQualification(
        response: ForecastVerificationResponse?,
        local: LocalWindowsResponse,
        window: LocalChargingWindow
    ) -> LocalForecastErrorQualification? {
        guard let response,
              local.hasSafeNationalForecastScope,
              local.plan.recommendedWindow == window,
              window.end > window.start,
              window.end.timeIntervalSince(window.start)
                == TimeInterval(local.plan.requestedDurationMinutes * 60),
              window.coverageFraction == 1,
              !window.sourceRecordIDs.isEmpty,
              Set(window.sourceRecordIDs).isSubset(of: Set(local.forecast.sourceRecordIDs)),
              let capturedAt = local.forecast.capturedAt,
              let localSourceID = local.forecast.sourceID,
              let localMethodology = local.forecast.methodologyVersion,
              localSourceID == reviewedNationalCarbonSource,
              localMethodology == reviewedNationalCarbonForecastMethod,
              local.forecast.issueTimeBasis == unavailableSourceIssueTime,
              local.forecast.captureTimeBasis == retrievalVintageTime else {
            return nil
        }

        guard let localIssueBasis = local.forecast.issueTimeBasis else { return nil }

        let startLead = window.start.timeIntervalSince(capturedAt)
        let endLead = window.end.timeIntervalSince(capturedAt)
        let candidates = ForecastVerificationHorizonID.allCases.compactMap { horizonID -> ForecastVerificationItem? in
            guard horizonID != .unknown,
                  let item = response.uniqueItem(
                    metric: .nationalCarbonIntensity,
                    horizon: horizonID
                  ),
                  item.isDisplayEligible(in: response),
                  item.horizon.contains(startLead: startLead, endLead: endLead),
                  item.forecast.sourceID == localSourceID,
                  item.forecast.methodologyVersion == localMethodology,
                  item.forecast.sourceID == reviewedNationalCarbonSource,
                  item.forecast.methodologyVersion == reviewedNationalCarbonForecastMethod,
                  item.outturn.sourceID == reviewedNationalCarbonSource,
                  item.outturn.methodologyVersion == reviewedNationalCarbonOutturnMethod,
                  item.outturn.factClass == .estimated,
                  item.issueTimeBasis == localIssueBasis,
                  item.issueTimeBasis == unavailableSourceIssueTime,
                  item.effectiveVintageTimeBasis == local.forecast.captureTimeBasis,
                  item.effectiveVintageTimeBasis == retrievalVintageTime else {
                return nil
            }
            return item
        }
        guard candidates.count == 1,
              let item = candidates.first,
              let mae = item.mae,
              let verificationWindow = item.verificationWindow else {
            return nil
        }
        return LocalForecastErrorQualification(
            mae: mae,
            unit: item.unit,
            horizon: item.horizon,
            verifiedSamples: item.verifiedSamples,
            coverage: item.coverage,
            verificationWindow: verificationWindow
        )
    }
}
