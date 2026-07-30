import Foundation

/// The guide the user is part-way through pasting, and the settings around it.
///
/// Same shape and the same reasoning as `DraftStore`: one JSON file in
/// Application Support, an atomic write, and errors swallowed. Losing a pasted
/// study guide is the Study Plan equivalent of losing a spoken answer — the user
/// may have assembled it from several places, and there is no way to get it back
/// from the server, because a guide that has not been previewed yet has never
/// been sent anywhere.
struct GuideDraft: Codable, Equatable {
    var guideText: String
    var requestedWeeks: Int
    var weeklyCapacityHours: Int
    var fixedDeadline: Bool
    var subjectHint: String
}

enum GuideDraftStore {
    private static var url: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("study-plan-guide.json")
    }

    static func read() -> GuideDraft? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        return try? JSONDecoder().decode(GuideDraft.self, from: data)
    }

    static func save(_ draft: GuideDraft) {
        guard !draft.guideText.isEmpty else { return clear() }
        let directory = url.deletingLastPathComponent()
        try? FileManager.default.createDirectory(
            at: directory, withIntermediateDirectories: true
        )
        try? JSONEncoder().encode(draft).write(to: url, options: .atomic)
    }

    static func clear() {
        try? FileManager.default.removeItem(at: url)
    }
}
