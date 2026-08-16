import XCTest
@testable import Devmax

/// Decoding checks against a real server response.
///
/// `Fixtures/card_detail.json` was captured from `GET /cards/{id}` on the running
/// backend (FastAPI + Postgres), not hand-written, so it carries the actual wire
/// format including `timestamptz` values serialized with six fractional digits.
///
/// This is the regression test for a bug that shipped: the decoder used
/// `.dateDecodingStrategy = .iso8601`, which rejects fractional seconds, so every
/// `CardDetail` decode threw. `CardHistoryScreen` swallows that with `try?`, so all
/// three Card History states rendered blank against a real server while looking
/// perfect on `MockAPI` fixtures.
final class WireFormatTests: XCTestCase {
    private func fixture(_ name: String) throws -> Data {
        let bundle = Bundle(for: type(of: self))
        let url = try XCTUnwrap(
            bundle.url(forResource: name, withExtension: "json"),
            "\(name).json is missing from the test bundle resources"
        )
        return try Data(contentsOf: url)
    }

    private func decodeCardDetail() throws -> CardDetail {
        try LiveAPI.decoder.decode(CardDetail.self, from: fixture("card_detail"))
    }

    func testACapturedCardDetailDecodes() throws {
        let card = try decodeCardDetail()

        XCTAssertEqual(card.topic, "Raft leader election")
        XCTAssertEqual(card.sessions.count, 3)
        XCTAssertFalse(card.masterySummary.isEmpty)
    }

    func testFractionalSecondTimestampsDecode() throws {
        let card = try decodeCardDetail()

        // The exact failure: 2026-07-26T09:26:29.058299Z
        for session in card.sessions {
            XCTAssertGreaterThan(session.date.timeIntervalSince1970, 0)
        }
        // Newest first, as GET /cards/{id} orders them.
        let dates = card.sessions.map(\.date)
        XCTAssertEqual(dates, dates.sorted(by: >))
    }

    func testTimestampsWithoutFractionalSecondsStillDecode() throws {
        // Nothing guarantees six digits forever — a whole-second value must not
        // start throwing the way fractional ones used to.
        struct Wrapper: Codable { let date: Date }
        let json = Data(#"{"date":"2026-07-26T09:26:29Z"}"#.utf8)

        XCTAssertNoThrow(try LiveAPI.decoder.decode(Wrapper.self, from: json))
    }

    func testAnUnparseableTimestampThrowsRatherThanSilentlyDefaulting() throws {
        struct Wrapper: Codable { let date: Date }
        let json = Data(#"{"date":"last tuesday"}"#.utf8)

        XCTAssertThrowsError(try LiveAPI.decoder.decode(Wrapper.self, from: json))
    }

    func testAllFiveTurnRolesDecode() throws {
        let card = try decodeCardDetail()
        let roles = Set(card.sessions.flatMap { $0.turns.map(\.role) })

        // The fixture covers an open session, a completed one, and one that used
        // its follow-up — so every role the server can emit appears here.
        XCTAssertEqual(roles, [.question, .answer, .followUp, .score])
    }

    func testAnOpenSessionDecodesWithNoScore() throws {
        let card = try decodeCardDetail()
        let open = try XCTUnwrap(card.sessions.first { $0.score == nil })

        XCTAssertEqual(open.turns.map(\.role), [.question])
    }

    /// A session can now carry two follow-up pairs, because the model may ask a
    /// second time when one probe left it unable to score honestly. The wire
    /// format is unchanged — the turns array is flat and ordered, and Card
    /// History renders it in order — but the shape is new, and the transcript
    /// keys its rows by `Turn.id`, which is role plus text. Two probes in one
    /// session that collapsed into a single row would be a silent loss.
    func testASessionWithTwoFollowUpsDecodesAsSevenDistinctTurns() throws {
        let json = Data(#"""
        {"id":"00000000-0000-0000-0000-0000000000f1",
         "date":"2026-07-16T21:32:00.000000Z","score":3,
         "feedback":"Clear on lookups; didn't mention replication.",
         "turns":[{"role":"question","text":"How does a client find the owner?"},
                  {"role":"answer","text":"Hash it, walk clockwise."},
                  {"role":"follow_up","text":"One more — what about during a join?"},
                  {"role":"answer","text":"Some keys point at the new node."},
                  {"role":"follow_up","text":"Last one — who serves them meanwhile?"},
                  {"role":"answer","text":"Whichever one has the data."},
                  {"role":"score","text":"3 — Correct lookup path."}]}
        """#.utf8)

        let session = try LiveAPI.decoder.decode(SessionHistory.self, from: json)

        XCTAssertEqual(
            session.turns.map(\.role),
            [.question, .answer, .followUp, .answer, .followUp, .answer, .score]
        )
        XCTAssertEqual(Set(session.turns.map(\.id)).count, session.turns.count)
    }

    func testSnakeCaseKeysMapOntoTheSwiftProperties() throws {
        let card = try decodeCardDetail()

        // mastery_summary, last_score, ease_factor, interval_days, next_review_at,
        // missed_count all arrive snake_cased.
        XCTAssertEqual(card.lastScore, 1)
        XCTAssertGreaterThan(card.easeFactor, 0)
        XCTAssertFalse(card.nextReviewAt.isEmpty)
        XCTAssertGreaterThanOrEqual(card.missedCount, 0)
    }

    // MARK: - The library shape Review Sprint and Coverage read

    private static let libraryRow = Data(#"""
    [{"id":"00000000-0000-0000-0000-0000000000c1","topic":"Consistent hashing",
      "category":"Core Concept","delivery_mode":"conversational",
      "mastery_summary":"solid on ring mechanics","last_score":3,
      "last_accuracy":4,"last_depth":1,
      "last_boundaries":2,"ease_factor":2.36,"interval_days":3,
      "repetitions":3,"next_review_at":"2026-07-27","due_label":"3 days overdue",
      "days_since_review":9,"missed_count":0}]
    """#.utf8)

    func testTheLibraryRowDecodesWithItsAxisScores() throws {
        let cards = try LiveAPI.decoder.decode([CardSummary].self, from: Self.libraryRow)
        let card = try XCTUnwrap(cards.first)

        XCTAssertEqual(card.lastAccuracy, 4)
        XCTAssertEqual(card.lastDepth, 1)
        XCTAssertEqual(card.lastBoundaries, 2)
        // Both computed server-side — the client never re-derives them.
        XCTAssertEqual(card.dueLabel, "3 days overdue")
        XCTAssertEqual(card.daysSinceReview, 9)
    }

    func testAnUnscoredLibraryRowLeavesEveryAxisNil() throws {
        let json = Data(#"""
        [{"id":"00000000-0000-0000-0000-0000000000c9","topic":"Virtual memory",
          "category":"Operating Systems","delivery_mode":"conversational",
          "mastery_summary":"","last_score":null,"last_accuracy":null,
          "last_depth":null,"last_boundaries":null,
          "ease_factor":2.5,"interval_days":1,"repetitions":0,
          "next_review_at":"2026-08-01","due_label":"due in 5 days",
          "days_since_review":null,"missed_count":0}]
        """#.utf8)

        let card = try XCTUnwrap(try LiveAPI.decoder.decode([CardSummary].self, from: json).first)

        XCTAssertNil(card.lastScore)
        XCTAssertNil(card.lastAccuracy)
        XCTAssertNil(card.daysSinceReview)
        // Coverage groups it as `untested`, not as a zero.
        XCTAssertEqual(ScoreStyle.Tier.of(card.lastScore), .untested)
    }

    // MARK: - The answer outcome

    func testACompletedAnswerCarriesThePracticeFlag() throws {
        let json = Data(#"""
        {"status":"complete","score":4,"feedback":"Solid.",
         "next_review_at":"2026-07-30","interval_days":6,"practice":true}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(
            let score, _, _, _, _, let interval, let practice, _, _, _, _, _
        ) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertEqual(score, 4)
        XCTAssertEqual(interval, 6)
        XCTAssertTrue(practice)
    }

    func testAnOutcomeWithoutThePracticeFlagReadsAsADailyReview() throws {
        // A server that predates practice mode omits the field entirely; the
        // schedule line must still be the real one rather than crashing or
        // claiming the schedule was untouched.
        let json = Data(#"""
        {"status":"complete","score":3,"feedback":"Partial.",
         "next_review_at":"2026-07-28","interval_days":1}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(
            _, _, _, _, _, _, let practice, let reattempt, let prompt, _, _, _
        ) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertFalse(practice)
        // Same reasoning for the coached re-attempt: a server that predates it
        // omits the field, and the affordance must stay hidden rather than offer a
        // turn the server would reject.
        XCTAssertFalse(reattempt)
        XCTAssertNil(prompt)
    }

    func testACompletedAnswerCarriesTheReattemptOffer() throws {
        // Server-computed from `accuracy <= 2`. The client never receives
        // the axis itself — the score block shows one numeral, the composite.
        let json = Data(#"""
        {"status":"complete","score":1,"feedback":"The mechanism is arc ownership.",
         "next_review_at":"2026-07-29","interval_days":1,"practice":false,
         "reattempt_offered":true,"reattempt_prompt":"In your words — What moves?"}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)

        guard case .complete(
            let score, _, _, _, _, _, _, let reattempt, let prompt, _, _, _
        ) = outcome else {
            return XCTFail("expected a completed outcome, got \(outcome)")
        }
        XCTAssertEqual(score, 1)
        XCTAssertTrue(reattempt)
        // The server composes the prompt: on a resumed follow-up session the client
        // cannot reconstruct it, because its only `.question` entry is the probe.
        XCTAssertEqual(prompt, "In your words — What moves?")
    }

    func testAV2OutcomeCarriesRecallAndQualitativeCoaching() throws {
        let json = Data(#"""
        {"status":"complete","score":4,"recall_score":4,
         "scoring_contract_version":2,"feedback":"Essential idea recalled.",
         "next_review_at":"2026-07-30","interval_days":6,"practice":false,
         "coaching_offered":true,"coaching_focus":"depth",
         "coaching_question":"One level deeper — why does it work?"}
        """#.utf8)

        let outcome = try LiveAPI.decoder.decode(AnswerOutcome.self, from: json)
        guard case .complete(
            let score, let recall, let version, _, _, _, _, _, _, let offered,
            let focus, let question
        ) = outcome else { return XCTFail("expected V2 completion") }
        XCTAssertEqual(score, 4)
        XCTAssertEqual(recall, 4)
        XCTAssertEqual(version, 2)
        XCTAssertTrue(offered)
        XCTAssertEqual(focus, "depth")
        XCTAssertEqual(question, "One level deeper — why does it work?")
    }

    // MARK: - Coverage's tier vocabulary

    func testTheCoverageTiersSplitTheMiddleOfTheScoreRange() {
        // Deliberately finer than Today's four bands: a 3 is `developing`, not
        // the same bucket as a 2.
        XCTAssertEqual(ScoreStyle.Tier.of(nil), .untested)
        XCTAssertEqual(ScoreStyle.Tier.of(0), .cold)
        XCTAssertEqual(ScoreStyle.Tier.of(1), .cold)
        XCTAssertEqual(ScoreStyle.Tier.of(2), .shaky)
        XCTAssertEqual(ScoreStyle.Tier.of(3), .developing)
        XCTAssertEqual(ScoreStyle.Tier.of(4), .solid)
        XCTAssertEqual(ScoreStyle.Tier.of(5), .solid)
    }

    // MARK: - Actionable Study Plan items

    private static let actionablePlanItem = Data(#"""
    {"id":"00000000-0000-0000-0000-000000000010",
     "plan_id":"00000000-0000-0000-0000-000000000001",
     "plan_revision":7,
     "full_title":"Practice sliding window in Python","phase_title":"Foundations",
     "week_index":1,"type":"practice","priority":"core","status":"pending",
     "why_it_matters":"State the invariant before coding.",
     "done_when":"Solve one problem in Python without a hint.",
     "estimate_minutes":90,"estimate_source":"imported","estimate_confidence":"high",
     "source_excerpt":"","source_label":"","recall_supported":true,"notes":"",
     "study_block_label":"","study_block_weekday":null,
     "study_block_minute_of_day":null,"study_block_reminder_on":false,
     "completed_at":null,"reopened_at":null,"linked_card_ids":[],
     "card_proposals_available":false,"practice_debrief_eligible":false,
     "practice_debrief":null,"blocked_by":[],
     "resources":[{"kind":"lesson","provider":"Hello Interview",
       "label":"Sliding window","action_label":"Open pattern",
       "url":"https://www.hellointerview.com/learn/code/sliding-window/overview",
       "language":"Python"}],
     "stretch_actions":[{"title":"Solve an unseen medium",
       "done_when":"Write the invariant first.","minutes":45,
       "resource_url":"https://leetcode.com/problemset/",
       "resource_label":"LeetCode · Problemset"}],
     "mapped_recall_cards":[{"topic":"Sliding-window invariant","card_id":null,
       "availability_label":"available after practice"}]}
    """#.utf8)

    func testActionableStudyPlanFieldsDecodeFromSnakeCase() throws {
        let item = try LiveAPI.decoder.decode(
            PlanItemDetail.self, from: Self.actionablePlanItem
        )

        XCTAssertEqual(item.actionableResources.first?.provider, "Hello Interview")
        XCTAssertEqual(item.planRevision, 7)
        XCTAssertEqual(item.actionableResources.first?.language, "Python")
        XCTAssertEqual(item.advisoryStretchActions.first?.minutes, 45)
        XCTAssertNil(item.recallMappings.first?.cardId)
        XCTAssertEqual(item.recallMappings.first?.topic, "Sliding-window invariant")
    }

    func testActionableStudyPlanFieldsRemainOptionalDuringRollout() throws {
        let json = Data(#"""
        {"id":"00000000-0000-0000-0000-000000000010",
         "plan_id":"00000000-0000-0000-0000-000000000001",
         "full_title":"Legacy item","phase_title":"Foundations","week_index":1,
         "type":"learn","priority":"core","status":"pending",
         "why_it_matters":"","done_when":"","estimate_minutes":30,
         "estimate_source":"imported","estimate_confidence":"high",
         "source_excerpt":"","source_label":"","recall_supported":false,"notes":"",
         "study_block_label":"","study_block_weekday":null,
         "study_block_minute_of_day":null,"study_block_reminder_on":false,
         "completed_at":null,"reopened_at":null,"linked_card_ids":[],
         "card_proposals_available":false,"practice_debrief_eligible":false,
         "practice_debrief":null,"blocked_by":[]}
        """#.utf8)

        let item = try LiveAPI.decoder.decode(PlanItemDetail.self, from: json)

        XCTAssertNil(item.planRevision)
        XCTAssertTrue(item.actionableResources.isEmpty)
        XCTAssertTrue(item.advisoryStretchActions.isEmpty)
        XCTAssertTrue(item.recallMappings.isEmpty)
    }

    func testExternalStudyLinksRejectExecutableAndFileSchemes() {
        XCTAssertNotNil(SafeExternalURL.parse("https://www.hellointerview.com/learn/code"))
        XCTAssertNotNil(SafeExternalURL.parse("http://localhost:8083/source"))
        XCTAssertNil(SafeExternalURL.parse("javascript:alert(1)"))
        XCTAssertNil(SafeExternalURL.parse("file:///tmp/answer.txt"))
        XCTAssertNil(SafeExternalURL.parse("not a url"))
    }

    func testActionableRoutesMirrorWeekOneCurriculumMetadata() {
        let lesson = StudyPlanFixtures.actionableItemDetail()
        let python = StudyPlanFixtures.pythonItemDetail()
        let week = StudyPlanFixtures.week1Detail()

        XCTAssertEqual(lesson.weekIndex, 1)
        XCTAssertEqual(lesson.phaseTitle, "Foundations")
        XCTAssertEqual(lesson.estimateMinutes, 180)
        XCTAssertEqual(lesson.estimateSource, "imported")
        XCTAssertTrue(lesson.recallSupported)
        XCTAssertEqual(lesson.recallMappings.count, 3)

        XCTAssertEqual(python.weekIndex, 1)
        XCTAssertEqual(python.phaseTitle, "Foundations")
        XCTAssertEqual(python.estimateMinutes, 120)
        XCTAssertFalse(python.recallSupported)
        XCTAssertTrue(python.recallMappings.isEmpty)
        XCTAssertEqual(python.actionableResources.count, 3)

        XCTAssertEqual(week.index, 1)
        XCTAssertEqual(week.plannedMinutes, 1_200)
        XCTAssertEqual(week.capacityMinutes, 1_200)
    }

    func testPilotPreviewDecodesWithoutAnyAnswerAuthority() throws {
        let data = Data(#"""
        {"id":"00000000-0000-0000-0000-000000000901",
         "title":"Networking 101","kind":"article",
         "source_url":"https://example.com/networking",
         "content_provenance":"exact_source_excerpt","status":"ready",
         "import_path":"lesson","intent":"already_studied",
         "clean_count":1,"attention_count":0,"error":"",
         "lesson_grounding_required":false,
         "proposals_ready_at":"2026-08-15T16:02:00.123456Z",
         "review_opened_at":"2026-08-15T16:04:00Z","confirmed_at":null,
         "topics":[{"id":"00000000-0000-0000-0000-000000000902",
           "position":1,"section_title":"Network layer",
           "topic":"Network layer best-effort delivery",
           "formation_question":"How does best-effort IP shape transport?",
           "status":"clean","issue":"","formation_state":"not_started",
           "transfer_state":"locked"}]}
        """#.utf8)

        let preview = try LiveAPI.decoder.decode(MaterialLessonPreview.self, from: data)

        XCTAssertEqual(preview.topics.count, 1)
        XCTAssertTrue(preview.topics[0].isAvailable)
        XCTAssertEqual(preview.topics[0].transferState, "locked")
        XCTAssertFalse(preview.topics[0].hasTransferEntryPoint)
        XCTAssertEqual(preview.topics[0].formationQuestion, "How does best-effort IP shape transport?")
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        let topics = try XCTUnwrap(object["topics"] as? [[String: Any]])
        for forbidden in [
            "answer_anchor", "source_excerpt", "answer_basis", "answer_rubric",
            "recall_questions", "feedback", "canonical_question"
        ] {
            XCTAssertNil(topics[0][forbidden], "preview leaked \(forbidden)")
        }
    }

    func testLessonCheckGetShapeDecodesWithoutCorrectionOrAuthority() throws {
        let data = Data(#"""
        {"id":"00000000-0000-0000-0000-000000000903",
         "proposal_id":"00000000-0000-0000-0000-000000000902",
         "card_id":null,"kind":"formation","condition":"attempt_first",
         "prompt_level":"canonical","prompt_version":"formation-v1",
         "prompt_text":"How does best-effort IP shape transport?","status":"open",
         "draft_text":"IP routes packets","qualitative_outcome":null,
         "has_feedback":false,"exposed_at":null,"recall_not_before_at":null,
         "available_at":null,"started_at":"2026-08-15T16:05:00Z",
         "submitted_at":null,"updated_at":"2026-08-15T16:05:01.012345Z"}
        """#.utf8)

        let check = try LiveAPI.decoder.decode(LessonCheck.self, from: data)

        XCTAssertEqual(check.kind, .formation)
        XCTAssertEqual(check.condition, .attemptFirst)
        XCTAssertEqual(check.draftText, "IP routes packets")
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        for forbidden in [
            "feedback", "source_excerpt", "answer_basis", "answer_rubric",
            "recall_questions", "score"
        ] {
            XCTAssertNil(object[forbidden], "GET check leaked \(forbidden)")
        }
    }

    func testAuthorityWriteResponseDecodesQualitativeFormation() throws {
        let data = Data(#"""
        {"check":{"id":"00000000-0000-0000-0000-000000000903",
          "proposal_id":"00000000-0000-0000-0000-000000000902",
          "card_id":null,"kind":"formation","condition":"attempt_first",
          "prompt_level":"canonical","prompt_version":"formation-v1",
          "prompt_text":"How does best-effort IP shape transport?","status":"exposed",
          "draft_text":"","qualitative_outcome":"missing_mechanism",
          "has_feedback":true,"exposed_at":"2026-08-15T16:06:00Z",
          "recall_not_before_at":"2026-08-16T07:00:00Z","available_at":null,
          "started_at":"2026-08-15T16:05:00Z","submitted_at":"2026-08-15T16:06:00Z",
          "updated_at":"2026-08-15T16:06:00Z"},
         "proposal_id":"00000000-0000-0000-0000-000000000902",
         "topic":"Network layer best-effort delivery","section_title":"Network layer",
         "source_title":"Networking 101","source_url":"https://example.com/networking",
         "content_provenance":"exact_source_excerpt",
         "source_excerpt":"IP offers best-effort packet delivery.",
         "answer_basis":"Transport adds the guarantees an application needs.",
         "canonical_question":"How does best-effort IP shape transport?",
         "answer_rubric":{"mechanism":"Name loss, reordering, and duplication."},
         "recall_questions":[{"level":"mechanism","question":"Where do guarantees live?"}],
         "feedback":"Name the missing delivery mechanism.",
         "exposed_at":"2026-08-15T16:06:00Z",
         "recall_not_before_at":"2026-08-16T07:00:00Z",
         "confirmation_title":"Approve this grounded concept?",
         "confirmation_message":"Approval creates a held Recall card."}
        """#.utf8)

        let authority = try LiveAPI.decoder.decode(MaterialTopicAuthority.self, from: data)

        XCTAssertEqual(authority.check.qualitativeOutcome, .missingMechanism)
        XCTAssertTrue(authority.check.hasFeedback)
        XCTAssertEqual(authority.answerRubric["mechanism"], "Name loss, reordering, and duplication.")
        XCTAssertEqual(authority.recallQuestions.map(\.level), ["mechanism"])
    }

    func testEnrolledPilotPollingStillDecodesTheLegacyImportShapeWithEmptyTopics() throws {
        let data = Data(#"""
        {"id":"00000000-0000-0000-0000-000000000901",
         "title":"Networking 101","kind":"article",
         "source_url":"https://example.com/networking",
         "content_provenance":"exact_source_excerpt","version":1,"status":"ready",
         "import_path":"lesson","intent":"already_studied","original_filename":"",
         "character_count":876,"clean_count":1,"attention_count":0,"error":"",
         "plan_draft_id":null,"comparison":{},"topics":[],
         "lesson_grounding_required":false,"artifacts_ready":false,"distilled_at":null,
         "created_at":"2026-08-15T16:00:00Z","updated_at":"2026-08-15T16:02:00Z"}
        """#.utf8)

        let value = try LiveAPI.decoder.decode(MaterialImport.self, from: data)

        XCTAssertEqual(value.status, "ready")
        XCTAssertEqual(value.cleanCount, 1)
        XCTAssertTrue(value.topics.isEmpty)
        XCTAssertEqual(value.importPath, "lesson")
    }
}
