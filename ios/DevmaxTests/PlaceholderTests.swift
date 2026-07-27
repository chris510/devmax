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

    func testMissingScoreRendersAsEmDash() {
        XCTAssertEqual(ScoreStyle.label(for: nil), "—")
        XCTAssertEqual(ScoreStyle.label(for: 3), "3")
    }
}
