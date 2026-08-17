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
        if materialImportDelay != .zero {
            try await Task.sleep(for: materialImportDelay)
        }
        if let materialImportFixture { return materialImportFixture }
        let route = await MainActor.run { DebugFlags.shared.route }
        let status = ["extracting", "import-background"].contains(route)
            ? "processing"
            : "ready"
        let isLesson = ["lesson-concepts", "lesson-concept-expanded"].contains(route)
            || route.hasPrefix("lesson-pilot-")
        return MaterialImport(
            id: id,
            title: isLesson ? "Networking 101" : "Contracts: formation",
            kind: isLesson ? "article" : "guide", version: 1,
            status: status, importPath: isLesson ? "lesson" : "topics",
            intent: "already_studied",
            originalFilename: isLesson ? "" : "contracts.md",
            characterCount: isLesson ? 876 : 1284, cleanCount: isLesson ? 2 : 3,
            attentionCount: 0, error: "", planDraftId: nil,
            comparison: ["added": 2, "changed": 1, "removed": 0, "unchanged": 3],
            topics: [
                MaterialTopic(
                    id: Self.topicID, position: 1,
                    sectionTitle: isLesson ? "Networking 101" : "Formation",
                    topic: isLesson ? "Network layer best-effort delivery" : "Offer",
                    answerAnchor: isLesson
                        ? "IP routes packets between networks with best-effort delivery; "
                            + "packets may be lost, reordered, or duplicated."
                        : "An offer is an objective manifestation of willingness to bargain, "
                            + "with definite terms and an invitation to accept.",
                    sourceExcerpt: isLesson
                        ? "IP addresses and routes packets between networks using best-effort "
                            + "delivery, so packets may be lost, reordered, or duplicated."
                        : "An offer requires definite terms and intent to be bound.",
                    canonicalQuestion: isLesson
                        ? "How does best-effort IP delivery shape the transport layer above it?"
                        : "How does consistent hashing limit key movement when membership changes?",
                    answerRubric: Self.lessonRubric,
                    recallQuestions: Self.lessonPrompts(
                        for: isLesson ? "network layer best-effort delivery" : "consistent hashing"
                    ),
                    status: "clean", issue: ""
                ),
                MaterialTopic(
                    id: UUID(uuidString: "00000000-0000-0000-0000-000000000903")!,
                    position: 2,
                    sectionTitle: isLesson ? "Networking 101" : "Formation",
                    topic: isLesson ? "Persistent TCP connection trade-offs" : "Acceptance",
                    answerAnchor: isLesson
                        ? "Connection reuse avoids repeated TCP setup latency, while each open "
                            + "connection consumes sockets, memory, buffers, and server state."
                        : "Acceptance is assent to the offer's terms in the manner invited by "
                            + "the offer.",
                    sourceExcerpt: isLesson
                        ? "Reusing persistent connections avoids repeated setup, but each open "
                            + "connection consumes sockets, memory, buffers, and server state."
                        : "Acceptance must mirror the terms and be communicated.",
                    canonicalQuestion: isLesson
                        ? "Why does TCP connection reuse reduce latency, and what does it cost?"
                        : "How does Raft elect a leader without losing log safety?",
                    answerRubric: Self.lessonRubric,
                    recallQuestions: Self.lessonPrompts(
                        for: isLesson ? "persistent TCP connections" : "Raft leader election"
                    ),
                    status: "clean", issue: ""
                )
            ], createdAt: Date(), updatedAt: Date()
        )
    }

    func startMaterialImport(_ request: MaterialImportRequest) async throws -> MaterialImport {
        try await materialImport(Self.sourceID)
    }

    func retryMaterialImport(_ id: UUID) async throws -> MaterialImport {
        if let retryMaterialImportFixture { return retryMaterialImportFixture }
        return try await materialImport(id)
    }
    func deleteMaterialImport(_ id: UUID) async throws {
        if materialDeletionFails { throw APIError.status(500) }
    }

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

    func confirmMaterial(
        _ id: UUID, topics: [UUID], contentProvenance: String?
    ) async throws -> MaterialConfirmation {
        if confirmMaterialDelay != .zero {
            try await Task.sleep(for: confirmMaterialDelay)
        }
        pilotConfirmationAttempts += 1
        confirmedMaterialSelections.append(topics)
        let route = await MainActor.run { DebugFlags.shared.route }
        if (pilotConfirmationFailsOnce || route == "lesson-pilot-confirm-failure"),
           pilotConfirmationAttempts == 1 {
            throw APIError.status(500)
        }
        let cards = topics.map { topic in
            topic == Self.topicID ? Self.publicCardID : Self.secondPublicCardID
        }
        return MaterialConfirmation(
            sourceId: id,
            createdCardIds: cards
        )
    }

    func lessonPilotPreview(_ id: UUID) async throws -> MaterialLessonPreview {
        let route = await MainActor.run { DebugFlags.shared.route }
        let restudy = route == "lesson-pilot-restudy"
        let transferRoute = route.hasPrefix("lesson-pilot-transfer")
        let formationState = transferRoute ? "unavailable" : "not_started"
        let transferKey = "transfer:\(Self.topicID.uuidString)"
        let storedTransfer = pilotLessonCheckByProposal[transferKey]
            .flatMap { pilotLessonChecks[$0] }
        let transferState: String
        if storedTransfer?.status == .submitted {
            transferState = "submitted"
        } else if storedTransfer?.status == .exposed {
            transferState = "debriefed"
        } else {
            transferState = transferRoute ? "available" : "unavailable"
        }
        return MaterialLessonPreview(
            id: id, title: "Networking 101", kind: "article",
            sourceUrl: "https://example.com/networking",
            contentProvenance: LessonContentProvenance.exactSourceExcerpt.rawValue,
            status: "ready", importPath: "lesson", intent: "already_studied",
            cleanCount: 1, attentionCount: 0, error: "",
            lessonGroundingRequired: false,
            proposalsReadyAt: WireDate.parse("2026-08-15T16:02:00Z"),
            reviewOpenedAt: nil, confirmedAt: nil,
            topics: [
                MaterialTopicPreview(
                    id: Self.topicID, position: 1, sectionTitle: "Network layer",
                    topic: "Network layer best-effort delivery",
                    formationQuestion: restudy
                        ? nil
                        : "How does best-effort IP delivery shape the transport layer above it?",
                    status: "clean", issue: "", formationState: formationState,
                    transferState: transferState
                )
            ]
        )
    }

    func markLessonReviewOpened(_ id: UUID) async throws -> MaterialLessonPreview {
        if pilotSourceNotAssigned { throw APIError.pilotSourceNotAssigned }
        var value = try await lessonPilotPreview(id)
        value = MaterialLessonPreview(
            id: value.id, title: value.title, kind: value.kind,
            sourceUrl: value.sourceUrl, contentProvenance: value.contentProvenance,
            status: value.status, importPath: value.importPath, intent: value.intent,
            cleanCount: value.cleanCount, attentionCount: value.attentionCount,
            error: value.error, lessonGroundingRequired: value.lessonGroundingRequired,
            proposalsReadyAt: value.proposalsReadyAt, reviewOpenedAt: Date(),
            confirmedAt: value.confirmedAt, topics: value.topics
        )
        return value
    }

    func excludePilotLessonProposal(_ id: UUID) async throws -> MaterialTopicPreview {
        MaterialTopicPreview(
            id: id, position: 1, sectionTitle: "Network layer",
            topic: "Network layer best-effort delivery", formationQuestion: nil,
            status: "excluded", issue: "", formationState: "unavailable",
            transferState: "unavailable"
        )
    }

    func startFormationCheck(proposalID: UUID) async throws -> LessonCheck {
        let key = "formation:\(proposalID.uuidString)"
        if let checkID = pilotLessonCheckByProposal[key],
           let existing = pilotLessonChecks[checkID] {
            return existing
        }
        let check = Self.pilotCheck(
            proposalID: proposalID, kind: .formation, condition: .attemptFirst,
            promptLevel: "canonical",
            prompt: "How does best-effort IP delivery shape the transport layer above it?"
        )
        pilotLessonChecks[check.id] = check
        pilotLessonCheckByProposal[key] = check.id
        return check
    }

    func saveLessonCheckDraft(checkID: UUID, text: String) async throws -> LessonCheck {
        guard let existing = pilotLessonChecks[checkID] else { throw APIError.status(404) }
        let saved = Self.replacingPilotCheck(existing, draftText: text)
        pilotLessonChecks[checkID] = saved
        return saved
    }

    func submitFormationCheck(
        checkID: UUID, text: String
    ) async throws -> MaterialTopicAuthority {
        guard let existing = pilotLessonChecks[checkID], existing.kind == .formation
        else { throw APIError.status(404) }
        pilotLessonSubmitAttempts += 1
        let route = await MainActor.run { DebugFlags.shared.route }
        if (pilotFormationFailsOnce || route == "lesson-pilot-provider-failure"),
           pilotLessonSubmitAttempts == 1 {
            pilotLessonChecks[checkID] = Self.replacingPilotCheck(
                existing, draftText: text
            )
            throw APIError.scoringUnavailable
        }
        let outcome: LessonCheckOutcome = route == "lesson-pilot-correction"
            ? .missingMechanism
            : .accurateAccount
        let exposed = Self.replacingPilotCheck(
            existing, status: .exposed, draftText: "", outcome: outcome,
            hasFeedback: true, exposedAt: Date(), submittedAt: Date()
        )
        pilotLessonChecks[checkID] = exposed
        return Self.pilotAuthority(
            check: exposed,
            feedback: outcome == .missingMechanism
                ? "You named unreliability, but not the mechanism: IP makes no delivery, ordering, or deduplication guarantee, so transport must add the guarantees it needs."
                : "The mechanism is intact: transport adds only the guarantees the application needs above best-effort IP."
        )
    }

    func startLessonRestudy(proposalID: UUID) async throws -> MaterialTopicAuthority {
        let key = "formation:\(proposalID.uuidString)"
        if let checkID = pilotLessonCheckByProposal[key],
           let existing = pilotLessonChecks[checkID], existing.status == .exposed {
            return Self.pilotAuthority(
                check: existing,
                feedback: "Study the grounded account, then reconstruct it after the hold."
            )
        }
        let opened = Self.pilotCheck(
            proposalID: proposalID, kind: .formation, condition: .restudy,
            promptLevel: "canonical", prompt: ""
        )
        let exposed = Self.replacingPilotCheck(
            opened, status: .exposed, hasFeedback: true,
            exposedAt: Date(), submittedAt: Date()
        )
        pilotLessonChecks[exposed.id] = exposed
        pilotLessonCheckByProposal[key] = exposed.id
        return Self.pilotAuthority(
            check: exposed,
            feedback: "Study the grounded account, then reconstruct it after the hold."
        )
    }

    func startTransferCheck(proposalID: UUID) async throws -> LessonCheck {
        let key = "transfer:\(proposalID.uuidString)"
        if let checkID = pilotLessonCheckByProposal[key],
           let existing = pilotLessonChecks[checkID] {
            return existing
        }
        let check = Self.pilotCheck(
            proposalID: proposalID, kind: .transfer, condition: nil,
            promptLevel: "failure_tradeoff",
            prompt: "A service needs ordered, duplicate-free delivery over IP. What must move above the network layer, and why?"
        )
        pilotLessonChecks[check.id] = check
        pilotLessonCheckByProposal[key] = check.id
        return check
    }

    func lessonCheck(_ id: UUID) async throws -> LessonCheck {
        guard let check = pilotLessonChecks[id] else { throw APIError.status(404) }
        return check
    }

    func submitTransferCheck(checkID: UUID, text: String) async throws -> LessonCheck {
        guard let existing = pilotLessonChecks[checkID], existing.kind == .transfer
        else { throw APIError.status(404) }
        pilotLessonSubmitAttempts += 1
        let route = await MainActor.run { DebugFlags.shared.route }
        if (pilotTransferFailsOnce || route == "lesson-pilot-transfer-failure"),
           pilotLessonSubmitAttempts == 1 {
            pilotLessonChecks[checkID] = Self.replacingPilotCheck(
                existing, draftText: text
            )
            throw APIError.status(500)
        }
        let submitted = Self.replacingPilotCheck(
            existing, status: .submitted, draftText: "", submittedAt: Date()
        )
        pilotLessonChecks[checkID] = submitted
        return submitted
    }

    func reopenLessonAuthority(checkID: UUID) async throws -> MaterialTopicAuthority {
        guard let existing = pilotLessonChecks[checkID], existing.status == .exposed
        else { throw APIError.status(409) }
        let restamped = Self.replacingPilotCheck(
            existing, exposedAt: Date(), submittedAt: existing.submittedAt
        )
        pilotLessonChecks[checkID] = restamped
        return Self.pilotAuthority(
            check: restamped,
            feedback: existing.condition == .restudy
                ? "Study the grounded account, then reconstruct it after the hold."
                : "The highest-value correction remains grounded in the source below."
        )
    }

    func lessonTransferDebrief(checkID: UUID) async throws -> MaterialTopicAuthority {
        guard let existing = pilotLessonChecks[checkID],
              existing.kind == .transfer, existing.status == .submitted
        else { throw APIError.status(409) }
        let exposed = Self.replacingPilotCheck(
            existing, status: .exposed, hasFeedback: true, exposedAt: Date(),
            submittedAt: existing.submittedAt
        )
        pilotLessonChecks[checkID] = exposed
        return Self.pilotAuthority(
            check: exposed,
            feedback: "Reliability belongs above best-effort IP: sequencing, acknowledgements, retransmission, and duplicate handling provide the required contract."
        )
    }

    private static func pilotCheck(
        proposalID: UUID, kind: LessonCheckKind, condition: LessonCheckCondition?,
        promptLevel: String, prompt: String
    ) -> LessonCheck {
        let now = Date()
        return LessonCheck(
            id: UUID(), proposalId: proposalID, cardId: nil, kind: kind,
            condition: condition, promptLevel: promptLevel,
            promptVersion: kind == .formation ? "formation-v1" : "transfer-v1",
            promptText: prompt, status: .open, draftText: "",
            qualitativeOutcome: nil, hasFeedback: false, exposedAt: nil,
            recallNotBeforeAt: nil,
            availableAt: kind == .transfer ? now : nil,
            startedAt: now, submittedAt: nil, updatedAt: now
        )
    }

    private static func replacingPilotCheck(
        _ value: LessonCheck,
        status: LessonCheckStatus? = nil,
        draftText: String? = nil,
        outcome: LessonCheckOutcome? = nil,
        hasFeedback: Bool? = nil,
        exposedAt: Date? = nil,
        submittedAt: Date? = nil
    ) -> LessonCheck {
        let exposure = exposedAt ?? value.exposedAt
        return LessonCheck(
            id: value.id, proposalId: value.proposalId, cardId: value.cardId,
            kind: value.kind, condition: value.condition,
            promptLevel: value.promptLevel, promptVersion: value.promptVersion,
            promptText: value.promptText, status: status ?? value.status,
            draftText: draftText ?? value.draftText,
            qualitativeOutcome: outcome ?? value.qualitativeOutcome,
            hasFeedback: hasFeedback ?? value.hasFeedback,
            exposedAt: exposure,
            recallNotBeforeAt: exposure.map { Calendar.current.date(byAdding: .day, value: 1, to: $0)! },
            availableAt: value.availableAt, startedAt: value.startedAt,
            submittedAt: submittedAt ?? value.submittedAt, updatedAt: Date()
        )
    }

    private static func pilotAuthority(
        check: LessonCheck, feedback: String
    ) -> MaterialTopicAuthority {
        let exposedAt = check.exposedAt ?? Date()
        let recallAt = check.recallNotBeforeAt
            ?? Calendar.current.date(byAdding: .day, value: 1, to: exposedAt)!
        return MaterialTopicAuthority(
            check: check, proposalId: check.proposalId,
            topic: "Network layer best-effort delivery", sectionTitle: "Network layer",
            sourceTitle: "Networking 101", sourceUrl: "https://example.com/networking",
            contentProvenance: LessonContentProvenance.exactSourceExcerpt.rawValue,
            sourceExcerpt: "IP routes packets between networks using best-effort delivery, so packets may be lost, reordered, or duplicated.",
            answerBasis: "IP supplies addressing and routing without delivery, ordering, or deduplication guarantees; transport adds the guarantees an application needs.",
            canonicalQuestion: "How does best-effort IP delivery shape the transport layer above it?",
            answerRubric: lessonRubric,
            recallQuestions: lessonPrompts(for: "network layer best-effort delivery"),
            feedback: feedback, exposedAt: exposedAt,
            recallNotBeforeAt: recallAt,
            confirmationTitle: "Approve this grounded concept?",
            confirmationMessage: "Approval creates a held Recall card. Formation is not a score."
        )
    }

    func lessonProgress(_ id: UUID) async throws -> LessonProgress {
        if lessonProgressDelay != .zero {
            try await Task.sleep(for: lessonProgressDelay)
        }
        return LessonProgress(
            sourceId: id, title: "Contracts: formation", conceptCount: 2,
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
        if lessonArtifactDelay != .zero {
            try await Task.sleep(for: lessonArtifactDelay)
        }
        return MaterialArtifacts(
            sourceId: id, title: "Contracts: formation",
            sourceUrl: "https://example.com/lesson",
            contentProvenance: "exact_source_excerpt", distilledAt: Date(),
            canonicalNoteMarkdown: "# Contracts: formation\n\nA concise canonical note.",
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
                version: 1, title: "Contracts: formation",
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
