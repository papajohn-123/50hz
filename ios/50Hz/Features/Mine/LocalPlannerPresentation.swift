import Foundation

enum LocalActivityPreset: String, CaseIterable, Identifiable, Sendable {
    case laundry
    case dishwasher
    case evTopUp
    case custom

    var id: String { rawValue }

    var title: String {
        switch self {
        case .laundry: "Laundry"
        case .dishwasher: "Dishwasher"
        case .evTopUp: "EV top-up"
        case .custom: "Custom"
        }
    }

    var systemImage: String {
        switch self {
        case .laundry: "washer"
        case .dishwasher: "dishwasher"
        case .evTopUp: "bolt.car"
        case .custom: "slider.horizontal.3"
        }
    }

    var presetDurationMinutes: Int? {
        switch self {
        case .laundry, .dishwasher: 120
        case .evTopUp: 240
        case .custom: nil
        }
    }

    func durationMinutes(customDurationMinutes: Int) -> Int {
        presetDurationMinutes ?? min(max(customDurationMinutes, 30), 720)
    }
}

enum LocalPlannerCopy {
    static let nationalScope = "GB national forecast · not regional or postcode-level"
    static let support = "Choose how long it needs to run. 50Hz checks complete continuous windows in the next available GB national forecast."
    static let methodology = "Flexible-use results compare continuous half-hour windows in the GB national carbon forecast. They report forecast intensity, not energy use, total emissions or price."

    static func resultTitle(for response: LocalWindowsResponse) -> String {
        switch response.plan.status {
        case .lowerCarbonWindow:
            return "A meaningfully lower-intensity window is available."
        case .noMeaningfulDifference:
            return "No meaningful difference."
        case .windowFound:
            return "Lowest complete window found."
        case .insufficientCoverage:
            return "No reliable window available."
        case .unknown:
            return response.plan.recommendedWindow == nil
                ? "No reliable window available."
                : "Lowest complete window found."
        }
    }

    static func comparisonSummary(
        _ comparison: LocalFlexibleUseComparison?,
        recommended: LocalChargingWindow
    ) -> String? {
        guard comparison?.status == .compatible,
              let comparison,
              let startNow = comparison.startNowWindow else { return nil }

        let averages = "Starting now averages \(intensity(startNow.averageIntensityGCO2KWh)); this window averages \(intensity(recommended.averageIntensityGCO2KWh))."
        if comparison.isMeaningful == false {
            return "No meaningful difference. \(averages)"
        }
        if comparison.isMeaningful == true,
           let percent = comparison.percentLowerThanStartNow {
            return "\(percent.formatted(.number.precision(.fractionLength(0...1))))% lower forecast intensity than starting now. \(averages)"
        }
        return averages
    }

    static func coverageSummary(_ coverage: LocalForecastCoverage) -> String {
        guard coverage.expectedIntervalCount > 0 else { return "Coverage details unavailable" }
        let percent = Int((min(max(coverage.coverageFraction, 0), 1) * 100).rounded())
        return "\(coverage.availableIntervalCount) of \(coverage.expectedIntervalCount) intervals · \(percent)% coverage"
    }

    static func durationLabel(minutes: Int) -> String {
        if minutes < 60 { return "\(minutes) min" }
        let hours = Double(minutes) / 60
        return "\(hours.formatted(.number.precision(.fractionLength(hours.rounded() == hours ? 0 : 1)))) hr"
    }

    static func spokenDuration(minutes: Int) -> String {
        if minutes < 60 { return "\(minutes) minutes" }
        let hours = Double(minutes) / 60
        let unit = hours == 1 ? "hour" : "hours"
        return "\(hours.formatted(.number.precision(.fractionLength(hours.rounded() == hours ? 0 : 1)))) \(unit)"
    }

    static func intensity(_ value: Double) -> String {
        "\(value.formatted(.number.precision(.fractionLength(0...1)))) gCO₂/kWh"
    }

    static func isTooOldToRecommend(_ response: LocalWindowsResponse, now: Date = Date()) -> Bool {
        guard let capturedAt = response.forecast.capturedAt else { return true }
        let staleAfter = TimeInterval(response.forecast.captureStaleAfterSeconds ?? 5_400)
        let captureAge = now.timeIntervalSince(capturedAt)
        if captureAge > staleAfter || captureAge < -300 { return true }
        if let end = response.plan.recommendedWindow?.end, end <= now { return true }
        return false
    }
}
