import SwiftUI

private struct GridMission: Identifiable {
    let id: String
    let title: String
    let detail: String
    let symbol: String
}

struct LogView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("log.prediction") private var predictionChoice = ""
    @AppStorage("log.mission.clean") private var cleanMissionDone = false
    @AppStorage("log.mission.connector") private var connectorMissionDone = false
    @AppStorage("log.mission.evidence") private var evidenceMissionDone = false

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
                    Text("4")
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
                Text("LOCKS 17:30")
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
            withAnimation(.snappy(duration: 0.22)) { predictionChoice = label }
        } label: {
            Label(label, systemImage: symbol)
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity, minHeight: 46)
                .background(selected ? GridTheme.forecastViolet : GridTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 11))
                .foregroundStyle(selected ? GridTheme.background : GridTheme.textSecondary)
                .overlay(RoundedRectangle(cornerRadius: 11).stroke(GridTheme.forecastViolet.opacity(selected ? 0 : 0.18), lineWidth: 1))
        }
        .buttonStyle(.plain)
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
            withAnimation(.snappy(duration: 0.2)) { isDone.wrappedValue.toggle() }
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
            SectionLabel("Moments observed", trailing: "3 saved")
            momentRow(symbol: "wind", color: GridTheme.fuel(.wind), title: "Wind took the lead", time: "09:20", evidence: "Observed")
            momentRow(symbol: "arrow.left.arrow.right", color: GridTheme.fuel(.imports), title: "Britain began exporting", time: "11:40", evidence: "Observed")
            momentRow(symbol: "sun.max", color: GridTheme.fuel(.solar), title: "Solar reached 6.3 GW", time: "13:10", evidence: "Estimated")
        }
    }

    private func momentRow(symbol: String, color: Color, title: String, time: String, evidence: String) -> some View {
        HStack(spacing: 13) {
            Image(systemName: symbol)
                .font(.subheadline)
                .foregroundStyle(color)
                .frame(width: 34, height: 34)
                .background(color.opacity(0.09), in: Circle())
            VStack(alignment: .leading, spacing: 3) {
                Text(title).font(.subheadline.weight(.medium))
                Text(evidence).font(.caption2).foregroundStyle(GridTheme.textTertiary)
            }
            Spacer()
            Text(time)
                .font(.caption)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
        }
        .frame(minHeight: 48)
    }

    private var notebookNote: some View {
        VStack(alignment: .leading, spacing: 7) {
            Hairline()
            Text("Your notebook is stored locally. Streaks reward taking part and learning—not only correct predictions.")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }
}
