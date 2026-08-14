import XCTest
@testable import Devmax

final class LessonWorkflowTests: XCTestCase {
    private static let firstID = UUID(uuidString: "00000000-0000-0000-0000-0000000000a1")!
    private static let secondID = UUID(uuidString: "00000000-0000-0000-0000-0000000000a2")!

    private static func summary(_ id: UUID, topic: String) -> CardSummary {
        CardSummary(
            id: id, topic: topic, category: "Lesson", deliveryMode: "conversational",
            masterySummary: "No signal yet.", lastScore: nil,
            lastAccuracy: nil, lastDepth: nil, lastBoundaries: nil,
            easeFactor: 2.5, intervalDays: 1, repetitions: 0,
            nextReviewAt: "2026-08-14", dueLabel: "due today",
            daysSinceReview: nil, missedCount: 0
        )
    }

    private static func topic(_ name: String, position: Int) -> MaterialTopic {
        MaterialTopic(
            id: UUID(), position: position, sectionTitle: "Source section", topic: name,
            answerAnchor: "A trusted answer anchor.", sourceExcerpt: "A source excerpt.",
            status: "clean", issue: ""
        )
    }

    @MainActor
    func testConfirmedLessonCardsFollowSourceOrderAndKeepUnmatchedCards() {
        let first = Self.summary(Self.firstID, topic: "First concept")
        let second = Self.summary(Self.secondID, topic: "Second concept")
        let unmatchedID = UUID()
        let unmatched = Self.summary(unmatchedID, topic: "Renamed during rollout")

        let cards = PublicOnboardingState.orderedLessonCards(
            cardIDs: [unmatchedID, Self.secondID, Self.firstID],
            topics: [
                Self.topic("Second concept", position: 2),
                Self.topic("First concept", position: 1)
            ],
            library: [second, unmatched, first]
        )

        XCTAssertEqual(cards.map(\.id), [Self.firstID, Self.secondID, unmatchedID])
    }

    @MainActor
    func testOneConceptLessonStillEndsOnResults() {
        let state = AppState(api: MockAPI())
        let card = Self.summary(Self.firstID, topic: "One concept").asQueueCard()

        state.beginSession(cards: [card], replacingPath: true, origin: .lesson)

        XCTAssertEqual(state.sessionEndLabel, "See results")
        state.run = [
            RunEntry(
                id: Self.firstID, topic: "One concept", category: "Lesson",
                score: 3, feedback: "Good mechanism; one trade-off was missing.",
                scheduleLine: "NEXT REVIEW · 17 AUG · INTERVAL 3D", practice: false
            )
        ]
        state.nextCard()
        XCTAssertEqual(state.path, [.recap])
        XCTAssertTrue(state.runWasLesson)
    }

    func testLessonRequestCarriesAttributionWithoutFetchingTheURL() throws {
        let request = MaterialImportRequest(
            title: "Consistent hashing", sourceText: String(repeating: "source ", count: 40),
            originalFilename: "", mimeType: "text/plain", kind: "documentation",
            sourceUrl: "https://example.com/lesson", importPath: "lesson",
            intent: "already_studied", requestedWeeks: 12,
            weeklyCapacityMinutes: 480, mode: "flexible", deadline: nil,
            previousVersionId: nil
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: LiveAPI.encoder.encode(request)) as? [String: Any]
        )
        XCTAssertEqual(object["kind"] as? String, "documentation")
        XCTAssertEqual(object["source_url"] as? String, "https://example.com/lesson")
        XCTAssertEqual(object["import_path"] as? String, "lesson")
        XCTAssertNotNil(object["source_text"])
    }

    func testLessonURLRejectsCredentialsAndUnescapedWhitespace() {
        XCTAssertNil(SafeExternalURL.parse("https://user:secret@example.com/lesson"))
        XCTAssertNil(SafeExternalURL.parse("https://example.com/not allowed"))
        XCTAssertNotNil(SafeExternalURL.parse("https://example.com/allowed%20path"))
    }

    func testOlderPublicDraftDecodesWithLessonMetadataDefaults() throws {
        let data = Data(#"{"title":"Saved guide","guideText":"kept"}"#.utf8)

        let draft = try JSONDecoder().decode(PublicSetupDraft.self, from: data)

        XCTAssertEqual(draft.title, "Saved guide")
        XCTAssertEqual(draft.guideText, "kept")
        XCTAssertEqual(draft.sourceURL, "")
        XCTAssertEqual(draft.sourceType, "guide")
        XCTAssertEqual(draft.importPath, "topics")
    }

    @MainActor
    func testPreparedExportContainsOnlyDistilledArtifacts() async throws {
        let flow = PublicOnboardingState(api: MockAPI(), route: "lesson-add")
        flow.draft.sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000901")!

        await flow.prepareLessonArtifacts()

        XCTAssertEqual(flow.lessonArtifactState, .ready)
        let url = try XCTUnwrap(flow.lessonExportURL)
        defer { try? FileManager.default.removeItem(at: url) }
        let exported = try String(contentsOf: url, encoding: .utf8)
        XCTAssertTrue(exported.contains("A concise canonical note."))
        XCTAssertTrue(exported.contains("Explain the mechanism."))
        XCTAssertFalse(exported.contains("FULL RAW SOURCE"))
    }
}
