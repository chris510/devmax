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
        struct StartStub {
            let delay: Duration
            let result: Result<SessionStart, APIError>
        }

        var answerCalls: [String] = []
        var reattemptCalls: [String] = []
        var coachingCalls: [String] = []
        var saveDraftCalls: [String] = []
        var startStubs: [UUID: [StartStub]] = [:]

        func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome {
            answerCalls.append(text)
            return .complete(
                score: 1, recallScore: 1, scoringContractVersion: 1,
                feedback: "", nextReviewAt: "2026-07-30", intervalDays: 1,
                practice: false, reattemptOffered: true, reattemptPrompt: "In your words — why?",
                coachingOffered: false, coachingFocus: nil, coachingQuestion: nil
            )
        }

        func submitReattempt(sessionID: UUID, text: String) async throws {
            reattemptCalls.append(text)
        }

        func submitCoaching(sessionID: UUID, text: String) async throws -> CoachingOutcome {
            coachingCalls.append(text)
            return CoachingOutcome(focus: "depth", question: "Why?", feedback: "Grounded.")
        }

        func due() async throws -> [DueCard] { [] }
        func cards(sort: String, mode: String) async throws -> [CardSummary] { [] }
        func card(_ id: UUID) async throws -> CardDetail { throw CancellationError() }
        func captures() async throws -> [CaptureSummary] { [] }
        func capture(_ id: UUID) async throws -> PendingCapture { throw APIError.status(404) }
        func createCapture(topic: String, context: String) async throws -> PendingCapture {
            throw CancellationError()
        }
        func updateCapture(
            _ id: UUID, update: CaptureUpdateRequest
        ) async throws -> PendingCapture { throw CancellationError() }
        func prepareCaptureQuestion(
            _ id: UUID, regenerate: Bool
        ) async throws -> PendingCapture { throw CancellationError() }
        func activateCapture(_ id: UUID, schedule: String) async throws -> CardSummary {
            throw CancellationError()
        }
        func discardCapture(_ id: UUID) async throws { throw CancellationError() }
        func cardMaintenance(_ id: UUID) async throws -> CardMaintenance {
            throw CancellationError()
        }
        func archiveCard(_ id: UUID) async throws -> CardMaintenance {
            throw CancellationError()
        }
        func restoreCard(_ id: UUID) async throws -> CardMaintenance {
            throw CancellationError()
        }
        func replaceCard(
            _ id: UUID, question: String, schedule: String
        ) async throws -> CardSummary { throw CancellationError() }
        func startSession(cardID: UUID, practice: Bool) async throws -> SessionStart {
            guard var stubs = startStubs[cardID], !stubs.isEmpty else {
                throw CancellationError()
            }
            let stub = stubs.removeFirst()
            startStubs[cardID] = stubs
            try await Task.sleep(for: stub.delay)
            return try stub.result.get()
        }
        func saveDraft(sessionID: UUID, text: String) async throws {
            saveDraftCalls.append(text)
        }
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
        func completePlanItem(
            _ id: UUID, itemID: UUID, revision: Int?
        ) async throws -> PlanItemDetail {
            throw CancellationError()
        }
        func savePracticeDebriefDraft(
            _ id: UUID, itemID: UUID, text: String
        ) async throws -> PracticeDebrief { throw CancellationError() }
        func submitPracticeDebrief(
            _ id: UUID, itemID: UUID, text: String
        ) async throws -> PracticeDebrief { throw CancellationError() }
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

    @MainActor
    func testSpokenQualitativeCoachingUsesOnlyTheCoachingEndpoint() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionID = UUID()
        state.stage = .recordingCoaching

        await state.submit("the deeper causal link")

        XCTAssertEqual(api.coachingCalls, ["the deeper causal link"])
        XCTAssertTrue(api.answerCalls.isEmpty)
        XCTAssertTrue(api.reattemptCalls.isEmpty)
        XCTAssertEqual(state.stage, .result)
        XCTAssertEqual(state.thread.last?.role, .coachingFeedback)
    }

    /// The stage carries which turn an answer belongs to. Flattening it before
    /// `submit` reads it is what the routing bug did.
    func testRecordingStagesCarryTheirTurn() {
        XCTAssertTrue(Stage.recordingReattempt.isReattempt)
        XCTAssertTrue(Stage.reattempt.isReattempt)
        XCTAssertFalse(Stage.recording.isReattempt)
        XCTAssertFalse(Stage.recordingFollowUp.isReattempt)
        XCTAssertFalse(Stage.processing.isReattempt, "the flattened stage loses turn 3")
        XCTAssertTrue(Stage.recordingCoaching.isCoaching)
        XCTAssertFalse(Stage.recordingCoaching.isReattempt)

        // And the failure rewind lands back on the right turn.
        XCTAssertEqual(Stage.recordingReattempt.answeringTwin, .reattempt)
        XCTAssertEqual(Stage.recordingFollowUp.answeringTwin, .followUp)
        XCTAssertEqual(Stage.recording.answeringTwin, .idle)
        XCTAssertEqual(Stage.recordingCoaching.answeringTwin, .coaching)
    }

    @MainActor
    func testSubmissionFlushCancelsDraftUploadRaceButKeepsLocalCopy() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Draft race")
        state.sessionCards = [card]
        state.sessionID = UUID()
        state.updateDraft("the final spoken transcript")
        defer { DraftStore.clear(for: card.id) }

        await state.flushDraftForSubmission()

        XCTAssertEqual(DraftStore.read(for: card.id), "the final spoken transcript")
        XCTAssertTrue(api.saveDraftCalls.isEmpty, "a late PATCH can repopulate the next turn")
    }

    // MARK: - A question that never loaded

    private static let card = DueCard(
        id: UUID(), topic: "Consistent hashing", category: "Core Concept",
        masterySummary: "", lastScore: nil, dueLabel: "due today",
        resumable: false, missedCount: 0
    )

    private static func card(_ topic: String) -> DueCard {
        DueCard(
            id: UUID(), topic: topic, category: "Core Concept", masterySummary: "",
            lastScore: nil, dueLabel: "due today", resumable: false, missedCount: 0
        )
    }

    private static func start(_ id: UUID, question: String) -> SessionStart {
        SessionStart(
            sessionId: id, question: question, isFollowUp: false,
            draftText: "", resumed: false
        )
    }

    /// The regression from the shipped bug: a failed `startSession` reported
    /// itself as a *submit* failure ("your answer is saved" — nothing was), and
    /// left the previous card's `sessionID` in place. Answering then posted to a
    /// live session belonging to a different card, so the wrong card got scored
    /// and rescheduled.
    ///
    /// Shares `SpyAPI` — its `startSession` already throws, and its conformance is
    /// the whole client surface, so a second copy would be a second thing to keep
    /// in sync for no extra coverage.
    @MainActor
    func testFailedQuestionLoadClearsTheSessionAndBlocksAnswering() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionCards = [Self.card]
        // A session left over from the card before this one.
        state.sessionID = UUID()

        await state.openCard(Self.card)

        XCTAssertNil(state.sessionID, "a stale session would score the previous card")
        XCTAssertEqual(state.stage, .questionFailed("SERVER UNREACHABLE"))
        XCTAssertFalse(state.submitError, "nothing was submitted, and nothing was saved")
        XCTAssertTrue(state.thread.isEmpty)

        await state.submit("an answer with nowhere to go")
        XCTAssertTrue(api.answerCalls.isEmpty)
        XCTAssertTrue(api.reattemptCalls.isEmpty)
    }

    /// The dead state must stay dead through every `Stage` helper — the two twins
    /// fall through to a live answering stage on their `default:` arms, which is
    /// how a stale stage became answerable in the first place.
    func testQuestionFailedIsAnsweredByNothing() {
        let failed = Stage.questionFailed("SERVER UNREACHABLE")
        XCTAssertFalse(failed.acceptsAnswer)
        XCTAssertFalse(failed.isRecording)
        XCTAssertFalse(failed.isReattempt)
        XCTAssertEqual(failed.recordingTwin, failed)
        XCTAssertEqual(failed.answeringTwin, failed)
        XCTAssertEqual(failed.footer, .hidden, "no session means no answer control")
        XCTAssertEqual(Stage.loadingQuestion.footer, .answer)
        XCTAssertEqual(Stage.result.footer, .result)
    }

    /// The note is the actionable half — it says which side is down, and a 503
    /// (Claude unreachable) is a different problem than a dropped connection.
    func testLoadNoteNamesTheCause() {
        XCTAssertEqual(APIError.scoringUnavailable.loadNote, "QUESTION GENERATION UNAVAILABLE")
        XCTAssertEqual(APIError.status(500).loadNote, "SERVER ERROR 500")
        XCTAssertEqual(CancellationError().loadNote, "SERVER UNREACHABLE")
    }

    /// Retry is the recovery contract for `.questionFailed`: it must create a new
    /// owned attempt and make the card answerable when the service comes back.
    @MainActor
    func testRetryRecoversAQuestionFailure() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("retry")
        let recoveredSession = UUID()
        state.sessionCards = [card]
        api.startStubs[card.id] = [
            .init(delay: .zero, result: .failure(.scoringUnavailable)),
            .init(
                delay: .zero,
                result: .success(Self.start(recoveredSession, question: "recovered question"))
            ),
        ]

        await state.openCard(card)
        XCTAssertEqual(state.stage, .questionFailed("QUESTION GENERATION UNAVAILABLE"))

        await state.retryQuestion()

        XCTAssertEqual(state.sessionID, recoveredSession)
        XCTAssertEqual(state.thread.first?.text, "recovered question")
        XCTAssertEqual(state.stage, .idle)
    }

    /// Card A can spend tens of seconds generating its canonical question while
    /// card B already has one and returns immediately. A's late response must not
    /// replace B's session — otherwise the next answer scores and reschedules A
    /// while the screen still identifies B.
    @MainActor
    func testOlderQuestionSuccessCannotOverwriteTheCurrentCard() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let slow = Self.card("slow")
        let fast = Self.card("fast")
        let slowSession = UUID()
        let fastSession = UUID()
        api.startStubs[slow.id] = [
            .init(
                delay: .milliseconds(100),
                result: .success(Self.start(slowSession, question: "slow question"))
            )
        ]
        api.startStubs[fast.id] = [
            .init(
                delay: .zero,
                result: .success(Self.start(fastSession, question: "fast question"))
            )
        ]

        state.sessionCards = [slow]
        let older = Task { await state.openCard(slow) }
        try? await Task.sleep(for: .milliseconds(10))
        state.sessionCards = [fast]
        await state.openCard(fast)
        await older.value

        XCTAssertEqual(state.currentCard?.id, fast.id)
        XCTAssertEqual(state.sessionID, fastSession)
        XCTAssertEqual(state.thread.first?.text, "fast question")
        XCTAssertEqual(state.stage, .idle)
    }

    /// Failure is just as stateful as success: an old request cannot turn a newer
    /// answerable card into a question-failure screen.
    @MainActor
    func testOlderQuestionFailureCannotReplaceANewerSuccess() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let slow = Self.card("slow")
        let fast = Self.card("fast")
        let fastSession = UUID()
        api.startStubs[slow.id] = [
            .init(delay: .milliseconds(100), result: .failure(.scoringUnavailable))
        ]
        api.startStubs[fast.id] = [
            .init(
                delay: .zero,
                result: .success(Self.start(fastSession, question: "fast question"))
            )
        ]

        state.sessionCards = [slow]
        let older = Task { await state.openCard(slow) }
        try? await Task.sleep(for: .milliseconds(10))
        state.sessionCards = [fast]
        await state.openCard(fast)
        await older.value

        XCTAssertEqual(state.sessionID, fastSession)
        XCTAssertEqual(state.thread.first?.text, "fast question")
        XCTAssertEqual(state.stage, .idle)
    }

    /// Card identity cannot arbitrate two retries of the same card. The attempt
    /// token must ensure the second tap owns the state even when the first returns
    /// last.
    @MainActor
    func testLatestRetryWinsForTheSameCard() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("same card")
        let oldSession = UUID()
        let newSession = UUID()
        state.sessionCards = [card]
        api.startStubs[card.id] = [
            .init(
                delay: .milliseconds(100),
                result: .success(Self.start(oldSession, question: "old question"))
            ),
            .init(
                delay: .zero,
                result: .success(Self.start(newSession, question: "new question"))
            ),
        ]

        let older = Task { await state.openCard(card) }
        try? await Task.sleep(for: .milliseconds(10))
        await state.openCard(card)
        await older.value

        XCTAssertEqual(state.sessionID, newSession)
        XCTAssertEqual(state.thread.first?.text, "new question")
    }

    /// Exiting is itself a newer navigation decision. A request that finishes
    /// afterward cannot repopulate the hidden conversation state.
    @MainActor
    func testLeavingInvalidatesAnInFlightQuestionLoad() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("leaving")
        state.sessionCards = [card]
        api.startStubs[card.id] = [
            .init(
                delay: .milliseconds(100),
                result: .success(Self.start(UUID(), question: "late question"))
            )
        ]

        let load = Task { await state.openCard(card) }
        try? await Task.sleep(for: .milliseconds(10))
        state.finish()
        await load.value

        XCTAssertTrue(state.path.isEmpty)
        XCTAssertNil(state.sessionID)
        XCTAssertTrue(state.thread.isEmpty)
        XCTAssertEqual(state.stage, .loadingQuestion)
    }
}
