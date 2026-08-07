import XCTest
@testable import Devmax

final class StudyQualitySelectionTests: XCTestCase {
    @MainActor
    func test_only_weakest_depth_axis_is_actionable() {
        let state = AppState(api: MockAPI.shared)
        state.library = [
            card(1, tradeOff: 1, failureMode: 4),
            card(2, tradeOff: 2, failureMode: 3),
            card(3, tradeOff: 1, failureMode: 4),
            card(4, tradeOff: 2, failureMode: 3),
        ]
        state.libraryLoad = .ready

        let actionable = state.axisRollup.filter(\.isActionable)
        XCTAssertEqual(actionable.count, 1)
        XCTAssertEqual(actionable.first?.depthAxis, .tradeOff)
    }

    @MainActor
    func test_depth_repair_ranks_the_selected_axis_and_removal_does_not_refill() {
        let state = AppState(api: MockAPI.shared)
        state.library = (0..<8).map {
            card($0, tradeOff: $0 % 4, failureMode: 4, daysSinceReview: $0)
        }
        state.libraryLoad = .ready
        state.setupSize = 6
        state.sprintKind = .depth(.tradeOff)

        let before = state.sprintSet
        let poolCount = state.sprintPool.count
        XCTAssertEqual(before.count, 6)
        XCTAssertEqual(before.map(\.lastTradeOffAwareness), before.map(\.lastTradeOffAwareness).sorted {
            ($0 ?? 5) < ($1 ?? 5)
        })

        state.removeFromSprint(before[0].id)

        XCTAssertEqual(state.sprintSet.count, 5)
        XCTAssertFalse(state.sprintSet.contains { $0.id == before[0].id })
        XCTAssertEqual(state.sprintPool.count, poolCount)
    }

    private static func id(_ value: Int) -> UUID {
        UUID(uuidString: String(format: "00000000-0000-0000-0000-%012x", value + 1))!
    }

    private func card(
        _ value: Int, tradeOff: Int, failureMode: Int, daysSinceReview: Int = 1
    ) -> CardSummary {
        CardSummary(
            id: Self.id(value), topic: "Topic \(value)", category: "Core Concept",
            deliveryMode: "conversational", masterySummary: "", lastScore: 3,
            lastMechanismAccuracy: 4, lastTradeOffAwareness: tradeOff,
            lastFailureModeAwareness: failureMode, easeFactor: 2.5, intervalDays: 3,
            repetitions: 2, nextReviewAt: "2026-08-08", dueLabel: "not due",
            daysSinceReview: daysSinceReview, missedCount: 0
        )
    }
}
