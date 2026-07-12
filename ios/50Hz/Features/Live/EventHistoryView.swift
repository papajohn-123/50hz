import SwiftUI

@MainActor
final class EventHistoryViewModel: ObservableObject {
    @Published private(set) var history: EventHistoryResponse?
    @Published private(set) var isLoading = false
    @Published private(set) var isFromCache = false
    @Published private(set) var errorMessage: String?

    let eventID: String
    private let client: any InspectionDataProviding
    private var requestID = UUID()

    init(eventID: String, client: any InspectionDataProviding = HTTPInspectionClient()) {
        self.eventID = eventID
        self.client = client
    }

    func load() async {
        let currentRequest = UUID()
        requestID = currentRequest
        isLoading = true
        errorMessage = nil

        guard InspectionEndpoint.isValidEventID(eventID) else {
            history = nil
            errorMessage = InspectionRequestError.invalidEventID.localizedDescription
            isLoading = false
            return
        }

        if history == nil, let cached = await client.cachedEventHistory(eventID: eventID) {
            guard requestID == currentRequest, !Task.isCancelled else { return }
            history = cached
            isFromCache = true
        }

        do {
            let refreshed = try await client.eventHistory(eventID: eventID)
            guard requestID == currentRequest, !Task.isCancelled else { return }
            history = refreshed
            isFromCache = false
            errorMessage = nil
        } catch {
            guard requestID == currentRequest,
                  !Task.isCancelled,
                  !Self.isCancellation(error) else { return }
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

struct EventHistorySheet: View {
    let eventTitle: String
    @StateObject private var viewModel: EventHistoryViewModel
    @Environment(\.dismiss) private var dismiss

    init(
        eventID: String,
        eventTitle: String,
        client: any InspectionDataProviding = HTTPInspectionClient()
    ) {
        self.eventTitle = eventTitle
        _viewModel = StateObject(
            wrappedValue: EventHistoryViewModel(eventID: eventID, client: client)
        )
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 26) {
                    titleSection
                    content
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
                .padding(.bottom, 34)
            }
            .scrollIndicators(.hidden)
            .navigationTitle("Revision history")
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
        .task(id: viewModel.eventID) { await viewModel.load() }
    }

    private var titleSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(eventTitle)
                .font(.system(.title2, design: .rounded, weight: .medium))
                .tracking(-0.5)
                .foregroundStyle(GridTheme.textPrimary)
            Text("Publisher revisions are shown newest first. They record changes to the reported notice; they do not prove a resulting movement elsewhere on the grid.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
        }
    }

    @ViewBuilder
    private var content: some View {
        if let history = viewModel.history {
            historyContent(history)
        } else if viewModel.isLoading {
            HStack(spacing: 10) {
                ProgressView().tint(GridTheme.liveCyan)
                Text("Loading immutable revisions…")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, minHeight: 140)
        } else {
            VStack(alignment: .leading, spacing: 10) {
                Text("Revision history unavailable")
                    .font(.headline)
                Text(viewModel.errorMessage ?? "No saved history is available.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                Button("Try again") { Task { await viewModel.load() } }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(minHeight: 44)
            }
        }
    }

    private func historyContent(_ history: EventHistoryResponse) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Lifecycle", trailing: "\(history.revisionCount) REVISION\(history.revisionCount == 1 ? "" : "S")")
                HStack(alignment: .firstTextBaseline) {
                    Text(EventHistoryPresentation.statusLabel(history.lifecycleStatus))
                        .font(.system(.title3, design: .rounded, weight: .medium))
                        .foregroundStyle(EventHistoryPresentation.statusColor(history.lifecycleStatus))
                    Spacer()
                    Text("Latest \(InspectionPresentation.shortTimestamp(history.latestPublishedAt))")
                        .font(.caption)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                Text("First published \(InspectionPresentation.fullTimestamp(history.firstPublishedAt)). Latest revision \(InspectionPresentation.fullTimestamp(history.latestPublishedAt)).")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)

                if viewModel.isFromCache || viewModel.errorMessage != nil {
                    Label(
                        viewModel.errorMessage.map { "Showing saved revisions. \($0)" }
                            ?? "Showing saved revisions while a refresh completes.",
                        systemImage: "clock.arrow.circlepath"
                    )
                    .font(.caption)
                    .foregroundStyle(GridTheme.staleAmber)
                }
                if history.isTruncated {
                    Text("Showing the newest \(history.returnedRevisionCount) of \(history.revisionCount) revisions. The API bounds one response at 100 revisions.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                }
            }

            VStack(alignment: .leading, spacing: 0) {
                SectionLabel("Reported revisions", trailing: "NEWEST FIRST")
                    .padding(.bottom, 8)
                ForEach(Array(history.revisions.enumerated()), id: \.element.id) { index, revision in
                    EventRevisionView(revision: revision, isCurrent: index == 0)
                }
            }

            Text("Event ID: \(history.eventID) · schema \(history.schemaVersion)")
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
                .textSelection(.enabled)
        }
    }
}

private struct EventRevisionView: View {
    let revision: EventHistoryRevision
    let isCurrent: Bool
    @State private var evidenceExpanded = false

    var body: some View {
        HStack(alignment: .top, spacing: 13) {
            timeline
            VStack(alignment: .leading, spacing: 12) {
                header
                reportedState
                if !revision.changes.isEmpty { changes }
                evidence
                Hairline().padding(.top, 4)
            }
            .padding(.bottom, 18)
        }
    }

    private var timeline: some View {
        VStack(spacing: 0) {
            Circle()
                .fill(isCurrent ? GridTheme.liveCyan : GridTheme.textTertiary)
                .frame(width: isCurrent ? 9 : 7, height: isCurrent ? 9 : 7)
                .shadow(color: isCurrent ? GridTheme.liveCyan.opacity(0.45) : .clear, radius: 4)
                .padding(.top, 7)
            Rectangle()
                .fill(GridTheme.hairline)
                .frame(width: 1)
                .frame(maxHeight: .infinity)
        }
        .frame(width: 10)
        .accessibilityHidden(true)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text("Revision \(revision.revisionNumber)")
                    .font(.headline)
                if isCurrent {
                    Text("CURRENT")
                        .font(.system(size: 9, weight: .semibold, design: .monospaced))
                        .tracking(0.6)
                        .foregroundStyle(GridTheme.liveCyan)
                }
                Spacer(minLength: 8)
                Text(EventHistoryPresentation.statusLabel(revision.status))
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(EventHistoryPresentation.statusColor(revision.status))
            }
            Text("Published \(InspectionPresentation.fullTimestamp(revision.publishedAt))")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            Text(EventHistoryPresentation.authorityLabel(revision.authority))
                .font(.caption2)
                .foregroundStyle(GridTheme.textSecondary)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var reportedState: some View {
        VStack(alignment: .leading, spacing: 7) {
            if let reason = revision.materialReason {
                detail("Revision reason", reason)
            }
            if let asset = revision.reportedAsset {
                let label = [asset.name, asset.assetID].compactMap { $0 }.joined(separator: " · ")
                detail("Reported asset", label)
                if !asset.identityReliable {
                    Text("The publisher identity is not marked reliable.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.staleAmber)
                }
            }
            if let capacity = revision.reportedCapacity {
                if let unavailable = capacity.unavailableMW {
                    detail("Reported unavailable", EventHistoryPresentation.megawatts(unavailable))
                }
                if let normal = capacity.normalCapacityMW {
                    detail("Reported normal capacity", EventHistoryPresentation.megawatts(normal))
                }
            }
            if let window = revision.effectiveWindow {
                if let start = window.start { detail("Effective from", InspectionPresentation.fullTimestamp(start)) }
                if let end = window.end { detail("Effective to", InspectionPresentation.fullTimestamp(end)) }
            }
            if let planned = revision.planned {
                detail("Publisher classification", planned ? "Planned" : "Unplanned")
            }
            if let cause = revision.reportedCause {
                detail("Reported cause", cause)
            }
            if let superseded = revision.supersededByEventID {
                detail("Superseded by", superseded)
            }
        }
    }

    private var changes: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("CHANGES FROM PRIOR REVISION")
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .tracking(0.6)
                .foregroundStyle(GridTheme.textTertiary)
            ForEach(revision.changes) { change in
                VStack(alignment: .leading, spacing: 3) {
                    Text(EventHistoryPresentation.fieldLabel(change.field))
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.textPrimary)
                    Text("\(EventHistoryPresentation.scalar(change.before, field: change.field)) → \(EventHistoryPresentation.scalar(change.after, field: change.field))")
                        .font(.caption)
                        .fontDesign(change.field == .evidenceChecksum ? .monospaced : .default)
                        .foregroundStyle(GridTheme.textSecondary)
                        .lineLimit(change.field == .evidenceChecksum ? 2 : nil)
                        .textSelection(.enabled)
                }
                .accessibilityElement(children: .combine)
            }
        }
        .padding(.top, 2)
    }

    private var evidence: some View {
        DisclosureGroup(isExpanded: $evidenceExpanded) {
            VStack(alignment: .leading, spacing: 8) {
                evidenceList("Source IDs", revision.sourceIDs)
                evidenceList("Source record IDs", revision.sourceRecordIDs)
                Text("Evidence checksum")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(GridTheme.textTertiary)
                Text(revision.evidenceChecksum)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(GridTheme.textSecondary)
                    .textSelection(.enabled)
            }
            .padding(.top, 8)
        } label: {
            Text(evidenceExpanded ? "Hide provenance" : "Show provenance")
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
                .frame(minHeight: 44, alignment: .leading)
        }
        .tint(GridTheme.liveCyan)
    }

    private func evidenceList(_ title: String, _ values: [String]) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(title)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(GridTheme.textTertiary)
            ForEach(values, id: \.self) { value in
                Text(value)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(GridTheme.textSecondary)
                    .textSelection(.enabled)
            }
        }
    }

    private func detail(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(GridTheme.textTertiary)
            Text(value)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(2)
                .textSelection(.enabled)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(label), \(value)")
    }
}

enum EventHistoryPresentation {
    static func statusLabel(_ status: EventLifecycleState) -> String {
        switch status {
        case .open: "Open"
        case .updated: "Updated"
        case .resolved: "Resolved"
        case .superseded: "Superseded"
        case .withdrawn: "Withdrawn"
        case .unknown: "Unknown"
        }
    }

    static func statusColor(_ status: EventLifecycleState) -> Color {
        switch status {
        case .open, .updated: GridTheme.staleAmber
        case .resolved: GridTheme.liveCyan
        case .superseded, .withdrawn, .unknown: GridTheme.textTertiary
        }
    }

    static func authorityLabel(_ authority: EventRevisionAuthority) -> String {
        switch authority {
        case .systemWarning: "Published system warning"
        case .authoritativeNotice: "Authoritative participant notice"
        case .otherReported: "Other reported notice"
        case .unknown: "Reported authority not classified"
        }
    }

    static func fieldLabel(_ field: EventChangedField) -> String {
        switch field {
        case .unavailableMW: "Unavailable capacity"
        case .normalCapacityMW: "Normal capacity"
        case .effectiveStart: "Effective start"
        case .effectiveEnd: "Effective end"
        case .status: "Lifecycle status"
        case .reportedCause: "Reported cause"
        case .evidenceChecksum: "Evidence checksum"
        case .materialReason: "Revision reason"
        case .unknown: "Other reported field"
        }
    }

    static func scalar(_ scalar: InspectionScalar, field: EventChangedField) -> String {
        return switch scalar {
        case .number(let value) where field == .unavailableMW || field == .normalCapacityMW:
            megawatts(value)
        case .text(let value) where field == .effectiveStart || field == .effectiveEnd:
            timestampScalar(value)
        case .text(let value): value
        case .number(let value): value.formatted(.number.precision(.fractionLength(0...2)))
        case .boolean(let value): value ? "Yes" : "No"
        case .null: "Not supplied"
        }
    }

    private static func timestampScalar(_ value: String) -> String {
        guard let data = try? JSONEncoder().encode(value),
              let date = try? GridJSON.decoder.decode(Date.self, from: data) else {
            return "Unrecognised timestamp"
        }
        return InspectionPresentation.fullTimestamp(date)
    }

    static func megawatts(_ value: Double) -> String {
        "\(value.formatted(.number.precision(.fractionLength(0...1)))) MW"
    }
}
