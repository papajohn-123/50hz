import SwiftUI

/// The action-first home for regional context and flexible-use planning.
/// Detailed controls remain available in `MineView`, but the default surface
/// begins with the recommendation rather than the form that produced it.
struct PlanView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("mine.postcode") private var postcode = ""
    @AppStorage("mine.activity") private var activityRawValue = LocalActivityPreset.laundry.rawValue
    @AppStorage("mine.customDurationMinutes") private var customDurationMinutes = 120
    @AppStorage("mine.lastPlannedActivity") private var lastPlannedActivityRawValue = LocalActivityPreset.laundry.rawValue
    @State private var isPlannerPresented = false

    private let londonTimeZone = TimeZone(identifier: "Europe/London") ?? .current

    private var selectedActivity: LocalActivityPreset {
        LocalActivityPreset(rawValue: activityRawValue) ?? .laundry
    }

    private var selectedDurationMinutes: Int {
        selectedActivity.durationMinutes(customDurationMinutes: customDurationMinutes)
    }

    private var request: LocalWindowsRequest {
        LocalWindowsRequest(postcode: postcode, durationMinutes: selectedDurationMinutes)
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 26) {
                header
                recommendation

                if let timeline = model.displayTimeline {
                    TodayHorizonCurve(timeline: timeline)

                    if !model.isForecastTimelineUsable,
                       model.timeline?.samples.contains(where: { $0.factClass == .forecast }) == true {
                        Label(model.forecastUnavailableReason, systemImage: "clock.badge.exclamationmark")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.staleAmber)
                            .fixedSize(horizontal: false, vertical: true)
                            .accessibilityLabel("Forecast withheld. \(model.forecastUnavailableReason)")
                    }
                }

                regionalSnapshot
                plannerAction
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .gridPageBackground()
        .task { await loadInitialContextIfNeeded() }
        .sheet(isPresented: $isPlannerPresented) {
            NavigationStack {
                MineView()
                    .navigationTitle("Adjust plan")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarTrailing) {
                            Button("Done") { isPlannerPresented = false }
                                .foregroundStyle(GridTheme.liveCyan)
                        }
                    }
            }
            .presentationDetents([.large])
            .presentationDragIndicator(.visible)
            .presentationBackground(GridTheme.background)
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text("Plan")
                .font(.system(.largeTitle, design: .rounded, weight: .medium))
                .tracking(-1.2)
                .accessibilityAddTraits(.isHeader)
            Spacer(minLength: 8)
            Label(regionLabel, systemImage: "location.fill")
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
                .lineLimit(1)
            GlobalInfoButton()
        }
    }

    @ViewBuilder
    private var recommendation: some View {
        if let response = model.localWindows,
           response.matches(request),
           response.hasSafeNationalForecastScope,
           !LocalPlannerCopy.isTooOldToRecommend(response),
           let window = response.plan.recommendedWindow {
            VStack(alignment: .leading, spacing: 13) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Label(selectedActivity.title, systemImage: selectedActivity.systemImage)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.forecastViolet)
                    Spacer(minLength: 8)
                    Text(LocalPlannerCopy.durationLabel(minutes: response.plan.requestedDurationMinutes))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(GridTheme.textTertiary)
                }

                Text(recommendationLead(response))
                    .font(.system(.title2, design: .rounded, weight: .medium))
                    .tracking(-0.55)
                    .foregroundStyle(GridTheme.textPrimary)

                Text(windowLabel(window))
                    .font(.system(.title, design: .monospaced, weight: .medium))
                    .foregroundStyle(GridTheme.forecastViolet)
                    .fixedSize(horizontal: false, vertical: true)

                HStack(spacing: 0) {
                    recommendationFact(
                        LocalPlannerCopy.intensity(window.averageIntensityGCO2KWh),
                        label: "Forecast average"
                    )
                    Rectangle()
                        .fill(GridTheme.hairline)
                        .frame(width: 1, height: 34)
                        .padding(.horizontal, 15)
                    recommendationFact(
                        comparisonValue(response),
                        label: "Compared with now"
                    )
                }

                if model.localWindowsIsFromCache || model.localWindowsError != nil || model.isRefreshingLocalWindows {
                    HStack(spacing: 8) {
                        Image(systemName: model.localWindowsError == nil ? "clock.arrow.circlepath" : "wifi.exclamationmark")
                            .foregroundStyle(model.localWindowsError == nil ? GridTheme.textTertiary : GridTheme.staleAmber)
                            .accessibilityHidden(true)
                        Text(planConfidenceLabel)
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(model.localWindowsError == nil ? GridTheme.textTertiary : GridTheme.staleAmber)
                        Spacer(minLength: 4)
                        if model.localWindowsError != nil, !model.isRefreshingLocalWindows {
                            Button("Retry") { refreshPlan() }
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(GridTheme.forecastViolet)
                                .frame(minHeight: 44)
                        }
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(planConfidenceAccessibilityLabel)
                }
            }
            .padding(.leading, 15)
            .overlay(alignment: .leading) {
                Rectangle()
                    .fill(GridTheme.forecastViolet)
                    .frame(width: 2)
            }
        } else if model.localWindowsLoadPhase == .loading || model.isRefreshingLocalWindows {
            HStack(spacing: 12) {
                ProgressView().tint(GridTheme.forecastViolet)
                Text("Finding the cleanest complete window…")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            }
            .frame(minHeight: 74)
        } else {
            VStack(alignment: .leading, spacing: 9) {
                Text("No current recommendation")
                    .font(.title2.weight(.medium))
                Text(model.localWindowsError ?? "A complete, current GB forecast window is not available.")
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(3)
                Button("Try again") {
                    Task {
                        await model.loadLocalWindows(
                            postcode: postcode,
                            durationMinutes: selectedDurationMinutes
                        )
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.forecastViolet)
                .frame(minHeight: 44)
            }
            .padding(.leading, 15)
            .overlay(alignment: .leading) {
                Rectangle().fill(GridTheme.staleAmber).frame(width: 2)
            }
        }
    }

    private var regionalSnapshot: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            regionalSnapshot(at: context.date)
        }
    }

    @ViewBuilder
    private func regionalSnapshot(at now: Date) -> some View {
        if let context = model.regionalContext {
            VStack(alignment: .leading, spacing: 12) {
                SectionLabel("Regional forecast", trailing: regionalStateLabel(context, at: now))
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(context.carbonIntensity.formatted(.number.precision(.fractionLength(0))))
                        .font(.system(.title, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.forecastViolet)
                    Text("gCO₂/kWh")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                    Spacer(minLength: 8)
                    Text(context.rating.lowercased())
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(GridTheme.textSecondary)
                }

                let delta = context.carbonIntensity - context.nationalCarbonIntensity
                Text(regionalComparison(delta: delta, national: context.nationalCarbonIntensity))
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)

                if regionalIsDelayed(context, at: now), let periodEnd = context.regionalPeriodEnd {
                    Text("Latest available regional period ended \(periodEnd.formatted(.dateTime.hour().minute())).")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.staleAmber)
                }

                if let error = model.regionError {
                    HStack(spacing: 8) {
                        Label("Saved value · refresh failed", systemImage: "wifi.exclamationmark")
                            .font(.caption2.weight(.medium))
                            .foregroundStyle(GridTheme.staleAmber)
                        Spacer(minLength: 4)
                        Button("Retry") {
                            Task { await model.loadRegion(postcode: postcode) }
                        }
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(minHeight: 44)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Saved regional value. Refresh failed. \(error)")
                }
            }
        } else if model.regionLoadPhase == .loading {
            ProgressView("Loading regional forecast…")
                .font(.caption)
                .tint(GridTheme.liveCyan)
        } else if case .failed(let message) = model.regionLoadPhase {
            VStack(alignment: .leading, spacing: 8) {
                Text("Regional forecast unavailable")
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(3)
                Button("Retry") {
                    Task { await model.loadRegion(postcode: postcode) }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
                .frame(minHeight: 44)
            }
        }
    }

    private var plannerAction: some View {
        VStack(alignment: .leading, spacing: 12) {
            Hairline()

            HStack(spacing: 12) {
                Menu {
                    ForEach(LocalActivityPreset.allCases) { activity in
                        Button {
                            activityRawValue = activity.rawValue
                            lastPlannedActivityRawValue = activity.rawValue
                            Task {
                                await model.loadLocalWindows(
                                    postcode: postcode,
                                    durationMinutes: activity.durationMinutes(
                                        customDurationMinutes: customDurationMinutes
                                    )
                                )
                            }
                        } label: {
                            Label(activity.title, systemImage: activity.systemImage)
                        }
                    }
                } label: {
                    HStack(spacing: 10) {
                        Image(systemName: selectedActivity.systemImage)
                            .foregroundStyle(GridTheme.liveCyan)
                        Text(selectedActivity.title)
                            .font(.subheadline.weight(.semibold))
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.textTertiary)
                    }
                    .foregroundStyle(GridTheme.textPrimary)
                    .frame(minHeight: 48)
                }

                Spacer(minLength: 4)

                Button("Adjust plan") { isPlannerPresented = true }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(GridTheme.background)
                    .padding(.horizontal, 16)
                    .frame(minHeight: 48)
                    .background(GridTheme.liveCyan, in: Capsule())
            }

            Text("Activity planning uses the GB national carbon forecast; the regional value above is context only.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var regionLabel: String {
        if let context = model.regionalContext { return "\(context.name) · \(context.postcode)" }
        return PostcodePrivacy.outwardCode(from: postcode)
    }

    private func recommendationLead(_ response: LocalWindowsResponse) -> String {
        if response.plan.comparison?.isMeaningful == true,
           let percent = response.plan.comparison?.percentLowerThanStartNow {
            return "\(percent.formatted(.number.precision(.fractionLength(0))))% lower forecast carbon intensity"
        }
        return LocalPlannerCopy.resultTitle(for: response)
    }

    private var planConfidenceLabel: String {
        if model.isRefreshingLocalWindows { return "Checking the saved result…" }
        if model.localWindowsError != nil { return "Saved result · refresh failed" }
        if model.localWindowsIsFromCache { return "Saved result · checking for an update" }
        return "Forecast checked"
    }

    private var planConfidenceAccessibilityLabel: String {
        guard let error = model.localWindowsError else { return planConfidenceLabel }
        return "\(planConfidenceLabel). \(error)"
    }

    private func regionalStateLabel(_ context: RegionalGridContext, at now: Date) -> String {
        if model.regionError != nil { return "SAVED · \(context.name.uppercased())" }
        if regionalIsDelayed(context, at: now) { return "DELAYED · \(context.name.uppercased())" }
        return "CURRENT · \(context.name.uppercased())"
    }

    private func regionalIsDelayed(_ context: RegionalGridContext, at now: Date) -> Bool {
        context.regionalIsDelayed == true
            || context.regionalPeriodEnd.map { $0 <= now } == true
    }

    private func refreshPlan() {
        Task {
            lastPlannedActivityRawValue = selectedActivity.rawValue
            await model.loadLocalWindows(
                postcode: postcode,
                durationMinutes: selectedDurationMinutes
            )
        }
    }

    @MainActor
    private func loadInitialContextIfNeeded() async {
        await model.loadRegion(postcode: postcode)
        guard model.localWindowsRequest == nil else { return }
        lastPlannedActivityRawValue = selectedActivity.rawValue
        await model.loadLocalWindows(
            postcode: postcode,
            durationMinutes: selectedDurationMinutes
        )
    }

    private func comparisonValue(_ response: LocalWindowsResponse) -> String {
        guard response.plan.comparison?.status == .compatible else { return "Not comparable" }
        if response.plan.comparison?.isMeaningful == false { return "Similar" }
        guard let percent = response.plan.comparison?.percentLowerThanStartNow else { return "Compared" }
        return "−\(percent.formatted(.number.precision(.fractionLength(0))))%"
    }

    private func recommendationFact(_ value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(value)
                .font(.subheadline.weight(.semibold))
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textPrimary)
            Text(label)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func windowLabel(_ window: LocalChargingWindow) -> String {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = londonTimeZone
        let time = Date.FormatStyle(date: .omitted, time: .shortened, timeZone: londonTimeZone)
        if calendar.isDate(window.start, inSameDayAs: window.end) {
            let day = window.start.formatted(
                Date.FormatStyle(date: .abbreviated, time: .omitted, timeZone: londonTimeZone)
            )
            return "\(day) · \(window.start.formatted(time))–\(window.end.formatted(time))"
        }
        let full = Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: londonTimeZone)
        return "\(window.start.formatted(full))–\(window.end.formatted(full))"
    }

    private func regionalComparison(delta: Double, national: Double) -> String {
        let magnitude = abs(delta).formatted(.number.precision(.fractionLength(0)))
        if abs(delta) < 1 {
            return "Close to the GB forecast of \(national.formatted(.number.precision(.fractionLength(0)))) gCO₂/kWh."
        }
        return "\(magnitude) gCO₂/kWh \(delta < 0 ? "below" : "above") the GB forecast."
    }
}
