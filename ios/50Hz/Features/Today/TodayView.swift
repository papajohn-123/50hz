import SwiftUI

private struct TodayMoment: Identifiable {
    let id: String
    let time: Date
    let title: String
    let detail: String
    let factClass: FactClass
    let subject: FuelKind?
    let importance: Bool
    let event: GridEvent?
}

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
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
        let hasForecast = model.isForecastTimelineUsable
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
                    GlobalInfoButton()
                }
                .padding(.bottom, 6)

                Text("Britain’s daily view — what changed, and what the forecast suggests next.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.bottom, 28)

                if !hasForecast {
                    ForecastUnavailableNotice(detail: model.forecastUnavailableReason)
                        .padding(.bottom, 24)
                } else if model.timelineRefreshError != nil {
                    TimelineRefreshNotice(confirmedAt: timeline.nowBoundary) {
                        Task { await model.retry() }
                    }
                    .padding(.bottom, 20)
                }

                if let eventError = model.eventsError, !model.events.isEmpty {
                    Text("Reported events are shown from the last confirmed event list. \(eventError)")
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                        .padding(.bottom, 18)
                }

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

                SectionLabel(
                    "Chronology",
                    trailing: hasForecast ? "Observed + forecast" : "Observed"
                )
                    .padding(.bottom, 8)

                ForEach(moments.filter { !$0.importance }) { moment in
                    MomentRow(moment: moment, isNow: moment.id.hasPrefix("now-")) {
                        open(moment)
                    }
                }

                VStack(alignment: .leading, spacing: 8) {
                    Hairline()
                    Text(hasForecast
                        ? "Forecast moments use a timeline confirmed within the past hour. Predictions can change as weather and system conditions evolve."
                        : "Only confirmed observations are shown while the forecast freshness gate is closed.")
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
        if let event = moment.event {
            model.selectedTab = .live
            Task { @MainActor in
                await Task.yield()
                model.selectedEvent = event
            }
            return
        }
        let selectMoment = {
            model.selectedTime = moment.time
            model.selectedFuel = moment.subject
            model.selectedTab = .live
        }
        if reduceMotion {
            selectMoment()
        } else {
            withAnimation(.snappy(duration: 0.3), selectMoment)
        }
    }

    private func makeMoments(snapshot: GridSnapshot, timeline: GridTimeline) -> [TodayMoment] {
        let boundary = timeline.nowBoundary
        let observed = timeline.samples.filter { $0.factClass != .forecast && $0.timestamp <= boundary }
        let forecast = model.isForecastTimelineUsable
            ? timeline.samples.filter {
                $0.factClass == .forecast
                    && $0.timestamp >= boundary
                    && $0.timestamp > Date()
            }
            : []
        var moments: [TodayMoment] = []

        if let cleanest = forecast.min(by: { $0.carbonIntensity < $1.carbonIntensity }) {
            moments.append(
                TodayMoment(
                    id: "forecast-cleanest-\(cleanest.timestamp.timeIntervalSince1970)",
                    time: cleanest.timestamp,
                    title: "Cleanest forecast period",
                    detail: "The latest forecast reaches about \(Int(cleanest.carbonIntensity.rounded())) gCO₂/kWh. Treat this as a window that can move as the forecast updates.",
                    factClass: .forecast,
                    subject: leadingFuel(in: cleanest),
                    importance: true,
                    event: nil
                )
            )
        }

        if let solarHigh = observed.max(by: { megawatts(.solar, in: $0) < megawatts(.solar, in: $1) }),
           megawatts(.solar, in: solarHigh) > 0 {
            moments.append(
                TodayMoment(
                    id: "observed-solar-high-\(solarHigh.timestamp.timeIntervalSince1970)",
                    time: solarHigh.timestamp,
                    title: "Solar’s observed high",
                    detail: "The national estimate reached \(gigawatts(megawatts(.solar, in: solarHigh))) GW in the available timeline.",
                    factClass: solarHigh.generation.first(where: { $0.fuel == .solar })?.factClass ?? .estimated,
                    subject: .solar,
                    importance: false,
                    event: nil
                )
            )
        }

        if let observedDemandHigh = observed.max(by: { $0.demandMW < $1.demandMW }) {
            moments.append(
                TodayMoment(
                    id: "observed-demand-high-\(observedDemandHigh.timestamp.timeIntervalSince1970)",
                    time: observedDemandHigh.timestamp,
                    title: "Observed demand high",
                    detail: "Demand reached \(gigawatts(observedDemandHigh.demandMW)) GW in the timeline currently held by 50Hz.",
                    factClass: .observed,
                    subject: nil,
                    importance: false,
                    event: nil
                )
            )
        }

        moments.append(
            TodayMoment(
                id: "now-\(snapshot.timestamp.timeIntervalSince1970)",
                time: snapshot.timestamp,
                title: [snapshot.headline.balance, snapshot.headline.energyPosition].joined(separator: " · "),
                detail: snapshot.headline.publicInterpretation(for: snapshot.generation),
                factClass: .observed,
                subject: snapshot.generation.min(by: { $0.rank < $1.rank })?.fuel,
                importance: false,
                event: nil
            )
        )

        if let demandHigh = forecast.max(by: { $0.demandMW < $1.demandMW }) {
            moments.append(
                TodayMoment(
                    id: "forecast-demand-high-\(demandHigh.timestamp.timeIntervalSince1970)",
                    time: demandHigh.timestamp,
                    title: "Forecast demand high",
                    detail: "The available forecast reaches about \(gigawatts(demandHigh.demandMW)) GW. This is a forecast, not a system warning.",
                    factClass: .forecast,
                    subject: leadingFuel(in: demandHigh),
                    importance: false,
                    event: nil
                )
            )
        }

        if let windHigh = forecast.max(by: { megawatts(.wind, in: $0) < megawatts(.wind, in: $1) }),
           megawatts(.wind, in: windHigh) > 0 {
            moments.append(
                TodayMoment(
                    id: "forecast-wind-high-\(windHigh.timestamp.timeIntervalSince1970)",
                    time: windHigh.timestamp,
                    title: "Forecast wind high",
                    detail: "Wind reaches about \(gigawatts(megawatts(.wind, in: windHigh))) GW in the latest available forecast.",
                    factClass: .forecast,
                    subject: .wind,
                    importance: false,
                    event: nil
                )
            )
        }

        let startOfToday = Calendar.autoupdatingCurrent.startOfDay(for: snapshot.timestamp)
        var reportedEvents = model.events.filter {
            $0.startedAt >= startOfToday && $0.startedAt <= snapshot.timestamp
        }
        if let active = snapshot.activeEvent, !reportedEvents.contains(where: { $0.id == active.id }) {
            reportedEvents.append(active)
        }
        moments.append(contentsOf: reportedEvents.map { event in
            TodayMoment(
                id: "event-\(event.id)",
                time: event.startedAt,
                title: event.title,
                detail: event.summary,
                factClass: .observed,
                subject: nil,
                importance: false,
                event: event
            )
        })

        return moments.sorted { lhs, rhs in
            if lhs.importance != rhs.importance { return lhs.importance }
            return lhs.time < rhs.time
        }
    }

    private func megawatts(_ fuel: FuelKind, in sample: GridTimelineSample) -> Double {
        sample.generation.first(where: { $0.fuel == fuel })?.megawatts ?? 0
    }

    private func leadingFuel(in sample: GridTimelineSample) -> FuelKind? {
        sample.generation.max(by: { $0.megawatts < $1.megawatts })?.fuel
    }

    private func gigawatts(_ megawatts: Double) -> String {
        (megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))
    }

}

private struct ForecastUnavailableNotice: View {
    let detail: String

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: "cloud.slash")
                .foregroundStyle(GridTheme.staleAmber)
            VStack(alignment: .leading, spacing: 3) {
                Text("Current forecast unavailable")
                    .font(.subheadline.weight(.semibold))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .padding(14)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.staleAmber.opacity(0.18), lineWidth: 1))
    }
}

private struct TimelineRefreshNotice: View {
    let confirmedAt: Date
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: "clock.badge.exclamationmark")
                .foregroundStyle(GridTheme.staleAmber)
            VStack(alignment: .leading, spacing: 3) {
                Text("Forecast refresh incomplete")
                    .font(.subheadline.weight(.semibold))
                Text("Showing the last timeline confirmed at \(confirmedAt.formatted(.dateTime.hour().minute())). It remains inside 50Hz’s one-hour display limit.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            Spacer(minLength: 4)
            Button("Retry", action: retry)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.staleAmber)
                .frame(minHeight: 44)
        }
        .padding(14)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.staleAmber.opacity(0.18), lineWidth: 1))
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
                        Text(timestampLabel)
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

    private var timestampLabel: String {
        if isNow { return "NOW" }
        if Calendar.autoupdatingCurrent.isDateInToday(moment.time) {
            return moment.time.formatted(.dateTime.hour().minute())
        }
        let currentYear = Calendar.autoupdatingCurrent.component(.year, from: Date())
        let momentYear = Calendar.autoupdatingCurrent.component(.year, from: moment.time)
        if momentYear != currentYear {
            return moment.time.formatted(.dateTime.day().month(.abbreviated).year().hour().minute())
        }
        return moment.time.formatted(.dateTime.day().month(.abbreviated).hour().minute())
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
