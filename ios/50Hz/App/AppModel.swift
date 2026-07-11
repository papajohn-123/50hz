import Foundation
import Combine

@MainActor
final class AppModel: ObservableObject {
    private let repository: any GridRepository

    @Published var loadPhase: LoadPhase = .loading
    @Published var snapshot: GridSnapshot?
    @Published var timeline: GridTimeline?
    @Published var selectedFuel: FuelKind?
    @Published var selectedTime: Date?
    @Published var selectedTab: AppTab = .live
    @Published var isAskPresented = false
    @Published var selectedEvent: GridEvent?

    init(repository: any GridRepository = FixtureGridRepository()) {
        self.repository = repository
    }

    func load() async {
        guard loadPhase == .loading else { return }
        do {
            async let snapshot = repository.currentSnapshot()
            async let timeline = repository.timeline()
            let (loadedSnapshot, loadedTimeline) = try await (snapshot, timeline)
            self.snapshot = loadedSnapshot
            self.timeline = loadedTimeline
            self.loadPhase = .loaded
        } catch {
            loadPhase = .failed(error.localizedDescription)
        }
    }

    func retry() async {
        loadPhase = .loading
        await load()
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
