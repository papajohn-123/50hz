import SwiftUI
import UIKit

enum RegionalGridCopy {
    static func methodology(source: SourceReference?) -> String {
        let provenance = source.map {
            " Source: \($0.name), \($0.dataset), forecast period \($0.observedAt.formatted(.dateTime.hour().minute())), captured \($0.retrievedAt.formatted(.dateTime.hour().minute()))."
        } ?? ""
        return "The current half-hour carbon value is a regional forecast. The flexible-use planner is separate and uses Britain’s national carbon forecast. The national supply mix shown elsewhere is not a regional electricity supply mix.\(provenance)"
    }
}

struct MineView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL
    @AppStorage("mine.postcode") private var postcode = ""
    @AppStorage("mine.activity") private var activityRawValue = LocalActivityPreset.laundry.rawValue
    @AppStorage("mine.customDurationMinutes") private var customDurationMinutes = 120
    @AppStorage("mine.lastPlannedActivity") private var lastPlannedActivityRawValue = LocalActivityPreset.laundry.rawValue
    @AppStorage("mine.reminderActivity") private var reminderActivityRawValue = ""
    @StateObject private var reminder = LocalReminderCoordinator()
    @State private var draftPostcode = ""
    @State private var postcodeInputError: String?
    @FocusState private var postcodeFocused: Bool

    private let londonTimeZone = TimeZone(identifier: "Europe/London") ?? .current

    private var selectedActivity: LocalActivityPreset {
        LocalActivityPreset(rawValue: activityRawValue) ?? .laundry
    }

    private var selectedDurationMinutes: Int {
        selectedActivity.durationMinutes(customDurationMinutes: customDurationMinutes)
    }

    private var selectedRequest: LocalWindowsRequest {
        LocalWindowsRequest(postcode: postcode, durationMinutes: selectedDurationMinutes)
    }

    private var lastPlannedActivity: LocalActivityPreset {
        LocalActivityPreset(rawValue: lastPlannedActivityRawValue) ?? .laundry
    }

    private var plannerSelectionHasChanges: Bool {
        model.localWindowsRequest != selectedRequest || lastPlannedActivity != selectedActivity
    }

    private var nationalCarbon: Double {
        model.regionalContext?.nationalCarbonIntensity ?? model.snapshot?.carbonIntensity.value ?? 0
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 27) {
                header
                postcodeControl
                flexibleUsePlanner
                regionalContext
                methodology
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 30)
        }
        .scrollDismissesKeyboard(.interactively)
        .scrollIndicators(.hidden)
        .gridPageBackground()
        .onAppear {
            draftPostcode = PostcodePrivacy.outwardCode(from: postcode)
        }
        .task(id: postcode) {
            await model.loadRegion(postcode: postcode)
        }
        .task {
            guard model.localWindowsRequest == nil else { return }
            lastPlannedActivityRawValue = selectedActivity.rawValue
            await model.loadLocalWindows(
                postcode: postcode,
                durationMinutes: selectedDurationMinutes
            )
        }
        .task(id: reminderPlan) {
            await reminder.refresh(for: reminderPlan)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("Local")
                    .font(.system(.largeTitle, design: .rounded, weight: .medium))
                    .tracking(-1.2)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                GlobalInfoButton()
            }
            Label {
                Text(regionLabel)
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
            } icon: {
                Image(systemName: "location.circle.fill")
                    .foregroundStyle(GridTheme.liveCyan)
            }
            Text(postcode.isEmpty ? "Default region — not detected" : "Saved region — not detected")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var regionLabel: String {
        if let context = model.regionalContext {
            return "\(context.name) · \(PostcodePrivacy.outwardCode(from: context.postcode))"
        }
        let outward = PostcodePrivacy.outwardCode(from: postcode)
        return postcode.isEmpty ? "Central London · \(outward)" : outward
    }

    private var postcodeControl: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Your postcode")
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 9) {
                    postcodeField
                    postcodeButton
                }
                VStack(alignment: .leading, spacing: 9) {
                    postcodeField
                    postcodeButton
                }
            }
            Text("Stored on this device. Only the outward code is sent. Location permission is not used; the planner itself uses a GB national forecast.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            if let postcodeInputError {
                Text(postcodeInputError)
                    .font(.caption)
                    .foregroundStyle(GridTheme.staleAmber)
            }
        }
    }

    private var postcodeField: some View {
        TextField("Outward postcode", text: $draftPostcode)
            .textInputAutocapitalization(.characters)
            .autocorrectionDisabled()
            .focused($postcodeFocused)
            .submitLabel(.done)
            .onSubmit(savePostcode)
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 11))
            .overlay(RoundedRectangle(cornerRadius: 11).stroke(GridTheme.hairline, lineWidth: 1))
            .accessibilityLabel("Outward postcode")
            .onChange(of: draftPostcode) { _, _ in
                postcodeInputError = nil
            }
    }

    private var postcodeButton: some View {
        Button(action: savePostcode) {
            Text("Use postcode")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(GridTheme.background)
                .padding(.horizontal, 16)
                .frame(maxWidth: .infinity, minHeight: 48)
                .background(GridTheme.liveCyan, in: RoundedRectangle(cornerRadius: 11))
                .contentShape(Rectangle())
        }
    }

    private func savePostcode() {
        guard let outward = PostcodePrivacy.validatedOutwardCode(from: draftPostcode) else {
            postcodeInputError = GridAPIError.invalidPostcode.localizedDescription
            return
        }
        postcode = outward
        draftPostcode = outward
        postcodeInputError = nil
        postcodeFocused = false
    }

    private var flexibleUsePlanner: some View {
        VStack(alignment: .leading, spacing: 15) {
            SectionLabel("Plan flexible use", trailing: LocalPlannerCopy.durationLabel(minutes: selectedDurationMinutes))
            activityPicker
            if selectedActivity == .custom {
                customDurationControl
            }
            Text(LocalPlannerCopy.support)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
            plannerButton

            if let error = model.localWindowsError {
                LocalWindowStateBanner(
                    message: error,
                    hasSavedPlan: model.localWindows != nil
                ) {
                    requestPlan()
                }
            } else if model.localWindowsIsFromCache {
                Label("Saved plan · checking for a forecast update", systemImage: "arrow.clockwise")
                    .font(.caption)
                    .foregroundStyle(GridTheme.staleAmber)
            }

            plannerResult
            reminderManagement
        }
    }

    private var activityPicker: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("ACTIVITY")
                .font(.caption2.weight(.semibold))
                .tracking(1)
                .foregroundStyle(GridTheme.textTertiary)
            Menu {
                ForEach(LocalActivityPreset.allCases) { activity in
                    Button {
                        activityRawValue = activity.rawValue
                    } label: {
                        Label(activityMenuLabel(activity), systemImage: activity.systemImage)
                    }
                }
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: selectedActivity.systemImage)
                        .frame(width: 24)
                        .foregroundStyle(GridTheme.liveCyan)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(selectedActivity.title)
                            .font(.body.weight(.medium))
                            .foregroundStyle(GridTheme.textPrimary)
                        Text(activityDurationLabel(selectedActivity))
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    }
                    Spacer(minLength: 8)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .padding(.horizontal, 14)
                .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 12))
                .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.hairline, lineWidth: 1))
            }
            .accessibilityLabel("Activity")
            .accessibilityValue("\(selectedActivity.title), \(LocalPlannerCopy.spokenDuration(minutes: selectedDurationMinutes))")
        }
    }

    private func activityMenuLabel(_ activity: LocalActivityPreset) -> String {
        guard let duration = activity.presetDurationMinutes else { return "Custom duration" }
        return "\(activity.title) · \(LocalPlannerCopy.durationLabel(minutes: duration))"
    }

    private func activityDurationLabel(_ activity: LocalActivityPreset) -> String {
        activity == .custom
            ? "30 min–12 hr · 30-minute steps"
            : "\(LocalPlannerCopy.durationLabel(minutes: selectedDurationMinutes)) continuous"
    }

    private var customDurationControl: some View {
        Stepper(value: $customDurationMinutes, in: 30...720, step: 30) {
            ViewThatFits(in: .horizontal) {
                HStack {
                    Text("Duration")
                    Spacer()
                    Text(LocalPlannerCopy.durationLabel(minutes: customDurationMinutes))
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.liveCyan)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text("Duration")
                    Text(LocalPlannerCopy.durationLabel(minutes: customDurationMinutes))
                        .fontDesign(.monospaced)
                        .foregroundStyle(GridTheme.liveCyan)
                }
            }
        }
        .padding(.leading, 14)
        .frame(minHeight: 50)
        .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 12))
        .accessibilityLabel("Duration")
        .accessibilityValue(LocalPlannerCopy.spokenDuration(minutes: customDurationMinutes))
        .accessibilityHint("Adjusts in 30-minute steps from 30 minutes to 12 hours")
    }

    private var plannerButton: some View {
        Button(action: requestPlan) {
            HStack(spacing: 9) {
                if model.isRefreshingLocalWindows {
                    ProgressView()
                        .tint(GridTheme.background)
                } else {
                    Image(systemName: "clock.badge.checkmark")
                }
                Text(plannerButtonTitle)
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(GridTheme.background)
            .frame(maxWidth: .infinity, minHeight: 50)
            .background(GridTheme.liveCyan, in: RoundedRectangle(cornerRadius: 12))
        }
        .disabled(model.isRefreshingLocalWindows)
        .opacity(model.isRefreshingLocalWindows ? 0.75 : 1)
        .accessibilityLabel(model.isRefreshingLocalWindows ? "Checking the GB forecast" : plannerButtonTitle)
    }

    private var plannerButtonTitle: String {
        if model.localWindowsRequest == nil { return "Find best time" }
        if plannerSelectionHasChanges { return "Update best time" }
        return "Refresh forecast"
    }

    private func requestPlan() {
        lastPlannedActivityRawValue = selectedActivity.rawValue
        Task {
            await model.loadLocalWindows(
                postcode: postcode,
                durationMinutes: selectedDurationMinutes
            )
        }
    }

    @ViewBuilder
    private var plannerResult: some View {
        if let response = model.localWindows {
            if !response.hasSafeNationalForecastScope {
                unsafeScopePlannerState
            } else if LocalPlannerCopy.isTooOldToRecommend(response) {
                stalePlannerState(response)
            } else if response.plan.status == .insufficientCoverage
                        || response.plan.recommendedWindow == nil {
                unavailablePlannerState(response)
            } else if let window = response.plan.recommendedWindow {
                recommendedWindow(response, window: window)
            }
        } else if model.localWindowsError == nil {
            switch model.localWindowsLoadPhase {
            case .loading:
                LocalPlannerPlaceholder(
                    systemImage: "clock.arrow.circlepath",
                    title: "Checking the GB forecast",
                    message: "Looking for a complete continuous window for this duration."
                )
            case .failed:
                LocalPlannerPlaceholder(
                    systemImage: "wifi.exclamationmark",
                    title: "No current window",
                    message: "The planner needs a current national forecast. Try again when the grid service is available."
                )
            case .loaded:
                LocalPlannerPlaceholder(
                    systemImage: "clock.badge.questionmark",
                    title: "No current window",
                    message: "Choose an activity and check the national forecast."
                )
            }
        }
    }

    private func recommendedWindow(
        _ response: LocalWindowsResponse,
        window: LocalChargingWindow
    ) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            VStack(alignment: .leading, spacing: 5) {
                SectionLabel("Best GB window")
                Text(LocalPlannerCopy.nationalScope.uppercased())
                    .font(.caption2.weight(.semibold))
                    .tracking(0.6)
                    .foregroundStyle(GridTheme.forecastViolet)
                Text(LocalPlannerCopy.resultTitle(for: response))
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(GridTheme.textPrimary)
                Text(windowLabel(window))
                    .font(.system(.title2, design: .monospaced, weight: .medium))
                    .foregroundStyle(GridTheme.forecastViolet)
                    .fixedSize(horizontal: false, vertical: true)
                Text("\(lastPlannedActivity.title) · \(LocalPlannerCopy.durationLabel(minutes: response.plan.requestedDurationMinutes)) continuous")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }

            Hairline()

            LazyVGrid(
                columns: [GridItem(.adaptive(minimum: 135), spacing: 14, alignment: .leading)],
                alignment: .leading,
                spacing: 12
            ) {
                plannerFact(
                    value: LocalPlannerCopy.intensity(window.averageIntensityGCO2KWh),
                    label: "Average forecast intensity"
                )
                plannerFact(
                    value: capturedLabel(response.forecast.capturedAt),
                    label: "Forecast capture"
                )
            }

            if let comparison = LocalPlannerCopy.comparisonSummary(
                response.plan.comparison,
                recommended: window
            ) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("START NOW")
                        .font(.caption2.weight(.semibold))
                        .tracking(0.8)
                        .foregroundStyle(GridTheme.textTertiary)
                    Text(comparison)
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
            }

            coverageDetails(response.plan.coverage)
        }
        .padding(16)
        .background(GridTheme.forecastViolet.opacity(0.075), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.2), lineWidth: 1))
    }

    private var reminderPlan: LocalReminderPlan? {
        guard let response = model.localWindows,
              response.hasSafeNationalForecastScope,
              !LocalPlannerCopy.isTooOldToRecommend(response),
              let window = response.plan.recommendedWindow,
              let capturedAt = response.forecast.capturedAt,
              let outward = PostcodePrivacy.validatedOutwardCode(
                from: response.postcode,
                defaultWhenEmpty: false
              )
        else { return nil }

        return LocalReminderPlan(
            localIdentifier: LocalReminderCoordinator.localIdentifier,
            activityLabel: lastPlannedActivity.title,
            outwardRegion: outward,
            scope: .gbNational,
            forecastCapturedAt: capturedAt,
            start: window.start,
            end: window.end,
            averageIntensityGCO2KWh: window.averageIntensityGCO2KWh
        )
    }

    @ViewBuilder
    private var reminderManagement: some View {
        if let scheduled = reminder.scheduledMetadata {
            VStack(alignment: .leading, spacing: 12) {
                Label {
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Reminder set")
                            .font(.subheadline.weight(.semibold))
                        Text(reminderDateLabel(scheduled.start))
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    }
                } icon: {
                    Image(systemName: "bell.badge.fill")
                        .foregroundStyle(GridTheme.forecastViolet)
                }

                if reminderNeedsUpdate, let plan = reminderPlan {
                    Text("The activity or forecast window has changed. Your existing reminder has not moved.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                    reminderActionButton(
                        title: "Update reminder",
                        systemImage: "bell.and.waves.left.and.right",
                        primary: true
                    ) {
                        scheduleReminder(plan)
                    }
                }

                Button {
                    Task {
                        if await reminder.cancel() {
                            reminderActivityRawValue = ""
                        }
                    }
                } label: {
                    Label("Cancel reminder", systemImage: "bell.slash")
                        .font(.subheadline.weight(.semibold))
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(.plain)
                .foregroundStyle(GridTheme.textSecondary)
                .disabled(reminder.isWorking)

                reminderFeedback
            }
            .padding(14)
            .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.hairline, lineWidth: 1))
        } else if let plan = reminderPlan {
            VStack(alignment: .leading, spacing: 10) {
                Text("Keep the window")
                    .font(.subheadline.weight(.semibold))
                Text("50Hz can remind you at the forecast start time. The reminder stays on this device and will not move unless you update it.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                reminderActionButton(
                    title: "Remind me at the start",
                    systemImage: "bell",
                    primary: true
                ) {
                    scheduleReminder(plan)
                }
                reminderFeedback
            }
            .padding(14)
            .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.hairline, lineWidth: 1))
        } else if reminder.feedback == .cancelled {
            Text("Reminder cancelled.")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
        }
    }

    private var reminderNeedsUpdate: Bool {
        let activityChanged = !reminderActivityRawValue.isEmpty
            && reminderActivityRawValue != lastPlannedActivityRawValue
        return reminder.hasMaterialChange || activityChanged
    }

    private func reminderActionButton(
        title: String,
        systemImage: String,
        primary: Bool,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if reminder.isWorking {
                    ProgressView()
                        .tint(primary ? GridTheme.background : GridTheme.textPrimary)
                } else {
                    Image(systemName: systemImage)
                }
                Text(title)
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(primary ? GridTheme.background : GridTheme.textPrimary)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(
                primary ? GridTheme.forecastViolet : GridTheme.surfaceRaised,
                in: RoundedRectangle(cornerRadius: 11)
            )
        }
        .buttonStyle(.plain)
        .disabled(reminder.isWorking)
        .opacity(reminder.isWorking ? 0.72 : 1)
    }

    @ViewBuilder
    private var reminderFeedback: some View {
        switch reminder.feedback {
        case .denied:
            VStack(alignment: .leading, spacing: 7) {
                Text("Notifications are off for 50Hz. The forecast plan is still available here.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.staleAmber)
                Button("Open Settings") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    openURL(url)
                }
                .font(.caption.weight(.semibold))
                .frame(minWidth: 44, minHeight: 44, alignment: .leading)
            }
        case .notAvailable:
            Text("Reminders are not available on this device.")
                .font(.caption)
                .foregroundStyle(GridTheme.staleAmber)
        case .invalid:
            Text("This forecast window cannot be scheduled. Refresh the forecast and try again.")
                .font(.caption)
                .foregroundStyle(GridTheme.staleAmber)
        case .past:
            Text("That window has already started. Refresh to find the next available time.")
                .font(.caption)
                .foregroundStyle(GridTheme.staleAmber)
        case .error:
            Text("The reminder could not be changed. Nothing was rescheduled.")
                .font(.caption)
                .foregroundStyle(GridTheme.staleAmber)
        case .scheduled, .cancelled, .none:
            EmptyView()
        }
    }

    private func scheduleReminder(_ plan: LocalReminderPlan) {
        Task {
            if await reminder.schedule(plan) {
                reminderActivityRawValue = lastPlannedActivityRawValue
            }
        }
    }

    private func reminderDateLabel(_ date: Date) -> String {
        date.formatted(
            Date.FormatStyle(
                date: .abbreviated,
                time: .shortened,
                timeZone: londonTimeZone
            )
        )
    }

    private func plannerFact(value: String, label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value)
                .font(.subheadline.weight(.medium))
                .fontDesign(.monospaced)
                .fixedSize(horizontal: false, vertical: true)
            Text(label)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func coverageDetails(_ coverage: LocalForecastCoverage) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(LocalPlannerCopy.coverageSummary(coverage))
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            if !coverage.gapStarts.isEmpty {
                Text(gapSummary(coverage.gapStarts))
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            } else if coverage.expectedIntervalCount > 0 {
                Text("No gaps in the evaluated forecast intervals.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
    }

    private func unavailablePlannerState(_ response: LocalWindowsResponse) -> some View {
        LocalPlannerPlaceholder(
            systemImage: "rectangle.and.text.magnifyingglass",
            title: LocalPlannerCopy.resultTitle(for: response),
            message: "The forecast does not contain a complete continuous window for this duration. \(LocalPlannerCopy.coverageSummary(response.plan.coverage)). \(gapSummary(response.plan.coverage.gapStarts))"
        )
    }

    private func stalePlannerState(_ response: LocalWindowsResponse) -> some View {
        LocalPlannerPlaceholder(
            systemImage: "clock.badge.exclamationmark",
            title: "Saved forecast is out of date",
            message: "50Hz is withholding the old recommendation. \(capturedLabel(response.forecast.capturedAt)). Refresh when the grid service is available.",
            color: GridTheme.staleAmber
        )
    }

    private var unsafeScopePlannerState: some View {
        LocalPlannerPlaceholder(
            systemImage: "exclamationmark.shield",
            title: "Recommendation withheld",
            message: "The response was not confirmed as a continuous GB national forecast.",
            color: GridTheme.staleAmber
        )
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

    private func capturedLabel(_ capturedAt: Date?) -> String {
        guard let capturedAt else { return "Capture time unavailable" }
        let formatter = Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: londonTimeZone)
        return "Captured \(capturedAt.formatted(formatter))"
    }

    private func gapSummary(_ gapStarts: [Date]) -> String {
        guard !gapStarts.isEmpty else { return "No forecast gaps reported." }
        let formatter = Date.FormatStyle(date: .omitted, time: .shortened, timeZone: londonTimeZone)
        let shown = gapStarts.prefix(3).map { $0.formatted(formatter) }.joined(separator: ", ")
        let remainder = gapStarts.count - min(gapStarts.count, 3)
        return remainder > 0
            ? "Forecast gaps at \(shown), plus \(remainder) more."
            : "Forecast gaps at \(shown)."
    }

    @ViewBuilder
    private var regionalContext: some View {
        VStack(alignment: .leading, spacing: 24) {
            if let error = model.regionError {
                RegionalStateBanner(message: error, hasCachedValue: model.regionalContext != nil) {
                    Task { await model.loadRegion(postcode: postcode) }
                }
            }
            if let context = model.regionalContext {
                regionalReading(context)
                comparison(context)
            } else {
                regionalPlaceholder
            }
        }
    }

    private func regionalReading(_ context: RegionalGridContext) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            SectionLabel("Regional forecast now")
            Text(context.regionalIsDelayed == true ? "\(context.name.uppercased()) · DELAYED" : context.name.uppercased())
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(context.regionalIsDelayed == true ? GridTheme.staleAmber : GridTheme.textTertiary)
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    regionalValue(context)
                    Text("gCO₂/kWh")
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                VStack(alignment: .leading, spacing: 2) {
                    regionalValue(context)
                    Text("gCO₂/kWh")
                        .font(.subheadline)
                        .foregroundStyle(GridTheme.textSecondary)
                }
            }
            Text(context.rating)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textPrimary)
            if context.regionalIsDelayed == true, let periodEnd = context.regionalPeriodEnd {
                Text("Latest available regional period ended \(periodEnd.formatted(.dateTime.hour().minute())).")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Regional carbon forecast for the current half-hour, \(Int(context.carbonIntensity.rounded())) grams of carbon dioxide per kilowatt hour, \(context.rating)")
    }

    private func regionalValue(_ context: RegionalGridContext) -> some View {
        Text(Int(context.carbonIntensity.rounded()).formatted())
            .font(.system(.largeTitle, design: .rounded, weight: .light))
            .tracking(-1.2)
            .foregroundStyle(GridTheme.liveCyan)
            .monospacedDigit()
    }

    private func comparison(_ context: RegionalGridContext) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            SectionLabel("Against Britain")
            comparisonBar(label: context.name, value: context.carbonIntensity, maximum: comparisonMaximum(context), color: GridTheme.liveCyan)
            comparisonBar(label: "Great Britain", value: nationalCarbon, maximum: comparisonMaximum(context), color: GridTheme.textSecondary)
            Text(comparisonCopy(context))
                .font(.caption)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private func comparisonMaximum(_ context: RegionalGridContext) -> Double {
        max(context.carbonIntensity, context.nationalCarbonIntensity, 100) * 1.2
    }

    private func comparisonCopy(_ context: RegionalGridContext) -> String {
        let national = max(context.nationalCarbonIntensity, 1)
        let difference = Int((abs(context.carbonIntensity - national) / national * 100).rounded())
        if context.carbonIntensity < national {
            return "\(context.name) is approximately \(difference)% lower carbon than the national forecast for this half-hour."
        }
        if context.carbonIntensity > national {
            return "\(context.name) is approximately \(difference)% more carbon-intensive than the national forecast for this half-hour."
        }
        return "The regional and national forecasts are aligned for this half-hour."
    }

    private func comparisonBar(label: String, value: Double, maximum: Double, color: Color) -> some View {
        VStack(spacing: 6) {
            ViewThatFits(in: .horizontal) {
                HStack {
                    Text(label).font(.caption)
                    Spacer()
                    Text("\(Int(value)) g/kWh").font(.caption).fontDesign(.monospaced)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(.caption)
                    Text("\(Int(value)) g/kWh").font(.caption).fontDesign(.monospaced)
                }
            }
            GeometryReader { proxy in
                Capsule().fill(GridTheme.surfaceRaised)
                Capsule().fill(color).frame(width: proxy.size.width * min(value / maximum, 1))
            }
            .frame(height: 5)
        }
    }

    private var methodology: some View {
        VStack(alignment: .leading, spacing: 8) {
            Hairline()
            Text(LocalPlannerCopy.methodology)
            Text(RegionalGridCopy.methodology(source: model.regionalContext?.source))
        }
        .font(.caption2)
        .foregroundStyle(GridTheme.textTertiary)
    }

    private var regionalPlaceholder: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Regional forecast now")
            if case .failed(let message) = model.regionLoadPhase {
                Text(message)
                    .font(.subheadline)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(4)
            } else {
                ProgressView("Resolving the regional forecast…")
                    .tint(GridTheme.liveCyan)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 110, alignment: .leading)
    }
}

private struct LocalWindowStateBanner: View {
    let message: String
    let hasSavedPlan: Bool
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "clock.badge.exclamationmark")
                .foregroundStyle(GridTheme.staleAmber)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(hasSavedPlan ? "Showing the last forecast plan" : "Planner unavailable")
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(4)
            }
            Spacer(minLength: 4)
            Button("Retry", action: retry)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.staleAmber)
                .frame(minWidth: 44, minHeight: 44)
        }
        .padding(12)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }
}

private struct LocalPlannerPlaceholder: View {
    let systemImage: String
    let title: String
    let message: String
    var color: Color = GridTheme.textSecondary

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: systemImage)
                .font(.title3)
                .foregroundStyle(color)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 88, alignment: .topLeading)
        .padding(14)
        .background(GridTheme.surface, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.hairline, lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

private struct RegionalStateBanner: View {
    let message: String
    let hasCachedValue: Bool
    let retry: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "location.slash")
                .foregroundStyle(GridTheme.staleAmber)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(hasCachedValue ? "Showing the last regional forecast" : "Regional data unavailable")
                    .font(.subheadline.weight(.semibold))
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .lineLimit(4)
            }
            Spacer(minLength: 4)
            Button("Retry", action: retry)
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.staleAmber)
                .frame(minWidth: 44, minHeight: 44)
        }
        .padding(12)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }
}
