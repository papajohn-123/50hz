import SwiftUI

struct LiveView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sharePayload: GridShareCardPayload?
    @State private var isDataDetailsPresented = false
    @State private var isEventListPresented = false
    @State private var selectedMapAsset: LiveMapAsset?

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
        .sheet(isPresented: $isDataDetailsPresented) {
            if let snapshot = model.snapshot {
                DataDetailsSheet(snapshot: snapshot, mode: model.timelineModeLabel)
                    .presentationDetents([.large])
                    .presentationDragIndicator(.visible)
                    .presentationBackground(GridTheme.background)
            }
        }
        .sheet(isPresented: $isEventListPresented) {
            ReportedEventsListSheet()
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.background)
        }
        .sheet(item: $selectedMapAsset) { asset in
            LiveAssetInspector(asset: asset)
                .presentationDetents([.medium])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
    }

    private func loadedContent(snapshot: GridSnapshot, model: AppModel) -> some View {
        let isForecast = model.selectedSample?.factClass == .forecast

        return VStack(spacing: 0) {
            LivePersistentHeader(
                snapshot: snapshot,
                mode: model.timelineModeLabel,
                onAsk: { model.isAskPresented = true },
                onShare: { sharePayload = .current(snapshot) },
                onStatusTap: { isDataDetailsPresented = true }
            )

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 19) {

                    if snapshot.freshness != .live || model.lastRefreshError != nil {
                        DataStateBanner(snapshot: snapshot, lastError: model.lastRefreshError, isRefreshing: model.isRefreshing) {
                            Task { await model.retry() }
                        }
                    }

                    ConditionHeadlineView(
                        headline: snapshot.headline,
                        isForecast: isForecast,
                        interpretation: snapshot.headline.publicInterpretation(for: snapshot.generation)
                    )

                    LiveMeasurementLedger(snapshot: snapshot, mode: model.timelineModeLabel)

                    BritainGridMap(
                        snapshot: snapshot,
                        selectedFuel: model.selectedFuel,
                        isForecast: isForecast,
                        assets: [],
                        onAssetTap: { selectedMapAsset = $0 }
                    )
                    .frame(height: 340)
                    .opacity(snapshot.freshness == .offline ? 0.62 : (snapshot.freshness == .stale ? 0.82 : 1))
                    .padding(.horizontal, -GridTheme.horizontalPadding)

                    if model.timelineModeLabel == "LIVE" {
                        Button {
                            isEventListPresented = true
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "exclamationmark.bubble")
                                    .foregroundStyle(model.eventsError == nil ? GridTheme.liveCyan : GridTheme.staleAmber)
                                    .frame(width: 22)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("Active reported events")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(GridTheme.textPrimary)
                                    Text(model.eventsError == nil
                                         ? "\(model.events.count) in the current server-ranked list"
                                         : "Saved list · refresh incomplete")
                                        .font(.caption2)
                                        .foregroundStyle(GridTheme.textTertiary)
                                }
                                Spacer(minLength: 8)
                                Image(systemName: "chevron.right")
                                    .font(.caption)
                                    .foregroundStyle(GridTheme.textTertiary)
                                    .accessibilityHidden(true)
                            }
                            .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                            .contentShape(Rectangle())
                            .overlay(alignment: .bottom) { Hairline() }
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens the full active reported-event list")
                    }

                    if model.timelineModeLabel == "LIVE" {
                        InterconnectorLedger(snapshot: snapshot)
                    }

                    if snapshot.generation.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            SectionLabel(LiveTruthCopy.supplyTitle, trailing: "NOT IN FRAME")
                            Text("A time-aligned transmission-visible supply mix is not available for this frame, so 50Hz does not project one.")
                                .font(.caption)
                                .foregroundStyle(GridTheme.textSecondary)
                        }
                    } else {
                        VStack(alignment: .leading, spacing: 12) {
                            SectionLabel(
                                LiveTruthCopy.supplyTitle,
                                trailing: "\((snapshot.totalGenerationMW / 1_000).formatted(.number.precision(.fractionLength(1)))) GW"
                            )
                            Text(supplyScope(snapshot))
                                .font(.caption)
                                .foregroundStyle(GridTheme.textTertiary)
                                .fixedSize(horizontal: false, vertical: true)
                            GenerationMixBar(readings: snapshot.generation, selectedFuel: model.selectedFuel)
                        }

                        FuelFilter(readings: snapshot.generation, selection: $model.selectedFuel)

                        if let fuel = model.selectedFuel,
                           let reading = snapshot.reading(for: fuel),
                           let timeline = model.displayTimeline {
                            TransmissionFuelFocusView(reading: reading, timeline: timeline)
                        }
                    }

                    SourceFootnote(snapshot: snapshot, mode: model.timelineModeLabel)
                        .padding(.bottom, 20)
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
            }
            .scrollIndicators(.hidden)
            .layoutPriority(1)

            if let timeline = model.displayTimeline {
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

    private func supplyScope(_ snapshot: GridSnapshot) -> String {
        if let supply = snapshot.supply {
            let completeness = supply.isComplete
                ? ""
                : " This is a partial operational boundary, not all electricity generated in Britain."
            return supply.boundary + completeness
        }
        return "National fuel totals visible to the transmission system. The map does not place individual generators."
    }
}

struct LiveAssetInspector: View {
    let asset: LiveMapAsset
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text(asset.name)
                        .font(.title2.weight(.medium))
                    Text(asset.fuel?.displayName ?? "Generation asset")
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                }

                VStack(alignment: .leading, spacing: 0) {
                    inspectorRow("Capacity", asset.capacityMW.map { "\($0.formatted(.number.precision(.fractionLength(0)))) MW" } ?? "Not reported")
                    inspectorRow("Coordinate", "\(asset.latitude.formatted(.number.precision(.fractionLength(3)))), \(asset.longitude.formatted(.number.precision(.fractionLength(3))))")
                    inspectorRow("Observed", asset.observedAt.formatted(.dateTime.day().month().hour().minute()))
                    inspectorRow("Source", asset.sourceID)
                }

                Text("The source coordinate is approximately projected onto 50Hz’s schematic Britain outline. It is not a transmission-route map.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)

                Spacer(minLength: 0)
            }
            .padding(GridTheme.horizontalPadding)
            .navigationTitle("Generator")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(GridTheme.liveCyan)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func inspectorRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(label)
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
            Spacer(minLength: 8)
            Text(value)
                .font(.subheadline)
                .multilineTextAlignment(.trailing)
                .foregroundStyle(GridTheme.textPrimary)
        }
        .frame(maxWidth: .infinity, minHeight: 48)
        .overlay(alignment: .bottom) { Hairline() }
    }
}

private struct LivePersistentHeader: View {
    let snapshot: GridSnapshot
    let mode: String
    let onAsk: () -> Void
    let onShare: () -> Void
    let onStatusTap: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 2) {
                Text("50Hz")
                    .font(.system(.title2, design: .rounded, weight: .bold))
                    .tracking(-0.8)
                    .foregroundStyle(GridTheme.textPrimary)
                    .accessibilityAddTraits(.isHeader)

                Spacer(minLength: 8)

                GlobalInfoButton()

                Button(action: onShare) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Share this grid state")

                Button(action: onAsk) {
                    Label("Ask", systemImage: "sparkles")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 12)
                        .frame(minHeight: 44)
                        .foregroundStyle(GridTheme.textPrimary)
                        .background(GridTheme.liveCyan.opacity(0.10), in: Capsule())
                        .overlay(Capsule().stroke(GridTheme.liveCyan.opacity(0.28), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Ask the Grid")
                .accessibilityHint("Opens a source-grounded question about the selected grid state")
            }

            TimelineView(.periodic(from: .now, by: 30)) { context in
                Button(action: onStatusTap) {
                    HStack(spacing: 8) {
                        Circle()
                            .fill(statusColor(at: context.date))
                            .frame(width: 6, height: 6)
                        Text(statusLabel(at: context.date))
                            .font(.caption2.weight(.semibold))
                            .fontDesign(.monospaced)
                            .tracking(0.25)
                            .lineLimit(1)
                            .minimumScaleFactor(0.82)
                        Spacer(minLength: 8)
                        Text("Inspect timing")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.textTertiary)
                        Image(systemName: "chevron.right")
                            .font(.system(size: 9, weight: .semibold))
                            .foregroundStyle(GridTheme.textTertiary)
                            .accessibilityHidden(true)
                    }
                    .foregroundStyle(GridTheme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Data timing, \(statusLabel(at: context.date))")
                .accessibilityHint("Opens timing for each source")
            }
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.top, 4)
        .background(GridTheme.background.opacity(0.97))
        .overlay(alignment: .bottom) { Hairline() }
    }

    private func statusLabel(at date: Date) -> String {
        switch mode {
        case "FORECAST":
            return "FORECAST · valid \(snapshot.timestamp.formatted(.dateTime.hour().minute()))"
        case "REPLAY":
            return "REPLAY · frame \(snapshot.timestamp.formatted(.dateTime.hour().minute()))"
        default:
            return "LIVE INPUTS · \(LiveFreshnessPresentation.make(snapshot: snapshot, at: date).compactLabel)"
        }
    }

    private func statusColor(at date: Date) -> Color {
        if mode == "FORECAST" { return GridTheme.forecastViolet }
        if mode == "REPLAY" { return GridTheme.liveCyan.opacity(0.75) }
        return LiveFreshnessPresentation.make(snapshot: snapshot, at: date).hasConcern
            ? GridTheme.staleAmber
            : GridTheme.liveCyan
    }
}

private struct LiveMeasurementLedger: View {
    let snapshot: GridSnapshot
    let mode: String

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            VStack(alignment: .leading, spacing: 0) {
                SectionLabel("Measured state", trailing: mode)
                    .padding(.bottom, 6)

                ForEach(LiveMetricPresentation.make(snapshot: snapshot, mode: mode, at: context.date)) { metric in
                    metricRow(metric)
                }
            }
        }
    }

    private func metricRow(_ metric: LiveMetricPresentation) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(metric.label)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(GridTheme.textPrimary)
                    Text(metric.factLabel)
                        .font(.system(size: 9, weight: .semibold, design: .monospaced))
                        .tracking(0.5)
                        .foregroundStyle(factColor(metric.factClass))
                }
                Text(metric.sourceLabel)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            VStack(alignment: .trailing, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 4) {
                    Text(metric.value)
                        .font(.system(.title3, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.textPrimary)
                        .contentTransition(.numericText())
                    Text(metric.unit)
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                HStack(spacing: 5) {
                    Circle()
                        .fill(stateColor(metric.state))
                        .frame(width: 5, height: 5)
                    Text(metric.timingLabel())
                        .font(.caption2)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
            }
        }
        .frame(maxWidth: .infinity, minHeight: 66, alignment: .leading)
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(metric.label), \(metric.value) \(metric.unit), \(metric.factLabel.lowercased()), \(metric.sourceLabel), \(metric.timingLabel())")
    }

    private func factColor(_ factClass: FactClass?) -> Color {
        factClass == .forecast ? GridTheme.forecastViolet : GridTheme.liveCyan
    }

    private func stateColor(_ state: LiveDatumState) -> Color {
        switch state {
        case .current: GridTheme.liveCyan
        case .delayed, .stale, .unavailable: GridTheme.staleAmber
        case .unknown: GridTheme.textTertiary
        }
    }
}

private struct InterconnectorLedger: View {
    let snapshot: GridSnapshot

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            let evidence = LiveFamilyEvidencePresentation.make(
                family: .interconnectors,
                snapshot: snapshot,
                at: context.date
            )

            VStack(alignment: .leading, spacing: 0) {
                SectionLabel("Cross-border flows", trailing: netPosition)
                    .padding(.bottom, 6)

                Text("Named connector readings below are real; paths in the Britain view are schematic and do not trace cable routes.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.bottom, 9)

                if snapshot.interconnectors.isEmpty {
                    Text("No time-aligned interconnector position is available for this frame.")
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                        .overlay(alignment: .bottom) { Hairline() }
                } else {
                    ForEach(snapshot.interconnectors.map(LiveInterconnectorPresentation.make)) { flow in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            VStack(alignment: .leading, spacing: 3) {
                                Text(flow.name)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(GridTheme.textPrimary)
                                Text("\(flow.factLabel) · \(flow.flowDescription)")
                                    .font(.caption2)
                                    .fontDesign(.monospaced)
                                    .foregroundStyle(GridTheme.textTertiary)
                            }
                            Spacer(minLength: 8)
                            VStack(alignment: .trailing, spacing: 3) {
                                Text(flow.magnitude)
                                    .font(.system(.subheadline, design: .monospaced, weight: .semibold))
                                    .foregroundStyle(GridTheme.textPrimary)
                                Text(flow.direction)
                                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                                    .tracking(0.5)
                                    .foregroundStyle(flow.direction == "IMPORT" ? GridTheme.liveCyan : GridTheme.forecastViolet)
                            }
                        }
                        .frame(maxWidth: .infinity, minHeight: 55, alignment: .leading)
                        .overlay(alignment: .bottom) { Hairline() }
                        .accessibilityElement(children: .combine)
                    }
                }

                Text(evidenceLine(evidence))
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .padding(.top, 8)
            }
        }
    }

    private var netPosition: String {
        guard !snapshot.interconnectors.isEmpty else { return "UNAVAILABLE" }
        let net = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts }
        let magnitude = (abs(net) / 1_000).formatted(.number.precision(.fractionLength(1)))
        return "\(magnitude) GW \(net >= 0 ? "IMPORT" : "EXPORT")"
    }

    private func evidenceLine(_ evidence: LiveFamilyEvidencePresentation) -> String {
        let observed = evidence.observedAt.map { "Observed \($0.formatted(.dateTime.hour().minute()))" } ?? "Observation time unavailable"
        return "\(evidence.sourceLabel) · \(observed) · \(evidence.state.displayName)"
    }
}

private struct TransmissionFuelFocusView: View {
    let reading: FuelReading
    let timeline: GridTimeline

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text(reading.fuel.displayName)
                    .font(.headline)
                Spacer()
                Text("#\(reading.rank) in this view")
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

            LiveFuelSparkline(fuel: reading.fuel, timeline: timeline)
                .frame(height: 54)

            Text("\(Int(reading.share * 100))% of transmission-visible supply · \(reading.factClass.rawValue.capitalized)")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(.vertical, 14)
        .overlay(alignment: .top) { Hairline() }
        .overlay(alignment: .bottom) { Hairline() }
    }
}

private struct LiveFuelSparkline: View {
    let fuel: FuelKind
    let timeline: GridTimeline

    var body: some View {
        Canvas { context, size in
            let points = timeline.samples.compactMap { sample in
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
            context.stroke(
                path,
                with: .color(GridTheme.fuel(fuel)),
                style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round)
            )
        }
        .accessibilityHidden(true)
    }
}

private struct SourceFootnote: View {
    let snapshot: GridSnapshot
    let mode: String

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Hairline()
                .padding(.bottom, 4)
            Text("\(timePrefix) \(snapshot.timestamp.formatted(.dateTime.hour().minute())) · Retrieved \(snapshot.retrievedAt.formatted(.dateTime.hour().minute().second()))")
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
            if mode == "LIVE" {
                Text(snapshot.sources.map(\.name).uniqued().joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            } else {
                Text("Timeline frames do not carry time-aligned interconnector or event state; those layers are withheld outside Live.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Text(LiveTruthCopy.mapDisclosure)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var timePrefix: String {
        switch mode {
        case "FORECAST": "Forecast valid"
        case "REPLAY": "Observed frame"
        default: "Observed"
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
        case .stale: "One or more required inputs are delayed. Each measurement below retains its own observation time. \(lastError ?? "Automatic retry is in progress.")"
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
