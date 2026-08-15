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
    func testPreparedExportSharesExactPrivacyBoundedJSONBundle() async throws {
        let flow = PublicOnboardingState(api: MockAPI(), route: "lesson-add")
        flow.draft.sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000901")!

        await flow.prepareLessonArtifacts()

        XCTAssertEqual(flow.lessonArtifactState, .ready)
        let url = try XCTUnwrap(flow.lessonExportURL)
        defer { try? FileManager.default.removeItem(at: url) }
        XCTAssertEqual(url.pathExtension, "json")
        let data = try Data(contentsOf: url)
        let exported = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(exported["schema"] as? String, "second-brain.learning-writeback")
        XCTAssertEqual(exported["schema_version"] as? Int, 1)
        XCTAssertEqual(exported["producer"] as? String, "devmax")
        XCTAssertEqual(exported["export_id"] as? String, "sha256:mock-writeback-export")
        let source = try XCTUnwrap(exported["source"] as? [String: Any])
        XCTAssertTrue((source["id"] as? String)?.hasPrefix("devmax:source:") == true)
        let concepts = try XCTUnwrap(exported["concepts"] as? [[String: Any]])
        XCTAssertEqual(concepts.count, 1)
        let candidates = try XCTUnwrap(
            concepts[0]["recall_candidates"] as? [[String: Any]]
        )
        XCTAssertEqual(candidates.count, 5)
        XCTAssertTrue(candidates.allSatisfy { $0["answer_rubric"] is String })
        let serialized = try XCTUnwrap(String(data: data, encoding: .utf8))
        for forbidden in [
            "FULL RAW SOURCE", "source_text", "answer_text", "transcript",
            "next_review_at", "interval_days", "mastery_summary", "canonical_question"
        ] {
            XCTAssertFalse(serialized.contains(forbidden))
        }
    }

    func testMaterialArtifactsStillDecodesWhenOlderServerOmitsBundle() throws {
        let data = Data(
            ##"{"source_id":"00000000-0000-0000-0000-000000000901","title":"Old artifacts","source_url":"","distilled_at":"2026-08-14T22:45:00Z","canonical_note_markdown":"# Note","recall_export_markdown":"# Recall","concepts":[]}"##.utf8
        )

        let artifacts = try LiveAPI.decoder.decode(MaterialArtifacts.self, from: data)

        XCTAssertNil(artifacts.writebackBundle)
    }
}
