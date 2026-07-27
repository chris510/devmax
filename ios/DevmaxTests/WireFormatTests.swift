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
}
