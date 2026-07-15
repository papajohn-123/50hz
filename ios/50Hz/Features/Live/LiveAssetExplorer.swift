import SwiftUI

struct LiveAssetLayerStatus: View {
    let response: GridAssetMapResponse?
    let isLoading: Bool
    let errorMessage: String?
    let isLiveMode: Bool
    let onBrowse: () -> Void
    let onRetry: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                SectionLabel("Source-located sites", trailing: trailingLabel)
                Spacer(minLength: 4)
                if isLoading {
                    ProgressView()
                        .controlSize(.small)
                        .tint(GridTheme.liveCyan)
                        .accessibilityLabel("Loading generator locations")
                } else if response != nil {
                    Button("Browse", action: onBrowse)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(minHeight: 44)
                        .buttonStyle(.plain)
                }
            }

            if let errorMessage {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(errorMessage)
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                    Spacer(minLength: 4)
                    Button("Retry", action: onRetry)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(minHeight: 44)
                        .buttonStyle(.plain)
                }
            }

            Text(scopeCopy)
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)

            if let response {
                sourceDeliverySummary(response)

                if response.isTruncated {
                    Label(
                        "Map response limit reached: showing \(response.returnedCount.formatted()) of \(response.totalCount.formatted()) matching sites. Browse and map results are not the complete matching set.",
                        systemImage: "exclamationmark.triangle.fill"
                    )
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    private var trailingLabel: String {
        guard isLiveMode else { return "LIVE LAYER" }
        guard let response else { return isLoading ? "LOADING" : "NOT LOADED" }
        return response.sourceStatus.state.uppercased()
    }

    private var scopeCopy: String {
        guard isLiveMode else {
            return "Generator locations are a present-day reference layer and are hidden from replay and forecast frames."
        }
        guard let response else {
            return isLoading
                ? "Loading the DESNZ renewable and storage site register. Elexon unit records are linked only where the evidence is strong."
                : "The source-located site layer has not been loaded for this live view."
        }
        return response.boundary + ". " + response.disclaimer
    }

    private func sourceDeliverySummary(_ response: GridAssetMapResponse) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Circle()
                    .fill(sourceStateColor(response.sourceStatus.state))
                    .frame(width: 6, height: 6)
                    .accessibilityHidden(true)
                Text("Source state: \(response.sourceStatus.state)")
            }
            Text("Last successful delivery: \(lastSuccessLabel(response.sourceStatus.lastSuccessfulAt))")
            Text("Response: \(response.returnedCount.formatted()) returned of \(response.totalCount.formatted()) matching · \(response.validLocatedAssets.count.formatted()) usable map points")
            Text("Source register: \(response.sourceStatus.locatedAssetCount.formatted()) located sites · \(response.sourceStatus.assetReferenceCount.formatted()) Elexon asset references")
        }
        .font(.caption2.monospacedDigit())
        .foregroundStyle(GridTheme.textSecondary)
        .accessibilityElement(children: .combine)
    }

    private func lastSuccessLabel(_ date: Date?) -> String {
        guard let date else { return "not reported" }
        return date.formatted(.dateTime.day().month(.abbreviated).year().hour().minute())
    }

    private func sourceStateColor(_ state: String) -> Color {
        switch state.lowercased() {
        case "current", "healthy", "available": GridTheme.liveCyan
        case "delayed", "stale", "degraded": GridTheme.staleAmber
        default: GridTheme.textTertiary
        }
    }
}

struct GeneratorExplorerSheet: View {
    let title: String
    let assets: [LiveMapAsset]
    let assetClient: any GridAssetProviding

    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                if filteredAssets.isEmpty {
                    ContentUnavailableView.search(text: query)
                        .listRowBackground(Color.clear)
                } else {
                    ForEach(filteredAssets) { asset in
                        NavigationLink {
                            LiveAssetInspector(
                                asset: asset,
                                assetClient: assetClient,
                                embedsNavigationStack: false
                            )
                        } label: {
                            assetRow(asset)
                        }
                        .listRowBackground(GridTheme.surface)
                    }
                }

                Section {
                    Text("Map points come from DESNZ REPD. A site can be real and source-located without having an Elexon BM-unit match or current operating evidence.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .listRowBackground(GridTheme.surface)
            }
            .scrollContentBackground(.hidden)
            .background(GridTheme.background)
            .searchable(text: $query, prompt: "Site, operator, technology or region")
            .navigationTitle(title)
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

    private var filteredAssets: [LiveMapAsset] {
        let normalized = query.trimmingCharacters(in: .whitespacesAndNewlines)
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
        return assets
            .filter { normalized.isEmpty || $0.searchableText.localizedStandardContains(normalized) }
            .sorted {
                if $0.capacityMW != $1.capacityMW {
                    return ($0.capacityMW ?? -1) > ($1.capacityMW ?? -1)
                }
                return $0.name.localizedStandardCompare($1.name) == .orderedAscending
            }
    }

    private func assetRow(_ asset: LiveMapAsset) -> some View {
        HStack(spacing: 12) {
            Circle()
                .fill(asset.fuel.map(GridTheme.fuel) ?? GridTheme.liveCyan)
                .frame(width: 8, height: 8)
                .accessibilityHidden(true)

            VStack(alignment: .leading, spacing: 3) {
                Text(asset.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.textPrimary)
                Text([asset.technology, asset.region].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineLimit(1)
            }

            Spacer(minLength: 8)

            if let capacity = asset.capacityMW {
                Text("\(capacity.formatted(.number.precision(.fractionLength(0)))) MW")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .frame(minHeight: 48)
        .accessibilityElement(children: .combine)
    }
}

struct LiveAssetInspector: View {
    let asset: LiveMapAsset
    let assetClient: any GridAssetProviding
    let embedsNavigationStack: Bool

    @Environment(\.dismiss) private var dismiss
    @State private var detail: GridAssetDetailResponse?
    @State private var isLoading = false
    @State private var detailError: String?

    init(
        asset: LiveMapAsset,
        assetClient: any GridAssetProviding,
        embedsNavigationStack: Bool = true
    ) {
        self.asset = asset
        self.assetClient = assetClient
        self.embedsNavigationStack = embedsNavigationStack
    }

    @ViewBuilder
    var body: some View {
        Group {
            if embedsNavigationStack {
                NavigationStack {
                    inspectorContent
                        .toolbar {
                            ToolbarItem(placement: .topBarTrailing) {
                                Button("Done") { dismiss() }
                                    .foregroundStyle(GridTheme.liveCyan)
                            }
                        }
                }
            } else {
                inspectorContent
            }
        }
        .preferredColorScheme(.dark)
        .task(id: asset.id) { await fetchDetail() }
    }

    private var inspectorContent: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                header
                referenceSection
                operatingEvidenceSection

                if let detail, !detail.bmUnits.isEmpty {
                    bmUnitSection(detail.bmUnits)
                }

                if let detail, !detail.limitations.isEmpty {
                    limitationsSection(detail.limitations)
                }

                if isLoading {
                    HStack(spacing: 10) {
                        ProgressView().tint(GridTheme.liveCyan)
                        Text("Loading linked Elexon evidence…")
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    }
                } else if let detailError {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(detailError)
                            .font(.caption)
                            .foregroundStyle(GridTheme.staleAmber)
                        Button("Retry evidence", action: loadDetail)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(minHeight: 44)
                    }
                }
            }
            .padding(GridTheme.horizontalPadding)
        }
        .scrollIndicators(.hidden)
        .background(GridTheme.background)
        .navigationTitle("Energy site")
        .navigationBarTitleDisplayMode(.inline)
    }

    private var presentedAsset: LiveMapAsset {
        detail.map { LiveMapAsset(item: $0.asset) } ?? asset
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text(presentedAsset.name)
                .font(.title2.weight(.medium))
                .foregroundStyle(GridTheme.textPrimary)
            Text([presentedAsset.technology, presentedAsset.operatorName].compactMap { $0 }.joined(separator: " · "))
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
            HStack(spacing: 8) {
                evidenceBadge("REFERENCE", color: GridTheme.liveCyan)
                evidenceBadge("NO LIVE UNIT METER", color: GridTheme.staleAmber)
            }
        }
    }

    private var referenceSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionLabel("Published site record", trailing: presentedAsset.lifecycle.rawValue.uppercased())
                .padding(.bottom, 6)
            inspectorRow(
                "Capacity",
                presentedAsset.capacityMW.map { "\($0.formatted(.number.precision(.fractionLength(0)))) MW" } ?? "Not reported"
            )
            inspectorRow("Region", [presentedAsset.region, presentedAsset.country].compactMap { $0 }.joined(separator: ", ").nilIfEmpty ?? "Not reported")
            inspectorRow("Coordinate", "\(presentedAsset.latitude.formatted(.number.precision(.fractionLength(3)))), \(presentedAsset.longitude.formatted(.number.precision(.fractionLength(3))))")
            inspectorRow("Precision", presentedAsset.coordinatePrecision ?? "Source point")
            inspectorRow("Retrieved", presentedAsset.observedAt.formatted(.dateTime.day().month().year().hour().minute()))
            inspectorRow("Source", presentedAsset.coordinateSource?.publisher ?? presentedAsset.sourceID)

            if let source = presentedAsset.coordinateSource {
                Link(destination: source.canonicalURL) {
                    Label("Open the official dataset", systemImage: "arrow.up.right.square")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(minHeight: 44)
                }
                .accessibilityHint("Opens the publisher page in your browser")

                Text(source.attribution + " " + source.licence)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    @ViewBuilder
    private var operatingEvidenceSection: some View {
        let plan = detail?.plan ?? presentedAsset.operatingEvidence?.participantSubmittedPlan.map { [$0] } ?? []
        let settled = detail?.settledMetered ?? presentedAsset.operatingEvidence?.latestSettledMetered.map { [$0] } ?? []

        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Operating evidence", trailing: "ELEXON")
            Text("Plans are participant submissions. Settled metered energy is delayed. Neither is a live generator meter.")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)

            if plan.isEmpty && settled.isEmpty {
                Text(presentedAsset.linkedBMUnitCount == 0
                     ? "No BM unit has been conservatively linked to this site."
                     : "Linked BM units do not currently have a usable plan or settled interval in this response.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            } else {
                ForEach(Array(plan.prefix(6).enumerated()), id: \.offset) { _, evidence in
                    evidenceCard(
                        label: "PARTICIPANT PLAN",
                        value: AssetEvidencePresentation.signedMeasurement(
                            evidence.levelMW,
                            unit: "MW",
                            direction: evidence.direction
                        ),
                        timing: "At \(evidence.at.formatted(.dateTime.day().month().hour().minute())) · SP \(evidence.settlementPeriod)",
                        caveat: evidence.caveat,
                        color: GridTheme.forecastViolet
                    )
                }
                ForEach(Array(settled.prefix(6).enumerated()), id: \.offset) { _, evidence in
                    evidenceCard(
                        label: "SETTLED METERED",
                        value: AssetEvidencePresentation.signedMeasurement(
                            evidence.averageMW,
                            unit: "MW average",
                            direction: evidence.direction
                        ),
                        timing: "\(AssetEvidencePresentation.signedMeasurement(evidence.energyMWh, unit: "MWh", direction: evidence.direction)) settled energy · \(evidence.intervalStart.formatted(.dateTime.day().month().hour().minute()))–\(evidence.intervalEnd.formatted(.dateTime.hour().minute())) · SP \(evidence.settlementPeriod)",
                        caveat: evidence.caveat,
                        color: GridTheme.liveCyan
                    )
                }
            }
        }
    }

    private func bmUnitSection(_ units: [GridBMUnitSummary]) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Linked BM units", trailing: units.count.formatted())
            ForEach(units) { unit in
                VStack(alignment: .leading, spacing: 4) {
                    Text(unit.name ?? unit.nationalGridBMUnit)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(GridTheme.textPrimary)
                    Text([unit.nationalGridBMUnit, unit.leadPartyName, unit.fuelType].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                    Text("\(unit.matchMethod) · \((unit.matchConfidence * 100).formatted(.number.precision(.fractionLength(0))))% match confidence")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(GridTheme.textSecondary)
                }
                .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                .overlay(alignment: .bottom) { Hairline() }
            }
        }
    }

    private func limitationsSection(_ limitations: [String]) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel("Limitations", trailing: "READ THIS")
            ForEach(limitations, id: \.self) { limitation in
                Label(limitation, systemImage: "info.circle")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
    }

    private func evidenceCard(
        label: String,
        value: String,
        timing: String,
        caveat: String,
        color: Color
    ) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(label)
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .tracking(0.6)
                .foregroundStyle(color)
            Text(value)
                .font(.title3.monospacedDigit().weight(.medium))
                .foregroundStyle(GridTheme.textPrimary)
            Text(timing)
                .font(.caption2.monospacedDigit())
                .foregroundStyle(GridTheme.textSecondary)
            Text(caveat)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).stroke(color.opacity(0.18), lineWidth: 1))
    }

    private func evidenceBadge(_ label: String, color: Color) -> some View {
        Text(label)
            .font(.system(size: 8, weight: .semibold, design: .monospaced))
            .tracking(0.5)
            .foregroundStyle(color)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(color.opacity(0.10), in: Capsule())
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
        .frame(maxWidth: .infinity, minHeight: 46)
        .overlay(alignment: .bottom) { Hairline() }
    }

    private func loadDetail() {
        Task { await fetchDetail() }
    }

    @MainActor
    private func fetchDetail() async {
        guard !isLoading else { return }
        isLoading = true
        detailError = nil
        defer { isLoading = false }
        do {
            detail = try await assetClient.assetDetail(id: asset.id)
        } catch is CancellationError {
            return
        } catch GridAPIError.cancelled {
            return
        } catch {
            detailError = "The site record is available, but linked Elexon evidence could not be refreshed."
        }
    }
}

enum AssetEvidencePresentation {
    static func signedMeasurement(_ value: Double, unit: String, direction: String) -> String {
        let normalizedValue = abs(value) < 0.000_001 ? 0 : value
        let sign: String
        if normalizedValue > 0 {
            sign = "+"
        } else if normalizedValue < 0 {
            sign = "−"
        } else {
            sign = ""
        }
        let magnitude = abs(normalizedValue).formatted(.number.precision(.fractionLength(0)))
        return "\(sign)\(magnitude) \(unit) · \(directionLabel(direction))"
    }

    private static func directionLabel(_ direction: String) -> String {
        switch direction.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "export", "exporting", "generation", "generating": "export"
        case "import", "importing", "demand", "consuming": "import"
        case "idle", "zero", "none": "idle"
        case "": "direction not reported"
        default: direction.lowercased()
        }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
