import SwiftUI

struct LiveView: View {
    @EnvironmentObject private var model: AppModel
    @State private var sharePayload: GridShareCardPayload?
    @State private var isDataDetailsPresented = false
    @State private var isGridDetailsPresented = false
    @State private var isEventListPresented = false
    @State private var selectedMapAsset: LiveMapAsset?
    @State private var selectedMapCluster: LiveMapAssetCluster?
    @State private var isGeneratorExplorerPresented = false
    @State private var assetMapResponse: GridAssetMapResponse?
    @State private var isAssetMapLoading = false
    @State private var assetMapError: String?

    private let assetClient: any GridAssetProviding

    init(assetClient: any GridAssetProviding = HTTPGridAssetClient()) {
        self.assetClient = assetClient
    }

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
        .sheet(isPresented: $isGridDetailsPresented) {
            if let snapshot = model.presentedSnapshot {
                GridStateDetailsSheet(
                    snapshot: snapshot,
                    mode: model.timelineModeLabel,
                    selectedFuel: $model.selectedFuel,
                    assetResponse: model.timelineModeLabel == "LIVE" ? assetMapResponse : nil,
                    isAssetMapLoading: model.timelineModeLabel == "LIVE" && isAssetMapLoading,
                    assetMapError: model.timelineModeLabel == "LIVE" ? assetMapError : nil,
                    eventCount: model.events.count,
                    eventsUnavailable: model.eventsError != nil,
                    onBrowseSites: { isGeneratorExplorerPresented = true },
                    onBrowseEvents: { isEventListPresented = true },
                    onRetryAssets: { Task { await loadAssetMap(force: true) } }
                )
                .presentationDetents([.medium, .large])
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
            LiveAssetInspector(asset: asset, assetClient: assetClient)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
        .sheet(item: $selectedMapCluster) { cluster in
            GeneratorExplorerSheet(
                title: "Sites in this area",
                assets: cluster.assets,
                assetClient: assetClient
            )
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
            .presentationBackground(GridTheme.background)
        }
        .sheet(isPresented: $isGeneratorExplorerPresented) {
            GeneratorExplorerSheet(
                title: "Energy sites",
                assets: liveMapAssets,
                assetClient: assetClient
            )
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
            .presentationBackground(GridTheme.background)
        }
        .task(id: model.timelineModeLabel) {
            guard model.timelineModeLabel == "LIVE" else { return }
            await loadAssetMap()
        }
    }

    private func loadedContent(snapshot: GridSnapshot, model: AppModel) -> some View {
        let isForecast = model.selectedSample?.factClass == .forecast
        let mapOpacity = snapshot.freshness == .offline ? 0.62 : (snapshot.freshness == .stale ? 0.82 : 1)

        return ZStack {
            BritainGridMap(
                snapshot: snapshot,
                selectedFuel: model.selectedFuel,
                isForecast: isForecast,
                assets: model.timelineModeLabel == "LIVE" ? liveMapAssets : [],
                onAssetTap: { selectedMapAsset = $0 },
                onClusterInspect: { selectedMapCluster = $0 }
            )
            .opacity(mapOpacity)
            .ignoresSafeArea(edges: .top)

            LinearGradient(
                colors: [GridTheme.background.opacity(0.96), GridTheme.background.opacity(0.18), .clear],
                startPoint: .top,
                endPoint: .bottom
            )
            .frame(height: 132)
            .frame(maxHeight: .infinity, alignment: .top)
            .allowsHitTesting(false)

            VStack(spacing: 0) {
                GridCanvasHeader(
                    snapshot: snapshot,
                    mode: model.timelineModeLabel,
                    selectedFuel: $model.selectedFuel,
                    availableFuels: snapshot.generation.map(\.fuel),
                    hasSites: !liveMapAssets.isEmpty,
                    onStatusTap: { isDataDetailsPresented = true },
                    onSearch: { isGeneratorExplorerPresented = true },
                    onShare: { sharePayload = .current(snapshot) },
                    onEvents: { isEventListPresented = true }
                )

                Spacer(minLength: 0)

                GridNowDock(
                    snapshot: snapshot,
                    mode: model.timelineModeLabel,
                    eventCount: model.events.count,
                    hasEventError: model.eventsError != nil,
                    onExplain: { model.isAskPresented = true },
                    onOpenDetails: { isGridDetailsPresented = true }
                )

                if let timeline = model.displayTimeline {
                    GridTimelineView(timeline: timeline, selectedTime: $model.selectedTime)
                }
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

    private var liveMapAssets: [LiveMapAsset] {
        assetMapResponse?.validLocatedAssets.map(LiveMapAsset.init(item:)) ?? []
    }

    @MainActor
    private func loadAssetMap(force: Bool = false) async {
        guard model.timelineModeLabel == "LIVE" else { return }
        guard force || assetMapResponse == nil else { return }
        guard !isAssetMapLoading else { return }
        isAssetMapLoading = true
        assetMapError = nil
        defer { isAssetMapLoading = false }

        do {
            if force {
                await assetClient.invalidateMapCache()
            }
            assetMapResponse = try await assetClient.mapAssets()
        } catch is CancellationError {
            return
        } catch GridAPIError.cancelled {
            return
        } catch {
            assetMapError = "Generator locations could not be refreshed. The national grid view remains available."
        }
    }
}

private struct GridCanvasHeader: View {
    let snapshot: GridSnapshot
    let mode: String
    @Binding var selectedFuel: FuelKind?
    let availableFuels: [FuelKind]
    let hasSites: Bool
    let onStatusTap: () -> Void
    let onSearch: () -> Void
    let onShare: () -> Void
    let onEvents: () -> Void

    @Environment(\.openInfo) private var openInfo

    var body: some View {
        TimelineView(.periodic(from: .now, by: 30)) { context in
            HStack(spacing: 6) {
                Text("50Hz")
                    .font(.system(.title2, design: .rounded, weight: .bold))
                    .tracking(-0.9)
                    .foregroundStyle(GridTheme.textPrimary)
                    .accessibilityAddTraits(.isHeader)

                Button(action: onStatusTap) {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(statusColor(at: context.date))
                            .frame(width: 6, height: 6)
                            .shadow(color: statusColor(at: context.date).opacity(0.7), radius: 4)
                        Text(statusLabel(at: context.date))
                            .font(.caption2.weight(.semibold))
                            .fontDesign(.monospaced)
                            .lineLimit(1)
                    }
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.horizontal, 10)
                    .frame(minHeight: 44)
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Data status, \(statusLabel(at: context.date))")
                .accessibilityHint("Opens source timing")

                Spacer(minLength: 4)

                Button(action: onSearch) {
                    Image(systemName: "magnifyingglass")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(hasSites ? GridTheme.textPrimary : GridTheme.textTertiary)
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .disabled(!hasSites)
                .accessibilityLabel("Find an energy site")

                Menu {
                    Button {
                        selectedFuel = nil
                    } label: {
                        Label("All energy", systemImage: selectedFuel == nil ? "checkmark" : "circle")
                    }

                    ForEach(uniqueFuels, id: \.self) { fuel in
                        Button {
                            selectedFuel = fuel
                        } label: {
                            Label(fuel.displayName, systemImage: selectedFuel == fuel ? "checkmark" : "circle")
                        }
                    }

                    Divider()

                    Button(action: onEvents) {
                        Label("Reported events", systemImage: "exclamationmark.bubble")
                    }
                    Button(action: onShare) {
                        Label("Share grid state", systemImage: "square.and.arrow.up")
                    }
                    Button { openInfo() } label: {
                        Label("Information and help", systemImage: "info.circle")
                    }
                } label: {
                    Image(systemName: selectedFuel == nil ? "line.3.horizontal.decrease" : "line.3.horizontal.decrease.circle.fill")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(selectedFuel.map(GridTheme.fuel) ?? GridTheme.textPrimary)
                        .frame(width: 44, height: 44)
                        .contentShape(Circle())
                }
                .accessibilityLabel("Map layers and actions")
            }
            .padding(.horizontal, 16)
            .padding(.top, 2)
        }
    }

    private var uniqueFuels: [FuelKind] {
        availableFuels.reduce(into: []) { result, fuel in
            if !result.contains(fuel) { result.append(fuel) }
        }
    }

    private func statusLabel(at date: Date) -> String {
        switch mode {
        case "FORECAST":
            return "Forecast \(snapshot.timestamp.formatted(.dateTime.hour().minute()))"
        case "REPLAY":
            return "Replay \(snapshot.timestamp.formatted(.dateTime.hour().minute()))"
        default:
            let state = LiveFreshnessPresentation.make(snapshot: snapshot, at: date)
            return state.hasConcern ? "Delayed" : "Live"
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

private struct GridNowDock: View {
    let snapshot: GridSnapshot
    let mode: String
    let eventCount: Int
    let hasEventError: Bool
    let onExplain: () -> Void
    let onOpenDetails: () -> Void

    private var insight: GridPrimaryInsight {
        GridPrimaryInsight.make(snapshot: snapshot)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(insight.title)
                    .font(.system(.title2, design: .rounded, weight: .medium))
                    .tracking(-0.65)
                    .foregroundStyle(GridTheme.textPrimary)
                    .lineLimit(2)
                Spacer(minLength: 4)
                Button(action: onExplain) {
                    Label("Why?", systemImage: "sparkles")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(insight.accent.color)
                        .frame(minHeight: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(insight.contextualQuestion)
                .accessibilityHint("Explains this selected grid state using supplied evidence")
            }

            Text(insight.detail)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: 0) {
                metric(
                    label: "Frequency",
                    value: snapshot.frequency?.value.formatted(.number.precision(.fractionLength(2))) ?? "—",
                    unit: snapshot.frequency == nil ? "" : "Hz"
                )
                divider
                metric(
                    label: "Demand",
                    value: (snapshot.demand.value / 1_000).formatted(.number.precision(.fractionLength(1))),
                    unit: "GW"
                )
                divider
                metric(
                    label: "Carbon",
                    value: snapshot.carbonIntensity.formatted(),
                    unit: "g/kWh"
                )
            }

            Button(action: onOpenDetails) {
                HStack(spacing: 8) {
                    Text(mode == "LIVE" ? "Explore the grid" : "Inspect this frame")
                    if mode == "LIVE", eventCount > 0 {
                        Text("· \(eventCount) event\(eventCount == 1 ? "" : "s")")
                            .foregroundStyle(hasEventError ? GridTheme.staleAmber : GridTheme.textTertiary)
                    }
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.up")
                        .font(.caption2.weight(.bold))
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.textSecondary)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens generation mix, events, flows and source details")
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.top, 17)
        .padding(.bottom, 5)
        .background(.ultraThinMaterial.opacity(0.88))
        .overlay(alignment: .top) { Hairline() }
    }

    private func metric(label: String, value: String, unit: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                .tracking(0.7)
                .foregroundStyle(GridTheme.textTertiary)
            HStack(alignment: .firstTextBaseline, spacing: 3) {
                Text(value)
                    .font(.system(.subheadline, design: .monospaced, weight: .semibold))
                    .foregroundStyle(GridTheme.textPrimary)
                    .contentTransition(.numericText())
                Text(unit)
                    .font(.system(size: 8, design: .monospaced))
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value) \(unit)")
    }

    private var divider: some View {
        Rectangle()
            .fill(GridTheme.hairline)
            .frame(width: 1, height: 30)
            .padding(.trailing, 12)
            .accessibilityHidden(true)
    }
}

private struct GridStateDetailsSheet: View {
    let snapshot: GridSnapshot
    let mode: String
    @Binding var selectedFuel: FuelKind?
    let assetResponse: GridAssetMapResponse?
    let isAssetMapLoading: Bool
    let assetMapError: String?
    let eventCount: Int
    let eventsUnavailable: Bool
    let onBrowseSites: () -> Void
    let onBrowseEvents: () -> Void
    let onRetryAssets: () -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 25) {
                    LiveMeasurementLedger(snapshot: snapshot, mode: mode)

                    if !snapshot.generation.isEmpty {
                        VStack(alignment: .leading, spacing: 12) {
                            SectionLabel(
                                LiveTruthCopy.supplyTitle,
                                trailing: "\((snapshot.totalGenerationMW / 1_000).formatted(.number.precision(.fractionLength(1)))) GW"
                            )
                            GenerationMixBar(readings: snapshot.generation, selectedFuel: selectedFuel)
                            FuelFilter(readings: snapshot.generation, selection: $selectedFuel)
                        }
                    }

                    if mode == "LIVE" {
                        Button(action: onBrowseEvents) {
                            HStack(spacing: 12) {
                                Image(systemName: "exclamationmark.bubble")
                                    .foregroundStyle(eventsUnavailable ? GridTheme.staleAmber : GridTheme.liveCyan)
                                Text("Reported events")
                                    .font(.subheadline.weight(.semibold))
                                Spacer()
                                Text(eventCount.formatted())
                                    .font(.subheadline.monospacedDigit())
                                    .foregroundStyle(GridTheme.textSecondary)
                                Image(systemName: "chevron.right")
                                    .font(.caption)
                                    .foregroundStyle(GridTheme.textTertiary)
                            }
                            .frame(minHeight: 52)
                            .contentShape(Rectangle())
                            .overlay(alignment: .bottom) { Hairline() }
                        }
                        .buttonStyle(.plain)

                        InterconnectorLedger(snapshot: snapshot)

                        LiveAssetLayerStatus(
                            response: assetResponse,
                            isLoading: isAssetMapLoading,
                            errorMessage: assetMapError,
                            isLiveMode: true,
                            onBrowse: onBrowseSites,
                            onRetry: onRetryAssets
                        )
                    }

                    SourceFootnote(snapshot: snapshot, mode: mode)
                        .padding(.bottom, 20)
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
                .padding(.top, 12)
            }
            .scrollIndicators(.hidden)
            .background(GridTheme.background)
            .navigationTitle("Grid state")
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
