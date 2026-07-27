import Foundation
import SwiftUI

/// Client state, named as the handoff's state-model table describes it.
@MainActor
final class AppState: ObservableObject {
    enum LoadState: Equatable { case loading, ready, error }
    enum Screen: Hashable { case today, conversation(UUID), history(UUID) }
    enum Sheet: String, Identifiable { case settings, add; var id: String { rawValue } }

    // Today
    @Published var load: LoadState = .loading
    @Published var queue: [DueCard] = []
    @Published var filter: ScoreStyle.Band?
    @Published var upcoming: [CardSummary] = []
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

    enum InputMode { case voice, text }

    /// In-flight debounced draft upload; cancelled and replaced on each edit.
    private var draftSync: Task<Void, Never>?

    let api: WarmCacheAPI

    init(api: WarmCacheAPI = APIConfig.client) {
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
        switch load {
        case .loading: return "CHECKING"
        case .error: return "OFFLINE"
        case .ready: return queue.isEmpty ? "NOTHING DUE" : "\(queue.count) CARDS DUE"
        }
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
            if queue.isEmpty {
                upcoming = (try? await api.cards(sort: "next_review", mode: "conversational")) ?? []
            }
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
        settings = new
        Task { _ = try? await api.updateSettings(new) }
    }

    // MARK: - Conversation

    /// Entering from a single row, or from Start with the whole filtered queue.
    func beginSession(cards: [DueCard], startingAt index: Int = 0) {
        sessionCards = cards
        cursor = index
        guard let card = cards[safe: index] else { return }
        path.append(.conversation(card.id))
        Task { await openCard(card) }
    }

    var currentCard: DueCard? { sessionCards[safe: cursor] }

    /// `CARD 1 OF 3` in a multi-card session, otherwise the card's category.
    var conversationLabel: String {
        guard sessionCards.count > 1 else { return currentCard?.category ?? "" }
        return "CARD \(cursor + 1) OF \(sessionCards.count)"
    }

    func openCard(_ card: DueCard) async {
        stage = .loadingQuestion
        thread = []
        draft = ""
        submitError = false
        result = nil
        resumeAvailable = false
        storedPartial = ""
        inputMode = DebugFlags.shared.textFirst ? .text : .voice

        do {
            let start = try await api.startSession(cardID: card.id)
            sessionID = start.sessionId
            thread = [ThreadEntry(role: .question, text: start.question)]

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

    func submit(_ text: String) async {
        guard let sessionID, !text.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        let answering = stage
        submitError = false

        // Optimistic: the answer appears in the thread immediately.
        thread.append(ThreadEntry(role: .answer, text: text))
        stage = .processing

        do {
            let outcome = try await api.submitAnswer(sessionID: sessionID, text: text)
            if let card = currentCard { DraftStore.clear(for: card.id) }
            draft = ""

            switch outcome {
            case .followUp(let question):
                thread.append(ThreadEntry(role: .followUpQuestion, text: question))
                stage = .followUp
            case .complete(let score, let feedback, let nextReviewAt, let intervalDays):
                result = SessionResult(
                    score: score,
                    feedback: feedback,
                    scheduleLine: Self.scheduleLine(nextReviewAt: nextReviewAt, intervalDays: intervalDays)
                )
                stage = .result
            }
        } catch {
            // The highest-stakes failure in the app. Remove the optimistic answer,
            // restore the text verbatim, rewind the stage so the control is live
            // again, and show the inline strip. No toast, no data loss.
            thread.removeLast()
            draft = text
            stage = (answering == .recordingFollowUp || answering == .followUp) ? .followUp : .idle
            submitError = true
        }
    }

    func nextCard() {
        guard cursor + 1 < sessionCards.count else {
            finish()
            return
        }
        cursor += 1
        guard let card = currentCard else { return }
        path = [.conversation(card.id)]
        Task { await openCard(card) }
    }

    var hasMoreCards: Bool { cursor + 1 < sessionCards.count }

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
        default:
            // Everything else is a Conversation stage.
            guard let card = queue.first else { return }
            beginSession(cards: queue)
            _ = card
            await waitForQuestion()
            await advance(to: route)
        }
    }

    private func waitForQuestion() async {
        for _ in 0..<40 where stage == .loadingQuestion {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }

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
        case "followup", "score", "submit-failure":
            await submit(answer)
            if route == "followup" { return }
            if route == "submit-failure" {
                // The first submit already failed under WC_FAIL_SUBMIT.
                return
            }
            await submit("Each physical node gets many positions on the ring, so a new node picks up lots of small slices instead of one big one, which spreads the transfer across all the existing nodes.")
            await submit("Right — and replication follows the successor list.")
        default:
            return
        }
    }

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
