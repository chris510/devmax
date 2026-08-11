import XCTest
@testable import Devmax

final class HistoryRoutingTests: XCTestCase {
    private static let card = DueCard(
        id: UUID(), topic: "Non-functional requirements", category: "Delivery",
        masterySummary: "No signal yet.", lastScore: nil, dueLabel: "due today",
        resumable: false, missedCount: 0
    )

    @MainActor
    func testEmptyHistoryCanStartOnlyItsDueReview() {
        let state = AppState(api: MockAPI.shared)
        state.queue = [Self.card]
        state.path = [.history(Self.card.id)]

        XCTAssertTrue(state.beginReviewFromHistory(cardID: Self.card.id))
        XCTAssertEqual(state.sessionCards, [Self.card])
        XCTAssertEqual(state.path, [.conversation(Self.card.id)])
    }

    @MainActor
    func testHistoryCannotStartACardOutsideTheDueQueue() {
        let state = AppState(api: MockAPI.shared)
        let otherID = UUID()
        state.queue = [Self.card]
        state.path = [.history(otherID)]

        XCTAssertFalse(state.beginReviewFromHistory(cardID: otherID))
        XCTAssertTrue(state.sessionCards.isEmpty)
        XCTAssertEqual(state.path, [.history(otherID)])
    }
}
