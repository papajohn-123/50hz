import SwiftUI
import UIKit

struct GridTimelineScale: Equatable, Sendable {
    let firstDate: Date
    let lastDate: Date

    var span: TimeInterval { max(lastDate.timeIntervalSince(firstDate), 1) }

    func ratio(for date: Date) -> Double {
        let rawValue = date.timeIntervalSince(firstDate) / span
        guard rawValue.isFinite else { return 0 }
        return min(max(rawValue, 0), 1)
    }

    func date(at ratio: Double) -> Date {
        let finiteRatio = ratio.isFinite ? ratio : 0
        return firstDate.addingTimeInterval(span * min(max(finiteRatio, 0), 1))
    }
}

struct GridTimelineView: View {
    let timeline: GridTimeline
    @Binding var selectedTime: Date?
    @State private var feedbackTick = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var firstDate: Date { timeline.samples.first?.timestamp ?? timeline.nowBoundary }
    private var lastDate: Date { timeline.samples.last?.timestamp ?? timeline.nowBoundary }
    private var selectedDate: Date { selectedTime ?? timeline.nowBoundary }
    private var scale: GridTimelineScale { GridTimelineScale(firstDate: firstDate, lastDate: lastDate) }
    private var nowRatio: CGFloat { CGFloat(scale.ratio(for: timeline.nowBoundary)) }
    private var selectedRatio: CGFloat { CGFloat(scale.ratio(for: selectedDate)) }
    private var isForecast: Bool { selectedDate > timeline.nowBoundary }
    private var isLive: Bool { selectedTime == nil }
    private var hasForecastRange: Bool {
        lastDate > timeline.nowBoundary.addingTimeInterval(TimeInterval(timeline.sourceResolutionSeconds / 2))
    }

    var body: some View {
        VStack(spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(isLive ? "NOW" : selectedDateLabel)
                    .font(.system(.caption, design: .monospaced, weight: .semibold))
                    .foregroundStyle(isForecast ? GridTheme.forecastViolet : GridTheme.textPrimary)

                Spacer()

                if !isLive {
                    Button {
                        resumeLive()
                    } label: {
                        Label("Live", systemImage: "dot.radiowaves.left.and.right")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(minHeight: 44)
                    }
                    .buttonStyle(.plain)
                } else {
                    Text(isForecast ? "FORECAST" : "LIVE")
                        .font(.caption2)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
            }

            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    Capsule()
                        .fill(GridTheme.surfaceRaised)
                        .frame(height: 8)

                    Capsule()
                        .fill(GridTheme.liveCyan.opacity(0.58))
                        .frame(width: proxy.size.width * nowRatio, height: 8)

                    ForecastHatch()
                        .foregroundStyle(GridTheme.forecastViolet.opacity(0.68))
                        .frame(width: proxy.size.width * (1 - nowRatio), height: 8)
                        .clipShape(Capsule())
                        .offset(x: proxy.size.width * nowRatio)

                    Rectangle()
                        .fill(GridTheme.textPrimary.opacity(0.55))
                        .frame(width: 1, height: 18)
                        .offset(x: proxy.size.width * nowRatio)

                    Circle()
                        .fill(isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan)
                        .frame(width: 14, height: 14)
                        .shadow(color: (isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan).opacity(0.65), radius: 6)
                        .offset(x: proxy.size.width * selectedRatio - 7)
                        .animation(
                            reduceMotion ? nil : .interactiveSpring(response: 0.22, dampingFraction: 0.88),
                            value: selectedRatio
                        )
                }
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let ratio = (value.location.x / max(proxy.size.width, 1)).clamped(to: 0...1)
                            selectedTime = scale.date(at: Double(ratio))
                            let newTick = Int(ratio * 48)
                            if newTick != feedbackTick { feedbackTick = newTick }
                        }
                )
                .accessibilityRepresentation {
                    GridTimelineAccessibilityControl(
                        value: timelineAccessibilityValue,
                        onIncrement: { adjustTimeline(by: 1) },
                        onDecrement: { adjustTimeline(by: -1) }
                    )
                }
            }
            .frame(height: 34)
            .sensoryFeedback(.selection, trigger: feedbackTick)

            HStack {
                Text(axisLabel(for: firstDate))
                Spacer()
                if hasForecastRange {
                    Text("NOW")
                    Spacer()
                    Text(axisLabel(for: lastDate))
                } else {
                    Text("NOW")
                }
            }
            .font(.system(size: 8, weight: .medium, design: .monospaced))
            .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.top, 4)
        .padding(.bottom, 8)
        .background(.ultraThinMaterial.opacity(0.88))
        .overlay(alignment: .top) { Hairline() }
    }

    private var selectedDateLabel: String {
        if Calendar.autoupdatingCurrent.isDate(selectedDate, inSameDayAs: timeline.nowBoundary) {
            return selectedDate.formatted(.dateTime.hour().minute())
        }
        return selectedDate.formatted(.dateTime.weekday(.abbreviated).hour().minute())
    }

    private var accessibilityStep: Double {
        min(max(Double(timeline.sourceResolutionSeconds) / scale.span, 0.000_001), 1)
    }

    private var timelineAccessibilityValue: String {
        if isLive {
            return "Live now, \(axisLabel(for: timeline.nowBoundary))"
        }
        return "\(isForecast ? "Forecast frame" : "Observed replay"), \(selectedDateLabel)"
    }

    private func adjustTimeline(by stepCount: Double) {
        let currentPosition = scale.ratio(for: selectedDate)
        let targetDate = scale.date(at: currentPosition + accessibilityStep * stepCount)
        guard targetDate != selectedDate else { return }
        selectedTime = targetDate
    }

    private func resumeLive() {
        if reduceMotion {
            selectedTime = nil
        } else {
            withAnimation(.snappy(duration: 0.28)) { selectedTime = nil }
        }
    }

    private func axisLabel(for date: Date) -> String {
        if Calendar.autoupdatingCurrent.isDate(date, inSameDayAs: timeline.nowBoundary) {
            return date.formatted(.dateTime.hour().minute())
        }
        return date.formatted(.dateTime.weekday(.abbreviated).hour().minute())
    }
}

private struct GridTimelineAccessibilityControl: UIViewRepresentable {
    let value: String
    let onIncrement: () -> Void
    let onDecrement: () -> Void

    func makeUIView(context: Context) -> AdjustableTimelineView {
        AdjustableTimelineView()
    }

    func updateUIView(_ uiView: AdjustableTimelineView, context: Context) {
        uiView.accessibilityLabel = "Grid timeline"
        uiView.accessibilityValue = value
        uiView.accessibilityHint = "Adjust to move by one source interval."
        uiView.onIncrement = onIncrement
        uiView.onDecrement = onDecrement
    }
}

private final class AdjustableTimelineView: UIView {
    var onIncrement: (() -> Void)?
    var onDecrement: (() -> Void)?

    override init(frame: CGRect) {
        super.init(frame: frame)
        isAccessibilityElement = true
        accessibilityTraits = .adjustable
        backgroundColor = .clear
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override func accessibilityIncrement() {
        onIncrement?()
    }

    override func accessibilityDecrement() {
        onDecrement?()
    }
}

private struct ForecastHatch: View {
    var body: some View {
        Canvas { context, size in
            for x in stride(from: -size.height, through: size.width + size.height, by: 7) {
                var line = Path()
                line.move(to: CGPoint(x: x, y: size.height))
                line.addLine(to: CGPoint(x: x + size.height, y: 0))
                context.stroke(line, with: .foreground, lineWidth: 1)
            }
        }
    }
}

private extension Comparable {
    func clamped(to range: ClosedRange<Self>) -> Self {
        min(max(self, range.lowerBound), range.upperBound)
    }
}
