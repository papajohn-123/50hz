import SwiftUI

struct ReportedEventsListSheet: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var selectedEvent: GridEvent?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Active reported events")
                            .font(.system(.title2, design: .rounded, weight: .medium))
                            .tracking(-0.5)
                        Text("Authoritative notices and system warnings currently returned by 50Hz. The order is set by the server; reported events are not claims about causation elsewhere on the grid.")
                            .font(.subheadline)
                            .foregroundStyle(GridTheme.textSecondary)
                            .lineSpacing(4)
                    }

                    eventContent
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
                .padding(.bottom, 34)
            }
            .scrollIndicators(.hidden)
            .navigationTitle("Reported events")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(GridTheme.liveCyan)
                }
            }
            .refreshable { await model.refreshEvents() }
        }
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .sheet(item: $selectedEvent) { event in
            EventDetailSheet(event: event, snapshot: model.presentedSnapshot)
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
    }

    @ViewBuilder
    private var eventContent: some View {
        if model.events.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Current list", trailing: "0")
                Text(model.eventsError == nil ? "No active reported events are in the current response." : "The active event list could not be refreshed, and no saved events are available.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)
                if let error = model.eventsError {
                    Text(error)
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                    retryButton
                }
            }
            .frame(maxWidth: .infinity, minHeight: 150, alignment: .topLeading)
        } else {
            VStack(alignment: .leading, spacing: 0) {
                SectionLabel("Current list", trailing: "\(min(model.events.count, 100))")
                    .padding(.bottom, 8)

                if let error = model.eventsError {
                    HStack(alignment: .top, spacing: 9) {
                        Image(systemName: "clock.arrow.circlepath")
                            .foregroundStyle(GridTheme.staleAmber)
                        VStack(alignment: .leading, spacing: 5) {
                            Text("Showing the last confirmed event list. \(error)")
                                .font(.caption)
                                .foregroundStyle(GridTheme.textSecondary)
                            retryButton
                        }
                    }
                    .padding(.vertical, 10)
                }

                ForEach(model.events.prefix(100)) { event in
                    Button {
                        selectedEvent = event
                    } label: {
                        eventRow(event)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens reported facts, interpretation and revision history")
                }

                if model.events.count > 100 {
                    Text("Showing the first 100 events from the saved response.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.staleAmber)
                        .padding(.top, 12)
                }
            }
        }
    }

    private func eventRow(_ event: GridEvent) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text("REPORTED · \(event.severity.uppercased())")
                        .font(.caption2.weight(.semibold))
                        .fontDesign(.monospaced)
                        .tracking(0.4)
                        .foregroundStyle(ReportedEventsPresentation.severityColor(event.severity))
                    Spacer(minLength: 8)
                    Text(event.startedAt.formatted(.dateTime.day().month(.abbreviated).hour().minute()))
                        .font(.caption2)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                Text(event.title)
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                    .multilineTextAlignment(.leading)
                Text(event.summary)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
                Text(event.sourceIDs.joined(separator: " · "))
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .lineLimit(2)
            }
            Image(systemName: "chevron.right")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
                .padding(.top, 4)
                .accessibilityHidden(true)
        }
        .frame(maxWidth: .infinity, minHeight: 102, alignment: .leading)
        .padding(.vertical, 12)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
    }

    private var retryButton: some View {
        Button("Retry") { Task { await model.refreshEvents() } }
            .font(.caption.weight(.semibold))
            .foregroundStyle(GridTheme.liveCyan)
            .frame(minHeight: 44, alignment: .leading)
    }
}

enum ReportedEventsPresentation {
    static func severityColor(_ raw: String) -> Color {
        switch raw.lowercased() {
        case "important", "critical", "material": GridTheme.warning
        case "notable": GridTheme.staleAmber
        case "info": GridTheme.liveCyan
        default: GridTheme.textTertiary
        }
    }
}
