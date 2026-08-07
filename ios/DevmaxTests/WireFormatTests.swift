import XCTest
@testable import Devmax

/// Decoding checks against a real server response.
///
/// `Fixtures/card_detail.json` was captured from `GET /cards/{id}` on the running
/// backend (FastAPI + Postgres), not hand-written, so it carries the actual wire
/// format including `timestamptz` values serialized with six fractional digits.
///
/// This is the regression test for a bug that shipped: the decoder used
/// `.dateDecodingStrategy = .iso8601`, which rejects fractional seconds, so every
/// `CardDetail` decode threw. `CardHistoryScreen` swallows that with `try?`, so all
/// three Card History states rendered blank against a real server while looking
/// perfect on `MockAPI` fixtures.
final class WireFormatTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: type(of: self))
        let url = try XCTUnwrap(
            bundle.url(forResource: name, withExtension: "json"),
            "\(name).json is missing from the test bundle resources"
        )
        return try Data(contentsOf: url)
    }

    private func decodeCardDetail() throws -> CardDetail {
        try LiveAPI.decoder.decode(CardDetail.self, from: fixture("card_detail"))
    }

    func testACapturedCardDetailDecodes() throws {
        let card = try decodeCardDetail()

        XCTAssertEqual(card.topic, "Raft leader election")
        XCTAssertEqual(card.sessions.count, 3)
        XCTAssertFalse(card.masterySummary.isEmpty)
    }

    func testFractionalSecondTimestampsDecode() throws {
        let card = try decodeCardDetail()

        // The exact failure: 2026-07-26T09:26:29.058299Z
        for session in card.sessions {
            XCTAssertGreaterThan(session.date.timeIntervalSince1970, 0)
        }
        // Newest first, as GET /cards/{id} orders them.
        let dates = card.sessions.map(\.date)
        XCTAssertEqual(dates, dates.sorted(by: >))
    }

    func testTimestampsWithoutFractionalSecondsStillDecode() throws {
        // Nothing guarantees six digits forever — a whole-second value must not
        // start throwing the way fractional ones used to.
        struct Wrapper: Codable { let date: Date }
        let json = Data(#"{"date":"2026-07-26T09:26:29Z"}"#.utf8)

        XCTAssertNoThrow(try LiveAPI.decoder.decode(Wrapper.self, from: json))
    }

    func testAnUnparseableTimestampThrowsRatherThanSilentlyDefaulting() throws {
        struct Wrapper: Codable { let date: Date }
        let json = Data(#"{"date":"last tuesday"}"#.utf8)

        XCTAssertThrowsError(try LiveAPI.decoder.decode(Wrapper.self, from: json))
    }

    func testAllFiveTurnRolesDecode() throws {
        let card = try decodeCardDetail()
        let roles = Set(card.sessions.flatMap { $0.turns.map(\.role) })

        // The fixture covers an open session, a completed one, and one that used
        // its follow-up — so every role the server can emit appears here.
        XCTAssertEqual(roles, [.question, .answer, .followUp, .score])
    }

    func testAnOpenSessionDecodesWithNoScore() throws {
        let card = try decodeCardDetail()
        let open = try XCTUnwrap(card.sessions.first { $0.score == nil })

        XCTAssertEqual(open.turns.map(\.role), [.question])
    }

    func testSnakeCaseKeysMapOntoTheSwiftProperties() throws {
        let card = try decodeCardDetail()

        // mastery_summary, last_score, ease_factor, interval_days, next_review_at,
        // missed_count all arrive snake_cased.
        XCTAssertEqual(card.lastScore, 1)
        XCTAssertGreaterThan(card.easeFactor, 0)
        XCTAssertFalse(card.nextReviewAt.isEmpty)
        XCTAssertGreaterThanOrEqual(card.missedCount, 0)
    }

    // MARK: - The library shape Review Sprint and Coverage read

    private static let libraryRow = Data(#"""
    [{"id":"00000000-0000-0000-0000-0000000000c1","topic":"Consistent hashing",
      "category":"Core Concept","delivery_mode":"conversational",
      "mastery_summary":"solid on ring mechanics","last_score":3,
      "last_accuracy":4,"last_depth":1,
      "last_boundaries":2,"ease_factor":2.36,"interval_days":3,
      "repetitions":3,"next_review_at":"2026-07-27","due_label":"3 days overdue",
      "days_since_review":9,"missed_count":0}]
    """#.utf8)

    func testTheLibraryRowDecodesWithItsAxisScores() throws {
        let cards = try LiveAPI.decoder.decode([CardSummary].self, from: Self.libraryRow)
        let card = try XCTUnwrap(cards.first)

        XCTAssertEqual(card.lastAccuracy, 4)
        XCTAssertEqual(card.lastDepth, 1)
        XCTAssertEqual(card.lastBoundaries, 2)
        // Both computed server-side — the client never re-derives them.
        XCTAssertEqual(card.dueLabel, "3 days overdue")
        XCTAssertEqual(card.daysSinceReview, 9)
    }

    func testAnUnscoredLibraryRowLeavesEveryAxisNil() throws {
        let json = Data(#"""
        [{"id":"00000000-0000-0000-0000-0000000000c9","topic":"Virtual memory",
          "category":"Operating Systems","delivery_mode":"conversational",
          "mastery_summary":"","last_score":null,"last_accuracy":null,
          "last_depth":null,"last_boundaries":null,
          "ease_factor":2.5,"interval_days":1,"repetitions":0,
          "next_review_at":"2026-08-01","due_label":"due in 5 days",
          "days_since_review":null,"missed_count":0}]
        """#.utf8)

        let card = try XCTUnwrap(try LiveAPI.decoder.decode([CardSummary].self, from: json).first)

        XCTAssertNil(card.lastScore)
        XCTAssertNil(card.lastAccuracy)
        XCTAssertNil(card.daysSinceReview)
        // Coverage groups it as `untested`, not as a zero.
        XCTAssertEqual(ScoreStyle.Tier.of(card.lastScore), .untested)
    }

    // MARK: - The answer outcome

    func testACompletedAnswerCarriesThePracticeFlag() throws {
        let json = Data(#"""
        {"status":"complete","score":4,"feedback":"Solid.",
         "next_review_at":"2026-07-30","interval_days":6,"practice":true}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(let score, _, _, let interval, let practice, _, _) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertEqual(score, 4)
        XCTAssertEqual(interval, 6)
        XCTAssertTrue(practice)
    }

    func testAnOutcomeWithoutThePracticeFlagReadsAsADailyReview() throws {
        // A server that predates practice mode omits the field entirely; the
        // schedule line must still be the real one rather than crashing or
        // claiming the schedule was untouched.
        let json = Data(#"""
        {"status":"complete","score":3,"feedback":"Partial.",
         "next_review_at":"2026-07-28","interval_days":1}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(_, _, _, _, let practice, let reattempt, let prompt) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertFalse(practice)
        // Same reasoning for the coached re-attempt: a server that predates it
        // omits the field, and the affordance must stay hidden rather than offer a
        // turn the server would reject.
        XCTAssertFalse(reattempt)
        XCTAssertNil(prompt)
    }

    func testACompletedAnswerCarriesTheReattemptOffer() throws {
        // Server-computed from `accuracy <= 2`. The client never receives
        // the axis itself — the score block shows one numeral, the composite.
        let json = Data(#"""
        {"status":"complete","score":1,"feedback":"The mechanism is arc ownership.",
         "next_review_at":"2026-07-29","interval_days":1,"practice":false,
         "reattempt_offered":true,"reattempt_prompt":"In your words — What moves?"}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(let score, _, _, _, _, let reattempt, let prompt) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertEqual(score, 1)
        XCTAssertTrue(reattempt)
        // The server composes the prompt: on a resumed follow-up session the client
        // cannot reconstruct it, because its only `.question` entry is the probe.
        XCTAssertEqual(prompt, "In your words — What moves?")
    }

    // MARK: - Coverage's tier vocabulary

    func testTheCoverageTiersSplitTheMiddleOfTheScoreRange() {
        // Deliberately finer than Today's four bands: a 3 is `developing`, not
        // the same bucket as a 2.
        XCTAssertEqual(ScoreStyle.Tier.of(nil), .untested)
        XCTAssertEqual(ScoreStyle.Tier.of(0), .cold)
        XCTAssertEqual(ScoreStyle.Tier.of(1), .cold)
        XCTAssertEqual(ScoreStyle.Tier.of(2), .shaky)
        XCTAssertEqual(ScoreStyle.Tier.of(3), .developing)
        XCTAssertEqual(ScoreStyle.Tier.of(4), .solid)
        XCTAssertEqual(ScoreStyle.Tier.of(5), .solid)
    }
}
