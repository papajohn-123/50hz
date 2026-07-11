import SwiftUI

struct MineView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("mine.postcode") private var postcode = ""
    @State private var draftPostcode = ""
    @FocusState private var postcodeFocused: Bool

    private var nationalCarbon: Double {
        model.snapshot?.carbonIntensity.value ?? 172
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                header
                regionalReading
                comparison
                cleanWindow
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
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("Mine")
                .font(.system(.largeTitle, design: .rounded, weight: .medium))
                .tracking(-1.2)
                .accessibilityAddTraits(.isHeader)
            HStack(spacing: 7) {
                Image(systemName: "location.circle.fill")
                    .foregroundStyle(GridTheme.liveCyan)
                Text(postcode.isEmpty ? "Central London · default region" : "London · \(postcode.uppercased())")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Text("No location permission requested")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var regionalReading: some View {
        VStack(alignment: .leading, spacing: 7) {
            SectionLabel("Regional carbon", trailing: "ESTIMATED")
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("138")
                    .font(.system(size: 54, weight: .light, design: .rounded))
                    .tracking(-2)
                    .foregroundStyle(GridTheme.liveCyan)
                    .monospacedDigit()
                Text("gCO₂/kWh")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Text("Low carbon intensity for this time of day")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textPrimary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Estimated regional carbon intensity, 138 grams of carbon dioxide per kilowatt hour, low")
    }

    private var comparison: some View {
        VStack(alignment: .leading, spacing: 13) {
            SectionLabel("Against Britain")
            comparisonBar(label: "Central London", value: 138, maximum: 260, color: GridTheme.liveCyan)
            comparisonBar(label: "Great Britain", value: nationalCarbon, maximum: 260, color: GridTheme.textSecondary)
            Text("London is approximately \(max(Int((1 - 138 / max(nationalCarbon, 1)) * 100), 0))% cleaner than the national snapshot.")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
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

    private var cleanWindow: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 5) {
                    SectionLabel("Best charging window")
                    Text("01:30–04:00")
                        .font(.system(.title, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.forecastViolet)
                    Text("Forecast carbon bottoms near 94 g/kWh")
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
                chargingFact(value: "7.0 kWh", label: "Charge")
                chargingFact(value: "0.7 kg", label: "Estimated CO₂")
                chargingFact(value: "35%", label: "Cleaner than now")
            }
            Text("Assumes 7 kWh delivered at 90% charging efficiency. Compares with this region’s current estimate.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(16)
        .background(GridTheme.forecastViolet.opacity(0.075), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.18), lineWidth: 1))
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
            Text("Stored only on this device. This fixture keeps the London region until the live postcode endpoint is connected.")
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
            Text("Regional values are modelled estimates from NESO Carbon Intensity data. National generation shown elsewhere comes from Elexon and is not a regional mix.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }
}
