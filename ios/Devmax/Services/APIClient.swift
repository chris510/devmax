import Foundation

enum APIError: Error {
    case unauthorized
    case pilotUpgradeRequired(minimumBuild: Int?)
    case pilotSourceNotAssigned
    /// Scoring failed server-side; nothing was written, so retrying the same
    /// payload is safe. This is what drives the inline submit-failure strip.
    case scoringUnavailable
    case transport(Error)
    case status(Int)
}

extension Notification.Name {
    static let aiConsentRequired = Notification.Name("devmax.ai-consent-required")
}

private struct APIErrorEnvelope: Decodable {
    struct Detail: Decodable {
        let code: String?
        let minimumClientBuild: Int?
    }
    let detail: Detail?
}

extension Error {
    /// The mono note under a failure that had nothing to retry — which half broke.
    ///
    /// Lives on the error rather than on a screen because it describes `APIError`,
    /// not any one caller: this app has a single user who is also its operator, and
    /// a 503 (Claude unreachable, so the card cannot be scored either) is a
    /// different afternoon from a dropped connection. Exhaustive on purpose — a new
    /// case should fail the build here rather than quietly report itself offline.
    var loadNote: String {
        guard let apiError = self as? APIError else { return "SERVER UNREACHABLE" }
        switch apiError {
        case .scoringUnavailable: return "QUESTION GENERATION UNAVAILABLE"
        case .unauthorized: return "API KEY REJECTED"
        case .pilotUpgradeRequired: return "PILOT BUILD UPDATE REQUIRED"
        case .pilotSourceNotAssigned: return "PILOT SOURCE NOT ASSIGNED"
        case .status(let code): return "SERVER ERROR \(code)"
        case .transport: return "SERVER UNREACHABLE"
        }
    }
}

/// Timestamps as the backend actually sends them.
///
/// `JSONDecoder.DateDecodingStrategy.iso8601` uses `.withInternetDateTime` alone,
/// which rejects fractional seconds — and the backend emits them. `started_at` is a
/// Postgres `timestamptz` that pydantic serializes as `2026-07-26T23:02:09.722946Z`,
/// so every `GET /cards/{id}` threw. `CardHistoryScreen` swallows that with `try?`,
/// which is why all three Card History states rendered blank against a real server
/// while working fine on `MockAPI`.
///
/// Two formatters because `ISO8601DateFormatter` can't make fractional seconds
/// optional. Both are `static let`, so each is built once, not per decode.
///
/// `DevmaxTests/Fixtures/card_detail.json` is a response captured from the running
/// app and pins this.
enum WireDate {
    private static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parse(_ text: String) -> Date? {
        fractional.date(from: text) ?? plain.date(from: text)
    }

    static func decode(_ decoder: Decoder) throws -> Date {
        let text = try decoder.singleValueContainer().decode(String.self)
        guard let date = parse(text) else {
            throw DecodingError.dataCorrupted(
                .init(
                    codingPath: decoder.codingPath,
                    debugDescription: "unparseable timestamp: \(text)"
                )
            )
        }
        return date
    }
}

enum APIClientBuild {
    /// `CFBundleVersion` is the server's rollout boundary. Keep this centralized
    /// so a newly added pilot endpoint cannot accidentally omit the build gate.
    static var current: Int {
        let value = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion")
        if let number = value as? NSNumber { return number.intValue }
        return Int(value as? String ?? "") ?? 0
    }
}

protocol DevmaxAPI {
    func due() async throws -> [DueCard]
    func cards(sort: String, mode: String) async throws -> [CardSummary]
    func card(_ id: UUID) async throws -> CardDetail
    func learnCard(_ id: UUID) async throws -> LearningCard
    func captures() async throws -> [CaptureSummary]
    func capture(_ id: UUID) async throws -> PendingCapture
    func createCapture(topic: String, context: String) async throws -> PendingCapture
    func updateCapture(_ id: UUID, update: CaptureUpdateRequest) async throws -> PendingCapture
    func prepareCaptureQuestion(_ id: UUID, regenerate: Bool) async throws -> PendingCapture
    func activateCapture(_ id: UUID, schedule: String) async throws -> CardSummary
    func discardCapture(_ id: UUID) async throws
    func cardMaintenance(_ id: UUID) async throws -> CardMaintenance
    func archiveCard(_ id: UUID) async throws -> CardMaintenance
    func restoreCard(_ id: UUID) async throws -> CardMaintenance
    func replaceCard(_ id: UUID, question: String, schedule: String) async throws -> CardSummary
    /// `practice` marks a Review Sprint run: scored and written to the card's
    /// history exactly like a normal session, with SM-2 left untouched.
    func startSession(cardID: UUID, practice: Bool) async throws -> SessionStart
    func saveDraft(sessionID: UUID, text: String, turnIndex: Int) async throws
    func submitAnswer(
        sessionID: UUID, text: String, turnIndex: Int
    ) async throws -> AnswerOutcome
    /// Turn 3, after the session is already complete and scored. Returns nothing:
    /// the server rewrites the card's mastery summary and there is no second score,
    /// so the client has nothing to display and nothing to store.
    func submitReattempt(sessionID: UUID, text: String) async throws
    /// Optional V2 practice after a passing Recall result. The response is
    /// qualitative only and never changes the session score or schedule.
    func submitCoaching(sessionID: UUID, text: String) async throws -> CoachingOutcome
    func settings() async throws -> AppSettings
    func updateSettings(_ settings: AppSettings) async throws -> AppSettings
    func registerDeviceToken(_ token: String) async throws

    // MARK: Public account and study material
    func accountProfile() async throws -> AccountProfile
    func completeOnboarding() async throws -> AccountProfile
    func updateAIConsent(action: String) async throws -> AIConsentReceipt
    func materialImports() async throws -> [MaterialImport]
    func materialImport(_ id: UUID) async throws -> MaterialImport
    func startMaterialImport(_ request: MaterialImportRequest) async throws -> MaterialImport
    func retryMaterialImport(_ id: UUID) async throws -> MaterialImport
    func deleteMaterialImport(_ id: UUID) async throws
    func editMaterialTopic(
        _ id: UUID, topic: String?, answerAnchor: String?, action: String,
        mergeInto: UUID?
    ) async throws -> MaterialTopic
    func confirmMaterial(
        _ id: UUID, topics: [UUID], contentProvenance: String?
    ) async throws -> MaterialConfirmation
    func lessonPilotPreview(_ id: UUID) async throws -> MaterialLessonPreview
    func markLessonReviewOpened(_ id: UUID) async throws -> MaterialLessonPreview
    func excludePilotLessonProposal(_ id: UUID) async throws -> MaterialTopicPreview
    func startFormationCheck(proposalID: UUID) async throws -> LessonCheck
    func saveLessonCheckDraft(checkID: UUID, text: String) async throws -> LessonCheck
    func submitFormationCheck(checkID: UUID, text: String) async throws -> MaterialTopicAuthority
    func startLessonRestudy(proposalID: UUID) async throws -> MaterialTopicAuthority
    func startTransferCheck(proposalID: UUID) async throws -> LessonCheck
    func lessonCheck(_ id: UUID) async throws -> LessonCheck
    func submitTransferCheck(checkID: UUID, text: String) async throws -> LessonCheck
    func reopenLessonAuthority(checkID: UUID) async throws -> MaterialTopicAuthority
    func lessonTransferDebrief(checkID: UUID) async throws -> MaterialTopicAuthority
    func lessonProgress(_ id: UUID) async throws -> LessonProgress
    func distillLesson(_ id: UUID) async throws -> MaterialArtifacts
    func materialArtifacts(_ id: UUID) async throws -> MaterialArtifacts
    func createManualMaterial(title: String, topics: [ManualTopic]) async throws -> MaterialConfirmation
    func materialCollections() async throws -> [MaterialCollection]
    func materialCollection(_ id: String) async throws -> MaterialCollectionDetail
    func addMaterialCollection(_ id: String) async throws -> MaterialConfirmation
    func savedPlanPreview(_ id: UUID) async throws -> PlanPreview
    func exportAccount() async throws -> Data
    func deleteAccount() async throws
    func logout() async throws

    // MARK: Study Plan
    //
    // `activePlan()` is the only one Today calls, and it is deliberately the
    // cheapest: Today loads it alongside `due()` and a failure here must never
    // stop a due card from appearing.
    func activePlan() async throws -> StudyPlanSummary
    func plans() async throws -> PlanList
    func planOverview(_ id: UUID) async throws -> PlanOverview
    func planWeek(_ id: UUID, index: Int) async throws -> WeekDetail
    func planItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail
    func editPlanItem(_ id: UUID, itemID: UUID, edit: PlanItemEdit) async throws -> PlanItemDetail
    func completePlanItem(
        _ id: UUID, itemID: UUID, revision: Int?
    ) async throws -> PlanItemDetail
    func savePracticeDebriefDraft(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief
    func submitPracticeDebrief(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief
    func previewReopen(_ id: UUID, itemID: UUID) async throws -> PlanProposal
    func reopenPlanItem(_ id: UUID, itemID: UUID, revision: Int) async throws -> PlanItemDetail
    func previewReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal
    func applyReplan(_ id: UUID, request: ReplanRequest) async throws -> PlanProposal
    func updateWeekCapacity(
        _ id: UUID, index: Int, minutes: Int?, revision: Int
    ) async throws -> PlanProposal
    func pausePlan(_ id: UUID) async throws -> PlanOverview
    func previewResume(_ id: UUID) async throws -> PlanProposal
    func applyResume(_ id: UUID, revision: Int) async throws -> PlanOverview
    func activatePlan(_ id: UUID, revision: Int) async throws -> PlanOverview
    func completePlan(_ id: UUID) async throws -> PlanOverview
    func archivePlan(_ id: UUID) async throws -> PlanOverview
    func duplicatePlan(_ id: UUID) async throws -> PlanOverview
    func planRevisions(_ id: UUID) async throws -> [PlanRevisionEntry]
    func planRecap(_ id: UUID) async throws -> PlanRecap
    func previewGuide(_ request: GuidePreviewRequest) async throws -> PlanPreview
    func retryPreview(draftID: UUID) async throws -> PlanPreview
    func editPreview(draftID: UUID, edit: PreviewEdit) async throws -> PlanPreview
    func createPlan(draftID: UUID, activate: Bool) async throws -> PlanOverview
    func createCardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList
    func cardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList
    func acceptCardProposals(
        _ id: UUID, selected: [UUID], idempotencyKey: String, revision: Int,
        edits: [String: [String: String]]
    ) async throws -> CardAcceptResult
    func resolveDuplicate(_ id: UUID, proposalID: UUID, action: String) async throws
}

/// Existing focused test doubles only model the review loop. Public launch
/// methods default to a clear unsupported error so those doubles stay narrow;
/// LiveAPI and MockAPI implement every method used by the app.
extension DevmaxAPI {
    func learnCard(_ id: UUID) async throws -> LearningCard { throw APIError.status(501) }
    func submitCoaching(sessionID: UUID, text: String) async throws -> CoachingOutcome {
        throw APIError.status(501)
    }
    func accountProfile() async throws -> AccountProfile { throw APIError.status(501) }
    func completeOnboarding() async throws -> AccountProfile { throw APIError.status(501) }
    func updateAIConsent(action: String) async throws -> AIConsentReceipt {
        throw APIError.status(501)
    }
    func materialImports() async throws -> [MaterialImport] { throw APIError.status(501) }
    func materialImport(_ id: UUID) async throws -> MaterialImport { throw APIError.status(501) }
    func startMaterialImport(_ request: MaterialImportRequest) async throws -> MaterialImport {
        throw APIError.status(501)
    }
    func retryMaterialImport(_ id: UUID) async throws -> MaterialImport { throw APIError.status(501) }
    func deleteMaterialImport(_ id: UUID) async throws { throw APIError.status(501) }
    func editMaterialTopic(
        _ id: UUID, topic: String?, answerAnchor: String?, action: String,
        mergeInto: UUID?
    ) async throws -> MaterialTopic { throw APIError.status(501) }
    func confirmMaterial(
        _ id: UUID, topics: [UUID], contentProvenance: String?
    ) async throws -> MaterialConfirmation {
        throw APIError.status(501)
    }
    func lessonPilotPreview(_ id: UUID) async throws -> MaterialLessonPreview {
        throw APIError.status(501)
    }
    func markLessonReviewOpened(_ id: UUID) async throws -> MaterialLessonPreview {
        throw APIError.status(501)
    }
    func excludePilotLessonProposal(_ id: UUID) async throws -> MaterialTopicPreview {
        throw APIError.status(501)
    }
    func startFormationCheck(proposalID: UUID) async throws -> LessonCheck {
        throw APIError.status(501)
    }
    func saveLessonCheckDraft(checkID: UUID, text: String) async throws -> LessonCheck {
        throw APIError.status(501)
    }
    func submitFormationCheck(
        checkID: UUID, text: String
    ) async throws -> MaterialTopicAuthority { throw APIError.status(501) }
    func startLessonRestudy(proposalID: UUID) async throws -> MaterialTopicAuthority {
        throw APIError.status(501)
    }
    func startTransferCheck(proposalID: UUID) async throws -> LessonCheck {
        throw APIError.status(501)
    }
    func lessonCheck(_ id: UUID) async throws -> LessonCheck { throw APIError.status(501) }
    func submitTransferCheck(checkID: UUID, text: String) async throws -> LessonCheck {
        throw APIError.status(501)
    }
    func reopenLessonAuthority(checkID: UUID) async throws -> MaterialTopicAuthority {
        throw APIError.status(501)
    }
    func lessonTransferDebrief(checkID: UUID) async throws -> MaterialTopicAuthority {
        throw APIError.status(501)
    }
    func lessonProgress(_ id: UUID) async throws -> LessonProgress { throw APIError.status(501) }
    func distillLesson(_ id: UUID) async throws -> MaterialArtifacts { throw APIError.status(501) }
    func materialArtifacts(_ id: UUID) async throws -> MaterialArtifacts {
        throw APIError.status(501)
    }
    func createManualMaterial(
        title: String, topics: [ManualTopic]
    ) async throws -> MaterialConfirmation { throw APIError.status(501) }
    func materialCollections() async throws -> [MaterialCollection] { throw APIError.status(501) }
    func materialCollection(_ id: String) async throws -> MaterialCollectionDetail {
        throw APIError.status(501)
    }
    func addMaterialCollection(_ id: String) async throws -> MaterialConfirmation {
        throw APIError.status(501)
    }
    func savedPlanPreview(_ id: UUID) async throws -> PlanPreview { throw APIError.status(501) }
    func exportAccount() async throws -> Data { throw APIError.status(501) }
    func deleteAccount() async throws { throw APIError.status(501) }
    func logout() async throws { throw APIError.status(501) }
}

/// Request bodies. Encoded with `.convertToSnakeCase`, so the field names here
/// are the camelCase spelling of the wire format and nothing restates them.
struct GuidePreviewRequest: Encodable {
    var guideText: String
    var requestedWeeks: Int
    var weeklyCapacityMinutes: Int
    var mode: String
    var deadline: String?
    var subjectHint: String = ""
    var titleHint: String = ""
}

struct PreviewEdit: Encodable {
    var estimatesReviewed: [String]?
    var omissionsAcknowledged: Bool?
    var retrievalApproved: [String]?
    var retrievalRejected: [String]?
    var dependenciesConfirmed: [String]?
    var itemEstimates: [String: Int] = [:]
    var overviewTitles: [String: String] = [:]
}

struct PlanItemEdit: Encodable {
    var fullTitle: String?
    var whyItMatters: String?
    var doneWhen: String?
    var estimateMinutes: Int?
    var notes: String?
    var overviewTitle: String?
    var studyBlockLabel: String?
    var studyBlockWeekday: Int?
    var studyBlockMinuteOfDay: Int?
    var studyBlockReminderOn: Bool?
}

/// Every replan is described by its *inputs*, never by a placement. The server
/// recomputes the proposal from these against the current revision, so a stale
/// client can never write a schedule it computed itself.
struct ReplanRequest: Encodable {
    var basePlanRevision: Int
    var capacityOverrides: [String: Int?] = [:]
    var defaultCapacityMinutes: Int?
    var deferredItemIds: [UUID] = []
    var extraWeeks: Int = 0
    var insertAfterPhase: Int?
    var confirmedCoreRemovals: [UUID] = []
}

struct LiveAPI: DevmaxAPI {
    var baseURL: URL
    var apiKey: String
    var session: URLSession = .shared
    var tokenStore: AuthTokenStore = .shared
    var clientBuild: Int = APIClientBuild.current

    // Not private: DevmaxTests decodes captured server responses through this
    // exact decoder, which is the only wire-format check available without a server.
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom { try WireDate.decode($0) }
        return d
    }()

    static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    private func announceAIConsentIfNeeded(_ data: Data) async {
        let envelope = try? Self.decoder.decode(APIErrorEnvelope.self, from: data)
        guard envelope?.detail?.code == "ai_consent_required" else { return }
        await MainActor.run {
            NotificationCenter.default.post(name: .aiConsentRequired, object: nil)
        }
    }

    private func request(
        _ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil,
        mayRefresh: Bool = true
    ) async throws -> Data {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }

        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        let accessToken = await tokenStore.accessToken()
        if let accessToken {
            req.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        } else if !apiKey.isEmpty {
            // Founder migration only. New accounts never receive this value.
            req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        }
        req.setValue(String(clientBuild), forHTTPHeaderField: "X-Devmax-Client-Build")
        if let body {
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }

        let (data, response): (Data, URLResponse)
        do {
            (data, response) = try await session.data(for: req)
        } catch {
            throw APIError.transport(error)
        }

        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        let envelope = try? Self.decoder.decode(APIErrorEnvelope.self, from: data)
        if envelope?.detail?.code == "pilot_upgrade_required" {
            throw APIError.pilotUpgradeRequired(
                minimumBuild: envelope?.detail?.minimumClientBuild
            )
        }
        if code == 404, envelope?.detail?.code == "pilot_source_not_assigned" {
            throw APIError.pilotSourceNotAssigned
        }
        switch code {
        case 200..<300: return data
        case 401 where mayRefresh && accessToken != nil:
            do {
                _ = try await tokenStore.refresh { refreshToken in
                    try await AuthClient(
                        baseURL: baseURL, session: session, store: tokenStore
                    ).refresh(refreshToken)
                }
                return try await request(
                    method, path, query: query, body: body, mayRefresh: false
                )
            } catch {
                await tokenStore.clear()
                throw APIError.unauthorized
            }
        case 401: throw APIError.unauthorized
        case 403:
            await announceAIConsentIfNeeded(data)
            throw APIError.status(code)
        case 503: throw APIError.scoringUnavailable
        default: throw APIError.status(code)
        }
    }

    func due() async throws -> [DueCard] {
        try Self.decoder.decode([DueCard].self, from: await request("GET", "cards/due"))
    }

    func cards(sort: String = "next_review", mode: String = "all") async throws -> [CardSummary] {
        let data = try await request(
            "GET", "cards",
            query: [URLQueryItem(name: "sort", value: sort), URLQueryItem(name: "mode", value: mode)]
        )
        return try Self.decoder.decode([CardSummary].self, from: data)
    }

    func card(_ id: UUID) async throws -> CardDetail {
        try Self.decoder.decode(CardDetail.self, from: await request("GET", "cards/\(id)"))
    }

    func learnCard(_ id: UUID) async throws -> LearningCard {
        try await post("cards/\(id)/learning", LearningCard.self)
    }

    func captures() async throws -> [CaptureSummary] {
        try await get("captures", [CaptureSummary].self)
    }

    func capture(_ id: UUID) async throws -> PendingCapture {
        try await get("captures/\(id)", PendingCapture.self)
    }

    func createCapture(topic: String, context: String) async throws -> PendingCapture {
        return try await post("captures", PendingCapture.self, body: CaptureCreateRequest(
            topic: topic, context: context
        ))
    }

    func updateCapture(
        _ id: UUID, update: CaptureUpdateRequest
    ) async throws -> PendingCapture {
        let body = try Self.encoder.encode(update)
        return try Self.decoder.decode(
            PendingCapture.self, from: await request("PATCH", "captures/\(id)", body: body)
        )
    }

    func prepareCaptureQuestion(
        _ id: UUID, regenerate: Bool = false
    ) async throws -> PendingCapture {
        try await post(
            "captures/\(id)/question", PendingCapture.self,
            query: [URLQueryItem(name: "regenerate", value: regenerate ? "true" : "false")]
        )
    }

    func activateCapture(_ id: UUID, schedule: String) async throws -> CardSummary {
        try await post(
            "captures/\(id)/activate", CardSummary.self, body: ["schedule": schedule]
        )
    }

    func discardCapture(_ id: UUID) async throws {
        _ = try await request("DELETE", "captures/\(id)")
    }

    func cardMaintenance(_ id: UUID) async throws -> CardMaintenance {
        try await get("cards/\(id)/maintenance", CardMaintenance.self)
    }

    func archiveCard(_ id: UUID) async throws -> CardMaintenance {
        try await post("cards/\(id)/archive", CardMaintenance.self)
    }

    func restoreCard(_ id: UUID) async throws -> CardMaintenance {
        try await post("cards/\(id)/restore", CardMaintenance.self)
    }

    func replaceCard(
        _ id: UUID, question: String, schedule: String
    ) async throws -> CardSummary {
        struct Body: Encodable {
            let canonicalQuestion: String
            let schedule: String
        }
        return try await post(
            "cards/\(id)/replace", CardSummary.self,
            body: Body(canonicalQuestion: question, schedule: schedule)
        )
    }

    func startSession(cardID: UUID, practice: Bool = false) async throws -> SessionStart {
        let data = try await request(
            "POST", "cards/\(cardID)/sessions",
            query: practice ? [URLQueryItem(name: "practice", value: "true")] : []
        )
        return try Self.decoder.decode(SessionStart.self, from: data)
    }

    func saveDraft(sessionID: UUID, text: String, turnIndex: Int) async throws {
        struct Body: Encodable { let draftText: String; let turnIndex: Int }
        let body = try Self.encoder.encode(Body(draftText: text, turnIndex: turnIndex))
        _ = try await request("PATCH", "sessions/\(sessionID)/draft", body: body)
    }

    func submitAnswer(
        sessionID: UUID, text: String, turnIndex: Int
    ) async throws -> AnswerOutcome {
        struct Body: Encodable { let text: String; let turnIndex: Int }
        let body = try Self.encoder.encode(Body(text: text, turnIndex: turnIndex))
        let data = try await request("POST", "sessions/\(sessionID)/answers", body: body)
        return try Self.decoder.decode(AnswerOutcome.self, from: data)
    }

    func submitReattempt(sessionID: UUID, text: String) async throws {
        let body = try Self.encoder.encode(["text": text])
        _ = try await request("POST", "sessions/\(sessionID)/reattempt", body: body)
    }

    func submitCoaching(sessionID: UUID, text: String) async throws -> CoachingOutcome {
        let body = try Self.encoder.encode(["text": text])
        let data = try await request("POST", "sessions/\(sessionID)/coaching", body: body)
        return try Self.decoder.decode(CoachingOutcome.self, from: data)
    }

    func settings() async throws -> AppSettings {
        try Self.decoder.decode(AppSettings.self, from: await request("GET", "settings"))
    }

    func updateSettings(_ settings: AppSettings) async throws -> AppSettings {
        let body = try Self.encoder.encode(settings)
        return try Self.decoder.decode(AppSettings.self, from: await request("PUT", "settings", body: body))
    }

    func registerDeviceToken(_ token: String) async throws {
        let body = try Self.encoder.encode(["token": token, "kind": "apns"])
        _ = try await request("POST", "device-tokens", body: body)
    }

    func accountProfile() async throws -> AccountProfile {
        try await get("auth/me", AccountProfile.self)
    }

    func updateAIConsent(action: String) async throws -> AIConsentReceipt {
        let body = try Self.encoder.encode([
            "action": action,
            "policy_version": AIProcessingDisclosure.policyVersion
        ])
        let data = try await request("PUT", "auth/ai-consent", body: body)
        return try Self.decoder.decode(AIConsentReceipt.self, from: data)
    }

    func completeOnboarding() async throws -> AccountProfile {
        try await post("auth/onboarding/complete", AccountProfile.self)
    }

    func materialImports() async throws -> [MaterialImport] {
        try await get("materials/imports", [MaterialImport].self)
    }

    func materialImport(_ id: UUID) async throws -> MaterialImport {
        try await get("materials/imports/\(id)", MaterialImport.self)
    }

    func startMaterialImport(_ body: MaterialImportRequest) async throws -> MaterialImport {
        try await post("materials/imports", MaterialImport.self, body: body)
    }

    func retryMaterialImport(_ id: UUID) async throws -> MaterialImport {
        try await post("materials/imports/\(id)/retry", MaterialImport.self)
    }

    func deleteMaterialImport(_ id: UUID) async throws {
        _ = try await request("DELETE", "materials/imports/\(id)")
    }

    func editMaterialTopic(
        _ id: UUID, topic: String?, answerAnchor: String?, action: String,
        mergeInto: UUID?
    ) async throws -> MaterialTopic {
        struct Body: Encodable {
            let topic: String?
            let answerAnchor: String?
            let action: String
            let mergeIntoId: UUID?
        }
        let data = try await request(
            "PATCH", "materials/topics/\(id)",
            body: Self.encoder.encode(
                Body(
                    topic: topic, answerAnchor: answerAnchor, action: action,
                    mergeIntoId: mergeInto
                )
            )
        )
        return try Self.decoder.decode(MaterialTopic.self, from: data)
    }

    func confirmMaterial(
        _ id: UUID, topics: [UUID], contentProvenance: String?
    ) async throws -> MaterialConfirmation {
        struct Body: Encodable {
            let selectedTopicIds: [UUID]
            let contentProvenance: String?
        }
        return try await post(
            "materials/imports/\(id)/confirm", MaterialConfirmation.self,
            body: Body(
                selectedTopicIds: topics,
                contentProvenance: contentProvenance
            )
        )
    }

    func lessonPilotPreview(_ id: UUID) async throws -> MaterialLessonPreview {
        try await get("materials/imports/\(id)/preview", MaterialLessonPreview.self)
    }

    func markLessonReviewOpened(_ id: UUID) async throws -> MaterialLessonPreview {
        try await post(
            "materials/imports/\(id)/review-opened", MaterialLessonPreview.self
        )
    }

    func excludePilotLessonProposal(_ id: UUID) async throws -> MaterialTopicPreview {
        struct Body: Encodable { let action = "exclude" }
        let data = try await request(
            "PATCH", "materials/topics/\(id)", body: Self.encoder.encode(Body())
        )
        return try Self.decoder.decode(MaterialTopicPreview.self, from: data)
    }

    func startFormationCheck(proposalID: UUID) async throws -> LessonCheck {
        try await post(
            "materials/topics/\(proposalID)/formation-check", LessonCheck.self
        )
    }

    func saveLessonCheckDraft(checkID: UUID, text: String) async throws -> LessonCheck {
        struct Body: Encodable { let draftText: String }
        let data = try await request(
            "PATCH", "materials/lesson-checks/\(checkID)/draft",
            body: Self.encoder.encode(Body(draftText: text))
        )
        return try Self.decoder.decode(LessonCheck.self, from: data)
    }

    func submitFormationCheck(
        checkID: UUID, text: String
    ) async throws -> MaterialTopicAuthority {
        struct Body: Encodable { let answerText: String }
        return try await post(
            "materials/lesson-checks/\(checkID)/submit", MaterialTopicAuthority.self,
            body: Body(answerText: text)
        )
    }

    func startLessonRestudy(proposalID: UUID) async throws -> MaterialTopicAuthority {
        try await post(
            "materials/topics/\(proposalID)/restudy", MaterialTopicAuthority.self
        )
    }

    func startTransferCheck(proposalID: UUID) async throws -> LessonCheck {
        try await post(
            "materials/topics/\(proposalID)/transfer-check", LessonCheck.self
        )
    }

    func lessonCheck(_ id: UUID) async throws -> LessonCheck {
        try await get("materials/lesson-checks/\(id)", LessonCheck.self)
    }

    func submitTransferCheck(checkID: UUID, text: String) async throws -> LessonCheck {
        struct Body: Encodable { let answerText: String }
        return try await post(
            "materials/lesson-checks/\(checkID)/submit", LessonCheck.self,
            body: Body(answerText: text)
        )
    }

    func reopenLessonAuthority(checkID: UUID) async throws -> MaterialTopicAuthority {
        try await post(
            "materials/lesson-checks/\(checkID)/authority", MaterialTopicAuthority.self
        )
    }

    func lessonTransferDebrief(checkID: UUID) async throws -> MaterialTopicAuthority {
        try await post(
            "materials/lesson-checks/\(checkID)/transfer-debrief",
            MaterialTopicAuthority.self
        )
    }

    func lessonProgress(_ id: UUID) async throws -> LessonProgress {
        try await get("materials/imports/\(id)/progress", LessonProgress.self)
    }

    func distillLesson(_ id: UUID) async throws -> MaterialArtifacts {
        try await post("materials/imports/\(id)/distill", MaterialArtifacts.self)
    }

    func materialArtifacts(_ id: UUID) async throws -> MaterialArtifacts {
        try await get("materials/imports/\(id)/artifacts", MaterialArtifacts.self)
    }

    func createManualMaterial(
        title: String, topics: [ManualTopic]
    ) async throws -> MaterialConfirmation {
        struct Body: Encodable { let title: String; let topics: [ManualTopic] }
        return try await post(
            "materials/manual", MaterialConfirmation.self, body: Body(title: title, topics: topics)
        )
    }

    func materialCollections() async throws -> [MaterialCollection] {
        try await get("materials/collections", [MaterialCollection].self)
    }

    func materialCollection(_ id: String) async throws -> MaterialCollectionDetail {
        try await get("materials/collections/\(id)", MaterialCollectionDetail.self)
    }

    func addMaterialCollection(_ id: String) async throws -> MaterialConfirmation {
        try await post("materials/collections/\(id)", MaterialConfirmation.self)
    }

    func savedPlanPreview(_ id: UUID) async throws -> PlanPreview {
        try await get("study-plans/preview/\(id)", PlanPreview.self)
    }

    func exportAccount() async throws -> Data { try await request("GET", "auth/export") }

    func deleteAccount() async throws { _ = try await request("DELETE", "auth/account") }

    func logout() async throws { _ = try await request("POST", "auth/logout") }

    // MARK: Study Plan

    private func get<T: Decodable>(_ path: String, _ type: T.Type) async throws -> T {
        try Self.decoder.decode(T.self, from: await request("GET", path))
    }

    private func post<T: Decodable>(
        _ path: String, _ type: T.Type, body: Encodable? = nil, query: [URLQueryItem] = []
    ) async throws -> T {
        let data = try await request(
            "POST", path, query: query, body: body.map { try? Self.encoder.encode($0) } ?? nil
        )
        return try Self.decoder.decode(T.self, from: data)
    }

    func activePlan() async throws -> StudyPlanSummary {
        try await get("study-plans/active/summary", StudyPlanSummary.self)
    }

    func plans() async throws -> PlanList { try await get("study-plans", PlanList.self) }

    func planOverview(_ id: UUID) async throws -> PlanOverview {
        try await get("study-plans/\(id)", PlanOverview.self)
    }

    func planWeek(_ id: UUID, index: Int) async throws -> WeekDetail {
        try await get("study-plans/\(id)/weeks/\(index)", WeekDetail.self)
    }

    func planItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
        try await get("study-plans/\(id)/items/\(itemID)", PlanItemDetail.self)
    }

    func editPlanItem(
        _ id: UUID, itemID: UUID, edit: PlanItemEdit
    ) async throws -> PlanItemDetail {
        let data = try await request(
            "PATCH", "study-plans/\(id)/items/\(itemID)", body: Self.encoder.encode(edit)
        )
        return try Self.decoder.decode(PlanItemDetail.self, from: data)
    }

    func completePlanItem(
        _ id: UUID, itemID: UUID, revision: Int?
    ) async throws -> PlanItemDetail {
        let query = revision.map {
            [URLQueryItem(name: "base_plan_revision", value: String($0))]
        } ?? []
        return try await post(
            "study-plans/\(id)/items/\(itemID)/complete", PlanItemDetail.self,
            query: query
        )
    }

    func previewReopen(_ id: UUID, itemID: UUID) async throws -> PlanProposal {
        try await post("study-plans/\(id)/items/\(itemID)/reopen/preview", PlanProposal.self)
    }

    func reopenPlanItem(
        _ id: UUID, itemID: UUID, revision: Int
    ) async throws -> PlanItemDetail {
        try await post(
            "study-plans/\(id)/items/\(itemID)/reopen", PlanItemDetail.self,
            query: [URLQueryItem(name: "base_plan_revision", value: String(revision))]
        )
    }

    func previewReplan(_ id: UUID, request body: ReplanRequest) async throws -> PlanProposal {
        try await post("study-plans/\(id)/replans/preview", PlanProposal.self, body: body)
    }

    func applyReplan(_ id: UUID, request body: ReplanRequest) async throws -> PlanProposal {
        try await post("study-plans/\(id)/replans/apply", PlanProposal.self, body: body)
    }

    func updateWeekCapacity(
        _ id: UUID, index: Int, minutes: Int?, revision: Int
    ) async throws -> PlanProposal {
        struct Body: Encodable {
            let weekIndex: Int
            let overrideCapacityMinutes: Int?
            let basePlanRevision: Int
        }
        let body = try Self.encoder.encode(
            Body(weekIndex: index, overrideCapacityMinutes: minutes, basePlanRevision: revision)
        )
        let data = try await request(
            "PATCH", "study-plans/\(id)/weeks/\(index)/capacity", body: body
        )
        return try Self.decoder.decode(PlanProposal.self, from: data)
    }

    func pausePlan(_ id: UUID) async throws -> PlanOverview {
        try await post("study-plans/\(id)/pause", PlanOverview.self)
    }

    func previewResume(_ id: UUID) async throws -> PlanProposal {
        try await post("study-plans/\(id)/resume/preview", PlanProposal.self)
    }

    func applyResume(_ id: UUID, revision: Int) async throws -> PlanOverview {
        try await post(
            "study-plans/\(id)/resume/apply", PlanOverview.self,
            query: [URLQueryItem(name: "base_plan_revision", value: String(revision))]
        )
    }

    func activatePlan(_ id: UUID, revision: Int) async throws -> PlanOverview {
        try await post(
            "study-plans/\(id)/activate", PlanOverview.self,
            query: [URLQueryItem(name: "base_plan_revision", value: String(revision))]
        )
    }

    func completePlan(_ id: UUID) async throws -> PlanOverview {
        try await post("study-plans/\(id)/complete", PlanOverview.self)
    }

    func archivePlan(_ id: UUID) async throws -> PlanOverview {
        try await post("study-plans/\(id)/archive", PlanOverview.self)
    }

    func duplicatePlan(_ id: UUID) async throws -> PlanOverview {
        try await post("study-plans/\(id)/duplicate", PlanOverview.self)
    }

    func planRevisions(_ id: UUID) async throws -> [PlanRevisionEntry] {
        try await get("study-plans/\(id)/revisions", [PlanRevisionEntry].self)
    }

    func planRecap(_ id: UUID) async throws -> PlanRecap {
        try await get("study-plans/\(id)/recap", PlanRecap.self)
    }

    func previewGuide(_ body: GuidePreviewRequest) async throws -> PlanPreview {
        try await post("study-plans/preview", PlanPreview.self, body: body)
    }

    func retryPreview(draftID: UUID) async throws -> PlanPreview {
        try await post("study-plans/preview/\(draftID)/retry", PlanPreview.self)
    }

    func editPreview(draftID: UUID, edit: PreviewEdit) async throws -> PlanPreview {
        let data = try await request(
            "PATCH", "study-plans/preview/\(draftID)", body: Self.encoder.encode(edit)
        )
        return try Self.decoder.decode(PlanPreview.self, from: data)
    }

    func createPlan(draftID: UUID, activate: Bool) async throws -> PlanOverview {
        struct Body: Encodable { let draftId: UUID; let activate: Bool }
        return try await post(
            "study-plans", PlanOverview.self, body: Body(draftId: draftID, activate: activate)
        )
    }

    func savePracticeDebriefDraft(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief {
        struct Body: Encodable { let text: String }
        let data = try await request(
            "PATCH", "study-plans/\(id)/items/\(itemID)/practice-debrief/draft",
            body: Self.encoder.encode(Body(text: text))
        )
        return try Self.decoder.decode(PracticeDebrief.self, from: data)
    }

    func submitPracticeDebrief(
        _ id: UUID, itemID: UUID, text: String
    ) async throws -> PracticeDebrief {
        struct Body: Encodable { let text: String }
        return try await post(
            "study-plans/\(id)/items/\(itemID)/practice-debrief",
            PracticeDebrief.self, body: Body(text: text)
        )
    }

    func createCardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList {
        try await post(
            "study-plans/\(id)/items/\(itemID)/card-proposals", CardProposalList.self
        )
    }

    func cardProposals(_ id: UUID, itemID: UUID) async throws -> CardProposalList {
        try await get("study-plans/\(id)/items/\(itemID)/card-proposals", CardProposalList.self)
    }

    func acceptCardProposals(
        _ id: UUID, selected: [UUID], idempotencyKey: String, revision: Int,
        edits: [String: [String: String]]
    ) async throws -> CardAcceptResult {
        struct Body: Encodable {
            let selectedProposalIds: [UUID]
            let idempotencyKey: String
            let proposalRevision: Int
            let edits: [String: [String: String]]
        }
        return try await post(
            "study-plans/\(id)/card-proposals/accept", CardAcceptResult.self,
            body: Body(
                selectedProposalIds: selected, idempotencyKey: idempotencyKey,
                proposalRevision: revision, edits: edits
            )
        )
    }

    func resolveDuplicate(_ id: UUID, proposalID: UUID, action: String) async throws {
        struct Body: Encodable { let proposalId: UUID; let action: String }
        _ = try await request(
            "POST", "study-plans/\(id)/card-proposals/resolve-duplicate",
            body: Self.encoder.encode(Body(proposalId: proposalID, action: action))
        )
    }
}

/// Where the app points.
///
/// The endpoint comes from the per-configuration xcconfigs in `ios/Config` via
/// Info.plist substitution. Public API calls use opaque bearer credentials from
/// `AuthTokenStore`. The separate founder claim token is only an availability
/// signal here; it must never be attached by `LiveAPI` as general authentication.
///
/// There is deliberately no fallback API key. A default here can only mask a
/// misconfigured build, and the value it used to fall back to is published in this
/// repo. Missing config produces a clean 401 instead of a mystery.
enum APIConfig {
    static let defaultBaseURL = URL(string: "http://localhost:8083")!

    static func info(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String,
              !value.trimmingCharacters(in: .whitespaces).isEmpty
        else { return nil }
        return value
    }

    static var client: DevmaxAPI {
        if DebugFlags.shared.useMockAPI { return MockAPI.shared }
        return LiveAPI(
            baseURL: info("WCBaseURL").flatMap(URL.init(string:)) ?? defaultBaseURL,
            apiKey: info("WCAPIKey") ?? "",
            tokenStore: .shared
        )
    }

    static var authClient: AuthClient {
        AuthClient(
            baseURL: info("WCBaseURL").flatMap(URL.init(string:)) ?? defaultBaseURL,
            store: .shared
        )
    }

    static var hasLegacyKey: Bool { info("WCAPIKey") != nil }
    static var founderClaimToken: String? { info("WCFounderClaimToken") }
    static var hasFounderClaimToken: Bool { founderClaimToken != nil }
}
