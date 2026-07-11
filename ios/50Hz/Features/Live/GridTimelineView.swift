import SwiftUI

struct GridTimelineView: View {
    let timeline: GridTimeline
    @Binding var selectedTime: Date?
    @State private var feedbackTick = 0

    private var firstDate: Date { timeline.samples.first?.timestamp ?? timeline.nowBoundary }
    private var lastDate: Date { timeline.samples.last?.timestamp ?? timeline.nowBoundary }
    private var selectedDate: Date { selectedTime ?? timeline.nowBoundary }
    private var span: TimeInterval { max(lastDate.timeIntervalSince(firstDate), 1) }
    private var nowRatio: CGFloat { CGFloat(timeline.nowBoundary.timeIntervalSince(firstDate) / span).clamped(to: 0...1) }
    private var selectedRatio: CGFloat { CGFloat(selectedDate.timeIntervalSince(firstDate) / span).clamped(to: 0...1) }
    private var isForecast: Bool { selectedDate > timeline.nowBoundary }
    private var isLive: Bool { selectedTime == nil }
    private var hasForecastRange: Bool {
        lastDate > timeline.nowBoundary.addingTimeInterval(TimeInterval(timeline.sourceResolutionSeconds / 2))
    }

    var body: some View {
        VStack(spacing: 11) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(isLive ? "NOW" : selectedDate.formatted(.dateTime.hour().minute()))
                        .font(.system(.subheadline, design: .monospaced, weight: .semibold))
                        .foregroundStyle(isForecast ? GridTheme.forecastViolet : GridTheme.textPrimary)
                    Text(isLive ? "Live snapshot" : (isForecast ? "Forecast frame" : "Observed replay"))
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }

                Spacer()

                if !isLive {
                    Button {
                        withAnimation(.snappy(duration: 0.28)) { selectedTime = nil }
                    } label: {
                        Label("Resume live", systemImage: "dot.radiowaves.left.and.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .padding(.horizontal, 10)
                            .frame(minHeight: 36)
                            .background(GridTheme.liveCyan.opacity(0.09), in: Capsule())
                    }
                    .buttonStyle(.plain)
                } else {
                    Text("\(timeline.sourceResolutionSeconds / 60) min source resolution")
                        .font(.caption2)
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
                        .animation(.interactiveSpring(response: 0.22, dampingFraction: 0.88), value: selectedRatio)
                }
                .frame(maxHeight: .infinity)
                .contentShape(Rectangle())
                .gesture(
                    DragGesture(minimumDistance: 0)
                        .onChanged { value in
                            let ratio = (value.location.x / max(proxy.size.width, 1)).clamped(to: 0...1)
                            let date = firstDate.addingTimeInterval(span * TimeInterval(ratio))
                            selectedTime = date
                            let newTick = Int(ratio * 48)
                            if newTick != feedbackTick { feedbackTick = newTick }
                        }
                )
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("Grid timeline")
                .accessibilityValue(isLive ? "Now" : selectedDate.formatted(.dateTime.hour().minute()))
                .accessibilityAdjustableAction { direction in
                    let step = TimeInterval(timeline.sourceResolutionSeconds)
                    let base = selectedTime ?? timeline.nowBoundary
                    switch direction {
                    case .increment: selectedTime = min(base.addingTimeInterval(step), lastDate)
                    case .decrement: selectedTime = max(base.addingTimeInterval(-step), firstDate)
                    @unknown default: break
                    }
                }
            }
            .frame(height: 24)
            .sensoryFeedback(.selection, trigger: feedbackTick)

            GeometryReader { proxy in
                ZStack {
                    Text(firstDate.formatted(.dateTime.hour().minute()))
                        .frame(maxWidth: .infinity, alignment: .leading)

                    if hasForecastRange {
                        Text("NOW")
                            .position(
                                x: (proxy.size.width * nowRatio).clamped(to: 48...(proxy.size.width - 48)),
                                y: proxy.size.height / 2
                            )
                        Text(lastDate.formatted(.dateTime.hour().minute()))
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    } else {
                        Text("NOW")
                            .frame(maxWidth: .infinity, alignment: .trailing)
                    }
                }
            }
            .frame(height: 12)
            .font(.system(size: 9, weight: .medium, design: .monospaced))
            .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.vertical, 14)
        .background(.ultraThinMaterial.opacity(0.68))
        .overlay(alignment: .top) { Hairline() }
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
