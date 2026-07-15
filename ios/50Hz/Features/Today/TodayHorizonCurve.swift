import SwiftUI

enum TodayHorizonPresentation {
    static let halfSpan: TimeInterval = 12 * 60 * 60

    static func samples(in timeline: GridTimeline) -> [GridTimelineSample] {
        let lower = timeline.nowBoundary.addingTimeInterval(-halfSpan)
        let upper = timeline.nowBoundary.addingTimeInterval(halfSpan)
        return timeline.samples.filter {
            $0.timestamp >= lower
                && $0.timestamp <= upper
                && $0.carbonIntensity.isFinite
        }
    }

    static func scopeLabel(for samples: [GridTimelineSample]) -> String {
        let hasObserved = samples.contains { !$0.isForecast }
        let hasForecast = samples.contains { $0.isForecast }
        switch (hasObserved, hasForecast) {
        case (true, true): return "24H · OBSERVED + FORECAST"
        case (true, false): return "24H · OBSERVED ONLY"
        case (false, true): return "24H · FORECAST ONLY"
        case (false, false): return "24H · UNAVAILABLE"
        }
    }

    static func rangeLabel(for samples: [GridTimelineSample]) -> String {
        guard let minimum = samples.map(\.carbonIntensity).min(),
              let maximum = samples.map(\.carbonIntensity).max() else {
            return "Carbon range unavailable"
        }
        return "\(Int(minimum.rounded()))–\(Int(maximum.rounded())) gCO₂/kWh"
    }
}

struct TodayHorizonCurve: View {
    let timeline: GridTimeline

    private var samples: [GridTimelineSample] {
        TodayHorizonPresentation.samples(in: timeline)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Carbon through the day", trailing: TodayHorizonPresentation.scopeLabel(for: samples))
            Text("The line changes colour at now; missing evidence is never bridged as a forecast.")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)

            if samples.count >= 2 {
                curve
                    .frame(height: 132)
                HStack(alignment: .firstTextBaseline) {
                    Text("−12 HR")
                    Spacer()
                    Text("NOW")
                    Spacer()
                    Text("+12 HR")
                }
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .tracking(0.5)
                .foregroundStyle(GridTheme.textTertiary)

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 16) { legend }
                    VStack(alignment: .leading, spacing: 6) { legend }
                }
                Text(TodayHorizonPresentation.rangeLabel(for: samples))
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            } else {
                TodayHorizonEmptyState()
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var curve: some View {
        GeometryReader { proxy in
            Canvas { context, size in
                let plot = CGRect(x: 1, y: 8, width: max(size.width - 2, 1), height: max(size.height - 16, 1))
                drawGrid(in: plot, context: &context)

                let observedPath = path(forForecast: false, in: plot)
                let forecastPath = path(forForecast: true, in: plot)

                context.stroke(
                    observedPath,
                    with: .color(GridTheme.liveCyan),
                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
                )
                context.stroke(
                    forecastPath,
                    with: .color(GridTheme.forecastViolet),
                    style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round)
                )

                var nowLine = Path()
                nowLine.move(to: CGPoint(x: plot.midX, y: plot.minY))
                nowLine.addLine(to: CGPoint(x: plot.midX, y: plot.maxY))
                context.stroke(
                    nowLine,
                    with: .color(GridTheme.textPrimary.opacity(0.32)),
                    style: StrokeStyle(lineWidth: 1, dash: [3, 4])
                )
            }
            .accessibilityHidden(true)
        }
        .background(GridTheme.surface.opacity(0.34), in: RoundedRectangle(cornerRadius: 10))
        .accessibilityLabel("Twenty-four hour carbon intensity curve")
        .accessibilityValue("\(TodayHorizonPresentation.scopeLabel(for: samples)), \(TodayHorizonPresentation.rangeLabel(for: samples))")
    }

    @ViewBuilder
    private var legend: some View {
        Label("Observed", systemImage: "circle.fill")
            .foregroundStyle(GridTheme.liveCyan)
        Label("Forecast", systemImage: "circle.fill")
            .foregroundStyle(GridTheme.forecastViolet)
    }

    private func drawGrid(in rect: CGRect, context: inout GraphicsContext) {
        for fraction in [0.25, 0.5, 0.75] {
            let y = rect.minY + rect.height * fraction
            var line = Path()
            line.move(to: CGPoint(x: rect.minX, y: y))
            line.addLine(to: CGPoint(x: rect.maxX, y: y))
            context.stroke(line, with: .color(GridTheme.hairline.opacity(0.6)), lineWidth: 0.5)
        }
    }

    private func path(forForecast forecast: Bool, in rect: CGRect) -> Path {
        var path = Path()
        var isDrawing = false
        for sample in samples {
            guard sample.isForecast == forecast else {
                isDrawing = false
                continue
            }
            let point = point(for: sample, in: rect)
            if isDrawing {
                path.addLine(to: point)
            } else {
                path.move(to: point)
                isDrawing = true
            }
        }
        return path
    }

    private func point(for sample: GridTimelineSample, in rect: CGRect) -> CGPoint {
        let lowerDate = timeline.nowBoundary.addingTimeInterval(-TodayHorizonPresentation.halfSpan)
        let fullSpan = TodayHorizonPresentation.halfSpan * 2
        let xRatio = min(max(sample.timestamp.timeIntervalSince(lowerDate) / fullSpan, 0), 1)
        let values = samples.map(\.carbonIntensity)
        let lowerValue = values.min() ?? 0
        let upperValue = values.max() ?? 1
        let valueSpan = max(upperValue - lowerValue, 1)
        let yRatio = min(max((sample.carbonIntensity - lowerValue) / valueSpan, 0), 1)
        return CGPoint(
            x: rect.minX + rect.width * xRatio,
            y: rect.maxY - rect.height * yRatio
        )
    }
}

private struct TodayHorizonEmptyState: View {
    var body: some View {
        Text("A 24-hour curve appears only when time-aligned carbon samples are available.")
            .font(.caption)
            .foregroundStyle(GridTheme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: 72, alignment: .leading)
            .overlay(alignment: .bottom) { Hairline() }
    }
}
