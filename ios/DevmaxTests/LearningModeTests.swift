import XCTest
@testable import Devmax

final class LearningModeTests: XCTestCase {
    private static let id = UUID(uuidString: "00000000-0000-0000-0000-0000000000c1")!
    private static let due = DueCard(
        id: id, topic: "Consistent hashing", category: "Core Concept",
        masterySummary: "No signal yet.", lastScore: nil, dueLabel: "due today",
        resumable: false, missedCount: 0
    )

    private static func summary(
        id: UUID = id, recallNotBeforeAt: String? = nil
    ) -> CardSummary {
        CardSummary(
            id: id, topic: "Consistent hashing", category: "Core Concept",
            deliveryMode: "conversational", masterySummary: "No signal yet.",
            lastScore: nil, lastAccuracy: nil, lastDepth: nil, lastBoundaries: nil,
            easeFactor: 2.5, intervalDays: 1, repetitions: 0,
            nextReviewAt: "2026-08-11", dueLabel: "due today",
            daysSinceReview: nil, missedCount: 0,
            recallNotBeforeAt: recallNotBeforeAt
        )
    }

    private static let learning = LearningCard(
        cardId: id, topic: "Consistent hashing", category: "Core Concept",
        sourceLabel: "Reviewed source", sourceSection: "Ring ownership",
        sourceUrl: "https://example.com/consistent-hashing", sourceExcerpt: "A trusted excerpt.",
        coreExplanation: "Keys and owners share an ordered hash space.",
        essentialAccount: "Walk clockwise to the next owner.",
        acceptableAlternative: "A fixed-slot ownership map.",
        depthExtension: "More virtual nodes improve balance at a metadata cost.",
        boundaryExtension: "Handoff must keep reads complete while ownership moves.",
        misconception: "Movement is bounded, not eliminated.",
        recallAvailableAt: "2099-08-12T19:00:00Z"
    )

    @MainActor
    func testOpeningLearningRemovesEveryLocalRecallLauncherWithoutMovingSM2() {
        let state = AppState(api: MockAPI.shared)
        state.queue = [Self.due]
        state.library = [Self.summary()]
        state.path = [.history(Self.id)]

        state.markLearningExposure(Self.learning)

        XCTAssertTrue(state.queue.isEmpty)
        XCTAssertTrue(state.sprintPool.isEmpty)
        XCTAssertEqual(state.library.first?.nextReviewAt, "2026-08-11")
        XCTAssertEqual(
            state.learningRecallNotBefore[Self.id], Self.learning.recallAvailableAt
        )
        XCTAssertFalse(state.beginReviewFromHistory(cardID: Self.id))
    }

    @MainActor
    func testHistoryPushesLearnAsItsOwnRoute() {
        let state = AppState(api: MockAPI.shared)
        state.path = [.history(Self.id)]

        state.beginLearningFromHistory(cardID: Self.id)

        XCTAssertEqual(state.path, [.history(Self.id), .learning(Self.id)])
    }

    @MainActor
    func testLearningTheLastDueCardHydratesTheExistingLibrary() async throws {
        let state = AppState(api: MockAPI.shared)
        state.queue = [Self.due]
        state.library = []

        state.markLearningExposure(Self.learning)
        for _ in 0..<20 where state.library.isEmpty {
            try await Task.sleep(for: .milliseconds(50))
        }

        XCTAssertTrue(state.queue.isEmpty)
        XCTAssertFalse(state.library.isEmpty)
    }

    func testFutureRecallDelayFailsClosedAndGetsAnAvailabilityLabel() throws {
        let now = try XCTUnwrap(WireDate.parse("2026-08-11T19:00:00Z"))
        let future = "2026-08-12T19:00:00Z"
        let past = "2026-08-10T19:00:00Z"

        XCTAssertFalse(Self.summary(recallNotBeforeAt: future).recallIsAvailable(at: now))
        XCTAssertNotNil(RecallGate.label(future, at: now))
        XCTAssertTrue(Self.summary(recallNotBeforeAt: past).recallIsAvailable(at: now))
        XCTAssertFalse(Self.summary(recallNotBeforeAt: "not-a-date").recallIsAvailable(at: now))
    }

    @MainActor
    func testSprintNamesRecallHoldsWithoutShrinkingTheLibrary() {
        let state = AppState(api: MockAPI.shared)
        state.libraryLoad = .ready
        state.library = (1...4).map { value in
            Self.summary(
                id: UUID(),
                recallNotBeforeAt: value == 1 ? "2099-08-12T19:00:00Z" : nil
            )
        }

        XCTAssertEqual(state.setupStatus, "3 CARDS AVAILABLE FOR RECALL")
        XCTAssertTrue(state.setupWaitingOnRecall)

        state.sprintKind = .depth(.depth)
        XCTAssertFalse(state.setupWaitingOnRecall)
    }

    func testLearningResponseDecodesTheSourceBackedWireShape() throws {
        let json = Data(#"""
        {"card_id":"00000000-0000-0000-0000-0000000000c1",
         "topic":"Consistent hashing","category":"Core Concept",
         "source_label":"Reviewed source","source_section":"Ring ownership",
         "source_url":"https://example.com/source","source_excerpt":"Trusted excerpt.",
         "core_explanation":"Core explanation.","essential_account":"Essential account.",
         "acceptable_alternative":"Alternative.","depth_extension":"Trade-off.",
         "boundary_extension":"Failure boundary.","misconception":"Misconception.",
         "recall_available_at":"2099-08-12T19:00:00Z"}
        """#.utf8)

        let value = try LiveAPI.decoder.decode(LearningCard.self, from: json)

        XCTAssertEqual(value.cardId, Self.id)
        XCTAssertEqual(value.essentialAccount, "Essential account.")
        XCTAssertEqual(value.boundaryExtension, "Failure boundary.")
        XCTAssertEqual(value.recallAvailableAt, "2099-08-12T19:00:00Z")
    }
}
