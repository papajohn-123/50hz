import SwiftUI

@main
struct FiftyHzApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .task { await model.load() }
                .preferredColorScheme(.dark)
        }
    }
}
