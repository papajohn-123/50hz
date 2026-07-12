import Foundation
import XCTest
@testable import FiftyHz

final class LocalPlanningBoundsTests: XCTestCase {
    func testDefaultsStartAtNextExactBoundaryAndSpanTwentyFourHours() {
        let now = Date(timeIntervalSince1970: 1_800_000_001)
        let bounds = LocalPlanningBoundsPolicy.defaults(now: now)

        XCTAssertTrue(LocalPlanningBoundsPolicy.isExactHalfHour(bounds.earliest))
        XCTAssertTrue(LocalPlanningBoundsPolicy.isExactHalfHour(bounds.latest))
        XCTAssertGreaterThan(bounds.earliest, now)
        XCTAssertEqual(bounds.latest.timeIntervalSince(bounds.earliest), 24 * 60 * 60)
        XCTAssertNil(
            LocalPlanningBoundsPolicy.issue(
                for: bounds,
                durationMinutes: 720,
                now: now
            )
        )
    }

    func testExactBoundaryIsRetainedRatherThanMovedForward() {
        let boundary = Date(timeIntervalSince1970: 1_800_000_000)
        XCTAssertEqual(
            LocalPlanningBoundsPolicy.nextHalfHour(atOrAfter: boundary),
            boundary
        )
    }

    func testBoundsRejectPastMisalignedShortAndOversizedRequests() {
        let now = Date(timeIntervalSince1970: 1_800_000_001)
        let next = LocalPlanningBoundsPolicy.nextHalfHour(atOrAfter: now)

        XCTAssertEqual(
            issue(next.addingTimeInterval(-1_800), next.addingTimeInterval(3_600), now: now),
            .earliestBeforeNextSlot
        )
        XCTAssertEqual(
            issue(next.addingTimeInterval(1), next.addingTimeInterval(3_600), now: now),
            .notOnHalfHour
        )
        XCTAssertEqual(issue(next, next, now: now), .deadlineBeforeStart)
        XCTAssertEqual(
            issue(next, next.addingTimeInterval(3_600), duration: 120, now: now),
            .durationDoesNotFit
        )
        XCTAssertEqual(
            issue(next, next.addingTimeInterval(49 * 3_600), now: now),
            .horizonTooLong
        )
        XCTAssertEqual(
            issue(
                next.addingTimeInterval(47 * 3_600),
                next.addingTimeInterval(49 * 3_600),
                now: now
            ),
            .outsideForecastHorizon
        )
        XCTAssertEqual(
            issue(next, next.addingTimeInterval(24 * 3_600), duration: 45, now: now),
            .invalidDuration
        )
    }

    func testPolicyUsesElapsedHalfHoursAcrossLondonClockChange() throws {
        let parser = ISO8601DateFormatter()
        let beforeSpringChange = try XCTUnwrap(parser.date(from: "2026-03-29T00:40:00Z"))
        let earliest = LocalPlanningBoundsPolicy.nextHalfHour(atOrAfter: beforeSpringChange)
        let bounds = LocalPlanningBounds(
            earliest: earliest,
            latest: earliest.addingTimeInterval(120 * 60)
        )

        XCTAssertEqual(parser.string(from: earliest), "2026-03-29T01:00:00Z")
        XCTAssertNil(
            LocalPlanningBoundsPolicy.issue(
                for: bounds,
                durationMinutes: 120,
                now: beforeSpringChange
            )
        )
    }

    private func issue(
        _ earliest: Date,
        _ latest: Date,
        duration: Int = 60,
        now: Date
    ) -> LocalPlanningBoundsIssue? {
        LocalPlanningBoundsPolicy.issue(
            for: LocalPlanningBounds(earliest: earliest, latest: latest),
            durationMinutes: duration,
            now: now
        )
    }
}
