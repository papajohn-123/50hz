import SwiftUI

struct EventDetailSheet: View {
    let event: GridEvent
    let snapshot: GridSnapshot?
    @Environment(\.dismiss) private var dismiss
    @State private var sharePayload: GridShareCardPayload?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    HStack(spacing: 8) {
                        Circle().fill(GridTheme.warning).frame(width: 7, height: 7)
                        Text(event.evidenceClass.uppercased())
                            .font(.caption2.weight(.semibold))
                            .fontDesign(.monospaced)
                            .tracking(0.8)
                            .foregroundStyle(GridTheme.warning)
                    }

                    Text(event.title)
                        .font(.title2.weight(.medium))

                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel("Reported facts")
                        Text(event.summary)
                            .font(.body)
                            .lineSpacing(4)
                        Text("Started \(event.startedAt.formatted(.dateTime.day().month().hour().minute()))")
                            .font(.caption)
                            .fontDesign(.monospaced)
                            .foregroundStyle(GridTheme.textTertiary)
                    }

                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel("Interpretation")
                        Text("50Hz is showing this because an authoritative notice is attached to the event. Any simultaneous movement in gas, demand or imports remains an observation—not proof that this event caused it.")
                            .font(.subheadline)
                            .foregroundStyle(GridTheme.textSecondary)
                            .lineSpacing(4)
                    }

                    VStack(alignment: .leading, spacing: 10) {
                        SectionLabel("Evidence IDs")
                        ForEach(event.sourceIDs, id: \.self) { sourceID in
                            Text(sourceID)
                                .font(.caption)
                                .fontDesign(.monospaced)
                                .foregroundStyle(GridTheme.textPrimary)
                                .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                                .overlay(alignment: .bottom) { Hairline() }
                        }
                    }

                    if let snapshot {
                        Text("Grid snapshot as of \(snapshot.timestamp.formatted(.dateTime.hour().minute())). Retrieved \(snapshot.retrievedAt.formatted(.dateTime.hour().minute().second())).")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.textTertiary)
                    }
                }
                .padding(GridTheme.horizontalPadding)
            }
            .navigationTitle("Grid event")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        sharePayload = .event(event, snapshot: snapshot)
                    } label: {
                        Image(systemName: "square.and.arrow.up")
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
        .sheet(item: $sharePayload) { payload in
            ShareCardSheet(payload: payload)
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.surface)
        }
    }
}
