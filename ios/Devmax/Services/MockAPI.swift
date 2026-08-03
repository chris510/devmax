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

        // The two whose default is `true` need the extra gate: a release build must
        // never fall back to fixtures or to fake speech. simulateSpeech especially —
        // it defaults on because the simulator has no usable microphone, so
        // ungated it typed out a hardcoded paragraph instead of recording the user.
        //
        // `isDebug` alone was not that gate. A phone runs the Debug configuration
        // too — that is the documented way to point the app at the Mac — and a phone
        // has a real microphone, so the fake one kept typing the model's own answer
        // into the transcript and submitting it as the user's. The default is the
        // condition it was always describing: no microphone. An explicit
        // `WC_SIM_SPEECH=1` still forces it on, on device included.
        useMockAPI = Self.isDebug && flag("WC_MOCK", default: true)
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
    private var completions = 0
    /// Set by the most recent `startSession`, so `submitAnswer` echoes the flag
    /// back the way the server does.
    private var sessionIsPractice = false
    private var storedSettings = AppSettings.placeholder
    private var extraCards: [DueCard] = []

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
    /// `MECHANISM 2.7 · TRADE-OFFS 1.4 · FAILURE MODES 2.3`.
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
            masterySummary: mastery, lastScore: score,
            lastMechanismAccuracy: axes?.0,
            lastTradeOffAwareness: axes?.1,
            lastFailureModeAwareness: axes?.2,
            easeFactor: 2.5, intervalDays: 7, repetitions: score == nil ? 0 : 2,
            nextReviewAt: "2026-07-25", dueLabel: due, daysSinceReview: ago, missedCount: missed
        )
    }

    func card(_ id: UUID) async throws -> CardDetail {
        try await Task.sleep(nanoseconds: 200_000_000)
        if id == Self.raftID {
            return CardDetail(
                id: id, topic: "Raft leader election", category: "Distributed Systems",
                masterySummary: "can describe terms and voting; fuzzy on log-matching safety",
                lastScore: 1, easeFactor: 1.96, intervalDays: 1, repetitions: 0,
                nextReviewAt: "2026-07-25", missedCount: 2, sessions: []
            )
        }
        if !extraCards.isEmpty, let match = extraCards.first(where: { $0.id == id }) {
            // A just-added card has no history yet — the Card History empty state.
            return CardDetail(
                id: id, topic: match.topic, category: match.category, masterySummary: "",
                lastScore: nil, easeFactor: 2.5, intervalDays: 1, repetitions: 0,
                nextReviewAt: "2026-07-24", missedCount: 0, sessions: []
            )
        }
        return CardDetail(
            id: Self.chID, topic: "Consistent hashing", category: "Core Concept",
            masterySummary: "solid on ring mechanics, shaky on virtual nodes",
            lastScore: 2, easeFactor: 2.36, intervalDays: 3, repetitions: 3,
            nextReviewAt: "2026-07-27", missedCount: 0,
            sessions: [
                SessionHistory(
                    id: UUID(), date: Self.date("2026-07-21T06:51:00Z"), score: 2,
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
                    feedback: "Clear on lookups; didn't mention replication.",
                    turns: [
                        Turn(role: .question, text: "How does a client find which node owns a key?"),
                        Turn(role: .answer, text: "Hash the key, walk clockwise on the ring to the first node position you hit, that node owns it."),
                        Turn(role: .score, text: "3 — Correct lookup path. No mention of how replicas are chosen from the successors."),
                    ]
                ),
                SessionHistory(
                    id: UUID(), date: Self.date("2026-07-12T07:04:00Z"), score: 4,
                    feedback: "First pass, mostly solid.",
                    turns: [
                        Turn(role: .question, text: "Describe consistent hashing in two sentences."),
                        Turn(role: .answer, text: "Nodes and keys are both hashed into the same circular space, and each key is owned by the next node clockwise. Adding or removing a node only affects the keys in the adjacent arc."),
                        Turn(role: .score, text: "4 — Concise and accurate framing."),
                    ]
                ),
            ]
        )
    }

    func createCard(topic: String, schedule: String) async throws -> CardSummary {
        try await Task.sleep(nanoseconds: 600_000_000)
        addAttempts += 1
        // Forced failures succeed on the retry so the path can be walked end to end.
        if await MainActor.run(body: { DebugFlags.shared.failAdd }), addAttempts % 2 == 1 {
            throw APIError.status(500)
        }
        let card = DueCard(
            id: UUID(), topic: topic, category: "Unsorted", masterySummary: "no signal yet",
            lastScore: nil,
            dueLabel: schedule == "now" ? "added just now" : "queued for next review",
            resumable: false, missedCount: 0
        )
        extraCards.insert(card, at: 0)
        return Self.summary(
            card.id, topic, "Unsorted", "", score: nil, axes: nil,
            due: card.dueLabel, ago: nil
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
        return .complete(
            score: score,
            feedback: score <= 2
                ? "The ring isn't ordered by node identity — each node owns the arc of hash space ending at its own position, so adding one only moves the slice that arc takes over."
                : "Good on ring mechanics and why mod-N is worse. The virtual-node answer covered load spreading but not the successor-node handoff during transfer, and replication factor never came up.",
            nextReviewAt: "2026-07-27",
            intervalDays: 3,
            practice: sessionIsPractice,
            // The server derives this from `mechanism_accuracy <= 2`. A composite of
            // 2 or less means exactly that (`derive_composite` caps at the mechanism
            // when it fails), so the mock can key off the score it already has.
            reattemptOffered: score <= 2,
            reattemptPrompt: score <= 2
                ? "In your words — You're adding a node to a consistent-hashing ring. Walk me through exactly what data moves and what doesn't."
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

    func settings() async throws -> AppSettings { storedSettings }

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
