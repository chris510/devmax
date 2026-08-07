import Foundation

/// Disk-first persistence for spoken Practice Debriefs.
///
/// The server receives a cheap debounced backup, but this file is what makes an
/// app kill, phone call, or network outage recover instantly without asking the
/// user to speak again.
enum PracticeDebriefDraftStore {
    private static let store = UUIDTextDraftStore(filename: "practice-debrief-drafts.json")

    static func save(_ text: String, for itemID: UUID) {
        store.save(text, for: itemID)
    }

    static func read(for itemID: UUID) -> String? {
        store.read(for: itemID)
    }

    static func clear(for itemID: UUID) {
        store.clear(for: itemID)
    }
}
