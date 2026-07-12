import UIKit
import UserNotifications

enum NotificationNavigation {
    static let userInfoKey = "50hz.destination"
    static let didSelectDestination = Notification.Name("50hz.notification.destination")

    private static let pendingLock = NSLock()
    nonisolated(unsafe) private static var pendingDestination: AppTab?

    static func tab(from userInfo: [AnyHashable: Any]) -> AppTab? {
        guard let rawValue = userInfo[userInfoKey] as? String,
              let destination = LocalNotificationDestination(rawValue: rawValue) else {
            return nil
        }
        return switch destination {
        case .local: .mine
        case .notebook: .log
        }
    }

    /// Retains the latest allowlisted destination before publishing it. A
    /// notification response can arrive while the app is still constructing
    /// its first SwiftUI scene, before an `onReceive` subscriber exists.
    @discardableResult
    static func retainDestination(from userInfo: [AnyHashable: Any]) -> AppTab? {
        guard let destination = tab(from: userInfo) else { return nil }
        pendingLock.withLock { pendingDestination = destination }
        return destination
    }

    /// Consumes a destination once the root app model is available. The
    /// one-shot handoff avoids replaying an old notification on later launches.
    static func consumePendingDestination() -> AppTab? {
        pendingLock.withLock {
            defer { pendingDestination = nil }
            return pendingDestination
        }
    }

    /// Clears only the destination handled by a live subscriber. If a newer
    /// response arrived in the meantime, it remains available for consumption.
    static func acknowledge(_ destination: AppTab) {
        pendingLock.withLock {
            if pendingDestination == destination {
                pendingDestination = nil
            }
        }
    }
}

final class FiftyHzAppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let userInfo = response.notification.request.content.userInfo
        guard let tab = NotificationNavigation.retainDestination(from: userInfo) else { return }
        await MainActor.run {
            NotificationCenter.default.post(
                name: NotificationNavigation.didSelectDestination,
                object: tab
            )
        }
    }
}
