import Foundation

/// Study Plan fixtures, matching the design handoff frame for frame.
///
/// The Week 4 table is the canonical fixture from V3.4 §3, reproduced item for
/// item — 660 of 720 minutes, the 420-minute override, and the 240-minute
/// overflow landing as 60 in Week 5 and 180 in Week 6. Those numbers are also
/// what `tests/test_study_plan_scheduler.py` asserts against, so a mock that
/// drifts from them is a mock that no longer shows what the app will do.
///
/// The anatomy plan is V3.5 STATE 9: the same compression on a subject with no
/// technical-interview vocabulary anywhere and no recall cards available.
enum StudyPlanFixtures {

    // Deterministic ids so a screenshot route lands on the same row every time.
    static func id(_ byte: UInt8) -> UUID {
        UUID(uuid: (0x5B, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, byte))
    }

    static let backendPlanID = id(0x01)
    static let anatomyPlanID = id(0x02)
    static let completedPlanID = id(0x03)
    static let archivedPlanID = id(0x04)
    static let draftID = id(0x05)
    static let practiceItemID = id(0x13)
    static let practiceDebriefID = id(0x30)

    // MARK: - the map

    static let backendPhases: [PlanPhaseRow] = [
        PlanPhaseRow(
            index: 1, displayTitle: "Foundations", fullTitle: "Foundations",
            status: "Complete", weekRange: "Weeks 1–3", currentWeekLabel: nil,
            weeks: [
                week(1, "The request path", "The request path", "Complete"),
                week(2, "Storage engines", "Storage engines and transactions", "Complete"),
                week(3, "Caching & sharding",
                     "Caching, sharding, consistent hashing, and CAP", "Complete"),
            ]
        ),
        PlanPhaseRow(
            index: 2, displayTitle: "Technologies", fullTitle: "Technologies",
            status: "Current", weekRange: "Weeks 4–6", currentWeekLabel: "Week 4",
            weeks: [
                week(4, "Databases", "Relational and NoSQL systems", "Current", current: true),
                week(5, "Streaming & search",
                     "Streaming, search, and large-object delivery", "Upcoming"),
                week(6, "Coordination",
                     "Coordination, stream processing, and approximate structures", "Upcoming"),
            ]
        ),
        PlanPhaseRow(
            index: 3, displayTitle: "Patterns and application",
            fullTitle: "Patterns & application", status: "Next",
            weekRange: "Weeks 7–9", currentWeekLabel: nil,
            weeks: [
                week(7, "Resilience patterns",
                     "Rate limiting, idempotency, and back-pressure", "Upcoming"),
                week(8, "Applied design", "Two complete system designs", "Upcoming"),
                week(9, "Timed coding",
                     "Timed coding under interview conditions", "Upcoming"),
            ]
        ),
        PlanPhaseRow(
            index: 4, displayTitle: "Simulation", fullTitle: "Simulation",
            status: "Later", weekRange: "Weeks 10–12", currentWeekLabel: nil,
            weeks: [
                week(10, "Mocks", "Mock interviews and observed gaps", "Upcoming"),
                week(11, "Target companies", "Target-company overlays", "Upcoming"),
                week(12, "Final pass",
                     "Behavioral rehearsal and final gap review", "Upcoming"),
            ]
        ),
    ]

    /// V3.5 STATE 9. Four phases, ten weeks, no interview vocabulary anywhere —
    /// and `supportsRecallCards: false`, because the technical scoring rubric
    /// cannot grade a clinical answer.
    static let anatomyPhases: [PlanPhaseRow] = [
        PlanPhaseRow(
            index: 1, displayTitle: "Cell and tissue", fullTitle: "Cell & tissue",
            status: "Complete", weekRange: "Weeks 1–2", currentWeekLabel: nil,
            weeks: [
                week(1, "Tissue types",
                     "Epithelial and connective tissue classification", "Complete"),
                week(2, "Membrane transport",
                     "Membrane transport and resting potential", "Complete"),
            ]
        ),
        PlanPhaseRow(
            index: 2, displayTitle: "Cardiovascular", fullTitle: "Cardiovascular",
            status: "Current", weekRange: "Weeks 3–5", currentWeekLabel: "Week 4",
            weeks: [
                week(3, "Cardiac cycle", "Chamber anatomy and the cardiac cycle", "Complete"),
                week(4, "Cardiac conduction",
                     "Cardiovascular electrical conduction and rhythm", "Current",
                     current: true),
                week(5, "Circulation",
                     "Systemic and pulmonary circulation pressures", "Upcoming"),
            ]
        ),
        PlanPhaseRow(
            index: 3, displayTitle: "Respiratory and renal",
            fullTitle: "Respiratory & renal", status: "Next",
            weekRange: "Weeks 6–8", currentWeekLabel: nil,
            weeks: [
                week(6, "Gas exchange", "Ventilation, perfusion, and gas exchange", "Upcoming"),
                week(7, "Filtration",
                     "Glomerular filtration and tubular transport", "Upcoming"),
                week(8, "Acid–base", "Acid–base balance and renal compensation", "Upcoming"),
            ]
        ),
        PlanPhaseRow(
            index: 4, displayTitle: "Integration", fullTitle: "Integration",
            status: "Later", weekRange: "Weeks 9–10", currentWeekLabel: nil,
            weeks: [
                week(9, "Compensation",
                     "Multi-system compensation under haemorrhage", "Upcoming"),
                week(10, "Case recall",
                     "Whole-body case reconstruction from memory", "Upcoming"),
            ]
        ),
    ]

    /// A five-phase plan. The overview's one-viewport budget is specified for
    /// 3–5 phases, so the upper bound needs a fixture to be checked against.
    static let fivePhases: [PlanPhaseRow] = backendPhases + [
        PlanPhaseRow(
            index: 5, displayTitle: "Offer conversations",
            fullTitle: "Offer conversations", status: "Later",
            weekRange: "Weeks 13–14", currentWeekLabel: nil,
            weeks: [
                week(13, "Compensation talk", "Compensation and levelling", "Upcoming"),
                week(14, "Decision framing", "Comparing offers and deciding", "Upcoming"),
            ]
        )
    ]

    private static func week(
        _ index: Int, _ short: String, _ full: String, _ status: String,
        current: Bool = false
    ) -> PlanWeekRow {
        PlanWeekRow(
            index: index, displayTitle: short, fullTitle: full,
            status: status, isCurrent: current
        )
    }

    static func overview(
        phases: [PlanPhaseRow] = backendPhases,
        id: UUID = backendPlanID,
        title: String = "Senior backend interview",
        subject: String = "Senior backend interview",
        weekTotal: Int = 12,
        status: String = "active",
        mode: String = "flexible",
        supportsCards: Bool = true
    ) -> PlanOverview {
        PlanOverview(
            id: id, title: "Study plan", subject: subject, mode: mode, status: status,
            weekIndex: 4, weekTotal: weekTotal,
            forecastLabel: "Est. completion · week of 19 Oct",
            forecastEndPlanWeek: weekTotal, revision: 7,
            supportsRecallCards: supportsCards, phases: phases,
            latestChange: "Week 4 capacity set to 7h · 2 days ago"
        )
    }

    // MARK: - Week 4, item for item (V3.4 §3)

    /// id, title, type, priority, minutes, complete.
    private static let week4Rows:
        [(UInt8, String, String, Bool, Int, Bool)] = [
        (0x10, "PostgreSQL query and write paths", "learn", false, 90, true),
        (0x11, "Redis, DynamoDB, and Cassandra", "learn", false, 90, true),
        (0x12, "API gateway mechanics", "learn", false, 60, false),
        (0x13, "Three timed coding sessions", "practice", false, 120, true),
        (0x14, "Begin the behavioral story catalog", "practice", false, 90, false),
        (0x15, "Consistent-hashing explanation", "practice", true, 30, false),
        (0x16, "Read one published architecture write-up", "practice", true, 60, false),
        (0x17, "Closed-book write-path explanation", "retrieve", false, 30, true),
        (0x18, "Cassandra versus DynamoDB", "retrieve", false, 30, true),
        (0x19, "Redraw the gateway request flow", "retrieve", false, 30, false),
        (0x1A, "Weekly closed-book pass on Weeks 1–3", "retrieve", false, 30, false),
    ]

    static let firstItemID = id(0x10)
    static let openItemID = id(0x12)

    static func weekDetail(planID: UUID = backendPlanID) -> WeekDetail {
        func rows(_ kind: String) -> [WeekItemRow] {
            week4Rows.filter { $0.2 == kind }.map {
                WeekItemRow(
                    id: id($0.0), title: $0.1, estimateMinutes: $0.4,
                    complete: $0.5, optional: $0.3, blockedBy: nil
                )
            }
        }
        let retrieveRows = rows("retrieve")
        let done = retrieveRows.filter(\.complete).count

        return WeekDetail(
            planId: planID, index: 4, phaseTitle: "Technologies",
            fullTitle: "Relational and NoSQL systems", displayTitle: "Databases",
            coreComplete: 3, coreTotal: 5,
            // 660 of 720 — the canonical baseline.
            plannedMinutes: 660, capacityMinutes: 720,
            coreLine: "3 of 5 Core complete",
            capacityLine: "11h of 12h planned",
            sections: [
                WeekSection(
                    type: "learn", label: "LEARN", aside: "Core · 240 min",
                    note: "", rows: rows("learn")
                ),
                WeekSection(
                    type: "practice", label: "PRACTICE",
                    aside: "Core · 210 min · Optional · 90 min", note: "",
                    rows: rows("practice")
                ),
                WeekSection(
                    type: "retrieve", label: "RETRIEVE",
                    aside: "\(done) of \(retrieveRows.count) done · Doesn't block the week",
                    note: "Reviews continue separately in Today. Retrieval time still "
                        + "counts toward this week's 12h.",
                    rows: retrieveRows
                ),
            ]
        )
    }

    static func itemDetail(
        _ itemID: UUID = firstItemID, planID: UUID = backendPlanID, complete: Bool = true,
        debrief: PracticeDebrief? = nil
    ) -> PlanItemDetail {
        let row = week4Rows.first { id($0.0) == itemID } ?? week4Rows[0]
        return PlanItemDetail(
            id: itemID, planId: planID, fullTitle: row.1, phaseTitle: "Technologies",
            weekIndex: 4, type: row.2, priority: row.3 ? "optional" : "core",
            status: complete ? "complete" : "pending",
            whyItMatters: "Connects indexing, transactions, replication, and write "
                + "bottlenecks into one system-level model.",
            doneWhen: "Explain the read and write paths closed-book in under two "
                + "minutes, including one scaling limit and one failure mode.",
            estimateMinutes: row.4, estimateSource: "user_edited",
            estimateConfidence: "high",
            sourceExcerpt: "PostgreSQL: query planning, indexes, MVCC, WAL, and "
                + "replication.",
            sourceLabel: "Hello Interview · PostgreSQL",
            recallSupported: true,
            notes: "Write out the WAL path once — that's the part I lose under pressure.",
            studyBlockLabel: "Tue 19:00", studyBlockWeekday: 2,
            studyBlockMinuteOfDay: 1140, studyBlockReminderOn: true,
            completedAt: complete ? Date(timeIntervalSince1970: 1_755_388_800) : nil,
            reopenedAt: nil,
            linkedCardIds: [],
            cardProposalsAvailable: complete && (row.2 != "practice" || debrief?.isSubmitted == true),
            practiceDebriefEligible: complete && row.2 == "practice",
            practiceDebrief: debrief,
            blockedBy: []
        )
    }

    static func practiceDebrief(
        planID: UUID = backendPlanID,
        itemID: UUID = practiceItemID,
        draft: String = "",
        submitted: Bool = false,
        proposalCount: Int = 0
    ) -> PracticeDebrief {
        let text = submitted
            ? "I drew the retry loop before the timeout and could not explain the upstream timeout path."
            : ""
        return PracticeDebrief(
            id: practiceDebriefID, planId: planID, itemId: itemID,
            draftText: draft.isEmpty ? text : draft, text: text,
            submittedAt: submitted ? Date(timeIntervalSince1970: 1_755_388_920) : nil,
            summary: text, proposalCount: proposalCount
        )
    }

    // MARK: - decisions

    /// V3.5 STATE 8. The canonical override: 7 hours doesn't fit Week 4, and the
    /// 240 displaced minutes land as 60 in Week 5 and 180 in Week 6.
    static let replan = PlanProposal(
        kind: "replan", basePlanRevision: 7,
        headline: "7 hours doesn't fit Week 4.",
        body: "360 minutes of completed work stays put, leaving 60 of the 420 "
            + "minutes for work still open. 240 minutes carry forward.",
        weeks: [
            placement(4, capacity: 420, before: 660, after: 420),
            placement(5, capacity: 720, before: 660, after: 720),
            placement(6, capacity: 720, before: 480, after: 660),
        ],
        moves: [
            PlanItemMove(title: "Begin the behavioral story catalog",
                         fromWeek: 4, toWeek: 6, minutes: 90),
            PlanItemMove(title: "Consistent-hashing explanation",
                         fromWeek: 4, toWeek: 5, minutes: 30),
            PlanItemMove(title: "Read one published architecture write-up",
                         fromWeek: 4, toWeek: 6, minutes: 60),
            PlanItemMove(title: "Redraw the gateway request flow",
                         fromWeek: 4, toWeek: 5, minutes: 30),
            PlanItemMove(title: "Weekly closed-book pass on Weeks 1–3",
                         fromWeek: 4, toWeek: 6, minutes: 30),
        ],
        unresolved: [], unresolvedMinutes: 0, displacedMinutes: 240,
        forecastEndPlanWeek: 12,
        forecastLabel: "Est. completion · week of 19 Oct — unchanged",
        forecastChanged: false, canApply: true, reasons: [],
        unchanged: [
            "Your cards, scores, session history, mastery summaries, and review "
                + "schedule are untouched.",
            "Completed work stays in the week you did it.",
        ]
    )

    /// V3.4 STATE 6. Weeks 5 and 6 are already full, so Core work is left with
    /// 30 minutes of nowhere to go and Apply is natively disabled.
    static let replanBlocked = PlanProposal(
        kind: "replan", basePlanRevision: 7,
        headline: "30 minutes have nowhere to go.",
        body: "Apply stays disabled until the selected option resolves every "
            + "week. Core work is never dropped to force a fit.",
        weeks: [
            placement(4, capacity: 420, before: 660, after: 420),
            placement(5, capacity: 720, before: 690, after: 720),
            placement(6, capacity: 720, before: 690, after: 720),
        ],
        moves: [
            PlanItemMove(title: "Redraw the gateway request flow",
                         fromWeek: 4, toWeek: 5, minutes: 30),
            PlanItemMove(title: "Weekly closed-book pass on Weeks 1–3",
                         fromWeek: 4, toWeek: 6, minutes: 30),
        ],
        unresolved: [
            PlanUnresolved(title: "Begin the behavioral story catalog",
                           minutes: 90, reason: "no_room")
        ],
        unresolvedMinutes: 30, displacedMinutes: 60, forecastEndPlanWeek: 12,
        forecastLabel: "Est. completion · week of 19 Oct",
        forecastChanged: false, canApply: false,
        reasons: ["work_unplaced", "core_removed_without_confirmation"],
        unchanged: [
            "Your cards, scores, session history, mastery summaries, and review "
                + "schedule are untouched.",
            "Completed work stays in the week you did it.",
        ]
    )

    /// V3.4 STATE 7. The deadline does not move; the gap is closed by choice.
    static let fixedRecovery = PlanProposal(
        kind: "resume", basePlanRevision: 7,
        headline: "The 18 Oct deadline is still fixed.",
        body: "68h of plan work remains and 44h is available across the last four "
            + "plan weeks. Each option below closes the full 24h gap.",
        weeks: [
            placement(9, capacity: 660, before: 1_020, after: 1_020),
            placement(10, capacity: 660, before: 1_020, after: 1_020),
            placement(11, capacity: 660, before: 1_020, after: 1_020),
            placement(12, capacity: 660, before: 1_020, after: 1_020),
        ],
        moves: [], unresolved: [
            PlanUnresolved(title: "Third full system design", minutes: 180, reason: "no_room")
        ],
        unresolvedMinutes: 1_440, displacedMinutes: 0, forecastEndPlanWeek: 12,
        forecastLabel: "Est. completion · week of 19 Oct",
        forecastChanged: false, canApply: false, reasons: ["deadline_missed"],
        unchanged: [
            "The deadline doesn't move, and no exact completion day is claimed.",
            "Your cards, scores, session history, mastery summaries, and review "
                + "schedule are untouched.",
        ]
    )

    /// V3.4 STATE 8. Reopening resolves on its own — nothing moves.
    static let reopen = PlanProposal(
        kind: "reopen", basePlanRevision: 7,
        headline: "Reopening this changes nothing else.",
        body: "Week 4 still fits its capacity, so no work carries forward and the "
            + "forecast is unchanged.",
        weeks: [placement(4, capacity: 420, before: 420, after: 420)],
        moves: [], unresolved: [], unresolvedMinutes: 0, displacedMinutes: 0,
        forecastEndPlanWeek: 12,
        forecastLabel: "Est. completion · week of 19 Oct",
        forecastChanged: false, canApply: true, reasons: [],
        unchanged: [
            "Reopening changes Study Plan progress only.",
            "Your cards, scores, session history, mastery summaries, and review "
                + "schedule are untouched.",
            "Completed work stays in the week you did it.",
        ]
    )

    /// V3.4 STATE 9. The same reopen, with the option that doesn't resolve.
    static let reopenBlocked = PlanProposal(
        kind: "reopen", basePlanRevision: 7,
        headline: "90 minutes have nowhere to go.",
        body: "Carrying the gateway item into Week 5 doesn't help: Week 5 is "
            + "already full. Apply stays disabled until every week resolves.",
        weeks: [
            placement(4, capacity: 420, before: 420, after: 450),
            placement(5, capacity: 720, before: 720, after: 780),
        ],
        moves: [
            PlanItemMove(title: "API gateway mechanics", fromWeek: 4, toWeek: 5, minutes: 60)
        ],
        unresolved: [
            PlanUnresolved(title: "PostgreSQL query and write paths",
                           minutes: 90, reason: "no_room")
        ],
        unresolvedMinutes: 90, displacedMinutes: 60, forecastEndPlanWeek: 12,
        forecastLabel: "Est. completion · week of 19 Oct",
        forecastChanged: false, canApply: false,
        reasons: ["week_over_capacity", "work_unplaced"],
        unchanged: [
            "Reopening changes Study Plan progress only.",
            "Your cards, scores, session history, mastery summaries, and review "
                + "schedule are untouched.",
        ]
    )

    private static func placement(
        _ index: Int, capacity: Int, before: Int, after: Int
    ) -> PlanWeekPlacement {
        PlanWeekPlacement(
            index: index, capacityMinutes: capacity, beforeMinutes: before,
            afterMinutes: after, changed: before != after, overCapacity: after > capacity
        )
    }

    // MARK: - preview

    /// V3.5 STATE 7. Two resolved checks read as one status line each; the three
    /// exceptions are the only rows with somewhere to go.
    static func preview(resolved: Bool = false, failed: Bool = false) -> PlanPreview {
        PlanPreview(
            draftId: draftID,
            status: failed ? "failed" : "ready",
            title: "Senior backend interview",
            subject: "Senior backend interview",
            mode: "flexible", weeks: 12, phases: 4,
            weeklyCapacityMinutes: 720, totalMinutes: 43_200,
            forecastLabel: "Est. completion · week of 19 Oct",
            supportsRecallCards: true,
            checks: failed ? [] : [
                check("capacity", "Capacity", resolved: true, value: "Fits"),
                check("estimates", "Workload estimates", resolved: resolved,
                      value: resolved ? "Reviewed" : "4 need review",
                      destination: "estimate-audit"),
                check("coverage", "Source coverage", resolved: resolved,
                      value: resolved ? "Complete" : "1 possible omission",
                      destination: "source-coverage"),
                check("retrieval", "Retrieval activities", resolved: resolved,
                      value: resolved ? "12 in the plan" : "4 need review",
                      destination: "retrieval-audit"),
                check("dependencies", "Dependencies", resolved: true, value: "Confirmed",
                      detail: "31 imported · 6 inferred"),
            ],
            canCreate: resolved && !failed,
            phaseRows: backendPhases.map {
                PreviewPhase(
                    index: $0.index, displayTitle: $0.displayTitle,
                    fullTitle: $0.fullTitle,
                    description: "See how concrete systems implement and trade off "
                        + "those foundations.",
                    weekRange: $0.weekRange
                )
            },
            error: failed
                ? "The import couldn't be completed. Your guide and settings are still here."
                : ""
        )
    }

    private static func check(
        _ key: String, _ label: String, resolved: Bool, value: String,
        detail: String = "", destination: String? = nil
    ) -> PlanCheck {
        PlanCheck(
            key: key, label: label, status: resolved ? "ok" : "needs_review",
            value: value, detail: detail,
            destination: resolved ? nil : destination
        )
    }

    // MARK: - lifecycle

    /// V3.4 STATE 16. Four lifecycle states, listed separately — completed is
    /// not archived.
    static func plans(hasActive: Bool = true) -> PlanList {
        PlanList(
            active: hasActive ? [
                entry(backendPlanID, "Senior backend interview", "active",
                      "Week 4 · Technologies · est. completion Week 12")
            ] : [],
            paused: [
                entry(anatomyPlanID, "Anatomy foundations", "paused",
                      "Paused 12 Jul · Week 6 of 10")
            ],
            completed: [
                entry(completedPlanID, "AWS certification", "completed",
                      "Completed 18 May · 32 of 32 Core")
            ],
            archived: [
                entry(archivedPlanID, "Systems reading list", "archived",
                      "Archived 3 Mar · 11 of 26 Core")
            ]
        )
    }

    private static func entry(
        _ id: UUID, _ title: String, _ status: String, _ meta: String
    ) -> PlanListEntry {
        PlanListEntry(
            id: id, title: title, subject: title, status: status, meta: meta,
            createdAt: Date(timeIntervalSince1970: 1_750_000_000)
        )
    }

    static let revisions: [PlanRevisionEntry] = [
        revision(0xA1, "capacity", "Week 4 capacity set to 7h", days: 2, reversible: true),
        revision(0xA2, "replan", "240 minutes carried forward · 60 to Week 5 · 180 to Week 6",
                 days: 2),
        revision(0xA3, "reopen", "Reopened “PostgreSQL query and write paths”", days: 5),
        revision(0xA4, "created", "Plan created from a guide · 96 items", days: 24),
    ]

    private static func revision(
        _ byte: UInt8, _ kind: String, _ summary: String, days: Int,
        reversible: Bool = false
    ) -> PlanRevisionEntry {
        PlanRevisionEntry(
            id: id(byte), kind: kind, summary: summary,
            createdAt: Date().addingTimeInterval(-Double(days) * 86_400),
            reversible: reversible
        )
    }

    /// V3.4 STATE 18. Plan work only: estimated, not measured, and global
    /// reviews are deliberately not counted.
    static let recap = PlanRecap(
        id: completedPlanID, title: "AWS certification",
        coreComplete: 32, coreTotal: 32, optionalDeferred: 3,
        estimatedMinutes: 3_960, learnPracticeMinutes: 3_480, retrievalMinutes: 480,
        retrievalCompleted: 18, practiceCompleted: 6,
        remainingGaps: ["Cardiac pharmacology", "Renal compensation"]
    )

    // MARK: - card proposals

    private static let gatePass: [CardGateResult] = [
        gate(1, true, "Hello Interview · PostgreSQL, and a failed closed-book attempt on 12 Aug"),
        gate(2, true, "Reconstructable in under two minutes · WAL append → flush → checkpoint"),
        gate(3, true, "Tests a scenario, not a definition · where the path stalls under load"),
        gate(4, true, "A different answer would reveal a real gap in write-path reasoning"),
        gate(5, true, "Better use of the budget than your index-types card, which scores 3"),
    ]

    private static func gate(
        _ index: Int, _ passed: Bool, _ reason: String
    ) -> CardGateResult {
        CardGateResult(
            questionIndex: index,
            question: [
                "What source lesson or observed failure justifies the card?",
                "Can its mechanism be reconstructed in under two minutes?",
                "Does it test a scenario rather than a definition?",
                "Would a different answer change an interview decision or reveal a real gap?",
                "Is it more useful than spending the same review budget on an existing "
                    + "weak card?",
            ][index - 1],
            passed: passed, reason: reason
        )
    }

    private static let passing = CardProposal(
        id: id(0x30), revision: 1, topic: "Postgres write path", category: "Databases",
        canonicalQuestion: "Walk the write path for a single row update, from WAL "
            + "append to checkpoint. Where can it stall under load?",
        reason: "The mechanism behind most write-throughput answers.",
        gate: gatePass, disposition: "suggested", duplicateCheckResult: "none",
        existingCardId: nil, existingCardContext: "",
        failedQuestionIndex: nil, failedReason: ""
    )

    private static let failing = CardProposal(
        id: id(0x31), revision: 1, topic: "Autovacuum thresholds", category: "Databases",
        canonicalQuestion: "What triggers autovacuum?",
        reason: "",
        gate: [
            gate(1, true, "Mentioned in the lesson."),
            gate(2, true, "Short enough."),
            gate(3, false, "Tests a definition, not a scenario"),
            gate(4, false, "Knowing the threshold wouldn't change a design decision."),
            gate(5, false, "Your index-types card is a better use of the same budget."),
        ],
        disposition: "not_suggested", duplicateCheckResult: "none",
        existingCardId: nil, existingCardContext: "",
        failedQuestionIndex: 3, failedReason: "Tests a definition, not a scenario"
    )

    private static let overlapping = CardProposal(
        id: id(0x32), revision: 1, topic: "Postgres write path", category: "Databases",
        canonicalQuestion: "Walk the write path for a single row update, from WAL "
            + "append to checkpoint.",
        reason: "Your existing card “Postgres index types” covers storage internals "
            + "but not the write path. Not an exact topic match.",
        gate: gatePass, disposition: "possible_overlap", duplicateCheckResult: "possible",
        existingCardId: id(0x40),
        existingCardContext: "Existing · written 2 Jun · score 3 · due 24 Aug",
        failedQuestionIndex: nil, failedReason: ""
    )

    static let cardBoundaryNote =
        "These cards join Today's review schedule. Their reviews don't change this Study Plan."

    /// V3.4 STATE 10: one passes, one is shown under NOT SUGGESTED with no action.
    static func proposals(
        planID: UUID = backendPlanID, itemID: UUID = firstItemID,
        variant: String = "default"
    ) -> CardProposalList {
        switch variant {
        case "none":
            return CardProposalList(
                planId: planID, itemId: itemID, supportsRecallCards: true,
                suggestedCount: 0, proposals: [failing], note: cardBoundaryNote
            )
        case "existing":
            return CardProposalList(
                planId: planID, itemId: itemID, supportsRecallCards: true,
                suggestedCount: 1, proposals: [overlapping], note: cardBoundaryNote
            )
        case "unsupported":
            return CardProposalList(
                planId: planID, itemId: itemID, supportsRecallCards: false,
                suggestedCount: 0, proposals: [],
                note: "This subject uses plan-local retrieval activities. Devmax "
                    + "recall cards are only proposed for subjects the scoring "
                    + "rubric can grade."
            )
        default:
            return CardProposalList(
                planId: planID, itemId: itemID, supportsRecallCards: true,
                suggestedCount: 1, proposals: [passing, failing], note: cardBoundaryNote
            )
        }
    }
}

// MARK: - MockAPI conformance
//
// Same shape as the rest of MockAPI: artificial latency so loading states are
// real, deterministic ids, and one `WC_PLAN_*` flag per failure variant. Forced
// failures succeed on the retry so each failure path walks end to end.

extension MockAPI {
    private func flags() async -> DebugFlags { await MainActor.run { DebugFlags.shared } }

    private func settle(_ ms: UInt64 = 300) async {
        try? await Task.sleep(nanoseconds: ms * 1_000_000)
    }

    func activePlan() async throws -> StudyPlanSummary {
        await settle(200)
        let f = await flags()
        if f.planSummaryFails { throw APIError.status(500) }
        if f.planNoActive { return .none }
        return StudyPlanSummary(
            active: true, planId: StudyPlanFixtures.backendPlanID,
            title: "Senior backend interview", subject: "Senior backend interview",
            weekIndex: 4, weekTotal: 12, phaseTitle: "Technologies",
            nextBlockLabel: "Tue 19:00"
        )
    }

    func plans() async throws -> PlanList {
        await settle()
        return StudyPlanFixtures.plans(hasActive: !(await flags()).planNoActive)
    }

    func planOverview(_ id: UUID) async throws -> PlanOverview {
        await settle()
        switch await flags().planVariant {
        case "anatomy":
            return StudyPlanFixtures.overview(
                phases: StudyPlanFixtures.anatomyPhases, id: StudyPlanFixtures.anatomyPlanID,
                title: "Anatomy foundations", subject: "Anatomy foundations",
                weekTotal: 10, supportsCards: false
            )
        case "five-phase":
            return StudyPlanFixtures.overview(phases: StudyPlanFixtures.fivePhases, weekTotal: 14)
        default:
            return StudyPlanFixtures.overview()
        }
    }

    func planWeek(_ id: UUID, index: Int) async throws -> WeekDetail {
        await settle()
        return StudyPlanFixtures.weekDetail(planID: id)
    }

    func planItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
        await settle(200)
        return StudyPlanFixtures.itemDetail(itemID, planID: id)
    }

    func editPlanItem(
        _ id: UUID, itemID: UUID, edit: PlanItemEdit
    ) async throws -> PlanItemDetail {
        await settle(200)
        return StudyPlanFixtures.itemDetail(itemID, planID: id)
    }

    func completePlanItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
        await settle(400)
        return StudyPlanFixtures.itemDetail(itemID, planID: id, complete: true)
    }

    func savePracticeDebriefDraft(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief {
        await settle(100)
        let row = PracticeDebrief(
            id: StudyPlanFixtures.practiceDebriefID, planId: id, itemId: itemID,
            draftText: text, text: "", submittedAt: nil, summary: text,
            proposalCount: 0
        )
        practiceDebriefs[itemID] = row
        return row
    }

    func submitPracticeDebrief(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief {
        await settle(500)
        debriefSubmitAttempts += 1
        if await flags().planFailDebriefSave, debriefSubmitAttempts % 2 == 1 {
            throw APIError.status(500)
        }
        let row = PracticeDebrief(
            id: StudyPlanFixtures.practiceDebriefID, planId: id, itemId: itemID,
            draftText: text, text: text, submittedAt: Date(), summary: text,
            proposalCount: 0
        )
        practiceDebriefs[itemID] = row
        return row
    }

    func previewReopen(_ id: UUID, itemID: UUID) async throws -> PlanProposal {
        await settle(400)
        return (await flags()).planReopenInvalid
            ? StudyPlanFixtures.reopenBlocked : StudyPlanFixtures.reopen
    }

    func reopenPlanItem(_ id: UUID, itemID: UUID, revision: Int) async throws -> PlanItemDetail {
        await settle(400)
        return StudyPlanFixtures.itemDetail(itemID, planID: id, complete: false)
    }

    func previewReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal {
        await settle(400)
        let f = await flags()
        if f.planFixedRecovery { return StudyPlanFixtures.fixedRecovery }
        return f.planReplanInvalid ? StudyPlanFixtures.replanBlocked : StudyPlanFixtures.replan
    }

    func applyReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal {
        await settle(500)
        return StudyPlanFixtures.replan
    }

    func updateWeekCapacity(
        _ id: UUID, index: Int, minutes: Int?, revision: Int
    ) async throws -> PlanProposal {
        await settle(400)
        // Lowering the week below what it holds displaces work, which is the
        // whole point of the capacity sheet's warning line.
        return (minutes ?? 720) < 660 ? StudyPlanFixtures.replan : StudyPlanFixtures.reopen
    }

    func pausePlan(_ id: UUID) async throws -> PlanOverview {
        await settle()
        return StudyPlanFixtures.overview(status: "paused")
    }

    func previewResume(_ id: UUID) async throws -> PlanProposal {
        await settle(400)
        return (await flags()).planFixedRecovery
            ? StudyPlanFixtures.fixedRecovery : StudyPlanFixtures.reopen
    }

    func applyResume(_ id: UUID, revision: Int) async throws -> PlanOverview {
        await settle()
        return StudyPlanFixtures.overview()
    }

    func activatePlan(_ id: UUID, revision: Int) async throws -> PlanOverview {
        await settle()
        return StudyPlanFixtures.overview()
    }

    func completePlan(_ id: UUID) async throws -> PlanOverview {
        await settle()
        return StudyPlanFixtures.overview(status: "completed")
    }

    func archivePlan(_ id: UUID) async throws -> PlanOverview {
        await settle()
        return StudyPlanFixtures.overview(status: "archived")
    }

    func duplicatePlan(_ id: UUID) async throws -> PlanOverview {
        await settle(500)
        return StudyPlanFixtures.overview(status: "paused")
    }

    func planRevisions(_ id: UUID) async throws -> [PlanRevisionEntry] {
        await settle(250)
        return StudyPlanFixtures.revisions
    }

    func planRecap(_ id: UUID) async throws -> PlanRecap {
        await settle()
        return StudyPlanFixtures.recap
    }

    func previewGuide(_ request: GuidePreviewRequest) async throws -> PlanPreview {
        // A real import takes minutes. The mock takes 1.5 seconds, which is long
        // enough for the progress state to be a real screen rather than a flash.
        await settle(1_500)
        return StudyPlanFixtures.preview(failed: await flags().planFailImport)
    }

    func retryPreview(draftID: UUID) async throws -> PlanPreview {
        await settle(1_500)
        // Retries succeed, so the failure path walks all the way through.
        return StudyPlanFixtures.preview()
    }

    func editPreview(draftID: UUID, edit: PreviewEdit) async throws -> PlanPreview {
        await settle(300)
        return StudyPlanFixtures.preview(resolved: true)
    }

    func createPlan(draftID: UUID, activate: Bool) async throws -> PlanOverview {
        await settle(600)
        return StudyPlanFixtures.overview()
    }

    func createCardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList {
        await settle(900)
        debriefCheckAttempts += 1
        if await flags().planFailDebriefCheck, debriefCheckAttempts % 2 == 1 {
            throw APIError.status(500)
        }
        return StudyPlanFixtures.proposals(
            planID: id, itemID: itemID, variant: await flags().planCardVariant
        )
    }

    func cardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList {
        await settle(300)
        return StudyPlanFixtures.proposals(
            planID: id, itemID: itemID, variant: await flags().planCardVariant
        )
    }

    func acceptCardProposals(
        _ id: UUID, selected: [UUID], idempotencyKey: String, revision: Int,
        edits: [String: [String: String]]
    ) async throws -> CardAcceptResult {
        await settle(700)
        cardAcceptAttempts += 1
        // The first attempt fails and the retry commits — the same alternating
        // shape the quick-add failure path uses, so STATE 12 and STATE 13 are
        // both reachable in one walk.
        if await flags().planFailAddCard, cardAcceptAttempts % 2 == 1 {
            throw APIError.status(500)
        }
        return CardAcceptResult(
            status: "committed", createdCardIds: selected, replayed: false
        )
    }

    func resolveDuplicate(_ id: UUID, proposalID: UUID, action: String) async throws {
        await settle(200)
    }
}
