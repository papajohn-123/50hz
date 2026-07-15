import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("onboarding.welcome.complete") private var hasCompletedWelcome = false
    @State private var isWelcomePresented = false
    @State private var isInfoPresented = false
    @State private var replayWelcomeAfterInfo = false

    var body: some View {
        TabView(selection: $model.selectedTab) {
            LiveView()
                .tabItem { Label("Grid", systemImage: "map") }
                .tag(AppTab.live)

            PlanView()
                .tabItem { Label("Plan", systemImage: "clock.badge.checkmark") }
                .tag(AppTab.mine)

            LogView()
                .tabItem { Label("Notebook", systemImage: "book.closed") }
                .tag(AppTab.log)
        }
        .tint(GridTheme.liveCyan)
        .toolbarBackground(GridTheme.background.opacity(0.96), for: .tabBar)
        .toolbarBackground(.visible, for: .tabBar)
        .toolbarColorScheme(.dark, for: .tabBar)
        .environment(\.openInfo, OpenInfoAction { isInfoPresented = true })
        .sheet(isPresented: $isWelcomePresented, onDismiss: {
            hasCompletedWelcome = true
        }) {
            WelcomeSheet {
                hasCompletedWelcome = true
                isWelcomePresented = false
            }
            .presentationDetents([.large])
            .presentationDragIndicator(.hidden)
            .presentationBackground(GridTheme.background)
        }
        .sheet(isPresented: $isInfoPresented, onDismiss: {
            guard replayWelcomeAfterInfo else { return }
            replayWelcomeAfterInfo = false
            isWelcomePresented = true
        }) {
            InfoHelpSheet {
                replayWelcomeAfterInfo = true
                isInfoPresented = false
            }
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
            .presentationBackground(GridTheme.background)
        }
        .onAppear {
            if model.selectedTab == .today {
                model.selectedTab = .mine
            }
            if WelcomePresentationPolicy.shouldPresent(hasCompletedWelcome: hasCompletedWelcome) {
                isWelcomePresented = true
            }
        }
        .onChange(of: model.selectedTab) { _, selectedTab in
            if selectedTab == .today {
                model.selectedTab = .mine
            }
        }
    }
}

#if DEBUG
private struct PreviewRoot: View {
    @StateObject private var model: AppModel
    let shouldLoad: Bool

    init(state: FreshnessState = .live, shouldLoad: Bool = true) {
        _model = StateObject(wrappedValue: AppModel(repository: PreviewGridRepository(state: state)))
        self.shouldLoad = shouldLoad
    }

    var body: some View {
        RootView()
            .environmentObject(model)
            .task {
                if shouldLoad { await model.load() }
            }
            .preferredColorScheme(.dark)
    }
}

private struct PreviewGridRepository: GridRepository {
    let state: FreshnessState

    func currentSnapshot() async throws -> GridSnapshot {
        var snapshot = try await FixtureGridRepository().currentSnapshot()
        snapshot.freshness = state
        if state == .stale { snapshot.freshnessAgeSeconds = 17 * 60 }
        if state == .offline { snapshot.freshnessAgeSeconds = 68 * 60 }
        if state == .critical {
            snapshot.activeEvent = GridEvent(
                id: "preview-critical-event",
                title: "Reported interconnector outage",
                summary: "An authoritative REMIT notice reports a 700 MW unavailability on an interconnector.",
                severity: "material",
                evidenceClass: "reported outage",
                startedAt: snapshot.timestamp.addingTimeInterval(-900),
                sourceIDs: ["remit-preview-441"],
                isAuthoritativelyReported: true
            )
        }
        return snapshot
    }

    func timeline() async throws -> GridTimeline {
        try await FixtureGridRepository().timeline()
    }
}

struct RootView_Previews: PreviewProvider {
    static var previews: some View {
        Group {
            PreviewRoot()
                .previewDisplayName("Live")
            PreviewRoot(shouldLoad: false)
                .previewDisplayName("Loading")
            PreviewRoot(state: .stale)
                .previewDisplayName("Stale")
            PreviewRoot(state: .offline)
                .previewDisplayName("Offline")
            PreviewRoot(state: .critical)
                .previewDisplayName("Critical event")
        }
    }
}
#endif
