import XCTest
@testable import Devmax

final class ReadAloudTests: XCTestCase {
    func testAnswerAndScoringMutationsDoNotChangeLatestSpokenPrompt() {
        let question = ThreadEntry(role: .question, text: "Why use consistent hashing?")
        var thread = [question]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, question.id)

        thread.append(ThreadEntry(role: .answer, text: "It limits key movement."))

        XCTAssertEqual(
            thread.latestSpokenPrompt?.id,
            question.id,
            "Submitting an answer must not make read-aloud replay the question during scoring."
        )
    }

    func testEachNewQuestionTurnBecomesTheSpokenPrompt() {
        let question = ThreadEntry(role: .question, text: "Opening question")
        let followUp = ThreadEntry(role: .followUpQuestion, text: "One more — why?")
        let coaching = ThreadEntry(role: .coachingQuestion, text: "Try this variant")
        var thread = [question, ThreadEntry(role: .answer, text: "Answer"), followUp]

        XCTAssertEqual(thread.latestSpokenPrompt?.id, followUp.id)

        thread.append(ThreadEntry(role: .answer, text: "Follow-up answer"))
        thread.append(ThreadEntry(role: .coachingFeedback, text: "Qualitative feedback"))
        XCTAssertEqual(thread.latestSpokenPrompt?.id, followUp.id)

        thread.append(coaching)
        XCTAssertEqual(thread.latestSpokenPrompt?.id, coaching.id)
    }
}
