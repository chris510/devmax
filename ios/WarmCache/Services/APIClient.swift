import Foundation

enum APIError: Error {
    case unauthorized
    /// Scoring failed server-side; nothing was written, so retrying the same
    /// payload is safe. This is what drives the inline submit-failure strip.
    case scoringUnavailable
    case transport(Error)
    case status(Int)
}

protocol WarmCacheAPI {
    func due() async throws -> [DueCard]
    func cards(sort: String, mode: String) async throws -> [CardSummary]
    func card(_ id: UUID) async throws -> CardDetail
    func createCard(topic: String, schedule: String) async throws -> CardSummary
    func startSession(cardID: UUID) async throws -> SessionStart
    func saveDraft(sessionID: UUID, text: String) async throws
    func submitAnswer(sessionID: UUID, text: String) async throws -> AnswerOutcome
    func settings() async throws -> AppSettings
    func updateSettings(_ settings: AppSettings) async throws -> AppSettings
    func registerDeviceToken(_ token: String) async throws
}

struct LiveAPI: WarmCacheAPI {
    var baseURL: URL
    var apiKey: String
    var session: URLSession = .shared

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        d.dateDecodingStrategy = .iso8601
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

    func startSession(cardID: UUID) async throws -> SessionStart {
        let data = try await request("POST", "cards/\(cardID)/sessions")
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
}

/// Where the app points. Local dev uses port 8083 per the ~/dev port contract.
enum APIConfig {
    static var client: WarmCacheAPI {
        if DebugFlags.shared.useMockAPI { return MockAPI.shared }
        return LiveAPI(
            baseURL: URL(string: Bundle.main.object(forInfoDictionaryKey: "WCBaseURL") as? String
                ?? "http://localhost:8083")!,
            apiKey: Bundle.main.object(forInfoDictionaryKey: "WCAPIKey") as? String ?? "dev-api-key"
        )
    }
}
