import Foundation
import UserNotifications

enum LocalReminderForecastScope: String, Codable, Equatable, Sendable {
    case gbNational
    case regional

    func copyLabel(outwardRegion: String) -> String {
        switch self {
        case .gbNational:
            "GB national forecast; saved area \(outwardRegion)"
        case .regional:
            "regional forecast for \(outwardRegion)"
        }
    }
}

struct LocalReminderPlan: Equatable, Sendable {
    let localIdentifier: String
    let activityLabel: String
    let outwardRegion: String
    let scope: LocalReminderForecastScope
    let forecastCapturedAt: Date
    let start: Date
    let end: Date
    let averageIntensityGCO2KWh: Double?
}

struct LocalReminderMetadata: Codable, Equatable, Sendable {
    let identifier: String
    let outwardRegion: String
    let scope: LocalReminderForecastScope
    let start: Date
    let durationMinutes: Int
    let averageIntensityGCO2KWh: Double?
}

enum LocalReminderScheduleState: Equatable, Sendable {
    case scheduled(identifier: String)
    case denied
    case notAvailable
    case invalid
    case past
    case error
}

enum LocalReminderCancellationState: Equatable, Sendable {
    case cancelled
    case invalid
    case error
}

enum LocalNotificationAuthorizationState: Equatable, Sendable {
    case notDetermined
    case denied
    case authorized
    case provisional
    case notAvailable
}

enum LocalNotificationDestination: String, Equatable, Sendable {
    case local
    case notebook
}

struct LocalNotificationDelivery: Equatable, Sendable {
    let identifier: String
    let title: String
    let body: String
    let fireDate: Date
    let destination: LocalNotificationDestination
}

protocol LocalNotificationCenter: AnyObject {
    func authorizationState() async -> LocalNotificationAuthorizationState
    func requestAuthorization() async throws -> Bool
    func add(_ delivery: LocalNotificationDelivery) async throws
    func removePending(identifier: String) async
}

protocol LocalReminderMetadataStore: AnyObject {
    func metadata(identifier: String) -> LocalReminderMetadata?
    func save(_ metadata: LocalReminderMetadata) throws
    func remove(identifier: String) throws
}

@MainActor
final class LocalReminderScheduler {
    private let center: LocalNotificationCenter
    private let store: LocalReminderMetadataStore
    private let now: () -> Date
    private let calendar: Calendar

    init(
        center: LocalNotificationCenter,
        store: LocalReminderMetadataStore,
        now: @escaping () -> Date = Date.init,
        calendar: Calendar = .localReminderLondon
    ) {
        self.center = center
        self.store = store
        self.now = now
        self.calendar = calendar
    }

    /// Reads the current status without prompting. Permission is requested only
    /// from `schedule`, which must be called by an explicit user action.
    func authorizationState() async -> LocalNotificationAuthorizationState {
        await center.authorizationState()
    }

    func schedule(_ plan: LocalReminderPlan) async -> LocalReminderScheduleState {
        let evaluationTime = now()
        guard let validated = LocalReminderValidation.validate(plan, now: evaluationTime) else {
            return LocalReminderValidation.isPast(plan, now: evaluationTime) ? .past : .invalid
        }

        let authorization = await center.authorizationState()
        switch authorization {
        case .denied:
            return .denied
        case .notAvailable:
            return .notAvailable
        case .notDetermined:
            do {
                guard try await center.requestAuthorization() else {
                    return .denied
                }
            } catch {
                return .error
            }
        case .authorized, .provisional:
            break
        }
        // A permission sheet can remain open until the selected window starts.
        // Never let a now-past calendar trigger roll to a future matching date.
        guard validated.metadata.start > now() else { return .past }

        let delivery = LocalNotificationDelivery(
            identifier: validated.metadata.identifier,
            title: "Forecast reminder: \(validated.activityLabel)",
            body: reminderBody(for: validated),
            fireDate: validated.metadata.start,
            destination: .local
        )
        do {
            // UNUserNotificationCenter replaces a pending request atomically
            // when its stable identifier is reused. No refresh path calls this
            // method, so a changed forecast is never silently rescheduled.
            try await center.add(delivery)
            do {
                try store.save(validated.metadata)
            } catch {
                await center.removePending(identifier: validated.metadata.identifier)
                return .error
            }
            return .scheduled(identifier: validated.metadata.identifier)
        } catch {
            return .error
        }
    }

    func cancel(localIdentifier: String) async -> LocalReminderCancellationState {
        guard let identifier = LocalReminderValidation.stableIdentifier(localIdentifier) else {
            return .invalid
        }
        await center.removePending(identifier: identifier)
        do {
            try store.remove(identifier: identifier)
            return .cancelled
        } catch {
            return .error
        }
    }

    func storedMetadata(localIdentifier: String) -> LocalReminderMetadata? {
        guard let identifier = LocalReminderValidation.stableIdentifier(localIdentifier) else {
            return nil
        }
        return store.metadata(identifier: identifier)
    }

    private func reminderBody(for plan: LocalReminderValidation.ValidatedPlan) -> String {
        let windowTime = DateFormatter.localReminderWindow(calendar: calendar)
        let capture = DateFormatter.localReminderCapture(calendar: calendar)
        var body = "\(plan.activityLabel) is planned for "
            + "\(windowTime.string(from: plan.metadata.start))–\(windowTime.string(from: plan.end)). "
            + "This uses the \(plan.scope.copyLabel(outwardRegion: plan.metadata.outwardRegion)); "
            + "forecast captured \(capture.string(from: plan.forecastCapturedAt))."
        if let intensity = plan.metadata.averageIntensityGCO2KWh {
            body += " Average forecast intensity \(intensity.formatted(.number.precision(.fractionLength(0...1)))) gCO₂/kWh."
        }
        return body
    }
}

enum LocalReminderValidation {
    struct ValidatedPlan {
        let activityLabel: String
        let scope: LocalReminderForecastScope
        let forecastCapturedAt: Date
        let end: Date
        let metadata: LocalReminderMetadata
    }

    private static let identifierPattern = try! NSRegularExpression(
        pattern: #"^[a-z0-9][a-z0-9._-]{0,39}$"#
    )
    private static let outwardPattern = try! NSRegularExpression(
        pattern: #"^(?:GIR|[A-Z]{1,2}[0-9][A-Z0-9]?)$"#
    )
    private static let fullPostcodePattern = try! NSRegularExpression(
        pattern: #"\b(?:GIR\s?0AA|[A-Z]{1,2}[0-9][A-Z0-9]?\s?[0-9][A-Z]{2})\b"#,
        options: [.caseInsensitive]
    )

    static func stableIdentifier(_ localIdentifier: String) -> String? {
        let normalized = localIdentifier.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard matches(identifierPattern, normalized) else { return nil }
        return "50hz.local.\(normalized)"
    }

    static func validate(_ plan: LocalReminderPlan, now: Date) -> ValidatedPlan? {
        guard let identifier = stableIdentifier(plan.localIdentifier) else { return nil }
        let activity = plan.activityLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !activity.isEmpty,
              activity.count <= 40,
              activity.rangeOfCharacter(from: .controlCharacters) == nil,
              !containsFullPostcode(activity)
        else { return nil }

        let outward = plan.outwardRegion.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
        guard matches(outwardPattern, outward), !plan.outwardRegion.contains(where: { $0.isWhitespace }) else {
            return nil
        }
        guard plan.start > now,
              plan.end > plan.start,
              plan.forecastCapturedAt <= now.addingTimeInterval(5 * 60)
        else {
            return nil
        }
        let durationSeconds = plan.end.timeIntervalSince(plan.start)
        let durationMinutes = Int(durationSeconds / 60)
        guard durationSeconds == Double(durationMinutes * 60),
              (30...720).contains(durationMinutes),
              durationMinutes.isMultiple(of: 30),
              plan.start.timeIntervalSince1970.rounded() == plan.start.timeIntervalSince1970
        else { return nil }
        if let intensity = plan.averageIntensityGCO2KWh,
           (!intensity.isFinite || intensity < 0) {
            return nil
        }
        return ValidatedPlan(
            activityLabel: activity,
            scope: plan.scope,
            forecastCapturedAt: plan.forecastCapturedAt,
            end: plan.end,
            metadata: LocalReminderMetadata(
                identifier: identifier,
                outwardRegion: outward,
                scope: plan.scope,
                start: plan.start,
                durationMinutes: durationMinutes,
                averageIntensityGCO2KWh: plan.averageIntensityGCO2KWh
            )
        )
    }

    static func isPast(_ plan: LocalReminderPlan, now: Date) -> Bool {
        plan.start <= now || plan.end <= now
    }

    private static func containsFullPostcode(_ value: String) -> Bool {
        let range = NSRange(value.startIndex..., in: value)
        return fullPostcodePattern.firstMatch(in: value, range: range) != nil
    }

    private static func matches(_ expression: NSRegularExpression, _ value: String) -> Bool {
        let range = NSRange(value.startIndex..., in: value)
        return expression.firstMatch(in: value, range: range)?.range == range
    }
}

func localReminderPlanHasMaterialChange(
    scheduled: LocalReminderMetadata,
    refreshed: LocalReminderMetadata
) -> Bool {
    if scheduled.identifier != refreshed.identifier
        || scheduled.outwardRegion.caseInsensitiveCompare(refreshed.outwardRegion) != .orderedSame
        || scheduled.scope != refreshed.scope
        || scheduled.durationMinutes != refreshed.durationMinutes {
        return true
    }
    if abs(refreshed.start.timeIntervalSince(scheduled.start)) >= 30 * 60 {
        return true
    }
    if let old = scheduled.averageIntensityGCO2KWh,
       let new = refreshed.averageIntensityGCO2KWh {
        if old == 0 { return new != 0 }
        return abs(new - old) / abs(old) >= 0.15
    }
    return false
}

final class UserDefaultsLocalReminderMetadataStore: LocalReminderMetadataStore {
    private let defaults: UserDefaults
    private let storageKey: String

    init(
        defaults: UserDefaults = .standard,
        storageKey: String = "50hz.local-reminder-metadata.v1"
    ) {
        self.defaults = defaults
        self.storageKey = storageKey
    }

    func metadata(identifier: String) -> LocalReminderMetadata? {
        load()[identifier]
    }

    func save(_ metadata: LocalReminderMetadata) throws {
        var entries = load()
        entries[metadata.identifier] = metadata
        // Keep storage bounded even if a future UI offers many saved regions.
        if entries.count > 32 {
            entries = Dictionary(
                uniqueKeysWithValues: entries.values
                    .sorted { $0.start > $1.start }
                    .prefix(32)
                    .map { ($0.identifier, $0) }
            )
        }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        defaults.set(try encoder.encode(entries), forKey: storageKey)
    }

    func remove(identifier: String) throws {
        var entries = load()
        entries.removeValue(forKey: identifier)
        if entries.isEmpty {
            defaults.removeObject(forKey: storageKey)
        } else {
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            defaults.set(try encoder.encode(entries), forKey: storageKey)
        }
    }

    private func load() -> [String: LocalReminderMetadata] {
        guard let data = defaults.data(forKey: storageKey), data.count <= 64_000 else {
            return [:]
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode([String: LocalReminderMetadata].self, from: data)) ?? [:]
    }
}

final class SystemLocalNotificationCenter: LocalNotificationCenter {
    private let center: UNUserNotificationCenter

    init(center: UNUserNotificationCenter = .current()) {
        self.center = center
    }

    func authorizationState() async -> LocalNotificationAuthorizationState {
        let status = await center.notificationSettings().authorizationStatus
        switch status {
        case .notDetermined: return .notDetermined
        case .denied: return .denied
        case .authorized: return .authorized
        case .provisional, .ephemeral: return .provisional
        @unknown default: return .notAvailable
        }
    }

    func requestAuthorization() async throws -> Bool {
        try await center.requestAuthorization(options: [.alert, .sound])
    }

    func add(_ delivery: LocalNotificationDelivery) async throws {
        let content = UNMutableNotificationContent()
        content.title = delivery.title
        content.body = delivery.body
        content.sound = .default
        content.userInfo = [
            NotificationNavigation.userInfoKey: delivery.destination.rawValue
        ]
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = .autoupdatingCurrent
        var components = calendar.dateComponents(
            [.year, .month, .day, .hour, .minute, .second],
            from: delivery.fireDate
        )
        components.calendar = calendar
        components.timeZone = calendar.timeZone
        let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
        try await center.add(
            UNNotificationRequest(
                identifier: delivery.identifier,
                content: content,
                trigger: trigger
            )
        )
    }

    func removePending(identifier: String) async {
        center.removePendingNotificationRequests(withIdentifiers: [identifier])
    }
}

private extension DateFormatter {
    static func localReminderWindow(calendar: Calendar) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_GB")
        formatter.timeZone = calendar.timeZone
        formatter.setLocalizedDateFormatFromTemplate("d MMM HH:mm")
        return formatter
    }

    static func localReminderCapture(calendar: Calendar) -> DateFormatter {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.locale = Locale(identifier: "en_GB")
        formatter.timeZone = calendar.timeZone
        formatter.setLocalizedDateFormatFromTemplate("d MMM HH:mm")
        return formatter
    }
}

private extension Calendar {
    static var localReminderLondon: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London")
            ?? TimeZone(secondsFromGMT: 0)!
        return calendar
    }
}
