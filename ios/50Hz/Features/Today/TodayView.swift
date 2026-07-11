import SwiftUI

private struct TodayMoment: Identifiable {
    let id: String
    let time: Date
    let title: String
    let detail: String
    let factClass: FactClass
    let subject: FuelKind?
    let importance: Bool
}

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sharePayload: GridShareCardPayload?

    var body: some View {
        Group {
            if let snapshot = model.snapshot, let timeline = model.timeline {
                content(snapshot: snapshot, timeline: timeline)
            } else {
                TodayLoadingView()
            }
        }
        .gridPageBackground()
        .sheet(item: $sharePayload) { payload in
            ShareCardSheet(payload: payload)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
    }

    private func content(snapshot: GridSnapshot, timeline: GridTimeline) -> some View {
        let moments = makeMoments(snapshot: snapshot, timeline: timeline)
        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Today")
                        .font(.system(.largeTitle, design: .rounded, weight: .medium))
                        .tracking(-1.2)
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    Text(snapshot.timestamp.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated)))
                        .font(.caption)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .padding(.bottom, 6)

                Text("Britain’s field log — what changed, and what the forecast suggests next.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.bottom, 28)

                if let lead = moments.first(where: \.importance) {
                    LeadMoment(
                        moment: lead,
                        action: { open(lead) },
                        share: {
                            sharePayload = .moment(
                                title: lead.title,
                                detail: lead.detail,
                                timestamp: lead.time,
                                factClass: lead.factClass,
                                sources: snapshot.sources
                            )
                        }
                    )
                    .padding(.bottom, 30)
                }

                SectionLabel("Chronology", trailing: "Observed + forecast")
                    .padding(.bottom, 8)

                ForEach(moments.filter { !$0.importance }) { moment in
                    MomentRow(moment: moment, isNow: abs(moment.time.timeIntervalSince(timeline.nowBoundary)) < 60) {
                        open(moment)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Hairline()
                    Text("Forecast moments use the latest available issue. Predictions can change as weather and system conditions evolve.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .padding(.top, 10)
                .padding(.bottom, 28)
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
        }
        .scrollIndicators(.hidden)
    }

    private func open(_ moment: TodayMoment) {
        withAnimation(.snappy(duration: 0.3)) {
            model.selectedTime = moment.time
            model.selectedFuel = moment.subject
            model.selectedTab = .live
        }
    }

    private func makeMoments(snapshot: GridSnapshot, timeline: GridTimeline) -> [TodayMoment] {
        let now = timeline.nowBoundary
        return [
            TodayMoment(
                id: "clean-window",
                time: now.addingTimeInterval(3 * 3_600),
                title: "Tonight’s cleanest window",
                detail: "Carbon is forecast to fall near 96 g/kWh as wind strengthens. Best viewed as a window, not an exact promise.",
                factClass: .forecast,
                subject: .wind,
                importance: true
            ),
            TodayMoment(
                id: "wind-lead",
                time: now.addingTimeInterval(-5 * 3_600),
                title: "Wind became the largest source",
                detail: "Output passed gas during the morning ramp and has remained in first place.",
                factClass: .observed,
                subject: .wind,
                importance: false
            ),
            TodayMoment(
                id: "solar-noon",
                time: now.addingTimeInterval(-2 * 3_600),
                title: "Solar reached its daytime high",
                detail: "The reported national estimate topped 6.3 GW before beginning its afternoon decline.",
                factClass: .estimated,
                subject: .solar,
                importance: false
            ),
            TodayMoment(
                id: "now",
                time: now,
                title: "Comfortable and exporting",
                detail: snapshot.headline.interpretation,
                factClass: .observed,
                subject: nil,
                importance: false
            ),
            TodayMoment(
                id: "wind-rise",
                time: now.addingTimeInterval(2 * 3_600),
                title: "Wind expected to rise",
                detail: "The latest forecast points to roughly 2.1 GW more wind by early evening.",
                factClass: .forecast,
                subject: .wind,
                importance: false
            ),
            TodayMoment(
                id: "evening-peak",
                time: now.addingTimeInterval(6 * 3_600),
                title: "Evening demand peak",
                detail: "Demand is forecast near 35.8 GW. Available data does not indicate a tight-system warning.",
                factClass: .forecast,
                subject: .gas,
                importance: false
            )
        ]
    }
}

private struct LeadMoment: View {
    let moment: TodayMoment
    let action: () -> Void
    let share: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Label("FORECAST", systemImage: "moon.stars")
                    .font(.caption2.weight(.semibold))
                    .fontDesign(.monospaced)
                    .tracking(0.8)
                    .foregroundStyle(GridTheme.forecastViolet)
                Spacer()
                Text(moment.time.formatted(.dateTime.hour().minute()))
                    .font(.caption)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                Button(action: share) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Share this forecast moment")
            }

            Button(action: action) {
                VStack(alignment: .leading, spacing: 15) {
                    Text(moment.title)
                        .font(.system(.title, design: .rounded, weight: .medium))
                        .tracking(-0.8)
                        .foregroundStyle(GridTheme.textPrimary)
                    Text(moment.detail)
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                        .lineSpacing(4)
                    HStack {
                        Text("Open on Live map")
                            .font(.caption.weight(.semibold))
                        Spacer()
                        Image(systemName: "arrow.right")
                    }
                    .foregroundStyle(GridTheme.forecastViolet)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(18)
        .background(GridTheme.forecastViolet.opacity(0.075), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.20), lineWidth: 1))
        .accessibilityElement(children: .contain)
    }
}

private struct MomentRow: View {
    let moment: TodayMoment
    let isNow: Bool
    let action: () -> Void

    private var color: Color {
        moment.factClass == .forecast ? GridTheme.forecastViolet : GridTheme.liveCyan
    }

    var body: some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 14) {
                VStack(spacing: 0) {
                    Circle()
                        .fill(color)
                        .frame(width: isNow ? 9 : 6, height: isNow ? 9 : 6)
                        .shadow(color: color.opacity(0.5), radius: 5)
                        .padding(.top, 7)
                    Rectangle()
                        .fill(color.opacity(0.18))
                        .frame(width: 1, height: 82)
                }
                VStack(alignment: .leading, spacing: 5) {
                    HStack {
                        Text(isNow ? "NOW" : moment.time.formatted(.dateTime.hour().minute()))
                            .font(.caption2.weight(.semibold))
                            .fontDesign(.monospaced)
                            .foregroundStyle(color)
                        Text(moment.factClass.rawValue.uppercased())
                            .font(.system(size: 8, weight: .medium, design: .monospaced))
                            .foregroundStyle(GridTheme.textTertiary)
                    }
                    Text(moment.title)
                        .font(.headline)
                        .foregroundStyle(GridTheme.textPrimary)
                    Text(moment.detail)
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .padding(.top, 8)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens this moment on the Live map")
    }
}

private struct TodayLoadingView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Today").font(.largeTitle)
            Text("Waiting for a confirmed timeline…")
                .foregroundStyle(GridTheme.textSecondary)
            ProgressView().tint(GridTheme.liveCyan)
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(GridTheme.horizontalPadding)
    }
}
