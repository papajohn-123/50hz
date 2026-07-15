import SwiftUI

enum GridPrimaryInsightKind: Equatable, Sendable {
    case reportedEvent
    case frequency
    case crossBorderFlow
    case carbon
    case visibleSupply
    case limitedData
}

enum GridPrimaryInsightAccent: Equatable, Sendable {
    case warning
    case live
    case forecast
    case neutral

    var color: Color {
        switch self {
        case .warning: GridTheme.warning
        case .live: GridTheme.liveCyan
        case .forecast: GridTheme.forecastViolet
        case .neutral: GridTheme.textSecondary
        }
    }
}

/// A single deterministic lead for the map-first home. The selector only uses
/// evidence already present in a `GridSnapshot`; it does not infer causes or
/// request generated text.
struct GridPrimaryInsight: Equatable, Sendable {
    let kind: GridPrimaryInsightKind
    let title: String
    let detail: String
    let accent: GridPrimaryInsightAccent
    let contextualQuestion: String

    static let usualFrequencyRange = 49.8...50.2
    static let materialCrossBorderFlowMW = 2_000.0

    static func make(snapshot: GridSnapshot) -> GridPrimaryInsight {
        let insight = select(snapshot: snapshot)
        guard !supportsCurrentLanguage(snapshot) else { return insight }
        return lastConfirmed(insight, at: snapshot.timestamp)
    }

    private static func select(snapshot: GridSnapshot) -> GridPrimaryInsight {
        if let event = materialAuthoritativeEvent(snapshot.activeEvent) {
            return GridPrimaryInsight(
                kind: .reportedEvent,
                title: oneLine("Reported: \(eventHeadline(event))", limit: 58),
                detail: oneLine("Publisher report · \(event.summary)", limit: 124),
                accent: .warning,
                contextualQuestion: "What has the publisher reported?"
            )
        }

        if let frequency = abnormalObservedFrequency(snapshot.frequency) {
            let position = frequency < usualFrequencyRange.lowerBound ? "below" : "above"
            return GridPrimaryInsight(
                kind: .frequency,
                title: "Frequency is \(position) its usual band",
                detail: "\(frequency.formatted(.number.precision(.fractionLength(2)))) Hz observed · this reading does not identify a cause.",
                accent: .warning,
                contextualQuestion: "What moves grid frequency?"
            )
        }

        if let flow = materialObservedFlow(snapshot) {
            let importing = flow.netMW > 0
            let magnitudeGW = abs(flow.netMW) / 1_000
            return GridPrimaryInsight(
                kind: .crossBorderFlow,
                title: "Visible connectors are net \(importing ? "importing" : "exporting")",
                detail: "\(magnitudeGW.formatted(.number.precision(.fractionLength(1)))) GW \(importing ? "into" : "out of") GB across \(flow.count) observed connector readings.",
                accent: .live,
                contextualQuestion: "Which connectors drive this flow?"
            )
        }

        if let carbon = carbonCondition(snapshot.carbonIntensity) {
            return carbon
        }

        if let leading = leadingVisibleSupply(snapshot.generation) {
            let isForecast = leading.factClass == .forecast
            let factLabel = factClassLabel(leading.factClass)
            let gigawatts = leading.megawatts / 1_000
            let share = leading.share.formatted(.percent.precision(.fractionLength(0)))
            return GridPrimaryInsight(
                kind: .visibleSupply,
                title: isForecast
                    ? "\(leading.fuel.displayName) leads the forecast view"
                    : "\(leading.fuel.displayName) leads this view",
                detail: "\(factLabel) \(gigawatts.formatted(.number.precision(.fractionLength(1)))) GW · \(share) of this partial transmission-visible view.",
                accent: isForecast ? .forecast : .live,
                contextualQuestion: "Why is \(leading.fuel.displayName.lowercased()) leading?"
            )
        }

        return GridPrimaryInsight(
            kind: .limitedData,
            title: "Grid view is limited",
            detail: "No comparable transmission-visible supply readings are available in this snapshot.",
            accent: .neutral,
            contextualQuestion: "Which grid data is available now?"
        )
    }

    /// `mixed` can still mean every required family is current; it only says
    /// those families were measured on independent source cadences. Delayed,
    /// stale, unavailable, and unknown summaries never inherit present-tense
    /// language, even when an older server reports the snapshot itself as live.
    private static func supportsCurrentLanguage(_ snapshot: GridSnapshot) -> Bool {
        guard snapshot.freshness == .live || snapshot.freshness == .critical else {
            return false
        }
        guard let summary = snapshot.freshnessSummary else {
            return true
        }
        guard summary.requiredFamilyCount > 0,
              summary.currentFamilyCount == summary.requiredFamilyCount,
              summary.delayedFamilyCount == 0,
              summary.staleFamilyCount == 0,
              summary.unavailableFamilyCount == 0 else {
            return false
        }
        return [.current, .mixed, .critical].contains(summary.state)
    }

    private static func lastConfirmed(
        _ insight: GridPrimaryInsight,
        at timestamp: Date
    ) -> GridPrimaryInsight {
        let time = timestamp.formatted(.dateTime.hour().minute())
        let title: String
        let detail: String
        let question: String

        switch insight.kind {
        case .reportedEvent:
            let report = insight.title.replacingOccurrences(of: "Reported: ", with: "")
            title = "Last confirmed report: \(report)"
            detail = insight.detail
            question = "What had the publisher reported by this snapshot?"
        case .frequency:
            title = insight.title.replacingOccurrences(
                of: "Frequency is ",
                with: "Last confirmed frequency was "
            )
            detail = insight.detail
            question = "What did this last confirmed frequency reading mean?"
        case .crossBorderFlow:
            title = insight.title.replacingOccurrences(
                of: "Visible connectors are ",
                with: "Last confirmed connectors were "
            )
            detail = insight.detail
            question = "Which connectors drove this last confirmed flow?"
        case .carbon:
            title = "Last confirmed \(insight.title.lowercased())"
            detail = insight.detail
            question = "What did this last confirmed carbon reading mean?"
        case .visibleSupply:
            let claim = insight.title.replacingOccurrences(of: " leads ", with: " led ")
            title = "Last confirmed supply: \(claim)"
            detail = insight.detail
            question = "What shaped this last confirmed supply view?"
        case .limitedData:
            title = "Last confirmed grid view was limited"
            detail = "No comparable transmission-visible supply readings were available in that snapshot."
            question = "Which data was available in this last confirmed snapshot?"
        }

        return GridPrimaryInsight(
            kind: insight.kind,
            title: title,
            detail: "Snapshot \(time) · \(detail)",
            accent: insight.accent,
            contextualQuestion: question
        )
    }

    private static func materialAuthoritativeEvent(_ event: GridEvent?) -> GridEvent? {
        guard let event, event.isAuthoritativelyReported else { return nil }
        let severity = event.severity.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        return ["important", "material", "critical"].contains(severity) ? event : nil
    }

    private static func eventHeadline(_ event: GridEvent) -> String {
        let identifier = event.assetName ?? event.assetID
        guard identifier.map(isTechnicalAssetIdentifier) == true else {
            return event.title
        }

        let subject = readableFuel(event.fuelType).map { "\($0) unit" } ?? "Generating unit"
        guard let unavailableMW = event.unavailableMW,
              unavailableMW.isFinite,
              unavailableMW > 0 else {
            return "\(subject) reported unavailable"
        }
        let decimals = unavailableMW.rounded() == unavailableMW ? 0 : 1
        let capacity = unavailableMW.formatted(
            .number.precision(.fractionLength(decimals))
        )
        return "\(subject) unavailable · \(capacity) MW"
    }

    private static func isTechnicalAssetIdentifier(_ value: String) -> Bool {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard normalized.count <= 40,
              normalized.contains("-"),
              normalized.contains(where: \.isLetter) else { return false }
        return !normalized.contains(where: \.isLowercase)
    }

    private static func readableFuel(_ value: String?) -> String? {
        guard let value else { return nil }
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return nil }
        let folded = normalized.lowercased()
        if folded.contains("gas") { return "Gas" }
        if folded.contains("wind") { return "Wind" }
        if folded.contains("nuclear") { return "Nuclear" }
        if folded.contains("biomass") { return "Biomass" }
        if folded.contains("hydro") { return "Hydro" }
        if folded.contains("coal") { return "Coal" }
        if folded.contains("storage") || folded.contains("battery") { return "Storage" }
        return normalized
    }

    private static func abnormalObservedFrequency(_ metric: GridMetric?) -> Double? {
        guard let metric,
              metric.factClass == .observed,
              metric.value.isFinite,
              !usualFrequencyRange.contains(metric.value) else { return nil }
        return metric.value
    }

    private static func materialObservedFlow(
        _ snapshot: GridSnapshot
    ) -> (netMW: Double, count: Int)? {
        guard !snapshot.interconnectors.isEmpty,
              snapshot.supply?.interconnectorDataAvailable != false,
              snapshot.interconnectors.allSatisfy({
                  $0.factClass == .observed && $0.megawatts.isFinite
              }) else { return nil }
        let netMW = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts }
        guard abs(netMW) >= materialCrossBorderFlowMW else { return nil }
        return (netMW, snapshot.interconnectors.count)
    }

    private static func carbonCondition(_ metric: GridMetric) -> GridPrimaryInsight? {
        guard metric.value.isFinite, metric.value >= 0 else { return nil }
        let band = CarbonIntensityWording.label(for: metric.value)
        guard band == "Lower carbon" || band == "Higher carbon" else { return nil }

        let isLower = band == "Lower carbon"
        let isForecast = metric.factClass == .forecast
        let descriptor: String
        switch metric.factClass {
        case .observed: descriptor = "Observed reading"
        case .estimated: descriptor = "GB estimate"
        case .derived: descriptor = "Derived GB reading"
        case .forecast: descriptor = "GB forecast"
        }
        let value = metric.value.formatted(.number.precision(.fractionLength(0)))
        return GridPrimaryInsight(
            kind: .carbon,
            title: "\(isLower ? "Lower" : "Higher")-carbon \(isForecast ? "forecast" : "period")",
            detail: "\(descriptor) · \(value) gCO₂/kWh\(isForecast ? " · not an observation" : "").",
            accent: isForecast ? .forecast : (isLower ? .live : .warning),
            contextualQuestion: isLower
                ? "How long might this lower-carbon period last?"
                : "When is the next lower-carbon window?"
        )
    }

    private static func leadingVisibleSupply(_ readings: [FuelReading]) -> FuelReading? {
        readings
            .filter {
                $0.megawatts.isFinite
                    && $0.megawatts >= 0
                    && $0.share.isFinite
                    && (0...1).contains($0.share)
            }
            .max { left, right in
                if left.megawatts == right.megawatts { return left.rank > right.rank }
                return left.megawatts < right.megawatts
            }
    }

    private static func factClassLabel(_ factClass: FactClass) -> String {
        switch factClass {
        case .observed: "Observed"
        case .estimated: "Estimated"
        case .derived: "Derived"
        case .forecast: "Forecast"
        }
    }

    private static func oneLine(_ value: String, limit: Int) -> String {
        let flattened = value
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
        guard flattened.count > limit else { return flattened }
        return String(flattened.prefix(max(limit - 1, 1))).trimmingCharacters(in: .whitespaces) + "…"
    }
}
