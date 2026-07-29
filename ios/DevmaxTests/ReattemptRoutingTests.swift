import XCTest
@testable import Devmax

/// Turn 3 must reach `POST /sessions/{id}/reattempt`, never `/answers`.
///
/// Both tests here are regressions for real bugs. The routing test is for one that
/// nearly shipped: `ConversationScreen` briefly set `stage = .processing` before
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
}
