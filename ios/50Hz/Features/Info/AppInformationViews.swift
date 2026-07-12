import SwiftUI

struct OpenInfoAction {
    let action: () -> Void

    func callAsFunction() {
        action()
    }
}

private struct OpenInfoKey: EnvironmentKey {
    static let defaultValue = OpenInfoAction(action: {})
}

extension EnvironmentValues {
    var openInfo: OpenInfoAction {
        get { self[OpenInfoKey.self] }
        set { self[OpenInfoKey.self] = newValue }
    }
}

enum WelcomePresentationPolicy {
    static func shouldPresent(hasCompletedWelcome: Bool) -> Bool {
        !hasCompletedWelcome
    }
}

enum AppVersionText {
    static func make(version: String?, build: String?) -> String {
        let version = version?.trimmingCharacters(in: .whitespacesAndNewlines)
        let build = build?.trimmingCharacters(in: .whitespacesAndNewlines)

        return switch (version?.isEmpty == false ? version : nil, build?.isEmpty == false ? build : nil) {
        case let (.some(version), .some(build)): "Version \(version) (\(build))"
        case let (.some(version), nil): "Version \(version)"
        case let (nil, .some(build)): "Build \(build)"
        case (nil, nil): "Development build"
        }
    }

    static var current: String {
        make(
            version: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String,
            build: Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String
        )
    }
}

struct GlobalInfoButton: View {
    @Environment(\.openInfo) private var openInfo

    var body: some View {
        Button { openInfo() } label: {
            Image(systemName: "info.circle")
                .font(.subheadline.weight(.medium))
                .foregroundStyle(GridTheme.textSecondary)
                .frame(width: 44, height: 44)
                .contentShape(Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Information and help")
    }
}

struct WelcomeSheet: View {
    let onComplete: () -> Void

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack {
                    Text("50Hz")
                        .font(.system(.title2, design: .rounded, weight: .bold))
                        .tracking(-0.8)
                        .accessibilityAddTraits(.isHeader)
                    Spacer()
                    Button(action: onComplete) {
                        Text("Skip")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(GridTheme.textSecondary)
                            .frame(minWidth: 44, minHeight: 44)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }

                WelcomeMapGraphic()
                    .frame(height: 255)
                    .padding(.horizontal, -GridTheme.horizontalPadding)

                VStack(alignment: .leading, spacing: 10) {
                    Text("Britain’s electricity system, alive.")
                        .font(.system(.largeTitle, design: .rounded, weight: .medium))
                        .tracking(-1.2)
                        .foregroundStyle(GridTheme.textPrimary)
                    Text("Start with the map, then move through time or inspect the evidence behind each reading.")
                        .font(.body)
                        .foregroundStyle(GridTheme.textSecondary)
                        .lineSpacing(4)
                }

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 18) { legend }
                    VStack(alignment: .leading, spacing: 9) { legend }
                }

                Hairline()

                welcomeFact(
                    symbol: "waveform.path.ecg",
                    title: "Observed and forecast stay distinct",
                    detail: "Cyan marks observed grid facts. Violet marks forecast frames.",
                    color: GridTheme.liveCyan
                )
                welcomeFact(
                    symbol: "text.bubble",
                    title: "Ask sends your question to the backend",
                    detail: "Your question, selected map time and optional region code go to the 50Hz backend. It may use OpenRouter to answer from bounded evidence.",
                    color: GridTheme.forecastViolet
                )
                welcomeFact(
                    symbol: "hand.raised",
                    title: "No account or location permission",
                    detail: "A postcode is optional and stored on this device. Predictions, missions and streaks also stay local.",
                    color: GridTheme.textSecondary
                )

                Button(action: onComplete) {
                    Text("Start exploring")
                        .font(.headline)
                        .foregroundStyle(GridTheme.background)
                        .frame(maxWidth: .infinity, minHeight: 52)
                        .background(GridTheme.liveCyan, in: Capsule())
                }
                .buttonStyle(.plain)
                .padding(.top, 4)
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 30)
        }
        .scrollIndicators(.hidden)
        .background(GridTheme.background.ignoresSafeArea())
        .preferredColorScheme(.dark)
    }

    @ViewBuilder
    private var legend: some View {
        Label("Observed", systemImage: "circle.fill")
            .foregroundStyle(GridTheme.liveCyan)
        Label("Forecast", systemImage: "circle.lefthalf.filled")
            .foregroundStyle(GridTheme.forecastViolet)
    }

    private func welcomeFact(symbol: String, title: String, detail: String, color: Color) -> some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: symbol)
                .font(.body.weight(.medium))
                .foregroundStyle(color)
                .frame(width: 24, height: 24)
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.textPrimary)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineSpacing(3)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct InfoHelpSheet: View {
    let onReplayWelcome: () -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    VStack(alignment: .leading, spacing: 7) {
                        Text("50Hz")
                            .font(.system(.largeTitle, design: .rounded, weight: .medium))
                            .tracking(-1.2)
                        Text("Britain’s electricity system, with its timing and limits left visible.")
                            .font(.subheadline)
                            .foregroundStyle(GridTheme.textSecondary)
                    }

                    infoSection(
                        title: "Data",
                        symbol: "clock.badge.checkmark",
                        body: "Current, delayed and offline describe whether required data is usable now. Delivery state tracks 50Hz receiving a source; fact state tracks when the underlying reading applies. The displayed supply mix is explicitly partial, not a complete Great Britain energy balance."
                    )

                    infoSection(
                        title: "AI",
                        symbol: "sparkles",
                        body: "Ask the Grid sends the text you enter, the selected map time and an optional region code to the 50Hz backend. The backend may call an OpenRouter-hosted model after gathering bounded evidence. Answers can still be incomplete or wrong, and the model cannot take actions on the grid or your device."
                    )

                    infoSection(
                        title: "Privacy",
                        symbol: "hand.raised",
                        body: "50Hz requires no account and does not request location permission. An optional postcode and all notebook participation state remain on this device."
                    )

                    VStack(alignment: .leading, spacing: 8) {
                        SectionLabel("Help")
                        Link(destination: URL(string: "https://50hz-api-production.up.railway.app/privacy")!) {
                            helpRow("Privacy policy", symbol: "lock")
                        }
                        Link(destination: URL(string: "https://50hz-api-production.up.railway.app/support")!) {
                            helpRow("Support", symbol: "lifepreserver")
                        }
                        Button(action: onReplayWelcome) {
                            helpRow("Replay welcome", symbol: "arrow.counterclockwise")
                        }
                        .buttonStyle(.plain)
                    }

                    Text(AppVersionText.current)
                        .font(.caption2)
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .padding(GridTheme.horizontalPadding)
                .padding(.bottom, 30)
            }
            .scrollIndicators(.hidden)
            .navigationTitle("Info & Help")
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

    private func infoSection(title: String, symbol: String, body: String) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: symbol)
                .font(.headline)
                .foregroundStyle(GridTheme.textPrimary)
            Text(body)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .lineSpacing(4)
            Hairline()
                .padding(.top, 4)
        }
    }

    private func helpRow(_ title: String, symbol: String) -> some View {
        HStack(spacing: 12) {
            Image(systemName: symbol)
                .foregroundStyle(GridTheme.liveCyan)
                .frame(width: 22)
            Text(title)
                .foregroundStyle(GridTheme.textPrimary)
            Spacer()
            Image(systemName: title == "Replay welcome" ? "chevron.right" : "arrow.up.right")
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .font(.subheadline.weight(.medium))
        .frame(minHeight: 44)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) { Hairline() }
    }
}

private struct WelcomeMapGraphic: View {
    private let observed = [
        CGPoint(x: 0.40, y: 0.22),
        CGPoint(x: 0.62, y: 0.42),
        CGPoint(x: 0.55, y: 0.68),
        CGPoint(x: 0.68, y: 0.80)
    ]
    private let forecast = [
        CGPoint(x: 0.34, y: 0.58),
        CGPoint(x: 0.48, y: 0.84),
        CGPoint(x: 0.72, y: 0.74)
    ]

    var body: some View {
        Canvas { context, size in
            let rect = CGRect(x: size.width * 0.18, y: 8, width: size.width * 0.64, height: size.height - 16)
            let shape = BritainShape().path(in: rect)

            var glow = context
            glow.addFilter(.blur(radius: 12))
            glow.stroke(shape, with: .color(GridTheme.liveCyan.opacity(0.22)), lineWidth: 4)

            context.fill(
                shape,
                with: .linearGradient(
                    Gradient(colors: [Color(hex: 0x182333), Color(hex: 0x090F18)]),
                    startPoint: CGPoint(x: rect.midX, y: rect.minY),
                    endPoint: CGPoint(x: rect.midX, y: rect.maxY)
                )
            )
            context.stroke(shape, with: .color(GridTheme.liveCyan.opacity(0.42)), lineWidth: 1)

            let hub = project(CGPoint(x: 0.67, y: 0.80), into: rect)
            for point in observed {
                draw(point: point, to: hub, color: GridTheme.liveCyan, in: rect, context: &context)
            }
            for point in forecast {
                draw(point: point, to: hub, color: GridTheme.forecastViolet, in: rect, context: &context)
            }
        }
        .background(
            RadialGradient(
                colors: [GridTheme.liveCyan.opacity(0.10), Color.clear],
                center: .center,
                startRadius: 8,
                endRadius: 180
            )
        )
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Illustrative map of Great Britain. Cyan marks observed data and violet marks forecast data.")
    }

    private func draw(
        point: CGPoint,
        to hub: CGPoint,
        color: Color,
        in rect: CGRect,
        context: inout GraphicsContext
    ) {
        let start = project(point, into: rect)
        var line = Path()
        line.move(to: start)
        line.addLine(to: hub)
        context.stroke(line, with: .color(color.opacity(0.25)), lineWidth: 0.8)

        var glow = context
        glow.addFilter(.blur(radius: 5))
        glow.fill(Path(ellipseIn: CGRect(x: start.x - 5, y: start.y - 5, width: 10, height: 10)), with: .color(color.opacity(0.35)))
        context.fill(Path(ellipseIn: CGRect(x: start.x - 2.5, y: start.y - 2.5, width: 5, height: 5)), with: .color(color))
    }

    private func project(_ point: CGPoint, into rect: CGRect) -> CGPoint {
        CGPoint(x: rect.minX + point.x * rect.width, y: rect.minY + point.y * rect.height)
    }
}
