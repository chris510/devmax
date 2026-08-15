import Foundation

struct PublicSetupDraft: Codable, Equatable {
    var title = ""
    var guideText = ""
    var originalFilename = ""
    var mimeType = "text/plain"
    /// Optional attribution only. Devmax never fetches this URL; it retains only
    /// the lesson text the learner intentionally pasted.
    var sourceURL = ""
    var sourceType = "guide"
    var contentProvenance = LessonContentProvenance.legacyUnspecified
    var importPath = "topics"
    var intent = "already_studied"
    var requestedWeeks = 12
    var weeklyCapacityHours = 8
    var sourceID: UUID?
    var previousVersionID: UUID?

    init() {}

    /// Device drafts predate lesson attribution. Decode additively so installing
    /// this build cannot strand an unfinished guide behind a key-not-found error.
    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        title = try values.decodeIfPresent(String.self, forKey: .title) ?? ""
        guideText = try values.decodeIfPresent(String.self, forKey: .guideText) ?? ""
        originalFilename = try values.decodeIfPresent(String.self, forKey: .originalFilename) ?? ""
        mimeType = try values.decodeIfPresent(String.self, forKey: .mimeType) ?? "text/plain"
        sourceURL = try values.decodeIfPresent(String.self, forKey: .sourceURL) ?? ""
        sourceType = try values.decodeIfPresent(String.self, forKey: .sourceType) ?? "guide"
        contentProvenance = try values.decodeIfPresent(
            String.self, forKey: .contentProvenance
        ) ?? LessonContentProvenance.legacyUnspecified
        importPath = try values.decodeIfPresent(String.self, forKey: .importPath) ?? "topics"
        intent = try values.decodeIfPresent(String.self, forKey: .intent) ?? "already_studied"
        requestedWeeks = try values.decodeIfPresent(Int.self, forKey: .requestedWeeks) ?? 12
        weeklyCapacityHours = try values.decodeIfPresent(Int.self, forKey: .weeklyCapacityHours) ?? 8
        sourceID = try values.decodeIfPresent(UUID.self, forKey: .sourceID)
        previousVersionID = try values.decodeIfPresent(UUID.self, forKey: .previousVersionID)
    }
}

enum PublicSetupStore {
    private static let filename = "public-setup.json"

    static func read() -> PublicSetupDraft? {
        LocalJSONStore.read(PublicSetupDraft.self, from: filename)
    }

    static func save(_ draft: PublicSetupDraft) {
        LocalJSONStore.save(draft, to: filename)
    }

    static func clear() { LocalJSONStore.clear(filename) }
}
