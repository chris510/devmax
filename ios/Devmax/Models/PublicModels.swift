import Foundation

struct AccountProfile: Codable, Equatable {
    let id: UUID
    let onboardingCompleted: Bool
    let isFounder: Bool
    let displayName: String
    let email: String
}

struct MaterialTopic: Codable, Identifiable, Equatable {
    let id: UUID
    let position: Int
    let sectionTitle: String
    var topic: String
    var answerAnchor: String
    let sourceExcerpt: String
    var status: String
    var issue: String

    var isClean: Bool { status == "clean" }
}

struct MaterialImport: Codable, Identifiable, Equatable {
    let id: UUID
    let title: String
    let kind: String
    let version: Int
    let status: String
    let importPath: String
    let intent: String
    let originalFilename: String
    let characterCount: Int
    let cleanCount: Int
    let attentionCount: Int
    let error: String
    let planDraftId: UUID?
    let comparison: [String: Int]
    var topics: [MaterialTopic]
    let createdAt: Date
    let updatedAt: Date

    var cleanTopicIDs: Set<UUID> { Set(topics.filter(\.isClean).map(\.id)) }
}

struct MaterialImportRequest: Encodable {
    let title: String
    let sourceText: String
    let originalFilename: String
    let mimeType: String
    let importPath: String
    let intent: String
    let requestedWeeks: Int
    let weeklyCapacityMinutes: Int
    let mode: String
    let deadline: String?
    let previousVersionId: UUID?
}

struct MaterialConfirmation: Codable, Equatable {
    let sourceId: UUID
    let createdCardIds: [UUID]
}

struct ManualTopic: Codable, Equatable {
    var topic: String
    var answerAnchor: String
}

struct MaterialCollection: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let version: String
    let topicCount: Int
    let available: Bool
}

struct MaterialCollectionDetail: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let version: String
    let topicCount: Int
    let available: Bool
    let sections: [String]
    let sourceNote: String
    let topics: [ManualTopic]
}
