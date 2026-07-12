import SwiftUI

@main
struct FiftyHzApp: App {
    @UIApplicationDelegateAdaptor(FiftyHzAppDelegate.self) private var appDelegate
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = AppModel(repository: HTTPGridRepository())

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .task {
                    await model.bootstrap()
                    applyPendingNotificationDestination()
                    if scenePhase == .active { model.startForegroundRefresh() }
                }
                .onChange(of: scenePhase) { _, newPhase in
                    if newPhase == .active {
                        applyPendingNotificationDestination()
                        model.startForegroundRefresh()
                        Task { await model.refresh() }
                    } else {
                        model.stopForegroundRefresh()
                    }
                }
                .onReceive(
                    NotificationCenter.default.publisher(
                        for: NotificationNavigation.didSelectDestination
                    )
                ) { notification in
                    if let tab = notification.object as? AppTab {
                        model.selectedTab = tab
                        NotificationNavigation.acknowledge(tab)
                    }
                }
                .preferredColorScheme(.dark)
        }
    }

    private func applyPendingNotificationDestination() {
        if let tab = NotificationNavigation.consumePendingDestination() {
            model.selectedTab = tab
        }
    }
}
