import Foundation

enum AIProcessingDisclosure {
    static let policyVersion = "anthropic-openai-2026-08-13-v2"
}

enum PrivacyLinks {
    static let policy = URL(
        string: "https://devmax-recall.christrinh5.chatgpt.site/privacy"
    )!
    static let anthropicRetention = URL(
        string: "https://privacy.anthropic.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data"
    )!
    static let openAIDataControls = URL(
        string: "https://developers.openai.com/api/docs/guides/your-data#default-usage-policies-by-endpoint"
    )!
}

struct AccountProfile: Codable, Equatable {
    let id: UUID
    let onboardingCompleted: Bool
    let isFounder: Bool
    let displayName: String
    let email: String
    let appleUserIdentifier: String
    let aiConsentStatus: String
    let aiConsentVersion: String
    let aiConsentUpdatedAt: Date?
    let aiProcessingAllowed: Bool
    let aiConsentPromptRequired: Bool

    init(
        id: UUID, onboardingCompleted: Bool, isFounder: Bool,
        displayName: String, email: String, appleUserIdentifier: String = "",
        aiConsentStatus: String = "pending", aiConsentVersion: String = "",
        aiConsentUpdatedAt: Date? = nil, aiProcessingAllowed: Bool = false,
        aiConsentPromptRequired: Bool = true
    ) {
        self.id = id
        self.onboardingCompleted = onboardingCompleted
        self.isFounder = isFounder
        self.displayName = displayName
        self.email = email
        self.appleUserIdentifier = appleUserIdentifier
        self.aiConsentStatus = aiConsentStatus
        self.aiConsentVersion = aiConsentVersion
        self.aiConsentUpdatedAt = aiConsentUpdatedAt
        self.aiProcessingAllowed = aiProcessingAllowed
        self.aiConsentPromptRequired = aiConsentPromptRequired
    }
}

struct AIConsentReceipt: Codable, Equatable {
    let provider: String
    let policyVersion: String
    let status: String
    let updatedAt: Date
    let processingAllowed: Bool
    let promptRequired: Bool
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
