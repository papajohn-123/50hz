import Foundation

enum LiveTruthCopy {
    static let supplyTitle = "Transmission-visible supply"
    static let mapScope = "SCHEMATIC NATIONAL VIEW"
    static let mapDisclosure = "Flows show national direction and magnitude. They do not locate power stations, cables or reported events."
}

/// Live-only bridge from the source-backed asset endpoint into the schematic
/// map. Nothing is rendered unless the backend supplies both provenance and a
/// plausible Great Britain coordinate.
struct LiveMapAsset: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let fuel: FuelKind?
    let latitude: Double
    let longitude: Double
    let capacityMW: Double?
    let sourceID: String
    let observedAt: Date
    let operatorName: String?
    let technology: String?
    let lifecycle: GridAssetLifecycle
    let region: String?
    let country: String?
    let coordinatePrecision: String?
    let coordinateSource: GridAssetSource?
    let linkedBMUnitCount: Int
    let operatingEvidence: GridAssetOperatingEvidence?

    init(
        id: String,
        name: String,
        fuel: FuelKind?,
        latitude: Double,
        longitude: Double,
        capacityMW: Double?,
        sourceID: String,
        observedAt: Date,
        operatorName: String? = nil,
        technology: String? = nil,
        lifecycle: GridAssetLifecycle = .unknown,
        region: String? = nil,
        country: String? = nil,
        coordinatePrecision: String? = nil,
        coordinateSource: GridAssetSource? = nil,
        linkedBMUnitCount: Int = 0,
        operatingEvidence: GridAssetOperatingEvidence? = nil
    ) {
        self.id = id
        self.name = name
        self.fuel = fuel
        self.latitude = latitude
        self.longitude = longitude
        self.capacityMW = capacityMW
        self.sourceID = sourceID
        self.observedAt = observedAt
        self.operatorName = operatorName
        self.technology = technology
        self.lifecycle = lifecycle
        self.region = region
        self.country = country
        self.coordinatePrecision = coordinatePrecision
        self.coordinateSource = coordinateSource
        self.linkedBMUnitCount = linkedBMUnitCount
        self.operatingEvidence = operatingEvidence
    }

    init(item: GridAssetMapItem) {
        self.init(
            id: item.id,
            name: item.name,
            fuel: item.fuel,
            latitude: item.coordinate.latitude,
            longitude: item.coordinate.longitude,
            capacityMW: item.capacityMW,
            sourceID: item.coordinate.source.sourceID,
            observedAt: item.coordinate.source.retrievedAt,
            operatorName: item.operatorName,
            technology: item.technology,
            lifecycle: item.lifecycle,
            region: item.region,
            country: item.country,
            coordinatePrecision: item.coordinate.precision,
            coordinateSource: item.coordinate.source,
            linkedBMUnitCount: item.linkedBMUnitCount,
            operatingEvidence: item.operatingEvidence
        )
    }

    var hasAuthoritativeCoordinate: Bool {
        !id.isEmpty
            && !name.isEmpty
            && !sourceID.isEmpty
            && latitude.isFinite
            && longitude.isFinite
            && (49.5...61.0).contains(latitude)
            && (-9.5...3.0).contains(longitude)
    }

    var searchableText: String {
        [name, operatorName, technology, region, country, fuel?.displayName]
            .compactMap { $0 }
            .joined(separator: " ")
            .folding(options: [.diacriticInsensitive, .caseInsensitive], locale: .current)
    }
}

enum LiveDatumState: String, Equatable, Sendable {
    case current
    case delayed
    case stale
    case unavailable
    case unknown

    var displayName: String {
        switch self {
        case .current: "Current"
        case .delayed: "Delayed"
        case .stale: "Stale"
        case .unavailable: "Unavailable"
        case .unknown: "Timing unavailable"
        }
    }
}

struct LiveFreshnessPresentation: Equatable, Sendable {
    let currentCount: Int
    let delayedCount: Int
    let staleCount: Int
    let unavailableCount: Int
    let isLegacy: Bool

    var hasConcern: Bool {
        delayedCount + staleCount + unavailableCount > 0
    }

    var compactLabel: String {
        guard !isLegacy else { return "Snapshot timing" }

        var parts: [String] = []
        if currentCount > 0 { parts.append("\(currentCount) current") }
        if delayedCount > 0 { parts.append("\(delayedCount) delayed") }
        if staleCount > 0 { parts.append("\(staleCount) stale") }
        if unavailableCount > 0 { parts.append("\(unavailableCount) unavailable") }
        return parts.isEmpty ? "No timed inputs" : parts.joined(separator: " · ")
    }

    static func make(snapshot: GridSnapshot, at date: Date) -> LiveFreshnessPresentation {
        guard let statuses = snapshot.dataStatus, !statuses.isEmpty else {
            return LiveFreshnessPresentation(
                currentCount: snapshot.freshness == .live || snapshot.freshness == .critical ? 1 : 0,
                delayedCount: snapshot.freshness == .stale ? 1 : 0,
                staleCount: 0,
                unavailableCount: snapshot.freshness == .offline ? 1 : 0,
                isLegacy: true
            )
        }

        let states = statuses.map { datumState(for: $0.resolved(at: date)) }
        return LiveFreshnessPresentation(
            currentCount: states.filter { $0 == .current }.count,
            delayedCount: states.filter { $0 == .delayed }.count,
            staleCount: states.filter { $0 == .stale }.count,
            unavailableCount: states.filter { $0 == .unavailable }.count,
            isLegacy: false
        )
    }

    static func datumState(for status: ResolvedDataFamilyStatus) -> LiveDatumState {
        if status.deliveryState == .unavailable || status.factState == .unavailable {
            return .unavailable
        }
        if status.deliveryState == .stale || status.factState == .stale {
            return .stale
        }
        if status.deliveryState == .delayed || status.factState == .delayed {
            return .delayed
        }
        if status.deliveryState == .healthy && status.factState == .live {
            return .current
        }
        return .unknown
    }
}

struct LiveMetricPresentation: Identifiable, Equatable, Sendable {
    let id: GridDataFamily
    let label: String
    let value: String
    let unit: String
    let factClass: FactClass?
    let sourceName: String
    let dataset: String?
    let observedAt: Date?
    let state: LiveDatumState
    let timePrefix: String

    var factLabel: String {
        factClass?.rawValue.uppercased() ?? "UNAVAILABLE"
    }

    var sourceLabel: String {
        guard let dataset, !dataset.isEmpty else { return sourceName }
        return "\(sourceName) · \(dataset)"
    }

    func timingLabel() -> String {
        guard let observedAt else { return state.displayName }
        let time = observedAt.formatted(.dateTime.hour().minute())
        return "\(timePrefix) \(time) · \(state.displayName)"
    }

    static func make(snapshot: GridSnapshot, mode: String, at date: Date) -> [LiveMetricPresentation] {
        [
            metric(
                family: .frequency,
                label: "Frequency",
                metric: snapshot.frequency,
                formattedValue: snapshot.frequency?.formatted(decimals: 2) ?? "—",
                fallbackUnit: mode == "FORECAST" ? "not forecast" : "unavailable",
                snapshot: snapshot,
                mode: mode,
                at: date
            ),
            metric(
                family: .demand,
                label: "Demand",
                metric: snapshot.demand,
                formattedValue: (snapshot.demand.value / 1_000).formatted(.number.precision(.fractionLength(1))),
                fallbackUnit: "GW",
                forcedUnit: "GW",
                snapshot: snapshot,
                mode: mode,
                at: date
            ),
            metric(
                family: .carbon,
                label: "Carbon",
                metric: snapshot.carbonIntensity,
                formattedValue: snapshot.carbonIntensity.formatted(),
                fallbackUnit: "g/kWh",
                forcedUnit: "g/kWh",
                snapshot: snapshot,
                mode: mode,
                at: date
            )
        ]
    }

    private static func metric(
        family: GridDataFamily,
        label: String,
        metric: GridMetric?,
        formattedValue: String,
        fallbackUnit: String,
        forcedUnit: String? = nil,
        snapshot: GridSnapshot,
        mode: String,
        at date: Date
    ) -> LiveMetricPresentation {
        let status = snapshot.dataStatus?.first { $0.family == family }
        let directSource = metric.flatMap { item in
            snapshot.sources.first { $0.id == item.sourceID }
        }
        let familySource = status?.sourceIDs.lazy.compactMap { sourceID in
            snapshot.sources.first { $0.id == sourceID }
        }.first
        let source = directSource ?? familySource

        let state: LiveDatumState
        let observedAt: Date?
        let timePrefix: String
        if mode == "FORECAST" {
            state = metric == nil ? .unavailable : .current
            observedAt = metric == nil ? nil : snapshot.timestamp
            timePrefix = "Valid"
        } else if mode == "REPLAY" {
            state = metric == nil ? .unavailable : .current
            observedAt = metric == nil ? nil : snapshot.timestamp
            timePrefix = "Frame"
        } else if let status {
            state = LiveFreshnessPresentation.datumState(for: status.resolved(at: date))
            observedAt = status.observedAt ?? source?.observedAt
            timePrefix = metric?.factClass == .forecast ? "Valid" : "Observed"
        } else if let source {
            let age = max(0, date.timeIntervalSince(source.observedAt))
            let liveLimit = Double(max(source.cadenceSeconds * 2, 60))
            let staleLimit = Double(max(source.cadenceSeconds * 4, 300))
            state = age <= liveLimit ? .current : (age < staleLimit ? .delayed : .stale)
            observedAt = source.observedAt
            timePrefix = metric?.factClass == .forecast ? "Valid" : "Observed"
        } else {
            state = metric == nil ? .unavailable : .unknown
            observedAt = metric == nil ? nil : snapshot.timestamp
            timePrefix = metric?.factClass == .forecast ? "Valid" : "Observed"
        }

        return LiveMetricPresentation(
            id: family,
            label: label,
            value: formattedValue,
            unit: forcedUnit ?? metric?.unit ?? fallbackUnit,
            factClass: metric?.factClass,
            sourceName: mode == "LIVE" ? (source?.name ?? "Source unavailable") : "50Hz timeline",
            dataset: mode == "LIVE" ? source?.dataset : nil,
            observedAt: observedAt,
            state: state,
            timePrefix: timePrefix
        )
    }
}

struct LiveInterconnectorPresentation: Identifiable, Equatable, Sendable {
    let id: String
    let name: String
    let countryCode: String
    let magnitude: String
    let direction: String
    let flowDescription: String
    let factLabel: String

    static func make(flow: InterconnectorFlow) -> LiveInterconnectorPresentation {
        let absoluteMW = abs(flow.megawatts).formatted(.number.precision(.fractionLength(0)))
        let importing = flow.megawatts >= 0
        return LiveInterconnectorPresentation(
            id: flow.id,
            name: flow.name,
            countryCode: flow.countryCode,
            magnitude: "\(absoluteMW) MW",
            direction: importing ? "IMPORT" : "EXPORT",
            flowDescription: importing ? "\(flow.countryCode) → GB" : "GB → \(flow.countryCode)",
            factLabel: flow.factClass.rawValue.uppercased()
        )
    }
}

struct LiveFamilyEvidencePresentation: Equatable, Sendable {
    let sourceLabel: String
    let observedAt: Date?
    let state: LiveDatumState

    static func make(family: GridDataFamily, snapshot: GridSnapshot, at date: Date) -> LiveFamilyEvidencePresentation {
        let status = snapshot.dataStatus?.first { $0.family == family }
        let source = status?.sourceIDs.lazy.compactMap { sourceID in
            snapshot.sources.first { $0.id == sourceID }
        }.first
        let fallbackSource = family == .interconnectors
            ? snapshot.sources.first { $0.dataset.localizedCaseInsensitiveContains("B1610") || $0.id.localizedCaseInsensitiveContains("fuelinst") }
            : nil
        let resolvedSource = source ?? fallbackSource
        let state = status.map { LiveFreshnessPresentation.datumState(for: $0.resolved(at: date)) } ?? .unknown
        let sourceLabel = [resolvedSource?.name, resolvedSource?.dataset]
            .compactMap { $0 }
            .filter { !$0.isEmpty }
            .joined(separator: " · ")

        return LiveFamilyEvidencePresentation(
            sourceLabel: sourceLabel.isEmpty ? "Source timing unavailable" : sourceLabel,
            observedAt: status?.observedAt ?? resolvedSource?.observedAt,
            state: state
        )
    }
}
