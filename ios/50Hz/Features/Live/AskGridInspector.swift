import SwiftUI

struct AskGridInspector: View {
    let snapshot: GridSnapshot
    @Environment(\.dismiss) private var dismiss
    @State private var question = ""
    @State private var askedQuestion = "Why is wind leading right now?"

    private var answer: String {
        let wind = snapshot.reading(for: .wind)
        let gas = snapshot.reading(for: .gas)
        guard let wind else { return "There is not enough confirmed evidence in this snapshot to answer that." }

        if askedQuestion.localizedCaseInsensitiveContains("charge") {
            return "The national carbon forecast is expected to be lowest after 01:00. For Central London, the current fixture recommends 01:30–04:00. This is a forecast, not a guarantee."
        }
        if askedQuestion.localizedCaseInsensitiveContains("import") || askedQuestion.localizedCaseInsensitiveContains("export") {
            let net = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts }
            return "The reported interconnector flows sum to \(abs(Int(net))) MW \(net >= 0 ? "importing into" : "exporting from") Britain. Individual links can move in opposite directions at the same time."
        }
        let gasGW = ((gas?.megawatts ?? 0) / 1_000).formatted(.number.precision(.fractionLength(1)))
        return "Wind is producing \((wind.megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))) GW, or \(Int(wind.share * 100))% of the generation shown. That places it #\(wind.rank) in this snapshot. Gas is at \(gasGW) GW. The data shows the difference, but does not by itself establish a cause."
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(askedQuestion)
                            .font(.title2.weight(.medium))
                            .foregroundStyle(GridTheme.textPrimary)
                        Label("Bounded analysis", systemImage: "checkmark.shield")
                            .font(.caption)
                            .foregroundStyle(GridTheme.liveCyan)
                    }

                    Text(answer)
                        .font(.body)
                        .foregroundStyle(GridTheme.textPrimary)
                        .lineSpacing(5)
                        .textSelection(.enabled)

                    VStack(alignment: .leading, spacing: 10) {
                        SectionLabel("Qualification")
                        Text("This answer uses only the validated facts in the selected snapshot. It will not infer a fault, outage or cause unless an authoritative notice reports one.")
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                            .padding(12)
                            .background(GridTheme.forecastViolet.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
                    }

                    VStack(alignment: .leading, spacing: 12) {
                        SectionLabel("Evidence", trailing: snapshot.timestamp.formatted(.dateTime.hour().minute()))
                        ForEach(snapshot.sources) { source in
                            HStack(alignment: .top, spacing: 12) {
                                Image(systemName: "doc.text.magnifyingglass")
                                    .foregroundStyle(GridTheme.liveCyan)
                                VStack(alignment: .leading, spacing: 3) {
                                    Text(source.name).font(.subheadline.weight(.medium))
                                    Text("\(source.dataset) · observed \(source.observedAt.formatted(.dateTime.hour().minute()))")
                                        .font(.caption2)
                                        .fontDesign(.monospaced)
                                        .foregroundStyle(GridTheme.textTertiary)
                                }
                                Spacer()
                            }
                            .accessibilityElement(children: .combine)
                        }
                    }

                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel("Try asking")
                        suggestion("Are we importing or exporting?")
                        suggestion("When should I charge tonight?")
                        suggestion("Is this unusually clean?")
                    }
                }
                .padding(GridTheme.horizontalPadding)
            }
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 9) {
                    TextField("Ask about this grid state", text: $question)
                        .textFieldStyle(.plain)
                        .padding(.horizontal, 14)
                        .frame(minHeight: 44)
                        .background(GridTheme.surfaceRaised, in: Capsule())
                    Button {
                        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty else { return }
                        askedQuestion = trimmed
                        question = ""
                    } label: {
                        Image(systemName: "arrow.up")
                            .font(.headline)
                            .frame(width: 44, height: 44)
                            .background(GridTheme.liveCyan, in: Circle())
                            .foregroundStyle(GridTheme.background)
                    }
                    .accessibilityLabel("Ask question")
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
                .padding(.vertical, 10)
                .background(.ultraThinMaterial)
            }
            .navigationTitle("Ask the Grid")
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

    private func suggestion(_ text: String) -> some View {
        Button {
            withAnimation(.easeOut(duration: 0.18)) { askedQuestion = text }
        } label: {
            HStack {
                Text(text).font(.subheadline)
                Spacer()
                Image(systemName: "arrow.up.right").font(.caption)
            }
            .foregroundStyle(GridTheme.textSecondary)
            .frame(minHeight: 44)
            .overlay(alignment: .bottom) { Hairline() }
        }
        .buttonStyle(.plain)
    }
}
