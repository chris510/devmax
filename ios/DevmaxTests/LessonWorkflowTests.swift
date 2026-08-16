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

    private static func lessonImport(
        topics: [MaterialTopic], status: String = "ready", id: UUID = UUID(),
        lessonGroundingRequired: Bool? = nil
    ) -> MaterialImport {
        MaterialImport(
            id: id, title: "Networking 101", kind: "article", version: 1,
            status: status, importPath: "lesson", intent: "already_studied",
            originalFilename: "", sourceUrl: "https://example.com/networking",
            contentProvenance: LessonContentProvenance.learnerNotes.rawValue,
            characterCount: 876, cleanCount: topics.filter(\.isClean).count,
            attentionCount: topics.filter { !$0.isClean }.count, error: "",
            planDraftId: nil, comparison: [:], topics: topics,
            lessonGroundingRequired: lessonGroundingRequired,
            createdAt: Date(), updatedAt: Date()
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
            sourceUrl: "https://example.com/lesson",
            contentProvenance: "exact_source_excerpt", importPath: "lesson",
            intent: "already_studied", requestedWeeks: 12,
            weeklyCapacityMinutes: 480, mode: "flexible", deadline: nil,
            previousVersionId: nil
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: LiveAPI.encoder.encode(request)) as? [String: Any]
        )
        XCTAssertEqual(object["kind"] as? String, "documentation")
        XCTAssertEqual(object["source_url"] as? String, "https://example.com/lesson")
        XCTAssertEqual(object["content_provenance"] as? String, "exact_source_excerpt")
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
        XCTAssertEqual(
            draft.contentProvenance, LessonContentProvenance.legacyUnspecified
        )
        XCTAssertEqual(draft.importPath, "topics")
    }

    @MainActor
    func testLessonCannotStartUntilContentOriginIsExplicit() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "lesson-add")
        flow.draft.guideText = String(repeating: "source ", count: 40)
        flow.draft.contentProvenance = LessonContentProvenance.legacyUnspecified

        XCTAssertFalse(flow.lessonIsValid)

        flow.draft.contentProvenance = LessonContentProvenance.learnerNotes.rawValue

        XCTAssertTrue(flow.lessonIsValid)
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
        XCTAssertNil(source["content_provenance"])
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

    func testMaterialImportStillDecodesWhenOlderServerOmitsGroundingHint() throws {
        let original = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        let data = try JSONEncoder().encode(original)

        let decoded = try JSONDecoder().decode(MaterialImport.self, from: data)

        XCTAssertNil(decoded.lessonGroundingRequired)
        XCTAssertFalse(decoded.requiresLessonGroundingRecovery)
    }

    func testImportProgressUsesRealServerStateAndElapsedTime() {
        let now = Date(timeIntervalSince1970: 10_000)
        let value = ImportProgressPresentation(
            status: "processing", startedAt: now.addingTimeInterval(-72),
            checkedAt: now.addingTimeInterval(-4), now: now
        )

        XCTAssertEqual(value.title, "Reading and checking the source")
        XCTAssertEqual(value.elapsedLabel, "WORKING · 1M 12S")
        XCTAssertEqual(value.checkedLabel, "CHECKED 4S AGO")
    }

    func testImportProgressNeverInventsCompletionPercentage() {
        let now = Date(timeIntervalSince1970: 10_000)
        let value = ImportProgressPresentation(
            status: "pending", startedAt: now.addingTimeInterval(-8),
            checkedAt: nil, now: now
        )

        XCTAssertEqual(value.title, "Saved and waiting to start")
        XCTAssertEqual(value.elapsedLabel, "WORKING · 8S")
        XCTAssertEqual(value.checkedLabel, "CONNECTING")
        XCTAssertFalse(value.elapsedLabel.contains("percent"))
    }

    @MainActor
    func testRetryReconcilesAJobThatAlreadyFinished() async {
        let flow = PublicOnboardingState(api: MockAPI(), route: "extract-error")
        flow.draft.sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000901")!

        await flow.retryImport()

        XCTAssertEqual(flow.step, .importReady)
        XCTAssertEqual(flow.job?.status, "ready")
        XCTAssertNotNil(flow.lastImportCheckedAt)
    }

    @MainActor
    func testPreGateReadyLessonRoutesToGroundingRecovery() {
        let lesson = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)],
            lessonGroundingRequired: true
        )
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")

        flow.openSavedImport(lesson)

        XCTAssertEqual(flow.step, .importFailed)
        XCTAssertTrue(flow.lessonGroundingRecoveryRequired)
        XCTAssertTrue(flow.selectedTopics.isEmpty)
        XCTAssertFalse(flow.canConfirmSelectedTopics)
    }

    @MainActor
    func testPreGateReadyLessonRetriesEvenWhenLatestGetIsStillReady() async {
        let id = UUID()
        let topic = Self.topic("TCP reliability", position: 1)
        let legacyReady = Self.lessonImport(
            topics: [topic], id: id, lessonGroundingRequired: true
        )
        let requeued = Self.lessonImport(
            topics: [topic], status: "pending", id: id,
            lessonGroundingRequired: false
        )
        let api = MockAPI(
            materialImportFixture: legacyReady,
            retryMaterialImportFixture: requeued
        )
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.openSavedImport(legacyReady)

        await flow.retryImport()

        XCTAssertEqual(flow.job?.status, "pending")
        XCTAssertEqual(flow.step, .importing)
        XCTAssertFalse(flow.lessonGroundingRecoveryRequired)
        XCTAssertNotNil(flow.lastImportCheckedAt)
    }

    @MainActor
    func testFailedGroundingRecheckKeepsRecoveryStateAndCanRetryAgain() async {
        let id = UUID()
        let topic = Self.topic("TCP reliability", position: 1)
        let failedRecheck = Self.lessonImport(
            topics: [topic], status: "failed", id: id,
            lessonGroundingRequired: true
        )
        let requeued = Self.lessonImport(
            topics: [topic], status: "pending", id: id,
            lessonGroundingRequired: false
        )
        let api = MockAPI(
            materialImportFixture: failedRecheck,
            retryMaterialImportFixture: requeued
        )
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.openSavedImport(failedRecheck)

        XCTAssertEqual(flow.step, .importFailed)
        XCTAssertTrue(flow.lessonGroundingRecheckFailed)

        await flow.retryImport()

        XCTAssertEqual(flow.job?.status, "pending")
        XCTAssertEqual(flow.step, .importing)
        XCTAssertFalse(flow.lessonGroundingRecoveryRequired)
    }

    @MainActor
    func testForegroundRefreshRoutesFinishedImportWithoutRetrying() async {
        let flow = PublicOnboardingState(api: MockAPI(), route: "extract-error")
        flow.draft.sourceID = UUID(uuidString: "00000000-0000-0000-0000-000000000901")!

        await flow.refreshActiveImport()

        XCTAssertEqual(flow.step, .importReady)
        XCTAssertEqual(flow.job?.status, "ready")
        XCTAssertNotNil(flow.lastImportCheckedAt)
    }

    @MainActor
    func testSavedReadyImportCanReopenConceptReview() async throws {
        let api = MockAPI()
        let source = try await api.materialImport(
            UUID(uuidString: "00000000-0000-0000-0000-000000000901")!
        )
        let flow = PublicOnboardingState(api: api, route: "welcome")

        flow.openSavedImport(source)

        XCTAssertEqual(flow.step, .importReady)
        XCTAssertEqual(flow.job?.id, source.id)
        XCTAssertEqual(flow.selectedTopics, source.cleanTopicIDs)
    }

    @MainActor
    func testReadyUnassignedLessonFallsBackToLegacyTopicReview() async {
        let api = MockAPI(pilotSourceNotAssigned: true)
        let source = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.openSavedImport(source)

        XCTAssertEqual(flow.step, .importReady)
        await flow.openLessonPilotPreview(sourceID: source.id)

        XCTAssertEqual(flow.step, .topics)
        XCTAssertNil(flow.lessonPilotPreview)
    }

    @MainActor
    func testConfirmedAndSupersededUnassignedLessonsRemainLegacyEmpty() async {
        for status in ["confirmed", "superseded"] {
            let api = MockAPI(pilotSourceNotAssigned: true)
            let source = Self.lessonImport(
                topics: [Self.topic("TCP reliability", position: 1)], status: status
            )
            let flow = PublicOnboardingState(api: api, route: "welcome")

            flow.openSavedImport(source)
            await waitForTerminalLessonRouting(flow)

            XCTAssertEqual(flow.step, .empty, "status: \(status)")
            XCTAssertNil(flow.lessonPilotPreview, "status: \(status)")
        }
    }

    func testLessonCheckDraftStoreCanClearEveryDraft() {
        let first = UUID()
        let second = UUID()
        LessonCheckDraftStore.clearAll()
        defer { LessonCheckDraftStore.clearAll() }
        LessonCheckDraftStore.save("first answer", for: first)
        LessonCheckDraftStore.save("second answer", for: second)

        LessonCheckDraftStore.clearAll()

        XCTAssertNil(LessonCheckDraftStore.read(for: first))
        XCTAssertNil(LessonCheckDraftStore.read(for: second))
    }

    @MainActor
    func testSuccessfulMaterialDeletionClearsAllLessonCheckDrafts() async {
        let first = UUID()
        let second = UUID()
        LessonCheckDraftStore.clearAll()
        defer { LessonCheckDraftStore.clearAll() }
        LessonCheckDraftStore.save("formation", for: first)
        LessonCheckDraftStore.save("transfer", for: second)
        let source = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        flow.imports = [source]

        await flow.deleteMaterial(source.id)

        XCTAssertNil(LessonCheckDraftStore.read(for: first))
        XCTAssertNil(LessonCheckDraftStore.read(for: second))
        XCTAssertTrue(flow.imports.isEmpty)
    }

    @MainActor
    func testFailedMaterialDeletionPreservesLessonCheckDrafts() async {
        let checkID = UUID()
        LessonCheckDraftStore.clearAll()
        defer { LessonCheckDraftStore.clearAll() }
        LessonCheckDraftStore.save("keep this answer", for: checkID)
        let source = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        let flow = PublicOnboardingState(
            api: MockAPI(materialDeletionFails: true), route: "welcome"
        )
        flow.imports = [source]

        await flow.deleteMaterial(source.id)

        XCTAssertEqual(LessonCheckDraftStore.read(for: checkID), "keep this answer")
        XCTAssertEqual(flow.imports.map(\.id), [source.id])
    }

    @MainActor
    func testStartingAnotherGuideClearsThePriorImportIdentity() async throws {
        let api = MockAPI()
        let source = try await api.materialImport(UUID())
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.openSavedImport(source)
        flow.draft.sourceID = source.id

        flow.beginGuide(forceNew: true)

        XCTAssertNil(flow.job)
        XCTAssertNil(flow.draft.sourceID)
        XCTAssertNil(flow.draft.previousVersionID)
        XCTAssertTrue(flow.selectedTopics.isEmpty)
        XCTAssertEqual(flow.step, .guide)
    }

    @MainActor
    func testBeginningAnUpdatedVersionKeepsOnlyItsLineageIdentity() async throws {
        let api = MockAPI()
        let source = try await api.materialImport(UUID())
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.openSavedImport(source)

        flow.beginGuideUpdate(source)

        XCTAssertNil(flow.job)
        XCTAssertNil(flow.draft.sourceID)
        XCTAssertEqual(flow.draft.previousVersionID, source.id)
        XCTAssertTrue(flow.selectedTopics.isEmpty)
        XCTAssertEqual(flow.step, source.importPath == "lesson" ? .lesson : .guide)
    }

    @MainActor
    func testLateRefreshCannotRestoreAnImportAfterStartingANewGuide() async throws {
        let api = MockAPI(materialImportDelay: .milliseconds(100))
        let flow = PublicOnboardingState(api: api, route: "welcome")
        let processing = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)],
            status: "processing"
        )
        flow.openSavedImport(processing)
        let refresh = Task { await flow.refreshActiveImport() }
        try await Task.sleep(for: .milliseconds(20))

        flow.beginGuide(forceNew: true)
        await refresh.value

        XCTAssertNil(flow.job)
        XCTAssertNil(flow.draft.sourceID)
        XCTAssertEqual(flow.step, .guide)
    }

    @MainActor
    func testLateConfirmationCannotStartAnOldLessonAfterOpeningANewGuide() async throws {
        let api = MockAPI(confirmMaterialDelay: .milliseconds(100))
        let flow = PublicOnboardingState(api: api, route: "welcome")
        let app = AppState(api: api)
        let lesson = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        flow.openSavedImport(lesson)
        flow.selectedTopics.insert(lesson.topics[0].id)
        let confirmation = Task { await flow.confirmTopics(app: app) }
        try await Task.sleep(for: .milliseconds(20))

        flow.beginGuide(forceNew: true)
        await confirmation.value

        XCTAssertNil(flow.job)
        XCTAssertEqual(flow.step, .guide)
        XCTAssertTrue(app.path.isEmpty)
    }

    @MainActor
    func testLateArtifactResponseCannotAttachAnOldExportToANewGuide() async throws {
        let api = MockAPI(lessonArtifactDelay: .milliseconds(100))
        let flow = PublicOnboardingState(api: api, route: "welcome")
        let lesson = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        flow.openSavedImport(lesson)
        let preparation = Task { await flow.prepareLessonArtifacts() }
        try await Task.sleep(for: .milliseconds(20))

        flow.beginGuide(forceNew: true)
        await preparation.value

        XCTAssertNil(flow.lessonExportURL)
        XCTAssertEqual(flow.lessonArtifactState, .idle)
        XCTAssertEqual(flow.step, .guide)
    }

    @MainActor
    func testSavedLessonClassificationReplacesAnUnrelatedDraftChoice() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        flow.draft.contentProvenance = LessonContentProvenance.aiDerivedSummary.rawValue
        let source = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )

        flow.openSavedImport(source)

        XCTAssertEqual(
            flow.draft.contentProvenance,
            LessonContentProvenance.learnerNotes.rawValue
        )
    }

    @MainActor
    func testLegacySavedLessonClearsAStaleClassificationAndCannotConfirm() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        flow.draft.contentProvenance = LessonContentProvenance.aiDerivedSummary.rawValue
        var source = Self.lessonImport(
            topics: [Self.topic("TCP reliability", position: 1)]
        )
        source.contentProvenance = LessonContentProvenance.legacyUnspecified

        flow.openSavedImport(source)
        flow.selectedTopics.insert(source.topics[0].id)

        XCTAssertEqual(
            flow.draft.contentProvenance,
            LessonContentProvenance.legacyUnspecified
        )
        XCTAssertFalse(flow.canConfirmSelectedTopics)
    }

    @MainActor
    func testLessonConceptsRequireExplicitSelectionAfterStructuralCheck() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        let source = Self.lessonImport(topics: [Self.topic("TCP reliability", position: 1)])

        flow.openSavedImport(source)

        XCTAssertEqual(flow.step, .importReady)
        XCTAssertTrue(flow.isLessonDraft)
        XCTAssertTrue(flow.selectedTopics.isEmpty)
        XCTAssertFalse(flow.canConfirmSelectedTopics)

        flow.selectedTopics.insert(source.topics[0].id)

        XCTAssertTrue(flow.canConfirmSelectedTopics)
    }

    @MainActor
    func testLessonCannotPartiallyConfirmAndSilentlyDiscardConcepts() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        let topics = [
            Self.topic("IP delivery", position: 1),
            Self.topic("TCP reliability", position: 2)
        ]
        let source = Self.lessonImport(topics: topics)
        flow.openSavedImport(source)

        flow.selectedTopics.insert(topics[0].id)
        XCTAssertFalse(flow.canConfirmSelectedTopics)

        flow.selectedTopics.insert(topics[1].id)
        XCTAssertTrue(flow.canConfirmSelectedTopics)
    }

    @MainActor
    func testLessonCannotConfirmWhileAProposalStillNeedsAttention() {
        let flow = PublicOnboardingState(api: MockAPI(), route: "welcome")
        let clean = Self.topic("IP delivery", position: 1)
        var unresolved = Self.topic("TCP reliability", position: 2)
        unresolved.status = "needs_attention"
        unresolved.issue = "Grounding review required."
        let source = Self.lessonImport(topics: [clean, unresolved])
        flow.openSavedImport(source)

        flow.selectedTopics.insert(clean.id)

        XCTAssertFalse(flow.canConfirmSelectedTopics)
    }

    @MainActor
    private func pilotFlow(
        api: MockAPI
    ) async throws -> (PublicOnboardingState, MaterialTopicPreview) {
        let sourceID = UUID()
        let flow = PublicOnboardingState(api: api, route: "welcome")
        flow.draft.sourceID = sourceID
        flow.draft.importPath = "lesson"
        flow.draft.contentProvenance = LessonContentProvenance.exactSourceExcerpt.rawValue
        flow.lessonPilotPreview = try await api.lessonPilotPreview(sourceID)
        flow.step = .lessonCheck
        return (flow, try XCTUnwrap(flow.lessonPilotPreview?.topics.first))
    }

    @MainActor
    private func waitForTerminalLessonRouting(_ flow: PublicOnboardingState) async {
        for _ in 0..<100 {
            if !flow.busy, flow.step != .lessonCheck { return }
            try? await Task.sleep(for: .milliseconds(2))
        }
    }

    func testUnavailablePilotProposalCannotBeStarted() {
        let preview = MaterialTopicPreview(
            id: UUID(), position: 1, sectionTitle: "Section", topic: "Hidden target",
            formationQuestion: "This must not render.", status: "clean", issue: "",
            formationState: "unavailable", transferState: "unavailable"
        )

        XCTAssertFalse(preview.isAvailable)
        XCTAssertFalse(preview.isTransferAvailable)
    }

    @MainActor
    func testAttemptFirstFormationConfirmsHeldCardWithoutStartingConversation() async throws {
        let api = MockAPI()
        let (flow, preview) = try await pilotFlow(api: api)

        await flow.beginLessonActivity(preview)
        XCTAssertEqual(flow.lessonCheckStage, .attempt)
        XCTAssertEqual(flow.activeLessonCheck?.condition, .attemptFirst)

        flow.updateLessonCheckDraft(
            "IP is best effort, so transport supplies the guarantees the application needs."
        )
        await flow.submitLessonAttempt()
        XCTAssertEqual(flow.lessonCheckStage, .authority)
        XCTAssertEqual(flow.lessonAuthority?.check.qualitativeOutcome, .accurateAccount)

        await flow.acceptLessonAuthority()

        XCTAssertEqual(flow.lessonCheckStage, .held)
        XCTAssertEqual(flow.step, .lessonCheck)
        XCTAssertEqual(flow.confirmedLessonCardIDs.count, 1)
        XCTAssertNil(flow.activeLessonCheck)
        XCTAssertNil(flow.lessonAuthority)
    }

    @MainActor
    func testRestudyDoesNotShowAQuestionBeforeAuthorityExposure() async throws {
        let api = MockAPI()
        let (flow, original) = try await pilotFlow(api: api)
        let restudy = MaterialTopicPreview(
            id: original.id, position: original.position,
            sectionTitle: original.sectionTitle, topic: original.topic,
            formationQuestion: nil, status: original.status, issue: original.issue,
            formationState: original.formationState, transferState: original.transferState
        )

        XCTAssertNil(restudy.formationQuestion)
        await flow.beginLessonActivity(restudy)

        XCTAssertEqual(flow.lessonCheckStage, .authority)
        XCTAssertEqual(flow.activeLessonCheck?.condition, .restudy)
        XCTAssertEqual(flow.activeLessonCheck?.promptText, "")
        XCTAssertNotNil(flow.lessonAuthority?.sourceExcerpt)
    }

    @MainActor
    func testFormationProviderFailurePreservesTheExactDiskDraft() async throws {
        let api = MockAPI(pilotFormationFailsOnce: true)
        let (flow, preview) = try await pilotFlow(api: api)
        await flow.beginLessonActivity(preview)
        let checkID = try XCTUnwrap(flow.activeLessonCheck?.id)
        defer { LessonCheckDraftStore.clear(for: checkID) }
        let answer = "IP can lose or reorder packets, so transport adds a stronger contract."

        flow.updateLessonCheckDraft(answer)
        await flow.submitLessonAttempt()

        XCTAssertEqual(flow.lessonCheckStage, .submitFailed)
        XCTAssertEqual(flow.lessonCheckDraft, answer)
        XCTAssertEqual(LessonCheckDraftStore.read(for: checkID), answer)
        XCTAssertNil(flow.lessonAuthority)

        await flow.submitLessonAttempt()
        XCTAssertEqual(flow.lessonCheckStage, .authority)
        XCTAssertNil(LessonCheckDraftStore.read(for: checkID))
    }

    @MainActor
    func testLessonDraftRehydratesAfterAFlowIsRecreated() async throws {
        let api = MockAPI()
        let (firstFlow, preview) = try await pilotFlow(api: api)
        await firstFlow.beginLessonActivity(preview)
        let checkID = try XCTUnwrap(firstFlow.activeLessonCheck?.id)
        defer { LessonCheckDraftStore.clear(for: checkID) }
        let answer = "The network routes best-effort packets; delivery guarantees live above it."
        firstFlow.updateLessonCheckDraft(answer)

        let resumed = PublicOnboardingState(api: api, route: "welcome")
        resumed.draft.sourceID = firstFlow.draft.sourceID
        resumed.draft.importPath = "lesson"
        resumed.lessonPilotPreview = firstFlow.lessonPilotPreview
        resumed.step = .lessonCheck
        await resumed.beginLessonActivity(preview)

        XCTAssertEqual(resumed.activeLessonCheck?.id, checkID)
        XCTAssertEqual(resumed.lessonCheckDraft, answer)
        XCTAssertEqual(resumed.lessonCheckStage, .resume)
    }

    @MainActor
    func testExcludingAfterAuthorityStillConfirmsAZeroKeptSource() async throws {
        let api = MockAPI()
        let (flow, preview) = try await pilotFlow(api: api)
        await flow.beginLessonActivity(preview)
        flow.updateLessonCheckDraft("IP is unreliable, so TCP makes it reliable.")
        await flow.submitLessonAttempt()
        XCTAssertEqual(flow.lessonCheckStage, .authority)

        await flow.excludeLessonProposal(preview.id)

        XCTAssertEqual(flow.lessonCheckStage, .completeNoCards)
        XCTAssertTrue(flow.confirmedLessonCardIDs.isEmpty)
        let confirmations = await api.confirmedMaterialSelections
        XCTAssertEqual(confirmations, [[]])
    }

    @MainActor
    func testFailedConfirmationKeepsAuthorityAndRetriesWithoutReexposure() async throws {
        let api = MockAPI(pilotConfirmationFailsOnce: true)
        let (flow, preview) = try await pilotFlow(api: api)
        await flow.beginLessonActivity(preview)
        flow.updateLessonCheckDraft(
            "IP is best effort, so transport supplies the guarantees the application needs."
        )
        await flow.submitLessonAttempt()
        let exposedAt = try XCTUnwrap(flow.lessonAuthority?.exposedAt)

        await flow.acceptLessonAuthority()

        XCTAssertEqual(flow.lessonCheckStage, .authority)
        XCTAssertNotNil(flow.lessonAuthority)
        XCTAssertEqual(flow.lessonAuthority?.exposedAt, exposedAt)
        XCTAssertNil(flow.activeLessonCheck)

        await flow.retryPilotLessonConfirmation()

        XCTAssertEqual(flow.lessonCheckStage, .held)
        XCTAssertNil(flow.lessonAuthority)
        XCTAssertEqual(flow.confirmedLessonCardIDs.count, 1)
        let confirmations = await api.confirmedMaterialSelections
        XCTAssertEqual(confirmations, [[preview.id], [preview.id]])
    }

    @MainActor
    func testTransferSubmissionIsBlindNonnumericAndRetryable() async throws {
        let api = MockAPI(pilotTransferFailsOnce: true)
        let (flow, original) = try await pilotFlow(api: api)
        let transfer = MaterialTopicPreview(
            id: original.id, position: original.position,
            sectionTitle: original.sectionTitle, topic: original.topic,
            formationQuestion: nil, status: original.status, issue: original.issue,
            formationState: "unavailable", transferState: "available"
        )
        await flow.beginTransferCheck(transfer)
        let checkID = try XCTUnwrap(flow.activeLessonCheck?.id)
        defer { LessonCheckDraftStore.clear(for: checkID) }
        let response = "Sequencing and retransmission must live above best-effort IP."
        flow.updateLessonCheckDraft(response)

        await flow.submitLessonTransfer()
        XCTAssertEqual(flow.lessonCheckStage, .transferFailed)
        XCTAssertEqual(flow.lessonCheckDraft, response)
        XCTAssertNil(flow.lessonAuthority)

        await flow.submitLessonTransfer()
        XCTAssertEqual(flow.lessonCheckStage, .transferSubmitted)
        XCTAssertEqual(flow.activeLessonCheck?.status, .submitted)
        XCTAssertNil(flow.activeLessonCheck?.qualitativeOutcome)
        XCTAssertFalse(flow.activeLessonCheck?.hasFeedback ?? true)

        let sourceID = try XCTUnwrap(flow.draft.sourceID)
        let resumedPreview = try await api.lessonPilotPreview(sourceID)
        let submitted = try XCTUnwrap(resumedPreview.topics.first)
        XCTAssertEqual(submitted.transferState, "submitted")
        XCTAssertTrue(submitted.hasTransferEntryPoint)

        let resumed = PublicOnboardingState(api: api, route: "welcome")
        resumed.draft.sourceID = sourceID
        resumed.draft.importPath = "lesson"
        resumed.lessonPilotPreview = resumedPreview
        resumed.step = .lessonCheck
        await resumed.beginTransferCheck(submitted)

        XCTAssertEqual(resumed.lessonCheckStage, .transferSubmitted)
        XCTAssertEqual(resumed.activeLessonCheck?.id, checkID)
    }
}
