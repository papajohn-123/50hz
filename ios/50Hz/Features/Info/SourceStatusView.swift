import SwiftUI

@MainActor
final class SourceStatusViewModel: ObservableObject {
    @Published private(set) var response: SourceStatusResponse?
    @Published private(set) var isLoading = false
    @Published private(set) var isFromCache = false
    @Published private(set) var errorMessage: String?

    private let client: any InspectionDataProviding
    private var requestID = UUID()

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        self.client = client
    }

    func load() async {
        let currentRequest = UUID()
        requestID = currentRequest
        isLoading = true
        errorMessage = nil

        if response == nil, let cached = await client.cachedSourceStatus() {
            guard requestID == currentRequest, !Task.isCancelled else { return }
            response = cached
            isFromCache = true
        }

        do {
            let refreshed = try await client.sourceStatus()
            guard requestID == currentRequest, !Task.isCancelled else { return }
            response = refreshed
            isFromCache = false
            errorMessage = nil
        } catch {
            guard requestID == currentRequest,
                  !Task.isCancelled,
                  !SourceStatusViewModel.isCancellation(error) else { return }
            errorMessage = error.localizedDescription
        }
        if requestID == currentRequest { isLoading = false }
    }

    private static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let apiError = error as? GridAPIError, case .cancelled = apiError { return true }
        return false
    }
}

struct SourceStatusView: View {
    @StateObject private var viewModel: SourceStatusViewModel

    init(client: any InspectionDataProviding = HTTPInspectionClient()) {
        _viewModel = StateObject(wrappedValue: SourceStatusViewModel(client: client))
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                orientation
                content
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .navigationTitle("Source status")
        .navigationBarTitleDisplayMode(.inline)
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .task { await viewModel.load() }
        .refreshable { await viewModel.load() }
    }

    private var orientation: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Delivery and fact are separate")
                .font(.system(.title2, design: .rounded, weight: .medium))
                .tracking(-0.5)
                .foregroundStyle(GridTheme.textPrimary)
            Text("Delivery shows whether 50Hz is receiving each publisher on schedule. Fact shows whether that source currently covers the live grid view. A healthy delivery does not make an old fact current.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var content: some View {
        if let response = viewModel.response {
            VStack(alignment: .leading, spacing: 0) {
                SectionLabel(
                    "Published sources",
                    trailing: "\(response.sourceCount) · \(InspectionPresentation.shortTimestamp(response.evaluatedAt))"
                )
                .padding(.bottom, 8)

                if viewModel.isFromCache || viewModel.errorMessage != nil {
                    heldCopy
                        .padding(.vertical, 10)
                }

                ForEach(response.sources) { source in
                    NavigationLink {
                        SourceStatusDetailView(source: source, evaluatedAt: response.evaluatedAt)
                    } label: {
                        sourceRow(source)
                    }
                    .buttonStyle(.plain)
                }

                Text("Evaluated by the 50Hz service at \(InspectionPresentation.fullTimestamp(response.evaluatedAt)). Pull to refresh; this view does not probe publishers directly.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineSpacing(3)
                    .padding(.top, 16)
            }
        } else if viewModel.isLoading {
            loadingState
        } else {
            unavailableState
        }
    }

    private var heldCopy: some View {
        HStack(alignment: .top, spacing: 9) {
            Image(systemName: "clock.arrow.circlepath")
                .foregroundStyle(GridTheme.staleAmber)
            Text(viewModel.errorMessage.map { "Showing the last saved source report. \($0)" }
                 ?? "Showing the last saved source report while a refresh completes.")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
        }
        .accessibilityElement(children: .combine)
    }

    private func sourceRow(_ source: InspectedSourceStatus) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(source.displayName)
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                    .multilineTextAlignment(.leading)
                Spacer(minLength: 8)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(GridTheme.textTertiary)
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 18) {
                    statusLabel(
                        "Delivery",
                        value: InspectionPresentation.deliveryLabel(source.deliveryState),
                        color: InspectionPresentation.deliveryColor(source.deliveryState)
                    )
                    statusLabel(
                        "Fact",
                        value: InspectionPresentation.factLabel(source.factState),
                        color: InspectionPresentation.factColor(source.factState)
                    )
                }
                VStack(alignment: .leading, spacing: 8) {
                    statusLabel(
                        "Delivery",
                        value: InspectionPresentation.deliveryLabel(source.deliveryState),
                        color: InspectionPresentation.deliveryColor(source.deliveryState)
                    )
                    statusLabel(
                        "Fact",
                        value: InspectionPresentation.factLabel(source.factState),
                        color: InspectionPresentation.factColor(source.factState)
                    )
                }
            }
            Text("\(source.publisher) · \(source.dataset)")
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
                .lineLimit(2)
        }
        .padding(.vertical, 14)
        .frame(maxWidth: .infinity, minHeight: 78, alignment: .leading)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(
            "\(source.displayName). Delivery, \(InspectionPresentation.deliveryLabel(source.deliveryState)). Fact, \(InspectionPresentation.factLabel(source.factState))."
        )
        .accessibilityHint("Opens source timing and attribution")
    }

    private func statusLabel(_ name: String, value: String, color: Color) -> some View {
        HStack(spacing: 7) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text("\(name): \(value)")
                .font(.caption.weight(.medium))
                .foregroundStyle(GridTheme.textSecondary)
        }
    }

    private var loadingState: some View {
        HStack(spacing: 10) {
            ProgressView().tint(GridTheme.liveCyan)
            Text("Loading the latest source report…")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, minHeight: 120)
    }

    private var unavailableState: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Source report unavailable")
                .font(.headline)
            Text(viewModel.errorMessage ?? "No saved report is available yet.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
            Button("Try again") { Task { await viewModel.load() } }
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
                .frame(minHeight: 44)
        }
    }
}

private struct SourceStatusDetailView: View {
    let source: InspectedSourceStatus
    let evaluatedAt: Date

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 26) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(source.displayName)
                        .font(.system(.title2, design: .rounded, weight: .medium))
                    Text("\(source.publisher) · \(source.dataset)")
                        .font(.caption)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }

                stateSection
                timingSection
                attributionSection
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .navigationTitle("Source detail")
        .navigationBarTitleDisplayMode(.inline)
        .background(GridTheme.background.ignoresSafeArea())
    }

    private var stateSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Current assessment")
            inspectionRow(
                "Delivery",
                InspectionPresentation.deliveryLabel(source.deliveryState),
                color: InspectionPresentation.deliveryColor(source.deliveryState)
            )
            Text(InspectionPresentation.deliveryExplanation(source))
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
            inspectionRow(
                "Fact",
                InspectionPresentation.factLabel(source.factState),
                color: InspectionPresentation.factColor(source.factState)
            )
            Text(InspectionPresentation.factExplanation(source))
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(3)
            Text(source.note)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var timingSection: some View {
        VStack(alignment: .leading, spacing: 9) {
            SectionLabel("Timing")
            detailRow("Server evaluated", InspectionPresentation.fullTimestamp(evaluatedAt))
            detailRow("Expected fact cadence", InspectionPresentation.duration(source.expectedFactCadenceSeconds))
            if let value = source.deliveryLagSeconds {
                detailRow("Since successful delivery", InspectionPresentation.duration(value))
            }
            if let value = source.lastAttemptedAt {
                detailRow("Last attempted", InspectionPresentation.fullTimestamp(value))
            }
            if let value = source.lastSucceededAt {
                detailRow("Last succeeded", InspectionPresentation.fullTimestamp(value))
            }
            if let value = source.observedAt {
                detailRow("Fact observed", InspectionPresentation.fullTimestamp(value))
            }
            if let value = source.validTo {
                detailRow("Fact valid through", InspectionPresentation.fullTimestamp(value))
            }
            if let value = source.factAgeSeconds {
                detailRow("Fact age at evaluation", InspectionPresentation.duration(value))
            }
            if !source.factFamilies.isEmpty {
                detailRow("Current-view families", source.factFamilies.map(InspectionPresentation.familyLabel).joined(separator: ", "))
            }
        }
    }

    private var attributionSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Attribution")
            Text(source.attribution)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
            if let url = InspectionPresentation.approvedSourceURL(source.documentationURL) {
                Link(destination: url) {
                    linkRow("Publisher documentation", symbol: "doc.text")
                }
                .accessibilityHint("Opens the publisher website")
            }
            if let url = InspectionPresentation.approvedSourceURL(source.licenceURL) {
                Link(destination: url) {
                    linkRow("Licence", symbol: "checkmark.seal")
                }
                .accessibilityHint("Opens the publisher website")
            }
            Text(source.sourceID)
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
                .textSelection(.enabled)
        }
    }

    private func inspectionRow(_ label: String, _ value: String, color: Color) -> some View {
        HStack {
            Text(label).foregroundStyle(GridTheme.textSecondary)
            Spacer()
            HStack(spacing: 7) {
                Circle().fill(color).frame(width: 6, height: 6)
                Text(value).foregroundStyle(GridTheme.textPrimary)
            }
        }
        .font(.subheadline.weight(.medium))
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
                Text(value).foregroundStyle(GridTheme.textPrimary)
            }
        }
        .font(.caption)
        .foregroundStyle(GridTheme.textSecondary)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value)")
    }

    private func linkRow(_ title: String, symbol: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(GridTheme.liveCyan)
                .frame(width: 22)
            Text(title).foregroundStyle(GridTheme.textPrimary)
            Spacer()
            Image(systemName: "arrow.up.right")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .font(.subheadline.weight(.medium))
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
    }
}

enum InspectionPresentation {
    static func deliveryLabel(_ state: SourceDeliveryHealth) -> String {
        switch state {
        case .healthy: "On schedule"
        case .delayed: "Delayed"
        case .stale: "Stale"
        case .unavailable: "Unavailable"
        default: "Unknown"
        }
    }

    static func factLabel(_ state: SourceFactHealth) -> String {
        switch state {
        case .live: "Current"
        case .delayed: "Delayed"
        case .stale: "Stale"
        case .unavailable: "Unavailable"
        case .notApplicable: "Not used here"
        default: "Unknown"
        }
    }

    static func deliveryColor(_ state: SourceDeliveryHealth) -> Color {
        switch state {
        case .healthy: GridTheme.liveCyan
        case .delayed, .stale: GridTheme.staleAmber
        case .unavailable: GridTheme.warning
        default: GridTheme.textTertiary
        }
    }

    static func factColor(_ state: SourceFactHealth) -> Color {
        switch state {
        case .live: GridTheme.liveCyan
        case .delayed, .stale: GridTheme.staleAmber
        case .unavailable: GridTheme.warning
        case .notApplicable: GridTheme.textTertiary
        default: GridTheme.textTertiary
        }
    }

    static func deliveryExplanation(_ source: InspectedSourceStatus) -> String {
        switch source.deliveryState {
        case .healthy: "50Hz received a successful delivery within the cadence-derived on-schedule threshold."
        case .delayed: "The most recent successful delivery is later than the on-schedule threshold."
        case .stale: "The most recent successful delivery is beyond the stale threshold."
        case .unavailable: "No successful delivery time is available in this report."
        default: "The server returned a delivery state this app does not yet classify."
        }
    }

    static func factExplanation(_ source: InspectedSourceStatus) -> String {
        switch source.factState {
        case .live: "A fact from this source validly covers the current grid view."
        case .delayed: "The current-view fact is later than its live threshold."
        case .stale: "The current-view fact is beyond its stale threshold."
        case .unavailable: "No current-view fact from this source was available when evaluated."
        case .notApplicable: "This publisher supplies forecast or reported-event data, not a fact used in the current grid view."
        default: "The server returned a fact state this app does not yet classify."
        }
    }

    static func duration(_ seconds: Int) -> String {
        guard seconds >= 0 else { return "Unknown" }
        if seconds < 60 { return "\(seconds) sec" }
        if seconds < 3_600 { return "\(seconds / 60) min" }
        let hours = seconds / 3_600
        let minutes = (seconds % 3_600) / 60
        return minutes == 0 ? "\(hours) hr" : "\(hours) hr \(minutes) min"
    }

    static func shortTimestamp(_ date: Date) -> String {
        date.formatted(.dateTime.hour().minute())
    }

    static func fullTimestamp(_ date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_GB")
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.timeZone = LondonDay.timeZone
        formatter.dateFormat = "d MMM yyyy, HH:mm:ss z"
        return "\(formatter.string(from: date)) (UK)"
    }

    static func familyLabel(_ raw: String) -> String {
        switch raw {
        case "generation": "Generation"
        case "demand": "Demand"
        case "frequency": "Frequency"
        case "interconnectors": "Interconnectors"
        case "carbon": "Carbon"
        default: "Other"
        }
    }

    static func approvedSourceURL(_ raw: String?) -> URL? {
        guard let raw,
              let url = URL(string: raw),
              url.scheme?.lowercased() == "https",
              url.user == nil,
              url.password == nil,
              let host = url.host?.lowercased() else { return nil }
        let approvedHosts = ["bmrs.elexon.co.uk", "elexon.co.uk", "carbonintensity.org.uk"]
        guard approvedHosts.contains(where: { host == $0 || host.hasSuffix(".\($0)") }) else {
            return nil
        }
        return url
    }
}
