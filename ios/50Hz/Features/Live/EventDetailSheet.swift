import SwiftUI

struct EventDetailSheet: View {
    let event: GridEvent
    let snapshot: GridSnapshot?
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var sharePayload: GridShareCardPayload?
    @State private var resolvedEvent: GridEvent?
    @State private var explanation: EventExplanationResponse?
    @State private var isLoadingDetails = false
    @State private var isExplaining = false
    @State private var detailError: String?
    @State private var explanationError: String?
    @State private var isHistoryPresented = false

    private var displayedEvent: GridEvent { resolvedEvent ?? event }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    HStack(spacing: 8) {
                        Circle().fill(GridTheme.warning).frame(width: 7, height: 7)
                        Text(displayedEvent.evidenceClass.uppercased())
                            .font(.caption2.weight(.semibold))
                            .fontDesign(.monospaced)
                            .tracking(0.8)
                            .foregroundStyle(GridTheme.warning)
                    }

                    Text(displayedEvent.title)
                        .font(.title2.weight(.medium))

                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel("Reported facts")
                        Text(displayedEvent.summary)
                            .font(.body)
                            .lineSpacing(4)
                        Text("Started \(displayedEvent.startedAt.formatted(.dateTime.day().month().hour().minute()))")
                            .font(.caption)
                            .fontDesign(.monospaced)
                            .foregroundStyle(GridTheme.textTertiary)
                    }

                    explanationSection

                    VStack(alignment: .leading, spacing: 10) {
                        SectionLabel("Evidence IDs")
                        ForEach(displayedEvent.sourceIDs, id: \.self) { sourceID in
                            Text(sourceID)
                                .font(.caption)
                                .fontDesign(.monospaced)
                                .foregroundStyle(GridTheme.textPrimary)
                                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                .overlay(alignment: .bottom) { Hairline() }
                        }
                    }

                    if displayedEvent.isAuthoritativelyReported,
                       InspectionEndpoint.isValidEventID(displayedEvent.id) {
                        Button {
                            isHistoryPresented = true
                        } label: {
                            HStack(spacing: 12) {
                                Image(systemName: "clock.arrow.trianglehead.counterclockwise.rotate.90")
                                    .foregroundStyle(GridTheme.liveCyan)
                                    .frame(width: 22)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text("Revision history")
                                        .font(.subheadline.weight(.semibold))
                                        .foregroundStyle(GridTheme.textPrimary)
                                    Text("Current state, publisher changes and provenance")
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
                        .accessibilityHint("Opens the immutable reported-event revision list")
                    }

                    if let snapshot {
                        Text("Grid snapshot as of \(snapshot.timestamp.formatted(.dateTime.hour().minute())). Retrieved \(snapshot.retrievedAt.formatted(.dateTime.hour().minute().second())).")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.textTertiary)
                    }

                    if isLoadingDetails {
                        Label("Refreshing the reported event…", systemImage: "arrow.triangle.2.circlepath")
                            .font(.caption)
                            .foregroundStyle(GridTheme.textTertiary)
                    } else if let detailError {
                        Text("The summary above is the last confirmed copy. \(detailError)")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.staleAmber)
                    }
                }
                .padding(GridTheme.horizontalPadding)
            }
            .navigationTitle("Grid event")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        sharePayload = .event(displayedEvent, snapshot: snapshot)
                    } label: {
                        Image(systemName: "square.and.arrow.up")
                            .frame(width: 44, height: 44)
                            .contentShape(Rectangle())
                    }
                    .foregroundStyle(GridTheme.textSecondary)
                    .accessibilityLabel("Share this grid event")
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }.foregroundStyle(GridTheme.liveCyan)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task(id: event.id) { await refreshEvent() }
        .sheet(item: $sharePayload) { payload in
            ShareCardSheet(payload: payload)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
        .sheet(isPresented: $isHistoryPresented) {
            EventHistorySheet(eventID: displayedEvent.id, eventTitle: displayedEvent.title)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.background)
        }
    }

    @ViewBuilder
    private var explanationSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Interpretation")

            if let response = explanation {
                Text(response.explanation.headline)
                    .font(.headline)
                Text(response.explanation.plainLanguage)
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(4)
                    .textSelection(.enabled)
                if let why = response.explanation.whyItMatters {
                    Text(why)
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textPrimary)
                }
                if let caveat = response.explanation.caveat {
                    Label(caveat, systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                ForEach(response.citations) { citation in
                    Link(destination: citation.canonicalURL) {
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(citation.title).font(.caption.weight(.semibold))
                                Text(citation.publisher).font(.caption2).foregroundStyle(GridTheme.textTertiary)
                            }
                            Spacer()
                            Image(systemName: "arrow.up.right").font(.caption2)
                        }
                        .frame(minHeight: 44)
                    }
                    .foregroundStyle(GridTheme.liveCyan)
                }
                if response.usedFallback {
                    Text("A validated template was used because model-generated wording was unavailable.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }
            } else if isExplaining {
                HStack(spacing: 9) {
                    ProgressView().tint(GridTheme.liveCyan)
                    Text("Building a source-grounded explanation…")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                .frame(minHeight: 44)
            } else {
                Text("50Hz can explain this reported event using its validated evidence packet. The explanation will distinguish reported cause from simultaneous grid movement.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(4)
                if let explanationError {
                    Text(explanationError)
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                }
                Button {
                    Task { await explain() }
                } label: {
                    Label(explanationError == nil ? "Explain this event" : "Try explanation again", systemImage: "sparkles")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(minHeight: 44)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func refreshEvent() async {
        isLoadingDetails = true
        defer { isLoadingDetails = false }
        do {
            resolvedEvent = try await model.eventDetails(id: event.id)
            detailError = nil
        } catch {
            guard !Task.isCancelled else { return }
            detailError = error.localizedDescription
        }
    }

    private func explain() async {
        guard !isExplaining else { return }
        isExplaining = true
        explanationError = nil
        defer { isExplaining = false }
        do {
            explanation = try await model.explainEvent(id: displayedEvent.id)
        } catch {
            guard !Task.isCancelled else { return }
            explanationError = error.localizedDescription
        }
    }
}
