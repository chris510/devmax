import XCTest
@testable import Devmax

private func timed(
    _ text: String,
    from start: TimeInterval,
    to end: TimeInterval
) -> [SpeechTranscriptSegment] {
    [SpeechTranscriptSegment(text: text, audioStart: start, audioEnd: end)]
}

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
    func testPartialCorrectionsReplaceOnlyTheActiveUtterance() {
        var transcript = SpeechTranscriptAccumulator()

        transcript.updateActiveHypothesis(
            "A cash invalidation strategy",
            segments: timed("A cash invalidation strategy", from: 0.4, to: 1.8)
        )
        transcript.updateActiveHypothesis(
            "A cache invalidation strategy",
            segments: timed("A cache invalidation strategy", from: 0.46, to: 1.9)
        )

        XCTAssertEqual(transcript.text, "A cache invalidation strategy")
    }

    func testThinkingPauseKeepsFinalizedWordsWhenSpeechResumes() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis("Retries need an idempotency key")
        transcript.finalizeActiveHypothesis()

        transcript.updateActiveHypothesis("and exponential backoff")

        XCTAssertEqual(
            transcript.text,
            "Retries need an idempotency key and exponential backoff"
        )
    }

    func testTimedLaterUtteranceCannotReplaceEarlierSpeechWithoutFinalCallback() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key",
            segments: timed("Retries need an idempotency key", from: 0.4, to: 2.1)
        )

        // On-device recognition may begin reporting the utterance after a pause
        // without first emitting `isFinal` for the earlier words.
        transcript.updateActiveHypothesis(
            "and exponential backoff",
            segments: timed("and exponential backoff", from: 5.8, to: 7.2)
        )
        transcript.updateActiveHypothesis(
            "and bounded exponential backoff",
            segments: timed("and bounded exponential backoff", from: 5.8, to: 7.7)
        )

        XCTAssertEqual(
            transcript.text,
            "Retries need an idempotency key and bounded exponential backoff"
        )
    }

    func testCumulativeCorrectionAfterTimedResetDoesNotDuplicateEarlierSpeech() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key",
            segments: timed("Retries need an idempotency key", from: 0.4, to: 2.1)
        )
        transcript.updateActiveHypothesis(
            "and exponential backoff",
            segments: timed("and exponential backoff", from: 5.8, to: 7.2)
        )

        // A later result may once again cover the whole audio request.
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key and bounded exponential backoff",
            segments: timed(
                "Retries need an idempotency key and bounded exponential backoff",
                from: 0.4,
                to: 7.7
            )
        )

        XCTAssertEqual(
            transcript.text,
            "Retries need an idempotency key and bounded exponential backoff"
        )
    }

    func testPartiallyOverlappingSnapshotKeepsNonoverlappingEarlierWords() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key",
            segments: [
                .init(text: "Retries", audioStart: 0.4, audioEnd: 0.7),
                .init(text: "need", audioStart: 0.8, audioEnd: 1.0),
                .init(text: "an", audioStart: 1.08, audioEnd: 1.2),
                .init(text: "idempotency", audioStart: 1.3, audioEnd: 1.8),
                .init(text: "key", audioStart: 1.9, audioEnd: 2.1)
            ]
        )

        transcript.updateActiveHypothesis(
            "idempotency key and backoff",
            segments: [
                .init(text: "idempotency", audioStart: 1.3, audioEnd: 1.8),
                .init(text: "key", audioStart: 1.9, audioEnd: 2.1),
                .init(text: "and", audioStart: 2.2, audioEnd: 2.35),
                .init(text: "backoff", audioStart: 2.45, audioEnd: 3.0)
            ]
        )

        XCTAssertEqual(transcript.text, "Retries need an idempotency key and backoff")
    }

    func testAdjacentSnapshotDoesNotDeletePreviousWord() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries",
            segments: timed("Retries", from: 0.4, to: 1.0)
        )
        transcript.updateActiveHypothesis(
            "need backoff",
            segments: timed("need backoff", from: 1.05, to: 2.0)
        )

        XCTAssertEqual(transcript.text, "Retries need backoff")
    }

    func testSlightTimestampOverlapBetweenAdjacentWordsDoesNotDeletePreviousWord() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries",
            segments: timed("Retries", from: 0.4, to: 1.02)
        )
        transcript.updateActiveHypothesis(
            "need backoff",
            segments: timed("need backoff", from: 1.0, to: 2.0)
        )

        XCTAssertEqual(transcript.text, "Retries need backoff")
    }

    func testMissingTimestampsCannotEraseTimedSpeech() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key",
            segments: timed("Retries need an idempotency key", from: 0.4, to: 2.1)
        )

        transcript.updateActiveHypothesis("and exponential backoff", segments: [])

        XCTAssertEqual(transcript.text, "Retries need an idempotency key")
    }

    func testOutOfOrderNonoverlappingSnapshotsKeepBothUtterances() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis(
            "and bounded backoff",
            segments: timed("and bounded backoff", from: 5.8, to: 7.2)
        )
        transcript.updateActiveHypothesis(
            "Retries need an idempotency key",
            segments: timed("Retries need an idempotency key", from: 0.4, to: 2.1)
        )

        XCTAssertEqual(
            transcript.text,
            "Retries need an idempotency key and bounded backoff"
        )
    }

    func testPostPauseCorrectionCannotRewriteEarlierSpeech() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis("The write is committed first")
        transcript.finalizeActiveHypothesis()
        transcript.updateActiveHypothesis("then publish the cash")
        transcript.updateActiveHypothesis("then publish the cache")

        XCTAssertEqual(
            transcript.text,
            "The write is committed first then publish the cache"
        )
    }

    func testFinalCorrectionWinsBeforeUtteranceIsCommitted() {
        var transcript = SpeechTranscriptAccumulator()
        transcript.updateActiveHypothesis("then publish the cash")

        transcript.updateActiveHypothesis("then publish the cache")
        transcript.finalizeActiveHypothesis()

        XCTAssertEqual(transcript.text, "then publish the cache")
    }

    func testMultiplePausesPreserveDraftAndEveryFinalizedUtterance() {
        var transcript = SpeechTranscriptAccumulator(continuing: "A saved opening")
        transcript.updateActiveHypothesis("continues into the first phrase")
        transcript.finalizeActiveHypothesis()
        transcript.updateActiveHypothesis("and survives another pause")
        transcript.finalizeActiveHypothesis()
        transcript.updateActiveHypothesis("before the final thought")

        XCTAssertEqual(
            transcript.text,
            "A saved opening continues into the first phrase and survives another pause before the final thought"
        )

        transcript.updateActiveHypothesis("")
        transcript.updateActiveHypothesis("   ")
        transcript.finalizeActiveHypothesis()
        XCTAssertEqual(
            transcript.text,
            "A saved opening continues into the first phrase and survives another pause before the final thought"
        )
    }

    func testAutomaticFinalRollsOverWhileStopFinalCompletes() {
        XCTAssertEqual(
            SpeechService.recognitionBoundary(
                captureState: .recording,
                isFinal: true,
                hasError: false
            ),
            .rollOver
        )
        XCTAssertEqual(
            SpeechService.recognitionBoundary(
                captureState: .finalizing,
                isFinal: true,
                hasError: false
            ),
            .complete(.final)
        )
    }

    func testStopAfterFinalizedUtteranceAcceptsSilentTail() {
        XCTAssertEqual(
            SpeechService.recognitionBoundary(
                captureState: .finalizing,
                isFinal: false,
                hasError: true,
                hasFinalizedHypothesis: true,
                hasActiveHypothesis: false
            ),
            .complete(.final)
        )
        XCTAssertEqual(
            SpeechService.recognitionBoundary(
                captureState: .finalizing,
                isFinal: false,
                hasError: true,
                hasFinalizedHypothesis: true,
                hasActiveHypothesis: true
            ),
            .complete(.failed)
        )
    }

    func testSilentTaskFailureRestartsAfterFinalizedUtterance() {
        XCTAssertEqual(
            SpeechService.recognitionBoundary(
                captureState: .recording,
                isFinal: false,
                hasError: true,
                hasFinalizedHypothesis: true,
                hasActiveHypothesis: false
            ),
            .retryAfterSilence
        )
    }

    /// The seam the bug came through. `AppState.draft` is the copy that outlives a
    /// capture, so returning nothing here is what makes the caller fall back to it.
    func testFinishHandsBackNothingWhenNothingIsRecording() async {
        let speech = SpeechService()
        speech.restore("the answer the previous turn already submitted")

        let carried = await speech.finish()

        XCTAssertEqual(carried, "", "A finished capture must not carry into the next turn")
    }

    func testNoCaptureIsNotSafeToSubmit() async {
        let speech = SpeechService()

        let result = await speech.finishResult()

        XCTAssertEqual(result.status, .noCapture)
        XCTAssertFalse(result.isSafeToSubmit)
    }

    func testSimulatedFinalizationIsSafeAndClearsCaptureState() async {
        let speech = SpeechService()
        speech.start(continuing: "a spoken answer", simulated: true, simulate: " and more")

        let result = await speech.finishResult()

        XCTAssertEqual(result.status, .final)
        XCTAssertEqual(result.text, "a spoken answer")
        XCTAssertTrue(result.isSafeToSubmit)
        XCTAssertEqual(speech.captureState, .idle)
        XCTAssertTrue(speech.transcript.isEmpty)
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
}

final class SpeechVocabularyTests: XCTestCase {
    func testVocabularyBiasesTheExactTechnicalPhraseAndCurrentPrompt() {
        let terms = SpeechVocabulary.terms(
            for: "Timeouts, retries, and idempotency",
            prompt: "What should the payment server do with the same idempotency key?"
        )

        XCTAssertTrue(terms.contains("idempotency key"))
        XCTAssertTrue(terms.contains("payment"))
        XCTAssertTrue(terms.contains("server"))
        XCTAssertLessThanOrEqual(terms.count, 100)
    }
}
