import Foundation

struct PublicSetupDraft: Codable, Equatable {
    var title = ""
    var guideText = ""
    var originalFilename = ""
    var mimeType = "text/plain"
    var importPath = "topics"
    var intent = "already_studied"
    var requestedWeeks = 12
    var weeklyCapacityHours = 8
    var sourceID: UUID?
    var previousVersionID: UUID?
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
