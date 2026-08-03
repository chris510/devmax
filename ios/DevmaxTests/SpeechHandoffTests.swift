import XCTest
@testable import Devmax

/// A capture that has ended must not hand its transcript to the next turn.
///
/// The bug: `ConversationScreen`'s "Type instead" finalizes the recognizer before
/// swapping input modes, and `finish()` returned the stored `transcript` whenever
/// nothing was recording. Nothing cleared it between turns, so tapping "Type
/// instead" on a follow-up opened the text box already filled with the answer the
/// previous turn had submitted — one Submit away from posting it again.
///
/// Two things made that severe rather than cosmetic. `WC_SIM_SPEECH` types a
/// hardcoded *model* answer into the transcript, so the text handed back was the
/// correct answer to a question the user had not answered yet; and the draft is
/// what reaches `/answers`, so accepting it scored the fixture as recall.
@MainActor
final class SpeechHandoffTests: XCTestCase {
    /// The seam the bug came through. `AppState.draft` is the copy that outlives a
    /// capture, so returning nothing here is what makes the caller fall back to it.
    func testFinishHandsBackNothingWhenNothingIsRecording() async {
        let speech = SpeechService()
        speech.restore("the answer the previous turn already submitted")

        let carried = await speech.finish()

        XCTAssertEqual(carried, "", "A finished capture must not carry into the next turn")
    }

    /// And the state behind it: ending a capture leaves the service empty, so a
    /// later reader has nothing stale to find in the first place.
    func testEndingACaptureClearsTheTranscript() {
        let speech = SpeechService()
        speech.start(continuing: "a spoken answer", simulated: true, simulate: " and the rest")
        XCTAssertEqual(speech.transcript, "a spoken answer", "start(continuing:) seeds the transcript")

        speech.stop()

        XCTAssertTrue(speech.transcript.isEmpty, "An ended capture owns no text")
    }

    /// The carry-over that *is* wanted, so the fix above doesn't quietly take it
    /// away: swapping text → voice restores the draft for the mic to continue.
    func testRestoreSeedsTheNextCapture() {
        let speech = SpeechService()
        speech.restore("half an answer")

        XCTAssertEqual(speech.transcript, "half an answer")
    }
}
