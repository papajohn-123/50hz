import SwiftUI

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
    @AppStorage("log.participation.days") private var participationDays = ""
    @State private var savedPredictions: [SavedPrediction] = []
    @State private var missionProgress: [MissionProgress] = []
    @State private var feedbackTrigger = 0
    @State private var currentLondonDate = LondonDay.localDateKey()

    private let predictionStore = PredictionJournalStore()
    private let missionStore = MissionProgressStore()

    var body: some View {
        TimelineView(.periodic(from: .now, by: 60)) { context in
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    header
                    gameState
                    predictionSection
                    missionSection
                    resultSection
                    learnedSection
                    notebookNote
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
        .sensoryFeedback(.selection, trigger: feedbackTrigger)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    headerTitle
                    Spacer(minLength: 8)
                    streakBadge
                    GlobalInfoButton()
                }
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .center) {
                        headerTitle
                        Spacer(minLength: 8)
                        GlobalInfoButton()
                    }
                    streakBadge
                }
            }
            Text("Make a call, inspect the evidence, then keep what you learned.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
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
            Text("START TODAY")
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.6)
                .foregroundStyle(GridTheme.liveCyan)
                .accessibilityLabel("Start today")
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
            VStack(alignment: .leading, spacing: 9) {
                SectionLabel("Prediction", trailing: "LOCAL CHOICE")
                Text(savedPredictionForToday == nil ? "No prediction is open" : "Today’s prediction is closed")
                    .font(.title3.weight(.medium))
                if let saved = savedPredictionForToday {
                    Text("Your \(saved.choice.displayName.lowercased()) choice is still saved on this device. The published result is unavailable right now.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                } else {
                    Text("A prediction appears only when the backend has a date-matched definition. 50Hz will not invent one while the feed is unavailable.")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
            }
            .padding(17)
            .background(GridTheme.surface.opacity(0.72), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
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
        return VStack(alignment: .leading, spacing: 15) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Label("PREDICTION", systemImage: "scope")
                    .font(.caption2.weight(.semibold))
                    .fontDesign(.monospaced)
                    .tracking(0.7)
                    .foregroundStyle(GridTheme.forecastViolet)
                Spacer(minLength: 4)
                Text(locked ? "LOCKED · \(ukTime(prediction.locksAt)) UK" : "LOCKS \(ukTime(prediction.locksAt)) UK")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
                    .multilineTextAlignment(.trailing)
            }

            Text(prediction.question)
                .font(.title3.weight(.medium))
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 10) {
                ForEach(prediction.choices.filter { $0 != .other }) { choice in
                    predictionButton(choice, prediction: prediction, saved: saved, locked: locked)
                }
            }

            if let saved {
                Text("Saved on this device: \(saved.choice.displayName). Your choice is never submitted.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            } else if !prediction.supportsLocalChoiceContract {
                Text("This prediction uses a newer or unsupported rule. Choices are disabled and 50Hz will not infer how to score it.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            } else if locked {
                Text("Choices closed at the backend’s published lock time. No local choice was saved.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }

            if prediction.state == .pending, now >= prediction.locksAt {
                Text("Awaiting the evidence window through \(ukTime(prediction.evidenceTo)) UK. Pending is not a result.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .padding(17)
        .background(GridTheme.forecastViolet.opacity(0.07), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: GridTheme.cornerRadius)
                .stroke(GridTheme.forecastViolet.opacity(0.20), lineWidth: 1)
        )
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

    private var missionSection: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel(
                "Missions",
                trailing: currentGame.map { "\(completedMissionCount)/\(min($0.missions.count, 3)) LOCAL" } ?? "UNAVAILABLE"
            )
            .padding(.bottom, 6)

            if let missions = currentGame?.missions.prefix(3), !missions.isEmpty {
                ForEach(Array(missions)) { mission in missionRow(mission) }
                Text("Open the linked context first. ‘Mark done’ is a local, unverified notebook checkmark.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .padding(.top, 7)
            } else {
                Text("Up to three date-matched missions will appear when the daily plan is available.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.vertical, 9)
            }
        }
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
                .accessibilityHint("Opens the evidence context; completion is recorded separately after you return")
            } else {
                missionLabel(mission, progress: progress, target: nil)
                    .accessibilityElement(children: .combine)
            }

            if let progress, !progress.isCompleted, mission.available {
                Button {
                    guard missionStore.markCompleted(mission, date: currentGame?.date ?? todayKey) else { return }
                    registerParticipation()
                    updateWithFeedback { missionProgress = missionStore.progress() }
                } label: {
                    Label("Mark done", systemImage: "checkmark.circle")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(GridTheme.liveCyan)
                        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                        .padding(.leading, 38)
                }
                .buttonStyle(.plain)
                .accessibilityHint("Records an unverified completion only on this device")
            } else if progress?.isCompleted == true {
                Label("Done · local and unverified", systemImage: "checkmark.circle.fill")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(GridTheme.liveCyan)
                    .frame(minHeight: 44, alignment: .leading)
                    .padding(.leading, 38)
                    .accessibilityLabel("Done, local and unverified")
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
        case .findCleanWindow: "Compare forecast windows in your Local view."
        case .identifyLargestSource: "Inspect the current observed supply ranking."
        case .inspectInterconnector: "Inspect the signed flows shown in Live."
        case .openEventEvidence: "Read the event claim, sources and limitations."
        case .other: "Inspect the linked grid context."
        }
    }

    @ViewBuilder
    private var resultSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Newest saved result", trailing: "LOCAL COMPARISON")
            if let saved = resultPrediction {
                if let resolution = matchingResolution(for: saved),
                   let result = LocalPredictionResult.derive(saved: saved, resolution: resolution) {
                    resultCard(saved: saved, resolution: resolution, result: result)
                } else {
                    unavailableResult(saved)
                }
            } else {
                Text("Save a prediction to begin a private result history. 50Hz compares it on this device only after evidence is published.")
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
        VStack(alignment: .leading, spacing: 13) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(resultTitle(result.status))
                    .font(.title2.weight(.semibold))
                    .foregroundStyle(resultColor(result.status))
                Spacer(minLength: 8)
                Text(saved.date)
                    .font(.caption)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }

            Text(resultSummary(saved: saved, resolution: resolution, status: result.status))
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if resolution.isCorrection {
                Label("Result revised from published evidence · revision \(resolution.resolutionRevision)", systemImage: "arrow.triangle.2.circlepath")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.staleAmber)
                    .fixedSize(horizontal: false, vertical: true)
            }

            if let value = resolution.observedValueMW {
                evidenceLine(
                    label: "Observed position",
                    value: "\(signedMegawatts(value)) signed net interconnector flow"
                )
            }
            if let observedAt = resolution.observedAt {
                evidenceLine(label: "Observed at", value: "\(ukDateTime(observedAt)) UK")
            }
            evidenceLine(
                label: "Coverage",
                value: "\(resolution.coverage.observedConnectorCount) of \(resolution.coverage.expectedConnectorCount) connectors · \(coveragePercent(resolution.coverage.coverageFraction))"
            )
            evidenceLine(
                label: "Evidence window",
                value: "\(ukTime(resolution.evidenceFrom))–\(ukTime(resolution.evidenceTo)) UK · target \(ukTime(resolution.targetAt))"
            )
            evidenceLine(
                label: "Evidence trail",
                value: evidenceTrail(resolution)
            )

            Text(resolution.reason)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .padding(.top, 2)
                .fixedSize(horizontal: false, vertical: true)

            if let error = model.predictionResolutionErrors[saved.date],
               model.predictionResolutionCacheDates.contains(saved.date) {
                Label("Showing the protected saved result; refresh unavailable. \(error)", systemImage: "wifi.slash")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text("Choice saved locally · published outcome from resolution schema \(resolution.schemaVersion)")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .padding(16)
        .background(resultColor(result.status).opacity(0.06), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(
            RoundedRectangle(cornerRadius: GridTheme.cornerRadius)
                .stroke(resultColor(result.status).opacity(0.18), lineWidth: 1)
        )
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
            Text("Your \(saved.choice.displayName.lowercased()) choice for \(saved.date) remains on this device. It has not been marked correct or incorrect.")
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
            if let error, !isLoading {
                Text(error)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .padding(15)
        .background(GridTheme.surface.opacity(0.72), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
    }

    @ViewBuilder
    private var learnedSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionLabel("Learned concepts", trailing: learnedNotes.isEmpty ? "NONE YET" : "LOCAL NOTES")
            if learnedNotes.isEmpty {
                Text("Complete a mission after visiting its evidence context. A short, deterministic concept note will stay here on this device.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            } else {
                ForEach(learnedNotes) { note in
                    HStack(alignment: .top, spacing: 12) {
                        Image(systemName: "lightbulb.min")
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(note.learnedNote ?? "")
                                .font(.subheadline)
                                .fixedSize(horizontal: false, vertical: true)
                            Text("Learned \(note.date) · local note")
                                .font(.caption2)
                                .foregroundStyle(GridTheme.textTertiary)
                        }
                    }
                    .padding(.vertical, 5)
                }
            }
        }
    }

    private var notebookNote: some View {
        VStack(alignment: .leading, spacing: 8) {
            Hairline()
            Text("Predictions, completions, streaks and learned notes stay on this device. Results come from published grid evidence; 50Hz does not submit your choice or verify mission completion.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 14) {
                Link("Privacy", destination: URL(string: "https://50hz-api-production.up.railway.app/privacy")!)
                Link("Support", destination: URL(string: "https://50hz-api-production.up.railway.app/support")!)
            }
            .font(.caption2.weight(.medium))
            .foregroundStyle(GridTheme.liveCyan)
            .frame(minHeight: 44)
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
        missionStore.markVisited(mission, date: date)
        missionProgress = missionStore.progress()

        switch target {
        case .live:
            model.selectedTab = .live
            if mission.kind == .identifyLargestSource {
                model.selectedFuel = model.snapshot?.generation.max(by: { $0.megawatts < $1.megawatts })?.fuel
            } else {
                model.selectedFuel = nil
            }
        case .today:
            model.selectedTab = .today
        case .local:
            model.selectedTab = .mine
        case .event(let eventID):
            model.selectedTab = .live
            model.selectedEvent = model.events.first { $0.id == eventID }
                ?? model.snapshot?.activeEvent.flatMap { $0.id == eventID ? $0 : nil }
            if model.selectedEvent == nil {
                Task {
                    if let event = try? await model.eventDetails(id: eventID) {
                        model.selectedEvent = event
                    }
                }
            }
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

    private func resultSummary(
        saved: SavedPrediction,
        resolution: PredictionResolution,
        status: LocalPredictionResultStatus
    ) -> String {
        switch status {
        case .correct:
            "You chose \(saved.choice.displayName.lowercased()); the published outcome was \(resolution.outcome?.rawValue ?? "unknown"). Correctness was calculated locally."
        case .incorrect:
            "You chose \(saved.choice.displayName.lowercased()); the published outcome was \(resolution.outcome?.rawValue ?? "unknown"). Correctness was calculated locally."
        case .void:
            "There is no winning choice. Your \(saved.choice.displayName.lowercased()) prediction remains in the notebook without a correct or incorrect mark."
        case .pending:
            "The evidence window has not closed. Your local choice is saved and has not been scored."
        case .unsupported:
            "The server returned a newer or unsupported result state. Your local choice is preserved and has not been scored."
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
