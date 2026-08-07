import Foundation

enum APIError: Error {
    case unauthorized
    /// Scoring failed server-side; nothing was written, so retrying the same
    /// payload is safe. This is what drives the inline submit-failure strip.
    case scoringUnavailable
    case transport(Error)
    case status(Int)
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

protocol DevmaxAPI {
    func due() async throws -> [DueCard]
    func cards(sort: String, mode: String) async throws -> [CardSummary]
    func card(_ id: UUID) async throws -> CardDetail
    func createCard(topic: String, schedule: String) async throws -> CardSummary
    /// `practice` marks a Review Sprint run: scored and written to the card's
    /// history exactly like a normal session, with SM-2 left untouched.
    func startSession(cardID: UUID, practice: Bool) async throws -> SessionStart
    func saveDraft(sessionID: UUID, text: String) async throws
    func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome
    /// Turn 3, after the session is already complete and scored. Returns nothing:
    /// the server rewrites the card's mastery summary and there is no second score,
    /// so the client has nothing to display and nothing to store.
    func submitReattempt(sessionID: UUID, text: String) async throws
    func settings() async throws -> AppSettings
    func updateSettings(_ settings: AppSettings) async throws -> AppSettings
    func registerDeviceToken(_ token: String) async throws

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
    func completePlanItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail
    func practiceDebrief(_ id: UUID, itemID: UUID) async throws -> PracticeDebrief?
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

    // Not private: DevmaxTests decodes captured server responses through this
    // exact decoder, which is the only wire-format check available without a server.
    static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .custom(WireDate.decode)
        return d
    }()

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.keyEncodingStrategy = .convertToSnakeCase
        return e
    }()

    private func request(
        _ method: String, _ path: String, query: [URLQueryItem] = [], body: Data? = nil
    ) async throws -> Data {
        var components = URLComponents(url: baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
        if !query.isEmpty { components.queryItems = query }

        var req = URLRequest(url: components.url!)
        req.httpMethod = method
        req.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
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
        switch code {
        case 200..<300: return data
        case 401: throw APIError.unauthorized
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

    func createCard(topic: String, schedule: String) async throws -> CardSummary {
        let body = try Self.encoder.encode(["topic": topic, "schedule": schedule])
        return try Self.decoder.decode(CardSummary.self, from: await request("POST", "cards", body: body))
    }

    func startSession(cardID: UUID, practice: Bool = false) async throws -> SessionStart {
        let data = try await request(
            "POST", "cards/\(cardID)/sessions",
            query: practice ? [URLQueryItem(name: "practice", value: "true")] : []
        )
        return try Self.decoder.decode(SessionStart.self, from: data)
    }

    func saveDraft(sessionID: UUID, text: String) async throws {
        let body = try Self.encoder.encode(["draft_text": text])
        _ = try await request("PATCH", "sessions/\(sessionID)/draft", body: body)
    }

    func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome {
        let body = try Self.encoder.encode(["text": text])
        let data = try await request("POST", "sessions/\(sessionID)/answers", body: body)
        return try Self.decoder.decode(AnswerOutcome.self, from: data)
    }

    func submitReattempt(sessionID: UUID, text: String) async throws {
        let body = try Self.encoder.encode(["text": text])
        _ = try await request("POST", "sessions/\(sessionID)/reattempt", body: body)
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

    func completePlanItem(_ id: UUID, itemID: UUID) async throws -> PlanItemDetail {
        try await post("study-plans/\(id)/items/\(itemID)/complete", PlanItemDetail.self)
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

    func practiceDebrief(_ id: UUID, itemID: UUID) async throws -> PracticeDebrief? {
        try await get(
            "study-plans/\(id)/items/\(itemID)/practice-debrief",
            Optional<PracticeDebrief>.self
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
/// Both values come from the per-configuration xcconfigs in `ios/Config` via
/// Info.plist substitution: Debug points at localhost:8083 (the ~/dev port
/// contract), Release at the Fly deployment. `WC_API_KEY` lives in the gitignored
/// `Config/Secrets.xcconfig` — see `Config/Secrets.example.xcconfig`.
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
            apiKey: info("WCAPIKey") ?? ""
        )
    }
}
