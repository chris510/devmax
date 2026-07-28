import Foundation

// Wire models mirror the backend's snake_case shapes exactly; the decoder is
// configured with `.convertFromSnakeCase`, so nothing here restates key names.

struct DueCard: Codable, Identifiable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let masterySummary: String
    let lastScore: Int?
    /// Computed server-side ("due today", "3 days overdue") — the client never
    /// reimplements date math.
    let dueLabel: String
    let resumable: Bool
    let missedCount: Int
}

struct CardSummary: Codable, Identifiable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let deliveryMode: String
    let masterySummary: String
    let lastScore: Int?
    /// The three axes behind `lastScore`. Coverage's rollup line is the only
    /// consumer — nothing else in the app decomposes a score.
    let lastMechanismAccuracy: Int?
    let lastTradeOffAwareness: Int?
    let lastFailureModeAwareness: Int?
    let easeFactor: Double
    let intervalDays: Int
    let repetitions: Int
    let nextReviewAt: String
    /// Both computed server-side, like `DueCard.dueLabel`.
    let dueLabel: String
    let daysSinceReview: Int?
    let missedCount: Int

    /// Review Sprint walks library cards through the same Conversation screen the
    /// daily queue uses, so the set is adapted rather than the screen generalised.
    /// `resumable` is not a library fact — only a live session with a saved draft
    /// makes a card resumable — so it is supplied by whoever builds the queue.
    func asQueueCard(resumable: Bool = false) -> DueCard {
        DueCard(
            id: id, topic: topic, category: category, masterySummary: masterySummary,
            lastScore: lastScore, dueLabel: dueLabel, resumable: resumable,
            missedCount: missedCount
        )
    }
}

struct Turn: Codable, Equatable, Identifiable {
    enum Role: String, Codable { case question, answer, followUp = "follow_up", score }

    var id: String { role.rawValue + text }
    let role: Role
    let text: String
}

struct SessionHistory: Codable, Identifiable, Equatable {
    let id: UUID
    let date: Date
    let score: Int?
    let feedback: String
    let turns: [Turn]
}

struct CardDetail: Codable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let masterySummary: String
    let lastScore: Int?
    let easeFactor: Double
    let intervalDays: Int
    let repetitions: Int
    let nextReviewAt: String
    let missedCount: Int
    let sessions: [SessionHistory]
}

struct SessionStart: Codable, Equatable {
    let sessionId: UUID
    let question: String
    let isFollowUp: Bool
    let draftText: String
    let resumed: Bool
}

/// The `POST /sessions/{id}/answers` response is a discriminated union — the
/// server owns the follow-up decision, the client only reacts to it.
enum AnswerOutcome: Equatable {
    case followUp(question: String)
    case complete(
        score: Int, feedback: String, nextReviewAt: String, intervalDays: Int, practice: Bool
    )
}

extension AnswerOutcome: Decodable {
    private enum CodingKeys: String, CodingKey {
        case status, question, score, feedback, nextReviewAt, intervalDays, practice
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(String.self, forKey: .status) {
        case "follow_up":
            self = .followUp(question: try c.decode(String.self, forKey: .question))
        default:
            self = .complete(
                score: try c.decode(Int.self, forKey: .score),
                feedback: try c.decode(String.self, forKey: .feedback),
                nextReviewAt: try c.decode(String.self, forKey: .nextReviewAt),
                intervalDays: try c.decode(Int.self, forKey: .intervalDays),
                // Absent on a server that predates practice mode; a daily review.
                practice: try c.decodeIfPresent(Bool.self, forKey: .practice) ?? false
            )
        }
    }
}

struct NotificationWindow: Codable, Equatable, Identifiable {
    var id: String { label }
    var label: String
    var on: Bool
    var from: String
    var to: String
}

struct AppSettings: Codable, Equatable {
    var reviewsPerDay: Int
    var timezone: String
    var windows: [NotificationWindow]

    static let placeholder = AppSettings(
        reviewsPerDay: 2,
        timezone: TimeZone.current.identifier,
        windows: [
            NotificationWindow(label: "Morning", on: true, from: "07:10", to: "08:30"),
            NotificationWindow(label: "Evening", on: true, from: "21:00", to: "22:30"),
        ]
    )

    /// The only times a window boundary may take. Tapping a chip advances through
    /// this list.
    static let allowedTimes = [
        "06:30", "07:10", "07:45", "08:30", "12:15", "18:40", "21:00", "22:30",
    ]
}

/// One turn in the Conversation thread. Render order is the source of truth.
struct ThreadEntry: Identifiable, Equatable {
    enum Role: Equatable { case question, answer, followUpQuestion }

    let id = UUID()
    let role: Role
    var text: String
}

/// The Conversation state machine, exactly as the handoff specifies.
enum Stage: Equatable {
    case loadingQuestion
    case idle
    case recording
    case processing
    case followUp
    case recordingFollowUp
    case result
}

struct SessionResult: Equatable {
    let score: Int
    let feedback: String
    /// `NEXT REVIEW · 27 JUL · INTERVAL 3D`, or the practice-mode line in a sprint.
    let scheduleLine: String
}

/// One scored card in a multi-card run. Drives the progress rail's covered dots
/// and every row of Session Recap.
struct RunEntry: Identifiable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let score: Int
    let feedback: String
    /// As the server reported it for this card, not as the client requested it.
    let practice: Bool
}
