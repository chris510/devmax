import Foundation

extension MockAPI {
    private static var publicCardID: UUID {
        UUID(uuidString: "00000000-0000-0000-0000-0000000000c1")!
    }

    private static var secondPublicCardID: UUID {
        UUID(uuidString: "00000000-0000-0000-0000-0000000000c2")!
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
        let consentPending = route == "ai-consent"
        return AccountProfile(
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            // Private screenshot routes still need the authenticated app shell.
            // Only routes owned by PublicOnboardingState should force setup.
            onboardingCompleted: !isPublicRoute && !route.hasPrefix("public-"),
            isFounder: route == "returning",
            displayName: "Casey", email: "casey@privaterelay.appleid.com",
            appleUserIdentifier: "fixture-apple-user",
            aiConsentStatus: consentPending ? "pending" : "granted",
            aiConsentVersion: consentPending ? "" : AIProcessingDisclosure.policyVersion,
            aiProcessingAllowed: !consentPending,
            aiConsentPromptRequired: consentPending
        )
    }

    func completeOnboarding() async throws -> AccountProfile {
        AccountProfile(
            id: UUID(uuidString: "00000000-0000-0000-0000-000000000001")!,
            onboardingCompleted: true, isFounder: false, displayName: "Casey",
            email: "casey@privaterelay.appleid.com", appleUserIdentifier: "fixture-apple-user",
            aiConsentStatus: "granted",
            aiConsentVersion: AIProcessingDisclosure.policyVersion,
            aiProcessingAllowed: true, aiConsentPromptRequired: false
        )
    }

    func updateAIConsent(action: String) async throws -> AIConsentReceipt {
        let status = if action == "grant" {
            "granted"
        } else if action == "decline" {
            "declined"
        } else {
            "withdrawn"
        }
        return AIConsentReceipt(
            provider: "Anthropic and OpenAI",
            policyVersion: AIProcessingDisclosure.policyVersion,
            status: status,
            updatedAt: Date(), processingAllowed: action == "grant", promptRequired: false
        )
    }

    func materialImports() async throws -> [MaterialImport] { [try await materialImport(Self.sourceID)] }

    func materialImport(_ id: UUID) async throws -> MaterialImport {
        let route = await MainActor.run { DebugFlags.shared.route }
        let status = ["extracting", "import-background"].contains(route)
            ? "processing"
            : "ready"
        return MaterialImport(
            id: id, title: "Contracts — formation", kind: "guide", version: 1,
            status: status, importPath: "topics", intent: "already_studied",
            originalFilename: "contracts.md", characterCount: 1284, cleanCount: 3,
            attentionCount: 0, error: "", planDraftId: nil,
            comparison: ["added": 2, "changed": 1, "removed": 0, "unchanged": 3],
            topics: [
                MaterialTopic(
                    id: Self.topicID, position: 1, sectionTitle: "Formation", topic: "Offer",
                    answerAnchor: "An offer is an objective manifestation of willingness to bargain, with definite terms and an invitation to accept.",
                    sourceExcerpt: "An offer requires definite terms and intent to be bound.",
                    canonicalQuestion: "How does consistent hashing limit key movement when membership changes?",
                    answerRubric: Self.lessonRubric,
                    recallQuestions: Self.lessonPrompts(for: "consistent hashing"),
                    status: "clean", issue: ""
                ),
                MaterialTopic(
                    id: UUID(uuidString: "00000000-0000-0000-0000-000000000903")!,
                    position: 2, sectionTitle: "Formation", topic: "Acceptance",
                    answerAnchor: "Acceptance is assent to the offer's terms in the manner invited by the offer.",
                    sourceExcerpt: "Acceptance must mirror the terms and be communicated.",
                    canonicalQuestion: "How does Raft elect a leader without losing log safety?",
                    answerRubric: Self.lessonRubric,
                    recallQuestions: Self.lessonPrompts(for: "Raft leader election"),
                    status: "clean", issue: ""
                )
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
        let cards = topics.map { topic in
            topic == Self.topicID ? Self.publicCardID : Self.secondPublicCardID
        }
        return MaterialConfirmation(
            sourceId: id,
            createdCardIds: cards
        )
    }

    func lessonProgress(_ id: UUID) async throws -> LessonProgress {
        LessonProgress(
            sourceId: id, title: "Contracts — formation", conceptCount: 2,
            reviewedCount: 2, weakCount: 1, complete: true, nextCardId: nil,
            concepts: [
                LessonConceptProgress(
                    proposalId: Self.topicID, cardId: Self.publicCardID,
                    concept: "Consistent hashing",
                    masterySummary: "solid on ring ownership; virtual-node trade-offs need review",
                    lastScore: 3, recallScore: 3, scoreKind: "recall",
                    scoringContractVersion: 2, lastReviewedAt: Date(),
                    nextReviewAt: "2026-08-17", intervalDays: 3
                ),
                LessonConceptProgress(
                    proposalId: UUID(uuidString: "00000000-0000-0000-0000-000000000903")!,
                    cardId: Self.secondPublicCardID, concept: "Raft leader election",
                    masterySummary: "can explain terms and voting; log safety is still thin",
                    lastScore: 2, recallScore: 2, scoreKind: "recall",
                    scoringContractVersion: 2, lastReviewedAt: Date(),
                    nextReviewAt: "2026-08-15", intervalDays: 1
                )
            ]
        )
    }

    func distillLesson(_ id: UUID) async throws -> MaterialArtifacts {
        try await materialArtifacts(id)
    }

    func materialArtifacts(_ id: UUID) async throws -> MaterialArtifacts {
        MaterialArtifacts(
            sourceId: id, title: "Contracts — formation",
            sourceUrl: "https://example.com/lesson", distilledAt: Date(),
            canonicalNoteMarkdown: "# Contracts — formation\n\nA concise canonical note.",
            recallExportMarkdown: "# Recall questions\n\n- Explain the mechanism.",
            concepts: [],
            writebackBundle: Self.lessonWritebackBundle(sourceID: id)
        )
    }

    private static func lessonWritebackBundle(sourceID: UUID) -> LearningWritebackBundle {
        let proposalID = "00000000-0000-0000-0000-000000000902"
        let rubric = lessonRubric
        return LearningWritebackBundle(
            schema: "second-brain.learning-writeback", schemaVersion: 1,
            producer: "devmax", exportId: "sha256:mock-writeback-export",
            source: LearningWritebackSource(
                id: "devmax:source:\(sourceID)",
                lineageId: "devmax:source-lineage:00000000-0000-0000-0000-000000000901",
                version: 1, title: "Contracts — formation",
                url: "https://example.com/lesson", distilledAt: "2026-08-14T22:45:00Z"
            ),
            concepts: [
                LearningWritebackConcept(
                    id: "devmax:proposal:\(proposalID)",
                    cardId: "devmax:card:\(publicCardID)", title: "Contract formation",
                    answerRubric: rubric,
                    mentalModel: "Offer and acceptance create mutual assent.",
                    howItWorks: rubric["mechanism"]!,
                    gotchas: ["Do not treat every negotiation as acceptance."],
                    recallCandidates: lessonPrompts(for: "contract formation").map { prompt in
                        LearningWritebackCandidate(
                            id: "devmax:probe:\(proposalID):\(prompt.level)",
                            type: prompt.level, prompt: prompt.question,
                            answerRubric: rubric["mechanism"]!
                        )
                    },
                    quizEvidence: [
                        LearningWritebackEvidence(
                            id: "devmax:session:00000000-0000-0000-0000-000000000905",
                            reviewedAt: "2026-08-14T22:42:00Z",
                            prompt: "How is a contract formed?", score: 4,
                            gradedSummary: "Recalled mutual assent and consideration.",
                            scoringContractVersion: 2, scoredFollowUpUsed: true
                        )
                    ],
                    producerAssessment: "established"
                )
            ]
        )
    }

    private static var lessonRubric: [String: String] {
        [
            "mechanism": "Explain the causal mechanism.",
            "acceptable_alternative": "An equivalent accurate account is acceptable.",
            "trade_off": "Name the operational cost.",
            "failure_mode": "Describe where the mechanism breaks.",
            "misconception": "Do not confuse bounded movement with no movement."
        ]
    }

    private static func lessonPrompts(for concept: String) -> [LessonRecallPrompt] {
        [
            .init(level: "definition_recognition", question: "What is \(concept)?"),
            .init(level: "mechanism", question: "How does \(concept) work?"),
            .init(level: "derivation", question: "Why does \(concept) have that behavior?"),
            .init(level: "application", question: "Where would you apply \(concept)?"),
            .init(
                level: "failure_tradeoff",
                question: "Where does \(concept) fail, and what does it cost?"
            )
        ]
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
