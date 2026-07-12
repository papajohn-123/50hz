import SwiftUI
import UIKit

struct GridShareMetric: Identifiable, Sendable {
    let id: String
    let label: String
    let value: String
    let unit: String
}

struct GridShareCardPayload: Identifiable, Sendable {
    enum Accent: Sendable {
        case observed
        case forecast
        case event
    }

    let id = UUID()
    let eyebrow: String
    let title: String
    let detail: String
    let timestamp: Date
    let metrics: [GridShareMetric]
    let sourceLine: String
    let accent: Accent

    static func current(_ snapshot: GridSnapshot) -> GridShareCardPayload {
        var metrics: [GridShareMetric] = []
        if let frequency = snapshot.frequency {
            metrics.append(GridShareMetric(id: "frequency", label: "Frequency", value: frequency.formatted(decimals: 2), unit: "Hz"))
        }
        metrics.append(
            GridShareMetric(
                id: "demand",
                label: "Demand",
                value: (snapshot.demand.value / 1_000).formatted(.number.precision(.fractionLength(1))),
                unit: "GW"
            )
        )
        metrics.append(
            GridShareMetric(
                id: "carbon",
                label: "Carbon",
                value: snapshot.carbonIntensity.formatted(),
                unit: "g/kWh"
            )
        )

        return GridShareCardPayload(
            eyebrow: snapshot.demand.factClass == .forecast
                ? "BRITAIN · FORECAST FRAME"
                : (snapshot.freshness == .live ? "BRITAIN · LIVE GRID" : "BRITAIN · CONFIRMED SNAPSHOT"),
            title: "\(snapshot.headline.cleanliness) · \(snapshot.headline.balance) · \(snapshot.headline.energyPosition)",
            detail: snapshot.headline.publicInterpretation(for: snapshot.generation),
            timestamp: snapshot.timestamp,
            metrics: metrics,
            sourceLine: snapshot.demand.factClass == .forecast ? "50Hz forecast timeline" : sourceNames(snapshot.sources),
            accent: snapshot.demand.factClass == .forecast ? .forecast : .observed
        )
    }

    static func event(_ event: GridEvent, snapshot: GridSnapshot?) -> GridShareCardPayload {
        let evidence = GridShareMetric(
            id: "evidence",
            label: "Evidence",
            value: event.isAuthoritativelyReported ? "Reported" : "Observed",
            unit: event.evidenceClass
        )
        return GridShareCardPayload(
            eyebrow: "BRITAIN · GRID MOMENT",
            title: event.title,
            detail: event.summary,
            timestamp: event.startedAt,
            metrics: [evidence],
            sourceLine: snapshot.map { sourceNames($0.sources) } ?? event.sourceIDs.joined(separator: " · "),
            accent: .event
        )
    }

    static func moment(
        title: String,
        detail: String,
        timestamp: Date,
        factClass: FactClass,
        sources: [SourceReference]
    ) -> GridShareCardPayload {
        GridShareCardPayload(
            eyebrow: factClass == .forecast ? "BRITAIN · FORECAST MOMENT" : "BRITAIN · GRID MOMENT",
            title: title,
            detail: detail,
            timestamp: timestamp,
            metrics: [
                GridShareMetric(
                    id: "classification",
                    label: "Classification",
                    value: factClass.rawValue.capitalized,
                    unit: factClass == .forecast ? "Latest available issue" : "Confirmed timeline"
                )
            ],
            sourceLine: factClass == .forecast ? "50Hz forecast timeline" : sourceNames(sources),
            accent: factClass == .forecast ? .forecast : .observed
        )
    }

    private static func sourceNames(_ sources: [SourceReference]) -> String {
        var seen = Set<String>()
        return sources.map(\.name).filter { seen.insert($0).inserted }.joined(separator: " · ")
    }
}

struct GridShareCard: View {
    let payload: GridShareCardPayload

    private var accent: Color {
        switch payload.accent {
        case .observed: GridTheme.liveCyan
        case .forecast: GridTheme.forecastViolet
        case .event: GridTheme.warning
        }
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [GridTheme.background, Color(hex: 0x0B111A), GridTheme.background],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            BritainShape()
                .fill(accent.opacity(0.045))
                .overlay(BritainShape().stroke(accent.opacity(0.16), lineWidth: 1))
                .frame(width: 245, height: 360)
                .offset(x: 82, y: 48)

            VStack(alignment: .leading, spacing: 0) {
                HStack(alignment: .firstTextBaseline) {
                    Text("50Hz")
                        .font(.system(size: 25, weight: .bold, design: .rounded))
                        .tracking(-0.8)
                    Spacer()
                    Circle()
                        .fill(accent)
                        .frame(width: 7, height: 7)
                        .shadow(color: accent.opacity(0.7), radius: 5)
                    Text(payload.timestamp.formatted(.dateTime.hour().minute()))
                        .font(.system(size: 10, weight: .medium, design: .monospaced))
                        .foregroundStyle(GridTheme.textSecondary)
                }

                Spacer().frame(height: 76)

                Text(payload.eyebrow)
                    .font(.system(size: 9, weight: .semibold, design: .monospaced))
                    .tracking(1.1)
                    .foregroundStyle(accent)

                Text(payload.title)
                    .font(.system(size: 33, weight: .medium, design: .rounded))
                    .tracking(-1.25)
                    .foregroundStyle(GridTheme.textPrimary)
                    .lineLimit(4)
                    .minimumScaleFactor(0.72)
                    .padding(.top, 10)

                Text(payload.detail)
                    .font(.system(size: 14, weight: .regular, design: .default))
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(4)
                    .lineLimit(5)
                    .padding(.top, 14)

                Spacer(minLength: 26)

                Rectangle()
                    .fill(accent.opacity(0.32))
                    .frame(height: 1)

                HStack(alignment: .top, spacing: 16) {
                    ForEach(payload.metrics.prefix(3)) { metric in
                        VStack(alignment: .leading, spacing: 5) {
                            Text(metric.label.uppercased())
                                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                                .tracking(0.7)
                                .foregroundStyle(GridTheme.textTertiary)
                            Text(metric.value)
                                .font(.system(size: metric.value.count > 9 ? 14 : 20, weight: .medium, design: .monospaced))
                                .foregroundStyle(GridTheme.textPrimary)
                                .lineLimit(1)
                                .minimumScaleFactor(0.55)
                            Text(metric.unit)
                                .font(.system(size: 8, design: .monospaced))
                                .foregroundStyle(GridTheme.textTertiary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .padding(.top, 17)

                Spacer().frame(height: 24)

                HStack {
                    Text("Britain’s electricity system, alive.")
                    Spacer()
                    Text(payload.sourceLine)
                        .lineLimit(1)
                }
                .font(.system(size: 8, design: .monospaced))
                .foregroundStyle(GridTheme.textTertiary)
            }
            .padding(28)
        }
        .frame(width: 360, height: 600)
        .clipShape(RoundedRectangle(cornerRadius: 28))
        .overlay(RoundedRectangle(cornerRadius: 28).stroke(accent.opacity(0.14), lineWidth: 1))
        .environment(\.colorScheme, .dark)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("50Hz share card. \(payload.title). \(payload.detail)")
    }
}

struct RenderedGridShareCard {
    let image: UIImage
    let fileURL: URL
}

@MainActor
enum GridShareCardRenderer {
    static func render(_ payload: GridShareCardPayload) throws -> RenderedGridShareCard {
        let card = GridShareCard(payload: payload)
        let renderer = ImageRenderer(content: card)
        renderer.scale = 3
        renderer.isOpaque = true

        guard let image = renderer.uiImage, let data = image.pngData() else {
            throw GridShareCardError.renderFailed
        }

        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
            ?? URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        let directory = base.appendingPathComponent("50Hz/ShareCards", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        if let oldCards = try? FileManager.default.contentsOfDirectory(
            at: directory,
            includingPropertiesForKeys: nil
        ) {
            for oldCard in oldCards where oldCard.pathExtension.lowercased() == "png" {
                try? FileManager.default.removeItem(at: oldCard)
            }
        }
        let fileURL = directory.appendingPathComponent("50Hz-\(payload.id.uuidString).png")
        try data.write(to: fileURL, options: [.atomic, .completeFileProtectionUnlessOpen])
        return RenderedGridShareCard(image: image, fileURL: fileURL)
    }
}

enum GridShareCardError: LocalizedError {
    case renderFailed

    var errorDescription: String? { "The share card could not be rendered." }
}

struct ShareCardSheet: View {
    let payload: GridShareCardPayload
    @Environment(\.dismiss) private var dismiss
    @State private var rendered: RenderedGridShareCard?
    @State private var errorMessage: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    GridShareCard(payload: payload)
                        .scaleEffect(0.9)
                        .frame(width: 324, height: 540)
                        .shadow(color: Color.black.opacity(0.35), radius: 24, y: 10)

                    if let rendered {
                        ShareLink(
                            item: rendered.fileURL,
                            preview: SharePreview(payload.title, image: Image(uiImage: rendered.image))
                        ) {
                            Label("Share image", systemImage: "square.and.arrow.up")
                                .font(.headline)
                                .frame(maxWidth: .infinity, minHeight: 50)
                                .background(GridTheme.liveCyan, in: RoundedRectangle(cornerRadius: 13))
                                .foregroundStyle(GridTheme.background)
                        }
                    } else if let errorMessage {
                        Text(errorMessage)
                            .font(.caption)
                            .foregroundStyle(GridTheme.staleAmber)
                    } else {
                        ProgressView("Rendering locally…")
                            .tint(GridTheme.liveCyan)
                            .foregroundStyle(GridTheme.textSecondary)
                            .frame(minHeight: 50)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.bottom, 24)
            }
            .navigationTitle("Share this moment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(GridTheme.liveCyan)
                }
            }
        }
        .preferredColorScheme(.dark)
        .task(id: payload.id) {
            do { rendered = try GridShareCardRenderer.render(payload) }
            catch { errorMessage = error.localizedDescription }
        }
    }
}
