import UIKit
import UserNotifications

enum NotificationNavigation {
    static let userInfoKey = "50hz.destination"
    static let didSelectDestination = Notification.Name("50hz.notification.destination")

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
        guard let tab = NotificationNavigation.tab(from: userInfo) else { return }
        NotificationCenter.default.post(
            name: NotificationNavigation.didSelectDestination,
            object: tab
        )
    }
}
