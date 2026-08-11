import Foundation

/// The prototype's Tweaks, as runtime flags — this is how every designed state
/// (including the failure paths) is reachable in the simulator for screenshot
/// comparison. Forced failures succeed on the retry, exactly as the prototype
/// does, so each failure path can be walked end to end.
final class DebugFlags: ObservableObject {
    static let shared = DebugFlags()

    enum LoadState: String, CaseIterable { case auto, loading, error }
    /// The two prototyped progress rails. `dots` is the shipped option; `chips`
    /// stays reachable only for side-by-side comparison.
    enum RailStyle: String, CaseIterable { case dots, chips }

    @Published var useMockAPI: Bool
    @Published var loadState: LoadState
    @Published var failSubmit: Bool
    /// Fails `startSession`, which is the one call that reaches Claude before the
    /// user has said anything — so it is the state a card lands in when question
    /// generation is down.
    @Published var failQuestion: Bool
    /// Forces a failing mechanism score, which is what makes the coached
    /// re-attempt reachable — the affordance only appears below the band.
    @Published var failedMechanism: Bool
    @Published var scoringV2: Bool
    @Published var failAdd: Bool
    @Published var emptyQueue: Bool
    @Published var textFirst: Bool
    @Published var ttsEnabled: Bool
    /// Fakes streaming speech-to-text by typing the transcript out, as the
    /// prototype does — the simulator has no usable microphone. Defaults on in
    /// the simulator and *off* everywhere else, including a Debug build on a
    /// phone, which is the build the app actually gets used from.
    @Published var simulateSpeech: Bool
    @Published var railStyle: RailStyle

    // Study Plan. `planVariant` swaps the whole fixture set (`anatomy` proves the
    // map is subject-agnostic, `five-phase` checks the upper bound of the
    // one-viewport budget); the rest are one failure or empty state each.
    @Published var planNoActive: Bool
    /// Today must keep working when this endpoint does not.
    @Published var planSummaryFails: Bool
    @Published var planFailImport: Bool
    @Published var planFailAddCard: Bool
    @Published var planReplanInvalid: Bool
    @Published var planReopenInvalid: Bool
    @Published var planFixedRecovery: Bool
    @Published var planFailDebriefSave: Bool
    @Published var planFailDebriefCheck: Bool
    let planVariant: String
    let planCardVariant: String

    /// Drives the app straight to one designed state at launch, so every
    /// screenshot in the handoff can be reproduced and compared without hand
    /// navigation. Set via `simctl launch --setenv WC_ROUTE=…`.
    let route: String

    #if DEBUG
    private static let isDebug = true
    #else
    private static let isDebug = false
    #endif

    #if targetEnvironment(simulator)
    private static let isSimulator = true
    #else
    private static let isSimulator = false
    #endif

    private init() {
        // Release reads an empty environment, so every flag falls through to its
        // own default and there is no second list of values to keep in sync.
        let env = Self.isDebug ? ProcessInfo.processInfo.environment : [:]
        func flag(_ key: String, default fallback: Bool = false) -> Bool {
            guard let raw = env[key] else { return fallback }
            return raw == "1" || raw.lowercased() == "true"
        }

        // Both need the `isDebug` gate: a release build must never fall back to
        // fixtures or to fake speech. `simulateSpeech` needs a second one, because
        // a phone runs the Debug configuration too — that is the documented way to
        // point the app at the Mac — and a phone has a real microphone. Its default
        // is the condition it was always describing: no microphone.
        //
        // `useMockAPI` deliberately keeps the looser gate. Fixture cards announce
        // themselves — you are reading someone else's curriculum — whereas fake
        // speech is silent, and swaps the model's own answer in for the user's.
        // Their defaults also fail in opposite directions: mocks off with no
        // `WC_BASE_URL` is an app pointing at nothing.
        let configuredMockDefault =
            (Bundle.main.object(forInfoDictionaryKey: "WCDefaultMock") as? String) != "0"
        useMockAPI = Self.isDebug && flag("WC_MOCK", default: configuredMockDefault)
        simulateSpeech = Self.isDebug && flag("WC_SIM_SPEECH", default: Self.isSimulator)

        loadState = LoadState(rawValue: env["WC_LOAD"] ?? "") ?? .auto
        railStyle = RailStyle(rawValue: env["WC_RAIL_STYLE"] ?? "") ?? .dots
        failSubmit = flag("WC_FAIL_SUBMIT")

        // A few routes are unreachable without a forced failure, so they set their
        // own flag rather than making the caller remember a second env var. Derived
        // here, where `route` is already being read and nothing has run yet, so the
        // "set it before whatever consumes it" ordering that each route would
        // otherwise have to get right stops being a consideration at all.
        let route = env["WC_ROUTE"] ?? ""
        failQuestion = flag("WC_FAIL_QUESTION") || route == "question-failure"
        failedMechanism = flag("WC_FAILED_MECHANISM") || route.hasPrefix("reattempt")
        scoringV2 = flag("WC_SCORING_V2") || route.hasPrefix("coaching")
        failAdd = flag("WC_FAIL_ADD")
        emptyQueue = flag("WC_EMPTY")
        textFirst = flag("WC_TEXT_FIRST")
        ttsEnabled = flag("WC_TTS", default: true)
        planNoActive = flag("WC_PLAN_NO_ACTIVE")
        planSummaryFails = flag("WC_PLAN_SUMMARY_FAIL")
        planFailImport = flag("WC_PLAN_FAIL_IMPORT")
        planFailAddCard = flag("WC_PLAN_FAIL_ADD_CARD")
        planReplanInvalid = flag("WC_PLAN_REPLAN_INVALID")
        planReopenInvalid = flag("WC_PLAN_REOPEN_INVALID")
        planFixedRecovery = flag("WC_PLAN_FIXED_RECOVERY")
        planFailDebriefSave = flag("WC_PLAN_FAIL_DEBRIEF_SAVE")
            || route == "study-plan-debrief-save-failure"
        planFailDebriefCheck = flag("WC_PLAN_FAIL_DEBRIEF_CHECK")
            || route == "study-plan-debrief-check-failure"
        planVariant = env["WC_PLAN_VARIANT"] ?? ""
        planCardVariant = env["WC_PLAN_CARD_VARIANT"] ?? ""
        self.route = route
    }
}

/// Fixture data matching the design handoff's screenshots exactly.
actor MockAPI: DevmaxAPI {
    static let shared = MockAPI()

    private var submitAttempts = 0
    private var questionAttempts = 0
    private var addAttempts = 0
    /// Alternates so the card-add failure path and the idempotent retry that
    /// follows it are both reachable in a single walk.
    var cardAcceptAttempts = 0
    var debriefSubmitAttempts = 0
    var debriefCheckAttempts = 0
    var practiceDebriefs: [UUID: PracticeDebrief] = [:]
    private var completions = 0
    /// Set by the most recent `startSession`, so `submitAnswer` echoes the flag
    /// back the way the server does.
    private var sessionIsPractice = false
    private var storedSettings = AppSettings.placeholder
    private var extraCards: [DueCard] = []
    private var capturedGaps: [PendingCapture] = [
        PendingCapture(
            id: UUID(uuidString: "00000000-0000-0000-0000-0000000000d1")!,
            topic: "Lease-based leader election",
            context: "I mixed up lease expiry with quorum loss.",
            status: "pending_source", sourceUrl: "", sourceSection: "", sourceLabel: "",
            answerBasis: "", answerRubric: AnswerRubric(), canonicalQuestion: "",
            activatedCardId: nil, createdAt: Date(), updatedAt: Date()
        ),
        PendingCapture(
            id: UUID(uuidString: "00000000-0000-0000-0000-0000000000d2")!,
            topic: "Postgres vacuum horizons",
            context: "Why one old transaction prevents cleanup.",
            status: "ready_to_review", sourceUrl: "https://www.postgresql.org/docs/",
            sourceSection: "Routine Vacuuming", sourceLabel: "PostgreSQL documentation",
            answerBasis: "VACUUM cannot remove tuples that remain visible to any active snapshot.",
            answerRubric: AnswerRubric(
                mechanism: "The global oldest active snapshot bounds tuple removal.",
                acceptableAlternative: "Explain xmin or the visibility horizon accurately.",
                tradeOff: "Long snapshots preserve consistency but delay reclamation.",
                failureMode: "A long transaction causes dead tuples and table bloat.",
                misconception: "VACUUM can always delete every dead tuple immediately."
            ),
            canonicalQuestion: "How can one long-running transaction prevent Postgres from reclaiming dead tuples?",
            activatedCardId: nil, createdAt: Date(), updatedAt: Date()
        ),
    ]

    private static let chID = UUID(uuidString: "00000000-0000-0000-0000-0000000000c1")!
    private static let raftID = UUID(uuidString: "00000000-0000-0000-0000-0000000000c2")!
    private static let pgID = UUID(uuidString: "00000000-0000-0000-0000-0000000000c3")!

    private var flags: DebugFlags { DebugFlags.shared }

    func due() async throws -> [DueCard] {
        try await Task.sleep(nanoseconds: 350_000_000)
        if await MainActor.run(body: { DebugFlags.shared.loadState == .error }) {
            throw APIError.status(500)
        }
        if await MainActor.run(body: { DebugFlags.shared.emptyQueue }) { return [] }
        // The queue is the first three library cards, adapted — one fixture list,
        // not two. These fixtures *are* the screenshot acceptance test, so a
        // mastery string edited in one place and not the other would make Today
        // and Coverage disagree about the same card with nothing to catch it.
        // The Raft card is the one carrying a stored partial answer.
        return extraCards + Self.library.prefix(3).map {
            $0.asQueueCard(resumable: $0.id == Self.raftID)
        }
    }

    /// The whole library: the three due cards plus ten that exist only for Review
    /// Sprint and Coverage — 13 across 9 categories, matching the handoff.
    ///
    /// Every card's axis triple derives to its own `lastScore` under the backend's
    /// `derive_composite`, so the fixture can't drift into a state the server
    /// could never produce. Their means are the rollup line in `coverage.png`:
    /// `ACCURACY 2.7 · DEPTH 1.4 · BOUNDARIES 2.3`.
    func cards(sort: String, mode: String) async throws -> [CardSummary] {
        try await Task.sleep(nanoseconds: 350_000_000)
        if await MainActor.run(body: { DebugFlags.shared.loadState == .error }) {
            throw APIError.status(500)
        }
        return Self.library
    }

    private static let library: [CardSummary] = [
        // The three cards that are due today — no days-since-review, so Coverage
        // shows their due label instead.
        MockAPI.summary(MockAPI.chID, "Consistent hashing", "Core Concept",
                "solid on ring mechanics, shaky on virtual nodes",
                score: 2, axes: (2, 2, 4), due: "3 days overdue", ago: nil),
        MockAPI.summary(MockAPI.raftID, "Raft leader election", "Distributed Systems",
                "can describe terms and voting; fuzzy on log-matching safety",
                score: 1, axes: (1, 1, 2), due: "1 day overdue", ago: nil, missed: 2),
        MockAPI.summary(MockAPI.pgID, "Postgres index types", "Databases",
                "confident on B-tree vs GIN; hasn't explained index bloat",
                score: 3, axes: (4, 1, 2), due: "due today", ago: nil),

        MockAPI.summary(MockAPI.id(0xE1), "TCP congestion control", "Networking",
                "explains slow start; cubic vs reno unclear",
                score: 2, axes: (2, 2, 3), due: "not due", ago: 9),
        MockAPI.summary(MockAPI.id(0xE2), "Bloom filters", "Core Concept",
                "solid on false positives; sizing math shaky",
                score: 3, axes: (5, 2, 2), due: "not due", ago: 12),
        MockAPI.summary(MockAPI.id(0xE3), "MVCC in Postgres", "Databases",
                "knows snapshots; vacuum reasoning thin",
                score: 1, axes: (1, 0, 2), due: "not due", ago: 15),
        MockAPI.summary(MockAPI.id(0xE4), "Optimistic vs pessimistic locking", "Concurrency",
                "good on retry loops; no contention math",
                score: 4, axes: (5, 3, 2), due: "not due", ago: 6),
        MockAPI.summary(MockAPI.id(0xE5), "Cache invalidation strategies", "Caching",
                "write-through clear; TTL tradeoffs vague",
                score: 2, axes: (2, 1, 3), due: "not due", ago: 21),
        MockAPI.summary(MockAPI.id(0xE6), "Idempotency keys", "API Design",
                "can define; storage lifetime unexplained",
                score: 3, axes: (4, 1, 2), due: "not due", ago: 4),
        // The one untested card — its axes stay nil, so the rollup ignores it.
        MockAPI.summary(MockAPI.id(0xE7), "Virtual memory and page faults", "Operating Systems",
                "no signal yet", score: nil, axes: nil, due: "not due", ago: 30),
        MockAPI.summary(MockAPI.id(0xE8), "Backpressure and load shedding", "Reliability",
                "names the patterns; no queue-depth reasoning",
                score: 1, axes: (1, 1, 2), due: "not due", ago: 18),
        MockAPI.summary(MockAPI.id(0xE9), "TLS handshake", "Networking",
                "session resumption unclear",
                score: 2, axes: (2, 1, 2), due: "not due", ago: 11),
        MockAPI.summary(MockAPI.id(0xEA), "CAP in practice", "Distributed Systems",
                "quotes the theorem; weak on partition behaviour",
                score: 3, axes: (3, 2, 2), due: "not due", ago: 8),
    ]

    private static func id(_ byte: UInt8) -> UUID {
        UUID(uuidString: String(format: "00000000-0000-0000-0000-0000000000%02x", byte))!
    }

    private static func summary(
        _ id: UUID, _ topic: String, _ category: String, _ mastery: String,
        score: Int?, axes: (Int, Int, Int)?, due: String, ago: Int?, missed: Int = 0
    ) -> CardSummary {
        CardSummary(
            id: id, topic: topic, category: category, deliveryMode: "conversational",
            masterySummary: mastery, lastScore: score, recallScore: axes?.0,
            scoreKind: axes == nil ? "unrated" : "recall", scoringContractVersion: 1,
            lastAccuracy: axes?.0,
            lastDepth: axes?.1,
            lastBoundaries: axes?.2,
            easeFactor: 2.5, intervalDays: 7, repetitions: score == nil ? 0 : 2,
            nextReviewAt: "2026-07-25", dueLabel: due, daysSinceReview: ago, missedCount: missed
        )
    }

    func card(_ id: UUID) async throws -> CardDetail {
        try await Task.sleep(nanoseconds: 200_000_000)
        if id == StudyPlanFixtures.mappedCardID {
            return CardDetail(
                id: id,
                topic: "System design delivery: requirements to entities, APIs, "
                    + "high-level design, and deep dives",
                category: "Delivery", masterySummary: "", lastScore: nil,
                easeFactor: 2.5, intervalDays: 1, repetitions: 0,
                nextReviewAt: "2026-08-12", missedCount: 0, sessions: [],
                recallNotBeforeAt: "2026-08-12T08:00:00-07:00",
                learningAvailable: false,
                sourceLabel: "Hello Interview · Delivery Framework",
                sourceSection: "Delivery sequence"
            )
        }
        if id == Self.raftID {
            return CardDetail(
                id: id, topic: "Raft leader election", category: "Distributed Systems",
                masterySummary: "can describe terms and voting; fuzzy on log-matching safety",
                lastScore: 1, recallScore: 1, scoreKind: "recall",
                scoringContractVersion: 1,
                easeFactor: 1.96, intervalDays: 1, repetitions: 0,
                nextReviewAt: "2026-07-25", missedCount: 2, sessions: [],
                learningAvailable: true, sourceLabel: "Raft paper",
                sourceSection: "Leader election"
            )
        }
        if !extraCards.isEmpty, let match = extraCards.first(where: { $0.id == id }) {
            // A just-added card has no history yet — the Card History empty state.
            return CardDetail(
                id: id, topic: match.topic, category: match.category, masterySummary: "",
                lastScore: nil, easeFactor: 2.5, intervalDays: 1, repetitions: 0,
                nextReviewAt: "2026-07-24", missedCount: 0, sessions: [],
                learningAvailable: true, sourceLabel: "Reviewed source",
                sourceSection: match.topic
            )
        }
        return CardDetail(
            id: Self.chID, topic: "Consistent hashing", category: "Core Concept",
            masterySummary: "solid on ring mechanics, shaky on virtual nodes",
            lastScore: 2, recallScore: 2, scoreKind: "recall", scoringContractVersion: 1,
            easeFactor: 2.36, intervalDays: 3, repetitions: 3,
            nextReviewAt: "2026-07-27", missedCount: 0,
            sessions: [
                SessionHistory(
                    id: UUID(), date: Self.date("2026-07-21T06:51:00Z"), score: 2,
                    recallScore: 2, legacyCompositeScore: 2, scoringContractVersion: 1,
                    feedback: "Explained the ring, stalled on virtual nodes.",
                    turns: [
                        Turn(role: .question, text: "What problem does consistent hashing solve that modulo hashing doesn't?"),
                        Turn(role: .answer, text: "With mod-N, if you add a server the modulus changes and almost every key maps somewhere new, so the whole cache is cold. Consistent hashing keeps most keys where they are."),
                        Turn(role: .followUp, text: "One more — what are virtual nodes for?"),
                        Turn(role: .answer, text: "I think they… split a node into several ring positions? I'm not sure what that actually buys you."),
                        Turn(role: .score, text: "2 — Right on the core motivation. Virtual nodes were guessed at, not explained."),
                    ]
                ),
                SessionHistory(
                    id: UUID(), date: Self.date("2026-07-16T21:32:00Z"), score: 3,
                    recallScore: nil, legacyCompositeScore: 3, scoringContractVersion: 1,
                    feedback: "Clear on lookups; didn't mention replication.",
                    turns: [
                        Turn(role: .question, text: "How does a client find which node owns a key?"),
                        Turn(role: .answer, text: "Hash the key, walk clockwise on the ring to the first node position you hit, that node owns it."),
                        Turn(role: .score, text: "3 — Correct lookup path. No mention of how replicas are chosen from the successors."),
                    ]
                ),
                SessionHistory(
                    id: UUID(), date: Self.date("2026-07-12T07:04:00Z"), score: 4,
                    recallScore: 4, legacyCompositeScore: 4, scoringContractVersion: 1,
                    feedback: "First pass, mostly solid.",
                    turns: [
                        Turn(role: .question, text: "Describe consistent hashing in two sentences."),
                        Turn(role: .answer, text: "Nodes and keys are both hashed into the same circular space, and each key is owned by the next node clockwise. Adding or removing a node only affects the keys in the adjacent arc."),
                        Turn(role: .score, text: "4 — Concise and accurate framing."),
                    ]
                ),
            ],
            learningAvailable: true,
            sourceLabel: "Hello Interview · Consistent Hashing",
            sourceSection: "Ring ownership and virtual nodes"
        )
    }

    func learnCard(_ id: UUID) async throws -> LearningCard {
        try await Task.sleep(nanoseconds: 200_000_000)
        let summary = Self.library.first(where: { $0.id == id })
        let topic = summary?.topic ?? "System design mechanism"
        let category = summary?.category ?? "Core Concept"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let recallAt = formatter.string(from: Date().addingTimeInterval(24 * 60 * 60))

        return LearningCard(
            cardId: id, topic: topic, category: category,
            sourceLabel: "Hello Interview · Consistent Hashing",
            sourceSection: "Ring ownership and virtual nodes",
            sourceUrl: "https://www.hellointerview.com/learn/system-design/"
                + "core-concepts/consistent-hashing",
            sourceExcerpt: "Adding or removing a node should move only the keys "
                + "adjacent to that node's position.",
            coreExplanation: "Consistent hashing places both keys and owners in the same ordered "
                + "hash space. A key belongs to the next owner clockwise, so membership changes "
                + "disturb only a bounded part of the ring.",
            essentialAccount: "Hash the key onto the ring, walk clockwise to its owner, and use "
                + "virtual nodes to spread each physical node across multiple positions for "
                + "smoother balance.",
            acceptableAlternative: "A fixed-slot hash partition map also bounds movement if slot "
                + "ownership, rather than every key's modulo, is reassigned.",
            depthExtension: "More virtual nodes improve balance and failure spreading, but "
                + "increase ownership metadata and movement planning.",
            boundaryExtension: "Too few or uneven virtual nodes create hot owners; membership "
                + "changes also need a controlled handoff so reads do not miss keys while "
                + "ownership moves.",
            misconception: "Consistent hashing does not eliminate rebalancing. It limits which "
                + "keys move when membership changes.",
            recallAvailableAt: recallAt
        )
    }

    func captures() async throws -> [CaptureSummary] {
        try await Task.sleep(nanoseconds: 180_000_000)
        return capturedGaps.filter { $0.status != "activated" }.map(\.summary)
    }

    func capture(_ id: UUID) async throws -> PendingCapture {
        guard let value = capturedGaps.first(where: { $0.id == id }) else {
            throw APIError.status(404)
        }
        return value
    }

    func createCapture(topic: String, context: String) async throws -> PendingCapture {
        try await Task.sleep(nanoseconds: 350_000_000)
        addAttempts += 1
        if await MainActor.run(body: { DebugFlags.shared.failAdd }), addAttempts % 2 == 1 {
            throw APIError.status(500)
        }
        let now = Date()
        let capture = PendingCapture(
            id: UUID(), topic: topic, context: context, status: "pending_source",
            sourceUrl: "", sourceSection: "", sourceLabel: "", answerBasis: "",
            answerRubric: AnswerRubric(), canonicalQuestion: "", activatedCardId: nil,
            createdAt: now, updatedAt: now
        )
        capturedGaps.insert(capture, at: 0)
        return capture
    }

    func updateCapture(
        _ id: UUID, update: CaptureUpdateRequest
    ) async throws -> PendingCapture {
        guard let index = capturedGaps.firstIndex(where: { $0.id == id }) else {
            throw APIError.status(404)
        }
        capturedGaps[index].sourceUrl = update.sourceUrl
        capturedGaps[index].sourceSection = update.sourceSection
        capturedGaps[index].sourceLabel = update.sourceLabel
        capturedGaps[index].answerBasis = update.answerBasis
        capturedGaps[index].answerRubric = update.answerRubric
        capturedGaps[index].canonicalQuestion = update.canonicalQuestion
        capturedGaps[index].updatedAt = Date()
        capturedGaps[index].status = update.answerRubric.isComplete
            && !update.answerBasis.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !update.canonicalQuestion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "ready_to_review" : "pending_source"
        return capturedGaps[index]
    }

    func prepareCaptureQuestion(
        _ id: UUID, regenerate: Bool = false
    ) async throws -> PendingCapture {
        try await Task.sleep(nanoseconds: 450_000_000)
        guard let index = capturedGaps.firstIndex(where: { $0.id == id }) else {
            throw APIError.status(404)
        }
        if capturedGaps[index].canonicalQuestion.isEmpty || regenerate {
            capturedGaps[index].canonicalQuestion =
                "Explain \(capturedGaps[index].topic.lowercased()) from the mechanism outward. Where does it fail?"
        }
        capturedGaps[index].status = "ready_to_review"
        capturedGaps[index].updatedAt = Date()
        return capturedGaps[index]
    }

    func activateCapture(_ id: UUID, schedule: String) async throws -> CardSummary {
        guard let index = capturedGaps.firstIndex(where: { $0.id == id }) else {
            throw APIError.status(404)
        }
        let capture = capturedGaps[index]
        guard capture.status == "ready_to_review" else { throw APIError.status(409) }
        let due = schedule == "now" ? "added just now" : "queued for next review"
        let card = DueCard(
            id: capture.activatedCardId ?? UUID(), topic: capture.topic, category: "Unsorted",
            masterySummary: "no signal yet", lastScore: nil, dueLabel: due,
            resumable: false, missedCount: 0
        )
        if !extraCards.contains(where: { $0.topic == card.topic }) {
            extraCards.insert(card, at: 0)
        }
        capturedGaps[index].status = "activated"
        capturedGaps[index].activatedCardId = card.id
        return Self.summary(
            card.id, card.topic, card.category, "", score: nil, axes: nil,
            due: due, ago: nil
        )
    }

    func discardCapture(_ id: UUID) async throws {
        guard let index = capturedGaps.firstIndex(where: { $0.id == id }) else {
            throw APIError.status(404)
        }
        capturedGaps.remove(at: index)
    }

    func cardMaintenance(_ id: UUID) async throws -> CardMaintenance {
        CardMaintenance(
            id: id, lifecycleStatus: "active",
            canonicalQuestion: "Explain this topic from the mechanism outward.",
            sourceUrl: "https://example.com/source", sourceSection: "", sourceLabel: "Source",
            answerBasis: "Trusted answer basis.", answerRubric: AnswerRubric(
                mechanism: "Required mechanism", acceptableAlternative: "Equivalent framing",
                tradeOff: "Key trade-off", failureMode: "Key failure mode",
                misconception: "Common misconception"
            ), replacesCardId: nil, replacedByCardId: nil
        )
    }

    func archiveCard(_ id: UUID) async throws -> CardMaintenance {
        let value = try await cardMaintenance(id)
        return CardMaintenance(
            id: value.id, lifecycleStatus: "archived",
            canonicalQuestion: value.canonicalQuestion, sourceUrl: value.sourceUrl,
            sourceSection: value.sourceSection, sourceLabel: value.sourceLabel,
            answerBasis: value.answerBasis, answerRubric: value.answerRubric,
            replacesCardId: value.replacesCardId, replacedByCardId: value.replacedByCardId
        )
    }

    func restoreCard(_ id: UUID) async throws -> CardMaintenance {
        try await cardMaintenance(id)
    }

    func replaceCard(
        _ id: UUID, question: String, schedule: String
    ) async throws -> CardSummary {
        let original = Self.library.first(where: { $0.id == id }) ?? Self.library[0]
        return Self.summary(
            UUID(), original.topic, original.category, "", score: nil, axes: nil,
            due: schedule == "now" ? "added just now" : "queued for next review", ago: nil
        )
    }

    func startSession(cardID: UUID, practice: Bool = false) async throws -> SessionStart {
        try await Task.sleep(nanoseconds: 500_000_000)
        // Alternates like the submit path does, so Retry recovers and the failure
        // walks end to end. 503 is what the server returns when Claude is down —
        // the case that actually shipped.
        questionAttempts += 1
        if await MainActor.run(body: { DebugFlags.shared.failQuestion }), questionAttempts % 2 == 1 {
            throw APIError.scoringUnavailable
        }
        sessionIsPractice = practice
        if cardID == Self.raftID {
            return SessionStart(
                sessionId: UUID(),
                question: "A follower stops hearing heartbeats and starts an election. What stops it from becoming leader with an incomplete log?",
                isFollowUp: false,
                draftText: "Okay so each server has a term number, and when a follower stops hearing heartbeats it bumps its term and becomes a candidate, then it asks",
                resumed: true
            )
        }
        if cardID == Self.pgID {
            return SessionStart(
                sessionId: UUID(),
                question: "You have a jsonb column and queries that filter on keys inside it. Which index, and what does it cost you?",
                isFollowUp: false, draftText: "", resumed: false
            )
        }
        if cardID == Self.chID {
            return SessionStart(
                sessionId: UUID(),
                question: "You're adding a node to a consistent-hashing ring. Walk me through exactly what data moves and what doesn't.",
                isFollowUp: false, draftText: "", resumed: false
            )
        }
        // A library card walked in a Review Sprint. The same question comes back
        // every time for a given card, which is what `canonical_question` does
        // server-side.
        let topic = Self.library.first { $0.id == cardID }?.topic ?? "this topic"
        return SessionStart(
            sessionId: UUID(),
            question: "Reconstruct \(topic.lowercased()) from memory — what is the mechanism, and where does it break?",
            isFollowUp: false, draftText: "", resumed: false
        )
    }

    func saveDraft(sessionID: UUID, text: String) async throws {}

    func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome {
        try await Task.sleep(nanoseconds: 1_200_000_000)
        submitAttempts += 1
        if await MainActor.run(body: { DebugFlags.shared.failSubmit }), submitAttempts % 2 == 1 {
            throw APIError.scoringUnavailable
        }
        if submitAttempts <= 2 {
            return .followUp(question: "One more — how do virtual nodes change the amount of data that moves?")
        }
        // Sprint runs vary so the recap has something to show; a daily review
        // keeps the fixture the Conversation screenshots were taken against.
        let scores = [3, 4, 2, 2, 3, 3]
        var score = sessionIsPractice ? scores[completions % scores.count] : 3
        if await MainActor.run(body: { DebugFlags.shared.failedMechanism }) { score = 1 }
        completions += 1
        let scoringV2 = await MainActor.run { DebugFlags.shared.scoringV2 }
        let boundaryCoaching = await MainActor.run {
            DebugFlags.shared.route.contains("boundary")
        }
        let feedback = score <= 2
            ? "The ring isn't ordered by node identity — each node owns the arc of hash space ending at its own position, so adding one only moves the slice that arc takes over."
            : (scoringV2
                ? "The essential ring-ownership account was recalled. The successor-node handoff remained the one bounded omission."
                : "Good on ring mechanics and why mod-N is worse. The virtual-node answer covered load spreading but not the successor-node handoff during transfer, and replication factor never came up.")
        return .complete(
            score: score,
            recallScore: score,
            scoringContractVersion: scoringV2 ? 2 : 1,
            feedback: feedback,
            nextReviewAt: "2026-07-27",
            intervalDays: 3,
            practice: sessionIsPractice,
            // The server derives this from `accuracy <= 2`. A composite of
            // 2 or less means exactly that (`derive_composite` caps at the mechanism
            // when it fails), so the mock can key off the score it already has.
            reattemptOffered: score <= 2,
            reattemptPrompt: score <= 2
                ? "In your words — You're adding a node to a consistent-hashing ring. Walk me through exactly what data moves and what doesn't."
                : nil,
            coachingOffered: scoringV2 && score >= 3,
            coachingFocus: scoringV2 && score >= 3
                ? (boundaryCoaching ? "boundaries" : "depth") : nil,
            coachingQuestion: scoringV2 && score >= 3
                ? (boundaryCoaching
                    ? "One level deeper — what condition, exception, limitation, or failure case matters here?"
                    : "One level deeper — what reasoning, causal link, application, or trade-off matters here?")
                : nil
        )
    }

    func submitReattempt(sessionID: UUID, text: String) async throws {
        try await Task.sleep(nanoseconds: 1_200_000_000)
        submitAttempts += 1
        if await MainActor.run(body: { DebugFlags.shared.failSubmit }), submitAttempts % 2 == 1 {
            throw APIError.scoringUnavailable
        }
    }

    func submitCoaching(sessionID: UUID, text: String) async throws -> CoachingOutcome {
        try await Task.sleep(nanoseconds: 800_000_000)
        let boundary = await MainActor.run { DebugFlags.shared.route.contains("boundary") }
        return CoachingOutcome(
            focus: boundary ? "boundaries" : "depth",
            question: boundary
                ? "One level deeper — what condition, exception, limitation, or failure case matters here?"
                : "One level deeper — what reasoning, causal link, application, or trade-off matters here?",
            feedback: boundary
                ? "The boundary is grounded: skewed vnode assignment can still concentrate ownership and transfer load."
                : "The causal link is explicit, and the transfer consequence follows from it."
        )
    }

    func settings() async throws -> AppSettings {
        var value = storedSettings
        if await MainActor.run(body: { DebugFlags.shared.scoringV2 }) {
            value.activeScoringContractVersion = 2
        }
        return value
    }

    func updateSettings(_ settings: AppSettings) async throws -> AppSettings {
        storedSettings = settings
        return settings
    }

    func registerDeviceToken(_ token: String) async throws {}

    /// Same parser the live client uses, so a fixture timestamp that the real
    /// decoder would reject can't silently work here.
    private static func date(_ iso: String) -> Date {
        WireDate.parse(iso) ?? Date()
    }
}
