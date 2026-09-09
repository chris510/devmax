import XCTest

final class ReviewRecoveryUITests: XCTestCase {
    private let recoveredPartial = "Okay so each server has a term number, and when a follower stops hearing heartbeats it bumps its term and becomes a candidate, then it asks"

    func testRecoveryPreviewCanEndWithoutLosingItsSavedPartial() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "resume", "WC_TTS": "0"]
        app.launch()
        XCTAssertTrue(app.buttons["Resume answer"].waitForExistence(timeout: 10))
        capture(app, "recovery-preview")
        app.buttons["Review options"].tap()
        let end = app.buttons["End without scoring"]
        XCTAssertTrue(end.waitForExistence(timeout: 5))
        end.tap()
        let ended = app.staticTexts["Ended without a score."].firstMatch
        XCTAssertTrue(ended.waitForExistence(timeout: 5))
        ended.tap()
        XCTAssertTrue(app.staticTexts["SAVED PARTIAL · UNSCORED"].waitForExistence(timeout: 3))
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label == %@", recoveredPartial)
        ).firstMatch.exists)
        capture(app, "recovery-preview-ended-with-partial")
    }

    func testRecoveryPreviewContinuesInTextWithoutAnExtraResumeTap() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "resume", "WC_TTS": "0"]
        app.launch()
        XCTAssertTrue(app.buttons["Resume answer"].waitForExistence(timeout: 10))
        app.buttons["conversation-type-instead"].tap()
        let editor = app.textViews["conversation-answer-editor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertEqual(editor.value as? String, recoveredPartial)
        XCTAssertFalse(app.buttons["Resume answer"].exists)
        capture(app, "recovery-preview-continued-in-text")
    }

    func testRecoveryPreviewContinuesRecordingFromTheSavedPartial() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "resume", "WC_SIM_SPEECH": "1", "WC_TTS": "0"]
        app.launch()
        XCTAssertTrue(app.buttons["Resume answer"].waitForExistence(timeout: 10))
        app.buttons["Record answer"].tap()
        XCTAssertTrue(app.buttons["Stop and submit answer"].waitForExistence(timeout: 3))
        // Type instead finalizes capture without submitting or scoring it.
        app.buttons["conversation-type-instead"].tap()
        let editor = app.textViews["conversation-answer-editor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 5))
        XCTAssertTrue((editor.value as? String)?.hasPrefix(recoveredPartial) == true)
        XCTAssertFalse(app.buttons["Resume answer"].exists)
    }

    func testMaintenanceCloseDismissesItsLocalSheetWithoutChangingTheCard() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "history", "WC_TTS": "0"]
        app.launch()
        let maintain = app.buttons["MAINTAIN CARD"]
        XCTAssertTrue(maintain.waitForExistence(timeout: 10))
        maintain.tap()
        let archive = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "Archive card")).firstMatch
        XCTAssertTrue(archive.waitForExistence(timeout: 5))
        capture(app, "maintenance-close-before")
        app.buttons["Close"].tap()
        XCTAssertTrue(archive.waitForNonExistence(timeout: 3))
        XCTAssertTrue(maintain.isHittable)
        XCTAssertFalse(app.staticTexts["Archived · Your history and review schedule are saved."].exists)
        capture(app, "maintenance-close-after")
    }

    func testCaptureCloseStillDismissesTheAppSheet() {
        let app = XCUIApplication()
        app.launchEnvironment = ["WC_ROUTE": "add", "WC_TTS": "0"]
        app.launch()
        let close = app.buttons["Close"]
        XCTAssertTrue(close.waitForExistence(timeout: 10))
        close.tap()
        XCTAssertTrue(close.waitForNonExistence(timeout: 3))
        XCTAssertTrue(app.buttons["SETTINGS"].isHittable)
    }

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
