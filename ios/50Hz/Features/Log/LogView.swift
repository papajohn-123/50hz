import SwiftUI

private struct ObservedGridMoment: Identifiable {
    let id: String
    let symbol: String
    let color: Color
    let title: String
    let time: Date
    let evidence: String
}

struct LogView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @AppStorage("log.prediction") private var predictionChoice = ""
    @AppStorage("log.prediction.id") private var predictionID = ""
    @AppStorage("log.prediction.date") private var predictionDate = ""
    @AppStorage("log.mission.completions") private var missionCompletions = ""
    @AppStorage("log.participation.days") private var participationDays = ""

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                header
                gameState
                predictionSection
                missionList
                observedMoments
                notebookNote
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .gridPageBackground()
        .onAppear(perform: normalizeDailyState)
        .onChange(of: model.dailyGame?.prediction?.predictionID) { _, _ in
            normalizeDailyState()
        }
        .task { await model.refreshDailyGame() }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Notebook")
                    .font(.system(.largeTitle, design: .rounded, weight: .medium))
                    .tracking(-1.3)
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Text(currentStreak.formatted())
                        .font(.system(.title2, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.liveCyan)
                    Text("DAY STREAK")
                        .font(.system(size: 8, weight: .semibold, design: .monospaced))
                        .tracking(0.7)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                GlobalInfoButton()
            }
            Text("Observe. Predict. Learn how Britain’s grid changes.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
        }
    }

    @ViewBuilder
    private var gameState: some View {
        if let error = model.gameRefreshError, currentGame != nil {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "clock.badge.exclamationmark")
                    .foregroundStyle(GridTheme.staleAmber)
                VStack(alignment: .leading, spacing: 3) {
                    Text("Using today’s cached game plan")
                        .font(.subheadline.weight(.semibold))
                    Text("Mission availability may have changed. \(error)")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textSecondary)
                }
                Spacer(minLength: 4)
                Button("Retry") { Task { await model.refreshDailyGame() } }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(GridTheme.staleAmber)
                    .frame(minHeight: 44)
            }
            .padding(12)
            .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
        } else if currentGame == nil {
            HStack(alignment: .top, spacing: 10) {
                if model.gameLoadPhase == .loading {
                    ProgressView().tint(GridTheme.liveCyan)
                } else {
                    Image(systemName: "gamecontroller")
                        .foregroundStyle(GridTheme.staleAmber)
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.gameLoadPhase == .loading ? "Loading today’s game plan…" : "Today’s game plan is unavailable")
                        .font(.subheadline.weight(.semibold))
                    if let cachedDate = model.dailyGame?.date, cachedDate != todayKey {
                        Text("The cached plan is from \(cachedDate), so 50Hz is not presenting it as today’s plan.")
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    } else if let error = model.gameRefreshError {
                        Text(error)
                            .font(.caption)
                            .foregroundStyle(GridTheme.textSecondary)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(12)
            .background(GridTheme.surface.opacity(0.7), in: RoundedRectangle(cornerRadius: 12))
        }
    }

    @ViewBuilder
    private var predictionSection: some View {
        if let prediction = currentGame?.prediction {
            predictionCard(prediction)
        } else {
            predictionUnavailable
        }
    }

    private func predictionCard(_ prediction: GamePrediction) -> some View {
        let locked = Date() >= prediction.locksAt
        return VStack(alignment: .leading, spacing: 15) {
            HStack {
                Label("TODAY’S PREDICTION", systemImage: "scope")
                    .font(.caption2.weight(.semibold))
                    .fontDesign(.monospaced)
                    .tracking(0.7)
                    .foregroundStyle(GridTheme.forecastViolet)
                Spacer()
                Text(locked ? "LOCKED" : "LOCKS \(prediction.locksAt.formatted(.dateTime.hour().minute()))")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Text(prediction.question)
                .font(.title3.weight(.medium))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                ForEach(prediction.choices.filter { $0 != .other }) { choice in
                    predictionButton(choice, prediction: prediction, locked: locked)
                }
            }
            if !predictionChoice.isEmpty, predictionDate == todayKey {
                Text("Saved only on this device. 50Hz does not submit or score this prediction on the server.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .padding(17)
        .background(GridTheme.forecastViolet.opacity(0.07), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.20), lineWidth: 1))
    }

    private var predictionUnavailable: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("TODAY’S PREDICTION", systemImage: "scope")
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.7)
                .foregroundStyle(GridTheme.textTertiary)
            if !predictionChoice.isEmpty, predictionDate == todayKey {
                Text("Today’s prediction is closed")
                    .font(.title3.weight(.medium))
                Text("Your local choice, \(predictionChoice.capitalized), remains on this device. 50Hz does not submit or score it on the server.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            } else {
                Text("No prediction is currently open")
                    .font(.title3.weight(.medium))
                Text(currentGame?.sourceFresh == false
                    ? "The backend withheld today’s prediction because its source-freshness requirement was not met."
                    : "The daily plan may be loading, unavailable, or past its backend-defined lock time.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
            }
        }
        .padding(17)
        .background(GridTheme.surface.opacity(0.7), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
    }

    private func predictionButton(
        _ choice: GamePredictionChoice,
        prediction: GamePrediction,
        locked: Bool
    ) -> some View {
        let selected = predictionChoice == choice.rawValue && predictionID == prediction.predictionID
        let symbol = choice == .importing ? "arrow.down.left" : "arrow.up.right"
        return Button {
            guard !locked else { return }
            animateIfAllowed(duration: 0.22) {
                predictionChoice = choice.rawValue
                predictionID = prediction.predictionID
                predictionDate = currentGame?.date ?? todayKey
                registerParticipation()
            }
        } label: {
            Label(choice.displayName, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 46)
                .background(selected ? GridTheme.forecastViolet : GridTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 11))
                .foregroundStyle(selected ? GridTheme.background : GridTheme.textSecondary)
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(GridTheme.forecastViolet.opacity(selected ? 0 : 0.18), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(locked)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var missionList: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel(
                "Daily missions",
                trailing: currentGame.map { "\(completedMissionCount)/\($0.missions.count) LOCAL" } ?? "UNAVAILABLE"
            )
                .padding(.bottom, 8)
            if let missions = currentGame?.missions, !missions.isEmpty {
                ForEach(missions) { mission in
                    missionRow(mission)
                }
                Text("Mission completion is a local notebook checkmark; it is not verified by the backend.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
                    .padding(.top, 8)
            } else {
                Text("Mission titles and availability will appear only when today’s backend plan is available.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.vertical, 10)
            }
        }
    }

    private var completedMissionCount: Int {
        guard let missions = currentGame?.missions else { return 0 }
        return missions.filter { completedMissionIDs.contains($0.missionID) }.count
    }

    private func missionRow(_ mission: GameMission) -> some View {
        let isDone = completedMissionIDs.contains(mission.missionID)
        return Button {
            guard mission.available else { return }
            animateIfAllowed(duration: 0.2) {
                toggleMission(mission.missionID)
                registerParticipation()
            }
        } label: {
            HStack(spacing: 13) {
                Image(systemName: isDone ? "checkmark.circle.fill" : missionSymbol(mission.kind))
                    .frame(width: 25)
                    .foregroundStyle(isDone ? GridTheme.liveCyan : (mission.available ? GridTheme.textSecondary : GridTheme.textTertiary))
                VStack(alignment: .leading, spacing: 3) {
                    Text(mission.title)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(isDone || !mission.available ? GridTheme.textSecondary : GridTheme.textPrimary)
                    Text(missionDetail(mission))
                        .font(.caption2)
                        .foregroundStyle(mission.available ? GridTheme.textTertiary : GridTheme.staleAmber)
                }
                Spacer(minLength: 0)
                Image(systemName: mission.available ? "chevron.right" : "lock")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            .frame(minHeight: 66)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) { Hairline() }
        }
        .buttonStyle(.plain)
        .disabled(!mission.available)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isDone ? .isSelected : [])
    }

    private func missionDetail(_ mission: GameMission) -> String {
        if !mission.available {
            return mission.unavailableReason ?? "Unavailable in the current daily plan"
        }
        return switch mission.kind {
        case .findCleanWindow: "Use Today or the Live timeline, then mark this locally."
        case .identifyLargestSource: "Inspect the current supply ranking, then mark this locally."
        case .inspectInterconnector: "Inspect the current interconnector direction, then mark this locally."
        case .openEventEvidence: "Open a reported event’s evidence, then mark this locally."
        case .other: "Explore the requested grid state, then mark this locally."
        }
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

    private var observedMoments: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Moments in view", trailing: observedMomentData.isEmpty ? "NONE" : "\(observedMomentData.count) DATA-BACKED")
            if observedMomentData.isEmpty {
                Text("No source-backed grid moments are available in the current snapshot or timeline. 50Hz will not invent notebook entries.")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .padding(.vertical, 8)
            } else {
                ForEach(observedMomentData) { moment in
                    momentRow(moment)
                }
            }
        }
    }

    private func momentRow(_ moment: ObservedGridMoment) -> some View {
        HStack(spacing: 13) {
            Image(systemName: moment.symbol)
                .font(.subheadline)
                .foregroundStyle(moment.color)
                .frame(width: 34, height: 34)
                .background(moment.color.opacity(0.09), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(moment.title).font(.subheadline.weight(.medium))
                Text(moment.evidence).font(.caption2).foregroundStyle(GridTheme.textTertiary)
            }
            Spacer()
            Text(moment.time.formatted(.dateTime.hour().minute()))
                .font(.caption)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .frame(minHeight: 48)
    }

    private var observedMomentData: [ObservedGridMoment] {
        var moments: [ObservedGridMoment] = []

        let reportedEvents = model.events.isEmpty
            ? model.snapshot?.activeEvent.map { [$0] } ?? []
            : model.events
        moments.append(contentsOf: reportedEvents.prefix(2).map { event in
            ObservedGridMoment(
                id: "event-\(event.id)",
                symbol: "exclamationmark.triangle",
                color: GridTheme.warning,
                title: event.title,
                time: event.startedAt,
                evidence: event.evidenceClass.capitalized
            )
        })

        if let snapshot = model.snapshot,
           let leader = snapshot.generation.max(by: { $0.megawatts < $1.megawatts }) {
            moments.append(
                ObservedGridMoment(
                    id: "leader-\(snapshot.timestamp.timeIntervalSince1970)-\(leader.fuel.rawValue)",
                    symbol: leader.fuel == .wind ? "wind" : "bolt",
                    color: GridTheme.fuel(leader.fuel),
                    title: "\(leader.fuel.displayName) leads at \((leader.megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))) GW",
                    time: snapshot.timestamp,
                    evidence: leader.factClass.rawValue.capitalized
                )
            )
        }

        if let snapshot = model.snapshot, !snapshot.interconnectors.isEmpty {
            let net = snapshot.interconnectors.reduce(0) { $0 + $1.megawatts }
            let direction = net > 0 ? "Net imports" : (net < 0 ? "Net exports" : "Net interchange")
            moments.append(
                ObservedGridMoment(
                    id: "interconnectors-\(snapshot.timestamp.timeIntervalSince1970)",
                    symbol: "arrow.left.arrow.right",
                    color: GridTheme.fuel(.imports),
                    title: "\(direction) at \(abs(Int(net.rounded())).formatted()) MW",
                    time: snapshot.timestamp,
                    evidence: "Observed flows"
                )
            )
        }

        if let solarHigh = model.timeline?.samples
            .filter({ $0.factClass != .forecast })
            .compactMap({ sample -> (GridTimelineSample, FuelReading)? in
                guard let solar = sample.generation.first(where: { $0.fuel == .solar }), solar.megawatts > 0 else { return nil }
                return (sample, solar)
            })
            .max(by: { $0.1.megawatts < $1.1.megawatts }) {
            moments.append(
                ObservedGridMoment(
                    id: "solar-high-\(solarHigh.0.timestamp.timeIntervalSince1970)",
                    symbol: "sun.max",
                    color: GridTheme.fuel(.solar),
                    title: "Solar timeline high: \((solarHigh.1.megawatts / 1_000).formatted(.number.precision(.fractionLength(1)))) GW",
                    time: solarHigh.0.timestamp,
                    evidence: solarHigh.1.factClass.rawValue.capitalized
                )
            )
        }

        return Array(moments.prefix(5))
    }

    private var notebookNote: some View {
        VStack(alignment: .leading, spacing: 7) {
            Hairline()
            Text("Completions, predictions and streaks stay on this device. They are participation notes, not server-verified results or scores.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
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

    private var completedMissionIDs: Set<String> {
        guard let data = missionCompletions.data(using: .utf8),
              let identifiers = try? JSONDecoder().decode([String].self, from: data) else {
            return Set(missionCompletions.split(separator: ",").map(String.init))
        }
        return Set(identifiers)
    }

    private var britishCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London") ?? .current
        return calendar
    }

    private var todayKey: String { dayKey(Date()) }

    private var currentStreak: Int {
        let participated = Set(participationDays.split(separator: ",").map(String.init))
        guard !participated.isEmpty else { return 0 }
        var cursor = Date()
        if !participated.contains(dayKey(cursor)) {
            guard let yesterday = britishCalendar.date(byAdding: .day, value: -1, to: cursor),
                  participated.contains(dayKey(yesterday)) else { return 0 }
            cursor = yesterday
        }

        var count = 0
        while participated.contains(dayKey(cursor)) {
            count += 1
            guard let previous = britishCalendar.date(byAdding: .day, value: -1, to: cursor) else { break }
            cursor = previous
        }
        return count
    }

    private func dayKey(_ date: Date) -> String {
        let components = britishCalendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", components.year ?? 0, components.month ?? 0, components.day ?? 0)
    }

    private func registerParticipation() {
        var days = Set(participationDays.split(separator: ",").map(String.init))
        days.insert(todayKey)
        participationDays = days.sorted().suffix(90).joined(separator: ",")
    }

    private func toggleMission(_ missionID: String) {
        var completed = completedMissionIDs
        if completed.contains(missionID) {
            completed.remove(missionID)
        } else {
            completed.insert(missionID)
        }
        let retained = Array(completed.sorted().suffix(120))
        if let data = try? JSONEncoder().encode(retained),
           let encoded = String(data: data, encoding: .utf8) {
            missionCompletions = encoded
        }
    }

    private func normalizeDailyState() {
        if predictionDate != todayKey {
            predictionChoice = ""
            predictionID = ""
            predictionDate = todayKey
        }
        if predictionChoice == "Importing" { predictionChoice = GamePredictionChoice.importing.rawValue }
        if predictionChoice == "Exporting" { predictionChoice = GamePredictionChoice.exporting.rawValue }
        if let activeID = currentGame?.prediction?.predictionID,
           !predictionID.isEmpty,
           predictionID != activeID {
            predictionChoice = ""
            predictionID = ""
        }
    }

    private func animateIfAllowed(duration: Double, updates: () -> Void) {
        if reduceMotion {
            updates()
        } else {
            withAnimation(.snappy(duration: duration), updates)
        }
    }
}
