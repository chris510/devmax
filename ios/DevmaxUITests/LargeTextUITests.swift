import XCTest

final class LargeTextUITests: XCTestCase {
    func testLargestTextKeepsReviewControlsReachable() {
        let app = launch()
        let start = app.buttons["Start review · 3 cards"]
        XCTAssertTrue(start.waitForExistence(timeout: 10))
        XCTAssertTrue(start.isHittable)
        // Prove UIKit applied the requested category, rather than accidentally
        // exercising another default-size screenshot.
        XCTAssertGreaterThan(start.frame.height, 80)
        capture(app, "largest-text-today")
        start.tap()
        let record = app.buttons["Record answer"]
        XCTAssertTrue(record.waitForExistence(timeout: 10))
        XCTAssertTrue(record.isHittable)
        XCTAssertTrue(app.buttons["Review options"].isHittable)
        capture(app, "largest-text-question")
    }

    func testLargestTextKeepsPlanActionsReachable() {
        let app = launch(route: "study-plan-item")
        let reopen = app.buttons["Reopen"]
        XCTAssertTrue(reopen.waitForExistence(timeout: 10))
        XCTAssertTrue(reopen.isHittable)
        XCTAssertGreaterThan(reopen.frame.height, 60)
        app.swipeUp()
        XCTAssertTrue(reopen.isHittable)
        capture(app, "largest-text-plan")
    }

    private func launch(route: String = "") -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": route, "WC_TTS": "0"]
        app.launchArguments = [
            "-UIPreferredContentSizeCategoryName", "UICTContentSizeCategoryAccessibilityXXXL"
        ]
        app.launch()
        return app
    }

    private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
