import Foundation

/// The prototype's Tweaks, as runtime flags — this is how every designed state
/// (including the failure paths) is reachable in the simulator for screenshot
/// comparison. Forced failures succeed on the retry, exactly as the prototype
/// does, so each failure path can be walked end to end.
final class DebugFlags: ObservableObject {
    static let shared = DebugFlags()

    enum LoadState: String, CaseIterable { case auto, loading, error }

    @Published var useMockAPI: Bool
    @Published var loadState: LoadState
    @Published var failSubmit: Bool
    @Published var failAdd: Bool
    @Published var emptyQueue: Bool
    @Published var textFirst: Bool
    @Published var ttsEnabled: Bool
    /// Fakes streaming speech-to-text by typing the transcript out, as the
    /// prototype does — the simulator has no usable microphone.
    @Published var simulateSpeech: Bool

    /// Drives the app straight to one designed state at launch, so every
    /// screenshot in the handoff can be reproduced and compared without hand
    /// navigation. Set via `simctl launch --setenv WC_ROUTE=…`.
    let route: String

    private init() {
        let env = ProcessInfo.processInfo.environment
        func flag(_ key: String, default fallback: Bool = false) -> Bool {
            guard let raw = env[key] else { return fallback }
            return raw == "1" || raw.lowercased() == "true"
        }
        // Debug builds run on fixtures by default so the designs are reproducible
        // without a server; release builds always talk to the real API.
        //
        // Every flag that changes behaviour rather than presentation has to be
        // pinned in release the same way. simulateSpeech in particular: it defaults
        // to true because the simulator has no usable microphone, so an ungated
        // release build on a real phone typed out a hardcoded fixture paragraph
        // instead of recording the user.
        #if DEBUG
        useMockAPI = flag("WC_MOCK", default: true)
        loadState = LoadState(rawValue: env["WC_LOAD"] ?? "") ?? .auto
        failSubmit = flag("WC_FAIL_SUBMIT")
        failAdd = flag("WC_FAIL_ADD")
        emptyQueue = flag("WC_EMPTY")
        textFirst = flag("WC_TEXT_FIRST")
        ttsEnabled = flag("WC_TTS", default: true)
        simulateSpeech = flag("WC_SIM_SPEECH", default: true)
        route = env["WC_ROUTE"] ?? ""
        #else
        useMockAPI = false
        loadState = .auto
        failSubmit = false
        failAdd = false
        emptyQueue = false
        textFirst = false
        ttsEnabled = true
        simulateSpeech = false
        route = ""
        #endif
    }
}

/// Fixture data matching the design handoff's screenshots exactly.
actor MockAPI: WarmCacheAPI {
    static let shared = MockAPI()

    private var submitAttempts = 0
    private var addAttempts = 0
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
        return extraCards + [
            DueCard(
                id: Self.chID, topic: "Consistent hashing", category: "Core Concept",
                masterySummary: "solid on ring mechanics, shaky on virtual nodes",
                lastScore: 2, dueLabel: "3 days overdue", resumable: false, missedCount: 0
            ),
            DueCard(
                id: Self.raftID, topic: "Raft leader election", category: "Distributed Systems",
                masterySummary: "can describe terms and voting; fuzzy on log-matching safety",
                lastScore: 1, dueLabel: "1 day overdue", resumable: true, missedCount: 2
            ),
            DueCard(
                id: Self.pgID, topic: "Postgres index types", category: "Databases",
                masterySummary: "confident on B-tree vs GIN; hasn't explained index bloat",
                lastScore: 3, dueLabel: "due today", resumable: false, missedCount: 0
            ),
        ]
    }

    func cards(sort: String, mode: String) async throws -> [CardSummary] {
        [
            CardSummary(id: UUID(), topic: "TCP congestion control", category: "Core Concept",
                        deliveryMode: "conversational", masterySummary: "", lastScore: nil,
                        easeFactor: 2.5, intervalDays: 1, repetitions: 0,
                        nextReviewAt: "2026-07-25", missedCount: 0),
            CardSummary(id: Self.pgID, topic: "Postgres index types", category: "Databases",
                        deliveryMode: "conversational", masterySummary: "", lastScore: 3,
                        easeFactor: 2.5, intervalDays: 7, repetitions: 2,
                        nextReviewAt: "2026-07-25", missedCount: 0),
            CardSummary(id: UUID(), topic: "Bloom filters", category: "Core Concept",
                        deliveryMode: "conversational", masterySummary: "", lastScore: nil,
                        easeFactor: 2.5, intervalDays: 1, repetitions: 0,
                        nextReviewAt: "2026-07-26", missedCount: 0),
        ]
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
        return CardSummary(
            id: card.id, topic: topic, category: "Unsorted", deliveryMode: "conversational",
            masterySummary: "", lastScore: nil, easeFactor: 2.5, intervalDays: 1,
            repetitions: 0, nextReviewAt: "2026-07-24", missedCount: 0
        )
    }

    func startSession(cardID: UUID) async throws -> SessionStart {
        try await Task.sleep(nanoseconds: 500_000_000)
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
        return SessionStart(
            sessionId: UUID(),
            question: "You're adding a node to a consistent-hashing ring. Walk me through exactly what data moves and what doesn't.",
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
        return .complete(
            score: 3,
            feedback: "Good on ring mechanics and why mod-N is worse. The virtual-node answer covered load spreading but not the successor-node handoff during transfer, and replication factor never came up.",
            nextReviewAt: "2026-07-27",
            intervalDays: 3
        )
    }

    func settings() async throws -> AppSettings { storedSettings }

    func updateSettings(_ settings: AppSettings) async throws -> AppSettings {
        storedSettings = settings
        return settings
    }

    func registerDeviceToken(_ token: String) async throws {}

    private static func date(_ iso: String) -> Date {
        ISO8601DateFormatter().date(from: iso) ?? Date()
    }
}
