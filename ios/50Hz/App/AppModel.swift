import Foundation
import Combine

@MainActor
final class AppModel: ObservableObject {
    private static let maximumForecastAge: TimeInterval = 3_600
    private let repository: any GridRepository
    private var periodicRefreshTask: Task<Void, Never>?
    private var hasBootstrapped = false
    private var isRefreshingEvents = false
    private var isRefreshingDailyGame = false

    @Published var loadPhase: LoadPhase = .loading
    @Published var snapshot: GridSnapshot?
    @Published var timeline: GridTimeline?
    @Published var regionalContext: RegionalGridContext?
    @Published var events: [GridEvent] = []
    @Published var dailyGame: DailyGame?
    @Published var selectedFuel: FuelKind?
    @Published var selectedTime: Date?
    @Published var selectedTab: AppTab = .live
    @Published var isAskPresented = false
    @Published var selectedEvent: GridEvent?
    @Published var isRefreshing = false
    @Published var lastRefreshError: String?
    @Published var timelineRefreshError: String?
    @Published var lastSuccessfulRefreshAt: Date?
    @Published var regionLoadPhase: LoadPhase = .loading
    @Published var regionError: String?
    @Published var eventsError: String?
    @Published var gameLoadPhase: LoadPhase = .loading
    @Published var gameRefreshError: String?

    init(repository: any GridRepository = FixtureGridRepository()) {
        self.repository = repository
    }

    func bootstrap() async {
        guard !hasBootstrapped else { return }
        hasBootstrapped = true

        async let cachedSnapshot = repository.cachedSnapshot()
        async let cachedTimeline = repository.cachedTimeline()
        async let cachedEvents = repository.cachedEvents()
        async let cachedDailyGame = repository.cachedDailyGame()
        let (snapshot, timeline, events, dailyGame) = await (
            cachedSnapshot,
            cachedTimeline,
            cachedEvents,
            cachedDailyGame
        )

        if var snapshot {
            snapshot.freshness = .stale
            snapshot.freshnessAgeSeconds = max(
                snapshot.freshnessAgeSeconds,
                max(Int(Date().timeIntervalSince(snapshot.timestamp)), 0)
            )
            self.snapshot = snapshot
            self.timeline = timeline
            self.loadPhase = .loaded
        }
        if let events { self.events = events }
        if let dailyGame {
            self.dailyGame = dailyGame
            self.gameLoadPhase = .loaded
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

        if let refreshedTimeline {
            timeline = refreshedTimeline
            timelineRefreshError = nil
        } else if let timelineError,
                  !Self.isCancellation(timelineError) {
            timelineRefreshError = timelineError.localizedDescription
        }

        let errors = [snapshotError, timelineError].compactMap { error -> String? in
            guard let error else { return nil }
            if let apiError = error as? GridAPIError, case .cancelled = apiError { return nil }
            return error.localizedDescription
        }
        lastRefreshError = errors.isEmpty ? nil : errors.uniqued().joined(separator: " ")

        if snapshot == nil {
            loadPhase = .failed(lastRefreshError ?? "No confirmed grid snapshot is available.")
        }

        if !isForecastTimelineUsable,
           let selectedTime,
           let timeline,
           selectedTime > timeline.nowBoundary {
            self.selectedTime = nil
        }

        Task { [weak self] in await self?.refreshEvents() }
        Task { [weak self] in await self?.refreshDailyGame() }
    }

    func retry() async {
        if snapshot == nil { loadPhase = .loading }
        await refresh()
    }

    func loadRegion(postcode: String) async {
        let normalized = postcode.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        let requested = normalized.isEmpty ? "SW1A 1AA" : normalized
        regionError = nil

        if regionalContext?.postcode.caseInsensitiveCompare(requested) != .orderedSame {
            if let cached = await repository.cachedRegion(postcode: requested) {
                regionalContext = cached
                regionLoadPhase = .loaded
            } else {
                regionalContext = nil
                regionLoadPhase = .loading
            }
        }

        do {
            regionalContext = try await repository.region(postcode: requested)
            regionLoadPhase = .loaded
        } catch {
            guard !Task.isCancelled else { return }
            regionError = error.localizedDescription
            regionLoadPhase = regionalContext == nil ? .failed(error.localizedDescription) : .loaded
        }
    }

    func refreshEvents() async {
        guard !isRefreshingEvents else { return }
        isRefreshingEvents = true
        defer { isRefreshingEvents = false }
        do {
            events = try await repository.events()
            eventsError = nil
        } catch {
            guard !Task.isCancelled else { return }
            eventsError = error.localizedDescription
        }
    }

    func refreshDailyGame() async {
        guard !isRefreshingDailyGame else { return }
        isRefreshingDailyGame = true
        defer { isRefreshingDailyGame = false }
        do {
            dailyGame = try await repository.dailyGame()
            gameLoadPhase = .loaded
            gameRefreshError = nil
        } catch {
            guard !Task.isCancelled else { return }
            gameRefreshError = error.localizedDescription
            gameLoadPhase = dailyGame == nil ? .failed(error.localizedDescription) : .loaded
        }
    }

    func askGrid(question: String, regionCode: String? = nil) async throws -> AskGridAnswer {
        try await repository.ask(
            AskGridRequest(
                question: question,
                mapTime: selectedTime ?? snapshot?.timestamp,
                regionCode: regionCode
            )
        )
    }

    func eventDetails(id: String) async throws -> GridEvent {
        try await repository.event(id: id)
    }

    func explainEvent(id: String) async throws -> EventExplanationResponse {
        try await repository.eventExplanation(id: id)
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
        let sample = GridTimelineSampler(timeline: timeline).sample(at: selectedTime)
        guard sample?.factClass != .forecast || isForecastTimelineUsable else { return nil }
        return sample
    }

    /// Forecast frames are current only while both halves of the product state
    /// are trustworthy: a fresh/critical live snapshot and a timeline whose
    /// server "now" boundary is no more than one hour old.
    var isForecastTimelineUsable: Bool {
        guard let snapshot, let timeline else { return false }
        guard snapshot.freshness == .live || snapshot.freshness == .critical else { return false }
        guard snapshot.freshnessAgeSeconds <= Int(Self.maximumForecastAge) else { return false }
        let age = Date().timeIntervalSince(timeline.nowBoundary)
        guard age >= -300, age <= Self.maximumForecastAge else { return false }
        return timeline.samples.contains {
            $0.factClass == .forecast && $0.timestamp > Date()
        }
    }

    var displayTimeline: GridTimeline? {
        guard let timeline else { return nil }
        guard !isForecastTimelineUsable else { return timeline }
        let observed = timeline.samples.filter { $0.factClass != .forecast }
        guard !observed.isEmpty else { return nil }
        return GridTimeline(
            sourceResolutionSeconds: timeline.sourceResolutionSeconds,
            materialGapSeconds: timeline.materialGapSeconds,
            nowBoundary: timeline.nowBoundary,
            samples: observed
        )
    }

    var forecastUnavailableReason: String {
        guard let snapshot else {
            return "Today is waiting for a confirmed live snapshot before showing a forecast."
        }
        guard snapshot.freshness == .live || snapshot.freshness == .critical else {
            return "The live snapshot is delayed or offline, so cached forecast values are being withheld."
        }
        if snapshot.freshnessAgeSeconds > Int(Self.maximumForecastAge) {
            return "The live snapshot is more than one hour old, so cached forecast values are being withheld."
        }
        guard let timeline else {
            return "No confirmed forecast timeline is available."
        }
        let age = Date().timeIntervalSince(timeline.nowBoundary)
        if age > Self.maximumForecastAge {
            let minutes = max(Int(age / 60), 60)
            return "The last forecast timeline is \(minutes) minutes old, beyond 50Hz’s one-hour display limit."
        }
        if age < -300 {
            return "The forecast timeline clock could not be reconciled with this device, so it is being withheld."
        }
        return "No forward-looking forecast samples remain in the current timeline."
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
        // The timeline contract does not currently carry historical or forecast
        // interconnector/event state. Never leave the current values attached to
        // a different map time, where they would look like time-aligned facts.
        snapshot.interconnectors = []
        snapshot.activeEvent = nil
        if sample.factClass == .forecast {
            let hasGenerationForecast = !sample.generation.isEmpty
            snapshot.headline = ConditionHeadline(
                cleanliness: CarbonIntensityWording.label(for: sample.carbonIntensity),
                balance: "Forecast",
                energyPosition: hasGenerationForecast ? "Supply outlook" : "Carbon outlook",
                interpretation: hasGenerationForecast
                    ? "This forecast frame includes modelled demand, carbon and supply-mix values. Forecast values are shown in violet."
                    : "This forecast frame includes modelled demand and carbon intensity. A future supply mix is not available, so 50Hz does not project one."
            )
        } else {
            snapshot.headline = ConditionHeadline(
                cleanliness: CarbonIntensityWording.label(for: sample.carbonIntensity),
                balance: "Replay",
                energyPosition: "Observed frame",
                interpretation: sample.generation.isEmpty
                    ? "This historical frame includes observed demand and carbon intensity. Other system state is not available for this time."
                    : "This historical frame includes observed demand, carbon intensity and the supply mix. Interconnector and event state are not available for this time."
            )
        }
        return snapshot
    }

    var timelineModeLabel: String {
        guard let selectedTime else { return "LIVE" }
        guard let timeline else { return "REPLAY" }
        if selectedTime > timeline.nowBoundary {
            return isForecastTimelineUsable ? "FORECAST" : "LIVE"
        }
        return "REPLAY"
    }

    private static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        if let apiError = error as? GridAPIError, case .cancelled = apiError { return true }
        return false
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
