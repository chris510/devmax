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

    func clearAll() {
        LocalJSONStore.clear(filename)
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
    private static let store = UUIDTextDraftStore(filename: "drafts.json")

    static func save(_ text: String, for cardID: UUID) {
        store.save(text, for: cardID)
    }

    static func read(for cardID: UUID) -> String? {
        store.read(for: cardID)
    }

    static func clear(for cardID: UUID) {
        store.clear(for: cardID)
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
