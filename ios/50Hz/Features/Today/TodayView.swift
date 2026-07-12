import SwiftUI

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    @State private var isEventListPresented = false

    private var londonDate: String { LondonDay.localDateKey() }

    var body: some View {
        Group {
            if let briefing = model.todayBriefing {
                briefingContent(briefing)
            } else {
                emptyState
            }
        }
        .gridPageBackground()
        .task(id: londonDate) {
            await model.loadTodayBriefing(localDate: londonDate)
        }
        .sheet(isPresented: $isEventListPresented) {
            ReportedEventsListSheet()
                .presentationDetents([.large])
                .presentationDragIndicator(.visible)
                .presentationBackground(GridTheme.background)
        }
    }

    private func briefingContent(_ briefing: TodayBriefing) -> some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 27) {
                header(briefing)

                if let error = model.briefingError {
                    TodayStateNotice(
                        title: "Showing the last briefing",
                        message: error,
                        systemImage: "clock.badge.exclamationmark"
                    ) {
                        Task { await model.loadTodayBriefing(localDate: londonDate) }
                    }
                } else if model.briefingIsFromCache {
                    Label("Saved briefing · checking for an update", systemImage: "arrow.clockwise")
                        .font(.caption)
                        .foregroundStyle(GridTheme.staleAmber)
                }

                if briefing.coverage.status != .complete {
                    coverageNotice(briefing)
                }

                nowSection(briefing)
                bestWindowSection(briefing)
                changesSection(briefing)
                nextSection(briefing)
                eventsSection(briefing)
                coverageFooter(briefing)
            }
            .padding(.horizontal, GridTheme.horizontalPadding)
            .padding(.top, 12)
            .padding(.bottom, 32)
        }
        .scrollIndicators(.hidden)
        .refreshable {
            await model.loadTodayBriefing(localDate: LondonDay.localDateKey())
        }
    }

    private func header(_ briefing: TodayBriefing) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    todayTitle
                    Spacer(minLength: 8)
                    briefingDate(briefing)
                    GlobalInfoButton()
                }
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        todayTitle
                        Spacer()
                        GlobalInfoButton()
                    }
                    briefingDate(briefing)
                }
            }
            Text(briefing.headline)
                .font(.subheadline)
                .foregroundStyle(GridTheme.textSecondary)
            Text("\(TodayBriefingPresentation.systemScope) · as of \(TodayBriefingPresentation.timeLabel(briefing.asOf, relativeTo: briefing.displayPeriod.localDate))")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
        }
    }

    private var todayTitle: some View {
        Text("Today")
            .font(.system(.largeTitle, design: .rounded, weight: .medium))
            .tracking(-1.2)
            .accessibilityAddTraits(.isHeader)
    }

    private func briefingDate(_ briefing: TodayBriefing) -> some View {
        Text(TodayBriefingPresentation.dateLabel(briefing.asOf))
            .font(.caption)
            .fontDesign(.monospaced)
            .foregroundStyle(GridTheme.textTertiary)
    }

    private func coverageNotice(_ briefing: TodayBriefing) -> some View {
        HStack(alignment: .top, spacing: 11) {
            Image(systemName: coverageSymbol(briefing.coverage.status))
                .foregroundStyle(GridTheme.staleAmber)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 4) {
                Text(TodayBriefingPresentation.statusTitle(briefing.coverage.status))
                    .font(.subheadline.weight(.semibold))
                Text(briefing.summary)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.staleAmber.opacity(0.18), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }

    private func coverageSymbol(_ status: TodayBriefingStatus) -> String {
        switch status {
        case .partial: "circle.lefthalf.filled"
        case .offline: "wifi.slash"
        case .observedOnly: "eye"
        case .empty: "tray"
        case .complete: "checkmark.circle"
        case .unknown: "questionmark.circle"
        }
    }

    private func nowSection(_ briefing: TodayBriefing) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            SectionLabel("Now", trailing: briefing.now.status.rawValue.uppercased())
            Text("\(TodayBriefingPresentation.systemScope.uppercased()) · \(TodayBriefingPresentation.timeLabel(briefing.now.asOf ?? briefing.asOf, relativeTo: briefing.displayPeriod.localDate))")
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .tracking(0.5)
                .foregroundStyle(GridTheme.liveCyan)
            Text(briefing.now.text)
                .font(.system(.title3, design: .rounded, weight: .medium))
                .foregroundStyle(GridTheme.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            if !briefing.now.values.isEmpty {
                Hairline()
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 140), spacing: 18, alignment: .leading)],
                    alignment: .leading,
                    spacing: 16
                ) {
                    ForEach(briefing.now.values.prefix(3)) { value in
                        currentValue(value, localDate: briefing.displayPeriod.localDate)
                    }
                }
            }

            if !briefing.now.missingMetricIDs.isEmpty {
                Text("Unavailable now: \(briefing.now.missingMetricIDs.prefix(3).joined(separator: ", ")).")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            }
        }
    }

    private func currentValue(_ value: TodayCurrentValue, localDate: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("\(TodayBriefingPresentation.classification(value.factClass)) · GB")
                .font(.caption2.weight(.semibold))
                .fontDesign(.monospaced)
                .foregroundStyle(classificationColor(value.factClass))
            Text(TodayBriefingPresentation.timeLabel(value.observedAt, relativeTo: localDate))
                .font(.caption2)
                .fontDesign(.monospaced)
                .foregroundStyle(GridTheme.textTertiary)
            Text(TodayBriefingPresentation.value(value.value, unit: value.unit))
                .font(.system(.title3, design: .rounded, weight: .medium))
                .foregroundStyle(GridTheme.textPrimary)
            Text(value.label)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private func classificationColor(_ factClass: TodayCurrentFactClass) -> Color {
        switch factClass {
        case .reported: GridTheme.warning
        case .unknown: GridTheme.textTertiary
        default: GridTheme.liveCyan
        }
    }

    @ViewBuilder
    private func bestWindowSection(_ briefing: TodayBriefing) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionLabel("Best GB window", trailing: "60 MIN CONTINUOUS")
            if let window = TodayBriefingPresentation.visibleBestWindow(briefing) {
                VStack(alignment: .leading, spacing: 14) {
                    Text("FORECAST · GB NATIONAL · COMPLETE COVERAGE")
                        .font(.caption2.weight(.semibold))
                        .fontDesign(.monospaced)
                        .tracking(0.5)
                        .foregroundStyle(GridTheme.forecastViolet)
                    Text(TodayBriefingPresentation.windowLabel(window, relativeTo: briefing.displayPeriod.localDate))
                        .font(.system(.title2, design: .monospaced, weight: .medium))
                        .foregroundStyle(GridTheme.forecastViolet)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(window.label)
                        .font(.subheadline.weight(.medium))
                    Hairline()
                    ViewThatFits(in: .horizontal) {
                        HStack(alignment: .top, spacing: 24) {
                            briefingFact(
                                value: TodayBriefingPresentation.value(window.averageValue, unit: window.unit),
                                label: "Average forecast intensity"
                            )
                            briefingFact(
                                value: TodayBriefingPresentation.timeLabel(window.capturedAt, relativeTo: briefing.displayPeriod.localDate),
                                label: "Captured"
                            )
                        }
                        VStack(alignment: .leading, spacing: 12) {
                            briefingFact(
                                value: TodayBriefingPresentation.value(window.averageValue, unit: window.unit),
                                label: "Average forecast intensity"
                            )
                            briefingFact(
                                value: TodayBriefingPresentation.timeLabel(window.capturedAt, relativeTo: briefing.displayPeriod.localDate),
                                label: "Captured"
                            )
                        }
                    }
                    Text("100% of the selected window is covered by one compatible national forecast capture.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                    Button {
                        model.selectedTab = .mine
                    } label: {
                        Label("Plan an activity in Local", systemImage: "arrow.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.forecastViolet)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                .padding(16)
                .background(GridTheme.forecastViolet.opacity(0.075), in: RoundedRectangle(cornerRadius: GridTheme.cornerRadius))
                .overlay(RoundedRectangle(cornerRadius: GridTheme.cornerRadius).stroke(GridTheme.forecastViolet.opacity(0.2), lineWidth: 1))
            } else {
                TodayEmptySection(
                    message: "No complete future GB national forecast window is included in this briefing."
                )
            }
        }
    }

    private func briefingFact(value: String, label: String) -> some View {
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

    private func changesSection(_ briefing: TodayBriefing) -> some View {
        let changes = TodayBriefingPresentation.displayedChanges(briefing)
        return VStack(alignment: .leading, spacing: 0) {
            SectionLabel("What changed", trailing: changes.isEmpty ? "NONE" : "OBSERVED · \(changes.count)")
                .padding(.bottom, 8)
            if changes.isEmpty {
                TodayEmptySection(message: "No material observed changes qualified for this briefing.")
            } else {
                ForEach(changes) { change in
                    momentRow(
                        classification: "OBSERVED · GB",
                        time: TodayBriefingPresentation.timeLabel(change.observedAt, relativeTo: briefing.displayPeriod.localDate),
                        title: change.label,
                        detail: change.text,
                        color: GridTheme.liveCyan,
                        momentTime: change.observedAt
                    )
                }
            }
        }
    }

    private func nextSection(_ briefing: TodayBriefing) -> some View {
        let moments = TodayBriefingPresentation.displayedNextMoments(briefing)
        return VStack(alignment: .leading, spacing: 0) {
            SectionLabel("Coming next", trailing: moments.isEmpty ? "NONE" : "\(moments.count)")
                .padding(.bottom, 8)
            if moments.isEmpty {
                TodayEmptySection(message: "No qualifying future forecast or reported moment is included.")
            } else {
                ForEach(moments) { moment in
                    momentRow(
                        classification: "\(TodayBriefingPresentation.classification(moment.factClass)) · GB",
                        time: TodayBriefingPresentation.timeLabel(moment.startsAt, relativeTo: briefing.displayPeriod.localDate),
                        title: moment.label,
                        detail: moment.text,
                        color: moment.factClass == .forecast ? GridTheme.forecastViolet : GridTheme.warning,
                        momentTime: moment.startsAt
                    )
                }
            }
        }
    }

    @ViewBuilder
    private func momentRow(
        classification: String,
        time: String,
        title: String,
        detail: String,
        color: Color,
        momentTime: Date?
    ) -> some View {
        if let momentTime {
            Button {
                openMoment(momentTime)
            } label: {
                briefingRowContent(
                    classification: classification,
                    time: time,
                    title: title,
                    detail: detail,
                    color: color,
                    showsChevron: true
                )
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .accessibilityHint("Opens this supplied moment on Live")
        } else {
            briefingRowContent(
                classification: classification,
                time: time,
                title: title,
                detail: detail,
                color: color,
                showsChevron: false
            )
        }
    }

    private func briefingRowContent(
        classification: String,
        time: String,
        title: String,
        detail: String,
        color: Color,
        showsChevron: Bool
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            ViewThatFits(in: .horizontal) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    classificationLabel(classification, color: color)
                    Spacer(minLength: 8)
                    timeLabel(time)
                }
                VStack(alignment: .leading, spacing: 3) {
                    classificationLabel(classification, color: color)
                    timeLabel(time)
                }
            }
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                Spacer(minLength: 4)
                if showsChevron {
                    Image(systemName: "chevron.right")
                        .font(.caption)
                        .foregroundStyle(GridTheme.textTertiary)
                        .accessibilityHidden(true)
                }
            }
            Text(detail)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, minHeight: 82, alignment: .leading)
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
    }

    private func classificationLabel(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.semibold))
            .fontDesign(.monospaced)
            .foregroundStyle(color)
    }

    private func timeLabel(_ text: String) -> some View {
        Text(text)
            .font(.caption2)
            .fontDesign(.monospaced)
            .foregroundStyle(GridTheme.textTertiary)
    }

    private func eventsSection(_ briefing: TodayBriefing) -> some View {
        let events = TodayBriefingPresentation.displayedEvents(briefing)
        return VStack(alignment: .leading, spacing: 0) {
            SectionLabel(
                "Reported events",
                trailing: TodayBriefingPresentation.eventCountLabel(briefing.reportedEvents)
            )
            .padding(.bottom, 8)

            if events.isEmpty {
                TodayEmptySection(message: "No active or next-24-hour reported events are included.")
            } else {
                ForEach(events) { event in
                    eventRow(event, briefing: briefing)
                }
                if TodayBriefingPresentation.shouldShowAllEvents(briefing.reportedEvents) {
                    Text("Showing \(events.count) of \(briefing.reportedEvents.totalCount) server-ranked active or next-24-hour reported events. The separate current active list can differ because it excludes upcoming notices.")
                        .font(.caption2)
                        .foregroundStyle(GridTheme.textTertiary)
                        .padding(.top, 10)
                    Button {
                        isEventListPresented = true
                    } label: {
                        Label("Open current active list", systemImage: "list.bullet")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(GridTheme.liveCyan)
                            .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityHint("Opens the separate current active reported-event list")
                }
            }
        }
    }

    @ViewBuilder
    private func eventRow(_ event: TodayReportedEvent, briefing: TodayBriefing) -> some View {
        if let mapped = mappedEvent(event) {
            Button {
                open(mapped)
            } label: {
                eventRowContent(event, localDate: briefing.displayPeriod.localDate, showsChevron: true)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: 44)
            .accessibilityHint("Opens the matching reported event")
        } else {
            eventRowContent(event, localDate: briefing.displayPeriod.localDate, showsChevron: false)
        }
    }

    private func eventRowContent(
        _ event: TodayReportedEvent,
        localDate: String,
        showsChevron: Bool
    ) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        eventClassification(event)
                        Spacer(minLength: 8)
                        eventTime(event, localDate: localDate)
                    }
                    VStack(alignment: .leading, spacing: 3) {
                        eventClassification(event)
                        eventTime(event, localDate: localDate)
                    }
                }
                Text(event.title)
                    .font(.headline)
                    .foregroundStyle(GridTheme.textPrimary)
                Text(event.text)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if showsChevron {
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundStyle(GridTheme.textTertiary)
                    .padding(.top, 4)
                    .accessibilityHidden(true)
            }
        }
        .frame(maxWidth: .infinity, minHeight: 82, alignment: .leading)
        .padding(.vertical, 10)
        .overlay(alignment: .bottom) { Hairline() }
        .accessibilityElement(children: .combine)
    }

    private func eventClassification(_ event: TodayReportedEvent) -> some View {
        Text("REPORTED · \(event.timing.rawValue.uppercased())")
            .font(.caption2.weight(.semibold))
            .fontDesign(.monospaced)
            .foregroundStyle(eventColor(event.severity))
    }

    private func eventTime(_ event: TodayReportedEvent, localDate: String) -> some View {
        Text(TodayBriefingPresentation.timeLabel(event.startsAt ?? event.publishedAt, relativeTo: localDate))
            .font(.caption2)
            .fontDesign(.monospaced)
            .foregroundStyle(GridTheme.textTertiary)
    }

    private func eventColor(_ severity: TodayReportedEventSeverity) -> Color {
        switch severity {
        case .critical, .material: GridTheme.warning
        case .notable: GridTheme.staleAmber
        case .info: GridTheme.liveCyan
        case .unknown: GridTheme.textTertiary
        }
    }

    private func mappedEvent(_ event: TodayReportedEvent) -> GridEvent? {
        guard !event.stableID.isEmpty,
              event.evidenceClass.caseInsensitiveCompare("reported") == .orderedSame else { return nil }
        return model.events.first {
            $0.id == event.stableID && $0.isAuthoritativelyReported
        }
    }

    private func open(_ event: GridEvent) {
        model.selectedTab = .live
        Task { @MainActor in
            await Task.yield()
            model.selectedEvent = event
        }
    }

    private func openMoment(_ time: Date) {
        model.selectedTime = time
        model.selectedFuel = nil
        model.selectedTab = .live
    }

    private func coverageFooter(_ briefing: TodayBriefing) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            Hairline()
            SectionLabel("Coverage", trailing: briefing.coverage.status.rawValue.uppercased())
            Text(briefing.summary)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
            if let counts = TodayBriefingPresentation.sourceCountLabel(briefing.coverage.sourceCountsByState) {
                Text("Sources: \(counts).")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            if !briefing.coverage.missingFamilies.isEmpty {
                Text("Unavailable families: \(briefing.coverage.missingFamilies.prefix(6).joined(separator: ", ")).")
                    .font(.caption2)
                    .foregroundStyle(GridTheme.staleAmber)
            }
            ForEach(Array(briefing.coverage.notes.prefix(3).enumerated()), id: \.offset) { _, note in
                Text(note)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
            Text("Briefing as of \(TodayBriefingPresentation.timeLabel(briefing.asOf, relativeTo: briefing.displayPeriod.localDate))\(generatedSuffix(briefing)).")
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            Text(TodayBriefingPresentation.methodologyCopy)
                .font(.caption2)
                .foregroundStyle(GridTheme.textTertiary)
            ForEach(Array(briefing.limitations.prefix(2).enumerated()), id: \.offset) { _, limitation in
                Text(limitation)
                    .font(.caption2)
                    .foregroundStyle(GridTheme.textTertiary)
            }
        }
    }

    private func generatedSuffix(_ briefing: TodayBriefing) -> String {
        guard let generatedAt = briefing.generatedAt else { return "" }
        return ", generated \(TodayBriefingPresentation.timeLabel(generatedAt, relativeTo: briefing.displayPeriod.localDate))"
    }

    @ViewBuilder
    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack {
                Text("Today")
                    .font(.system(.largeTitle, design: .rounded, weight: .medium))
                    .accessibilityAddTraits(.isHeader)
                Spacer()
                GlobalInfoButton()
            }
            switch model.briefingLoadPhase {
            case .loading:
                ProgressView("Building today’s briefing…")
                    .tint(GridTheme.liveCyan)
                    .foregroundStyle(GridTheme.textSecondary)
            case .failed(let message):
                TodayStateNotice(
                    title: "Today’s briefing is unavailable",
                    message: message,
                    systemImage: "wifi.exclamationmark"
                ) {
                    Task { await model.loadTodayBriefing(localDate: londonDate) }
                }
            case .loaded:
                TodayEmptySection(message: "No briefing has been returned for the current London date.")
            }
            Spacer()
        }
        .padding(.horizontal, GridTheme.horizontalPadding)
        .padding(.top, 12)
    }
}

private struct TodayEmptySection: View {
    let message: String

    var body: some View {
        Text(message)
            .font(.caption)
            .foregroundStyle(GridTheme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
            .padding(.vertical, 6)
    }
}

private struct TodayStateNotice: View {
    let title: String
    let message: String
    let systemImage: String
    let retry: () -> Void

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 11) {
                noticeIcon
                noticeCopy
                Spacer(minLength: 4)
                retryButton(expands: false)
            }
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    noticeIcon
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                }
                Text(message)
                    .font(.caption)
                    .foregroundStyle(GridTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                retryButton(expands: true)
            }
        }
        .padding(14)
        .background(GridTheme.staleAmber.opacity(0.07), in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).stroke(GridTheme.staleAmber.opacity(0.18), lineWidth: 1))
    }

    private var noticeIcon: some View {
        Image(systemName: systemImage)
            .foregroundStyle(GridTheme.staleAmber)
            .accessibilityHidden(true)
    }

    private var noticeCopy: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.subheadline.weight(.semibold))
            Text(message)
                .font(.caption)
                .foregroundStyle(GridTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func retryButton(expands: Bool) -> some View {
        Button("Retry", action: retry)
            .font(.caption.weight(.semibold))
            .foregroundStyle(GridTheme.staleAmber)
            .frame(
                minWidth: 44,
                maxWidth: expands ? .infinity : nil,
                minHeight: 44,
                alignment: .leading
            )
    }
}
