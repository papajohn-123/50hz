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

struct LiveMapAssetCluster: Identifiable, Equatable, Sendable {
    let id: String
    let assets: [LiveMapAsset]
    let latitude: Double
    let longitude: Double

    var count: Int { assets.count }
    var singleAsset: LiveMapAsset? { assets.count == 1 ? assets[0] : nil }
    var totalCapacityMW: Double? {
        let capacities = assets.compactMap(\.capacityMW)
        return capacities.isEmpty ? nil : capacities.reduce(0, +)
    }
    var dominantFuel: FuelKind? {
        var counts: [FuelKind: Int] = [:]
        for fuel in assets.compactMap(\.fuel) { counts[fuel, default: 0] += 1 }
        return counts.max { lhs, rhs in
            lhs.value == rhs.value
                ? lhs.key.displayName > rhs.key.displayName
                : lhs.value < rhs.value
        }?.key
    }
}

enum LiveAssetClustering {
    private struct Cell: Hashable {
        let zoomBand: Int
        let x: Int
        let y: Int
    }

    struct Index: Equatable, Sendable {
        fileprivate let clustersByBand: [Int: [LiveMapAssetCluster]]

        init(assets: [LiveMapAsset] = []) {
            guard !assets.isEmpty else {
                clustersByBand = [:]
                return
            }
            clustersByBand = Dictionary(uniqueKeysWithValues: [
                (1, LiveAssetClustering.makeClusters(assets: assets, band: 1)),
                (2, LiveAssetClustering.makeClusters(assets: assets, band: 2)),
                (3, LiveAssetClustering.makeClusters(assets: assets, band: 3)),
                (4, LiveAssetClustering.makeClusters(assets: assets, band: 4))
            ])
        }
    }

    struct ViewportSelection: Equatable, Sendable {
        let clusters: [LiveMapAssetCluster]
        let requestedBand: Int
        let renderedBand: Int

        var isCoarsened: Bool { renderedBand < requestedBand }
    }

    static func clusters(
        assets: [LiveMapAsset],
        zoomScale: CGFloat
    ) -> [LiveMapAssetCluster] {
        makeClusters(assets: assets, band: band(for: zoomScale))
    }

    static func visibleClusters(
        in index: Index,
        zoomScale: CGFloat,
        offset: CGSize,
        viewportSize: CGSize,
        maximumMarkerCount: Int = 180
    ) -> ViewportSelection {
        let requestedBand = band(for: zoomScale)
        let safeMaximum = max(maximumMarkerCount, 1)

        for candidateBand in stride(from: requestedBand, through: 1, by: -1) {
            let visible = (index.clustersByBand[candidateBand] ?? []).filter { cluster in
                let point = LiveAssetMapProjection.position(
                    latitude: cluster.latitude,
                    longitude: cluster.longitude,
                    viewportSize: viewportSize
                )
                return LiveAssetMapProjection.isVisible(
                    point,
                    viewportSize: viewportSize,
                    zoomScale: zoomScale,
                    offset: offset
                )
            }
            if visible.count <= safeMaximum || candidateBand == 1 {
                return ViewportSelection(
                    clusters: visible,
                    requestedBand: requestedBand,
                    renderedBand: candidateBand
                )
            }
        }

        return ViewportSelection(clusters: [], requestedBand: requestedBand, renderedBand: 1)
    }

    private static func band(for zoomScale: CGFloat) -> Int {
        switch zoomScale {
        case ..<1.5: 1
        case ..<2.75: 2
        case ..<4.75: 3
        default: 4
        }
    }

    private static func makeClusters(assets: [LiveMapAsset], band: Int) -> [LiveMapAssetCluster] {
        let cellSize: (longitude: Double, latitude: Double)
        switch band {
        case 1: cellSize = (1.4, 1.0)
        case 2: cellSize = (0.72, 0.52)
        case 3: cellSize = (0.36, 0.26)
        default: cellSize = (0.16, 0.12)
        }

        var groups: [Cell: [LiveMapAsset]] = [:]
        for asset in assets where asset.hasAuthoritativeCoordinate {
            let cell = Cell(
                zoomBand: band,
                x: Int(floor((asset.longitude + 9.5) / cellSize.longitude)),
                y: Int(floor((asset.latitude - 49.5) / cellSize.latitude))
            )
            groups[cell, default: []].append(asset)
        }

        return groups.map { cell, members in
            let ordered = members.sorted { $0.id < $1.id }
            return LiveMapAssetCluster(
                id: "\(cell.zoomBand):\(cell.x):\(cell.y)",
                assets: ordered,
                latitude: ordered.reduce(0) { $0 + $1.latitude } / Double(ordered.count),
                longitude: ordered.reduce(0) { $0 + $1.longitude } / Double(ordered.count)
            )
        }
        .sorted { $0.id < $1.id }
    }
}

enum LiveAssetMapProjection {
    static func mapRect(in viewportSize: CGSize) -> CGRect {
        CGRect(
            x: viewportSize.width * 0.14,
            y: 8,
            width: viewportSize.width * 0.72,
            height: max(viewportSize.height - 18, 0)
        )
    }

    static func position(
        latitude: Double,
        longitude: Double,
        viewportSize: CGSize
    ) -> CGPoint {
        let rect = mapRect(in: viewportSize)
        let longitudeRatio = (longitude + 9.5) / 12.5
        let latitudeRatio = (latitude - 49.5) / 11.5
        let normalized = CGPoint(
            x: 0.20 + CGFloat(longitudeRatio) * 0.68,
            y: 0.96 - CGFloat(latitudeRatio) * 0.93
        )
        return CGPoint(
            x: rect.minX + normalized.x * rect.width,
            y: rect.minY + normalized.y * rect.height
        )
    }

    static func screenPosition(
        _ point: CGPoint,
        viewportSize: CGSize,
        zoomScale: CGFloat,
        offset: CGSize
    ) -> CGPoint {
        CGPoint(
            x: ((point.x - viewportSize.width / 2) * zoomScale) + viewportSize.width / 2 + offset.width,
            y: ((point.y - viewportSize.height / 2) * zoomScale) + viewportSize.height / 2 + offset.height
        )
    }

    static func isVisible(
        _ point: CGPoint,
        viewportSize: CGSize,
        zoomScale: CGFloat,
        offset: CGSize,
        padding: CGFloat = 36
    ) -> Bool {
        let screenPoint = screenPosition(
            point,
            viewportSize: viewportSize,
            zoomScale: zoomScale,
            offset: offset
        )
        return CGRect(origin: .zero, size: viewportSize)
            .insetBy(dx: -padding, dy: -padding)
            .contains(screenPoint)
    }

    static func centeredOffset(
        for point: CGPoint,
        viewportSize: CGSize,
        zoomScale: CGFloat
    ) -> CGSize {
        CGSize(
            width: -(point.x - viewportSize.width / 2) * zoomScale,
            height: -(point.y - viewportSize.height / 2) * zoomScale
        )
    }
}

private struct LiveAssetOverlay: View {
    let assets: [LiveMapAsset]
    let zoomScale: CGFloat
    let offset: CGSize
    let showLabels: Bool
    let onAssetTap: ((LiveMapAsset) -> Void)?
    let onClusterTap: (LiveMapAssetCluster, CGPoint, CGSize) -> Void

    @State private var clusterIndex = LiveAssetClustering.Index()

    var body: some View {
        GeometryReader { proxy in
            let selection = LiveAssetClustering.visibleClusters(
                in: clusterIndex,
                zoomScale: zoomScale,
                offset: offset,
                viewportSize: proxy.size
            )

            ForEach(selection.clusters) { cluster in
                let point = LiveAssetMapProjection.position(
                    latitude: cluster.latitude,
                    longitude: cluster.longitude,
                    viewportSize: proxy.size
                )
                Button {
                    if let asset = cluster.singleAsset {
                        onAssetTap?(asset)
                    } else {
                        onClusterTap(cluster, point, proxy.size)
                    }
                } label: {
                    marker(for: cluster)
                    .frame(width: 44, height: 44)
                    .overlay(alignment: .leading) {
                        if showLabels, let asset = cluster.singleAsset {
                            Text(asset.name)
                                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                                .lineLimit(1)
                                .foregroundStyle(GridTheme.textPrimary)
                                .padding(.horizontal, 5)
                                .padding(.vertical, 3)
                                .background(GridTheme.surface.opacity(0.92), in: Capsule())
                                .offset(x: 30)
                        }
                    }
                }
                .buttonStyle(.plain)
                .disabled(onAssetTap == nil && cluster.singleAsset != nil)
                .scaleEffect(1 / max(zoomScale, 1))
                .position(point)
                .accessibilityLabel(accessibilityLabel(cluster))
                .accessibilityHint(
                    cluster.singleAsset == nil
                        ? (zoomScale < 7.75
                           ? "Recenters and zooms towards this group of source-located sites"
                           : "Opens a list of the source-located sites in this group")
                        : "Opens the source-backed generator inspector"
                )
            }
        }
        .task(id: assetCacheKey) {
            guard !assets.isEmpty else {
                clusterIndex = LiveAssetClustering.Index()
                return
            }
            let sourceAssets = assets
            let rebuilt = await Task.detached(priority: .userInitiated) {
                LiveAssetClustering.Index(assets: sourceAssets)
            }.value
            guard !Task.isCancelled else { return }
            clusterIndex = rebuilt
        }
    }

    private var assetCacheKey: String {
        let first = assets.first
        let last = assets.last
        return "\(assets.count):\(first?.id ?? "-"):\(last?.id ?? "-"):\(first?.observedAt.timeIntervalSinceReferenceDate ?? 0)"
    }

    @ViewBuilder
    private func marker(for cluster: LiveMapAssetCluster) -> some View {
        let accent = cluster.dominantFuel.map(GridTheme.fuel) ?? GridTheme.liveCyan
        if cluster.count == 1 {
            ZStack {
                Circle()
                    .fill(accent)
                    .frame(width: 8, height: 8)
                    .shadow(color: accent.opacity(0.55), radius: 4)
                Circle()
                    .stroke(GridTheme.textPrimary.opacity(0.75), lineWidth: 1)
                    .frame(width: 13, height: 13)
            }
        } else {
            Text(cluster.count.formatted())
                .font(.system(size: 9, weight: .bold, design: .monospaced))
                .foregroundStyle(GridTheme.background)
                .lineLimit(1)
                .minimumScaleFactor(0.65)
                .frame(width: 27, height: 27)
                .background(accent, in: Circle())
                .overlay(Circle().stroke(GridTheme.textPrimary.opacity(0.75), lineWidth: 1))
                .shadow(color: accent.opacity(0.38), radius: 5)
        }
    }

    private func accessibilityLabel(_ cluster: LiveMapAssetCluster) -> String {
        guard let asset = cluster.singleAsset else {
            let capacity = cluster.totalCapacityMW.map {
                ", \($0.formatted(.number.precision(.fractionLength(0)))) megawatts reported capacity"
            } ?? ""
            return "\(cluster.count) source-located energy sites\(capacity)"
        }
        let fuel = asset.fuel.map { ", \($0.displayName)" } ?? ""
        let capacity = asset.capacityMW.map { ", \($0.formatted(.number.precision(.fractionLength(0)))) megawatts" } ?? ""
        return "\(asset.name)\(fuel)\(capacity), source located"
    }
}

private struct MapLink: Identifiable {
    let id: String
    let start: CGPoint
    let end: CGPoint
    let curve: CGFloat
    let magnitude: Double
    let reversed: Bool
}

struct BritainGridMap: View {
    let snapshot: GridSnapshot
    let selectedFuel: FuelKind?
    let isForecast: Bool
    let assets: [LiveMapAsset]
    let onAssetTap: ((LiveMapAsset) -> Void)?
    let onClusterInspect: ((LiveMapAssetCluster) -> Void)?

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var newDataGlow = 0.0
    @State private var committedScale: CGFloat = 1
    @State private var committedOffset: CGSize = .zero
    @State private var viewportSize = CGSize(width: 390, height: 340)
    @GestureState private var gestureScale: CGFloat = 1
    @GestureState private var gestureOffset: CGSize = .zero

    init(
        snapshot: GridSnapshot,
        selectedFuel: FuelKind?,
        isForecast: Bool,
        assets: [LiveMapAsset] = [],
        onAssetTap: ((LiveMapAsset) -> Void)? = nil,
        onClusterInspect: ((LiveMapAssetCluster) -> Void)? = nil
    ) {
        self.snapshot = snapshot
        self.selectedFuel = selectedFuel
        self.isForecast = isForecast
        self.assets = assets
        self.onAssetTap = onAssetTap
        self.onClusterInspect = onClusterInspect
    }

    var body: some View {
        ZStack {
            TimelineView(.animation(minimumInterval: reduceMotion ? 1 : 1 / 30, paused: reduceMotion)) { timeline in
                Canvas(rendersAsynchronously: true) { context, size in
                    drawScene(context: &context, size: size, date: timeline.date)
                }
            }

            if !authoritativeAssets.isEmpty {
                LiveAssetOverlay(
                    assets: authoritativeAssets,
                    zoomScale: effectiveScale,
                    offset: effectiveOffset,
                    showLabels: effectiveScale >= 5.5,
                    onAssetTap: onAssetTap,
                    onClusterTap: handleClusterTap
                )
            }
        }
        .scaleEffect(effectiveScale)
        .offset(effectiveOffset)
        .gesture(zoomGesture)
        .simultaneousGesture(panGesture)
        .clipped()
        .overlay(alignment: .topLeading) {
            VStack(alignment: .leading, spacing: 4) {
                Text(LiveTruthCopy.mapScope)
                    .font(.system(size: 8, weight: .semibold, design: .monospaced))
                    .tracking(0.8)
                Text(authoritativeAssets.isEmpty ? "No asset locations" : "\(authoritativeAssets.count) source-located assets")
                    .font(.system(size: 9, weight: .regular, design: .monospaced))
            }
            .foregroundStyle(GridTheme.textTertiary)
            .padding(.top, 10)
            .padding(.leading, 10)
        }
        .overlay(alignment: .topTrailing) {
            Text(interconnectorPositionLabel)
                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                .tracking(0.8)
                .foregroundStyle(GridTheme.textTertiary)
                .padding(.top, 10)
                .padding(.trailing, 10)
        }
        .overlay(alignment: .bottomLeading) {
            Text(effectiveScale > 1.01 ? "\(Double(effectiveScale).formatted(.number.precision(.fractionLength(1))))× · DRAG TO PAN" : "PINCH TO INSPECT")
                .font(.system(size: 8, weight: .semibold, design: .monospaced))
                .tracking(0.6)
                .foregroundStyle(GridTheme.textTertiary)
                .padding(.leading, 10)
                .padding(.bottom, 10)
                .accessibilityHidden(true)
        }
        .overlay(alignment: .bottomTrailing) {
            if effectiveScale > 1.01 {
                Button("Reset") {
                    withAnimation(reduceMotion ? nil : .snappy(duration: 0.24)) {
                        committedScale = 1
                        committedOffset = .zero
                    }
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
                .frame(minWidth: 44, minHeight: 44)
                .padding(.trailing, 4)
                .padding(.bottom, 1)
                .accessibilityLabel("Reset map zoom")
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
        .background {
            GeometryReader { proxy in
                Color.clear
                    .onAppear { viewportSize = proxy.size }
                    .onChange(of: proxy.size) { _, newSize in viewportSize = newSize }
            }
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: authoritativeAssets.isEmpty ? .combine : .contain)
        .accessibilityLabel("Schematic national view of Great Britain's electricity system")
        .accessibilityValue(accessibilityValue)
        .accessibilityHint(mapDisclosure)
        .onChange(of: snapshot.timestamp) { _, _ in
            guard !reduceMotion else {
                newDataGlow = 0
                return
            }
            newDataGlow = 1
            withAnimation(.easeOut(duration: 1.1)) {
                newDataGlow = 0
            }
        }
    }

    private var authoritativeAssets: [LiveMapAsset] {
        assets.filter(\.hasAuthoritativeCoordinate)
    }

    private var effectiveScale: CGFloat {
        min(max(committedScale * gestureScale, 1), 8)
    }

    private var effectiveOffset: CGSize {
        CGSize(
            width: committedOffset.width + gestureOffset.width,
            height: committedOffset.height + gestureOffset.height
        )
    }

    private var mapDisclosure: String {
        if authoritativeAssets.isEmpty { return LiveTruthCopy.mapDisclosure }
        return "Interconnector paths remain schematic. Asset dots use publisher-backed coordinates, approximately projected onto this abstract Britain outline."
    }

    private var zoomGesture: some Gesture {
        MagnificationGesture()
            .updating($gestureScale) { value, state, _ in
                state = value
            }
            .onEnded { value in
                let nextScale = min(max(committedScale * value, 1), 8)
                committedScale = nextScale
                if nextScale <= 1.01 {
                    committedScale = 1
                    committedOffset = .zero
                } else {
                    committedOffset = bounded(committedOffset, for: nextScale, viewportSize: viewportSize)
                }
            }
    }

    private var panGesture: some Gesture {
        DragGesture(minimumDistance: 8)
            .updating($gestureOffset) { value, state, _ in
                guard effectiveScale > 1.01 else { return }
                state = value.translation
            }
            .onEnded { value in
                guard committedScale > 1.01 else { return }
                committedOffset = bounded(
                    CGSize(
                        width: committedOffset.width + value.translation.width,
                        height: committedOffset.height + value.translation.height
                    ),
                    for: committedScale,
                    viewportSize: viewportSize
                )
            }
    }

    private func bounded(_ offset: CGSize, for scale: CGFloat, viewportSize: CGSize) -> CGSize {
        let horizontalLimit = max(viewportSize.width * (scale - 1) / 2, 0)
        let verticalLimit = max(viewportSize.height * (scale - 1) / 2, 0)
        return CGSize(
            width: min(max(offset.width, -horizontalLimit), horizontalLimit),
            height: min(max(offset.height, -verticalLimit), verticalLimit)
        )
    }

    private func handleClusterTap(
        _ cluster: LiveMapAssetCluster,
        point: CGPoint,
        viewportSize: CGSize
    ) {
        if effectiveScale < 7.75 {
            let targetScale = min(max(effectiveScale * 1.8, 2), 8)
            let centeredOffset = LiveAssetMapProjection.centeredOffset(
                for: point,
                viewportSize: viewportSize,
                zoomScale: targetScale
            )
            withAnimation(reduceMotion ? nil : .snappy(duration: 0.28)) {
                committedScale = targetScale
                committedOffset = bounded(
                    centeredOffset,
                    for: targetScale,
                    viewportSize: viewportSize
                )
            }
        } else {
            onClusterInspect?(cluster)
        }
    }

    private var interconnectorPositionLabel: String {
        guard !snapshot.interconnectors.isEmpty else { return "FLOW UNAVAILABLE" }
        let net = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts }
        return net >= 0 ? "NET IMPORT" : "NET EXPORT"
    }

    private var accessibilityValue: String {
        let leading = snapshot.generation.sorted { $0.megawatts > $1.megawatts }.prefix(3)
            .map { "\($0.fuel.displayName) \(($0.megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))) gigawatts" }
            .joined(separator: ", ")
        let generationSummary = leading.isEmpty
            ? "Transmission-visible supply is unavailable for this frame."
            : "Leading transmission-visible supply sources: \(leading)."
        guard !snapshot.interconnectors.isEmpty else {
            return "\(generationSummary) Interconnector position unavailable for this frame."
        }
        let position = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts } >= 0 ? "net importing" : "net exporting"
        return "\(generationSummary) Britain is \(position)."
    }

    private func drawScene(context: inout GraphicsContext, size: CGSize, date: Date) {
        let mapRect = CGRect(x: size.width * 0.14, y: 8, width: size.width * 0.72, height: size.height - 18)
        let shape = BritainShape().path(in: mapRect)
        let accent = mapAccent
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

        if newDataGlow > 0 {
            var arrivalGlow = context
            arrivalGlow.addFilter(.blur(radius: 12))
            arrivalGlow.stroke(shape, with: .color(accent.opacity(0.42 * newDataGlow)), lineWidth: 5)
        }

        drawTopography(context: &context, in: mapRect, clippedTo: shape, accent: accent)
        drawNationalSignal(context: &context, in: mapRect, clippedTo: shape, accent: accent, date: date)
        let links = mapLinks(in: mapRect)
        drawLinks(links, context: &context, date: date, accent: accent)
    }

    private var mapAccent: Color {
        if isForecast { return GridTheme.forecastViolet }
        if let selectedFuel { return GridTheme.fuel(selectedFuel) }
        return GridTheme.liveCyan
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

    private func drawNationalSignal(
        context: inout GraphicsContext,
        in rect: CGRect,
        clippedTo shape: Path,
        accent: Color,
        date: Date
    ) {
        var clipped = context
        clipped.clip(to: shape)

        // These lines are deliberately regular and coast-to-coast: they depict a
        // national signal plane, not substations, generators or transmission routes.
        for index in 0..<6 {
            let y = rect.minY + rect.height * (0.18 + CGFloat(index) * 0.13)
            var line = Path()
            line.move(to: CGPoint(x: rect.minX, y: y))
            line.addCurve(
                to: CGPoint(x: rect.maxX, y: y + CGFloat(index.isMultiple(of: 2) ? 8 : -8)),
                control1: CGPoint(x: rect.minX + rect.width * 0.34, y: y - 16),
                control2: CGPoint(x: rect.minX + rect.width * 0.66, y: y + 16)
            )
            clipped.stroke(line, with: .color(accent.opacity(0.085)), lineWidth: 0.7)
        }

        let centre = CGPoint(x: rect.midX + rect.width * 0.04, y: rect.midY + rect.height * 0.08)
        let frequencyDeviation = min(abs((snapshot.frequency?.value ?? 50) - 50), 0.25)
        let pulse = reduceMotion ? 0.5 : (sin(date.timeIntervalSinceReferenceDate * 1.2) + 1) / 2
        let radius = CGFloat(15 + frequencyDeviation * 40 + pulse * 2)
        clipped.stroke(
            Path(ellipseIn: CGRect(x: centre.x - radius, y: centre.y - radius, width: radius * 2, height: radius * 2)),
            with: .color(accent.opacity(0.10)),
            lineWidth: 0.8
        )
    }

    private func mapLinks(in rect: CGRect) -> [MapLink] {
        snapshot.interconnectors.prefix(8).enumerated().map { index, flow in
            let isWestern = ["IE", "NI"].contains(flow.countryCode.uppercased())
            let lane = CGFloat(index % 4)
            let anchor = CGPoint(
                x: isWestern ? rect.minX - 34 : rect.maxX + 34,
                y: rect.minY + rect.height * (0.25 + lane * 0.17)
            )
            let coast = project(
                CGPoint(x: isWestern ? 0.29 : 0.77, y: 0.38 + lane * 0.13),
                into: rect
            )
            return MapLink(
                id: flow.id,
                start: anchor,
                end: coast,
                curve: isWestern ? -22 : 22,
                magnitude: abs(flow.megawatts),
                reversed: flow.megawatts < 0
            )
        }
    }

    private func drawLinks(_ links: [MapLink], context: inout GraphicsContext, date: Date, accent: Color) {
        for link in links {
            let color = GridTheme.fuel(.imports)
            let focused = selectedFuel == nil || selectedFuel == .imports
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
                let stableOffset = Double(abs(link.id.hashValue % 13)) / 13
                var progress = (date.timeIntervalSinceReferenceDate * speed + Double(particle) / Double(count) + stableOffset)
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
