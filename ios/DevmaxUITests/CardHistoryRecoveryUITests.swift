import XCTest

final class CardHistoryRecoveryUITests: XCTestCase {
    func testHistoryFailureRetriesWithoutLeavingTheCard() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "history-failure", "WC_TTS": "0"]
        app.launch()

        XCTAssertTrue(app.staticTexts["Couldn't load card history."].waitForExistence(timeout: 10))
        app.buttons["Retry"].tap()

        XCTAssertTrue(app.staticTexts["Consistent hashing"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["MAINTAIN CARD"].exists)
        XCTAssertFalse(app.staticTexts["Couldn't load card history."].exists)
    }
}
