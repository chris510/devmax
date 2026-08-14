import Foundation
import SwiftUI

/// Client state, named as the handoff's state-model table describes it.
@MainActor
final class AppState: ObservableObject {
    enum LoadState: Equatable { case loading, ready, error }
    enum Screen: Hashable {
        case today, conversation(UUID), history(UUID), learning(UUID)
        case sprintSetup, coverage, recap
        // Study Plan: Today → overview → week → item, plus the flows that hang
        // off them. No new tab, and no second navigation stack.
        case planBuild
        case planPreview
        case planOverview(UUID)
        case planWeek(UUID, Int)
        case planItem(UUID, UUID)
        case practiceDebrief(UUID, UUID, Bool)
        case planProposal(UUID, ProposalKind)
        case planReopen(UUID, UUID)
        case planCards(UUID, UUID)
        case planUpdates(UUID)
        case planLifecycle(UUID, PlanLifecycleAction, PlanLifecycleOrigin)
        case planRecap(UUID)
        case planAudit(String)
        case materialSetup
        case fullSettings
        case privacy
        case deleteAccount
    }
    enum Sheet: String, Identifiable {
        case settings, add, plans, planCapacity
        var id: String { rawValue }
    }
    enum CaptureRoute: Identifiable, Equatable {
        case inbox
        case review(UUID)

        var id: String {
            switch self {
            case .inbox: return "inbox"
            case .review(let id): return "review-\(id)"
            }
        }
    }

    enum SessionOrigin: Equatable {
        case review, lesson
    }

    /// The expanded tier on Coverage. One at a time, across the whole screen.
    struct OpenTier: Equatable {
        let category: String
        let tier: ScoreStyle.Tier
    }

    // Today
    @Published var load: LoadState = .loading
    @Published var queue: [DueCard] = []
    /// The active plan's one line. Optional and never awaited on its own: a
    /// Study Plan outage must not stop a due card from appearing, so this is
    /// fetched concurrently with the queue and a failure leaves it nil.
    @Published var planSummary: StudyPlanSummary?
    @Published var planSummaryFailed = false
    @Published var filter: ScoreStyle.Band?
    @Published var sheet: Sheet?
    @Published var addPending = false
    @Published var addError = false
    @Published var captures: [CaptureSummary] = []
    @Published var savedCapture: PendingCapture?
    @Published var captureRoute: CaptureRoute?
    @Published var settings: AppSettings = .placeholder
    /// Immediate client-side overlay for a learning exposure. History remains
    /// alive underneath Learn in the navigation stack, so its original detail
    /// response cannot be trusted to know about the just-created delay.
    @Published private(set) var learningRecallNotBefore: [UUID: String] = [:]

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
    /// Presentation context only. Scheduling still comes exclusively from the
    /// server's per-session `practice` flag.
    @Published private(set) var sessionOrigin: SessionOrigin = .review
    /// Installed only for onboarding's first real review. The ordinary review
    /// path has no callback and continues to Today exactly as before.
    var firstReviewCompletion: (() -> Void)?

    // Review Sprint / Coverage
    @Published var library: [CardSummary] = []
    @Published var libraryLoad: LoadState = .loading
    /// Empty means the whole library, not "nothing".
    @Published var setupCats: Set<String> = []
    @Published var setupSize = 6
    /// Bumped by Shuffle; the only input that re-rolls the suggested set.
    @Published var seed = 1
    @Published var sprintKind: SprintKind = .review
    @Published var sprintExcluded: Set<UUID> = []
    @Published var covOpen: OpenTier?
    @Published var recapOpen: UUID?

    enum InputMode { case voice, text }

    static let minSessionSize = 4
    static let maxSessionSize = 10

    enum DepthAxis: String { case depth, boundaries }
    enum SprintKind: Equatable { case review, depth(DepthAxis) }

    struct AxisRollupItem: Identifiable {
        let id: String
        let label: String
        let value: Double
        let depthAxis: DepthAxis?
        let isActionable: Bool

        var text: String { label + " " + String(format: "%.1f", value) }
    }

    /// In-flight debounced draft upload; cancelled and replaced on each edit.
    private var draftSync: Task<Void, Never>?
    /// The question request currently allowed to write conversation state.
    ///
    /// Card identity alone is insufficient: two retries for the same card can
    /// resolve out of order. The token makes the latest attempt the sole owner,
    /// while the task handle lets normal UI navigation cancel work promptly.
    private var questionLoadTask: Task<Void, Never>?
    private var questionLoadID = UUID()

    let api: DevmaxAPI

    init(api: DevmaxAPI = APIConfig.client) {
        self.api = api
        if DebugFlags.shared.scoringV2 {
            settings.activeScoringContractVersion = 2
        }
    }

    var usesRecallContract: Bool { settings.usesRecallContract }

    func displayScore(_ card: DueCard) -> Int? {
        usesRecallContract ? card.recallScore : card.lastScore
    }

    func displayScore(_ card: CardSummary) -> Int? {
        usesRecallContract ? card.recallScore : card.lastScore
    }

    // MARK: - Today

    /// The cards Start will walk, honouring the active mastery-band filter.
    var visibleQueue: [DueCard] {
        guard let filter else { return queue }
        return queue.filter { ScoreStyle.Band.of(displayScore($0)) == filter }
    }

    /// Bands present in the queue, in fixed order, with zero-count bands omitted.
    var bands: [(band: ScoreStyle.Band, count: Int)] {
        ScoreStyle.Band.allCases.compactMap { band in
            let count = queue.filter { ScoreStyle.Band.of(displayScore($0)) == band }.count
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
        // Concurrent, not sequential. The plan summary is a second network call
        // and it must not add its latency to the queue's — nor its failure. The
        // `try?` is the whole safety property: an unreachable Study Plan degrades
        // one line on Today and leaves the due cards untouched.
        async let dueCards = api.due()
        async let summary = try? await api.activePlan()
        async let captured = try? await api.captures()
        do {
            queue = try await dueCards
            planSummary = await summary
            captures = await captured ?? captures
            planSummaryFailed = planSummary == nil
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

    func saveCapture(topic: String, context: String) async {
        addPending = true
        addError = false
        do {
            let capture = try await api.createCapture(topic: topic, context: context)
            savedCapture = capture
            captures.removeAll { $0.id == capture.id }
            captures.insert(capture.summary, at: 0)
            addPending = false
        } catch {
            addPending = false
            addError = true
        }
    }

    func refreshCaptures() async {
        if let value = try? await api.captures() { captures = value }
    }

    func openCapture(_ id: UUID) {
        sheet = nil
        captureRoute = .review(id)
    }

    func closeCaptureFlow() {
        captureRoute = nil
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
        sprintKind = .review
        sprintExcluded = []
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
        orderedCategories(in: library)
    }

    var sprintCategories: [String] {
        orderedCategories(in: sprintBase)
    }

    private func orderedCategories(in cards: [CardSummary]) -> [String] {
        let present = Set(cards.map(\.category))
        let known = Self.categoryOrder.filter(present.contains)
        return known + present.subtracting(Self.categoryOrder).sorted()
    }

    private var sprintBase: [CardSummary] {
        let recallReady = library.filter { $0.recallIsAvailable() }
        if usesRecallContract { return recallReady }
        switch sprintKind {
        case .review:
            return recallReady
        case .depth(let axis):
            let scored = recallReady.filter { depthValue($0, axis: axis) != nil }
            let thin = scored.filter { (depthValue($0, axis: axis) ?? 5) <= 2 }
            return thin.count >= Self.minSessionSize ? thin : scored
        }
    }

    /// The cards a sprint would draw from: the whole library, or just the
    /// selected categories. An empty selection means everything.
    var sprintPool: [CardSummary] {
        let filtered = setupCats.isEmpty
            ? sprintBase : sprintBase.filter { setupCats.contains($0.category) }
        return filtered
    }

    /// The suggested set, ranked weakest-first then least-recently-reviewed,
    /// shuffled within the top `size + 4`, then re-sorted back into rank order so
    /// the walk always opens on the weakest card.
    ///
    /// Unrated cards sort as weakest — an untested card is the strongest reason
    /// to run a sprint at all.
    var sprintSet: [CardSummary] {
        let ranked = sprintPool.sorted { a, b in
            let wa: Int
            let wb: Int
            switch sprintKind {
            case .review:
                (wa, wb) = (displayScore(a) ?? -1, displayScore(b) ?? -1)
            case .depth(let axis):
                (wa, wb) = (depthValue(a, axis: axis) ?? 5, depthValue(b, axis: axis) ?? 5)
            }
            if wa != wb { return wa < wb }
            return (a.daysSinceReview ?? 0) > (b.daysSinceReview ?? 0)
        }
        // Seeded so the same seed always yields the same set — Shuffle is the only
        // thing that changes it, not a re-render. Filtering `ranked` at the end is
        // what puts the walk back into rank order.
        var rng = SeededGenerator(seed: seed)
        let chosen = Set(ranked.prefix(setupSize + 4).shuffled(using: &rng).prefix(setupSize).map(\.id))
        return ranked.filter { chosen.contains($0.id) && !sprintExcluded.contains($0.id) }
    }

    var setupStatus: String {
        libraryLoad.status {
            if case .depth = sprintKind {
                let n = sprintPool.count
                return "\(n) CARD\(n == 1 ? "" : "S") WITH DEPTH SIGNAL"
            }
            guard !setupCats.isEmpty else {
                let count = sprintBase.count
                if count < library.count {
                    return "\(count) CARD\(count == 1 ? "" : "S") AVAILABLE FOR RECALL"
                }
                return "\(count) CARD\(count == 1 ? "" : "S") IN LIBRARY"
            }
            let n = sprintPool.count
            return "\(n) CARD\(n == 1 ? "" : "S") IN FILTER"
        }
    }

    /// The filtered pool holds at least a session's minimum. When the library has
    /// loaded and this is false, the pool is too narrow and Setup says so.
    var setupReady: Bool { libraryLoad == .ready && sprintSet.count >= Self.minSessionSize }

    var setupWaitingOnRecall: Bool {
        guard case .review = sprintKind else { return false }
        return libraryLoad == .ready
            && sprintBase.count < Self.minSessionSize
            && library.count >= Self.minSessionSize
    }

    /// The category chip's second line: its most urgent count, in priority order.
    func chipNote(for category: String) -> String {
        if case .depth = sprintKind {
            let count = sprintBase.filter { $0.category == category }.count
            return "\(count) depth gap\(count == 1 ? "" : "s")"
        }
        let cards = sprintBase.filter { $0.category == category }
        let weak = tally(cards, [.cold, .shaky])
        if weak > 0 { return "\(weak) shaky" }
        for tier in [ScoreStyle.Tier.untested, .developing] where tally(cards, [tier]) > 0 {
            return "\(tally(cards, [tier])) \(tier.rawValue)"
        }
        return "\(tally(cards, [.solid])) solid"
    }

    func toggleCategory(_ name: String) {
        sprintExcluded = []
        if setupCats.contains(name) { setupCats.remove(name) } else { setupCats.insert(name) }
    }

    func removeFromSprint(_ id: UUID) {
        sprintExcluded.insert(id)
    }

    func enterDepthRepair(_ axis: DepthAxis) {
        guard !usesRecallContract else { return }
        sprintKind = .depth(axis)
        sprintExcluded = []
        setupCats = []
        seed += 1
        covOpen = nil
        if path.last == .coverage { path.removeLast() }
    }

    private func depthValue(_ card: CardSummary, axis: DepthAxis) -> Int? {
        switch axis {
        case .depth: return card.lastDepth
        case .boundaries: return card.lastBoundaries
        }
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
                let (wa, wb) = (tally(a.cards, [.cold, .shaky]), tally(b.cards, [.cold, .shaky]))
                if wa != wb { return wa > wb }
                let (ua, ub) = (tally(a.cards, [.untested]), tally(b.cards, [.untested]))
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

    func tally(_ cards: [CardSummary], _ tiers: [ScoreStyle.Tier]) -> Int {
        cards.filter { tiers.contains(ScoreStyle.Tier.of(displayScore($0))) }.count
    }

    var coverageStatus: String {
        libraryLoad.status { "\(library.count) CARDS · \(categories.count) CATEGORIES" }
    }

    /// `ACCURACY 4.1 · DEPTH 2.8 · BOUNDARIES 3.2`.
    ///
    /// Scoring runs on three axes internally; this is the only place that
    /// decomposition surfaces, because "which axis is systemically weak" is the
    /// question Coverage exists to answer. Empty until something has been scored.
    var axisRollup: [AxisRollupItem] {
        guard libraryLoad == .ready, !usesRecallContract else { return [] }
        let axes: [(String, DepthAxis?, (CardSummary) -> Int?)] = [
            ("ACCURACY", nil, \.lastAccuracy),
            ("DEPTH", .depth, \.lastDepth),
            ("BOUNDARIES", .boundaries, \.lastBoundaries),
        ]
        let means: [(String, DepthAxis?, Double)] = axes.compactMap { name, depthAxis, value in
            let values = library.compactMap(value)
            guard !values.isEmpty else { return nil }
            let mean = Double(values.reduce(0, +)) / Double(values.count)
            return (name, depthAxis, mean)
        }
        guard means.count == axes.count else { return [] }
        let weakestDepth = means
            .filter { $0.1 != nil }
            .min { $0.2 < $1.2 }?.1
        return means.map { name, depthAxis, value in
            AxisRollupItem(
                id: name, label: name, value: value, depthAxis: depthAxis,
                isActionable: depthAxis == weakestDepth
            )
        }
    }

    // MARK: - Conversation

    /// Entering from a single row, from Start with the whole filtered queue, or
    /// from a Review Sprint's suggested set.
    func beginSession(
        cards: [DueCard], startingAt index: Int = 0, practice: Bool = false,
        replacingPath: Bool = false, origin: SessionOrigin = .review
    ) {
        sessionCards = cards
        cursor = index
        self.practice = practice
        sessionOrigin = origin
        run = []
        recapOpen = nil
        guard let card = cards[safe: index] else { return }
        // A sprint replaces Setup rather than stacking on it, so ✕ lands on Today.
        if replacingPath { path = [.conversation(card.id)] } else { path.append(.conversation(card.id)) }
        launchQuestionLoad(card)
    }

    /// Rescues the empty-history path without turning Card History into an
    /// unscheduled practice launcher. Only a card in Today's due queue may start
    /// here, and Conversation replaces History so its close action returns home.
    @discardableResult
    func beginReviewFromHistory(cardID: UUID) -> Bool {
        guard RecallGate.isOpen(learningRecallNotBefore[cardID]) else { return false }
        guard let card = queue.first(where: { $0.id == cardID }) else { return false }
        beginSession(cards: [card], replacingPath: true)
        return true
    }

    func beginLearningFromHistory(cardID: UUID) {
        path.append(.learning(cardID))
    }

    /// A Study Plan mapping can name the recall card before the Card row has
    /// been created. Only a committed mapping gets a History destination; a
    /// pending mapping stays visible but inert on the item screen.
    @discardableResult
    func openMappedRecallCard(
        _ mapping: PlanMappedRecallCard, itemComplete: Bool
    ) -> Bool {
        guard itemComplete, let cardID = mapping.cardId else { return false }
        path.append(.history(cardID))
        return true
    }

    /// History is normally entered from Today, but Study Plan mappings push it
    /// on top of an item. Keep the label honest without changing pop behavior.
    var historyBackLabel: String {
        guard path.count > 1 else { return "← Today" }
        if case .planItem = path[path.count - 2] { return "← Plan item" }
        return "← Today"
    }

    func recallNotBefore(cardID: UUID, fallback: String?) -> String? {
        learningRecallNotBefore[cardID] ?? fallback
    }

    func recallIsAvailable(cardID: UUID, fallback: String?) -> Bool {
        RecallGate.isOpen(recallNotBefore(cardID: cardID, fallback: fallback))
    }

    /// POST /learning is the exposure write. Once it succeeds, every local
    /// recall launcher must close immediately; SM-2 fields remain untouched.
    func markLearningExposure(_ learning: LearningCard) {
        learningRecallNotBefore[learning.cardId] = learning.recallAvailableAt
        queue.removeAll { $0.id == learning.cardId }
        sprintExcluded.insert(learning.cardId)
        if let index = library.firstIndex(where: { $0.id == learning.cardId }) {
            library[index].recallNotBeforeAt = learning.recallAvailableAt
        }
        // A normal Today launch defers the full library fetch while cards are
        // due. If Learn removes the last one, populate that library now so Today
        // cannot mistake an existing deck for a brand-new empty account.
        if queue.isEmpty, library.isEmpty {
            Task { await loadLibrary() }
        }
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
        // Cleared with the rest of the card's state, not left behind. A failed
        // `startSession` used to leave the *previous* card's id in place, and the
        // next answer submitted against it scored the wrong card.
        sessionID = nil
        result = nil
        resumeAvailable = false
        storedPartial = ""
        inputMode = DebugFlags.shared.textFirst ? .text : .voice
    }

    /// Opens a card directly and waits for the attempt to settle. UI entry points
    /// use `launchQuestionLoad` so they can cancel the task on navigation; this
    /// form stays available for deterministic state-machine tests.
    func openCard(_ card: DueCard) async {
        let loadID = prepareQuestionLoad()
        let requestedPractice = practice
        await loadCard(card, practice: requestedPractice, loadID: loadID)
    }

    /// Starts a UI-owned load. Preparation is synchronous so the outgoing card is
    /// cleared in the same turn as navigation, before the new task can be scheduled.
    @discardableResult
    private func launchQuestionLoad(_ card: DueCard) -> Task<Void, Never> {
        let loadID = prepareQuestionLoad()
        let requestedPractice = practice
        let task = Task { [weak self] in
            guard let self else { return }
            await self.loadCard(card, practice: requestedPractice, loadID: loadID)
        }
        questionLoadTask = task
        return task
    }

    /// Cancels the previous UI task and gives this attempt exclusive ownership of
    /// the state it will eventually populate. A new UUID also invalidates direct
    /// test calls and any transport that does not cooperate with cancellation.
    private func prepareQuestionLoad() -> UUID {
        invalidateQuestionLoad()
        let loadID = UUID()
        questionLoadID = loadID
        resetForNewCard()
        return loadID
    }

    private func invalidateQuestionLoad() {
        questionLoadTask?.cancel()
        questionLoadTask = nil
        questionLoadID = UUID()
    }

    private func loadCard(_ card: DueCard, practice: Bool, loadID: UUID) async {
        do {
            let start = try await api.startSession(cardID: card.id, practice: practice)
            // Question generation can take long enough to leave this screen and
            // open another card. A late response must never replace that newer
            // card's session, even if cancellation did not reach the transport.
            guard questionLoadID == loadID, currentCard?.id == card.id else { return }
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
            // A superseded request may fail after the replacement has already
            // loaded. Its error belongs to the old screen and is discarded.
            guard questionLoadID == loadID, currentCard?.id == card.id else { return }
            // No session, so no question and nothing to answer. Reporting this as
            // the submit-failure strip was wrong twice over: it claimed "your answer
            // is saved" when nothing had been said yet, and its `Try again` called
            // `submit`, which returns immediately on a nil `sessionID` — so the
            // control stayed live over a dead session and every spoken answer went
            // nowhere. `.questionFailed` accepts no answer and has no footer, so
            // that combination is now unrepresentable rather than merely avoided.
            stage = .questionFailed(error.loadNote)
        }
    }

    /// Re-open the current card. The only recovery from a question that failed to
    /// load — and the only thing the failure's `Retry` may call.
    func retryQuestion() async {
        guard let card = currentCard else { return }
        await launchQuestionLoad(card).value
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
        syncDraft(debounced: false, uploadToServer: true)
    }

    /// Persist the final transcript to disk immediately before `/answers`, while
    /// cancelling any debounced server PATCH. Uploading that same draft here can
    /// race the answer transaction: a late PATCH then repopulates `draft_text`
    /// after the server has advanced to the follow-up turn.
    func flushDraftForSubmission() async {
        // Cancellation alone is not an ordering guarantee once URLSession may
        // already have put the PATCH on the wire. Wait for that task to return
        // before `/answers`; even a server that finishes the cancelled PATCH now
        // necessarily does so before the answer transaction clears the draft.
        let pendingUpload = draftSync
        draftSync = nil
        pendingUpload?.cancel()
        syncDraft(debounced: false, uploadToServer: false)
        await pendingUpload?.value
    }

    private func scheduleDraftSync() {
        syncDraft(debounced: true, uploadToServer: true)
    }

    /// Disk is the source of truth for instant rehydration; the server copy is the
    /// durable backup. Both are debounced because this is called on every keystroke
    /// *and* every speech-recognition partial — several times a second while
    /// speaking. `DraftStore.save` is a read-modify-rewrite of the whole file plus an
    /// atomic rename, so running it per partial competes with the live caret and
    /// auto-scroll for the same main-thread frame budget. `flushDraft` on
    /// backgrounding and on recording-stop is what makes the guarantee exact.
    private func syncDraft(debounced: Bool, uploadToServer: Bool) {
        draftSync?.cancel()
        guard let card = currentCard else { return }
        let text = draft

        guard debounced else {
            DraftStore.save(text, for: card.id)
            if uploadToServer, let sessionID {
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
        // Cleared with the commit, not with the response: the answer is in the
        // thread now, and a draft outliving it renders the same words a second
        // time with a live caret under the scoring indicator. Only the in-memory
        // copy goes — disk still holds the answer until the request lands, so an
        // app killed in flight can rehydrate it, and the catch below restores it.
        draft = ""

        do {
            let value = try await send(sessionID, text)
            // The request outlives the screen: ✕ is live during `.processing`, so the
            // user can close the session and open another card while this is in
            // flight. Applying a stale result would score the wrong card.
            guard self.sessionID == sessionID else { return nil }
            if let card = currentCard { DraftStore.clear(for: card.id) }
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
        if stage.isCoaching {
            await submitCoaching(text)
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
            let score, let recallScore, let scoringContractVersion, let feedback,
            let nextReviewAt, let intervalDays, let wasPractice,
            let reattemptOffered, let reattemptPrompt, let coachingOffered,
            let coachingFocus, let coachingQuestion
        ):
            let displayedScore = scoringContractVersion == 2 ? recallScore : score
            let scheduleLine = wasPractice
                ? Self.practiceScheduleLine
                : Self.scheduleLine(nextReviewAt: nextReviewAt, intervalDays: intervalDays)
            result = SessionResult(
                score: displayedScore,
                scoringContractVersion: scoringContractVersion,
                feedback: feedback,
                scheduleLine: scheduleLine,
                reattemptOffered: reattemptOffered,
                reattemptPrompt: reattemptPrompt,
                coachingOffered: coachingOffered,
                coachingFocus: coachingFocus,
                coachingQuestion: coachingQuestion
            )
            if let card = currentCard {
                run.append(
                    RunEntry(
                        id: card.id, topic: card.topic, category: card.category,
                        score: displayedScore, feedback: feedback,
                        scheduleLine: scheduleLine, practice: wasPractice
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

    /// Open V2's optional qualitative practice. It is mutually exclusive with
    /// the failed-Recall re-attempt and keeps the scored result visible as the
    /// only numeral associated with the session.
    func beginCoaching() {
        guard let result, result.coachingOffered, stage == .result,
              let question = result.coachingQuestion
        else { return }
        thread.append(ThreadEntry(role: .coachingQuestion, text: question))
        draft = ""
        submitError = false
        stage = .coaching
    }

    private func submitCoaching(_ text: String) async {
        guard let outcome = await sendAnswer(
            text, via: { id, body in
                try await self.api.submitCoaching(sessionID: id, text: body)
            }
        ) else { return }
        thread.append(ThreadEntry(role: .coachingFeedback, text: outcome.feedback))
        result?.coachingOffered = false
        stage = .result
    }

    /// The session-end button. `Next card` on every card but the last; on the last
    /// card of a multi-card run it reads `See recap`, and a single-card session
    /// keeps `Done`. The transition is always a tap, never an automatic swap.
    var sessionEndLabel: String {
        if hasMoreCards { return "Next card" }
        if sessionOrigin == .lesson { return "See results" }
        return sessionCards.count > 1 ? "See recap" : "Done"
    }

    func nextCard() {
        guard cursor + 1 < sessionCards.count else {
            if sessionCards.count > 1 || sessionOrigin == .lesson {
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
        launchQuestionLoad(card)
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

    var runWasLesson: Bool { sessionOrigin == .lesson && !run.isEmpty }

    func runAnother() {
        path = [.sprintSetup]
        Task { await loadLibrary() }
    }

    func finish() {
        // Leaving while Claude is generating a question must invalidate both a
        // late success and a late failure. The server may still finish and retain
        // a resumable session; reopening the card will retrieve it normally.
        invalidateQuestionLoad()
        path = []
        if let completion = firstReviewCompletion {
            firstReviewCompletion = nil
            completion()
            return
        }
        Task { await loadToday() }
    }

    // MARK: - Screenshot routing

    /// Applies `WC_ROUTE` after the queue loads, so a single `simctl launch` can
    /// land on any designed state for comparison against its screenshot.
    /// The Study Plan screenshot routes. Every designed state, including the
    /// failure paths, reachable without hand navigation.
    private func applyStudyPlanRoute(_ route: String, plan: StudyPlanState) async {
        let planID = StudyPlanFixtures.backendPlanID
        let itemID = StudyPlanFixtures.firstItemID
        let practiceItemID = StudyPlanFixtures.practiceItemID

        switch route {
        case "study-plan-build":
            path.append(.planBuild)

        case "study-plan-import-failure":
            path.append(.planBuild)
            plan.guideText = String(
                repeating: "Week 1: The request path. Trace a request end to end. ", count: 6
            )
            await plan.runPreview()
            path.append(.planPreview)

        case "study-plan-preview":
            plan.guideText = String(
                repeating: "Week 1: The request path. Trace a request end to end. ", count: 6
            )
            await plan.runPreview()
            path.append(.planPreview)

        case "study-plan-no-active":
            // Today's zero-plan affordance; nothing to push.
            break

        case "study-plan-plans":
            sheet = .plans

        case "study-plan-overview", "study-plan-overview-expanded",
             "study-plan-overview-future":
            path.append(.planOverview(planID))
            await waitUntil { plan.overviewLoad == .ready }
            switch route {
            case "study-plan-overview": plan.chooseOpenPhase(nil)
            case "study-plan-overview-expanded": plan.chooseOpenPhase(2)
            // The last phase, which is the case the one-viewport budget is
            // most likely to fail on.
            default: plan.chooseOpenPhase(plan.overview?.phases.last?.index)
            }

        case "study-plan-week":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))

        case "study-plan-item", "study-plan-item-actionable",
             "study-plan-item-python", "study-plan-item-linked":
            let routedWeekIndex = route == "study-plan-item" ? 4 : 1
            let routedItemID: UUID
            switch route {
            case "study-plan-item-actionable", "study-plan-item-linked":
                routedItemID = StudyPlanFixtures.week1DeliveryItemID
            case "study-plan-item-python":
                routedItemID = StudyPlanFixtures.week1PythonItemID
            default:
                routedItemID = itemID
            }
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, routedWeekIndex))
            path.append(.planItem(planID, routedItemID))
            await waitUntil { plan.itemLoad == .ready }
            switch route {
            case "study-plan-item-actionable":
                plan.item = StudyPlanFixtures.actionableItemDetail(planID: planID)
            case "study-plan-item-python":
                plan.item = StudyPlanFixtures.pythonItemDetail(planID: planID)
            case "study-plan-item-linked":
                plan.item = StudyPlanFixtures.linkedItemDetail(planID: planID)
            default:
                break
            }

        case "study-plan-debrief-offer", "study-plan-debrief-idle",
             "study-plan-debrief-mic-unavailable", "study-plan-debrief-recording",
             "study-plan-debrief-text", "study-plan-debrief-resume",
             "study-plan-debrief-save-failure", "study-plan-debrief-checking",
             "study-plan-debrief-check-failure":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))
            path.append(.planItem(planID, practiceItemID))
            await waitUntil { plan.itemLoad == .ready }
            if route == "study-plan-debrief-resume" {
                PracticeDebriefDraftStore.save(
                    "I got the request order right, but I froze on retries and drew them outside the timeout.",
                    for: practiceItemID
                )
            }
            plan.preparePracticeDebrief()
            path.append(.practiceDebrief(
                planID, practiceItemID, route == "study-plan-debrief-offer"
            ))

        case "study-plan-debrief-completed":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))
            path.append(.planItem(planID, practiceItemID))
            await waitUntil { plan.itemLoad == .ready }
            plan.item = StudyPlanFixtures.itemDetail(
                practiceItemID, planID: planID, complete: true,
                debrief: StudyPlanFixtures.practiceDebrief(submitted: true, proposalCount: 2)
            )

        case "study-plan-capacity":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))
            await waitUntil { plan.weekLoad == .ready }
            sheet = .planCapacity

        case "study-plan-replan", "study-plan-replan-invalid", "study-plan-fixed-recovery":
            path.append(.planOverview(planID))
            await waitUntil { plan.overviewLoad == .ready }
            path.append(.planProposal(planID, .replan))

        case "study-plan-reopen", "study-plan-reopen-invalid":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))
            path.append(.planItem(planID, itemID))
            await waitUntil { plan.itemLoad == .ready }
            path.append(.planReopen(planID, itemID))

        case "study-plan-card-proposal", "study-plan-card-failure",
             "study-plan-card-existing":
            path.append(.planOverview(planID))
            path.append(.planWeek(planID, 4))
            path.append(.planItem(planID, itemID))
            await waitUntil { plan.itemLoad == .ready }
            path.append(.planCards(planID, itemID))
            await waitUntil { plan.cardLoad == .ready }
            if route == "study-plan-card-failure" { await plan.addSelectedCards() }

        case "study-plan-retrieval-audit":
            await pushAudit("retrieval-audit", plan: plan)
        case "study-plan-estimate-audit":
            await pushAudit("estimate-audit", plan: plan)
        case "study-plan-dependency-audit":
            await pushAudit("dependency-audit", plan: plan)

        case "study-plan-updates":
            path.append(.planOverview(planID))
            path.append(.planUpdates(planID))

        case "study-plan-complete":
            path.append(.planRecap(StudyPlanFixtures.completedPlanID))

        default:
            // Named but unhandled: land on the overview rather than silently
            // somewhere else entirely.
            path.append(.planOverview(planID))
        }
    }

    private func pushAudit(_ destination: String, plan: StudyPlanState) async {
        plan.guideText = String(
            repeating: "Week 1: The request path. Trace a request end to end. ", count: 6
        )
        await plan.runPreview()
        path.append(.planPreview)
        path.append(.planAudit(destination))
    }

    func applyDebugRoute(plan: StudyPlanState? = nil) async {
        let route = DebugFlags.shared.route
        guard !route.isEmpty else { return }

        // Study Plan routes are handled first and return, because the `default`
        // case below falls through to Conversation — a misspelled plan route
        // would otherwise open a card and read as a routing bug.
        if route.hasPrefix("study-plan"), let plan {
            await applyStudyPlanRoute(route, plan: plan)
            return
        }

        switch route {
        case "settings": sheet = .settings
        case "add": sheet = .add
        case "capture-inbox": captureRoute = .inbox
        case "capture-source":
            if let capture = captures.first(where: { $0.status == "pending_source" }) {
                captureRoute = .review(capture.id)
            }
        case "capture-question":
            if let capture = captures.first(where: { $0.status == "ready_to_review" }) {
                captureRoute = .review(capture.id)
            }
        case "filter": filter = .shaky
        case "history":
            if let card = queue.first { path.append(.history(card.id)) }
        case "history-empty":
            if let capture = captures.first(where: { $0.status == "ready_to_review" }) {
                _ = try? await api.activateCapture(capture.id, schedule: "now")
            }
            queue = (try? await api.due()) ?? queue
            if let card = queue.first { path.append(.history(card.id)) }
        case "learning":
            if let card = queue.first {
                path.append(.history(card.id))
                path.append(.learning(card.id))
            }
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
                   tally(section.cards, [tier]) > 0
               }) {
                covOpen = OpenTier(category: section.category, tier: tier)
            }
        case "depth-repair":
            enterSprintSetup()
            await waitForLibrary()
            if let axis = axisRollup.first(where: \.isActionable)?.depthAxis {
                enterDepthRepair(axis)
            }
        case "recap", "recap-expanded":
            enterSprintSetup()
            await waitForLibrary()
            startSprint()
            await waitForQuestion()
            // Walk the whole set so the recap has a full run behind it. A sprint
            // card is never probed, so one answer scores it — but answering until
            // the score lands, rather than a fixed number of times, is what keeps
            // a change to that budget from stranding the walk mid-card.
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
        case "question", "question-failure":
            // `question-failure` needs no steps of its own — `WC_FAIL_QUESTION`
            // fails the load and the screen is already in the state.
            return
        case "text":
            inputMode = .text
        case "recording":
            stage = .recording
            draft = answer
        case "processing":
            thread.append(ThreadEntry(role: .answer, text: answer))
            stage = .processing
        case "followup", "followup-second", "score", "submit-failure", "reattempt",
             "reattempt-answered", "coaching", "coaching-answered", "coaching-boundary",
             "coaching-boundary-answered":
            await submit(answer)
            if route == "followup" { return }
            if route == "submit-failure" {
                // The first submit already failed under WC_FAIL_SUBMIT.
                return
            }
            // A daily review is probed once, so this second answer is the one the
            // score lands on — except under `followup-second`, where the route has
            // armed the session's second probe and this answer draws that instead.
            await submit("Each physical node gets many positions on the ring, so a new node picks up lots of small slices instead of one big one, which spreads the transfer across all the existing nodes.")
            if route == "followup-second" { return }
            if route.hasPrefix("coaching") {
                beginCoaching()
                if route == "coaching" || route == "coaching-boundary" { return }
                await submit("The deeper causal link is that virtual nodes spread ownership changes across many small arcs.")
                return
            }
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
