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
        var saveDraftStartedCalls: [String] = []
        var saveDraftCancellationStates: [Bool] = []
        var saveDraftTurnIndexes: [Int] = []
        var saveDraftError: APIError?
        var saveDraftDelay = Duration.zero
        var holdNextDraft = false
        private var heldDraftContinuation: CheckedContinuation<Void, Never>?
        var answerTurnIndexes: [Int] = []
        var registeredDeviceTokens: [String] = []
        var startedDeviceTokens: [String] = []
        var holdNextDeviceToken = false
        private var heldDeviceTokenContinuation: CheckedContinuation<Void, Never>?
        var answerDelay = Duration.zero
        var stubbedAnswer: AnswerOutcome?
        var dueResult: Result<[DueCard], APIError> = .success([])
        var dueCallCount = 0
        var holdNextDue = false
        private var heldDueContinuation:
            CheckedContinuation<Result<[DueCard], APIError>, Never>?
        var startStubs: [UUID: [StartStub]] = [:]

        func submitAnswer(
            sessionID: UUID, text: String, turnIndex: Int
        ) async throws -> AnswerOutcome {
            if answerDelay > .zero { try await Task.sleep(for: answerDelay) }
            answerTurnIndexes.append(turnIndex)
            answerCalls.append(text)
            if let stubbedAnswer { return stubbedAnswer }
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

        func due() async throws -> [DueCard] {
            dueCallCount += 1
            if holdNextDue {
                holdNextDue = false
                let result = await withCheckedContinuation { continuation in
                    heldDueContinuation = continuation
                }
                return try result.get()
            }
            return try dueResult.get()
        }
        func releaseHeldDue(_ result: Result<[DueCard], APIError>) {
            heldDueContinuation?.resume(returning: result)
            heldDueContinuation = nil
        }
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
        func saveDraft(sessionID: UUID, text: String, turnIndex: Int) async throws {
            saveDraftTurnIndexes.append(turnIndex)
            saveDraftStartedCalls.append(text)
            if holdNextDraft {
                holdNextDraft = false
                await withCheckedContinuation { continuation in
                    heldDraftContinuation = continuation
                }
            }
            if saveDraftDelay > .zero { try await Task.sleep(for: saveDraftDelay) }
            saveDraftCancellationStates.append(Task.isCancelled)
            saveDraftCalls.append(text)
            if let saveDraftError { throw saveDraftError }
        }
        func releaseHeldDraft() {
            heldDraftContinuation?.resume()
            heldDraftContinuation = nil
        }
        func settings() async throws -> AppSettings { throw CancellationError() }
        func updateSettings(_ settings: AppSettings) async throws -> AppSettings {
            throw CancellationError()
        }
        func registerDeviceToken(_ token: String) async throws {
            startedDeviceTokens.append(token)
            if holdNextDeviceToken {
                holdNextDeviceToken = false
                await withCheckedContinuation { continuation in
                    heldDeviceTokenContinuation = continuation
                }
            }
            registeredDeviceTokens.append(token)
        }
        func releaseHeldDeviceToken() {
            heldDeviceTokenContinuation?.resume()
            heldDeviceTokenContinuation = nil
        }

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
    private func waitUntil(
        timeout: Duration = .seconds(1), _ condition: () -> Bool
    ) async {
        let clock = ContinuousClock()
        let deadline = clock.now.advanced(by: timeout)
        while !condition(), clock.now < deadline {
            try? await Task.sleep(for: .milliseconds(1))
        }
    }

    @MainActor
    func testSpokenReattemptGoesToTheReattemptEndpoint() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        state.sessionCards = [Self.card]
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
        state.sessionCards = [Self.card]
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
        state.sessionCards = [Self.card]
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
    func testSubmissionFlushCancelsDraftUploadRaceButKeepsLocalCopy() async throws {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Draft race")
        state.sessionCards = [card]
        state.sessionID = UUID()
        state.updateDraft("the final spoken transcript")
        defer { DraftStore.clear(for: card.id) }

        await state.flushDraftForSubmission()

        XCTAssertEqual(
            DraftStore.read(
                for: card.id, sessionID: try XCTUnwrap(state.sessionID), turnIndex: 0
            ),
            "the final spoken transcript"
        )
        XCTAssertTrue(api.saveDraftCalls.isEmpty, "a late PATCH can repopulate the next turn")
    }

    @MainActor
    func testSubmitOwnsTheBarrierTurnIndexAndDoubleTapGate() async {
        let api = SpyAPI()
        api.answerDelay = .milliseconds(75)
        let state = AppState(api: api)
        let card = Self.card("Double submit")
        state.sessionCards = [card]
        state.sessionID = UUID()
        state.stage = .idle
        state.updateDraft("one honest answer")
        defer { DraftStore.clear(for: card.id) }

        let first = Task { await state.submit("one honest answer") }
        await waitUntil { state.submissionPending }
        let duplicate = Task { await state.submit("one honest answer") }
        await duplicate.value
        await first.value

        XCTAssertEqual(api.answerCalls, ["one honest answer"])
        XCTAssertEqual(api.answerTurnIndexes, [0])
        XCTAssertFalse(state.submissionPending)
    }

    @MainActor
    func testFollowUpAdvancesTheTurnUsedByDraftsAndAnswers() async {
        let api = SpyAPI()
        api.stubbedAnswer = .followUp(question: "One more — why?", turnIndex: 1)
        let state = AppState(api: api)
        let card = Self.card("Indexed probe")
        state.sessionCards = [card]
        state.sessionID = UUID()
        state.stage = .idle
        state.updateDraft("opening answer")
        defer { DraftStore.clear(for: card.id) }

        await state.submit("opening answer")
        XCTAssertEqual(state.answerTurnIndex, 1)
        XCTAssertEqual(state.stage, .followUp)
        XCTAssertEqual(api.answerTurnIndexes, [0])

        state.updateDraft("probe draft")
        state.flushDraft()
        await waitUntil { api.saveDraftTurnIndexes.last == 1 }
        XCTAssertEqual(api.saveDraftTurnIndexes.last, 1)
    }

    @MainActor
    func testPriorTurnDiskDraftDoesNotHydrateAnAdvancedProbe() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Stale probe")
        let sessionID = UUID()
        state.sessionCards = [card]
        api.startStubs[card.id] = [
            .init(
                delay: .zero,
                result: .success(
                    SessionStart(
                        sessionId: sessionID, question: "probe", isFollowUp: true,
                        draftText: "", resumed: true, turnIndex: 1
                    )
                )
            )
        ]
        DraftStore.save(
            "opening answer", for: card.id, sessionID: sessionID, turnIndex: 0
        )
        defer { DraftStore.clear(for: card.id) }

        await state.openCard(card)

        XCTAssertEqual(state.answerTurnIndex, 1)
        XCTAssertFalse(state.resumeAvailable)
        XCTAssertTrue(state.storedPartial.isEmpty)
    }

    @MainActor
    func testLateFinalizedSpeechCannotPopulateANewerTurn() throws {
        let state = AppState(api: SpyAPI())
        let card = Self.card("Turn ownership")
        let firstSession = UUID()
        state.sessionCards = [card]
        state.sessionID = firstSession
        let firstTurn = try XCTUnwrap(state.conversationIdentity)

        // Moving to a new session is enough to make the old finalization stale.
        state.sessionID = UUID()
        state.updateDraft("new turn words")
        let accepted = state.acceptFinalizedDraft("old turn tail", from: firstTurn)
        defer { DraftStore.clear(for: card.id) }

        XCTAssertFalse(accepted)
        XCTAssertEqual(state.draft, "new turn words")
        XCTAssertNil(
            DraftStore.read(for: card.id, sessionID: firstSession, turnIndex: 0),
            "the newer contextual draft wins the card's disk slot"
        )
    }

    @MainActor
    func testStartOverClearsTheExactServerTurn() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Discard")
        let sessionID = UUID()
        state.sessionCards = [card]
        state.sessionID = sessionID
        state.storedPartial = "discard me"
        state.resumeAvailable = true
        DraftStore.save("discard me", for: card.id, sessionID: sessionID, turnIndex: 0)
        defer { DraftStore.clear(for: card.id) }

        await state.startOver()

        XCTAssertEqual(api.saveDraftCalls.last, "")
        XCTAssertEqual(api.saveDraftTurnIndexes.last, 0)
        XCTAssertFalse(state.resumeAvailable)
        XCTAssertFalse(state.draftResetPending)
        XCTAssertNil(DraftStore.read(for: card.id, sessionID: sessionID, turnIndex: 0))
    }

    @MainActor
    func testFailedStartOverClearLeavesAContextualTombstone() async {
        let api = SpyAPI()
        api.saveDraftError = .status(503)
        let state = AppState(api: api)
        let card = Self.card("Offline discard")
        let sessionID = UUID()
        state.sessionCards = [card]
        state.sessionID = sessionID
        state.storedPartial = "never resurrect this"
        state.resumeAvailable = true
        defer { DraftStore.clear(for: card.id) }

        await state.startOver()

        XCTAssertTrue(
            DraftStore.isDiscarded(for: card.id, sessionID: sessionID, turnIndex: 0)
        )
        XCTAssertFalse(state.resumeAvailable)
    }

    @MainActor
    func testBackgroundFlushCannotRemoveAStartOverTombstone() async {
        let api = SpyAPI()
        api.saveDraftDelay = .milliseconds(50)
        api.saveDraftError = .status(503)
        let state = AppState(api: api)
        let card = Self.card("Discard while backgrounding")
        let sessionID = UUID()
        state.sessionCards = [card]
        state.sessionID = sessionID
        state.stage = .idle
        DraftStore.save("discard me", for: card.id, sessionID: sessionID, turnIndex: 0)
        defer { DraftStore.clear(for: card.id) }

        let reset = Task { await state.startOver() }
        await waitUntil { state.draftResetPending }
        state.flushDraft()
        await reset.value

        XCTAssertTrue(
            DraftStore.isDiscarded(for: card.id, sessionID: sessionID, turnIndex: 0),
            "an app lifecycle flush must preserve an offline discard"
        )
    }

    @MainActor
    func testStartOverWaitsForAnActuallyStartedDraftUploadBeforeClearing() async {
        let api = SpyAPI()
        api.holdNextDraft = true
        let state = AppState(api: api)
        let card = Self.card("Ordered discard")
        let sessionID = UUID()
        state.sessionCards = [card]
        state.sessionID = sessionID
        state.stage = .idle
        state.updateDraft("older spoken answer")
        state.flushDraft()
        defer {
            api.releaseHeldDraft()
            DraftStore.clear(for: card.id)
        }
        await waitUntil { !api.saveDraftStartedCalls.isEmpty }
        XCTAssertEqual(api.saveDraftStartedCalls, ["older spoken answer"])

        let reset = Task { await state.startOver() }
        await waitUntil { state.draftResetPending }
        for _ in 0..<50 { await Task.yield() }
        XCTAssertEqual(
            api.saveDraftStartedCalls, ["older spoken answer"],
            "the empty PATCH must wait for the accepted same-turn write"
        )

        api.releaseHeldDraft()
        await reset.value

        XCTAssertEqual(api.saveDraftStartedCalls, ["older spoken answer", ""])
        XCTAssertEqual(api.saveDraftCalls, ["older spoken answer", ""])
        XCTAssertEqual(
            api.saveDraftCancellationStates, [false, false],
            "cancelling the debounce wrapper must not cancel an upload already in flight"
        )
        XCTAssertFalse(
            DraftStore.isDiscarded(for: card.id, sessionID: sessionID, turnIndex: 0)
        )
    }

    func testLegacyDraftMigrationRequiresResumedSessionAndTurnEvidence() {
        let resumedCard = UUID()
        let freshCard = UUID()
        let ambiguousProbeCard = UUID()
        let corroboratedProbeCard = UUID()
        let sessionID = UUID()
        LocalJSONStore.save(
            [
                resumedCard.uuidString: "legacy spoken words",
                freshCard.uuidString: "stale words",
                ambiguousProbeCard.uuidString: "old opening answer",
                corroboratedProbeCard.uuidString: "current probe draft"
            ],
            to: "drafts.json"
        )
        defer {
            DraftStore.clear(for: resumedCard)
            DraftStore.clear(for: freshCard)
            DraftStore.clear(for: ambiguousProbeCard)
            DraftStore.clear(for: corroboratedProbeCard)
        }

        XCTAssertEqual(
            DraftStore.adoptLegacy(
                for: resumedCard, sessionID: sessionID, turnIndex: 0,
                sessionResumed: true, serverDraftText: ""
            ),
            "legacy spoken words"
        )
        XCTAssertEqual(
            DraftStore.read(for: resumedCard, sessionID: sessionID, turnIndex: 0),
            "legacy spoken words"
        )
        XCTAssertNil(
            DraftStore.adoptLegacy(
                for: freshCard, sessionID: UUID(), turnIndex: 0,
                sessionResumed: false, serverDraftText: ""
            )
        )
        XCTAssertNil(
            DraftStore.adoptLegacy(
                for: ambiguousProbeCard, sessionID: sessionID, turnIndex: 1,
                sessionResumed: true, serverDraftText: "different current probe"
            ),
            "an unindexed opening answer must not be rebound to a later probe"
        )
        XCTAssertEqual(
            DraftStore.adoptLegacy(
                for: corroboratedProbeCard, sessionID: sessionID, turnIndex: 1,
                sessionResumed: true, serverDraftText: "current probe draft"
            ),
            "current probe draft"
        )
    }

    @MainActor
    func testPostResultDraftsPersistLocallyWithoutPatchingACompletedSession() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Local coached draft")
        let sessionID = UUID()
        state.sessionCards = [card]
        state.sessionID = sessionID
        state.stage = .reattempt
        defer { DraftStore.clear(for: card.id) }

        state.updateDraft("a coached correction")
        state.flushDraft()

        XCTAssertEqual(
            DraftStore.read(for: card.id, sessionID: sessionID, turnIndex: 0),
            "a coached correction"
        )
        XCTAssertTrue(
            api.saveDraftCalls.isEmpty,
            "completed sessions have no resumable server-draft coordinate"
        )
    }

    @MainActor
    func testColdPushAndTokenWaitThroughSignedOutBootstrapUntilAuthentication() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Cold push")
        state.queue = [card]
        api.dueResult = .success([card])
        let delegate = AppDelegate()

        delegate.receiveNotificationCard(card.id)
        delegate.receiveDeviceToken("cold-token")
        delegate.attach(state)
        delegate.setRoutingAuthenticated(false)
        for _ in 0..<50 { await Task.yield() }
        XCTAssertTrue(state.path.isEmpty)
        XCTAssertTrue(api.registeredDeviceTokens.isEmpty)

        delegate.setRoutingAuthenticated(true)
        await waitUntil {
            !state.path.isEmpty && api.registeredDeviceTokens == ["cold-token"]
        }
        delegate.setRoutingAuthenticated(true)
        await Task.yield()

        XCTAssertEqual(state.path, [.conversation(card.id)])
        XCTAssertEqual(api.registeredDeviceTokens, ["cold-token"])
    }

    @MainActor
    func testFailedColdPushLoadRetainsTheTapUntilAReadyQueue() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Retry push")
        api.dueResult = .failure(.status(503))
        let delegate = AppDelegate()
        delegate.receiveNotificationCard(card.id)
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)
        await waitUntil { state.load == .error }
        XCTAssertTrue(state.path.isEmpty)

        api.dueResult = .success([card])
        await state.loadToday()
        await waitUntil { !state.path.isEmpty }

        XCTAssertEqual(state.path, [.conversation(card.id)])
    }

    @MainActor
    func testPendingPushReloadsAfterOldAccountLoadCompletesWhileSignedOut() async {
        let api = SpyAPI()
        api.holdNextDue = true
        let state = AppState(api: api)
        let accountACard = Self.card("Account A card")
        let accountBCard = DueCard(
            id: accountACard.id, topic: "Account B card", category: "Core Concept",
            masterySummary: "", lastScore: nil, dueLabel: "due today",
            resumable: false, missedCount: 0
        )
        let delegate = AppDelegate()
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)
        delegate.receiveNotificationCard(accountACard.id)
        defer { api.releaseHeldDue(.success([accountACard])) }
        await waitUntil { api.dueCallCount > 0 }
        XCTAssertEqual(api.dueCallCount, 1)

        // Account A's request finishes after sign-out. B has a card with the same
        // identifier, so trusting the retained A queue at B activation would
        // appear superficially valid and route the wrong account's content.
        delegate.setRoutingAuthenticated(false)
        api.dueResult = .success([accountBCard])
        api.releaseHeldDue(.success([accountACard]))
        await waitUntil { state.queue.first?.topic == accountACard.topic }
        XCTAssertTrue(state.path.isEmpty)
        XCTAssertEqual(api.dueCallCount, 1)

        delegate.setRoutingAuthenticated(true)
        await waitUntil(timeout: .seconds(2)) {
            state.path == [.conversation(accountBCard.id)]
                && state.currentCard?.topic == accountBCard.topic
        }

        XCTAssertEqual(api.dueCallCount, 2, "account B must get a fresh Today load")
        XCTAssertEqual(state.path, [.conversation(accountBCard.id)])
        XCTAssertEqual(state.currentCard?.topic, "Account B card")
    }

    @MainActor
    func testDeviceTokenIsRegisteredAgainAfterSignOutDuringItsUpload() async {
        let api = SpyAPI()
        api.holdNextDeviceToken = true
        let state = AppState(api: api)
        let delegate = AppDelegate()
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)
        delegate.receiveDeviceToken("account-changing-token")
        defer { api.releaseHeldDeviceToken() }
        await waitUntil { !api.startedDeviceTokens.isEmpty }
        XCTAssertEqual(api.startedDeviceTokens, ["account-changing-token"])

        delegate.setRoutingAuthenticated(false)
        api.releaseHeldDeviceToken()
        await waitUntil { !api.registeredDeviceTokens.isEmpty }
        XCTAssertEqual(api.registeredDeviceTokens, ["account-changing-token"])

        delegate.setRoutingAuthenticated(true)
        await waitUntil { api.registeredDeviceTokens.count >= 2 }

        XCTAssertEqual(
            api.registeredDeviceTokens,
            ["account-changing-token", "account-changing-token"],
            "the old account's success must not consume the next account's token"
        )
    }

    @MainActor
    func testForegroundPushWaitsForTheActiveAnswerToExit() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let active = Self.card("Answer in progress")
        let pushed = Self.card("Pushed next")
        api.dueResult = .success([pushed])
        state.queue = [pushed]
        state.sessionCards = [active]
        state.sessionID = UUID()
        state.stage = .recording
        state.path = [.conversation(active.id)]
        let delegate = AppDelegate()
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)

        delegate.receiveNotificationCard(pushed.id)
        for _ in 0..<50 { await Task.yield() }

        XCTAssertEqual(state.path, [.conversation(active.id)])
        XCTAssertEqual(state.currentCard?.id, active.id)
        XCTAssertEqual(state.stage, .recording)

        state.finish()
        await waitUntil { state.path == [.conversation(pushed.id)] }

        XCTAssertEqual(state.path, [.conversation(pushed.id)])
        XCTAssertEqual(state.currentCard?.id, pushed.id)
    }

    @MainActor
    func testForegroundPushWaitsForAResultConversationToExit() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let active = Self.card("Scored card")
        let pushed = Self.card("Pushed after score")
        api.dueResult = .success([pushed])
        state.queue = [pushed]
        state.sessionCards = [active]
        state.sessionID = UUID()
        state.stage = .result
        state.path = [.conversation(active.id)]
        let delegate = AppDelegate()
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)

        delegate.receiveNotificationCard(pushed.id)
        for _ in 0..<50 { await Task.yield() }

        XCTAssertEqual(state.path, [.conversation(active.id)])
        XCTAssertEqual(state.currentCard?.id, active.id)

        state.path = []
        await waitUntil { state.path == [.conversation(pushed.id)] }

        XCTAssertEqual(state.path, [.conversation(pushed.id)])
        XCTAssertEqual(state.currentCard?.id, pushed.id)
    }

    @MainActor
    func testForegroundPushWaitsForHistoryAboveAResultConversationToExit() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let active = Self.card("Scored card with history")
        let pushed = Self.card("Pushed after history")
        api.dueResult = .success([pushed])
        state.queue = [pushed]
        state.sessionCards = [active]
        state.sessionID = UUID()
        state.stage = .result
        state.path = [.conversation(active.id), .history(active.id)]
        let delegate = AppDelegate()
        delegate.attach(state)
        delegate.setRoutingAuthenticated(true)

        delegate.receiveNotificationCard(pushed.id)
        for _ in 0..<50 { await Task.yield() }

        XCTAssertEqual(state.path, [.conversation(active.id), .history(active.id)])
        XCTAssertEqual(state.currentCard?.id, active.id)

        state.path = []
        await waitUntil { state.path == [.conversation(pushed.id)] }

        XCTAssertEqual(state.path, [.conversation(pushed.id)])
        XCTAssertEqual(state.currentCard?.id, pushed.id)
    }

    @MainActor
    func testInteractiveBackPopReleasesConversationOwnership() async {
        let api = SpyAPI()
        let state = AppState(api: api)
        let card = Self.card("Back-swiped answer")
        state.sessionCards = [card]
        state.sessionID = UUID()
        state.stage = .recording
        state.path = [.conversation(card.id)]

        // SwiftUI commits the empty path before Conversation's deferred
        // onDisappear recovery runs.
        state.path = []
        state.finishConversationAfterNavigationPopIfNeeded()

        XCTAssertTrue(state.path.isEmpty)
        XCTAssertNil(state.sessionID)
        XCTAssertTrue(state.canBeginSession)
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
        XCTAssertEqual(Stage.loadingQuestion.footer, .hidden)
        XCTAssertEqual(Stage.loadingQuestion.recordingTwin, .loadingQuestion)
        XCTAssertEqual(Stage.loadingQuestion.answeringTwin, .loadingQuestion)
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
