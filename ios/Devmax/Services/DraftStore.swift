import Foundation

/// File-backed text drafts keyed by a domain UUID.
///
/// Callers keep their own semantic API and filename while sharing the exact
/// disk-first persistence behavior.
struct UUIDTextDraftStore {
    private let filename: String

    init(filename: String) {
        self.filename = filename
    }

    private func load() -> [String: String] {
        LocalJSONStore.read([String: String].self, from: filename) ?? [:]
    }

    private func persist(_ map: [String: String]) {
        LocalJSONStore.save(map, to: filename)
    }

    func save(_ text: String, for id: UUID) {
        var map = load()
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            map.removeValue(forKey: id.uuidString)
        } else {
            map[id.uuidString] = text
        }
        persist(map)
    }

    func read(for id: UUID) -> String? {
        load()[id.uuidString]
    }

    func clear(for id: UUID) {
        var map = load()
        map.removeValue(forKey: id.uuidString)
        persist(map)
    }
}

/// Local persistence for an in-progress answer.
///
/// The server also holds a debounced copy (`PATCH /sessions/{id}/draft`), but
/// disk is the source of truth for instant rehydration: if the app is
/// backgrounded mid-answer, the text must be there the moment it returns,
/// without waiting on a network round trip. Losing a spoken answer is the worst
/// failure mode in the product.
enum DraftStore {
    struct Record: Codable, Equatable {
        let text: String
        let sessionID: UUID?
        let turnIndex: Int?
        let discarded: Bool

        init(text: String, sessionID: UUID?, turnIndex: Int?, discarded: Bool = false) {
            self.text = text
            self.sessionID = sessionID
            self.turnIndex = turnIndex
            self.discarded = discarded
        }

        /// Before turn ownership existed, `drafts.json` stored bare strings.
        /// Decode those files without losing the whole map, but leave their
        /// context nil so they can never be injected into an unrelated live
        /// session or an already-advanced follow-up.
        init(from decoder: Decoder) throws {
            if let legacy = try? decoder.singleValueContainer().decode(String.self) {
                self.init(text: legacy, sessionID: nil, turnIndex: nil)
                return
            }
            let container = try decoder.container(keyedBy: CodingKeys.self)
            self.init(
                text: try container.decode(String.self, forKey: .text),
                sessionID: try container.decodeIfPresent(UUID.self, forKey: .sessionID),
                turnIndex: try container.decodeIfPresent(Int.self, forKey: .turnIndex),
                discarded: try container.decodeIfPresent(Bool.self, forKey: .discarded) ?? false
            )
        }
    }

    private static let filename = "drafts.json"

    private static func load() -> [String: Record] {
        LocalJSONStore.read([String: Record].self, from: filename) ?? [:]
    }

    private static func persist(_ map: [String: Record]) {
        LocalJSONStore.save(map, to: filename)
    }

    static func save(
        _ text: String, for cardID: UUID, sessionID: UUID, turnIndex: Int
    ) {
        var map = load()
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            map.removeValue(forKey: cardID.uuidString)
        } else {
            map[cardID.uuidString] = Record(
                text: text, sessionID: sessionID, turnIndex: turnIndex
            )
        }
        persist(map)
    }

    /// A late recognizer result belongs to its original turn, but a newer turn's
    /// draft already stored under the card is more recent and must win.
    static func preserveIfCurrentOrEmpty(
        _ text: String, for cardID: UUID, sessionID: UUID, turnIndex: Int
    ) {
        var map = load()
        if let existing = map[cardID.uuidString],
           existing.sessionID != sessionID || existing.turnIndex != turnIndex {
            return
        }
        map[cardID.uuidString] = Record(
            text: text, sessionID: sessionID, turnIndex: turnIndex
        )
        persist(map)
    }

    static func read(for cardID: UUID, sessionID: UUID, turnIndex: Int) -> String? {
        guard let record = load()[cardID.uuidString], !record.discarded,
              record.sessionID == sessionID, record.turnIndex == turnIndex
        else { return nil }
        return record.text
    }

    /// One-time migration for builds that stored only `[cardID: text]`. Those
    /// records have no session or turn provenance, so this is deliberately a
    /// narrow no-loss heuristic rather than proof: a resumed opening turn can
    /// adopt its local value, while an advanced turn adopts only when the server's
    /// current-turn draft is exactly the same. When advanced copies disagree the
    /// indexed server value wins; binding an old opening answer to a probe is the
    /// more damaging failure.
    static func adoptLegacy(
        for cardID: UUID, sessionID: UUID, turnIndex: Int, sessionResumed: Bool,
        serverDraftText: String
    ) -> String? {
        var map = load()
        guard let record = map[cardID.uuidString],
              record.sessionID == nil, record.turnIndex == nil
        else { return nil }
        guard sessionResumed,
              turnIndex == 0 || (!serverDraftText.isEmpty && serverDraftText == record.text)
        else {
            map.removeValue(forKey: cardID.uuidString)
            persist(map)
            return nil
        }
        let adopted = Record(
            text: record.text, sessionID: sessionID, turnIndex: turnIndex
        )
        map[cardID.uuidString] = adopted
        persist(map)
        return adopted.text
    }

    static func discard(for cardID: UUID, sessionID: UUID, turnIndex: Int) {
        var map = load()
        map[cardID.uuidString] = Record(
            text: "", sessionID: sessionID, turnIndex: turnIndex, discarded: true
        )
        persist(map)
    }

    static func isDiscarded(for cardID: UUID, sessionID: UUID, turnIndex: Int) -> Bool {
        guard let record = load()[cardID.uuidString] else { return false }
        return record.discarded && record.sessionID == sessionID && record.turnIndex == turnIndex
    }

    static func clear(for cardID: UUID) {
        var map = load()
        map.removeValue(forKey: cardID.uuidString)
        persist(map)
    }
}

/// Caches the last successful `/cards/due` response so the offline state can
/// show a real `LAST SYNCED 06:12 · 3 CARDS CACHED` line rather than a guess.
enum DueCache {
    private static let timestampKey = "wc.due.syncedAt"
    private static let countKey = "wc.due.count"

    static func record(count: Int) {
        UserDefaults.standard.set(Date(), forKey: timestampKey)
        UserDefaults.standard.set(count, forKey: countKey)
    }

    /// `nil` when the queue has never loaded — the offline state then omits the note.
    static var note: String? {
        guard let synced = UserDefaults.standard.object(forKey: timestampKey) as? Date else {
            return nil
        }
        let count = UserDefaults.standard.integer(forKey: countKey)
        let formatter = DateFormatter()
        formatter.dateFormat = "HH:mm"
        return "LAST SYNCED \(formatter.string(from: synced)) · \(count) CARDS CACHED"
    }
}
