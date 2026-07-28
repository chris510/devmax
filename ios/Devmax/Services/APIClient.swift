import Foundation

enum APIError: Error {
    case unauthorized
    /// Scoring failed server-side; nothing was written, so retrying the same
    /// payload is safe. This is what drives the inline submit-failure strip.
    case scoringUnavailable
    case transport(Error)
    case status(Int)
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
