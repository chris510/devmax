import Foundation

/// Disk-first persistence for formation and transfer responses.
///
/// The server receives a cheap backup, but an app termination or provider
/// failure must recover the learner's words immediately without first waiting
/// on a GET. The UUID is a LessonCheck id, never a Session id.
enum LessonCheckDraftStore {
    private static let store = UUIDTextDraftStore(filename: "lesson-check-drafts.json")

    static func save(_ text: String, for checkID: UUID) {
        store.save(text, for: checkID)
    }

    static func read(for checkID: UUID) -> String? {
        store.read(for: checkID)
    }

    static func clear(for checkID: UUID) {
        store.clear(for: checkID)
    }

    static func clearAll() {
        store.clearAll()
    }
}
