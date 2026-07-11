import SwiftUI

struct AskGridInspector: View {
    let snapshot: GridSnapshot
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var question = ""
    @State private var askedQuestion: String?
    @State private var answer: AskGridAnswer?
    @State private var isAsking = false
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    heading

                    if isAsking {
                        HStack(spacing: 10) {
                            ProgressView().tint(GridTheme.liveCyan)
                            Text("Checking validated grid evidence…")
                                .font(.subheadline)
                                .foregroundStyle(GridTheme.textSecondary)
                        }
                        .frame(maxWidth: .infinity, minHeight: 100, alignment: .leading)
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel("Checking validated grid evidence")
                    } else if let answer {
                        answerContent(answer)
                    } else if let errorMessage {
                        errorContent(errorMessage)
                    } else {
                        introduction
                    }

                    suggestions
                }
                .padding(GridTheme.horizontalPadding)
            }
            .safeAreaInset(edge: .bottom) { composer }
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

    private var heading: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(askedQuestion ?? "What would you like to understand?")
                .font(.title2.weight(.medium))
                .foregroundStyle(GridTheme.textPrimary)
            Label("Source-grounded analysis", systemImage: "checkmark.shield")
                .font(.caption)
                .foregroundStyle(GridTheme.liveCyan)
        }
    }

    private var introduction: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ask about the selected grid state, a change in generation, imports, carbon intensity, or a reported event.")
                .font(.body)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
            Text("50Hz asks its backend to gather bounded evidence before the model answers. It will not diagnose an outage from an output change alone.")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    @ViewBuilder
    private func answerContent(_ answer: AskGridAnswer) -> some View {
        Text(answer.answer)
            .font(.body)
            .foregroundStyle(GridTheme.textPrimary)
            .lineSpacing(5)
            .textSelection(.enabled)

        if !answer.limitations.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                SectionLabel("Qualification")
                ForEach(answer.limitations, id: \.self) { limitation in
                    Label(limitation, systemImage: "info.circle")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
            }
            .padding(12)
            .background(GridTheme.forecastViolet.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
        }

        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Evidence", trailing: "\(answer.freshness.uppercased()) · \(answer.asOf.formatted(.dateTime.hour().minute()))")
            if answer.citations.isEmpty {
                ForEach(answer.evidenceRefs, id: \.self) { sourceID in
                    evidenceIDRow(sourceID)
                }
            } else {
                ForEach(answer.citations) { citation in
                    Link(destination: citation.canonicalURL) {
                        HStack(alignment: .top, spacing: 12) {
                            Image(systemName: "doc.text.magnifyingglass")
                                .foregroundStyle(GridTheme.liveCyan)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(citation.title).font(.subheadline.weight(.medium))
                                Text(citation.publisher)
                                    .font(.caption2)
                                    .foregroundStyle(GridTheme.textTertiary)
                            }
                            Spacer()
                            Image(systemName: "arrow.up.right")
                                .font(.caption2)
                                .foregroundStyle(GridTheme.textTertiary)
                        }
                        .frame(minHeight: 44)
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens the source in your browser")
                }
            }
        }
    }

    private func evidenceIDRow(_ sourceID: String) -> some View {
        let source = snapshot.sources.first { $0.id == sourceID }
        return HStack(alignment: .top, spacing: 12) {
            Image(systemName: "doc.text.magnifyingglass")
                .foregroundStyle(GridTheme.liveCyan)
            VStack(alignment: .leading, spacing: 3) {
                Text(source?.name ?? sourceID).font(.subheadline.weight(.medium))
                Text(source.map { "\($0.dataset) · observed \($0.observedAt.formatted(.dateTime.hour().minute()))" } ?? "Evidence reference supplied by the backend")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Spacer()
        }
        .frame(minHeight: 44)
        .accessibilityElement(children: .combine)
    }

    private func errorContent(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("A grounded answer is unavailable", systemImage: "exclamationmark.bubble")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(GridTheme.staleAmber)
            Text(message)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
            if let askedQuestion {
                Button("Try again") { ask(askedQuestion) }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.staleAmber)
                    .frame(minHeight: 44)
            }
        }
        .padding(14)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }

    private var suggestions: some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel("Try asking")
            ForEach(suggestionTexts, id: \.self) { text in
                Button { ask(text) } label: {
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
                .disabled(isAsking)
            }
        }
    }

    private var suggestionTexts: [String] {
        let supplied = answer?.suggestedQuestions.filter { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } ?? []
        return Array((supplied.isEmpty ? [
            "Are we importing or exporting?",
            "Why is the leading source ahead?",
            "Is this unusually clean?"
        ] : supplied).prefix(3))
    }

    private var composer: some View {
        HStack(spacing: 9) {
            TextField("Ask about this grid state", text: $question)
                .textFieldStyle(.plain)
                .submitLabel(.send)
                .onSubmit(submit)
                .padding(.horizontal, 14)
                .frame(minHeight: 44)
                .background(GridTheme.surfaceRaised, in: Capsule())
                .disabled(isAsking)
            Button(action: submit) {
                Image(systemName: "arrow.up")
                    .font(.headline)
                    .frame(width: 44, height: 44)
                    .background(GridTheme.liveCyan, in: Circle())
                    .foregroundStyle(GridTheme.background)
            }
            .disabled(isAsking || question.trimmingCharacters(in: .whitespacesAndNewlines).count < 2)
            .accessibilityLabel("Ask question")
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    private func submit() {
        let trimmed = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 2 else { return }
        question = ""
        ask(trimmed)
    }

    private func ask(_ text: String) {
        guard !isAsking else { return }
        askedQuestion = text
        isAsking = true
        answer = nil
        errorMessage = nil

        Task {
            do {
                answer = try await model.askGrid(question: text)
            } catch {
                guard !Task.isCancelled else { return }
                errorMessage = error.localizedDescription
            }
            isAsking = false
        }
    }
}
