import Foundation

/// Local persistence for an in-progress answer.
///
/// The server also holds a debounced copy (`PATCH /sessions/{id}/draft`), but
/// disk is the source of truth for instant rehydration: if the app is
/// backgrounded mid-answer, the text must be there the moment it returns,
/// without waiting on a network round trip. Losing a spoken answer is the worst
/// failure mode in the product.
enum DraftStore {
    private static let filename = "drafts.json"

    private static func load() -> [String: String] {
        LocalJSONStore.read([String: String].self, from: filename) ?? [:]
    }

    private static func persist(_ map: [String: String]) {
        LocalJSONStore.save(map, to: filename)
    }

    static func save(_ text: String, for cardID: UUID) {
        var map = load()
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            map.removeValue(forKey: cardID.uuidString)
        } else {
            map[cardID.uuidString] = text
        }
        persist(map)
    }

    static func read(for cardID: UUID) -> String? {
        load()[cardID.uuidString]
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
