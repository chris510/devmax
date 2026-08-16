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

    @MainActor
    func testPendingPlanItemCannotOpenAnAlreadyMappedCard() {
        let state = AppState(api: MockAPI.shared)
        let planID = UUID()
        let itemID = UUID()
        let cardID = UUID()
        let mapping = PlanMappedRecallCard(
            topic: "Delivery framework", cardId: cardID,
            availabilityLabel: "available after lesson"
        )
        state.path = [.planItem(planID, itemID)]

        XCTAssertFalse(state.openMappedRecallCard(mapping, itemComplete: false))
        XCTAssertEqual(state.path, [.planItem(planID, itemID)])
    }

    @MainActor
    func testCompletedPlanItemOpensItsMappedCardHistory() {
        let state = AppState(api: MockAPI.shared)
        let planID = UUID()
        let itemID = UUID()
        let cardID = UUID()
        let mapping = PlanMappedRecallCard(
            topic: "Delivery framework", cardId: cardID,
            availabilityLabel: "first review tomorrow"
        )
        state.path = [.planItem(planID, itemID)]

        XCTAssertTrue(state.openMappedRecallCard(mapping, itemComplete: true))
        XCTAssertEqual(state.path, [.planItem(planID, itemID), .history(cardID)])
        XCTAssertEqual(state.historyBackLabel, "← Plan item")
    }

    @MainActor
    func testHistoryBackLabelDefaultsToToday() {
        let state = AppState(api: MockAPI.shared)
        state.path = [.history(UUID())]

        XCTAssertEqual(state.historyBackLabel, "← Today")
    }

    @MainActor
    func testHistoryBackLabelNamesReviewCardsWhenOpenedFromLibrary() {
        let state = AppState(api: MockAPI.shared)
        let cardID = UUID()
        state.path = [.library, .libraryCards, .history(cardID)]

        XCTAssertEqual(state.historyBackLabel, "← Review cards")
    }

    @MainActor
    func testLinkedFixtureOpensTheNamedRecallCard() async throws {
        let mapping = try XCTUnwrap(
            StudyPlanFixtures.linkedItemDetail().recallMappings.first
        )
        let cardID = try XCTUnwrap(mapping.cardId)
        let detail = try await MockAPI.shared.card(cardID)

        XCTAssertEqual(detail.id, cardID)
        XCTAssertEqual(detail.topic, mapping.topic)
    }
}
