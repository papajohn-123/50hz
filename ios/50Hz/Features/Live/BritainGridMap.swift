import SwiftUI

struct BritainShape: Shape {
    func path(in rect: CGRect) -> Path {
        func p(_ x: CGFloat, _ y: CGFloat) -> CGPoint {
            CGPoint(x: rect.minX + (x * rect.width), y: rect.minY + (y * rect.height))
        }

        var path = Path()
        path.move(to: p(0.46, 0.03))
        path.addCurve(to: p(0.57, 0.11), control1: p(0.48, 0.05), control2: p(0.56, 0.06))
        path.addCurve(to: p(0.55, 0.20), control1: p(0.61, 0.15), control2: p(0.59, 0.19))
        path.addCurve(to: p(0.63, 0.29), control1: p(0.57, 0.23), control2: p(0.64, 0.24))
        path.addCurve(to: p(0.66, 0.38), control1: p(0.68, 0.33), control2: p(0.67, 0.36))
        path.addCurve(to: p(0.72, 0.48), control1: p(0.68, 0.42), control2: p(0.73, 0.44))
        path.addCurve(to: p(0.68, 0.57), control1: p(0.74, 0.52), control2: p(0.70, 0.55))
        path.addCurve(to: p(0.76, 0.66), control1: p(0.71, 0.60), control2: p(0.77, 0.61))
        path.addCurve(to: p(0.75, 0.75), control1: p(0.79, 0.70), control2: p(0.78, 0.73))
        path.addCurve(to: p(0.86, 0.81), control1: p(0.78, 0.77), control2: p(0.85, 0.77))
        path.addCurve(to: p(0.83, 0.87), control1: p(0.90, 0.84), control2: p(0.87, 0.87))
        path.addCurve(to: p(0.69, 0.88), control1: p(0.79, 0.88), control2: p(0.74, 0.87))
        path.addCurve(to: p(0.58, 0.96), control1: p(0.65, 0.90), control2: p(0.63, 0.95))
        path.addCurve(to: p(0.48, 0.94), control1: p(0.54, 0.98), control2: p(0.50, 0.97))
        path.addCurve(to: p(0.39, 0.89), control1: p(0.46, 0.91), control2: p(0.42, 0.90))
        path.addCurve(to: p(0.29, 0.87), control1: p(0.36, 0.88), control2: p(0.31, 0.90))
        path.addCurve(to: p(0.20, 0.79), control1: p(0.25, 0.85), control2: p(0.22, 0.82))
        path.addCurve(to: p(0.27, 0.72), control1: p(0.18, 0.75), control2: p(0.22, 0.73))
        path.addCurve(to: p(0.34, 0.67), control1: p(0.30, 0.71), control2: p(0.34, 0.70))
        path.addCurve(to: p(0.31, 0.61), control1: p(0.34, 0.64), control2: p(0.31, 0.64))
        path.addCurve(to: p(0.38, 0.54), control1: p(0.31, 0.57), control2: p(0.36, 0.56))
        path.addCurve(to: p(0.37, 0.46), control1: p(0.40, 0.51), control2: p(0.39, 0.48))
        path.addCurve(to: p(0.29, 0.39), control1: p(0.35, 0.43), control2: p(0.29, 0.43))
        path.addCurve(to: p(0.32, 0.31), control1: p(0.27, 0.35), control2: p(0.29, 0.32))
        path.addCurve(to: p(0.27, 0.23), control1: p(0.34, 0.28), control2: p(0.30, 0.25))
        path.addCurve(to: p(0.34, 0.18), control1: p(0.25, 0.20), control2: p(0.29, 0.18))
        path.addCurve(to: p(0.37, 0.09), control1: p(0.36, 0.15), control2: p(0.34, 0.12))
        path.addCurve(to: p(0.46, 0.03), control1: p(0.39, 0.06), control2: p(0.44, 0.06))
        path.closeSubpath()
        return path
    }
}

private struct MapNode: Identifiable {
    let id: Int
    let fuel: FuelKind
    let point: CGPoint
    let label: String
}

private struct MapLink: Identifiable {
    let id: Int
    let start: CGPoint
    let end: CGPoint
    let curve: CGFloat
    let fuel: FuelKind
    let magnitude: Double
    let reversed: Bool
}

struct BritainGridMap: View {
    let snapshot: GridSnapshot
    let selectedFuel: FuelKind?
    let isForecast: Bool
    let onEventTap: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private let nodes: [MapNode] = [
        MapNode(id: 11, fuel: .wind, point: CGPoint(x: 0.40, y: 0.20), label: "Scottish wind"),
        MapNode(id: 12, fuel: .wind, point: CGPoint(x: 0.62, y: 0.40), label: "North Sea wind"),
        MapNode(id: 13, fuel: .nuclear, point: CGPoint(x: 0.42, y: 0.50), label: "North-west nuclear"),
        MapNode(id: 14, fuel: .biomass, point: CGPoint(x: 0.62, y: 0.55), label: "Yorkshire biomass"),
        MapNode(id: 15, fuel: .hydro, point: CGPoint(x: 0.33, y: 0.65), label: "Welsh hydro"),
        MapNode(id: 16, fuel: .gas, point: CGPoint(x: 0.58, y: 0.70), label: "Midlands gas"),
        MapNode(id: 17, fuel: .solar, point: CGPoint(x: 0.50, y: 0.84), label: "Southern solar"),
        MapNode(id: 18, fuel: .nuclear, point: CGPoint(x: 0.72, y: 0.77), label: "South-east nuclear")
    ]

    var body: some View {
        TimelineView(.animation(minimumInterval: reduceMotion ? 1 : 1 / 30, paused: reduceMotion)) { timeline in
            Canvas(rendersAsynchronously: true) { context, size in
                drawScene(context: &context, size: size, date: timeline.date)
            }
        }
        .overlay(alignment: .topTrailing) {
            Text("ILLUSTRATIVE FLOWS")
                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                .tracking(0.8)
                .foregroundStyle(GridTheme.textTertiary)
                .padding(.top, 10)
                .padding(.trailing, 10)
        }
        .overlay(alignment: .topLeading) {
            if let event = snapshot.activeEvent {
                Button(action: onEventTap) {
                    HStack(spacing: 8) {
                        Circle().fill(GridTheme.warning).frame(width: 6, height: 6)
                        Text(event.title)
                            .font(.caption.weight(.medium))
                            .lineLimit(1)
                        Image(systemName: "chevron.right")
                            .font(.caption2)
                    }
                    .foregroundStyle(GridTheme.textPrimary)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 44)
                    .background(GridTheme.surface.opacity(0.92), in: Capsule())
                    .overlay(Capsule().stroke(GridTheme.warning.opacity(0.35), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .padding(.top, 28)
                .padding(.leading, 10)
            }
        }
        .background(
            RadialGradient(
                colors: [
                    (isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan).opacity(0.08),
                    Color.clear
                ],
                center: .center,
                startRadius: 10,
                endRadius: 210
            )
        )
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Abstract map of Great Britain's electricity system")
        .accessibilityValue(accessibilityValue)
        .accessibilityHint("Generation nodes and interconnector paths are illustrative, not literal transmission routes.")
    }

    private var accessibilityValue: String {
        let leading = snapshot.generation.sorted { $0.megawatts > $1.megawatts }.prefix(3)
            .map { "\($0.fuel.displayName) \(($0.megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))) gigawatts" }
            .joined(separator: ", ")
        let generationSummary = leading.isEmpty ? "Generation mix unavailable for this frame." : "Leading sources: \(leading)."
        guard !snapshot.interconnectors.isEmpty else {
            return "\(generationSummary) Interconnector position unavailable for this frame."
        }
        let position = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts } >= 0 ? "net importing" : "net exporting"
        return "\(generationSummary) Britain is \(position)."
    }

    private func drawScene(context: inout GraphicsContext, size: CGSize, date: Date) {
        let mapRect = CGRect(x: size.width * 0.14, y: 8, width: size.width * 0.72, height: size.height - 18)
        let shape = BritainShape().path(in: mapRect)
        let accent = isForecast ? GridTheme.forecastViolet : GridTheme.liveCyan
        let frequencyDeviation = abs((snapshot.frequency?.value ?? 50) - 50)
        let breatheAmount = min(frequencyDeviation * 5, 0.35)
        let breathe = reduceMotion ? 0 : sin(date.timeIntervalSinceReferenceDate * 1.45) * (0.03 + breatheAmount)

        drawField(context: &context, size: size, date: date, accent: accent)

        var coastGlow = context
        coastGlow.addFilter(.blur(radius: 9))
        coastGlow.stroke(shape, with: .color(accent.opacity(0.20 + breathe)), lineWidth: 3)

        context.fill(
            shape,
            with: .linearGradient(
                Gradient(colors: [Color(hex: 0x182333).opacity(0.92), Color(hex: 0x0A111B).opacity(0.97)]),
                startPoint: CGPoint(x: mapRect.midX, y: mapRect.minY),
                endPoint: CGPoint(x: mapRect.midX, y: mapRect.maxY)
            )
        )
        context.stroke(shape, with: .color(accent.opacity(0.34)), lineWidth: 0.9)

        drawTopography(context: &context, in: mapRect, clippedTo: shape, accent: accent)
        let links = mapLinks(in: mapRect)
        drawLinks(links, context: &context, date: date, accent: accent)
        drawNodes(context: &context, in: mapRect, date: date)

        if snapshot.freshness == .critical, let event = snapshot.activeEvent {
            let eventPoint = project(CGPoint(x: 0.66, y: 0.52), into: mapRect)
            drawEventPulse(context: &context, at: eventPoint, date: date, title: event.title)
        }
    }

    private func drawField(context: inout GraphicsContext, size: CGSize, date: Date, accent: Color) {
        for index in 0..<38 {
            let x = CGFloat((index * 47) % 101) / 101 * size.width
            let y = CGFloat((index * 73 + 19) % 103) / 103 * size.height
            let phase = reduceMotion ? 0.5 : (sin(date.timeIntervalSinceReferenceDate * 0.18 + Double(index)) + 1) / 2
            let radius = CGFloat(index % 3 + 1) * 0.45
            context.fill(
                Path(ellipseIn: CGRect(x: x, y: y, width: radius, height: radius)),
                with: .color(accent.opacity(0.05 + phase * 0.08))
            )
        }
    }

    private func drawTopography(context: inout GraphicsContext, in rect: CGRect, clippedTo shape: Path, accent: Color) {
        var clipped = context
        clipped.clip(to: shape)
        for index in 0..<11 {
            let inset = CGFloat(index) * 10 - 30
            let topoRect = rect.insetBy(dx: inset, dy: CGFloat(index) * 16 - 50)
            let path = Path(ellipseIn: topoRect)
            clipped.stroke(path, with: .color(accent.opacity(0.045)), lineWidth: 0.6)
        }

        if isForecast {
            for index in stride(from: -Int(rect.height), through: Int(rect.width), by: 16) {
                var hatch = Path()
                hatch.move(to: CGPoint(x: rect.minX + CGFloat(index), y: rect.maxY))
                hatch.addLine(to: CGPoint(x: rect.minX + CGFloat(index) + rect.height, y: rect.minY))
                clipped.stroke(hatch, with: .color(GridTheme.forecastViolet.opacity(0.045)), lineWidth: 0.6)
            }
        }
    }

    private func mapLinks(in rect: CGRect) -> [MapLink] {
        let london = project(CGPoint(x: 0.68, y: 0.79), into: rect)
        var links = nodes.map { node in
            let output = snapshot.reading(for: node.fuel)?.megawatts ?? 500
            return MapLink(
                id: node.id,
                start: project(node.point, into: rect),
                end: london,
                curve: node.point.x < 0.5 ? -18 : 18,
                fuel: node.fuel,
                magnitude: output,
                reversed: false
            )
        }

        let connectorAnchors = [
            CGPoint(x: rect.maxX + 34, y: rect.minY + rect.height * 0.36),
            CGPoint(x: rect.maxX + 42, y: rect.minY + rect.height * 0.67),
            CGPoint(x: rect.maxX + 30, y: rect.minY + rect.height * 0.84),
            CGPoint(x: rect.minX - 30, y: rect.minY + rect.height * 0.72)
        ]
        for (index, flow) in snapshot.interconnectors.prefix(4).enumerated() {
            let coast = project(CGPoint(x: index == 3 ? 0.30 : 0.78, y: 0.68 + CGFloat(index % 3) * 0.07), into: rect)
            links.append(
                MapLink(
                    id: 100 + index,
                    start: connectorAnchors[index],
                    end: coast,
                    curve: index == 3 ? -22 : 22,
                    fuel: .imports,
                    magnitude: abs(flow.megawatts),
                    reversed: flow.megawatts < 0
                )
            )
        }
        return links
    }

    private func drawLinks(_ links: [MapLink], context: inout GraphicsContext, date: Date, accent: Color) {
        for link in links {
            let color = GridTheme.fuel(link.fuel)
            let focused = selectedFuel == nil || selectedFuel == link.fuel
            let control = CGPoint(
                x: (link.start.x + link.end.x) / 2 + link.curve,
                y: (link.start.y + link.end.y) / 2 - abs(link.curve) * 0.45
            )
            var path = Path()
            path.move(to: link.start)
            path.addQuadCurve(to: link.end, control: control)
            context.stroke(path, with: .color(color.opacity(focused ? 0.23 : 0.045)), lineWidth: focused ? 0.9 : 0.5)

            guard focused else { continue }
            if reduceMotion {
                let startProgress = link.reversed ? 0.60 : 0.40
                let point = quadraticPoint(start: link.start, control: control, end: link.end, progress: startProgress)
                context.fill(Path(ellipseIn: CGRect(x: point.x - 1.5, y: point.y - 1.5, width: 3, height: 3)), with: .color(color.opacity(0.8)))
                continue
            }

            let count = min(max(Int(link.magnitude / 1_600), 2), 7)
            let speed = min(max(link.magnitude / 10_000, 0.08), 0.30)
            for particle in 0..<count {
                var progress = (date.timeIntervalSinceReferenceDate * speed + Double(particle) / Double(count) + Double(link.id % 13) / 13)
                    .truncatingRemainder(dividingBy: 1)
                if link.reversed { progress = 1 - progress }
                let point = quadraticPoint(start: link.start, control: control, end: link.end, progress: progress)
                let radius: CGFloat = particle == 0 ? 2.2 : 1.5
                var glow = context
                glow.addFilter(.blur(radius: 3))
                glow.fill(Path(ellipseIn: CGRect(x: point.x - 3, y: point.y - 3, width: 6, height: 6)), with: .color(color.opacity(0.30)))
                context.fill(Path(ellipseIn: CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)), with: .color(color.opacity(0.88)))
            }
        }
    }

    private func drawNodes(context: inout GraphicsContext, in rect: CGRect, date: Date) {
        for node in nodes {
            guard let reading = snapshot.reading(for: node.fuel) else { continue }
            let point = project(node.point, into: rect)
            let color = GridTheme.fuel(node.fuel)
            let focused = selectedFuel == nil || selectedFuel == node.fuel
            let scaled = min(max(reading.megawatts / 5_500, 0.65), 1.45)
            let pulse = reduceMotion ? 1 : 1 + sin(date.timeIntervalSinceReferenceDate * 1.1 + Double(node.id)) * 0.08
            let radius = CGFloat(scaled * pulse * 4.3)

            var glow = context
            glow.addFilter(.blur(radius: 7))
            glow.fill(Path(ellipseIn: CGRect(x: point.x - radius * 2, y: point.y - radius * 2, width: radius * 4, height: radius * 4)), with: .color(color.opacity(focused ? 0.33 : 0.05)))
            context.fill(Path(ellipseIn: CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)), with: .color(color.opacity(focused ? 0.95 : 0.18)))
            context.stroke(Path(ellipseIn: CGRect(x: point.x - radius - 3, y: point.y - radius - 3, width: (radius + 3) * 2, height: (radius + 3) * 2)), with: .color(color.opacity(focused ? 0.24 : 0.04)), lineWidth: 0.7)
        }
    }

    private func drawEventPulse(context: inout GraphicsContext, at point: CGPoint, date: Date, title: String) {
        let progress = reduceMotion ? 0.7 : (date.timeIntervalSinceReferenceDate * 0.35).truncatingRemainder(dividingBy: 1)
        let radius = CGFloat(8 + progress * 28)
        context.stroke(Path(ellipseIn: CGRect(x: point.x - radius, y: point.y - radius, width: radius * 2, height: radius * 2)), with: .color(GridTheme.warning.opacity(0.7 * (1 - progress))), lineWidth: 1.4)
        context.fill(Path(ellipseIn: CGRect(x: point.x - 3, y: point.y - 3, width: 6, height: 6)), with: .color(GridTheme.warning))
    }

    private func project(_ point: CGPoint, into rect: CGRect) -> CGPoint {
        CGPoint(x: rect.minX + point.x * rect.width, y: rect.minY + point.y * rect.height)
    }

    private func quadraticPoint(start: CGPoint, control: CGPoint, end: CGPoint, progress: Double) -> CGPoint {
        let t = CGFloat(progress)
        let oneMinus = 1 - t
        return CGPoint(
            x: oneMinus * oneMinus * start.x + 2 * oneMinus * t * control.x + t * t * end.x,
            y: oneMinus * oneMinus * start.y + 2 * oneMinus * t * control.y + t * t * end.y
        )
    }
}
