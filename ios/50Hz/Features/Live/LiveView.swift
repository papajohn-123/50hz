import SwiftUI

struct LiveView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sharePayload: GridShareCardPayload?

    var body: some View {
        ZStack {
            GridTheme.background.ignoresSafeArea()

            switch model.loadPhase {
            case .loading:
                LiveLoadingView()
            case .failed(let message):
                LiveFailureView(message: message) {
                    Task { await model.retry() }
                }
            case .loaded:
                if let snapshot = model.presentedSnapshot {
                    loadedContent(snapshot: snapshot, model: model)
                } else {
                    LiveFailureView(message: "No confirmed grid snapshot is available.") {
                        Task { await model.retry() }
                    }
                }
            }
        }
        .gridPageBackground()
        .sheet(isPresented: $model.isAskPresented) {
            if let snapshot = model.presentedSnapshot {
                AskGridInspector(snapshot: snapshot)
                    .presentationDetents([.medium, .large])
                    .presentationDragIndicator(.visible)
                    .presentationBackground(GridTheme.surface)
            }
        }
        .sheet(item: $model.selectedEvent) { event in
            EventDetailSheet(event: event, snapshot: model.presentedSnapshot)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
        .sheet(item: $sharePayload) { payload in
            ShareCardSheet(payload: payload)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
    }

    private func loadedContent(snapshot: GridSnapshot, model: AppModel) -> some View {
        let isForecast = model.selectedSample?.factClass == .forecast

        return ScrollView {
            LazyVStack(alignment: .leading, spacing: 19) {
                BrandHeader(
                    snapshot: snapshot,
                    mode: model.timelineModeLabel,
                    onShare: { sharePayload = .current(snapshot) }
                )
                    .padding(.top, 8)

                if snapshot.freshness != .live || model.lastRefreshError != nil {
                    DataStateBanner(snapshot: snapshot, lastError: model.lastRefreshError, isRefreshing: model.isRefreshing) {
                        Task { await model.retry() }
                    }
                }

                ConditionHeadlineView(headline: snapshot.headline, isForecast: isForecast)

                MeasurementRow(snapshot: snapshot, isForecast: isForecast)

                BritainGridMap(
                    snapshot: snapshot,
                    selectedFuel: model.selectedFuel,
                    isForecast: isForecast,
                    onEventTap: { model.selectedEvent = snapshot.activeEvent }
                )
                .frame(height: 340)
                .opacity(snapshot.freshness == .offline ? 0.62 : (snapshot.freshness == .stale ? 0.82 : 1))
                .padding(.horizontal, -GridTheme.horizontalPadding)
                .overlay(alignment: .bottomTrailing) {
                    Button {
                        model.isAskPresented = true
                    } label: {
                        Label("Ask the Grid", systemImage: "sparkles")
                            .font(.caption.weight(.semibold))
                            .padding(.horizontal, 14)
                            .frame(minHeight: 44)
                            .foregroundStyle(GridTheme.textPrimary)
                            .background(GridTheme.surface.opacity(0.92), in: Capsule())
                            .overlay(Capsule().stroke(GridTheme.liveCyan.opacity(0.25), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .padding(.trailing, 2)
                    .padding(.bottom, 2)
                }

                VStack(alignment: .leading, spacing: 12) {
                    SectionLabel(
                        "Generation mix",
                        trailing: "\((snapshot.totalGenerationMW / 1_000).formatted(.number.precision(.fractionLength(1)))) GW"
                    )
                    GenerationMixBar(readings: snapshot.generation, selectedFuel: model.selectedFuel)
                }

                FuelFilter(readings: snapshot.generation, selection: $model.selectedFuel)

                if let fuel = model.selectedFuel,
                   let reading = snapshot.reading(for: fuel),
                   let timeline = model.timeline {
                    FuelFocusView(reading: reading, timeline: timeline)
                }

                SourceFootnote(snapshot: snapshot)
                    .padding(.bottom, 12)
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
        }
        .scrollIndicators(.hidden)
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if let timeline = model.timeline {
                GridTimelineView(timeline: timeline, selectedTime: $model.selectedTime)
                    .background(GridTheme.background.opacity(0.95))
            }
        }
        .overlay(alignment: .leading) {
            if snapshot.freshness == .critical {
                Rectangle()
                    .fill(GridTheme.warning)
                    .frame(width: 2)
                    .ignoresSafeArea()
                    .accessibilityHidden(true)
            }
        }
    }
}

private struct SourceFootnote: View {
    let snapshot: GridSnapshot

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Hairline()
                .padding(.bottom, 4)
            Text("Observed \(snapshot.timestamp.formatted(.dateTime.hour().minute())) · Retrieved \(snapshot.retrievedAt.formatted(.dateTime.hour().minute().second()))")
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
            Text(snapshot.sources.map(\.name).uniqued().joined(separator: " · "))
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            Text("Map flows are illustrative; tap evidence for exact source and timing.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }
}

private struct DataStateBanner: View {
    let snapshot: GridSnapshot
    let lastError: String?
    let isRefreshing: Bool
    let retry: () -> Void

    private var color: Color {
        if lastError != nil { return GridTheme.staleAmber }
        return snapshot.freshness == .critical ? GridTheme.warning : GridTheme.staleAmber
    }

    private var title: String {
        switch snapshot.freshness {
        case .live: lastError == nil ? "Connected" : "Timeline update incomplete"
        case .stale: "Feed delayed"
        case .offline: "Viewing the last confirmed snapshot"
        case .critical: "Material reported event"
        }
    }

    private var detail: String {
        switch snapshot.freshness {
        case .live: lastError ?? "The latest source data is available."
        case .stale: "Measurements are \(max(snapshot.freshnessAgeSeconds / 60, 1)) minutes old. \(lastError ?? "Automatic retry is in progress.")"
        case .offline: "Last confirmed at \(snapshot.timestamp.formatted(.dateTime.hour().minute())). \(lastError ?? "The story will resume when the connection returns.")"
        case .critical: snapshot.activeEvent?.summary ?? "Open the event for source-backed details."
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: snapshot.freshness == .critical ? "exclamationmark.triangle" : "clock.badge.exclamationmark")
                .foregroundStyle(color)
                .padding(.top, 2)
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            if snapshot.freshness != .critical {
                Button(isRefreshing ? "Retrying…" : "Retry", action: retry)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(color)
                    .frame(minHeight: 44)
                    .disabled(isRefreshing)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(color.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(color.opacity(0.22), lineWidth: 1))
    }
}

private struct LiveLoadingView: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 15, paused: reduceMotion)) { timeline in
            let pulse = reduceMotion ? 0.40 : 0.28 + ((sin(timeline.date.timeIntervalSinceReferenceDate * 1.8) + 1) * 0.09)
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    Text("50Hz").font(.title2.bold())
                    Spacer()
                    Text("CONNECTING")
                        .font(.caption2)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                Text("Connecting to the grid…")
                    .font(.title2.weight(.medium))
                    .accessibilityAddTraits(.updatesFrequently)
                RoundedRectangle(cornerRadius: 3).fill(GridTheme.surface).frame(width: 270, height: 12).opacity(pulse)
                HStack(spacing: 14) {
                    ForEach(0..<3, id: \.self) { _ in
                        RoundedRectangle(cornerRadius: 8).fill(GridTheme.surface).frame(maxWidth: .infinity).frame(height: 55).opacity(pulse)
                    }
                }
                BritainShape()
                    .fill(GridTheme.surface)
                    .overlay(BritainShape().stroke(GridTheme.liveCyan.opacity(0.10), lineWidth: 1))
                    .frame(maxWidth: .infinity)
                    .frame(height: 330)
                    .padding(.horizontal, 70)
                    .opacity(pulse)
                Spacer()
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 8)
        }
    }
}

private struct LiveFailureView: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("50Hz").font(.title2.bold())
            Spacer()
            Image(systemName: "wifi.slash")
                .font(.system(size: 27, weight: .light))
                .foregroundStyle(GridTheme.staleAmber)
            Text("The grid is out of reach")
                .font(.title2.weight(.medium))
            Text(message)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
            Text("No cached snapshot is being labelled as live. Your story will resume on reconnect.")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
            Button(action: retry) {
                Label("Try again", systemImage: "arrow.clockwise")
                    .font(.subheadline.weight(.semibold))
                    .padding(.horizontal, 16)
                    .frame(minHeight: 44)
                    .background(GridTheme.staleAmber.opacity(0.12), in: Capsule())
                    .foregroundStyle(GridTheme.staleAmber)
            }
            .buttonStyle(.plain)
            Spacer()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(GridTheme.horizontalPadding)
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen: Set<Element> = []
        return filter { seen.insert($0).inserted }
    }
}
