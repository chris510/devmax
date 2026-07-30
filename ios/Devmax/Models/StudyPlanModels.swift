import Foundation

// Study Plan wire models. Same conventions as Models.swift: snake_case on the
// wire, `.convertFromSnakeCase` on the decoder, no `CodingKeys` unless a shape
// genuinely needs hand-decoding.
//
// Two things these types deliberately do not carry:
//
//   * an exact completion date. Forecasts are plan-week precision, and the
//     server sends the rendered label so no client can derive a day from
//     weekly capacity.
//   * a short title without its long one. `displayTitle` is what a compressed
//     row shows; `fullTitle` is always present and is what VoiceOver reads.

struct StudyPlanSummary: Codable, Equatable {
    let active: Bool
    let planId: UUID?
    let title: String
    let subject: String
    let weekIndex: Int?
    let weekTotal: Int?
    /// The current phase's concise title. Uppercased by the mono style at the
    /// call site, not stored uppercased.
    let phaseTitle: String
    let nextBlockLabel: String?

    static let none = StudyPlanSummary(
        active: false, planId: nil, title: "", subject: "", weekIndex: nil,
        weekTotal: nil, phaseTitle: "", nextBlockLabel: nil
    )

    /// `PLAN · WEEK 4 · TECHNOLOGIES · NEXT TUE 19:00`, or the no-plan prompt.
    /// One compact line: no capacity, no progress, no description.
    var todayLine: String {
        guard active, let weekIndex else { return "PLAN · ADD A STUDY GUIDE" }
        var parts = ["PLAN", "WEEK \(weekIndex)"]
        if !phaseTitle.isEmpty { parts.append(phaseTitle.uppercased()) }
        if let nextBlockLabel { parts.append("NEXT \(nextBlockLabel.uppercased())") }
        return parts.joined(separator: " · ")
    }

    var accessibleLine: String {
        guard active, let weekIndex, let weekTotal else {
            return "Add a study guide. Opens plan creation."
        }
        var text = "Study plan. Week \(weekIndex) of \(weekTotal)."
        if !phaseTitle.isEmpty { text += " \(phaseTitle)." }
        if let nextBlockLabel { text += " Next study block \(nextBlockLabel)." }
        return text + " Opens the plan."
    }
}

struct PlanWeekRow: Codable, Equatable, Identifiable {
    var id: Int { index }
    let index: Int
    let displayTitle: String
    let fullTitle: String
    let status: String
    let isCurrent: Bool
}

struct PlanPhaseRow: Codable, Equatable, Identifiable {
    var id: Int { index }
    let index: Int
    let displayTitle: String
    let fullTitle: String
    let status: String
    let weekRange: String
    let currentWeekLabel: String?
    let weeks: [PlanWeekRow]

    /// `1 · Foundations`. The number is part of the label, not a separate column.
    var numberedTitle: String { "\(index) · \(displayTitle)" }

    /// `Weeks 4–6 · Week 4` — the range, plus position on the current phase only.
    var rangeLine: String {
        guard let currentWeekLabel else { return weekRange }
        return "\(weekRange) · \(currentWeekLabel)"
    }

    /// The short title is a visual compression; the accessible name is not.
    var accessibleLabel: String {
        "\(fullTitle) phase. \(rangeLine). \(status)."
    }
}

struct PlanOverview: Codable, Equatable, Identifiable {
    let id: UUID
    let title: String
    let subject: String
    let mode: String
    let status: String
    let weekIndex: Int
    let weekTotal: Int
    /// Pre-rendered, plan-week precision: "Est. completion · week of 19 Oct".
    let forecastLabel: String
    let forecastEndPlanWeek: Int
    let revision: Int
    let supportsRecallCards: Bool
    let phases: [PlanPhaseRow]
    let latestChange: String?

    /// `Week 4 of 12 · Flexible`. Sentence case, per V3.5.
    var positionLine: String {
        "Week \(weekIndex) of \(weekTotal) · \(mode.capitalized)"
    }
}

struct WeekItemRow: Codable, Equatable, Identifiable {
    let id: UUID
    let title: String
    let estimateMinutes: Int
    let complete: Bool
    let optional: Bool
    let blockedBy: String?

    /// `90 min`, or `30 min · Optional`. Priority appears only where it applies.
    var metaLine: String {
        optional ? "\(estimateMinutes) min · Optional" : "\(estimateMinutes) min"
    }
}

struct WeekSection: Codable, Equatable, Identifiable {
    var id: String { type }
    let type: String
    let label: String
    let aside: String
    let note: String
    let rows: [WeekItemRow]
}

struct WeekDetail: Codable, Equatable {
    let planId: UUID
    let index: Int
    let phaseTitle: String
    let fullTitle: String
    let displayTitle: String
    let coreComplete: Int
    let coreTotal: Int
    let plannedMinutes: Int
    let capacityMinutes: Int
    let sections: [WeekSection]

    /// The two facts under the title, and only two.
    var coreLine: String { "\(coreComplete) of \(coreTotal) Core complete" }
    var capacityLine: String {
        "\(PlanFormat.hours(plannedMinutes)) of \(PlanFormat.hours(capacityMinutes)) planned"
    }
}

struct PlanItemDetail: Codable, Equatable, Identifiable {
    let id: UUID
    let planId: UUID
    let fullTitle: String
    let phaseTitle: String
    let weekIndex: Int
    let type: String
    let priority: String
    let status: String
    let whyItMatters: String
    let doneWhen: String
    let estimateMinutes: Int
    let estimateSource: String
    let estimateConfidence: String
    let sourceExcerpt: String
    let sourceLabel: String
    let notes: String
    let studyBlockLabel: String
    let studyBlockWeekday: Int?
    let studyBlockMinuteOfDay: Int?
    let studyBlockReminderOn: Bool
    let completedAt: Date?
    let reopenedAt: Date?
    let linkedCardIds: [UUID]
    let cardProposalsAvailable: Bool
    let blockedBy: [String]

    var isComplete: Bool { status == "complete" }

    /// `WEEK 4 · TECHNOLOGIES · CORE · COMPLETE`. No internal id, ever.
    var metaLine: String {
        var parts = ["WEEK \(weekIndex)"]
        if !phaseTitle.isEmpty { parts.append(phaseTitle.uppercased()) }
        parts.append(priority.uppercased())
        if isComplete { parts.append("COMPLETE") }
        return parts.joined(separator: " · ")
    }
}

struct PlanWeekPlacement: Codable, Equatable, Identifiable {
    var id: Int { index }
    let index: Int
    let capacityMinutes: Int
    let beforeMinutes: Int
    let afterMinutes: Int
    let changed: Bool
    let overCapacity: Bool

    var label: String {
        "WEEK \(index) · WAS \(beforeMinutes) / \(capacityMinutes)"
    }
    var fit: String { "\(afterMinutes) / \(capacityMinutes) MIN" }
}

struct PlanItemMove: Codable, Equatable, Identifiable {
    var id: String { "\(title)-\(fromWeek)-\(toWeek)" }
    let title: String
    let fromWeek: Int
    let toWeek: Int
    let minutes: Int
}

struct PlanUnresolved: Codable, Equatable, Identifiable {
    var id: String { title }
    let title: String
    let minutes: Int
    let reason: String

    var explanation: String {
        switch reason {
        case "does_not_fit": return "This item does not fit in a single study week."
        case "dependency_cycle": return "Something it depends on has nowhere to go either."
        default: return "No week in its phase has room for it."
        }
    }
}

/// A decision screen's whole payload. Leads with the outcome, then the
/// arithmetic — the one place in Study Plan where length is not the constraint.
struct PlanProposal: Codable, Equatable {
    let kind: String
    let basePlanRevision: Int
    let headline: String
    let body: String
    let weeks: [PlanWeekPlacement]
    let moves: [PlanItemMove]
    let unresolved: [PlanUnresolved]
    let unresolvedMinutes: Int
    let displacedMinutes: Int
    let forecastEndPlanWeek: Int
    let forecastLabel: String
    let forecastChanged: Bool
    let canApply: Bool
    let reasons: [String]
    let unchanged: [String]

    var affectedWeeks: [PlanWeekPlacement] { weeks.filter { $0.changed } }

    /// Why Apply is disabled, in the app's own words rather than the codes.
    var blockingReasons: [String] {
        reasons.map { code in
            switch code {
            case "week_over_capacity": return "A week is over its capacity."
            case "dependency_out_of_order": return "A prerequisite would come after what needs it."
            case "deadline_missed": return "The plan would finish after the deadline."
            case "core_removed_without_confirmation": return "Core work would be dropped."
            case "work_unplaced": return "Some work has nowhere to go."
            default: return code
            }
        }
    }
}

struct PlanCheck: Codable, Equatable, Identifiable {
    var id: String { key }
    let key: String
    let label: String
    let status: String
    let value: String
    let detail: String
    let destination: String?

    var isResolved: Bool { status == "ok" }
    /// Only exceptions get a disclosure arrow and somewhere to go.
    var isTappable: Bool { destination != nil && !isResolved }
}

struct PreviewPhase: Codable, Equatable, Identifiable {
    var id: Int { index }
    let index: Int
    let displayTitle: String
    let fullTitle: String
    let description: String
    let weekRange: String
}

struct PlanPreview: Codable, Equatable {
    let draftId: UUID
    let status: String
    let title: String
    let subject: String
    let mode: String
    let weeks: Int
    let phases: Int
    let weeklyCapacityMinutes: Int
    let totalMinutes: Int
    let forecastLabel: String
    let supportsRecallCards: Bool
    let checks: [PlanCheck]
    let canCreate: Bool
    let phaseRows: [PreviewPhase]
    let error: String

    var failed: Bool { status == "failed" }

    /// `12 weeks · 4 phases · 12h per week · Flexible`.
    var summaryLine: String {
        "\(weeks) weeks · \(phases) phases · \(PlanFormat.hours(weeklyCapacityMinutes)) "
            + "per week · \(mode.capitalized)"
    }
    var unresolvedCount: Int { checks.filter { !$0.isResolved }.count }
}

struct PlanListEntry: Codable, Equatable, Identifiable {
    let id: UUID
    let title: String
    let subject: String
    let status: String
    let meta: String
    let createdAt: Date
}

struct PlanList: Codable, Equatable {
    let active: [PlanListEntry]
    let paused: [PlanListEntry]
    let completed: [PlanListEntry]
    let archived: [PlanListEntry]

    var isEmpty: Bool {
        active.isEmpty && paused.isEmpty && completed.isEmpty && archived.isEmpty
    }

    /// Lifecycle order, and every group is shown even when empty — "no active
    /// plan" is a state the sheet has to be able to say out loud.
    var groups: [(label: String, entries: [PlanListEntry])] {
        [
            ("ACTIVE", active), ("PAUSED", paused),
            ("COMPLETED", completed), ("ARCHIVED", archived),
        ]
    }
}

struct PlanRevisionEntry: Codable, Equatable, Identifiable {
    let id: UUID
    let kind: String
    let summary: String
    let createdAt: Date
    let reversible: Bool
}

struct PlanRecap: Codable, Equatable {
    let id: UUID
    let title: String
    let coreComplete: Int
    let coreTotal: Int
    let optionalDeferred: Int
    let estimatedMinutes: Int
    let learnPracticeMinutes: Int
    let retrievalMinutes: Int
    let retrievalCompleted: Int
    let practiceCompleted: Int
    let remainingGaps: [String]
}

struct CardGateResult: Codable, Equatable, Identifiable {
    var id: Int { questionIndex }
    let questionIndex: Int
    let question: String
    let passed: Bool
    let reason: String
}

struct CardProposal: Codable, Equatable, Identifiable {
    let id: UUID
    let revision: Int
    let topic: String
    let category: String
    let canonicalQuestion: String
    let reason: String
    let gate: [CardGateResult]
    let disposition: String
    let duplicateCheckResult: String
    let existingCardId: UUID?
    let existingCardContext: String
    let failedQuestionIndex: Int?
    let failedReason: String

    /// Only a clean five-of-five candidate is selectable, and only these count.
    var isSuggested: Bool { disposition == "suggested" }
    var isPossibleOverlap: Bool { disposition == "possible_overlap" }
    var isExisting: Bool { disposition == "existing" }
    var isNotSuggested: Bool { disposition == "not_suggested" }
    var isAdded: Bool { disposition == "accepted" }

    /// `FAILED Q3 · TESTS A DEFINITION, NOT A SCENARIO`. No action accompanies it.
    var failureLine: String {
        guard let failedQuestionIndex else { return "" }
        return "FAILED Q\(failedQuestionIndex) · \(failedReason.uppercased())"
    }
    var gateLine: String {
        let passed = gate.filter(\.passed).count
        return "GATE · \(passed) OF \(gate.count) PASSED · \(topic.uppercased())"
    }
}

struct CardProposalList: Codable, Equatable {
    let planId: UUID
    let itemId: UUID
    let supportsRecallCards: Bool
    let suggestedCount: Int
    let proposals: [CardProposal]
    let note: String

    var suggested: [CardProposal] { proposals.filter { !$0.isNotSuggested } }
    var notSuggested: [CardProposal] { proposals.filter(\.isNotSuggested) }

    /// "1 focused card suggested" — the count matches what is selectable.
    var headline: String {
        switch suggestedCount {
        case 0: return "No cards suggested"
        case 1: return "1 focused card suggested"
        default: return "\(suggestedCount) focused cards suggested"
        }
    }
}

struct CardAcceptResult: Codable, Equatable {
    let status: String
    let createdCardIds: [UUID]
    let replayed: Bool
}

/// Display formatting for plan numbers. Minutes are the unit everywhere in the
/// data; hours exist only here.
enum PlanFormat {
    static func hours(_ minutes: Int) -> String {
        let value = Double(minutes) / 60
        return value == value.rounded()
            ? "\(Int(value))h"
            : String(format: "%.1fh", value)
    }

    static func minutes(_ minutes: Int) -> String { "\(minutes) min" }
}
