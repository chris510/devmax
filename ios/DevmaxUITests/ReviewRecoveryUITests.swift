import XCTest

final class ReviewRecoveryUITests: XCTestCase {
    func testTypedAttemptCanRetryEndingThenStudyWithoutAScore() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "review-end-failure", "WC_TEXT_FIRST": "1", "WC_TTS": "0"]
        app.launch()
        let editor = app.textViews["conversation-answer-editor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        editor.typeText("I cannot yet explain the handoff.")
        app.buttons["Review options"].tap()
        let end = app.buttons["End without scoring"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        capture(app, "unfinished-review")
        end.tap()
        let failure = app.staticTexts["Couldn't confirm that this attempt ended. Your answer is saved. Try again."]
        XCTAssertTrue(failure.waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Study source again"].exists)
        capture(app, "end-review-failure")
        end.tap()
        let study = app.buttons["Study source again"]
        XCTAssertTrue(study.waitForExistence(timeout: 5))
        capture(app, "ended-review")
        study.tap()
        XCTAssertTrue(app.staticTexts["Build the mechanism with the source open. Recall returns later, closed-book."].waitForExistence(timeout: 5))
        capture(app, "learn-after-ended-review")
    }

    func testArchivedCardCanBeFoundAndRestoredAfterLeavingHistory() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "history", "WC_TTS": "0"]
        app.launch()
        XCTAssertTrue(app.buttons["MAINTAIN CARD"].waitForExistence(timeout: 10))
        app.buttons["MAINTAIN CARD"].tap()
        XCTAssertTrue(app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Archive card")).firstMatch.waitForExistence(timeout: 3))
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Archive card")).firstMatch.tap()
        XCTAssertTrue(app.staticTexts["Archived · Your history and review schedule are saved."].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["Review now"].exists)
        app.buttons["← Today"].tap()
        app.buttons["SETTINGS"].tap()
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Material")).firstMatch.tap()
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Archived cards")).firstMatch.tap()
        let archived = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Consistent hashing")).firstMatch
        XCTAssertTrue(archived.waitForExistence(timeout: 5))
        capture(app, "archived-library")
        archived.tap()
        app.buttons["MAINTAIN CARD"].tap()
        app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Restore card")).firstMatch.tap()
        XCTAssertTrue(app.buttons["Study source again"].waitForExistence(timeout: 5))
        capture(app, "restored-card")
    }

    func testRecordingCanLeaveForOptionsAndResumeItsSavedPartial() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "question", "WC_SIM_SPEECH": "1", "WC_TTS": "0"]
        app.launch()
        let record = app.buttons["Record answer"]
        XCTAssertTrue(record.waitForExistence(timeout: 10))
        record.tap()
        let partial = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "So the key space")).firstMatch
        XCTAssertTrue(partial.waitForExistence(timeout: 6))
        app.buttons["Review options"].tap()
        let resume = app.buttons["Resume review"]
        XCTAssertTrue(resume.waitForExistence(timeout: 5))
        resume.tap()
        XCTAssertTrue(app.buttons["Resume answer"].waitForExistence(timeout: 5))
        capture(app, "resumed-spoken-partial")
    }

    private func capture(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
