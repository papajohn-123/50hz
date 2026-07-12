import Foundation

struct LocalPlanningBounds: Hashable, Sendable {
    var earliest: Date
    var latest: Date
}

enum LocalPlanningBoundsIssue: Equatable, Sendable {
    case invalidDuration
    case earliestBeforeNextSlot
    case notOnHalfHour
    case deadlineBeforeStart
    case durationDoesNotFit
    case horizonTooLong
    case outsideForecastHorizon

    var message: String {
        switch self {
        case .invalidDuration:
            "Choose a duration from 30 minutes to 12 hours, in 30-minute steps."
        case .earliestBeforeNextSlot:
            "The earliest start must be the next available half-hour or later."
        case .notOnHalfHour:
            "Start and finish times must use half-hour boundaries."
        case .deadlineBeforeStart:
            "The latest finish must be after the earliest start."
        case .durationDoesNotFit:
            "The selected time range is too short for this activity."
        case .horizonTooLong:
            "The selected search range cannot exceed 48 hours."
        case .outsideForecastHorizon:
            "Choose a latest finish within the next 48 hours."
        }
    }
}

enum LocalPlanningBoundsPolicy {
    static let interval: TimeInterval = 30 * 60
    static let defaultHorizon: TimeInterval = 24 * 60 * 60
    static let maximumHorizon: TimeInterval = 48 * 60 * 60

    static func nextHalfHour(atOrAfter date: Date) -> Date {
        let seconds = date.timeIntervalSince1970
        let boundary = (seconds / interval).rounded(.up) * interval
        return Date(timeIntervalSince1970: boundary)
    }

    static func defaults(now: Date = Date()) -> LocalPlanningBounds {
        let earliest = nextHalfHour(atOrAfter: now)
        return LocalPlanningBounds(
            earliest: earliest,
            latest: earliest.addingTimeInterval(defaultHorizon)
        )
    }

    static func issue(
        for bounds: LocalPlanningBounds,
        durationMinutes: Int,
        now: Date = Date()
    ) -> LocalPlanningBoundsIssue? {
        guard (30...720).contains(durationMinutes), durationMinutes.isMultiple(of: 30) else {
            return .invalidDuration
        }
        guard isExactHalfHour(bounds.earliest), isExactHalfHour(bounds.latest) else {
            return .notOnHalfHour
        }
        let firstAvailableSlot = nextHalfHour(atOrAfter: now)
        guard bounds.earliest >= firstAvailableSlot else {
            return .earliestBeforeNextSlot
        }
        guard bounds.latest > bounds.earliest else { return .deadlineBeforeStart }
        let span = bounds.latest.timeIntervalSince(bounds.earliest)
        guard span >= Double(durationMinutes * 60) else { return .durationDoesNotFit }
        guard span <= maximumHorizon else { return .horizonTooLong }
        guard bounds.latest <= firstAvailableSlot.addingTimeInterval(maximumHorizon) else {
            return .outsideForecastHorizon
        }
        return nil
    }

    static func isExactHalfHour(_ date: Date) -> Bool {
        date.timeIntervalSince1970
            .truncatingRemainder(dividingBy: interval) == 0
    }
}
