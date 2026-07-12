import Foundation

enum TodayBriefingPresentation {
    static let systemScope = "Great Britain"
    static let nationalForecastScope = "GB national forecast"
    static let methodologyCopy = "50Hz displays the server-ranked briefing in its supplied order. Observed, estimated, forecast and reported facts remain distinct; omitted sections are not treated as zero."

    static func statusTitle(_ status: TodayBriefingStatus) -> String {
        switch status {
        case .complete: "Briefing complete"
        case .partial: "Partial briefing"
        case .offline: "Sources offline"
        case .observedOnly: "Observed facts only"
        case .empty: "No qualifying briefing items"
        case .unknown: "Coverage not confirmed"
        }
    }

    static func classification(_ factClass: TodayCurrentFactClass) -> String {
        switch factClass {
        case .observed: "OBSERVED"
        case .estimated: "ESTIMATED"
        case .derived: "DERIVED"
        case .reported: "REPORTED"
        case .unknown: "UNCLASSIFIED"
        }
    }

    static func classification(_ factClass: TodayFutureFactClass) -> String {
        switch factClass {
        case .forecast: "FORECAST"
        case .reported: "REPORTED"
        case .unknown: "UNCLASSIFIED"
        }
    }

    static func displayedChanges(_ briefing: TodayBriefing) -> [TodayObservedChange] {
        Array(briefing.changes.prefix(3))
    }

    static func displayedNextMoments(_ briefing: TodayBriefing) -> [TodayFutureMoment] {
        Array(briefing.nextMoments.prefix(3))
    }

    static func displayedEvents(_ briefing: TodayBriefing) -> [TodayReportedEvent] {
        Array(briefing.reportedEvents.items.prefix(3))
    }

    static func shouldShowAllEvents(_ events: TodayReportedEvents) -> Bool {
        events.totalCount > min(events.items.count, 3)
    }

    static func visibleBestWindow(
        _ briefing: TodayBriefing,
        now: Date = Date()
    ) -> TodayBestWindow? {
        guard let window = briefing.bestWindow,
              window.isCompleteNationalForecastWindow,
              let end = window.end,
              let capturedAt = window.capturedAt,
              end > now,
              capturedAt <= briefing.asOf.addingTimeInterval(300) else { return nil }
        return window
    }

    static func value(_ value: Double?, unit: String) -> String {
        guard let value, value.isFinite else { return "Value unavailable" }
        let normalizedUnit = unit.replacingOccurrences(of: "CO2", with: "CO₂")
        let number = value.formatted(.number.precision(.fractionLength(0...2)))
        return normalizedUnit.isEmpty ? number : "\(number) \(normalizedUnit)"
    }

    static func timeLabel(_ date: Date?, relativeTo localDate: String) -> String {
        guard let date else { return "Time unavailable" }
        if LondonDay.localDateKey(at: date) == localDate {
            return date.formatted(
                Date.FormatStyle(date: .omitted, time: .shortened, timeZone: LondonDay.timeZone)
            )
        }
        return date.formatted(
            Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: LondonDay.timeZone)
        )
    }

    static func dateLabel(_ date: Date) -> String {
        date.formatted(
            Date.FormatStyle(date: .abbreviated, time: .omitted, timeZone: LondonDay.timeZone)
        )
    }

    static func windowLabel(_ window: TodayBestWindow, relativeTo localDate: String) -> String {
        guard let start = window.start, let end = window.end else { return "Time unavailable" }
        let startText = timeLabel(start, relativeTo: localDate)
        let endText: String
        if LondonDay.localDateKey(at: start) == LondonDay.localDateKey(at: end) {
            endText = end.formatted(
                Date.FormatStyle(date: .omitted, time: .shortened, timeZone: LondonDay.timeZone)
            )
        } else {
            endText = timeLabel(end, relativeTo: localDate)
        }
        return "\(startText)–\(endText)"
    }

    static func eventCountLabel(_ events: TodayReportedEvents) -> String {
        let shown = min(events.items.count, 3)
        if events.totalCount > shown {
            return "\(shown) of \(events.totalCount)"
        }
        return "\(events.totalCount)"
    }

    static func sourceCountLabel(_ counts: [String: Int]) -> String? {
        let order = ["live", "delayed", "stale", "unavailable"]
        let parts = order.compactMap { state -> String? in
            guard let count = counts[state], count > 0 else { return nil }
            return "\(count) \(state)"
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
