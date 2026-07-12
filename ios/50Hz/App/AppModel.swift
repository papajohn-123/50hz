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
    @Published var todayBriefing: TodayBriefing?
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
    @Published var localWindows: LocalWindowsResponse?
    @Published var localWindowsLoadPhase: LoadPhase = .loading
    @Published var localWindowsError: String?
    @Published var localWindowsRequest: LocalWindowsRequest?
    @Published var localWindowsIsFromCache = false
    @Published var isRefreshingLocalWindows = false
    @Published var eventsError: String?
    @Published var gameLoadPhase: LoadPhase = .loading
    @Published var gameRefreshError: String?
    @Published var briefingLoadPhase: LoadPhase = .loading
    @Published var briefingError: String?
    @Published var briefingRequestDate: String?
    @Published var briefingIsFromCache = false
    @Published var isRefreshingBriefing = false

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
        let currentLondonDate = LondonDay.localDateKey()
        async let cachedBriefing = repository.cachedTodayBriefing(localDate: currentLondonDate)
        let (snapshot, timeline, events, dailyGame, briefing) = await (
            cachedSnapshot,
            cachedTimeline,
            cachedEvents,
            cachedDailyGame,
            cachedBriefing
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
        if let briefing {
            todayBriefing = briefing
            briefingRequestDate = currentLondonDate
            briefingIsFromCache = true
            briefingLoadPhase = .loaded
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
        if selectedTab == .today {
            Task { [weak self] in await self?.loadTodayBriefing() }
        }
    }

    func retry() async {
        if snapshot == nil { loadPhase = .loading }
        await refresh()
    }

    func loadRegion(postcode: String) async {
        guard let requested = PostcodePrivacy.validatedOutwardCode(from: postcode) else {
            let message = GridAPIError.invalidPostcode.localizedDescription
            regionError = message
            regionLoadPhase = regionalContext == nil ? .failed(message) : .loaded
            return
        }
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

    func loadLocalWindows(postcode: String, durationMinutes: Int) async {
        guard let outward = PostcodePrivacy.validatedOutwardCode(from: postcode) else {
            let message = GridAPIError.invalidPostcode.localizedDescription
            localWindowsError = message
            localWindowsLoadPhase = localWindows == nil ? .failed(message) : .loaded
            return
        }
        guard (30...720).contains(durationMinutes), durationMinutes.isMultiple(of: 30) else {
            let message = "Choose a duration from 30 minutes to 12 hours, in 30-minute steps."
            localWindowsError = message
            localWindowsLoadPhase = .failed(message)
            return
        }

        let request = LocalWindowsRequest(postcode: outward, durationMinutes: durationMinutes)
        localWindowsRequest = request
        localWindowsError = nil
        isRefreshingLocalWindows = true
        defer {
            if localWindowsRequest == request {
                isRefreshingLocalWindows = false
            }
        }

        if !Self.matches(localWindows, request: request) {
            localWindows = nil
            localWindowsIsFromCache = false
            localWindowsLoadPhase = .loading

            if let cached = await repository.cachedLocalWindows(
                postcode: request.outwardPostcode,
                durationMinutes: request.durationMinutes
            ) {
                guard !Task.isCancelled, localWindowsRequest == request else { return }
                if Self.matches(cached, request: request) {
                    localWindows = cached
                    localWindowsIsFromCache = true
                    localWindowsLoadPhase = .loaded
                }
            }
        } else {
            localWindowsLoadPhase = .loaded
        }

        guard !Task.isCancelled, localWindowsRequest == request else { return }

        do {
            let refreshed = try await repository.localWindows(
                postcode: request.outwardPostcode,
                durationMinutes: request.durationMinutes
            )
            guard !Task.isCancelled, localWindowsRequest == request else { return }
            guard Self.matches(refreshed, request: request) else {
                throw GridAPIError.decoding("Local window response did not match its request.")
            }
            localWindows = refreshed
            localWindowsIsFromCache = false
            localWindowsLoadPhase = .loaded
            localWindowsError = nil
        } catch {
            guard !Task.isCancelled,
                  localWindowsRequest == request,
                  !Self.isCancellation(error) else { return }
            localWindowsError = error.localizedDescription
            localWindowsLoadPhase = localWindows == nil ? .failed(error.localizedDescription) : .loaded
        }
    }

    func loadTodayBriefing(localDate: String = LondonDay.localDateKey()) async {
        let request = TodayBriefingRequest(localDate: localDate)
        guard request.localDate != "unknown-date" else {
            let message = "The current London date could not be resolved."
            briefingError = message
            briefingLoadPhase = todayBriefing == nil ? .failed(message) : .loaded
            return
        }
        if isRefreshingBriefing, briefingRequestDate == request.localDate { return }

        briefingRequestDate = request.localDate
        briefingError = nil
        isRefreshingBriefing = true
        defer {
            if briefingRequestDate == request.localDate {
                isRefreshingBriefing = false
            }
        }

        if todayBriefing?.matches(request) != true {
            todayBriefing = nil
            briefingIsFromCache = false
            briefingLoadPhase = .loading

            if let cached = await repository.cachedTodayBriefing(localDate: request.localDate) {
                guard !Task.isCancelled, briefingRequestDate == request.localDate else { return }
                if cached.matches(request) {
                    todayBriefing = cached
                    briefingIsFromCache = true
                    briefingLoadPhase = .loaded
                }
            }
        } else {
            briefingLoadPhase = .loaded
        }

        guard !Task.isCancelled, briefingRequestDate == request.localDate else { return }

        do {
            let refreshed = try await repository.todayBriefing(localDate: request.localDate)
            guard !Task.isCancelled, briefingRequestDate == request.localDate else { return }
            guard refreshed.matches(request) else {
                throw GridAPIError.decoding("Today briefing did not match the requested London date or methodology.")
            }
            todayBriefing = refreshed
            briefingIsFromCache = false
            briefingLoadPhase = .loaded
            briefingError = nil
        } catch {
            guard !Task.isCancelled,
                  briefingRequestDate == request.localDate,
                  !Self.isCancellation(error) else { return }
            briefingError = error.localizedDescription
            briefingLoadPhase = todayBriefing == nil ? .failed(error.localizedDescription) : .loaded
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

    private static func matches(
        _ response: LocalWindowsResponse?,
        request: LocalWindowsRequest
    ) -> Bool {
        guard let response else { return false }
        return response.matches(request)
    }
}

private extension Sequence where Element: Hashable {
    func uniqued() -> [Element] {
        var seen = Set<Element>()
        return filter { seen.insert($0).inserted }
    }
}
