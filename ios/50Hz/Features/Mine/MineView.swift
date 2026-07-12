import SwiftUI

enum RegionalGridCopy {
    static func methodology(source: SourceReference?) -> String {
        let provenance = source.map {
            " Source: \($0.name), \($0.dataset), forecast period \($0.observedAt.formatted(.dateTime.hour().minute())), captured \($0.retrievedAt.formatted(.dateTime.hour().minute()))."
        } ?? ""
        return "The current half-hour carbon value is a regional forecast. The charging window uses Britain’s national carbon forecast, and the national supply mix shown elsewhere is not a regional electricity supply mix.\(provenance)"
    }
}

struct MineView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("mine.postcode") private var postcode = ""
    @State private var draftPostcode = ""
    @FocusState private var postcodeFocused: Bool

    private var nationalCarbon: Double {
        model.regionalContext?.nationalCarbonIntensity ?? model.snapshot?.carbonIntensity.value ?? 0
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                header
                if let error = model.regionError {
                    RegionalStateBanner(message: error, hasCachedValue: model.regionalContext != nil) {
                        Task { await model.loadRegion(postcode: postcode) }
                    }
                }
                if let context = model.regionalContext {
                    regionalReading(context)
                    comparison(context)
                    cleanWindow(context)
                } else {
                    regionalPlaceholder
                }
                postcodeControl
                methodology
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 30)
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollIndicators(.hidden)
        .gridPageBackground()
        .onAppear { draftPostcode = postcode }
        .task(id: postcode) {
            await model.loadRegion(postcode: postcode)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Local")
                .font(.system(.largeTitle, design: .rounded, weight: .medium))
                .tracking(-1.2)
                .accessibilityAddTraits(.isHeader)
            HStack(spacing: 7) {
                Image(systemName: "location.circle.fill")
                    .foregroundStyle(GridTheme.liveCyan)
                Text(model.regionalContext.map { "\($0.name) · \($0.postcode)" }
                    ?? (postcode.isEmpty ? "Central London · default region" : postcode.uppercased()))
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Text("No location permission requested")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private func regionalReading(_ context: RegionalGridContext) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            SectionLabel(
                "Regional forecast now",
                trailing: context.regionalIsDelayed == true ? "\(context.name.uppercased()) · DELAYED" : context.name.uppercased()
            )
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(Int(context.carbonIntensity.rounded()).formatted())
                    .font(.system(size: 54, weight: .light, design: .rounded))
                    .tracking(-2)
                    .foregroundStyle(GridTheme.liveCyan)
                    .monospacedDigit()
                Text("gCO₂/kWh")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Text(context.rating)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textPrimary)
            if context.regionalIsDelayed == true, let periodEnd = context.regionalPeriodEnd {
                Text("Latest available regional period ended \(periodEnd.formatted(.dateTime.hour().minute())).")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Regional carbon forecast for the current half-hour, \(Int(context.carbonIntensity.rounded())) grams of carbon dioxide per kilowatt hour, \(context.rating)")
    }

    private func comparison(_ context: RegionalGridContext) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            SectionLabel("Against Britain")
            comparisonBar(label: context.name, value: context.carbonIntensity, maximum: comparisonMaximum(context), color: GridTheme.liveCyan)
            comparisonBar(label: "Great Britain", value: nationalCarbon, maximum: comparisonMaximum(context), color: GridTheme.textSecondary)
            Text(comparisonCopy(context))
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private func comparisonMaximum(_ context: RegionalGridContext) -> Double {
        max(context.carbonIntensity, context.nationalCarbonIntensity, 100) * 1.2
    }

    private func comparisonCopy(_ context: RegionalGridContext) -> String {
        let national = max(context.nationalCarbonIntensity, 1)
        let difference = Int((abs(context.carbonIntensity - national) / national * 100).rounded())
        if context.carbonIntensity < national {
            return "\(context.name) is approximately \(difference)% lower carbon than the national forecast for this half-hour."
        }
        if context.carbonIntensity > national {
            return "\(context.name) is approximately \(difference)% more carbon-intensive than the national forecast for this half-hour."
        }
        return "The regional and national forecasts are aligned for this half-hour."
    }

    private func comparisonBar(label: String, value: Double, maximum: Double, color: Color) -> some View {
        VStack(spacing: 6) {
            HStack {
                Text(label).font(.caption)
                Spacer()
                Text("\(Int(value)) g/kWh").font(.caption).fontDesign(.monospaced)
            }
            GeometryReader { proxy in
                Capsule().fill(GridTheme.surfaceRaised)
                Capsule().fill(color).frame(width: proxy.size.width * min(value / maximum, 1))
            }
            .frame(height: 5)
        }
    }

    private func cleanWindow(_ context: RegionalGridContext) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    SectionLabel("Best GB window", trailing: "NATIONAL FORECAST")
                    Text(windowLabel(start: context.chargingWindowStart, end: context.chargingWindowEnd))
                        .font(.system(.title, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.forecastViolet)
                    Text("Lowest continuous charging period in the national forecast")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                Spacer()
                Image(systemName: "bolt.circle.fill")
                    .font(.system(size: 32, weight: .light))
                    .foregroundStyle(GridTheme.forecastViolet)
            }
            Hairline()
            HStack(spacing: 0) {
                chargingFact(value: durationLabel(start: context.chargingWindowStart, end: context.chargingWindowEnd), label: "Window")
                chargingFact(value: context.chargingWindowStart.formatted(.dateTime.weekday(.abbreviated)), label: "Day")
                chargingFact(value: context.forecastIssuedAt.formatted(.dateTime.hour().minute()), label: "Captured")
            }
            Text("This window comes from Britain’s national forecast; the value above is the regional forecast for the current half-hour. 50Hz does not claim an emissions saving until the API supplies the window’s average intensity.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(16)
        .background(GridTheme.forecastViolet.opacity(0.075), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.18), lineWidth: 1))
    }

    private func windowLabel(start: Date, end: Date) -> String {
        "\(start.formatted(.dateTime.hour().minute()))–\(end.formatted(.dateTime.hour().minute()))"
    }

    private func durationLabel(start: Date, end: Date) -> String {
        let hours = max(end.timeIntervalSince(start) / 3_600, 0)
        return "\(hours.formatted(.number.precision(.fractionLength(hours.rounded() == hours ? 0 : 1)))) hr"
    }

    private func chargingFact(value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value).font(.subheadline.weight(.medium)).fontDesign(.monospaced)
            Text(label).font(.caption2).foregroundStyle(GridTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var postcodeControl: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Your region")
            HStack(spacing: 9) {
                TextField("Enter postcode", text: $draftPostcode)
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .focused($postcodeFocused)
                    .submitLabel(.done)
                    .onSubmit(savePostcode)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 46)
                    .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 11))
                    .overlay(RoundedRectangle(cornerRadius: 11).stroke(GridTheme.hairline, lineWidth: 1))
                Button("Use") { savePostcode() }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.background)
                    .padding(.horizontal, 16)
                    .frame(minHeight: 46)
                    .background(GridTheme.liveCyan, in: RoundedRectangle(cornerRadius: 11))
            }
            Text("Stored only on this device. 50Hz sends only the outward code to its backend to resolve a NESO carbon-intensity region; location permission is not used.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private func savePostcode() {
        postcode = draftPostcode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        postcodeFocused = false
    }

    private var methodology: some View {
        VStack(alignment: .leading, spacing: 8) {
            Hairline()
            Text(methodologyCopy)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var methodologyCopy: String {
        RegionalGridCopy.methodology(source: model.regionalContext?.source)
    }

    private var regionalPlaceholder: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Regional forecast now")
            if case .failed(let message) = model.regionLoadPhase {
                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            } else {
                ProgressView("Resolving the regional forecast…")
                    .tint(GridTheme.liveCyan)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 130, alignment: .leading)
    }
}

private struct RegionalStateBanner: View {
    let message: String
    let hasCachedValue: Bool
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "location.slash")
                .foregroundStyle(GridTheme.staleAmber)
            VStack(alignment: .leading, spacing: 3) {
                Text(hasCachedValue ? "Showing the last regional forecast" : "Regional data unavailable")
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Spacer(minLength: 4)
            Button("Retry", action: retry)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.staleAmber)
                .frame(minHeight: 44)
        }
        .padding(12)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }
}
