import SwiftUI

private struct GridMission: Identifiable {
    let id: String
    let title: String
    let detail: String
    let symbol: String
}

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
    @AppStorage("log.prediction") private var predictionChoice = ""
    @AppStorage("log.mission.clean") private var cleanMissionDone = false
    @AppStorage("log.mission.connector") private var connectorMissionDone = false
    @AppStorage("log.mission.evidence") private var evidenceMissionDone = false
    @AppStorage("log.prediction.date") private var predictionDate = ""
    @AppStorage("log.mission.date") private var missionDate = ""
    @AppStorage("log.participation.days") private var participationDays = ""

    private let missions = [
        GridMission(id: "clean", title: "Find today’s cleanest half-hour", detail: "Open the forecast moment on the Live timeline.", symbol: "leaf"),
        GridMission(id: "connector", title: "Inspect the largest interconnector", detail: "Learn which direction electricity is moving.", symbol: "arrow.left.arrow.right"),
        GridMission(id: "evidence", title: "Read an evidence source", detail: "Separate a reported fact from interpretation.", symbol: "doc.text.magnifyingglass")
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                header
                prediction
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
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Field notebook")
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
            }
            Text("Observe. Predict. Learn how Britain stays balanced.")
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
        }
    }

    private var prediction: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Label("TODAY’S PREDICTION", systemImage: "scope")
                    .font(.caption2.weight(.semibold))
                    .fontDesign(.monospaced)
                    .tracking(0.7)
                    .foregroundStyle(GridTheme.forecastViolet)
                Spacer()
                Text(predictionLocked ? "LOCKED" : "LOCKS 17:45")
                    .font(.caption2)
                    .fontDesign(.monospaced)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Text("Will Britain be importing or exporting at 18:00?")
                .font(.title3.weight(.medium))
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                predictionButton("Importing", symbol: "arrow.down.left")
                predictionButton("Exporting", symbol: "arrow.up.right")
            }
            if !predictionChoice.isEmpty {
                Text("Prediction saved on this device. The result is void if source coverage is insufficient.")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
        .padding(17)
        .background(GridTheme.forecastViolet.opacity(0.07), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
        .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.20), lineWidth: 1))
    }

    private func predictionButton(_ label: String, symbol: String) -> some View {
        let selected = predictionChoice == label
        return Button {
            guard !predictionLocked else { return }
            withAnimation(.snappy(duration: 0.22)) {
                predictionChoice = label
                predictionDate = todayKey
                registerParticipation()
            }
        } label: {
            Label(label, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 46)
                .background(selected ? GridTheme.forecastViolet : GridTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 11))
                .foregroundStyle(selected ? GridTheme.background : GridTheme.textSecondary)
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(GridTheme.forecastViolet.opacity(selected ? 0 : 0.18), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .disabled(predictionLocked)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private var missionList: some View {
        VStack(alignment: .leading, spacing: 2) {
            SectionLabel("Daily missions", trailing: "\(completedMissionCount)/3")
                .padding(.bottom, 8)
            missionRow(missions[0], isDone: $cleanMissionDone)
            missionRow(missions[1], isDone: $connectorMissionDone)
            missionRow(missions[2], isDone: $evidenceMissionDone)
        }
    }

    private var completedMissionCount: Int {
        [cleanMissionDone, connectorMissionDone, evidenceMissionDone].filter { $0 }.count
    }

    private func missionRow(_ mission: GridMission, isDone: Binding<Bool>) -> some View {
        Button {
            withAnimation(.snappy(duration: 0.2)) {
                isDone.wrappedValue.toggle()
                missionDate = todayKey
                registerParticipation()
            }
        } label: {
            HStack(spacing: 13) {
                Image(systemName: isDone.wrappedValue ? "checkmark.circle.fill" : mission.symbol)
                    .frame(width: 25)
                    .foregroundStyle(isDone.wrappedValue ? GridTheme.liveCyan : GridTheme.textSecondary)
                VStack(alignment: .leading, spacing: 3) {
                    Text(mission.title)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(isDone.wrappedValue ? GridTheme.textSecondary : GridTheme.textPrimary)
                    Text(mission.detail)
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            .frame(minHeight: 66)
            .contentShape(Rectangle())
            .overlay(alignment: .bottom) { Hairline() }
        }
        .buttonStyle(.plain)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(isDone.wrappedValue ? .isSelected : [])
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
            Text("Your notebook is stored locally. Streaks reward taking part and learning—not only correct predictions.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var britishCalendar: Calendar {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: "Europe/London") ?? .current
        return calendar
    }

    private var todayKey: String { dayKey(Date()) }

    private var predictionLocked: Bool {
        let components = britishCalendar.dateComponents([.year, .month, .day], from: Date())
        guard let day = britishCalendar.date(from: components),
              let lock = britishCalendar.date(bySettingHour: 17, minute: 45, second: 0, of: day) else { return false }
        return Date() >= lock
    }

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

    private func normalizeDailyState() {
        if predictionDate != todayKey {
            predictionChoice = ""
            predictionDate = todayKey
        }
        if missionDate != todayKey {
            cleanMissionDone = false
            connectorMissionDone = false
            evidenceMissionDone = false
            missionDate = todayKey
        }
    }
}
