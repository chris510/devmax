import XCTest
@testable import Devmax

final class ScoreStyleTests: XCTestCase {
    func testBandsMatchTheDesignThresholds() {
        XCTAssertEqual(ScoreStyle.Band.of(0), .cold)
        XCTAssertEqual(ScoreStyle.Band.of(1), .cold)
        XCTAssertEqual(ScoreStyle.Band.of(2), .shaky)
        XCTAssertEqual(ScoreStyle.Band.of(3), .shaky)
        XCTAssertEqual(ScoreStyle.Band.of(4), .solid)
        XCTAssertEqual(ScoreStyle.Band.of(5), .solid)
        XCTAssertEqual(ScoreStyle.Band.of(nil), .unrated)
    }

    func testMissingScoreUsesLiteralState() {
        XCTAssertEqual(ScoreStyle.label(for: nil), "unrated")
        XCTAssertEqual(ScoreStyle.label(for: 3), "3")
    }

    func testScoreLabelMatchesTheActivatedContract() {
        XCTAssertEqual(result(contract: 1).scoreLabel, "SCORE")
        XCTAssertEqual(result(contract: 2).scoreLabel, "RECALL")
        XCTAssertEqual(result(contract: 99).scoreLabel, "SCORE")
    }

    @MainActor
    func testFirstReviewOnlyCompletesAfterACommittedResult() {
        let state = AppState(api: MockAPI())
        var completionCount = 0
        state.firstReviewCompletion = { completionCount += 1 }

        state.finish()
        XCTAssertEqual(completionCount, 0)
        XCTAssertNotNil(state.firstReviewCompletion)

        state.result = result(contract: 1)
        state.finish()
        XCTAssertEqual(completionCount, 1)
        XCTAssertNil(state.firstReviewCompletion)
    }

    private func result(contract: Int) -> SessionResult {
        SessionResult(
            score: 3,
            scoringContractVersion: contract,
            feedback: "Clear enough to schedule.",
            scheduleLine: "NEXT REVIEW · TOMORROW",
            reattemptOffered: false,
            reattemptPrompt: nil,
            coachingOffered: false,
            coachingFocus: nil,
            coachingQuestion: nil
        )
    }
}
