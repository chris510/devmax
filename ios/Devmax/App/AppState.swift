import Foundation
import SwiftUI

/// Client state, named as the handoff's state-model table describes it.
@MainActor
final class AppState: ObservableObject {
    enum LoadState: Equatable { case loading, ready, error }
    enum Screen: Hashable {
        case today, conversation(UUID), history(UUID)
        case sprintSetup, coverage, recap
    }
    enum Sheet: String, Identifiable { case settings, add; var id: String { rawValue } }

    /// The expanded tier on Coverage. One at a time, across the whole screen.
    struct OpenTier: Equatable {
        let category: String
        let tier: ScoreStyle.Tier
    }

    // Today
    @Published var load: LoadState = .loading
    @Published var queue: [DueCard] = []
    @Published var filter: ScoreStyle.Band?
    @Published var sheet: Sheet?
    @Published var addPending = false
    @Published var addError = false
    @Published var settings: AppSettings = .placeholder

    // Navigation
    @Published var path: [Screen] = []

    // Conversation
    @Published var stage: Stage = .loadingQuestion
    @Published var thread: [ThreadEntry] = []
    @Published var draft = ""
    @Published var inputMode: InputMode = .voice
    @Published var submitError = false
    @Published var resumeAvailable = false
    @Published var storedPartial = ""
    @Published var result: SessionResult?
    @Published var sessionCards: [DueCard] = []
    @Published var cursor = 0
    @Published var sessionID: UUID?
    /// A Review Sprint run — suppresses SM-2 server-side and swaps the schedule line.
    @Published var practice = false
    /// This session's scored cards, in walk order. Drives the rail and the recap.
    @Published var run: [RunEntry] = []

    // Review Sprint / Coverage
    @Published var library: [CardSummary] = []
    @Published var libraryLoad: LoadState = .loading
    /// Empty means the whole library, not "nothing".
    @Published var setupCats: Set<String> = []
    @Published var setupSize = 6
    /// Bumped by Shuffle; the only input that re-rolls the suggested set.
    @Published var seed = 1
    @Published var covOpen: OpenTier?
    @Published var recapOpen: UUID?

    enum InputMode { case voice, text }

    static let minSessionSize = 4
    static let maxSessionSize = 10

    /// In-flight debounced draft upload; cancelled and replaced on each edit.
    private var draftSync: Task<Void, Never>?

    let api: DevmaxAPI

    init(api: DevmaxAPI = APIConfig.client) {
        self.api = api
    }

    // MARK: - Today

    /// The cards Start will walk, honouring the active mastery-band filter.
    var visibleQueue: [DueCard] {
        guard let filter else { return queue }
        return queue.filter { ScoreStyle.Band.of($0.lastScore) == filter }
    }

    /// Bands present in the queue, in fixed order, with zero-count bands omitted.
    var bands: [(band: ScoreStyle.Band, count: Int)] {
        ScoreStyle.Band.allCases.compactMap { band in
            let count = queue.filter { ScoreStyle.Band.of($0.lastScore) == band }.count
            return count > 0 ? (band, count) : nil
        }
    }

    var headerStatus: String {
        load.status { queue.isEmpty ? "NOTHING DUE" : "\(queue.count) CARDS DUE" }
    }

    var headerDate: String {
        let f = DateFormatter()
        f.dateFormat = "EEE d MMM"
        return f.string(from: Date()).uppercased()
    }

    func loadToday() async {
        switch DebugFlags.shared.loadState {
        case .loading:
            load = .loading
            return  // hold the skeleton so it can be compared to the screenshot
        default:
            break
        }
        load = .loading
        do {
            queue = try await api.due()
            DueCache.record(count: queue.count)
            load = .ready
            // Today's "COMING UP" list and Coverage read the same library, so
            // it is fetched once into one store rather than twice into two.
            if queue.isEmpty { await loadLibrary() }
            settings = (try? await api.settings()) ?? settings
        } catch {
            load = .error
        }
    }

    func addCard(topic: String, schedule: String) async {
        addPending = true
        addError = false
        do {
            _ = try await api.createCard(topic: topic, schedule: schedule)
            addPending = false
            sheet = nil
            queue = (try? await api.due()) ?? queue
            load = .ready
        } catch {
            // The sheet stays open and the typed topic stays put.
            addPending = false
            addError = true
        }
    }

    func saveSettings(_ new: AppSettings) {
        let previous = settings
        settings = new
        Task {
            // The server is the authority on whether a window is usable: it rejects
            // one shorter than the cron's poll interval, and TimeChip advances
            // `from` and `to` independently, so `from == to` is two taps away.
            // Discarding the response left the sheet showing a window as saved
            // that was never stored. Adopting it means a rejected edit snaps back.
            guard let saved = try? await api.updateSettings(new) else {
                settings = previous
                return
            }
            settings = saved
        }
    }

    // MARK: - Review Sprint

    func loadLibrary() async {
        if DebugFlags.shared.loadState == .loading {
            libraryLoad = .loading
            return  // hold the skeleton so it can be compared to the screenshot
        }
        libraryLoad = .loading
        do {
            library = try await api.cards(sort: "next_review", mode: "conversational")
            libraryLoad = .ready
        } catch {
            libraryLoad = .error
        }
    }

    func enterSprintSetup() {
        covOpen = nil
        path.append(.sprintSetup)
        Task { await loadLibrary() }
    }

    /// The curriculum order the design ships, not alphabetical — it runs from
    /// fundamentals outward, and `sprint-setup-default.png` shows it exactly.
    /// (`CATEGORIES` in the prototype, Devmax.dc.html.)
    private static let categoryOrder = [
        "Core Concept", "Distributed Systems", "Databases", "Networking",
        "Concurrency", "Caching", "Operating Systems", "API Design", "Reliability",
    ]

    /// Every category present in the library, in curriculum order. Anything the
    /// list doesn't know about — a quick-added card lands in `Unsorted` — sorts
    /// alphabetically after it, so a new category still appears without a code
    /// change rather than being silently dropped.
    var categories: [String] {
        let present = Set(library.map(\.category))
        let known = Self.categoryOrder.filter(present.contains)
        return known + present.subtracting(Self.categoryOrder).sorted()
    }

    /// The cards a sprint would draw from: the whole library, or just the
    /// selected categories. An empty selection means everything.
    var sprintPool: [CardSummary] {
        setupCats.isEmpty ? library : library.filter { setupCats.contains($0.category) }
    }

    /// The suggested set, ranked weakest-first then least-recently-reviewed,
    /// shuffled within the top `size + 4`, then re-sorted back into rank order so
    /// the walk always opens on the weakest card.
    ///
    /// Unrated cards sort as weakest — an untested card is the strongest reason
    /// to run a sprint at all.
    var sprintSet: [CardSummary] {
        let ranked = sprintPool.sorted { a, b in
            let (wa, wb) = (a.lastScore ?? -1, b.lastScore ?? -1)
            if wa != wb { return wa < wb }
            return (a.daysSinceReview ?? 0) > (b.daysSinceReview ?? 0)
        }
        // Seeded so the same seed always yields the same set — Shuffle is the only
        // thing that changes it, not a re-render. Filtering `ranked` at the end is
        // what puts the walk back into rank order.
        var rng = SeededGenerator(seed: seed)
        let chosen = Set(ranked.prefix(setupSize + 4).shuffled(using: &rng).prefix(setupSize).map(\.id))
        return ranked.filter { chosen.contains($0.id) }
    }

    var setupStatus: String {
        libraryLoad.status {
            guard !setupCats.isEmpty else { return "\(library.count) CARDS IN LIBRARY" }
            let n = sprintPool.count
            return "\(n) CARD\(n == 1 ? "" : "S") IN FILTER"
        }
    }

    /// The filtered pool holds at least a session's minimum. When the library has
    /// loaded and this is false, the pool is too narrow and Setup says so.
    var setupReady: Bool { libraryLoad == .ready && sprintPool.count >= Self.minSessionSize }

    /// The category chip's second line: its most urgent count, in priority order.
    func chipNote(for category: String) -> String {
        let cards = library.filter { $0.category == category }
        let weak = Self.tally(cards, [.cold, .shaky])
        if weak > 0 { return "\(weak) shaky" }
        for tier in [ScoreStyle.Tier.untested, .developing] where Self.tally(cards, [tier]) > 0 {
            return "\(Self.tally(cards, [tier])) \(tier.rawValue)"
        }
        return "\(Self.tally(cards, [.solid])) solid"
    }

    func toggleCategory(_ name: String) {
        if setupCats.contains(name) { setupCats.remove(name) } else { setupCats.insert(name) }
    }

    func startSprint() {
        let cards = sprintSet.map { $0.asQueueCard() }
        guard !cards.isEmpty else { return }
        beginSession(cards: cards, practice: true, replacingPath: true)
    }

    // MARK: - Coverage

    /// One section per category, worst-first by an exact comparator: weak
    /// (cold + shaky) descending, then untested descending, then alphabetical.
    /// Deterministic for any data set — no sort control, no per-render judgment.
    var coverageSections: [(category: String, cards: [CardSummary])] {
        categories
            .map { name in (category: name, cards: library.filter { $0.category == name }) }
            .sorted { a, b in
                let (wa, wb) = (Self.tally(a.cards, [.cold, .shaky]), Self.tally(b.cards, [.cold, .shaky]))
                if wa != wb { return wa > wb }
                let (ua, ub) = (Self.tally(a.cards, [.untested]), Self.tally(b.cards, [.untested]))
                if ua != ub { return ua > ub }
                return a.category < b.category
            }
    }

    /// How many of `cards` fall in any of `tiers`. The one tier counter — the
    /// chip notes, the Coverage section headers and the section ordering all go
    /// through it, so adjacent screens cannot report different numbers for the
    /// same category.
    static func tally(_ cards: [CardSummary], _ tiers: [ScoreStyle.Tier]) -> Int {
        cards.filter { tiers.contains(ScoreStyle.Tier.of($0.lastScore)) }.count
    }

    var coverageStatus: String {
        libraryLoad.status { "\(library.count) CARDS · \(categories.count) CATEGORIES" }
    }

    /// `MECHANISM 4.1 · TRADE-OFFS 2.8 · FAILURE MODES 3.2`.
    ///
    /// Scoring runs on three axes internally; this is the only place that
    /// decomposition surfaces, because "which axis is systemically weak" is the
    /// question Coverage exists to answer. Empty until something has been scored.
    var axisRollup: [String] {
        guard libraryLoad == .ready else { return [] }
        let axes: [(String, (CardSummary) -> Int?)] = [
            ("MECHANISM", \.lastMechanismAccuracy),
            ("TRADE-OFFS", \.lastTradeOffAwareness),
            ("FAILURE MODES", \.lastFailureModeAwareness),
        ]
        let means: [String] = axes.compactMap { name, axis in
            let values = library.compactMap(axis)
            guard !values.isEmpty else { return nil }
            let mean = Double(values.reduce(0, +)) / Double(values.count)
            return name + " " + String(format: "%.1f", mean)
        }
        return means.count == axes.count ? means : []
    }

    // MARK: - Conversation

    /// Entering from a single row, from Start with the whole filtered queue, or
    /// from a Review Sprint's suggested set.
    func beginSession(
        cards: [DueCard], startingAt index: Int = 0, practice: Bool = false,
        replacingPath: Bool = false
    ) {
        sessionCards = cards
        cursor = index
        self.practice = practice
        run = []
        recapOpen = nil
        guard let card = cards[safe: index] else { return }
        // A sprint replaces Setup rather than stacking on it, so ✕ lands on Today.
        if replacingPath { path = [.conversation(card.id)] } else { path.append(.conversation(card.id)) }
        Task { await openCard(card) }
    }

    var currentCard: DueCard? { sessionCards[safe: cursor] }

    /// The current card's topic in a multi-card session, otherwise its category.
    ///
    /// This replaced `CARD 2 OF 3`: the rail below already carries position, so
    /// the chrome slot can carry the literal information instead of repeating it.
    var conversationLabel: String {
        guard sessionCards.count > 1 else { return currentCard?.category ?? "" }
        return currentCard?.topic ?? ""
    }

    /// One stop per card in the session. Shown whenever a session has more than
    /// one card — not only in a Review Sprint.
    struct RailStop: Identifiable, Equatable {
        let id: UUID
        let topic: String
        let isCurrent: Bool
        /// Set once the card has been walked past *and* scored.
        let coveredScore: Int?
    }

    var rail: [RailStop] {
        guard sessionCards.count > 1 else { return [] }
        return sessionCards.enumerated().map { index, card in
            let scored = run.first { $0.id == card.id }
            return RailStop(
                id: card.id,
                topic: card.topic,
                isCurrent: index == cursor,
                coveredScore: index < cursor ? scored?.score : nil
            )
        }
    }

    /// Clears the previous card off the screen. Synchronous and separate from
    /// `openCard` because `nextCard` navigates before the fetch is even
    /// scheduled — leaving the reset inside the async body let the outgoing
    /// card's score block render for a frame on the incoming card's screen.
    private func resetForNewCard() {
        stage = .loadingQuestion
        thread = []
        draft = ""
        submitError = false
        result = nil
        resumeAvailable = false
        storedPartial = ""
        inputMode = DebugFlags.shared.textFirst ? .text : .voice
    }

    func openCard(_ card: DueCard) async {
        resetForNewCard()

        do {
            let start = try await api.startSession(cardID: card.id, practice: practice)
            sessionID = start.sessionId
            // A session resumed mid-probe comes back with the *probe* as `question`,
            // so it has to be tagged as one — otherwise it renders at the opening
            // question's 25px instead of the follow-up's 21px serif.
            thread = [
                ThreadEntry(
                    role: start.isFollowUp ? .followUpQuestion : .question, text: start.question
                )
            ]

            // Disk wins over the server's copy — it's the more recent of the two
            // when the app was backgrounded mid-answer.
            let partial = DraftStore.read(for: card.id) ?? start.draftText
            if !partial.isEmpty {
                storedPartial = partial
                resumeAvailable = true
            }
            stage = start.isFollowUp ? .followUp : .idle
        } catch {
            // Nothing was created, so there's no session to resume — the ✕ is the
            // only sensible action, and the thread stays empty.
            stage = .idle
            submitError = true
        }
    }

    func resumeAnswer() {
        draft = storedPartial
        resumeAvailable = false
    }

    func startOver() {
        draft = ""
        storedPartial = ""
        resumeAvailable = false
        if let card = currentCard { DraftStore.clear(for: card.id) }
    }

    /// The single way the in-progress answer changes.
    ///
    /// Typing and speech both route through here, so "a word the user produced
    /// reaches storage" is one guarantee in one place rather than a convention each
    /// call site has to remember — losing a spoken answer is the worst failure mode
    /// in the product, and the previous shape let a caller assign `draft` and skip
    /// persistence.
    func updateDraft(_ text: String) {
        draft = text
        scheduleDraftSync()
    }

    /// Write the current draft everywhere, immediately. Called when the app is
    /// backgrounded and when recording stops — the moments a delay isn't free.
    func flushDraft() {
        syncDraft(debounced: false)
    }

    private func scheduleDraftSync() {
        syncDraft(debounced: true)
    }

    /// Disk is the source of truth for instant rehydration; the server copy is the
    /// durable backup. Both are debounced because this is called on every keystroke
    /// *and* every speech-recognition partial — several times a second while
    /// speaking. `DraftStore.save` is a read-modify-rewrite of the whole file plus an
    /// atomic rename, so running it per partial competes with the live caret and
    /// auto-scroll for the same main-thread frame budget. `flushDraft` on
    /// backgrounding and on recording-stop is what makes the guarantee exact.
    private func syncDraft(debounced: Bool) {
        draftSync?.cancel()
        guard let card = currentCard else { return }
        let text = draft

        guard debounced else {
            DraftStore.save(text, for: card.id)
            if let sessionID {
                Task { [api] in try? await api.saveDraft(sessionID: sessionID, text: text) }
            }
            return
        }

        draftSync = Task { [api, sessionID] in
            try? await Task.sleep(for: .seconds(1))
            guard !Task.isCancelled else { return }
            DraftStore.save(text, for: card.id)

            guard let sessionID else { return }
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { return }
            try? await api.saveDraft(sessionID: sessionID, text: text)
        }
    }

    /// The submit envelope every turn shares: the optimistic thread write, the
    /// processing stage, and — the part that matters — the rollback.
    ///
    /// Losing a spoken answer is the worst failure mode in the product, so that
    /// recovery path exists exactly once. `send` supplies only the network call;
    /// the caller handles the outcome. Returns nil when the call failed or the
    /// text was empty, in which case the rollback has already run.
    private func sendAnswer<T>(
        _ text: String, via send: (UUID, String) async throws -> T
    ) async -> T? {
        guard let sessionID, !text.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        let answering = stage
        submitError = false

        // Optimistic: the answer appears in the thread immediately.
        thread.append(ThreadEntry(role: .answer, text: text))
        stage = .processing

        do {
            let value = try await send(sessionID, text)
            // The request outlives the screen: ✕ is live during `.processing`, so the
            // user can close the session and open another card while this is in
            // flight. Applying a stale result would score the wrong card.
            guard self.sessionID == sessionID else { return nil }
            if let card = currentCard { DraftStore.clear(for: card.id) }
            draft = ""
            return value
        } catch {
            guard self.sessionID == sessionID else { return nil }
            // Remove the optimistic answer, restore the text verbatim, rewind the
            // stage so the control is live again, and show the inline strip. No
            // toast, no data loss.
            //
            // The identity guard above is what makes `removeLast` safe: without it a
            // late failure pops the *new* card's question off an already-reset
            // thread, or traps outright on an empty one.
            thread.removeLast()
            draft = text
            stage = answering.answeringTwin
            submitError = true
            return nil
        }
    }

    func submit(_ text: String) async {
        // Turn 3 goes to a different endpoint. Dispatched here rather than at each
        // of the three call sites in ConversationScreen — the view sends an answer
        // and doesn't need to know which turn it belongs to.
        if stage.isReattempt {
            await submitReattempt(text)
            return
        }

        guard let outcome = await sendAnswer(
            text, via: { id, body in try await self.api.submitAnswer(sessionID: id, text: body) }
        ) else { return }

        switch outcome {
        case .followUp(let question):
            thread.append(ThreadEntry(role: .followUpQuestion, text: question))
            stage = .followUp
        case .complete(
            let score, let feedback, let nextReviewAt, let intervalDays, let wasPractice,
            let reattemptOffered, let reattemptPrompt
        ):
            result = SessionResult(
                score: score,
                feedback: feedback,
                scheduleLine: wasPractice
                    ? Self.practiceScheduleLine
                    : Self.scheduleLine(nextReviewAt: nextReviewAt, intervalDays: intervalDays),
                reattemptOffered: reattemptOffered,
                reattemptPrompt: reattemptPrompt
            )
            if let card = currentCard {
                run.append(
                    RunEntry(
                        id: card.id, topic: card.topic, category: card.category,
                        score: score, feedback: feedback, practice: wasPractice
                    )
                )
            }
            stage = .result
        }
    }

    /// Open turn 3. The session is already complete and the schedule already
    /// applied, so this only adds a turn to the thread — nothing here can change
    /// the score the user is looking at.
    func beginReattempt() {
        // The prompt arrives with the score, so the tap is still instant — but the
        // server composed it. Deriving it from the thread was wrong on a resumed
        // follow-up session, where the client's only `.question` entry holds the
        // probe rather than the card's question.
        guard let result, result.reattemptOffered, stage == .result,
              let prompt = result.reattemptPrompt
        else { return }
        thread.append(ThreadEntry(role: .reattemptQuestion, text: prompt))
        draft = ""
        submitError = false
        stage = .reattempt
    }

    /// Submit turn 3. Deliberately does not touch `result.score` or `run`: the
    /// numeral on screen and the recap row both describe the unaided attempt, and a
    /// coached re-attempt is not evidence about that.
    ///
    /// Private because `submit` is the only caller — the view sends an answer and
    /// the dispatch above decides which turn it is.
    private func submitReattempt(_ text: String) async {
        guard await sendAnswer(
            text, via: { id, body in try await self.api.submitReattempt(sessionID: id, text: body) }
        ) != nil else { return }

        // Back to the score block, with the re-attempt now in the thread above it.
        // The affordance is gone because the server refuses a second one.
        result?.reattemptOffered = false
        stage = .result
    }

    /// The session-end button. `Next card` on every card but the last; on the last
    /// card of a multi-card run it reads `See recap`, and a single-card session
    /// keeps `Done`. The transition is always a tap, never an automatic swap.
    var sessionEndLabel: String {
        if hasMoreCards { return "Next card" }
        return sessionCards.count > 1 ? "See recap" : "Done"
    }

    func nextCard() {
        guard cursor + 1 < sessionCards.count else {
            if sessionCards.count > 1 {
                recapOpen = nil
                path = [.recap]
            } else {
                finish()
            }
            return
        }
        cursor += 1
        guard let card = currentCard else { return }
        path = [.conversation(card.id)]
        // Before the Task, not inside it: the stage must leave `.result` in the
        // same turn the path changes.
        resetForNewCard()
        Task { await openCard(card) }
    }

    var hasMoreCards: Bool { cursor + 1 < sessionCards.count }

    /// The recap's aggregate, rounded for its colour band but shown to one decimal.
    var runAverage: Double? {
        guard !run.isEmpty else { return nil }
        return Double(run.map(\.score).reduce(0, +)) / Double(run.count)
    }

    /// Whether the server left the schedule alone, as it reported per card —
    /// not what the client asked for. If the query param were ever lost, the
    /// Conversation screen would print a real schedule; the recap must not then
    /// claim the schedule was untouched.
    var runWasPractice: Bool { run.allSatisfy(\.practice) && !run.isEmpty }

    func runAnother() {
        path = [.sprintSetup]
        Task { await loadLibrary() }
    }

    func finish() {
        path = []
        Task { await loadToday() }
    }

    // MARK: - Screenshot routing

    /// Applies `WC_ROUTE` after the queue loads, so a single `simctl launch` can
    /// land on any designed state for comparison against its screenshot.
    func applyDebugRoute() async {
        let route = DebugFlags.shared.route
        guard !route.isEmpty else { return }

        switch route {
        case "settings": sheet = .settings
        case "add": sheet = .add
        case "filter": filter = .shaky
        case "history":
            if let card = queue.first { path.append(.history(card.id)) }
        case "history-empty":
            _ = try? await api.createCard(topic: "Write-ahead logging", schedule: "now")
            queue = (try? await api.due()) ?? queue
            if let card = queue.first { path.append(.history(card.id)) }
        case "resume":
            // The Raft card is the one with a stored partial answer.
            if let card = queue.first(where: { $0.resumable }) { beginSession(cards: [card]) }
        case "setup", "sprint-setup":
            enterSprintSetup()
        case "coverage", "coverage-expanded":
            enterSprintSetup()
            await waitForLibrary()
            path.append(.coverage)
            if route == "coverage-expanded",
               let section = coverageSections.first,
               let tier = ScoreStyle.Tier.allCases.first(where: { tier in
                   Self.tally(section.cards, [tier]) > 0
               }) {
                covOpen = OpenTier(category: section.category, tier: tier)
            }
        case "recap", "recap-expanded":
            enterSprintSetup()
            await waitForLibrary()
            startSprint()
            await waitForQuestion()
            // Walk the whole set so the recap has a full run behind it. Each card
            // may take a follow-up turn before it completes, so answer until the
            // score lands rather than a fixed number of times.
            while true {
                for _ in 0..<3 where stage != .result {
                    await submit("A fixture answer, scored by the mock.")
                }
                guard hasMoreCards else { break }
                nextCard()
                await waitForQuestion()
            }
            nextCard()  // the last card's `See recap`
            if route == "recap-expanded" { recapOpen = run.first?.id }
        default:
            // Everything else is a Conversation stage.
            guard let card = queue.first else { return }
            beginSession(cards: queue)
            _ = card
            await waitForQuestion()
            await advance(to: route)
        }
    }

    /// Screenshot routing only — poll until a load settles, then carry on.
    private func waitUntil(_ ready: () -> Bool) async {
        for _ in 0..<40 where !ready() {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    private func waitForQuestion() async { await waitUntil { stage != .loadingQuestion } }
    private func waitForLibrary() async { await waitUntil { libraryLoad != .loading } }

    private func advance(to route: String) async {
        let answer = "So the key space is a ring of hashes, and each node owns the arc that ends at its own position. When you add a node, it takes over part of one neighbour's arc, so only the keys in that slice move — everything else stays put. That's the whole point versus mod-N hashing, where changing N reshuffles nearly everything."

        switch route {
        case "question":
            return
        case "text":
            inputMode = .text
        case "recording":
            stage = .recording
            draft = answer
        case "processing":
            thread.append(ThreadEntry(role: .answer, text: answer))
            stage = .processing
        case "followup", "score", "submit-failure", "reattempt", "reattempt-answered":
            // The re-attempt routes need a failing mechanism to be reachable at all,
            // so they force it rather than making the caller remember a second flag.
            if route.hasPrefix("reattempt") { DebugFlags.shared.failedMechanism = true }
            await submit(answer)
            if route == "followup" { return }
            if route == "submit-failure" {
                // The first submit already failed under WC_FAIL_SUBMIT.
                return
            }
            await submit("Each physical node gets many positions on the ring, so a new node picks up lots of small slices instead of one big one, which spreads the transfer across all the existing nodes.")
            await submit("Right — and replication follows the successor list.")
            guard route.hasPrefix("reattempt") else { return }
            beginReattempt()
            if route == "reattempt" { return }
            await submit("Right — so it's the arc, not the node name. Each node owns the stretch of hash space that ends at its own position.")
        default:
            return
        }
    }

    /// A sprint scores the card and writes it to history, but the schedule it
    /// would otherwise quote is untouched — so the line says that instead.
    static let practiceScheduleLine = "PRACTICE MODE · SCHEDULE UNCHANGED"

    /// `NEXT REVIEW · 27 JUL · INTERVAL 3D`
    static func scheduleLine(nextReviewAt: String, intervalDays: Int) -> String {
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd"
        let formatter = DateFormatter()
        formatter.dateFormat = "d MMM"
        let day = parser.date(from: nextReviewAt).map { formatter.string(from: $0) } ?? nextReviewAt
        return "NEXT REVIEW · \(day.uppercased()) · INTERVAL \(intervalDays)D"
    }
}

extension Array {
    subscript(safe index: Int) -> Element? {
        indices.contains(index) ? self[index] : nil
    }
}

extension AppState.LoadState {
    /// Every screen's mono status line reads the same while loading or offline
    /// and only differs once the data is in hand — so only that part is written
    /// per screen. The two strings are copy, and copy is final.
    func status(_ ready: () -> String) -> String {
        switch self {
        case .loading: return "CHECKING"
        case .error: return "OFFLINE"
        case .ready: return ready()
        }
    }
}

/// A deterministic source for `shuffled(using:)`, so Review Sprint's suggested
/// set is stable for a given seed and only Shuffle re-rolls it.
///
/// Not for anything that needs real randomness — this is a plain LCG whose only
/// job is reproducibility.
struct SeededGenerator: RandomNumberGenerator {
    private var state: UInt64

    init(seed: Int) {
        // Any non-zero start works; the constant just keeps small seeds from
        // producing near-identical first draws.
        state = UInt64(truncatingIfNeeded: seed) &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
    }

    mutating func next() -> UInt64 {
        state = state &* 6_364_136_223_846_793_005 &+ 1_442_695_040_888_963_407
        // Xorshift the high bits down: the low bits of a raw LCG cycle short.
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }
}
