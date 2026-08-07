import Foundation

/// Disk-first persistence for spoken Practice Debriefs.
///
/// The server receives a cheap debounced backup, but this file is what makes an
/// app kill, phone call, or network outage recover instantly without asking the
/// user to speak again.
enum PracticeDebriefDraftStore {
    private static var url: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("practice-debrief-drafts.json")
    }

    private static func load() -> [String: String] {
        guard let data = try? Data(contentsOf: url),
              let map = try? JSONDecoder().decode([String: String].self, from: data)
        else { return [:] }
        return map
    }

    private static func persist(_ map: [String: String]) {
        let directory = url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try? JSONEncoder().encode(map).write(to: url, options: .atomic)
    }

    static func save(_ text: String, for itemID: UUID) {
        var map = load()
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            map.removeValue(forKey: itemID.uuidString)
        } else {
            map[itemID.uuidString] = text
        }
        persist(map)
    }

    static func read(for itemID: UUID) -> String? {
        load()[itemID.uuidString]
    }

    static func clear(for itemID: UUID) {
        var map = load()
        map.removeValue(forKey: itemID.uuidString)
        persist(map)
    }
}
