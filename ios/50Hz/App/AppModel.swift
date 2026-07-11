import Foundation
import Combine

@MainActor
final class AppModel: ObservableObject {
    private let repository: any GridRepository
    private var periodicRefreshTask: Task<Void, Never>?
    private var hasBootstrapped = false

    @Published var loadPhase: LoadPhase = .loading
    @Published var snapshot: GridSnapshot?
    @Published var timeline: GridTimeline?
    @Published var selectedFuel: FuelKind?
    @Published var selectedTime: Date?
    @Published var selectedTab: AppTab = .live
    @Published var isAskPresented = false
    @Published var selectedEvent: GridEvent?
    @Published var isRefreshing = false
    @Published var lastRefreshError: String?
    @Published var lastSuccessfulRefreshAt: Date?

    init(repository: any GridRepository = FixtureGridRepository()) {
        self.repository = repository
    }

    func bootstrap() async {
        guard !hasBootstrapped else { return }
        hasBootstrapped = true

        async let cachedSnapshot = repository.cachedSnapshot()
        async let cachedTimeline = repository.cachedTimeline()
        let (snapshot, timeline) = await (cachedSnapshot, cachedTimeline)

        if var snapshot {
            snapshot.freshness = .stale
            snapshot.freshnessAgeSeconds = max(Int(Date().timeIntervalSince(snapshot.timestamp)), 0)
            self.snapshot = snapshot
            self.timeline = timeline
            self.loadPhase = .loaded
        }

        await refresh()
    }

    /// Kept for fixture previews and small focused tests.
    func load() async {
        await bootstrap()
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }

        async let snapshotRequest = repository.currentSnapshot()
        async let timelineRequest = repository.timeline()

        var refreshedSnapshot: GridSnapshot?
        var refreshedTimeline: GridTimeline?
        var snapshotError: Error?
        var timelineError: Error?

        do { refreshedSnapshot = try await snapshotRequest }
        catch { snapshotError = error }

        do { refreshedTimeline = try await timelineRequest }
        catch { timelineError = error }

        guard !Task.isCancelled else { return }

        if var refreshedSnapshot {
            refreshedSnapshot.freshnessAgeSeconds = max(
                refreshedSnapshot.freshnessAgeSeconds,
                max(Int(Date().timeIntervalSince(refreshedSnapshot.timestamp)), 0)
            )
            snapshot = refreshedSnapshot
            loadPhase = .loaded
            lastSuccessfulRefreshAt = Date()
        } else if var held = snapshot {
            held.freshness = .offline
            held.freshnessAgeSeconds = max(Int(Date().timeIntervalSince(held.timestamp)), held.freshnessAgeSeconds)
            snapshot = held
            loadPhase = .loaded
        }

        if let refreshedTimeline { timeline = refreshedTimeline }

        let errors = [snapshotError, timelineError].compactMap { error -> String? in
            guard let error else { return nil }
            if let apiError = error as? GridAPIError, case .cancelled = apiError { return nil }
            return error.localizedDescription
        }
        lastRefreshError = errors.isEmpty ? nil : errors.uniqued().joined(separator: " ")

        if snapshot == nil {
            loadPhase = .failed(lastRefreshError ?? "No confirmed grid snapshot is available.")
        }
    }

    func retry() async {
        if snapshot == nil { loadPhase = .loading }
        await refresh()
    }

    func startForegroundRefresh() {
        guard periodicRefreshTask == nil else { return }
        periodicRefreshTask = Task { [weak self] in
            while !Task.isCancelled {
                do {
                    try await Task.sleep(for: .seconds(60))
                } catch {
                    return
                }
                guard !Task.isCancelled else { return }
                await self?.refresh()
            }
        }
    }

    func stopForegroundRefresh() {
        periodicRefreshTask?.cancel()
        periodicRefreshTask = nil
    }

    func select(time: Date?) {
        selectedTime = time
    }

    func resumeLive() {
        selectedTime = nil
    }

    var selectedSample: GridTimelineSample? {
        guard let selectedTime, let timeline else { return nil }
        return GridTimelineSampler(timeline: timeline).sample(at: selectedTime)
    }

    var presentedSnapshot: GridSnapshot? {
        guard var snapshot else { return nil }
        guard let sample = selectedSample else { return snapshot }
        snapshot.timestamp = sample.timestamp
        snapshot.demand = GridMetric(
            value: sample.demandMW,
            unit: "MW",
            factClass: sample.factClass,
            sourceID: snapshot.demand.sourceID
        )
        snapshot.carbonIntensity = GridMetric(
            value: sample.carbonIntensity,
            unit: "gCO₂/kWh",
            factClass: sample.factClass,
            sourceID: snapshot.carbonIntensity.sourceID
        )
        snapshot.frequency = sample.frequencyHz.map {
            GridMetric(value: $0, unit: "Hz", factClass: sample.factClass, sourceID: snapshot.frequency?.sourceID ?? "elexon-freq")
        }
        snapshot.generation = sample.generation
        if sample.factClass == .forecast {
            snapshot.headline = ConditionHeadline(
                cleanliness: sample.carbonIntensity < 130 ? "Very clean" : "Cleaner",
                balance: "Forecast",
                energyPosition: "Wind-led",
                interpretation: "The forecast points to a cleaner period as wind output rises. Forecast values are shown in violet."
            )
        }
        return snapshot
    }

    var timelineModeLabel: String {
        guard let selectedTime else { return "LIVE" }
        guard let timeline else { return "REPLAY" }
        return selectedTime > timeline.nowBoundary ? "FORECAST" : "REPLAY"
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
