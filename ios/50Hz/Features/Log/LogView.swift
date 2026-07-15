import SwiftUI
import UIKit

private struct NotebookPredictionDefinition {
    let predictionID: String
    let date: String
    let question: String
    let choices: [GamePredictionChoice]
    let locksAt: Date
    let evidenceTo: Date
    let state: PredictionResolutionState?
    let supportsLocalChoiceContract: Bool

    init(_ prediction: GamePrediction, date: String) {
        predictionID = prediction.predictionID
        self.date = date
        question = prediction.question
        choices = prediction.choices
        locksAt = prediction.locksAt
        evidenceTo = prediction.resolvesTo
        state = nil
        supportsLocalChoiceContract = prediction.metric == "net_interconnector_flow_mw"
            && prediction.ruleVersion == 1
            && prediction.choices.count == 2
            && Set(prediction.choices) == Set([.importing, .exporting])
    }

    init(_ resolution: PredictionResolution) {
        predictionID = resolution.predictionID
        date = resolution.date
        question = resolution.question
        choices = resolution.choices
        locksAt = resolution.locksAt
        evidenceTo = resolution.evidenceTo
        state = resolution.state
        supportsLocalChoiceContract = resolution.supportsLocalChoiceContract
    }
}

struct LogView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.openURL) private var openURL
    @AppStorage("log.participation.days") private var participationDays = ""
    @State private var savedPredictions: [SavedPrediction] = []
    @State private var missionProgress: [MissionProgress] = []
    @State private var feedbackTrigger = 0
    @State private var currentLondonDate = LondonDay.localDateKey()
    @State private var isReminderExpanded = false
    @State private var isMoreEvidenceExpanded = false
    @State private var isResultEvidenceExpanded = false
    @State private var isHistoryExpanded = false
    @StateObject private var predictionReminder = PredictionReminderCoordinator()

    private let predictionStore = PredictionJournalStore()
    private let missionStore = MissionProgressStore()

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    header
                    gameState
                    predictionSection
                    nextEvidenceSection
                    resultSection
                    historySection
                }
                .padding(.horizontal, GridTheme.horizontalPadding)
                .padding(.top, 12)
                .padding(.bottom, 36)
            }
            .scrollIndicators(.hidden)
            .gridPageBackground()
            .onChange(of: LondonDay.localDateKey(at: context.date), initial: true) { _, localDate in
                currentLondonDate = localDate
            }
        }
        .onAppear(perform: loadLocalNotebook)
        .onChange(of: model.dailyGame?.date) { _, _ in loadLocalNotebook() }
        .task(id: currentLondonDate) { await model.refreshDailyGame() }
        .task(id: resolutionRequestSignature) {
            for date in resolutionRequestDates {
                guard !Task.isCancelled else { return }
                await loadResolutionAfterAnyCancelledRequestClears(date)
            }
        }
        .task(id: predictionReminderSignature) {
            guard let prediction = activePrediction else { return }
            await predictionReminder.refresh(
                predictionID: prediction.predictionID,
                date: prediction.date
            )
        }
        .sensoryFeedback(.selection, trigger: feedbackTrigger)
    }

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                headerTitle
                Spacer(minLength: 8)
                streakBadge
                GlobalInfoButton()
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack {
                    headerTitle
                    Spacer(minLength: 8)
                    GlobalInfoButton()
                }
                streakBadge
            }
        }
    }

    private var headerTitle: some View {
        Text("Notebook")
            .font(.system(.largeTitle, design: .rounded, weight: .medium))
            .tracking(-1.3)
            .accessibilityAddTraits(.isHeader)
    }

    @ViewBuilder
    private var streakBadge: some View {
        if currentStreak == 0 {
            Text("TODAY")
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.6)
                .foregroundStyle(GridTheme.liveCyan)
                .accessibilityLabel("No current streak")
        } else {
            VStack(alignment: .trailing, spacing: 1) {
                Text(currentStreak.formatted())
                    .font(.system(.title2, design: .monospaced, weight: .medium))
                    .foregroundStyle(GridTheme.liveCyan)
                Text("DAY STREAK")
                    .font(.system(size: 8, weight: .semibold, design: .monospaced))
                    .tracking(0.7)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(currentStreak) day streak")
        }
    }

    @ViewBuilder
    private var gameState: some View {
        if let error = model.gameRefreshError, currentGame != nil {
            notice(
                symbol: "clock.badge.exclamationmark",
                title: "Using the saved daily plan",
                detail: "Mission availability may have changed. \(error)",
                color: GridTheme.staleAmber,
                retry: { Task { await model.refreshDailyGame() } }
            )
        } else if currentGame == nil, activePrediction == nil {
            notice(
                symbol: model.gameLoadPhase == .loading ? "hourglass" : "book.closed",
                title: model.gameLoadPhase == .loading ? "Loading today’s notebook…" : "Today’s plan is unavailable",
                detail: model.gameRefreshError ?? "50Hz will keep your saved choices and notes on this device.",
                color: GridTheme.textTertiary,
                retry: model.gameLoadPhase == .loading ? nil : { Task { await model.refreshDailyGame() } }
            )
        }
    }

    private func notice(
        symbol: String,
        title: String,
        detail: String,
        color: Color,
        retry: (() -> Void)?
    ) -> some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: symbol)
                .foregroundStyle(color)
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 4)
            if let retry {
                Button("Retry", action: retry)
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(color)
                    .frame(minHeight: 44)
            }
        }
        .padding(13)
        .background(color.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder
    private var predictionSection: some View {
        if let prediction = activePrediction {
            TimelineView(.periodic(from: prediction.locksAt, by: 60)) { context in
                predictionCard(prediction, now: context.date)
            }
        } else {
            VStack(alignment: .leading, spacing: 7) {
                SectionLabel("Today’s call", trailing: savedPredictionForToday == nil ? "NO QUESTION" : "CLOSED")
                Text(savedPredictionForToday == nil ? "No call to make today" : "Your call is saved")
                    .font(.title3.weight(.medium))
                if let saved = savedPredictionForToday {
                    Text("\(saved.choice.displayName) · saved on this device")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
            }
            .padding(.leading, 14)
            .overlay(alignment: .leading) {
                Rectangle()
                    .fill(GridTheme.forecastViolet.opacity(0.7))
                    .frame(width: 2)
            }
        }
    }

    private func predictionCard(_ prediction: NotebookPredictionDefinition, now: Date) -> some View {
        let canSelect = PredictionInteractionPolicy.canSelect(
            now: now,
            locksAt: prediction.locksAt,
            state: prediction.state,
            supportsLocalChoiceContract: prediction.supportsLocalChoiceContract
        )
        let locked = !canSelect
        let saved = savedPredictions.first { $0.predictionID == prediction.predictionID && $0.date == prediction.date }
        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("TODAY’S CALL")
                    .font(.caption2.weight(.semibold))
                    .tracking(1)
                    .foregroundStyle(GridTheme.textTertiary)
                Spacer(minLength: 4)
                Text(locked ? "LOCKED · \(ukTime(prediction.locksAt)) UK" : "LOCKS \(ukTime(prediction.locksAt)) UK")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .multilineTextAlignment(.trailing)
            }

            Text(prediction.question)
                .font(.system(.title2, design: .rounded, weight: .medium))
                .tracking(-0.35)
                .fixedSize(horizontal: false, vertical: true)

            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) {
                    ForEach(prediction.choices.filter { $0 != .other }) { choice in
                        predictionButton(choice, prediction: prediction, saved: saved, locked: locked)
                    }
                }
                VStack(spacing: 10) {
                    ForEach(prediction.choices.filter { $0 != .other }) { choice in
                        predictionButton(choice, prediction: prediction, saved: saved, locked: locked)
                    }
                }
            }

            if let saved {
                Label("Your call: \(saved.choice.displayName)", systemImage: "checkmark.circle.fill")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.forecastViolet)
            } else if !prediction.supportsLocalChoiceContract {
                Text("This question cannot be scored by this version of 50Hz.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            } else if locked {
                Text("The call closed without a saved choice.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }

            if prediction.state == .pending, now >= prediction.locksAt {
                Text("Result after \(ukTime(prediction.evidenceTo)) UK")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }

            predictionReminderControls(prediction, saved: saved, now: now)
        }
        .padding(.leading, 14)
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(GridTheme.forecastViolet)
                .frame(width: 2)
        }
    }

    private func predictionButton(
        _ choice: GamePredictionChoice,
        prediction: NotebookPredictionDefinition,
        saved: SavedPrediction?,
        locked: Bool
    ) -> some View {
        let selected = saved?.choice == choice
        let symbol = choice == .importing ? "arrow.down.left" : "arrow.up.right"
        return Button {
            guard !locked,
                  PredictionInteractionPolicy.canSelect(
                    now: Date(),
                    locksAt: prediction.locksAt,
                    state: prediction.state,
                    supportsLocalChoiceContract: prediction.supportsLocalChoiceContract
                  ) else { return }
            predictionStore.save(
                predictionID: prediction.predictionID,
                date: prediction.date,
                choice: choice
            )
            registerParticipation()
            updateWithFeedback { savedPredictions = predictionStore.predictions() }
        } label: {
            HStack(spacing: 10) {
                Image(systemName: symbol)
                Text(choice.displayName)
                Spacer(minLength: 0)
                if selected { Image(systemName: "checkmark") }
            }
            .font(.subheadline.weight(.semibold))
            .padding(.horizontal, 14)
            .frame(maxWidth: .infinity, minHeight: 48)
            .background(selected ? GridTheme.forecastViolet : GridTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 11))
            .foregroundStyle(selected ? GridTheme.background : GridTheme.textSecondary)
            .overlay(
                RoundedRectangle(cornerRadius: 11)
                    .stroke(GridTheme.forecastViolet.opacity(selected ? 0 : 0.18), lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
        .disabled(locked)
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityHint(
            !prediction.supportsLocalChoiceContract
                ? "Unavailable because this prediction uses an unsupported rule"
                : locked
                    ? "The published lock time has passed"
                    : "Saves this choice only on this device"
        )
    }

    @ViewBuilder
    private func predictionReminderControls(
        _ prediction: NotebookPredictionDefinition,
        saved: SavedPrediction?,
        now: Date
    ) -> some View {
        let lockPlan = predictionReminderPlan(
            kind: .lock,
            prediction: prediction,
            saved: saved,
            now: now
        )
        let resultPlan = predictionReminderPlan(
            kind: .result,
            prediction: prediction,
            saved: saved,
            now: now
        )
        let hasStoredReminder = PredictionReminderKind.allCases.contains {
            predictionReminder.scheduled[$0] != nil
        }

        if lockPlan != nil || resultPlan != nil || hasStoredReminder {
            Hairline()
            DisclosureGroup(isExpanded: $isReminderExpanded) {
                VStack(alignment: .leading, spacing: 8) {
                    predictionReminderRow(
                        kind: .lock,
                        plan: lockPlan,
                        prediction: prediction
                    )
                    predictionReminderRow(
                        kind: .result,
                        plan: resultPlan,
                        prediction: prediction
                    )
                }
                .padding(.top, 8)
            } label: {
                Label(reminderLabel, systemImage: "bell")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }
            .tint(GridTheme.textTertiary)
        }
    }

    private var reminderLabel: String {
        let count = PredictionReminderKind.allCases.filter {
            predictionReminder.scheduled[$0] != nil
        }.count
        return count == 0 ? "Reminders" : "Reminders · \(count) set"
    }

    @ViewBuilder
    private func predictionReminderRow(
        kind: PredictionReminderKind,
        plan: PredictionReminderPlan?,
        prediction: NotebookPredictionDefinition
    ) -> some View {
        if let metadata = predictionReminder.scheduled[kind] {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: kind == .lock ? "bell.badge" : "bell.and.waves.left.and.right")
                    .foregroundStyle(GridTheme.forecastViolet)
                    .frame(width: 22)
                VStack(alignment: .leading, spacing: 2) {
                    Text(kind == .lock ? "Lock reminder set" : "Result-check reminder set")
                        .font(.caption.weight(.semibold))
                    Text("\(ukDateTime(metadata.fireDate)) UK")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                Spacer(minLength: 4)
                Button("Cancel") {
                    Task {
                        if await predictionReminder.cancel(kind: kind, date: prediction.date) {
                            feedbackTrigger += 1
                        }
                    }
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.textSecondary)
                .frame(minWidth: 44, minHeight: 44)
                .disabled(predictionReminder.workingKinds.contains(kind))
            }
            predictionReminderFeedback(kind)
        } else if let plan {
            Button {
                Task {
                    if await predictionReminder.schedule(plan) {
                        feedbackTrigger += 1
                    }
                }
            } label: {
                HStack(spacing: 9) {
                    if predictionReminder.workingKinds.contains(kind) {
                        ProgressView().tint(GridTheme.textPrimary)
                    } else {
                        Image(systemName: kind == .lock ? "bell" : "bell.and.waves.left.and.right")
                    }
                    Text(
                        kind == .lock
                            ? "Remind me 15 min before lock"
                            : "Remind me after evidence closes"
                    )
                    Spacer(minLength: 0)
                }
                .font(.caption.weight(.semibold))
                .foregroundStyle(GridTheme.textPrimary)
                .padding(.horizontal, 12)
                .frame(maxWidth: .infinity, minHeight: 44)
                .background(GridTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 10))
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .stroke(GridTheme.forecastViolet.opacity(0.18), lineWidth: 1)
                )
            }
            .buttonStyle(.plain)
            .disabled(predictionReminder.workingKinds.contains(kind))
            predictionReminderFeedback(kind)
        }
    }

    @ViewBuilder
    private func predictionReminderFeedback(_ kind: PredictionReminderKind) -> some View {
        switch predictionReminder.feedback[kind] ?? .none {
        case .denied:
            HStack(spacing: 10) {
                Text("Notifications are off for 50Hz.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
                Button("Open Settings") {
                    guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                    openURL(url)
                }
                .font(.caption2.weight(.semibold))
                .frame(minWidth: 44, minHeight: 44)
            }
        case .notAvailable:
            reminderFeedbackText("Reminders are not available on this device.")
        case .invalid:
            reminderFeedbackText("This reminder time is no longer valid.")
        case .past:
            reminderFeedbackText("That reminder time has already passed.")
        case .error:
            reminderFeedbackText("The reminder could not be changed.")
        case .cancelled:
            Text("Reminder cancelled.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textSecondary)
        case .scheduled, .none:
            EmptyView()
        }
    }

    private func reminderFeedbackText(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .foregroundStyle(GridTheme.staleAmber)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func predictionReminderPlan(
        kind: PredictionReminderKind,
        prediction: NotebookPredictionDefinition,
        saved: SavedPrediction?,
        now: Date
    ) -> PredictionReminderPlan? {
        guard prediction.supportsLocalChoiceContract,
              prediction.state != .resolved,
              prediction.state != .void else { return nil }

        let fireDate: Date
        switch kind {
        case .lock:
            fireDate = prediction.locksAt.addingTimeInterval(
                -PredictionReminderValidation.lockLeadTime
            )
        case .result:
            guard saved != nil else { return nil }
            fireDate = prediction.evidenceTo.addingTimeInterval(
                PredictionReminderValidation.resultDelay
            )
        }
        guard fireDate > now else { return nil }
        return PredictionReminderPlan(
            predictionID: prediction.predictionID,
            date: prediction.date,
            kind: kind,
            fireDate: fireDate,
            locksAt: prediction.locksAt,
            evidenceTo: prediction.evidenceTo
        )
    }

    @ViewBuilder
    private var nextEvidenceSection: some View {
        if !evidenceMissions.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                SectionLabel(
                    "Next evidence",
                    trailing: "\(completedMissionCount)/\(evidenceMissions.count) VISITED"
                )

                if let mission = nextEvidenceMission,
                   let target = MissionNavigationTarget.resolve(mission, events: model.events) {
                    primaryMissionAction(mission, target: target)
                } else {
                    Label(
                        completedMissionCount == evidenceMissions.count ? "You’re caught up" : "No evidence action available",
                        systemImage: completedMissionCount == evidenceMissions.count ? "checkmark.circle.fill" : "clock"
                    )
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(completedMissionCount == evidenceMissions.count ? GridTheme.liveCyan : GridTheme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
                }

                if !secondaryEvidenceMissions.isEmpty {
                    DisclosureGroup(isExpanded: $isMoreEvidenceExpanded) {
                        VStack(alignment: .leading, spacing: 0) {
                            ForEach(secondaryEvidenceMissions) { mission in
                                missionRow(mission)
                            }
                        }
                        .padding(.top, 2)
                    } label: {
                        Text("More evidence · \(secondaryEvidenceMissions.count)")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.textSecondary)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                    }
                    .tint(GridTheme.textTertiary)
                }
            }
        }
    }

    private func primaryMissionAction(
        _ mission: GameMission,
        target: MissionNavigationTarget
    ) -> some View {
        Button { openMission(mission, target: target) } label: {
            HStack(spacing: 13) {
                Image(systemName: missionSymbol(mission.kind))
                    .font(.body.weight(.medium))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(width: 26)
                Text(mission.title)
                    .font(.title3.weight(.medium))
                    .foregroundStyle(GridTheme.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 8)
                Image(systemName: "arrow.up.right")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.liveCyan)
            }
            .frame(maxWidth: .infinity, minHeight: 62, alignment: .leading)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) { Hairline() }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(mission.title). \(target.label)")
        .accessibilityHint("Opens the evidence and records this visit on this device")
    }

    @ViewBuilder
    private func missionRow(_ mission: GameMission) -> some View {
        let progress = progress(for: mission)
        let target = MissionNavigationTarget.resolve(mission, events: model.events)
        VStack(alignment: .leading, spacing: 0) {
            if let target, mission.available {
                Button { openMission(mission, target: target) } label: {
                    missionLabel(mission, progress: progress, target: target)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(mission.title). \(target.label)")
                .accessibilityHint("Opens the evidence context and records a private evidence visit")
            } else {
                missionLabel(mission, progress: progress, target: nil)
                    .accessibilityElement(children: .combine)
            }

            if let progress,
               !progress.isCompleted,
               mission.available,
               !MissionCompletionPolicy.completesOnEvidenceVisit(mission.kind) {
                Button {
                    guard missionStore.markCompleted(mission, date: currentGame?.date ?? todayKey) else { return }
                    registerParticipation()
                    updateWithFeedback { missionProgress = missionStore.progress() }
                } label: {
                    Label("Record takeaway", systemImage: "checkmark.circle")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .padding(.leading, 38)
                }
                .buttonStyle(.plain)
                .accessibilityHint("Stores this takeaway only on this device")
            } else if progress?.isCompleted == true {
                Label("Evidence visited · private", systemImage: "checkmark.circle.fill")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(minHeight: 44, alignment: .leading)
                    .padding(.leading, 38)
                    .accessibilityLabel("Evidence visited, stored privately")
            }
            Hairline()
        }
    }

    private func missionLabel(
        _ mission: GameMission,
        progress: MissionProgress?,
        target: MissionNavigationTarget?
    ) -> some View {
        HStack(alignment: .center, spacing: 13) {
            Image(systemName: progress?.isCompleted == true ? "checkmark.circle.fill" : missionSymbol(mission.kind))
                .frame(width: 25)
                .foregroundStyle(progress?.isCompleted == true ? GridTheme.liveCyan : mission.available ? GridTheme.textSecondary : GridTheme.textTertiary)
            VStack(alignment: .leading, spacing: 4) {
                Text(mission.title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(mission.available ? GridTheme.textPrimary : GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(missionSubtitle(mission, target: target))
                    .font(.caption2)
                    .foregroundStyle(mission.available ? GridTheme.textTertiary : GridTheme.staleAmber)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 6)
            if let target {
                VStack(alignment: .trailing, spacing: 3) {
                    Image(systemName: "arrow.up.right")
                    Text(target.label.replacingOccurrences(of: "Open ", with: ""))
                        .lineLimit(1)
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(GridTheme.liveCyan)
            } else if !mission.available {
                Image(systemName: "lock").font(.caption2).foregroundStyle(GridTheme.textTertiary)
            }
        }
        .frame(minHeight: 68)
        .contentShape(Rectangle())
    }

    private func missionSubtitle(_ mission: GameMission, target: MissionNavigationTarget?) -> String {
        if !mission.available { return mission.unavailableReason ?? "Unavailable in this daily plan" }
        if target == nil { return "No safe destination is defined for this mission." }
        return switch mission.kind {
        case .findCleanWindow: "Compare forecast windows in Plan."
        case .identifyLargestSource: "Inspect the current observed supply ranking."
        case .inspectInterconnector: "Inspect the signed flows shown in Live."
        case .openEventEvidence: "Read the event claim, sources and limitations."
        case .other: "Inspect the linked grid context."
        }
    }

    @ViewBuilder
    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Latest result", trailing: resultPrediction?.date)
            if let saved = resultPrediction {
                if let resolution = matchingResolution(for: saved),
                   let result = LocalPredictionResult.derive(saved: saved, resolution: resolution) {
                    resultCard(saved: saved, resolution: resolution, result: result)
                } else {
                    unavailableResult(saved)
                }
            } else {
                Text("Make today’s call to start your history.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.vertical, 7)
            }
        }
    }

    private func resultCard(
        saved: SavedPrediction,
        resolution: PredictionResolution,
        result: LocalPredictionResult
    ) -> some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .center, spacing: 10) {
                Text(resultTitle(result.status))
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(resultColor(result.status))
                Spacer(minLength: 8)
                Image(systemName: resultSymbol(result.status))
                    .font(.title3)
                    .foregroundStyle(resultColor(result.status))
                    .accessibilityHidden(true)
            }

            Text(resultSummary(saved: saved, resolution: resolution, status: result.status))
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if let value = resolution.observedValueMW {
                HStack(alignment: .firstTextBaseline, spacing: 7) {
                    Text(signedMegawatts(value))
                        .font(.system(.title3, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.textPrimary)
                    Text(value >= 0 ? "net import" : "net export")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                }
            }

            Hairline()
            DisclosureGroup(isExpanded: $isResultEvidenceExpanded) {
                VStack(alignment: .leading, spacing: 10) {
                    if resolution.isCorrection {
                        Label("Revised result · revision \(resolution.resolutionRevision)", systemImage: "arrow.triangle.2.circlepath")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.staleAmber)
                    }
                    if let observedAt = resolution.observedAt {
                        evidenceLine(label: "Observed", value: "\(ukDateTime(observedAt)) UK")
                    }
                    evidenceLine(
                        label: "Coverage",
                        value: "\(resolution.coverage.observedConnectorCount) of \(resolution.coverage.expectedConnectorCount) connectors · \(coveragePercent(resolution.coverage.coverageFraction))"
                    )
                    evidenceLine(
                        label: "Window",
                        value: "\(ukTime(resolution.evidenceFrom))–\(ukTime(resolution.evidenceTo)) UK · target \(ukTime(resolution.targetAt))"
                    )
                    evidenceLine(label: "Sources", value: evidenceTrail(resolution))
                    Text(resolution.reason)
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)

                    if let error = model.predictionResolutionErrors[saved.date],
                       model.predictionResolutionCacheDates.contains(saved.date) {
                        Label("Saved result shown; refresh unavailable. \(error)", systemImage: "wifi.slash")
                            .font(.caption2)
                            .foregroundStyle(GridTheme.staleAmber)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Text("Plan choice · published schema \(resolution.schemaVersion)")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .padding(.top, 8)
            } label: {
                Text("Evidence details")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.textSecondary)
                    .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
            }
            .tint(GridTheme.textTertiary)
        }
        .padding(.leading, 14)
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(resultColor(result.status))
                .frame(width: 2)
        }
        .accessibilityElement(children: .contain)
    }

    private func unavailableResult(_ saved: SavedPrediction) -> some View {
        let isLoading = model.predictionResolutionLoadingDates.contains(saved.date)
        let error = model.predictionResolutionErrors[saved.date]
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 9) {
                if isLoading { ProgressView().tint(GridTheme.liveCyan) }
                Image(systemName: isLoading ? "clock" : "wifi.slash")
                    .foregroundStyle(GridTheme.textTertiary)
                Text(isLoading ? "Checking the published result…" : "Result unavailable")
                    .font(.subheadline.weight(.semibold))
            }
            Text("\(saved.choice.displayName) · \(saved.date)")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
            if let error, !isLoading {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .padding(.leading, 14)
        .overlay(alignment: .leading) {
            Rectangle()
                .fill(GridTheme.textTertiary)
                .frame(width: 2)
        }
    }

    private var historySection: some View {
        VStack(alignment: .leading, spacing: 0) {
            Hairline()
            DisclosureGroup(isExpanded: $isHistoryExpanded) {
                VStack(alignment: .leading, spacing: 18) {
                    if savedPredictions.isEmpty {
                        Text("No saved calls yet.")
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    } else {
                        VStack(alignment: .leading, spacing: 0) {
                            Text("RECENT CALLS")
                                .font(.caption2.weight(.semibold))
                                .tracking(0.8)
                                .foregroundStyle(GridTheme.textTertiary)
                                .padding(.bottom, 5)
                            ForEach(savedPredictions.prefix(5)) { prediction in
                                HStack(spacing: 10) {
                                    Text(prediction.date)
                                        .font(.caption.monospacedDigit())
                                        .foregroundStyle(GridTheme.textTertiary)
                                    Spacer(minLength: 8)
                                    Text(prediction.choice.displayName)
                                        .font(.subheadline.weight(.medium))
                                        .foregroundStyle(GridTheme.textPrimary)
                                }
                                .frame(minHeight: 42)
                                .overlay(alignment: .bottom) { Hairline() }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }

                    if !learnedNotes.isEmpty {
                        VStack(alignment: .leading, spacing: 9) {
                            Text("TAKEAWAYS")
                                .font(.caption2.weight(.semibold))
                                .tracking(0.8)
                                .foregroundStyle(GridTheme.textTertiary)
                            ForEach(learnedNotes.prefix(3)) { note in
                                HStack(alignment: .top, spacing: 10) {
                                    Image(systemName: "lightbulb.min")
                                        .foregroundStyle(GridTheme.liveCyan)
                                        .frame(width: 20)
                                    Text(note.learnedNote ?? "")
                                        .font(.caption)
                                        .foregroundStyle(GridTheme.textSecondary)
                                        .fixedSize(horizontal: false, vertical: true)
                                }
                                .accessibilityElement(children: .combine)
                            }
                        }
                    }

                    HStack(spacing: 14) {
                        Text("Saved on this device")
                        Spacer(minLength: 8)
                        Link("Privacy", destination: URL(string: "https://50hz-api-production.up.railway.app/privacy")!)
                        Link("Support", destination: URL(string: "https://50hz-api-production.up.railway.app/support")!)
                    }
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .frame(minHeight: 44)
                }
                .padding(.top, 10)
            } label: {
                HStack(spacing: 8) {
                    Text("History & takeaways")
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(GridTheme.textPrimary)
                    Spacer(minLength: 8)
                    Text("\(savedPredictions.count) CALLS · \(learnedNotes.count) NOTES")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(GridTheme.textTertiary)
                }
                .frame(maxWidth: .infinity, minHeight: 52, alignment: .leading)
            }
            .tint(GridTheme.textTertiary)
        }
    }

    private var currentGame: DailyGame? {
        guard model.dailyGame?.date == todayKey else { return nil }
        return model.dailyGame
    }

    private var activePrediction: NotebookPredictionDefinition? {
        if let resolution = model.predictionResolutions[todayKey],
           resolution.matches(PredictionResolutionRequest(localDate: todayKey)) {
            return NotebookPredictionDefinition(resolution)
        }
        if let game = currentGame, let prediction = game.prediction {
            return NotebookPredictionDefinition(prediction, date: game.date)
        }
        return nil
    }

    private var savedPredictionForToday: SavedPrediction? {
        savedPredictions.first { $0.date == todayKey }
    }

    private var resultPrediction: SavedPrediction? {
        let terminal = savedPredictions.first { saved in
            guard let resolution = matchingResolution(for: saved) else { return false }
            return resolution.state == .resolved || resolution.state == .void
        }
        return terminal ?? savedPredictions.first
    }

    private func matchingResolution(for saved: SavedPrediction) -> PredictionResolution? {
        guard let resolution = model.predictionResolutions[saved.date],
              resolution.predictionID == saved.predictionID,
              resolution.matches(PredictionResolutionRequest(localDate: saved.date)) else { return nil }
        return resolution
    }

    private var resolutionRequestDates: [String] {
        var dates: [String] = [todayKey]
        for date in predictionStore.predictions().map(\.date) where !dates.contains(date) {
            dates.append(date)
            if dates.count == 4 { break }
        }
        return dates.filter(LondonDay.isValidLocalDateKey)
    }

    private var resolutionRequestSignature: String { resolutionRequestDates.joined(separator: "|") }

    private var predictionReminderSignature: String {
        guard let prediction = activePrediction else { return "none" }
        return "\(prediction.predictionID)|\(prediction.date)"
    }

    private var evidenceMissions: [GameMission] {
        Array(currentGame?.missions.prefix(3) ?? [])
    }

    private var nextEvidenceMission: GameMission? {
        evidenceMissions.first { mission in
            progress(for: mission)?.isCompleted != true
                && mission.available
                && MissionNavigationTarget.resolve(mission, events: model.events) != nil
        }
    }

    private var secondaryEvidenceMissions: [GameMission] {
        guard let nextEvidenceMission else { return evidenceMissions }
        return evidenceMissions.filter { $0.missionID != nextEvidenceMission.missionID }
    }

    private var completedMissionCount: Int {
        guard let game = currentGame else { return 0 }
        let IDs = Set(missionProgress.filter { $0.date == game.date && $0.isCompleted }.map(\.missionID))
        return game.missions.prefix(3).filter { IDs.contains($0.missionID) }.count
    }

    private func progress(for mission: GameMission) -> MissionProgress? {
        guard let date = currentGame?.date else { return nil }
        return missionProgress.first { $0.date == date && $0.missionID == mission.missionID }
    }

    private var learnedNotes: [MissionProgress] {
        var seen = Set<String>()
        return missionProgress.filter { progress in
            guard progress.isCompleted, let note = progress.learnedNote, !note.isEmpty else { return false }
            return seen.insert(note).inserted
        }
        .prefix(6)
        .map { $0 }
    }

    private func openMission(_ mission: GameMission, target: MissionNavigationTarget) {
        guard let date = currentGame?.date else { return }

        switch target {
        case .live:
            model.selectedTab = .live
            if mission.kind == .identifyLargestSource {
                model.selectedFuel = model.snapshot?.generation.max(by: { $0.megawatts < $1.megawatts })?.fuel
            } else {
                model.selectedFuel = nil
            }
            recordEvidenceVisit(mission, date: date)
        case .local:
            model.selectedTab = .mine
            recordEvidenceVisit(mission, date: date)
        case .event(let eventID):
            model.selectedTab = .live
            if let event = (
                model.events.first(where: { $0.id == eventID })
                    ?? model.snapshot?.activeEvent.flatMap { $0.id == eventID ? $0 : nil }
            ) {
                model.selectedEvent = event
                recordEvidenceVisit(mission, date: date)
            } else {
                Task {
                    if let event = try? await model.eventDetails(id: eventID) {
                        model.selectedEvent = event
                        recordEvidenceVisit(mission, date: date)
                    }
                }
            }
        }
    }

    private func recordEvidenceVisit(_ mission: GameMission, date: String) {
        let didComplete = missionStore.recordEvidenceVisit(mission, date: date)
        missionProgress = missionStore.progress()
        if didComplete {
            registerParticipation()
            feedbackTrigger += 1
        }
    }

    private func loadLocalNotebook() {
        predictionStore.migrateLegacyPredictionIfNeeded()
        savedPredictions = predictionStore.predictions()
        missionProgress = missionStore.progress()
    }

    private func registerParticipation() {
        var days = Set(participationDays.split(separator: ",").map(String.init))
        days.insert(todayKey)
        participationDays = days.sorted().suffix(90).joined(separator: ",")
    }

    private var todayKey: String { currentLondonDate }

    private func loadResolutionAfterAnyCancelledRequestClears(_ date: String) async {
        while model.predictionResolutionLoadingDates.contains(date) {
            do {
                try await Task.sleep(for: .milliseconds(50))
            } catch {
                return
            }
        }
        guard !Task.isCancelled else { return }
        await model.loadPredictionResolution(localDate: date)
    }

    private var currentStreak: Int {
        let participated = Set(participationDays.split(separator: ",").map(String.init))
        guard !participated.isEmpty else { return 0 }
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = LondonDay.timeZone
        var cursor = Date()
        if !participated.contains(LondonDay.localDateKey(at: cursor)) {
            guard let yesterday = calendar.date(byAdding: .day, value: -1, to: cursor),
                  participated.contains(LondonDay.localDateKey(at: yesterday)) else { return 0 }
            cursor = yesterday
        }
        var count = 0
        while participated.contains(LondonDay.localDateKey(at: cursor)) {
            count += 1
            guard let prior = calendar.date(byAdding: .day, value: -1, to: cursor) else { break }
            cursor = prior
        }
        return count
    }

    private func updateWithFeedback(_ updates: () -> Void) {
        if reduceMotion {
            updates()
        } else {
            withAnimation(.snappy(duration: 0.2), updates)
        }
        feedbackTrigger += 1
    }

    private func missionSymbol(_ kind: GameMissionKind) -> String {
        switch kind {
        case .findCleanWindow: "leaf"
        case .identifyLargestSource: "bolt"
        case .inspectInterconnector: "arrow.left.arrow.right"
        case .openEventEvidence: "doc.text.magnifyingglass"
        case .other: "scope"
        }
    }

    private func resultTitle(_ status: LocalPredictionResultStatus) -> String {
        switch status {
        case .correct: "Correct"
        case .incorrect: "Incorrect"
        case .void: "Void"
        case .pending: "Pending"
        case .unsupported: "Result unsupported"
        }
    }

    private func resultColor(_ status: LocalPredictionResultStatus) -> Color {
        switch status {
        case .correct: GridTheme.liveCyan
        case .incorrect: GridTheme.staleAmber
        case .void, .pending, .unsupported: GridTheme.textSecondary
        }
    }

    private func resultSymbol(_ status: LocalPredictionResultStatus) -> String {
        switch status {
        case .correct: "checkmark.circle.fill"
        case .incorrect: "xmark.circle.fill"
        case .void: "minus.circle"
        case .pending: "clock"
        case .unsupported: "questionmark.circle"
        }
    }

    private func resultSummary(
        saved: SavedPrediction,
        resolution: PredictionResolution,
        status: LocalPredictionResultStatus
    ) -> String {
        switch status {
        case .correct:
            "You called \(saved.choice.displayName.lowercased()). The published outcome was \(resolution.outcome?.rawValue ?? "unknown")."
        case .incorrect:
            "You called \(saved.choice.displayName.lowercased()). The published outcome was \(resolution.outcome?.rawValue ?? "unknown")."
        case .void:
            "No winning outcome was published for this call."
        case .pending:
            "The evidence window is still open."
        case .unsupported:
            "This result cannot be scored by this version of 50Hz."
        }
    }

    private func evidenceLine(label: String, value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.system(size: 9, weight: .semibold, design: .monospaced))
                .tracking(0.5)
                .foregroundStyle(GridTheme.textTertiary)
            Text(value)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func evidenceTrail(_ resolution: PredictionResolution) -> String {
        let publisher = resolution.sourceIDs.isEmpty ? "No source IDs" : resolution.sourceIDs.prefix(2).joined(separator: ", ")
        return "\(publisher) · \(resolution.sourceRecordIDs.count) records · \(resolution.sourceRevisionKeys.count) revision keys · rule \(resolution.ruleVersion) / registry \(resolution.connectorRegistryVersion)"
    }

    private func signedMegawatts(_ value: Double) -> String {
        let formatted = abs(value).formatted(.number.precision(.fractionLength(value.rounded() == value ? 0 : 1)))
        if value > 0 { return "+\(formatted) MW" }
        if value < 0 { return "−\(formatted) MW" }
        return "0 MW"
    }

    private func coveragePercent(_ value: Double) -> String {
        value.formatted(.percent.precision(.fractionLength(0)))
    }

    private func ukTime(_ date: Date) -> String {
        date.formatted(Date.FormatStyle(date: .omitted, time: .shortened, timeZone: LondonDay.timeZone))
    }

    private func ukDateTime(_ date: Date) -> String {
        date.formatted(Date.FormatStyle(date: .abbreviated, time: .shortened, timeZone: LondonDay.timeZone))
    }
}
