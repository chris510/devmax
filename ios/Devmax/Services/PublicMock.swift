import Foundation

extension MockAPI {
    private static var publicCardID: UUID {
        UUID(uuidString: "00000000-0000-0000-0000-0000000000c1")!
    }

    private static var sourceID: UUID {
        UUID(uuidString: "00000000-0000-0000-0000-000000000901")!
    }

    private static var topicID: UUID {
        UUID(uuidString: "00000000-0000-0000-0000-000000000902")!
    }

    func accountProfile() async throws -> AccountProfile {
        let route = await MainActor.run { DebugFlags.shared.route }
        let isPublicRoute = await MainActor.run {
            PublicOnboardingState.handlesDebugRoute(route)
        }
        return AccountProfile(
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            // Private screenshot routes still need the authenticated app shell.
            // Only routes owned by PublicOnboardingState should force setup.
            onboardingCompleted: !isPublicRoute && !route.hasPrefix("public-"),
            isFounder: route == "returning",
            displayName: "Casey", email: "casey@privaterelay.appleid.com"
        )
    }

    func completeOnboarding() async throws -> AccountProfile {
        AccountProfile(
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            onboardingCompleted: true, isFounder: false, displayName: "Casey",
            email: "casey@privaterelay.appleid.com"
        )
    }

    func materialImports() async throws -> [MaterialImport] { [try await materialImport(Self.sourceID)] }

    func materialImport(_ id: UUID) async throws -> MaterialImport {
        MaterialImport(
            id: id, title: "Contracts — formation", kind: "guide", version: 1,
            status: "ready", importPath: "topics", intent: "already_studied",
            originalFilename: "contracts.md", characterCount: 1284, cleanCount: 3,
            attentionCount: 0, error: "", planDraftId: nil,
            comparison: ["added": 2, "changed": 1, "removed": 0, "unchanged": 3],
            topics: [
                MaterialTopic(
                    id: Self.topicID, position: 1, sectionTitle: "Formation", topic: "Offer",
                    answerAnchor: "An offer is an objective manifestation of willingness to bargain, with definite terms and an invitation to accept.",
                    sourceExcerpt: "An offer requires definite terms and intent to be bound.",
                    status: "clean", issue: ""
                ),
                MaterialTopic(
                    id: UUID(uuidString: "00000000-0000-0000-0000-000000000903")!,
                    position: 2, sectionTitle: "Formation", topic: "Acceptance",
                    answerAnchor: "Acceptance is assent to the offer's terms in the manner invited by the offer.",
                    sourceExcerpt: "Acceptance must mirror the terms and be communicated.",
                    status: "clean", issue: ""
                ),
            ], createdAt: Date(), updatedAt: Date()
        )
    }

    func startMaterialImport(_ request: MaterialImportRequest) async throws -> MaterialImport {
        try await materialImport(Self.sourceID)
    }

    func retryMaterialImport(_ id: UUID) async throws -> MaterialImport { try await materialImport(id) }
    func deleteMaterialImport(_ id: UUID) async throws {}

    func editMaterialTopic(
        _ id: UUID, topic: String?, answerAnchor: String?, action: String,
        mergeInto: UUID?
    ) async throws -> MaterialTopic {
        let imported = try await materialImport(Self.sourceID)
        var value = imported.topics.first { $0.id == id } ?? imported.topics[0]
        if let topic { value.topic = topic }
        if let answerAnchor { value.answerAnchor = answerAnchor }
        value.status = action == "exclude" ? "excluded" : "clean"
        return value
    }

    func confirmMaterial(_ id: UUID, topics: [UUID]) async throws -> MaterialConfirmation {
        MaterialConfirmation(sourceId: id, createdCardIds: [Self.publicCardID])
    }

    func createManualMaterial(
        title: String, topics: [ManualTopic]
    ) async throws -> MaterialConfirmation {
        MaterialConfirmation(sourceId: Self.sourceID, createdCardIds: [Self.publicCardID])
    }

    func materialCollections() async throws -> [MaterialCollection] {
        [
            MaterialCollection(
                id: "system-design-foundations", title: "System design foundations",
                subtitle: "Core mechanisms and design decisions for interviews.",
                version: "1.0", topicCount: 6, available: true
            )
        ]
    }

    func materialCollection(_ id: String) async throws -> MaterialCollectionDetail {
        MaterialCollectionDetail(
            id: id, title: "System design foundations",
            subtitle: "Core mechanisms and design decisions for interviews.", version: "1.0",
            topicCount: 6, available: true,
            sections: ["Request and data foundations", "Concrete technologies", "Patterns"],
            sourceNote: "Reviewed against the Devmax system-design curriculum.",
            topics: [ManualTopic(topic: "Consistent hashing", answerAnchor: "Keys and nodes share a hash ring; virtual nodes balance ownership and limit movement.")]
        )
    }

    func addMaterialCollection(_ id: String) async throws -> MaterialConfirmation {
        MaterialConfirmation(sourceId: Self.sourceID, createdCardIds: [Self.publicCardID])
    }

    func savedPlanPreview(_ id: UUID) async throws -> PlanPreview { try await retryPreview(draftID: id) }
    func exportAccount() async throws -> Data { Data("{\"account\":{}}".utf8) }
    func deleteAccount() async throws {}
    func logout() async throws {}
}
