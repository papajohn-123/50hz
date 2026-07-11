import SwiftUI

struct BrandHeader: View {
    let snapshot: GridSnapshot?
    let mode: String
    let onShare: (() -> Void)?

    init(snapshot: GridSnapshot?, mode: String, onShare: (() -> Void)? = nil) {
        self.snapshot = snapshot
        self.mode = mode
        self.onShare = onShare
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("50Hz")
                .font(.system(.title2, design: .rounded, weight: .bold))
                .tracking(-0.8)
                .foregroundStyle(GridTheme.textPrimary)
                .accessibilityAddTraits(.isHeader)

            Spacer()

            if let onShare {
                Button(action: onShare) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(width: 38, height: 38)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Share this grid state")
            }

            StatusLabel(snapshot: snapshot, mode: mode)
        }
    }
}

struct StatusLabel: View {
    let snapshot: GridSnapshot?
    let mode: String

    private var color: Color {
        guard let snapshot else { return GridTheme.textTertiary }
        if mode == "FORECAST" { return GridTheme.forecastViolet }
        if mode == "REPLAY" { return GridTheme.liveCyan.opacity(0.75) }
        return switch snapshot.freshness {
        case .live: GridTheme.liveCyan
        case .stale, .offline: GridTheme.staleAmber
        case .critical: GridTheme.warning
        }
    }

    private var label: String {
        guard let snapshot else { return "CONNECTING" }
        if mode != "LIVE" { return mode }
        return switch snapshot.freshness {
        case .live: "LIVE · \(max(snapshot.freshnessAgeSeconds / 60, 1))m"
        case .stale: "STALE · \(max(snapshot.freshnessAgeSeconds / 60, 1))m"
        case .offline: "OFFLINE"
        case .critical: "REPORTED EVENT"
        }
    }

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
                .shadow(color: color.opacity(0.65), radius: 4)
            Text(label)
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.8)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 10)
        .frame(minHeight: 32)
        .background(GridTheme.surface.opacity(0.72), in: Capsule())
        .overlay(Capsule().stroke(color.opacity(0.18), lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Data status, \(label)")
    }
}

struct ConditionHeadlineView: View {
    let headline: ConditionHeadline
    let isForecast: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 7) { headlineParts }
                VStack(alignment: .leading, spacing: 2) { headlineParts }
            }
            .font(.system(.title2, design: .rounded, weight: .medium))
            .tracking(-0.7)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Grid condition: \(headline.cleanliness), \(headline.balance), \(headline.energyPosition)")

            Text(headline.interpretation)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var headlineParts: some View {
        Text(headline.cleanliness)
        Text("·").foregroundStyle(GridTheme.textTertiary)
        Text(headline.balance)
        Text("·").foregroundStyle(GridTheme.textTertiary)
        Text(headline.energyPosition)
            .foregroundStyle(isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan)
    }
}

struct MeasurementRow: View {
    let snapshot: GridSnapshot
    let isForecast: Bool

    var body: some View {
        let heldLabel = [.stale, .offline].contains(snapshot.freshness) ? "HELD" : nil
        HStack(alignment: .top, spacing: 0) {
            if let frequency = snapshot.frequency, !isForecast {
                MeasurementCell(label: "Frequency", value: frequency.formatted(decimals: 2), unit: frequency.unit, state: heldLabel)
            } else {
                MeasurementCell(label: isForecast ? "Position" : "Frequency", value: isForecast ? "Wind-led" : "—", unit: isForecast ? "FCST" : "unavailable", state: heldLabel)
            }
            MeasurementCell(label: "Demand", value: (snapshot.demand.value / 1_000).formatted(.number.precision(.fractionLength(1))), unit: "GW", state: heldLabel)
            MeasurementCell(label: "Carbon", value: snapshot.carbonIntensity.formatted(), unit: "g/kWh", state: heldLabel)
        }
        .accessibilityElement(children: .contain)
    }
}

private struct MeasurementCell: View {
    let label: String
    let value: String
    let unit: String
    let state: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label.uppercased() + state.map { " · \($0)" }.orEmpty)
                .font(.caption2.weight(.medium))
                .tracking(0.7)
                .foregroundStyle(GridTheme.textTertiary)
            HStack(alignment: .firstTextBaseline, spacing: 4) {
                Text(value)
                    .font(.system(.title3, design: .monospaced, weight: .medium))
                    .foregroundStyle(GridTheme.textPrimary)
                    .contentTransition(.numericText())
                Text(unit)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value) \(unit)")
    }
}

private extension Optional where Wrapped == String {
    var orEmpty: String { self ?? "" }
}

struct GenerationMixBar: View {
    let readings: [FuelReading]
    let selectedFuel: FuelKind?

    var body: some View {
        GeometryReader { proxy in
            let total = max(readings.reduce(0) { $0 + max($1.megawatts, 0) }, 1)
            HStack(spacing: 1) {
                ForEach(readings) { reading in
                    Rectangle()
                        .fill(GridTheme.fuel(reading.fuel))
                        .opacity(selectedFuel == nil || selectedFuel == reading.fuel ? 1 : 0.18)
                        .frame(width: max((reading.megawatts / total) * proxy.size.width, 1))
                        .shadow(
                            color: selectedFuel == reading.fuel ? GridTheme.fuel(reading.fuel).opacity(0.6) : .clear,
                            radius: 5
                        )
                }
            }
            .clipShape(Capsule())
        }
        .frame(height: 5)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Generation mix")
        .accessibilityValue(readings.prefix(4).map { "\($0.fuel.displayName) \(Int($0.share * 100)) percent" }.joined(separator: ", "))
    }
}

struct FuelFilter: View {
    let readings: [FuelReading]
    @Binding var selection: FuelKind?

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                chip(title: "All", color: GridTheme.liveCyan, isSelected: selection == nil) {
                    withAnimation(.snappy(duration: 0.28)) { selection = nil }
                }
                ForEach(readings) { reading in
                    chip(
                        title: reading.fuel.shortName,
                        color: GridTheme.fuel(reading.fuel),
                        isSelected: selection == reading.fuel
                    ) {
                        withAnimation(.snappy(duration: 0.28)) {
                            selection = selection == reading.fuel ? nil : reading.fuel
                        }
                    }
                }
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
        }
        .scrollIndicators(.hidden)
        .contentMargins(.horizontal, -GridTheme.horizontalPadding, for: .scrollContent)
    }

    private func chip(title: String, color: Color, isSelected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Circle().fill(color).frame(width: 5, height: 5)
                Text(title)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(isSelected ? GridTheme.textPrimary : GridTheme.textSecondary)
            }
            .padding(.horizontal, 12)
            .frame(minHeight: 44)
            .background(isSelected ? color.opacity(0.13) : GridTheme.surface.opacity(0.7), in: Capsule())
            .overlay(Capsule().stroke(isSelected ? color.opacity(0.42) : GridTheme.hairline, lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(title) generation")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

struct FuelFocusView: View {
    let reading: FuelReading
    let timeline: GridTimeline

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(reading.fuel.displayName)
                    .font(.headline)
                Spacer()
                Text("#\(reading.rank) today")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
            }

            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text((reading.megawatts / 1_000).formatted(.number.precision(.fractionLength(1))))
                    .font(.system(.title, design: .monospaced, weight: .medium))
                Text("GW")
                    .foregroundStyle(GridTheme.textSecondary)
                Text("\(reading.changeOneHour >= 0 ? "+" : "")\(Int(reading.changeOneHour)) MW in 1h")
                    .font(.caption)
                    .foregroundStyle(reading.changeOneHour >= 0 ? GridTheme.liveCyan : GridTheme.textSecondary)
                    .padding(.leading, 6)
            }

            FuelSparkline(fuel: reading.fuel, timeline: timeline)
                .frame(height: 54)

            Text("\(Int(reading.share * 100))% of generation · \(reading.factClass.rawValue.capitalized)")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(16)
        .background(GridTheme.surface.opacity(0.82), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: GridTheme.cornerRadius)
                .stroke(GridTheme.fuel(reading.fuel).opacity(0.2), lineWidth: 1)
        )
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }
}

private struct FuelSparkline: View {
    let fuel: FuelKind
    let timeline: GridTimeline

    var body: some View {
        Canvas { context, size in
            let points = timeline.samples.compactMap { sample -> Double? in
                sample.generation.first(where: { $0.fuel == fuel })?.megawatts
            }
            guard points.count > 1, let minValue = points.min(), let maxValue = points.max() else { return }
            let range = max(maxValue - minValue, 1)
            var path = Path()
            for (index, value) in points.enumerated() {
                let x = CGFloat(index) / CGFloat(points.count - 1) * size.width
                let y = size.height - CGFloat((value - minValue) / range) * (size.height - 8) - 4
                if index == 0 { path.move(to: CGPoint(x: x, y: y)) }
                else { path.addLine(to: CGPoint(x: x, y: y)) }
            }
            context.stroke(path, with: .color(GridTheme.fuel(fuel)), style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
        }
        .accessibilityHidden(true)
    }
}

struct SectionLabel: View {
    let title: String
    let trailing: String?

    init(_ title: String, trailing: String? = nil) {
        self.title = title
        self.trailing = trailing
    }

    var body: some View {
        HStack {
            Text(title.uppercased())
                .font(.caption2.weight(.semibold))
                .tracking(1)
                .foregroundStyle(GridTheme.textTertiary)
            Spacer()
            if let trailing {
                Text(trailing)
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
    }
}
