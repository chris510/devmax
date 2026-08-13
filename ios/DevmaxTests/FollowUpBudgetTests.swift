import XCTest
@testable import Devmax

/// The fixture layer's scored-probe budget — what every conversation screenshot
/// walk is timed against.
///
/// The rule this replaced counted submits across the whole app run: two probes,
/// ever, whichever sessions they happened to land in. That is not the shape of
/// the contract it stands in for. The server's budget belongs to the session, a
/// Review Sprint card is not probed at all, and the second probe exists only when
/// the model reports it still lacks the evidence to score — so a global counter
/// could only ever agree with it by accident, and the sprint recap walk paid for
/// the disagreement in latency.
final class FollowUpBudgetTests: XCTestCase {
    /// The consistent-hashing card — the one both probe fixtures are written about.
    private static let cardID = UUID(uuidString: "00000000-0000-0000-0000-0000000000c1")!

    override func tearDown() {
        // `DebugFlags` is an app-run singleton, so a leaked `true` would hand the
        // next test's session a probe it never asked for.
        DebugFlags.shared.secondProbe = false
        super.tearDown()
    }

    /// A private instance per test. The budget is keyed by session, but the
    /// shared mock would still carry another test's forced-failure parity in.
    private func mock() -> MockAPI { MockAPI() }

    private func startedSession(
        _ api: MockAPI, practice: Bool = false
    ) async throws -> UUID {
        try await api.startSession(cardID: Self.cardID, practice: practice).sessionId
    }

    // MARK: - The default budget

    /// Two submits is now the whole of a daily review, which is what the `score`,
    /// `reattempt*` and `coaching*` walks spend before the score block appears.
    func testADailyReviewIsProbedOnceAndThenScored() async throws {
        let api = mock()
        let session = try await startedSession(api)

        let first = try await api.submitAnswer(sessionID: session, text: "the ring answer")
        guard case .followUp(let probe) = first else {
            return XCTFail("a daily review's first answer draws a probe, got \(first)")
        }
        XCTAssertTrue(
            probe.hasPrefix("One more — "), "the first probe keeps its own preface: \(probe)"
        )

        let second = try await api.submitAnswer(sessionID: session, text: "the probe answer")
        guard case .complete = second else {
            return XCTFail("the one probe is spent, so this answer scores, got \(second)")
        }
    }

    /// The budget is a property of the session, not of the app run: the second
    /// card of a run is owed exactly what the first one was.
    func testASecondSessionGetsItsOwnBudget() async throws {
        let api = mock()
        let first = try await startedSession(api)
        _ = try await api.submitAnswer(sessionID: first, text: "the ring answer")
        _ = try await api.submitAnswer(sessionID: first, text: "the probe answer")

        let second = try await startedSession(api)
        let outcome = try await api.submitAnswer(sessionID: second, text: "the next card's answer")

        guard case .followUp = outcome else {
            return XCTFail("the previous session's spent probe is not this one's, got \(outcome)")
        }
    }

    // MARK: - The granted second probe

    /// `WC_SECOND_PROBE` stands in for the model reporting it still cannot tell
    /// two adjacent scores apart — the only thing that earns a second probe.
    func testAGrantedSecondProbeIsIssuedOnceAndThenTheSessionScores() async throws {
        await MainActor.run { DebugFlags.shared.secondProbe = true }
        let api = mock()
        let session = try await startedSession(api)

        let first = try await api.submitAnswer(sessionID: session, text: "the ring answer")
        guard case .followUp(let firstProbe) = first else {
            return XCTFail("expected the band-rule probe, got \(first)")
        }
        XCTAssertTrue(firstProbe.hasPrefix("One more — "))

        let second = try await api.submitAnswer(sessionID: session, text: "the first probe answer")
        guard case .followUp(let secondProbe) = second else {
            return XCTFail("expected the granted second probe, got \(second)")
        }
        // The preface is the only thing telling the user the session cannot keep
        // being extended, so it is copy, not decoration.
        XCTAssertTrue(
            secondProbe.hasPrefix("Last one — "),
            "the second probe announces itself as the last: \(secondProbe)"
        )
        XCTAssertNotEqual(secondProbe, firstProbe)

        let third = try await api.submitAnswer(sessionID: session, text: "the second probe answer")
        guard case .complete = third else {
            return XCTFail("the cap is two, so the third answer scores, got \(third)")
        }
    }

    /// The grant is read when the session starts, the way the server settles it
    /// before a word has been said — not per submit.
    func testASessionStartedWithoutTheGrantKeepsItsSingleProbe() async throws {
        let api = mock()
        let session = try await startedSession(api)
        await MainActor.run { DebugFlags.shared.secondProbe = true }

        _ = try await api.submitAnswer(sessionID: session, text: "the ring answer")
        let second = try await api.submitAnswer(sessionID: session, text: "the probe answer")

        guard case .complete = second else {
            return XCTFail("a session cannot be granted a probe halfway through, got \(second)")
        }
    }

    // MARK: - Review Sprint

    /// A sprint card is never probed — the recap walk answers six cards through
    /// mock latency, and a probe apiece buys the screenshot nothing. The grant is
    /// deliberately on here: practice is the stronger of the two rules.
    func testAReviewSprintCardIsScoredOnItsFirstAnswer() async throws {
        await MainActor.run { DebugFlags.shared.secondProbe = true }
        let api = mock()
        let session = try await startedSession(api, practice: true)

        let outcome = try await api.submitAnswer(sessionID: session, text: "a fixture answer")

        guard case .complete(_, _, _, _, _, _, let practice, _, _, _, _, _) = outcome else {
            return XCTFail("a practice session is never probed, got \(outcome)")
        }
        XCTAssertTrue(practice, "and it echoes the flag back the way the server does")
    }
}
