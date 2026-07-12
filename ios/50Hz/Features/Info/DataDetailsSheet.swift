import SwiftUI

struct DataDetailsSheet: View {
    let snapshot: GridSnapshot
    let mode: String
    @Environment(\.dismiss) private var dismiss
    @State private var inspectedAt = Date()

    private var summary: CurrentDataSummary {
        CurrentDataSummary.resolve(
            freshness: snapshot.freshness,
            fallbackAgeSeconds: snapshot.freshnessAgeSeconds,
            statuses: snapshot.dataStatus,
            evaluatedAt: inspectedAt
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    summarySection

                    if mode != "LIVE" {
                        Label(
                            "These delivery and supply details belong to the latest current snapshot, not the selected \(mode.lowercased()) frame.",
                            systemImage: "clock.arrow.circlepath"
                        )
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                        .accessibilityElement(children: .combine)
                    }

                    familySection
                    supplySection
                    sourceSection
                }
                .padding(GridTheme.horizontalPadding)
                .padding(.bottom, 32)
            }
            .scrollIndicators(.hidden)
            .navigationTitle("Data Details")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(GridTheme.liveCyan)
                }
            }
        }
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
    }

    private var summarySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 9) {
                Circle()
                    .fill(color(for: summary.state))
                    .frame(width: 8, height: 8)
                Text(summary.state.displayName)
                    .font(.system(.title, design: .rounded, weight: .medium))
                    .foregroundStyle(GridTheme.textPrimary)
                Spacer()
                Text("Observed \(ageText(summary.observationAgeSeconds)) ago")
                    .font(.caption)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Text(summaryExplanation)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
            Text("Delivery describes 50Hz receiving a source update. Fact describes when the underlying measurement applies. States and ages below are recalculated on this device when details open.")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
                .lineSpacing(3)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var familySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionLabel("Data families", trailing: "DELIVERY + FACT")
                .padding(.bottom, 10)

            if let statuses = snapshot.dataStatus, !statuses.isEmpty {
                ForEach(statuses.sorted(by: familySort)) { status in
                    familyRow(status)
                }
            } else {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Family-level timing is unavailable")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(GridTheme.textPrimary)
                    Text("This cached snapshot predates the additive data-status contract. Its overall observed age and source list are still shown.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                        .lineSpacing(3)
                }
                .padding(.vertical, 10)
            }
        }
    }

    private func familyRow(_ status: DataFamilyStatus) -> some View {
        let resolved = status.resolved(at: inspectedAt)
        return VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .firstTextBaseline) {
                Text(status.family.displayName)
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                Spacer()
                Text(status.requiredForSnapshot ? "REQUIRED" : "OPTIONAL")
                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                    .tracking(0.6)
                    .foregroundStyle(status.requiredForSnapshot ? GridTheme.liveCyan : GridTheme.textTertiary)
            }

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) {
                    stateLabel("Delivery", value: resolved.deliveryState.displayName, color: resolved.deliveryState.color)
                    stateLabel("Fact", value: resolved.factState.displayName, color: resolved.factState.color)
                }
                VStack(alignment: .leading, spacing: 7) {
                    stateLabel("Delivery", value: resolved.deliveryState.displayName, color: resolved.deliveryState.color)
                    stateLabel("Fact", value: resolved.factState.displayName, color: resolved.factState.color)
                }
            }

            if status.seriesCount == 0 {
                Text("No series was available when this snapshot was evaluated.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            } else {
                if let observedAt = status.observedAt { detailRow("Observed", dateText(observedAt)) }
                if let publishedAt = status.publishedAt { detailRow("Published", dateText(publishedAt)) }
                if let retrievedAt = status.retrievedAt { detailRow("Retrieved", dateText(retrievedAt)) }
                if let validTo = status.validTo { detailRow("Valid through", dateText(validTo)) }
                if let age = resolved.observationAgeSeconds { detailRow("Observed age now", ageText(age)) }
                if let age = resolved.retrievalAgeSeconds { detailRow("Delivery age now", ageText(age)) }
                detailRow("Server evaluated", dateText(status.evaluatedAt))
                detailRow("Expected cadence", durationText(status.expectedCadenceSeconds))
                detailRow("Series represented", status.seriesCount.formatted())
            }

            Text(status.metricIDs.joined(separator: " · "))
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(GridTheme.textTertiary)
                .textSelection(.enabled)

            Hairline()
                .padding(.top, 4)
        }
        .padding(.vertical, 12)
    }

    @ViewBuilder
    private var supplySection: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel(
                "Supply accounting",
                trailing: snapshot.supply?.isComplete == true ? "COMPLETE" : "PARTIAL BOUNDARY"
            )

            if let supply = snapshot.supply {
                Text(supply.boundary)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)

                detailRow("Generation data", supply.generationDataAvailable ? "Available" : "Unavailable")
                detailRow("Interconnector data", supply.interconnectorDataAvailable ? "Available" : "Unavailable")
                detailRow("Domestic generation", megawatts(supply.domesticGenerationMW))
                detailRow("Gross imports", megawatts(supply.grossImportsMW))
                detailRow("Gross exports", megawatts(supply.grossExportsMW))
                detailRow(
                    supply.netImportsMW >= 0 ? "Net imports" : "Net exports",
                    megawatts(abs(supply.netImportsMW))
                )
                detailRow("Storage generating", megawatts(supply.storageGenerationMW))
                detailRow(
                    "Storage charging",
                    supply.storageChargingMW.map(megawatts) ?? "Unavailable"
                )
                detailRow("Displayed mix total", megawatts(supply.legacyDisplayedGenerationMW))

                Text("Gross imports and gross exports are kept separate. Net imports are imports minus exports; a negative result is shown as net exports. Storage charging is unavailable when FUELINST cannot provide a complete charging measure.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)

                Text(supply.note)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineSpacing(3)

                Text("Displayed mix basis: \(supply.legacyMixBasis) · \(supply.methodologyVersion)")
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(GridTheme.textTertiary)
                    .textSelection(.enabled)
            } else {
                Text("Detailed supply accounting is unavailable in this cached snapshot. The displayed mix retains its original compatibility contract and must not be read as a complete Great Britain supply balance.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)
            }
        }
    }

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            SectionLabel("Sources", trailing: "\(snapshot.sources.count)")
            ForEach(snapshot.sources) { source in
                if let url = source.documentationURL {
                    Link(destination: url) { sourceRow(source, showsLink: true) }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens source documentation in your browser")
                } else {
                    sourceRow(source, showsLink: false)
                }
            }
        }
    }

    private func sourceRow(_ source: SourceReference, showsLink: Bool) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: "doc.text.magnifyingglass")
                .foregroundStyle(GridTheme.liveCyan)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(source.name)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(GridTheme.textPrimary)
                Text("\(source.dataset) · observed \(dateText(source.observedAt)) · retrieved \(dateText(source.retrievedAt))")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            if showsLink {
                Image(systemName: "arrow.up.right")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .frame(minHeight: 52)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
    }

    private func stateLabel(_ label: String, value: String, color: Color) -> some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text("\(label): \(value)")
                .font(.caption.weight(.medium))
                .foregroundStyle(GridTheme.textSecondary)
        }
        .accessibilityElement(children: .combine)
    }

    private func detailRow(_ label: String, _ value: String) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline) {
                Text(label)
                Spacer(minLength: 12)
                Text(value).multilineTextAlignment(.trailing)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                Text(value)
            }
        }
        .font(.caption)
        .foregroundStyle(GridTheme.textSecondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value)")
    }

    private var summaryExplanation: String {
        if snapshot.dataStatus?.contains(where: \.requiredForSnapshot) != true {
            return switch summary.state {
            case .current: "This snapshot’s legacy overall freshness state is current. Family-level delivery and fact states are unavailable."
            case .delayed: "This snapshot’s legacy overall freshness state is delayed. Family-level delivery and fact states are unavailable."
            case .offline: "This snapshot is being shown from offline cache. Family-level delivery and fact states are unavailable."
            }
        }
        return switch summary.state {
        case .current: "All required data families are available without a delayed or stale state."
        case .delayed: "At least one required family is delayed or stale. Confirm its fact and delivery timing below before relying on it."
        case .offline: "A required family is unavailable, or this snapshot is being shown from offline cache."
        }
    }

    private func familySort(_ lhs: DataFamilyStatus, _ rhs: DataFamilyStatus) -> Bool {
        if lhs.requiredForSnapshot != rhs.requiredForSnapshot { return lhs.requiredForSnapshot }
        return lhs.family.sortOrder < rhs.family.sortOrder
    }

    private func color(for state: CurrentDataState) -> Color {
        switch state {
        case .current: GridTheme.liveCyan
        case .delayed, .offline: GridTheme.staleAmber
        }
    }

    private func ageText(_ seconds: Int) -> String {
        if seconds < 60 { return "<1 min" }
        if seconds < 3_600 { return "\(seconds / 60) min" }
        let hours = seconds / 3_600
        let minutes = (seconds % 3_600) / 60
        return minutes == 0 ? "\(hours) hr" : "\(hours) hr \(minutes) min"
    }

    private func durationText(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds) sec" }
        if seconds % 3_600 == 0 { return "\(seconds / 3_600) hr" }
        return "\(seconds / 60) min"
    }

    private func dateText(_ date: Date) -> String {
        date.formatted(.dateTime.day().month(.abbreviated).hour().minute().second())
    }

    private func megawatts(_ value: Double) -> String {
        "\(value.formatted(.number.precision(.fractionLength(0)))) MW"
    }
}

private extension GridDataFamily {
    var displayName: String {
        switch self {
        case .generation: "Generation"
        case .demand: "Demand"
        case .frequency: "Frequency"
        case .interconnectors: "Interconnectors"
        case .carbon: "Carbon intensity"
        case .other: "Other data"
        }
    }

    var sortOrder: Int {
        switch self {
        case .generation: 0
        case .demand: 1
        case .carbon: 2
        case .frequency: 3
        case .interconnectors: 4
        case .other: 5
        }
    }
}

private extension DataDeliveryState {
    var displayName: String {
        switch self {
        case .healthy: "On schedule"
        case .delayed: "Delayed"
        case .stale: "Stale"
        case .unavailable: "Unavailable"
        case .unknown: "Unknown"
        }
    }

    var color: Color {
        switch self {
        case .healthy: GridTheme.liveCyan
        case .delayed, .stale: GridTheme.staleAmber
        case .unavailable, .unknown: GridTheme.textTertiary
        }
    }
}

private extension DataFactState {
    var displayName: String {
        switch self {
        case .live: "Current"
        case .delayed: "Delayed"
        case .stale: "Stale"
        case .unavailable: "Unavailable"
        case .unknown: "Unknown"
        }
    }

    var color: Color {
        switch self {
        case .live: GridTheme.liveCyan
        case .delayed, .stale: GridTheme.staleAmber
        case .unavailable, .unknown: GridTheme.textTertiary
        }
    }
}

private extension SourceReference {
    var documentationURL: URL? {
        let identity = "\(id) \(name) \(dataset)".lowercased()
        if identity.contains("elexon") || ["fuelinst", "indo", "freq"].contains(dataset.lowercased()) {
            return URL(string: "https://bmrs.elexon.co.uk/api-documentation")
        }
        if identity.contains("neso") || identity.contains("carbon") {
            return URL(string: "https://carbonintensity.org.uk/")
        }
        return nil
    }
}
