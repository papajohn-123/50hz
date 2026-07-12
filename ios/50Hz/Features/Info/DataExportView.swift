import SwiftUI

enum ExportPeriod: Int, CaseIterable, Identifiable, Sendable {
    case oneDay = 1
    case sevenDays = 7
    case thirtyOneDays = 31

    var id: Int { rawValue }
    var days: Int { rawValue }

    var shortLabel: String {
        switch self {
        case .oneDay: "24h"
        case .sevenDays: "7d"
        case .thirtyOneDays: "31d"
        }
    }

    var label: String {
        switch self {
        case .oneDay: "Last 24 hours"
        case .sevenDays: "Last 7 days"
        case .thirtyOneDays: "Last 31 days"
        }
    }
}

@MainActor
final class DataExportViewModel: ObservableObject {
    @Published private(set) var schema: ExportSchemaResponse?
    @Published private(set) var isLoadingSchema = false
    @Published private(set) var schemaIsFromCache = false
    @Published private(set) var schemaError: String?
    @Published private(set) var isPreparing = false
    @Published private(set) var preparationError: String?
    @Published private(set) var artifact: PreparedExport?
    @Published var selectedMetric: ExportMetricID = .nationalCarbon
    @Published var selectedSelector: String?
    @Published var selectedFormat: InspectionExportFormat = .csv
    @Published var selectedPeriod: ExportPeriod = .oneDay

    private let client: any InspectionDataProviding
    private var schemaRequestID = UUID()
    private var exportRequestID = UUID()

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        self.client = client
    }

    var availableMetrics: [ExportMetricSchema] {
        (schema?.metrics ?? []).filter { $0.metric != .unknown }
    }

    var availableFormats: [InspectionExportFormat] {
        (schema?.formats ?? []).filter { [.json, .csv].contains($0) }
    }

    var selectedMetricSchema: ExportMetricSchema? {
        availableMetrics.first { $0.metric == selectedMetric }
    }

    func loadSchema() async {
        let currentRequest = UUID()
        schemaRequestID = currentRequest
        isLoadingSchema = true
        schemaError = nil

        if schema == nil, let cached = await client.cachedExportSchema() {
            guard schemaRequestID == currentRequest, !Task.isCancelled else { return }
            schema = cached
            schemaIsFromCache = true
            normalizeSelection()
        }

        do {
            let refreshed = try await client.exportSchema()
            guard schemaRequestID == currentRequest, !Task.isCancelled else { return }
            schema = refreshed
            schemaIsFromCache = false
            schemaError = nil
            normalizeSelection()
        } catch {
            guard schemaRequestID == currentRequest,
                  !Task.isCancelled,
                  !Self.isCancellation(error) else { return }
            schemaError = error.localizedDescription
        }
        if schemaRequestID == currentRequest { isLoadingSchema = false }
    }

    func selectMetric(_ metric: ExportMetricID) {
        selectedMetric = metric
        selectedSelector = selectedMetricSchema?.selectorRequired == true
            ? selectedMetricSchema?.allowedSelectors.first
            : nil
        artifact = nil
        preparationError = nil
    }

    func request(now: Date = Date()) throws -> ExportRequestSpec {
        guard let schema,
              schema.maxWindowDays == 31,
              schema.maxRowCount == ExportRequestSpec.maximumRows,
              schema.resolutionsSeconds.contains(ExportRequestSpec.resolutionSeconds),
              availableFormats.contains(selectedFormat),
              let metric = selectedMetricSchema else {
            throw InspectionRequestError.unsupportedSelection
        }
        if metric.selectorRequired {
            guard let selectedSelector else { throw InspectionRequestError.selectorRequired }
            guard metric.allowedSelectors.contains(selectedSelector) else {
                throw InspectionRequestError.unsupportedSelection
            }
        }
        return try ExportRequestSpec.recent(
            metric: selectedMetric,
            selector: selectedSelector,
            days: selectedPeriod.days,
            format: selectedFormat,
            now: now
        )
    }

    func prepare(now: Date = Date()) async {
        let currentRequest = UUID()
        exportRequestID = currentRequest
        isPreparing = true
        preparationError = nil
        artifact = nil

        do {
            let request = try request(now: now)
            let prepared = try await client.prepareExport(request)
            guard exportRequestID == currentRequest, !Task.isCancelled else { return }
            artifact = prepared
        } catch {
            guard exportRequestID == currentRequest,
                  !Task.isCancelled,
                  !Self.isCancellation(error) else { return }
            preparationError = error.localizedDescription
        }
        if exportRequestID == currentRequest { isPreparing = false }
    }

    func invalidatePreparedArtifact() {
        exportRequestID = UUID()
        artifact = nil
        preparationError = nil
        isPreparing = false
    }

    private func normalizeSelection() {
        if !availableMetrics.contains(where: { $0.metric == selectedMetric }),
           let first = availableMetrics.first {
            selectedMetric = first.metric
        }
        if !availableFormats.contains(selectedFormat), let first = availableFormats.first {
            selectedFormat = first
        }
        if selectedMetricSchema?.selectorRequired == true {
            if let selectedSelector,
               selectedMetricSchema?.allowedSelectors.contains(selectedSelector) == true {
                return
            }
            selectedSelector = selectedMetricSchema?.allowedSelectors.first
        } else {
            selectedSelector = nil
        }
    }

    private static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let apiError = error as? GridAPIError, case .cancelled = apiError { return true }
        return false
    }
}

struct DataExportView: View {
    @StateObject private var viewModel: DataExportViewModel
    @State private var referenceNow = Date()

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        _viewModel = StateObject(wrappedValue: DataExportViewModel(client: client))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                orientation
                content
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.bottom, 34)
        }
        .scrollIndicators(.hidden)
        .navigationTitle("Export data")
        .navigationBarTitleDisplayMode(.inline)
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .task { await viewModel.loadSchema() }
    }

    private var orientation: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("A bounded evidence file")
                .font(.system(.title2, design: .rounded, weight: .medium))
                .tracking(-0.5)
            Text("Export one allowlisted national series at 30-minute resolution. Every interval is retained: missing data appears as an explicit insufficient-data row, never as zero or a filled estimate.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let schema = viewModel.schema,
           !viewModel.availableMetrics.isEmpty,
           !viewModel.availableFormats.isEmpty {
            configuration(schema)
        } else if viewModel.isLoadingSchema {
            HStack(spacing: 10) {
                ProgressView().tint(GridTheme.liveCyan)
                Text("Loading the published export contract…")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, minHeight: 140)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Export contract unavailable")
                    .font(.headline)
                Text(viewModel.schemaError ?? "No saved export contract is available.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                Button("Try again") { Task { await viewModel.loadSchema() } }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(minHeight: 44)
            }
        }
    }

    private func configuration(_ schema: ExportSchemaResponse) -> some View {
        VStack(alignment: .leading, spacing: 26) {
            if viewModel.schemaIsFromCache || viewModel.schemaError != nil {
                Label(
                    viewModel.schemaError.map { "Using the saved export contract. \($0)" }
                        ?? "Using the saved export contract while a refresh completes.",
                    systemImage: "clock.arrow.circlepath"
                )
                .font(.caption)
                .foregroundStyle(GridTheme.staleAmber)
            }

            VStack(alignment: .leading, spacing: 14) {
                SectionLabel("Series")
                Picker("Metric", selection: metricBinding) {
                    ForEach(viewModel.availableMetrics) { metric in
                        Text(ExportPresentation.metricLabel(metric.metric)).tag(metric.metric)
                    }
                }
                .pickerStyle(.menu)
                .tint(GridTheme.liveCyan)
                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)

                if let metric = viewModel.selectedMetricSchema, metric.selectorRequired {
                    Picker("Selector", selection: selectorBinding(metric)) {
                        ForEach(metric.allowedSelectors, id: \.self) { selector in
                            Text(ExportPresentation.selectorLabel(selector, metric: metric.metric))
                                .tag(Optional(selector))
                        }
                    }
                    .pickerStyle(.menu)
                    .tint(GridTheme.liveCyan)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                }
            }

            VStack(alignment: .leading, spacing: 14) {
                SectionLabel("Window", trailing: "UTC HALF-HOURS")
                Picker("Window", selection: $viewModel.selectedPeriod) {
                    ForEach(ExportPeriod.allCases) { period in
                        Text(period.shortLabel).tag(period)
                    }
                }
                .pickerStyle(.segmented)
                .onChange(of: viewModel.selectedPeriod) { _, _ in
                    referenceNow = Date()
                    viewModel.invalidatePreparedArtifact()
                }

                if let request = try? viewModel.request(now: referenceNow) {
                    exportDetailRow("From", ExportPresentation.utcTimestamp(request.from))
                    exportDetailRow("To", ExportPresentation.utcTimestamp(request.to))
                    exportDetailRow("Rows", "\(request.expectedRowCount) maximum, one per half-hour")
                }
                Text("The window ends at the most recent exact UTC half-hour. The 31-day option reaches the API maximum of 1,488 rows.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineSpacing(3)
            }

            VStack(alignment: .leading, spacing: 14) {
                SectionLabel("File")
                Picker("Format", selection: $viewModel.selectedFormat) {
                    ForEach(viewModel.availableFormats) { format in
                        Text(format.rawValue.uppercased()).tag(format)
                    }
                }
                .pickerStyle(.segmented)
                .onChange(of: viewModel.selectedFormat) { _, _ in
                    viewModel.invalidatePreparedArtifact()
                }

                Button {
                    referenceNow = Date()
                    Task { await viewModel.prepare(now: referenceNow) }
                } label: {
                    HStack(spacing: 9) {
                        if viewModel.isPreparing {
                            ProgressView().tint(GridTheme.background)
                        } else {
                            Image(systemName: "arrow.down.doc")
                        }
                        Text(viewModel.isPreparing ? "Preparing…" : "Prepare \(viewModel.selectedFormat.rawValue.uppercased())")
                    }
                    .font(.headline)
                    .foregroundStyle(GridTheme.background)
                    .frame(maxWidth: .infinity, minHeight: 52)
                    .background(GridTheme.liveCyan, in: Capsule())
                }
                .buttonStyle(.plain)
                .disabled(viewModel.isPreparing)

                if let error = viewModel.preparationError {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                }
            }

            if let artifact = viewModel.artifact {
                preparedSection(artifact)
                    .transition(.opacity.combined(with: .move(edge: .bottom)))
            }

            VStack(alignment: .leading, spacing: 8) {
                SectionLabel("Published contract", trailing: "SCHEMA \(schema.schemaVersion)")
                Text(schema.timestampPolicy)
                Text(schema.missingDataPolicy)
            }
            .font(.caption)
            .foregroundStyle(GridTheme.textTertiary)
            .lineSpacing(3)
        }
    }

    private func preparedSection(_ artifact: PreparedExport) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("File ready", trailing: artifact.format.rawValue.uppercased())
            HStack(alignment: .firstTextBaseline) {
                Text("\(artifact.expectedRows - artifact.missingRows) / \(artifact.expectedRows)")
                    .font(.system(.title2, design: .monospaced, weight: .medium))
                    .foregroundStyle(artifact.missingRows == 0 ? GridTheme.liveCyan : GridTheme.staleAmber)
                Text("intervals available")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            if artifact.missingRows > 0 {
                Text("\(artifact.missingRows) missing interval\(artifact.missingRows == 1 ? " is" : "s are") included as explicit insufficient-data rows.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            ShareLink(item: artifact.url) {
                Label("Share export", systemImage: "square.and.arrow.up")
                    .font(.headline)
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(maxWidth: .infinity, minHeight: 50)
                    .overlay(Capsule().stroke(GridTheme.liveCyan.opacity(0.35), lineWidth: 1))
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the system share sheet for this local export file")
            Text("Prepared on this device at \(InspectionPresentation.fullTimestamp(artifact.preparedAt)). The temporary file is protected while stored by 50Hz.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
                .lineSpacing(3)
        }
    }

    private var metricBinding: Binding<ExportMetricID> {
        Binding(
            get: { viewModel.selectedMetric },
            set: { value in
                viewModel.selectMetric(value)
                referenceNow = Date()
            }
        )
    }

    private func selectorBinding(_ metric: ExportMetricSchema) -> Binding<String?> {
        Binding(
            get: { viewModel.selectedSelector ?? metric.allowedSelectors.first },
            set: { value in
                viewModel.selectedSelector = value
                viewModel.invalidatePreparedArtifact()
            }
        )
    }

    private func exportDetailRow(_ label: String, _ value: String) -> some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline) {
                Text(label)
                Spacer(minLength: 12)
                Text(value).multilineTextAlignment(.trailing)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(label)
                Text(value).foregroundStyle(GridTheme.textPrimary)
            }
        }
        .font(.caption)
        .foregroundStyle(GridTheme.textSecondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value)")
    }
}

enum ExportPresentation {
    static func metricLabel(_ metric: ExportMetricID) -> String {
        switch metric {
        case .nationalCarbon: "National carbon intensity"
        case .nationalDemand: "National demand outturn"
        case .generationFuel: "Generation by fuel"
        case .interconnectorFlow: "Interconnector flow"
        case .unknown: "Unsupported metric"
        }
    }

    static func selectorLabel(_ selector: String, metric: ExportMetricID) -> String {
        if metric == .generationFuel {
            return selector.replacingOccurrences(of: "_", with: " ").capitalized
        }
        return selector
    }

    static func utcTimestamp(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB_POSIX")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = TimeZone(secondsFromGMT: 0)
        formatter.dateFormat = "d MMM yyyy, HH:mm 'UTC'"
        return formatter.string(from: date)
    }
}
