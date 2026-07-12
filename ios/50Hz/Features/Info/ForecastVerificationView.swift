import SwiftUI

@MainActor
final class ForecastVerificationViewModel: ObservableObject {
    @Published private(set) var response: ForecastVerificationResponse?
    @Published private(set) var isLoading = false
    @Published private(set) var isFromCache = false
    @Published private(set) var errorMessage: String?

    private let client: any InspectionDataProviding
    private var requestID = UUID()

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        self.client = client
    }

    func load() async {
        let currentRequest = UUID()
        requestID = currentRequest
        isLoading = true
        errorMessage = nil

        if response == nil, let cached = await client.cachedForecastVerification() {
            guard requestID == currentRequest, !Task.isCancelled else { return }
            response = cached
            isFromCache = true
        }

        do {
            let refreshed = try await client.forecastVerification()
            guard requestID == currentRequest, !Task.isCancelled else { return }
            response = refreshed
            isFromCache = false
            errorMessage = nil
        } catch {
            guard requestID == currentRequest,
                  !Task.isCancelled,
                  !Self.isCancellation(error) else { return }
            errorMessage = error.localizedDescription
        }
        if requestID == currentRequest { isLoading = false }
    }

    private static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let apiError = error as? GridAPIError, case .cancelled = apiError { return true }
        return false
    }
}

struct ForecastVerificationView: View {
    @StateObject private var viewModel: ForecastVerificationViewModel

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        _viewModel = StateObject(wrappedValue: ForecastVerificationViewModel(client: client))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                orientation
                content
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .navigationTitle("Forecast review")
        .navigationBarTitleDisplayMode(.inline)
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }

    private var orientation: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Typical recent error")
                .font(.system(.title2, design: .rounded, weight: .medium))
                .tracking(-0.5)
                .foregroundStyle(GridTheme.textPrimary)
            Text("National forecasts are paired with compatible outturns at exact timestamps. The review describes past error by forecast horizon; it is not a promise about one future interval and must not be applied to a region or postcode.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var content: some View {
        if let response = viewModel.response {
            VStack(alignment: .leading, spacing: 28) {
                if viewModel.isFromCache || viewModel.errorMessage != nil {
                    heldCopy
                }
                thresholdSection(response)
                metricSections(response)
                methodSection(response)
            }
        } else if viewModel.isLoading {
            HStack(spacing: 10) {
                ProgressView().tint(GridTheme.liveCyan)
                Text("Loading the national forecast review…")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, minHeight: 120, alignment: .leading)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Forecast review unavailable")
                    .font(.headline)
                Text(viewModel.errorMessage ?? "No saved review is available yet.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                Button("Try again") { Task { await viewModel.load() } }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(minHeight: 44)
            }
        }
    }

    private var heldCopy: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: "clock.arrow.circlepath")
                .foregroundStyle(GridTheme.staleAmber)
            Text(viewModel.errorMessage.map { "Showing the last saved review. \($0)" }
                 ?? "Showing the last saved review while a refresh completes.")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
        }
        .accessibilityElement(children: .combine)
    }

    private func thresholdSection(_ response: ForecastVerificationResponse) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            SectionLabel("Display gate", trailing: "NATIONAL")
            Text(ForecastVerificationPresentation.thresholdCopy(response))
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
            if let generatedAt = response.generatedAt {
                Text("Review generated \(ForecastVerificationPresentation.timestamp(generatedAt)).")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
    }

    @ViewBuilder
    private func metricSections(_ response: ForecastVerificationResponse) -> some View {
        let metrics = ForecastVerificationMetricID.allCases.filter { metric in
            metric != .unknown && response.results.contains(where: { $0.metric == metric })
        }
        if metrics.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                SectionLabel("Reviewed horizons")
                Text("No recognised national review rows have been published yet.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        } else {
            ForEach(metrics) { metric in
                VStack(alignment: .leading, spacing: 0) {
                    SectionLabel(
                        ForecastVerificationPresentation.metricLabel(metric),
                        trailing: ForecastVerificationPresentation.unitLabel(metric)
                    )
                    .padding(.bottom, 7)

                    ForEach(ForecastVerificationHorizonID.allCases.filter { $0 != .unknown }) { horizon in
                        if let item = response.uniqueItem(metric: metric, horizon: horizon) {
                            horizonRow(item, response: response)
                        }
                    }
                }
            }
        }
    }

    private func horizonRow(
        _ item: ForecastVerificationItem,
        response: ForecastVerificationResponse
    ) -> some View {
        let eligible = item.isDisplayEligible(in: response)
        return VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(ForecastVerificationPresentation.horizonLabel(item.horizon))
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                Spacer(minLength: 8)
                Text(eligible ? "REVIEWED" : "NOT SHOWN")
                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                    .tracking(0.6)
                    .foregroundStyle(eligible ? GridTheme.liveCyan : GridTheme.textTertiary)
            }

            if eligible, let mae = item.mae, let bias = item.bias {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .top, spacing: 22) {
                        statistic("MAE", ForecastVerificationPresentation.value(mae, unit: item.unit))
                        statistic("Bias", ForecastVerificationPresentation.signedValue(bias, unit: item.unit))
                        statistic("WAPE", ForecastVerificationPresentation.optionalPercent(item.wapePercent))
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        statistic("MAE", ForecastVerificationPresentation.value(mae, unit: item.unit))
                        statistic("Bias", ForecastVerificationPresentation.signedValue(bias, unit: item.unit))
                        statistic("WAPE", ForecastVerificationPresentation.optionalPercent(item.wapePercent))
                    }
                }

                Text("\(item.verifiedSamples.formatted()) reviewed pairs · \(ForecastVerificationPresentation.coverage(item.coverage)) coverage")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)

                if let window = item.verificationWindow {
                    Text("Verification window · \(ForecastVerificationPresentation.window(window))")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                Text("Forecast \(item.forecast.sourceID) / \(item.forecast.dataset) → outturn \(item.outturn.sourceID) / \(item.outturn.dataset)")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Method · \(item.verificationMethodologyVersion) · vintage \(item.issueTimeBasis) / \(item.effectiveVintageTimeBasis) · revision \(item.revision ?? 0)")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .textSelection(.enabled)
            } else {
                Text("Error statistics are withheld because this horizon is absent, ambiguous, or does not meet the published pair and coverage gates.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)
            }
        }
        .padding(.vertical, 15)
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
    }

    private func statistic(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.caption2.weight(.semibold))
                .tracking(0.7)
                .foregroundStyle(GridTheme.textTertiary)
            Text(value)
                .font(.subheadline.weight(.medium))
                .fontDesign(.monospaced)
                .monospacedDigit()
                .foregroundStyle(GridTheme.textPrimary)
                .contentTransition(.numericText())
        }
    }

    private func methodSection(_ response: ForecastVerificationResponse) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("How to read it")
            methodRow("MAE", "Mean absolute error: the typical absolute size of forecast minus outturn, in the source unit.")
            methodRow("Bias", "Mean signed error. Positive means forecasts ran above outturn; negative means below.")
            methodRow("WAPE", "Total absolute error divided by total absolute outturn. It can be unavailable when that denominator is not safe.")
            Text(response.methodology["pairing"] ?? "Only stored source vintages are paired with compatible exact-timestamp outturns; intervals are not filled or invented.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
                .lineSpacing(3)
            Text("Schema \(response.schemaVersion)")
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private func methodRow(_ label: String, _ copy: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.textPrimary)
            Text(copy)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
        }
    }
}

enum ForecastVerificationPresentation {
    private static let utc = TimeZone(secondsFromGMT: 0)!

    static func metricLabel(_ metric: ForecastVerificationMetricID) -> String {
        switch metric {
        case .nationalDemand: "National demand forecast"
        case .windGeneration: "Wind generation forecast"
        case .nationalCarbonIntensity: "National carbon-intensity forecast"
        case .unknown: "Unknown metric"
        }
    }

    static func unitLabel(_ metric: ForecastVerificationMetricID) -> String {
        switch metric {
        case .nationalDemand, .windGeneration: "MW"
        case .nationalCarbonIntensity: "gCO₂/kWh"
        case .unknown: "—"
        }
    }

    static func horizonLabel(_ horizon: ForecastVerificationHorizon) -> String {
        "\(horizon.minimumHours)–\(horizon.maximumHours) hours ahead"
    }

    static func compactHorizonLabel(_ horizon: ForecastVerificationHorizon) -> String {
        "\(horizon.minimumHours)–\(horizon.maximumHours)h horizon"
    }

    static func thresholdCopy(_ response: ForecastVerificationResponse) -> String {
        guard let samples = response.minimumVerifiedSamples,
              let coverage = response.minimumCoverage,
              samples >= 100,
              coverage >= 0.90 else {
            return "Error statistics remain withheld because the published review gate is incomplete or below 50Hz’s minimum evidence policy."
        }
        return "A horizon is shown only after at least \(samples.formatted()) compatible pairs and \(percent(coverage)) coverage of reviewed forecast vintages."
    }

    static func value(_ value: Double, unit: String) -> String {
        "\(number(value)) \(displayUnit(unit))"
    }

    static func signedValue(_ value: Double, unit: String) -> String {
        let sign = value > 0 ? "+" : ""
        return "\(sign)\(number(value)) \(displayUnit(unit))"
    }

    static func optionalPercent(_ value: Double?) -> String {
        value.map { "\(number($0))%" } ?? "Unavailable"
    }

    static func coverage(_ value: Double) -> String { percent(value) }

    static func window(_ window: ForecastVerificationWindow) -> String {
        let style = Date.FormatStyle(date: .abbreviated, time: .omitted, timeZone: utc)
        return "\(window.start.formatted(style))–\(window.end.formatted(style)) UTC"
    }

    static func timestamp(_ date: Date) -> String {
        date.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: utc)) + " UTC"
    }

    static func localQualificationCopy(_ qualification: LocalForecastErrorQualification) -> String {
        "MAE \(value(qualification.mae, unit: qualification.unit)) · \(compactHorizonLabel(qualification.horizon)) · \(qualification.verifiedSamples.formatted()) pairs · \(coverage(qualification.coverage)) coverage"
    }

    static func localQualificationWindow(_ qualification: LocalForecastErrorQualification) -> String {
        "National review window \(window(qualification.verificationWindow))"
    }

    private static func number(_ value: Double) -> String {
        value.formatted(.number.precision(.fractionLength(0...1)))
    }

    private static func percent(_ fraction: Double) -> String {
        (fraction * 100).formatted(.number.precision(.fractionLength(0...1))) + "%"
    }

    private static func displayUnit(_ unit: String) -> String {
        unit == "gCO2/kWh" ? "gCO₂/kWh" : unit
    }
}
