import XCTest
@testable import Devmax

/// Turn 3 must reach `POST /sessions/{id}/reattempt`, never `/answers`.
///
/// These are regressions for a bug that nearly shipped: `ConversationScreen`
/// briefly set `stage = .processing` before
/// awaiting the recognizer's final transcript, to stop a second tap submitting
/// twice. That looked like a duplicate of what `sendAnswer` already does, but
/// `Stage` is data — `submit` dispatches turn 3 on `stage.isReattempt` — so a
/// *spoken* re-attempt would have been posted to `/answers` instead. That path
/// applies SM-2, and a post-correction turn reaching the scheduler is the thing
/// `docs/multi-turn-coaching-design.md` exists to prevent.
///
/// A caveat worth stating: this asserts the dispatch seam in `AppState`, so it
/// pins the invariant but cannot see a view flattening the stage before calling
/// in. That specific regression was caught by reading, not by this test.
final class ReattemptRoutingTests: XCTestCase {
    /// Records which endpoint an answer reached. Only the two submit methods do
    /// anything; the rest satisfy the protocol.
    private final class SpyAPI: DevmaxAPI, @unchecked Sendable {
        var answerCalls: [String] = []
        var reattemptCalls: [String] = []

        func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome {
            answerCalls.append(text)
            return .complete(
                score: 1, feedback: "", nextReviewAt: "2026-07-30", intervalDays: 1,
                practice: false, reattemptOffered: true, reattemptPrompt: "In your words — why?"
            )
        }

        func submitReattempt(sessionID: UUID, text: String) async throws {
            reattemptCalls.append(text)
        }

        func due() async throws -> [DueCard] { [] }
        func cards(sort: String, mode: String) async throws -> [CardSummary] { [] }
        func card(_ id: UUID) async throws -> CardDetail { throw CancellationError() }
        func createCard(topic: String, schedule: String) async throws -> CardSummary {
            throw CancellationError()
        }
        func startSession(cardID: UUID, practice: Bool) async throws -> SessionStart {
            throw CancellationError()
        }
        func saveDraft(sessionID: UUID, text: String) async throws {}
        func settings() async throws -> AppSettings { throw CancellationError() }
        func updateSettings(_ settings: AppSettings) async throws -> AppSettings {
            throw CancellationError()
        }
        func registerDeviceToken(_ token: String) async throws {}

        // Study Plan. Not exercised by these tests, but the protocol is the
        // whole client surface — a new endpoint has to be answered here or the
        // test target stops compiling, which is the point.
        func activePlan() async throws -> StudyPlanSummary { .none }
        func plans() async throws -> PlanList { throw CancellationError() }
        func planOverview(_ id: UUID) async throws -> PlanOverview {
            throw CancellationError()
        }
        func planWeek(_ id: UUID, index: Int) async throws -> WeekDetail {
            throw CancellationError()
        }
        func planItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
            throw CancellationError()
        }
        func editPlanItem(
            _ id: UUID, itemID: UUID, edit: PlanItemEdit
        ) async throws -> PlanItemDetail { throw CancellationError() }
        func completePlanItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
            throw CancellationError()
        }
        func previewReopen(_ id: UUID, itemID: UUID) async throws -> PlanProposal {
            throw CancellationError()
        }
        func reopenPlanItem(
            _ id: UUID, itemID: UUID, revision: Int
        ) async throws -> PlanItemDetail { throw CancellationError() }
        func previewReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal {
            throw CancellationError()
        }
        func applyReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal {
            throw CancellationError()
        }
        func updateWeekCapacity(
            _ id: UUID, index: Int, minutes: Int?, revision: Int
        ) async throws -> PlanProposal { throw CancellationError() }
        func pausePlan(_ id: UUID) async throws -> PlanOverview { throw CancellationError() }
        func previewResume(_ id: UUID) async throws -> PlanProposal {
            throw CancellationError()
        }
        func applyResume(_ id: UUID, revision: Int) async throws -> PlanOverview {
            throw CancellationError()
        }
        func activatePlan(_ id: UUID, revision: Int) async throws -> PlanOverview {
            throw CancellationError()
        }
        func completePlan(_ id: UUID) async throws -> PlanOverview { throw CancellationError() }
        func archivePlan(_ id: UUID) async throws -> PlanOverview { throw CancellationError() }
        func duplicatePlan(_ id: UUID) async throws -> PlanOverview {
            throw CancellationError()
        }
        func planRevisions(_ id: UUID) async throws -> [PlanRevisionEntry] { [] }
        func planRecap(_ id: UUID) async throws -> PlanRecap { throw CancellationError() }
        func previewGuide(_ request: GuidePreviewRequest) async throws -> PlanPreview {
            throw CancellationError()
        }
        func retryPreview(draftID: UUID) async throws -> PlanPreview {
            throw CancellationError()
        }
        func editPreview(draftID: UUID, edit: PreviewEdit) async throws -> PlanPreview {
            throw CancellationError()
        }
        func createPlan(draftID: UUID, activate: Bool) async throws -> PlanOverview {
            throw CancellationError()
        }
        func createCardProposals(
            _ id: UUID, itemID: UUID
        ) async throws -> CardProposalList { throw CancellationError() }
        func cardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList {
            throw CancellationError()
        }
        func acceptCardProposals(
            _ id: UUID, selected: [UUID], idempotencyKey: String, revision: Int,
            edits: [String: [String: String]]
        ) async throws -> CardAcceptResult { throw CancellationError() }
        func resolveDuplicate(_ id: UUID, proposalID: UUID, action: String) async throws {}
    }

    @MainActor
    func testSpokenReattemptGoesToTheReattemptEndpoint() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionID = UUID()
        // The recording twin of turn 3 — what the stage is when a spoken
        // re-attempt is submitted.
        state.stage = .recordingReattempt

        await state.submit("a coached second try")

        XCTAssertEqual(api.reattemptCalls, ["a coached second try"])
        XCTAssertTrue(api.answerCalls.isEmpty, "a re-attempt must never reach /answers")
    }

    @MainActor
    func testSpokenFirstAnswerGoesToTheAnswersEndpoint() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionID = UUID()
        state.stage = .recording

        await state.submit("a first answer")

        XCTAssertEqual(api.answerCalls, ["a first answer"])
        XCTAssertTrue(api.reattemptCalls.isEmpty)
    }

    /// The stage carries which turn an answer belongs to. Flattening it before
    /// `submit` reads it is what the routing bug did.
    func testRecordingStagesCarryTheirTurn() {
        XCTAssertTrue(Stage.recordingReattempt.isReattempt)
        XCTAssertTrue(Stage.reattempt.isReattempt)
        XCTAssertFalse(Stage.recording.isReattempt)
        XCTAssertFalse(Stage.recordingFollowUp.isReattempt)
        XCTAssertFalse(Stage.processing.isReattempt, "the flattened stage loses turn 3")

        // And the failure rewind lands back on the right turn.
        XCTAssertEqual(Stage.recordingReattempt.answeringTwin, .reattempt)
        XCTAssertEqual(Stage.recordingFollowUp.answeringTwin, .followUp)
        XCTAssertEqual(Stage.recording.answeringTwin, .idle)
    }

    // MARK: - A question that never loaded

    /// Shares `SpyAPI` because its `startSession` already throws and its
    /// conformance is the whole client surface — a second copy would be a second
    /// thing to keep in sync for no extra coverage.
    private static let card = DueCard(
        id: UUID(), topic: "Consistent hashing", category: "Core Concept",
        masterySummary: "", lastScore: nil, dueLabel: "due today",
        resumable: false, missedCount: 0
    )

    /// The regression from the shipped bug: a failed `startSession` reported
    /// itself as a *submit* failure ("your answer is saved" — nothing was), and
    /// left the previous card's `sessionID` in place. Answering then posted to a
    /// live session belonging to a different card, so the wrong card got scored
    /// and rescheduled.
    @MainActor
    func testFailedQuestionLoadClearsTheSessionAndBlocksAnswering() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionCards = [Self.card]
        // A session left over from the card before this one.
        state.sessionID = UUID()

        await state.openCard(Self.card)

        XCTAssertNil(state.sessionID, "a stale session would score the previous card")
        XCTAssertNotNil(state.questionError)
        XCTAssertFalse(state.submitError, "nothing was submitted, and nothing was saved")
        XCTAssertTrue(state.thread.isEmpty)
        XCTAssertEqual(state.stage, .loadingQuestion, "the answer control must stay dead")
        XCTAssertFalse(state.stage.acceptsAnswer)

        await state.submit("an answer with nowhere to go")
        XCTAssertTrue(api.answerCalls.isEmpty)
        XCTAssertTrue(api.reattemptCalls.isEmpty)
    }

    /// The note is the actionable half — it says which side is down, and a 503
    /// (Claude unreachable) is a different problem than a dropped connection.
    @MainActor
    func testLoadNoteNamesTheCause() {
        XCTAssertEqual(
            AppState.loadNote(for: APIError.scoringUnavailable),
            "QUESTION GENERATION UNAVAILABLE"
        )
        XCTAssertEqual(AppState.loadNote(for: APIError.status(500)), "SERVER ERROR 500")
        XCTAssertEqual(AppState.loadNote(for: CancellationError()), "SERVER UNREACHABLE")
    }
}
