import Dispatch
import SwiftUI
import XCTest
@testable import Devmax

private enum ProcessingTransportError: Error {
    case unexpectedRequest
}

/// Serves the submit response, and — because `startLoading` runs on the loading
/// thread rather than the main actor — lets a test hold that response open while
/// it reads the state the screen is showing meanwhile.
private final class ProcessingURLProtocolStub: URLProtocol, @unchecked Sendable {
    static var handler: (@Sendable (URLRequest) throws -> (HTTPURLResponse, Data))?

    override static func canInit(with request: URLRequest) -> Bool { true }
    override static func canonicalRequest(for request: URLRequest) -> URLRequest { request }

    override func startLoading() {
        do {
            guard let handler = Self.handler else { throw ProcessingTransportError.unexpectedRequest }
            let (response, data) = try handler(request)
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        } catch {
            client?.urlProtocol(self, didFailWithError: error)
        }
    }

    override func stopLoading() {}
}

/// What the app shows while an answer is being scored.
///
/// Three things diverged from the prototype at once, and all three read to the
/// user as "it asked me the same question again": the question was re-spoken the
/// moment the SCORING dots appeared, the submitted answer was drawn a second time
/// with a live caret beneath it, and the mic stayed on screen offering to keep
/// going. The prototype commits the answer and the draft in one step
/// (`Devmax.dc.html` `commit`) and shows neither input while `stage === "proc"`.
final class ProcessingStateTests: XCTestCase {
    override func tearDown() {
        ProcessingURLProtocolStub.handler = nil
        super.tearDown()
    }

    private func api() -> LiveAPI {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [ProcessingURLProtocolStub.self]
        return LiveAPI(
            baseURL: URL(string: "https://example.test")!, apiKey: "test-key",
            session: URLSession(configuration: configuration),
            tokenStore: AuthTokenStore(persistence: nil)
        )
    }

    private func response(
        for request: URLRequest, status: Int, json: String
    ) -> (HTTPURLResponse, Data) {
        (
            HTTPURLResponse(
                url: request.url!, statusCode: status, httpVersion: nil,
                headerFields: ["Content-Type": "application/json"]
            )!,
            Data(json.utf8)
        )
    }

    private static let completedAnswer = """
    {"status":"complete","score":3,"feedback":"Ring mechanics held.",
     "next_review_at":"2026-07-30","interval_days":3,"practice":false}
    """

    private static let card = DueCard(
        id: UUID(), topic: "Consistent hashing", category: "Core Concept",
        masterySummary: "", lastScore: nil, dueLabel: "due today",
        resumable: false, missedCount: 0
    )

    /// Yields until the state under test settles, bounded so a broken expectation
    /// fails the test rather than hanging the suite.
    @MainActor
    private func settle(
        _ message: String, until reached: () -> Bool
    ) async {
        for _ in 0..<1_000 {
            if reached() { return }
            await Task.yield()
        }
        XCTFail(message)
    }

    // MARK: - The footer

    /// `.processing` is not a stage with no session — the answer is in flight and
    /// ✕ is still live — but it accepts nothing, so it shows no control either.
    func testNoAnswerControlIsShownWhileScoring() {
        XCTAssertEqual(Stage.processing.footer, .hidden)
        XCTAssertFalse(Stage.processing.acceptsAnswer)
        XCTAssertEqual(Stage.questionFailed("SERVER UNREACHABLE").footer, .hidden)
        XCTAssertEqual(Stage.result.footer, .result)
        XCTAssertEqual(Stage.idle.footer, .answer)
        XCTAssertEqual(Stage.followUp.footer, .answer)
        XCTAssertEqual(Stage.recordingFollowUp.footer, .answer)
    }

    // MARK: - What is read aloud

    private func entry(_ role: ThreadEntry.Role, _ text: String) -> ThreadEntry {
        ThreadEntry(role: role, text: text)
    }

    @MainActor
    func testAnAnswerIsNeverWhatGetsSpoken() {
        let question = entry(.question, "Walk me through what data moves.")
        let thread = [question, entry(.answer, "the user's own words, spoken back")]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, question.id)
    }

    /// The regression: submitting appended an answer and the thread-count observer
    /// read the question again over the SCORING dots. The prompt observer must not
    /// change for an answer-only mutation.
    @MainActor
    func testAppendingAnAnswerDoesNotChangeTheObservedPrompt() {
        let question = entry(.question, "Walk me through what data moves.")
        var thread = [question]
        let observed = thread.latestSpokenPrompt

        thread.append(entry(.answer, "an answer under way"))

        XCTAssertEqual(thread.latestSpokenPrompt, observed)
    }

    /// Coaching feedback is prose stating the answer, not a question — and the
    /// score block already shows it.
    @MainActor
    func testCoachingFeedbackIsNotSpeakable() {
        let probe = entry(.coachingQuestion, "One level deeper: why does it hold?")
        let thread = [
            entry(.question, "Walk me through what data moves."),
            entry(.answer, "an answer"),
            probe,
            entry(.answer, "a deeper answer"),
            entry(.coachingFeedback, "The causal link is explicit."),
        ]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, probe.id)
    }

    @MainActor
    func testANewProbeIsSpokenEvenThoughTheQuestionWasSpokenBefore() {
        let question = entry(.question, "Walk me through what data moves.")
        let probe = entry(.followUpQuestion, "One more: how do virtual nodes change it?")
        let thread = [question, entry(.answer, "a first answer"), probe]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, probe.id)
    }

    @MainActor
    func testTheNewestSpeakableEntryWins() {
        let reattempt = entry(.reattemptQuestion, "In your words: what moves?")
        let thread = [
            entry(.question, "Walk me through what data moves."),
            entry(.answer, "a first answer"),
            entry(.followUpQuestion, "One more: and virtual nodes?"),
            entry(.answer, "a second answer"),
            reattempt,
        ]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, reattempt.id)
    }

    @MainActor
    func testAThreadWithNothingToAskSpeaksNothing() {
        XCTAssertNil([ThreadEntry]().latestSpokenPrompt)
        XCTAssertNil([entry(.answer, "an answer with no question above it")].latestSpokenPrompt)
    }

    // MARK: - The draft during a submit

    /// The duplicated answer: the thread carries the committed answer, so a draft
    /// that outlives the commit renders the same words again with a live caret.
    @MainActor
    func testTheDraftIsClearedAtCommitRatherThanOnTheResponse() async {
        let release = DispatchSemaphore(value: 0)
        ProcessingURLProtocolStub.handler = { [self] request in
            // Blocks the loading thread, never the main actor — so the in-flight
            // state stays observable for exactly as long as the assertions need.
            if request.url?.path.contains("/answers") == true {
                release.wait()
                return response(for: request, status: 200, json: Self.completedAnswer)
            }
            // A previous lifecycle test may still be completing its intentionally
            // detached Today refresh. Never let that unrelated request consume
            // this answer test's semaphore.
            return response(for: request, status: 503, json: #"{"detail":"offline"}"#)
        }
        defer { release.signal() }

        let state = AppState(api: api())
        state.sessionCards = [Self.card]
        state.sessionID = UUID()
        state.stage = .idle
        state.draft = "the answer as spoken"

        let submit = Task { await state.submit("the answer as spoken") }
        await settle("the submit never reached .processing") { state.stage == .processing }

        XCTAssertTrue(state.draft.isEmpty, "a surviving draft draws the answer a second time")
        XCTAssertEqual(state.thread.last?.role, .answer)
        XCTAssertEqual(state.thread.last?.text, "the answer as spoken")

        release.signal()
        await submit.value

        XCTAssertEqual(state.stage, .result)
        XCTAssertTrue(state.draft.isEmpty)
    }

    /// The other half of the same move: clearing early may not cost the answer.
    /// Losing a spoken answer is the worst failure mode in the product.
    @MainActor
    func testAFailedSubmitRestoresTheAnswerVerbatim() async {
        ProcessingURLProtocolStub.handler = { [self] request in
            response(for: request, status: 503, json: #"{"detail":"scoring unavailable"}"#)
        }

        let state = AppState(api: api())
        state.sessionCards = [Self.card]
        state.sessionID = UUID()
        state.stage = .recordingFollowUp
        state.draft = "  a partly spoken answer  "

        await state.submit("  a partly spoken answer  ")

        XCTAssertEqual(state.draft, "  a partly spoken answer  ", "verbatim, spacing included")
        XCTAssertEqual(state.stage, .followUp, "rewound to the turn the answer belonged to")
        XCTAssertTrue(state.submitError)
        XCTAssertTrue(state.thread.isEmpty, "the optimistic answer is taken back")
    }

    @MainActor
    func testClosingDuringScoringDefersTodayReloadUntilTheWriteSettles() async {
        let release = DispatchSemaphore(value: 0)
        ProcessingURLProtocolStub.handler = { [self] request in
            if request.url?.path.contains("/answers") == true {
                release.wait()
                return response(for: request, status: 200, json: Self.completedAnswer)
            }
            return response(for: request, status: 503, json: #"{"detail":"offline"}"#)
        }
        defer { release.signal(); DraftStore.clear(for: Self.card.id) }

        let state = AppState(api: api())
        state.sessionCards = [Self.card]
        state.sessionID = UUID()
        state.stage = .idle
        state.load = .ready
        state.draft = "answer in flight"

        let submit = Task { await state.submit("answer in flight") }
        await settle("the submit never reached scoring") { state.stage == .processing }
        state.finish()
        for _ in 0..<20 { await Task.yield() }

        XCTAssertTrue(state.path.isEmpty)
        XCTAssertNil(state.sessionID)
        XCTAssertEqual(state.load, .ready, "a pre-commit reload can show the stale due card")
        XCTAssertFalse(
            state.beginSession(cards: [Self.card]),
            "the stale Today row must not reopen while its answer is committing"
        )
        XCTAssertTrue(state.path.isEmpty)
        XCTAssertNil(state.sessionID)
        XCTAssertEqual(state.stage, .processing, "a rejected reopen must not reset conversation state")

        release.signal()
        await submit.value
        await settle("Today was not refreshed after the write settled") { state.load == .error }
    }
}
