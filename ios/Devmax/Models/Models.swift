import Foundation

// Wire models mirror the backend's snake_case shapes exactly; the decoder is
// configured with `.convertFromSnakeCase`, so nothing here restates key names.

enum RecallGate {
    static func isOpen(_ value: String?, at now: Date = Date()) -> Bool {
        guard let value, !value.isEmpty else { return true }
        guard let date = WireDate.parse(value) else { return false }
        return date <= now
    }

    /// A future learning delay replaces stale SM-2 language in learner-facing
    /// metadata. The stored review date is intentionally left untouched.
    static func label(_ value: String?, at now: Date = Date()) -> String? {
        guard let value, let date = WireDate.parse(value), date > now else { return nil }
        let formatter = DateFormatter()
        formatter.locale = .current
        formatter.dateFormat = Calendar.current.isDate(date, inSameDayAs: now)
            ? "'recall available' HH:mm"
            : "'recall available' d MMM · HH:mm"
        return formatter.string(from: date)
    }
}

struct DueCard: Codable, Identifiable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let masterySummary: String
    let lastScore: Int?
    /// V2's only numeric signal. Optional so a compatible build can still read
    /// the pre-capability server during a rolling deployment.
    var recallScore: Int? = nil
    var scoreKind: String? = nil
    var scoringContractVersion: Int? = nil
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
    var recallScore: Int? = nil
    var scoreKind: String? = nil
    var scoringContractVersion: Int? = nil
    /// The three axes behind `lastScore`. Coverage's rollup line is the only
    /// consumer — nothing else in the app decomposes a score.
    let lastAccuracy: Int?
    let lastDepth: Int?
    let lastBoundaries: Int?
    let easeFactor: Double
    let intervalDays: Int
    let repetitions: Int
    let nextReviewAt: String
    /// Both computed server-side, like `DueCard.dueLabel`.
    let dueLabel: String
    let daysSinceReview: Int?
    let missedCount: Int
    /// A learning exposure creates a quiet delay before recall is honest again.
    /// Optional for rolling deploys and for cards that have never been exposed.
    var recallNotBeforeAt: String? = nil

    func recallIsAvailable(at now: Date = Date()) -> Bool {
        RecallGate.isOpen(recallNotBeforeAt, at: now)
    }

    var recallAvailabilityLabel: String? { RecallGate.label(recallNotBeforeAt) }

    /// Review Sprint walks library cards through the same Conversation screen the
    /// daily queue uses, so the set is adapted rather than the screen generalised.
    /// `resumable` is not a library fact — only a live session with a saved draft
    /// makes a card resumable — so it is supplied by whoever builds the queue.
    func asQueueCard(resumable: Bool = false) -> DueCard {
        DueCard(
            id: id, topic: topic, category: category, masterySummary: masterySummary,
            lastScore: lastScore, recallScore: recallScore, scoreKind: scoreKind,
            scoringContractVersion: scoringContractVersion,
            dueLabel: dueLabel, resumable: resumable,
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
    var recallScore: Int? = nil
    var legacyCompositeScore: Int? = nil
    var scoringContractVersion: Int? = nil
    let feedback: String
    let turns: [Turn]
    var coachingFocus: String? = nil
    var coachingQuestion: String? = nil
    var coachingAnswer: String? = nil
    var coachingFeedback: String? = nil
}

struct CardDetail: Codable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let masterySummary: String
    let lastScore: Int?
    var recallScore: Int? = nil
    var scoreKind: String? = nil
    var scoringContractVersion: Int? = nil
    let easeFactor: Double
    let intervalDays: Int
    let repetitions: Int
    let nextReviewAt: String
    let missedCount: Int
    let sessions: [SessionHistory]
    /// Learning fields are additive so a compatible client can still read a
    /// server while it rolls forward. A malformed delay fails closed.
    var recallNotBeforeAt: String? = nil
    var learningAvailable: Bool? = nil
    var sourceLabel: String? = nil
    var sourceSection: String? = nil

    func recallIsAvailable(at now: Date = Date()) -> Bool {
        RecallGate.isOpen(recallNotBeforeAt, at: now)
    }
}

/// Source-backed first exposure. Deliberately contains no canonical question:
/// Learn and closed-book recall are separate screens and separate moments.
struct LearningCard: Codable, Equatable, Identifiable {
    var id: UUID { cardId }
    let cardId: UUID
    let topic: String
    let category: String
    let sourceLabel: String
    let sourceSection: String
    let sourceUrl: String
    let sourceExcerpt: String
    let coreExplanation: String
    let essentialAccount: String
    let acceptableAlternative: String
    let depthExtension: String
    let boundaryExtension: String
    let misconception: String
    let recallAvailableAt: String
}

struct SessionStart: Codable, Equatable {
    let sessionId: UUID
    let question: String
    let isFollowUp: Bool
    let draftText: String
    let resumed: Bool
    let turnIndex: Int

    init(
        sessionId: UUID, question: String, isFollowUp: Bool, draftText: String,
        resumed: Bool, turnIndex: Int = 0
    ) {
        self.sessionId = sessionId
        self.question = question
        self.isFollowUp = isFollowUp
        self.draftText = draftText
        self.resumed = resumed
        self.turnIndex = turnIndex
    }

    private enum CodingKeys: String, CodingKey {
        case sessionId, question, isFollowUp, draftText, resumed, turnIndex
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        sessionId = try container.decode(UUID.self, forKey: .sessionId)
        question = try container.decode(String.self, forKey: .question)
        isFollowUp = try container.decode(Bool.self, forKey: .isFollowUp)
        draftText = try container.decode(String.self, forKey: .draftText)
        resumed = try container.decode(Bool.self, forKey: .resumed)
        // Rolling compatibility with the pre-index server. It cannot replay a
        // completed turn, but the opening turn remains unambiguously zero.
        turnIndex = try container.decodeIfPresent(Int.self, forKey: .turnIndex) ?? 0
    }
}

/// The `POST /sessions/{id}/answers` response is a discriminated union — the
/// server owns the follow-up decision, the client only reacts to it.
enum AnswerOutcome: Equatable {
    case followUp(question: String, turnIndex: Int?)
    case complete(
        score: Int, recallScore: Int, scoringContractVersion: Int,
        feedback: String, nextReviewAt: String, intervalDays: Int, practice: Bool,
        reattemptOffered: Bool, reattemptPrompt: String?, coachingOffered: Bool,
        coachingFocus: String?, coachingQuestion: String?
    )
}

extension AnswerOutcome: Decodable {
    private enum CodingKeys: String, CodingKey {
        case status, question, score, feedback, nextReviewAt, intervalDays, practice
        case turnIndex
        case reattemptOffered, reattemptPrompt
        case recallScore, scoringContractVersion
        case coachingOffered, coachingFocus, coachingQuestion
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        switch try container.decode(String.self, forKey: .status) {
        case "follow_up":
            self = .followUp(
                question: try container.decode(String.self, forKey: .question),
                turnIndex: try container.decodeIfPresent(Int.self, forKey: .turnIndex)
            )
        default:
            self = .complete(
                score: try container.decode(Int.self, forKey: .score),
                recallScore: try container.decodeIfPresent(Int.self, forKey: .recallScore)
                    ?? container.decode(Int.self, forKey: .score),
                scoringContractVersion: try container.decodeIfPresent(
                    Int.self, forKey: .scoringContractVersion
                ) ?? 1,
                feedback: try container.decode(String.self, forKey: .feedback),
                nextReviewAt: try container.decode(String.self, forKey: .nextReviewAt),
                intervalDays: try container.decode(Int.self, forKey: .intervalDays),
                // Absent on a server that predates practice mode; a daily review.
                practice: try container.decodeIfPresent(Bool.self, forKey: .practice) ?? false,
                // Absent on a server that predates the coached re-attempt. Defaulting
                // to false hides the affordance rather than offering a turn the
                // server would 409 — the safe direction for an optional extra turn.
                reattemptOffered: try container.decodeIfPresent(
                    Bool.self, forKey: .reattemptOffered
                ) ?? false,
                reattemptPrompt: try container.decodeIfPresent(
                    String.self, forKey: .reattemptPrompt
                ),
                coachingOffered: try container.decodeIfPresent(
                    Bool.self, forKey: .coachingOffered
                ) ?? false,
                coachingFocus: try container.decodeIfPresent(
                    String.self, forKey: .coachingFocus
                ),
                coachingQuestion: try container.decodeIfPresent(
                    String.self, forKey: .coachingQuestion
                )
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
    /// ISO weekday numbers: Monday = 1 through Sunday = 7.
    ///
    /// `days` is additive on the wire. A server that predates weekday-aware
    /// windows omits it, which must retain the old every-day behaviour during a
    /// rolling deployment rather than quietly silencing reminders.
    var days: [Int]

    init(
        label: String, on: Bool, from: String, to: String,
        days: [Int] = Array(1...7)
    ) {
        self.label = label
        self.on = on
        self.from = from
        self.to = to
        self.days = days
    }

    private enum CodingKeys: String, CodingKey { case label, on, from, to, days }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        label = try values.decode(String.self, forKey: .label)
        on = try values.decode(Bool.self, forKey: .on)
        from = try values.decode(String.self, forKey: .from)
        to = try values.decode(String.self, forKey: .to)
        days = if values.contains(.days) {
            try values.decode([Int].self, forKey: .days)
        } else {
            Array(1...7)
        }
    }
}

struct AppSettings: Codable, Equatable {
    var reviewsPerDay: Int
    var timezone: String
    var windows: [NotificationWindow]
    /// Read-only server capability. Missing means V1 during a rolling deploy.
    var activeScoringContractVersion: Int? = nil

    var usesRecallContract: Bool { activeScoringContractVersion == 2 }

    /// The endpoint retains a daily safety cap, while the product exposes one
    /// reminder per enabled window. Keep the wire value aligned with that UI.
    var normalizedReminderSettings: AppSettings {
        var value = self
        value.reviewsPerDay = min(6, max(1, windows.filter(\.on).count))
        return value
    }

    /// The push endpoint can issue at most one reminder per enabled window and
    /// never more than `reviewsPerDay` in a local day. Summing that capped count
    /// across the seven ISO weekdays is therefore the honest weekly maximum;
    /// multiplying either input alone overstates schedules with sparse or
    /// overlapping windows.
    var weeklyReminderMaximum: Int {
        (1...7).reduce(into: 0) { total, isoWeekday in
            let windowsThatDay = windows.filter {
                $0.on && $0.days.contains(isoWeekday)
            }.count
            total += min(reviewsPerDay, windowsThatDay)
        }
    }

    var weeklyReminderMaximumLabel: String {
        switch weeklyReminderMaximum {
        case 0: "No reminders scheduled"
        case 1: "Up to 1 reminder per week"
        case let count: "Up to \(count) reminders per week"
        }
    }

    var reminderScheduleValidationMessage: String? {
        for window in windows {
            guard !window.days.isEmpty else {
                return "Choose at least one day for every reminder window."
            }
            guard window.days.allSatisfy({ (1...7).contains($0) }),
                  Set(window.days).count == window.days.count
            else {
                return "Reminder days must be unique weekdays from Monday through Sunday."
            }
            guard let start = Self.minutes(window.from),
                  let end = Self.minutes(window.to),
                  end - start >= 30
            else {
                return "Each reminder window must end at least 30 minutes after it starts."
            }
        }

        return reminderWindowCollisionMessage
    }

    var reminderWindowCollisionMessage: String? {
        for (index, left) in windows.enumerated() where left.on {
            for right in windows.dropFirst(index + 1) where right.on {
                let sharesDay = !Set(left.days).isDisjoint(with: Set(right.days))
                if sharesDay, Self.minutes(left.from) == Self.minutes(right.from) {
                    return "Windows on the same day need different start times."
                }
            }
        }
        return nil
    }

    private static func timeComponents(_ value: String) -> (hour: Int, minute: Int)? {
        let parts = value.split(separator: ":").compactMap { Int($0) }
        guard parts.count == 2, (0...23).contains(parts[0]), (0...59).contains(parts[1])
        else { return nil }
        return (parts[0], parts[1])
    }

    private static func minutes(_ value: String) -> Int? {
        guard let time = timeComponents(value) else { return nil }
        return time.hour * 60 + time.minute
    }

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
    /// `reattemptQuestion` is the coached re-attempt after the correction,
    /// prefaced `In your words: ` the way the probes are prefaced
    /// `One more: ` and `Last one: `.
    enum Role: Equatable {
        case question, answer, followUpQuestion, reattemptQuestion
        case coachingQuestion, coachingFeedback
    }

    let id = UUID()
    let role: Role
    var text: String
}

extension ThreadEntry.Role {
    /// Only question turns belong to read-aloud. In particular, an optimistic
    /// answer or coaching feedback must not make the preceding question play
    /// again while the request is being scored.
    var isSpokenPrompt: Bool {
        switch self {
        case .question, .followUpQuestion, .reattemptQuestion, .coachingQuestion:
            return true
        case .answer, .coachingFeedback:
            return false
        }
    }
}

extension Array where Element == ThreadEntry {
    var latestSpokenPrompt: ThreadEntry? {
        last(where: { $0.role.isSpokenPrompt })
    }
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
    /// Turn 3. Reached only from `.result`, and only on a tap — the session is
    /// already complete and scored by the time this stage exists.
    case reattempt
    case recordingReattempt
    /// Optional, unscored V2 practice after a passing Recall result.
    case coaching
    case recordingCoaching
    /// The question never arrived, carrying the note that names why. A state of
    /// its own rather than `.loadingQuestion` plus a flag: there is no session, so
    /// every answering path below must be dead, and pairing two variables by
    /// convention is what let a *load* failure render as a submit failure with a
    /// live mic over a session that was never created.
    case questionFailed(String)

    /// The three answering stages and their three recording twins are a flat
    /// cross-product, so every consumer used to re-derive the partition itself —
    /// six `||` chains in two files, one of which (`simulatedAnswer`) was missed.
    /// Asking the enum about itself keeps them from drifting apart again.
    var isRecording: Bool {
        self == .recording || self == .recordingFollowUp || self == .recordingReattempt
            || self == .recordingCoaching
    }

    /// Whether the answer control is live. Recording counts — tapping it submits.
    var acceptsAnswer: Bool {
        self == .idle || self == .followUp || self == .reattempt || self == .coaching
            || isRecording
    }

    /// The recording twin of an answering stage; itself if already recording.
    ///
    /// `.questionFailed` is listed rather than left to the `default:` arm on
    /// purpose. Both twins fall through to a *live* answering stage, so a dead
    /// state that reached either would come back answerable — the exact shape of
    /// the bug this case exists to make impossible.
    var recordingTwin: Stage {
        switch self {
        case .idle, .recording: return .recording
        case .followUp, .recordingFollowUp: return .recordingFollowUp
        case .reattempt, .recordingReattempt: return .recordingReattempt
        case .coaching, .recordingCoaching: return .recordingCoaching
        case .loadingQuestion, .processing, .result, .questionFailed: return self
        }
    }

    /// The stage to rewind to when a submit fails — the turn the answer belonged to.
    var answeringTwin: Stage {
        switch self {
        case .idle, .recording: return .idle
        case .followUp, .recordingFollowUp: return .followUp
        case .reattempt, .recordingReattempt: return .reattempt
        case .coaching, .recordingCoaching: return .coaching
        case .loadingQuestion, .processing, .result, .questionFailed: return self
        }
    }

    /// Turn 3 goes to a different endpoint than turns 1 and 2.
    var isReattempt: Bool { self == .reattempt || self == .recordingReattempt }
    var isCoaching: Bool { self == .coaching || self == .recordingCoaching }

    /// Only an incomplete scored turn is resumable from `startSession`, so only
    /// those stages have a meaningful server draft coordinate. Post-result
    /// re-attempt/coaching drafts remain local for backgrounding and inline retry;
    /// navigating away intentionally ends those optional turns.
    var supportsServerDraft: Bool {
        self == .idle || self == .recording || self == .followUp
            || self == .recordingFollowUp
    }

    /// `hidden`, not `none` — a case named `none` reads as `Optional.none` at every
    /// call site, and Swift will happily infer the wrong one.
    enum Footer { case answer, result, hidden }

    /// Which footer the conversation shows. A stage with no session shows none, and
    /// the view asks the stage rather than re-deriving it — so a future footer edit
    /// cannot forget the case where there is nothing to answer.
    var footer: Footer {
        switch self {
        case .idle, .recording, .followUp, .recordingFollowUp,
             .reattempt, .recordingReattempt, .coaching, .recordingCoaching:
            return .answer
        // The turn is closed: the answer is sent and the scoring indicator owns
        // the screen. A mic still on screen reading `TAP TO KEEP GOING` offers to
        // continue an answer that is already being scored.
        case .loadingQuestion, .processing, .questionFailed: return .hidden
        case .result: return .result
        }
    }
}

struct SessionResult: Equatable {
    let score: Int
    let scoringContractVersion: Int
    let feedback: String
    /// `NEXT REVIEW · 27 JUL · INTERVAL 3D`, or the practice-mode line in a sprint.
    let scheduleLine: String
    /// Server-computed (`accuracy <= 2`). The client never sees the axis
    /// itself — the score block shows one numeral, and that numeral is the composite.
    /// `var` so consuming the offer is a one-field write, not a struct rebuild.
    var reattemptOffered: Bool
    /// The exact prompt turn 3 asks, composed by the server so what is shown is what
    /// the answer is graded against. Nil when no re-attempt is offered.
    let reattemptPrompt: String?
    var coachingOffered: Bool
    let coachingFocus: String?
    let coachingQuestion: String?

    /// Production still emits V1 composites. Only the activated V2 contract may
    /// call the displayed numeral Recall; unknown versions stay neutral.
    var scoreLabel: String {
        scoringContractVersion == 2 ? "RECALL" : "SCORE"
    }
}

struct CoachingOutcome: Codable, Equatable {
    let focus: String
    let question: String
    let feedback: String
}

/// One scored card in a multi-card run. Drives the progress rail's covered dots
/// and every row of Session Recap.
struct RunEntry: Identifiable, Equatable {
    let id: UUID
    let topic: String
    let category: String
    let score: Int
    let feedback: String
    /// The server-owned next-review result for this concept, carried into recap
    /// so lesson Results does not make the learner reopen every card.
    let scheduleLine: String
    /// As the server reported it for this card, not as the client requested it.
    let practice: Bool
}

/// Device-local preferences. Distinct from `AppSettings`, which the server owns
/// because the scheduler acts on it — nothing here leaves the phone.
enum Preferences {
    /// Whether a card's question is spoken when it opens. On by default: the
    /// product is built for answering half-awake or in line, where hearing the
    /// question beats reading it.
    static let readAloudKey = "wc.readAloud"
}
