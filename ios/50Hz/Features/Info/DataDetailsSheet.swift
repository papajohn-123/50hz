import SwiftUI

struct DataDetailsSheet: View {
    let snapshot: GridSnapshot
    let mode: String
    @Environment(\.dismiss) private var dismiss
    @State private var inspectedAt = Date()
    @State private var supplySort: SupplyTableSort = .output
    @State private var interconnectorSort: InterconnectorTableSort = .magnitude

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
                    technicalTablesSection
                    inspectionSection
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

    private var technicalTablesSection: some View {
        VStack(alignment: .leading, spacing: 26) {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .center) {
                    SectionLabel("Supply table", trailing: "DISPLAYED MIX")
                    Spacer(minLength: 10)
                    Menu {
                        Picker("Sort supply", selection: $supplySort) {
                            ForEach(SupplyTableSort.allCases) { sort in
                                Text(sort.label).tag(sort)
                            }
                        }
                    } label: {
                        Label(supplySort.label, systemImage: "arrow.up.arrow.down")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(minHeight: 44)
                    }
                    .accessibilityLabel("Sort supply table by \(supplySort.label)")
                }

                if snapshot.generation.isEmpty {
                    Text("No time-aligned supply rows are available in this current snapshot.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(minHeight: 44, alignment: .leading)
                } else {
                    ForEach(sortedGeneration) { reading in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(reading.fuel.displayName)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(GridTheme.textPrimary)
                                Text("\((reading.share * 100).formatted(.number.precision(.fractionLength(0...2))))% of displayed mix · \(reading.factClass.rawValue)")
                                    .font(.caption2)
                                    .foregroundStyle(GridTheme.textTertiary)
                            }
                            Spacer(minLength: 10)
                            Text(exactMegawatts(reading.megawatts))
                                .font(.subheadline)
                                .fontDesign(.monospaced)
                                .monospacedDigit()
                                .foregroundStyle(GridTheme.textPrimary)
                        }
                        .frame(minHeight: 50)
                        .overlay(alignment: .bottom) { Hairline() }
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("\(reading.fuel.displayName), \(exactMegawatts(reading.megawatts)), \((reading.share * 100).formatted(.number.precision(.fractionLength(0...2)))) percent of displayed mix")
                    }
                }
                Text("These are exact values returned in this snapshot. The table inherits the partial transmission-visible boundary described above; its percentages are not total Great Britain generation shares.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineSpacing(3)
                    .padding(.top, 4)
            }

            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .center) {
                    SectionLabel("Interconnector table", trailing: "SIGNED MW")
                    Spacer(minLength: 10)
                    Menu {
                        Picker("Sort interconnectors", selection: $interconnectorSort) {
                            ForEach(InterconnectorTableSort.allCases) { sort in
                                Text(sort.label).tag(sort)
                            }
                        }
                    } label: {
                        Label(interconnectorSort.label, systemImage: "arrow.up.arrow.down")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(minHeight: 44)
                    }
                    .accessibilityLabel("Sort interconnector table by \(interconnectorSort.label)")
                }

                if snapshot.interconnectors.isEmpty {
                    Text("No time-aligned interconnector rows are available in this current snapshot.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                        .frame(minHeight: 44, alignment: .leading)
                } else {
                    ForEach(sortedInterconnectors) { flow in
                        HStack(alignment: .firstTextBaseline, spacing: 10) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(flow.name)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(GridTheme.textPrimary)
                                Text("\(flow.countryCode) · \(interconnectorDirection(flow.megawatts)) · \(flow.factClass.rawValue)")
                                    .font(.caption2)
                                    .foregroundStyle(GridTheme.textTertiary)
                            }
                            Spacer(minLength: 10)
                            Text(signedMegawatts(flow.megawatts))
                                .font(.subheadline)
                                .fontDesign(.monospaced)
                                .monospacedDigit()
                                .foregroundStyle(flow.megawatts >= 0 ? GridTheme.liveCyan : GridTheme.forecastViolet)
                        }
                        .frame(minHeight: 50)
                        .overlay(alignment: .bottom) { Hairline() }
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("\(flow.name), \(interconnectorDirection(flow.megawatts)), \(exactMegawatts(abs(flow.megawatts)))")
                    }
                }
                Text("Positive values import into Britain; negative values export from Britain. Sort by absolute magnitude to compare physical direction without hiding the sign.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineSpacing(3)
                    .padding(.top, 4)
            }
        }
    }

    private var inspectionSection: some View {
        VStack(alignment: .leading, spacing: 0) {
            SectionLabel("Inspect & export")
                .padding(.bottom, 7)

            NavigationLink {
                SourceStatusView()
            } label: {
                inspectionRow(
                    title: "Source status",
                    detail: "Delivery timing and current-fact coverage",
                    symbol: "antenna.radiowaves.left.and.right"
                )
            }
            .buttonStyle(.plain)

            NavigationLink {
                ForecastVerificationView()
            } label: {
                inspectionRow(
                    title: "Forecast review",
                    detail: "National MAE, bias and WAPE by horizon",
                    symbol: "chart.line.uptrend.xyaxis"
                )
            }
            .buttonStyle(.plain)

            NavigationLink {
                DataExportView()
            } label: {
                inspectionRow(
                    title: "Export data",
                    detail: "Up to 31 days · JSON or CSV · half-hour rows",
                    symbol: "arrow.down.doc"
                )
            }
            .buttonStyle(.plain)
        }
    }

    private func inspectionRow(title: String, detail: String, symbol: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(GridTheme.liveCyan)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.textPrimary)
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
                .accessibilityHidden(true)
        }
        .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens \(title.lowercased())")
    }

    private var sortedGeneration: [FuelReading] {
        snapshot.generation.sorted { lhs, rhs in
            switch supplySort {
            case .output:
                if lhs.megawatts != rhs.megawatts { return lhs.megawatts > rhs.megawatts }
            case .share:
                if lhs.share != rhs.share { return lhs.share > rhs.share }
            case .name:
                return lhs.fuel.displayName.localizedCaseInsensitiveCompare(rhs.fuel.displayName) == .orderedAscending
            }
            return lhs.fuel.displayName < rhs.fuel.displayName
        }
    }

    private var sortedInterconnectors: [InterconnectorFlow] {
        snapshot.interconnectors.sorted { lhs, rhs in
            switch interconnectorSort {
            case .magnitude:
                if abs(lhs.megawatts) != abs(rhs.megawatts) { return abs(lhs.megawatts) > abs(rhs.megawatts) }
            case .signedFlow:
                if lhs.megawatts != rhs.megawatts { return lhs.megawatts > rhs.megawatts }
            case .name:
                return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
            }
            return lhs.name < rhs.name
        }
    }

    private func signedMegawatts(_ value: Double) -> String {
        let sign = value > 0 ? "+" : ""
        return "\(sign)\(value.formatted(.number.precision(.fractionLength(0...2)))) MW"
    }

    private func exactMegawatts(_ value: Double) -> String {
        "\(value.formatted(.number.precision(.fractionLength(0...2)))) MW"
    }

    private func interconnectorDirection(_ value: Double) -> String {
        if value > 0 { return "Importing" }
        if value < 0 { return "Exporting" }
        return "No net flow"
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

private enum SupplyTableSort: String, CaseIterable, Identifiable {
    case output
    case share
    case name

    var id: String { rawValue }
    var label: String {
        switch self {
        case .output: "Output"
        case .share: "Displayed share"
        case .name: "Name"
        }
    }
}

private enum InterconnectorTableSort: String, CaseIterable, Identifiable {
    case magnitude
    case signedFlow
    case name

    var id: String { rawValue }
    var label: String {
        switch self {
        case .magnitude: "Magnitude"
        case .signedFlow: "Signed flow"
        case .name: "Name"
        }
    }
}
