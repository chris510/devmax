import XCTest

/// The coached re-attempt must remain a complete text-only flow.
///
/// These tests intentionally call `typeText` without tapping the editor first.
/// That is the regression seam: scoring removes the TextEditor, which clears
/// FocusState, and the re-attempt used to put it back on screen without restoring
/// keyboard focus. A visible-but-unfocused editor made this flow appear unable to
/// type even though all of the AppState routing tests remained green.
final class ReattemptTypingUITests: XCTestCase {
    private let app = XCUIApplication()

    override func setUp() {
        continueAfterFailure = false
        app.launchEnvironment = [
            "WC_ROUTE": "score",
            "WC_FAILED_MECHANISM": "1",
            "WC_SIM_SPEECH": "0",
            "WC_TTS": "0"
        ]
    }

    func testTextFirstAnswerCanCompleteReattemptWithoutRefocusingByHand() {
        app.launchEnvironment["WC_TEXT_FIRST"] = "1"
        app.launch()

        beginReattempt()
        typeAnswer("The new node takes only the adjacent hash-space slice.")
        submitAndWaitForResult()
    }

    func testVoiceAnswerCanSwitchReattemptToTextAndSubmit() {
        app.launch()

        beginReattempt()
        let typeInstead = app.buttons["conversation-type-instead"]
        XCTAssertTrue(typeInstead.waitForExistence(timeout: 3))
        typeInstead.tap()

        typeAnswer("Everything outside the new node's adjacent slice stays put.")
        submitAndWaitForResult()
    }

    private func beginReattempt() {
        let action = app.buttons["conversation-reattempt"]
        XCTAssertTrue(action.waitForExistence(timeout: 10))
        action.tap()
    }

    private func typeAnswer(_ answer: String) {
        let editor = app.textViews["conversation-answer-editor"]
        XCTAssertTrue(editor.waitForExistence(timeout: 3))
        editor.typeText(answer)
    }

    private func submitAndWaitForResult() {
        let submit = app.buttons["conversation-submit-answer"]
        XCTAssertTrue(submit.isEnabled)
        submit.tap()

        XCTAssertTrue(app.buttons["Done"].waitForExistence(timeout: 5))
        XCTAssertFalse(app.buttons["conversation-reattempt"].exists)
    }
}
