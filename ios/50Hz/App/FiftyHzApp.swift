import SwiftUI

@main
struct FiftyHzApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model = AppModel(repository: HTTPGridRepository())

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .task {
                    await model.bootstrap()
                    if scenePhase == .active { model.startForegroundRefresh() }
                }
                .onChange(of: scenePhase) { _, newPhase in
                    if newPhase == .active {
                        model.startForegroundRefresh()
                        Task { await model.refresh() }
                    } else {
                        model.stopForegroundRefresh()
                    }
                }
                .preferredColorScheme(.dark)
        }
    }
}
