import SwiftUI

struct BrandHeader: View {
    let snapshot: GridSnapshot?
    let mode: String
    let onShare: (() -> Void)?
    let onStatusTap: (() -> Void)?

    init(
        snapshot: GridSnapshot?,
        mode: String,
        onShare: (() -> Void)? = nil,
        onStatusTap: (() -> Void)? = nil
    ) {
        self.snapshot = snapshot
        self.mode = mode
        self.onShare = onShare
        self.onStatusTap = onStatusTap
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("50Hz")
                .font(.system(.title2, design: .rounded, weight: .bold))
                .tracking(-0.8)
                .foregroundStyle(GridTheme.textPrimary)
                .accessibilityAddTraits(.isHeader)

            Spacer()

            GlobalInfoButton()

            if let onShare {
                Button(action: onShare) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Share this grid state")
            }

            StatusLabel(snapshot: snapshot, mode: mode, onTap: onStatusTap)
        }
    }
}

struct StatusLabel: View {
    let snapshot: GridSnapshot?
    let mode: String
    let onTap: (() -> Void)?

    private var summary: CurrentDataSummary? {
        snapshot.map {
            CurrentDataSummary.resolve(
                freshness: $0.freshness,
                fallbackAgeSeconds: $0.freshnessAgeSeconds,
                statuses: $0.dataStatus,
                evaluatedAt: Date()
            )
        }
    }

    private var color: Color {
        guard snapshot != nil else { return GridTheme.textTertiary }
        if mode == "FORECAST" { return GridTheme.forecastViolet }
        if mode == "REPLAY" { return GridTheme.liveCyan.opacity(0.75) }
        return switch summary?.state {
        case .current: GridTheme.liveCyan
        case .delayed, .offline: GridTheme.staleAmber
        case nil: GridTheme.textTertiary
        }
    }

    private var label: String {
        guard let summary else { return "Connecting" }
        if mode == "FORECAST" { return "Forecast frame" }
        if mode == "REPLAY" { return "Replay frame" }
        return "\(summary.state.displayName) · observed \(compactAge(summary.observationAgeSeconds))"
    }

    private var spokenLabel: String {
        guard let summary else { return "Data status, connecting" }
        if mode == "FORECAST" { return "Data status, forecast frame" }
        if mode == "REPLAY" { return "Data status, observed replay frame" }
        return "Data status, \(summary.state.displayName.lowercased()). Observed age is \(spokenAge(summary.observationAgeSeconds)) old."
    }

    var body: some View {
        if let onTap {
            Button(action: onTap) { content }
                .buttonStyle(.plain)
                .accessibilityLabel(spokenLabel)
                .accessibilityHint("Opens data details")
        } else {
            content
                .accessibilityElement(children: .combine)
                .accessibilityLabel(spokenLabel)
        }
    }

    private var content: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
                .shadow(color: color.opacity(0.65), radius: 4)
            Text(label)
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.3)
                .lineLimit(1)
                .minimumScaleFactor(0.72)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 9)
        .frame(minHeight: 44)
        .background(GridTheme.surface.opacity(0.72), in: Capsule())
        .overlay(Capsule().stroke(color.opacity(0.18), lineWidth: 1))
        .contentShape(Capsule())
    }

    private func compactAge(_ seconds: Int) -> String {
        if seconds < 60 { return "<1m" }
        if seconds < 3_600 { return "\(seconds / 60)m" }
        return "\(seconds / 3_600)h"
    }

    private func spokenAge(_ seconds: Int) -> String {
        if seconds < 60 { return "less than one minute" }
        if seconds < 3_600 {
            let minutes = seconds / 60
            return "\(minutes) minute\(minutes == 1 ? "" : "s")"
        }
        let hours = seconds / 3_600
        return "\(hours) hour\(hours == 1 ? "" : "s")"
    }
}

struct ConditionHeadlineView: View {
    let headline: ConditionHeadline
    let isForecast: Bool
    let interpretation: String

    init(headline: ConditionHeadline, isForecast: Bool, interpretation: String? = nil) {
        self.headline = headline
        self.isForecast = isForecast
        self.interpretation = interpretation ?? headline.interpretation
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 7) {
                    Text(headline.cleanliness)
                    Text("·").foregroundStyle(GridTheme.textTertiary)
                    Text(headline.balance)
                    Text("·").foregroundStyle(GridTheme.textTertiary)
                    Text(headline.energyPosition)
                        .foregroundStyle(isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan)
                }
                VStack(alignment: .leading, spacing: 5) {
                    Text(headline.cleanliness)
                    Text(headline.balance)
                    Text(headline.energyPosition)
                        .foregroundStyle(isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan)
                }
            }
            .font(.system(.title2, design: .rounded, weight: .medium))
            .tracking(-0.7)
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Grid condition: \(headline.cleanliness), \(headline.balance), \(headline.energyPosition)")

            Text(interpretation)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
                .fixedSize(horizontal: false, vertical: true)
        }
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
                MeasurementCell(label: "Frequency", value: "—", unit: isForecast ? "not forecast" : "unavailable", state: heldLabel)
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
        .accessibilityLabel("Supply mix")
        .accessibilityValue(readings.prefix(4).map { "\($0.fuel.displayName) \(Int($0.share * 100)) percent" }.joined(separator: ", "))
    }
}

struct FuelFilter: View {
    let readings: [FuelReading]
    @Binding var selection: FuelKind?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ScrollView(.horizontal) {
            HStack(spacing: 8) {
                chip(title: "All", accessibilityName: "All supply sources", color: GridTheme.liveCyan, isSelected: selection == nil) {
                    updateSelection(nil)
                }
                ForEach(readings) { reading in
                    chip(
                        title: reading.fuel.shortName,
                        accessibilityName: reading.fuel == .imports ? "Imported power" : "\(reading.fuel.displayName) supply",
                        color: GridTheme.fuel(reading.fuel),
                        isSelected: selection == reading.fuel
                    ) {
                        updateSelection(selection == reading.fuel ? nil : reading.fuel)
                    }
                }
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
        }
        .scrollIndicators(.hidden)
        .contentMargins(.horizontal, -GridTheme.horizontalPadding, for: .scrollContent)
    }

    private func chip(
        title: String,
        accessibilityName: String,
        color: Color,
        isSelected: Bool,
        action: @escaping () -> Void
    ) -> some View {
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
        .accessibilityLabel(accessibilityName)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private func updateSelection(_ newSelection: FuelKind?) {
        if reduceMotion {
            selection = newSelection
        } else {
            withAnimation(.snappy(duration: 0.28)) { selection = newSelection }
        }
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

            Text("\(Int(reading.share * 100))% of supply mix · \(reading.factClass.rawValue.capitalized)")
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
