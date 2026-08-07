import Foundation

/// Trusted answer frame attached to every reviewable card. These fields are
/// deliberately explicit: a gap cannot become a card while any one is blank.
struct AnswerRubric: Codable, Equatable {
    var mechanism: String = ""
    var acceptableAlternative: String = ""
    var tradeOff: String = ""
    var failureMode: String = ""
    var misconception: String = ""

    init(
        mechanism: String = "", acceptableAlternative: String = "", tradeOff: String = "",
        failureMode: String = "", misconception: String = ""
    ) {
        self.mechanism = mechanism
        self.acceptableAlternative = acceptableAlternative
        self.tradeOff = tradeOff
        self.failureMode = failureMode
        self.misconception = misconception
    }

    private enum CodingKeys: String, CodingKey {
        case mechanism, acceptableAlternative, tradeOff, failureMode, misconception
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        mechanism = try values.decodeIfPresent(String.self, forKey: .mechanism) ?? ""
        acceptableAlternative = try values.decodeIfPresent(
            String.self, forKey: .acceptableAlternative
        ) ?? ""
        tradeOff = try values.decodeIfPresent(String.self, forKey: .tradeOff) ?? ""
        failureMode = try values.decodeIfPresent(String.self, forKey: .failureMode) ?? ""
        misconception = try values.decodeIfPresent(String.self, forKey: .misconception) ?? ""
    }

    var isComplete: Bool {
        [mechanism, acceptableAlternative, tradeOff, failureMode, misconception]
            .allSatisfy { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }
}

struct PendingCapture: Codable, Identifiable, Equatable {
    let id: UUID
    var topic: String
    var context: String
    var status: String
    var sourceUrl: String
    var sourceSection: String
    var sourceLabel: String
    var answerBasis: String
    var answerRubric: AnswerRubric
    var canonicalQuestion: String
    var activatedCardId: UUID?
    let createdAt: Date
    var updatedAt: Date

    var needsSource: Bool {
        sourceUrl.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && sourceSection.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var sourceDisplay: String {
        [sourceLabel, sourceSection, sourceUrl]
            .first { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty } ?? ""
    }

    var summary: CaptureSummary {
        CaptureSummary(
            id: id, topic: topic, context: context, status: status,
            needsSource: needsSource, createdAt: createdAt, updatedAt: updatedAt
        )
    }
}

struct CaptureSummary: Codable, Identifiable, Equatable {
    let id: UUID
    let topic: String
    let context: String
    let status: String
    let needsSource: Bool
    let createdAt: Date
    let updatedAt: Date
}

struct CaptureCreateRequest: Encodable {
    let topic: String
    let context: String
}

struct CaptureUpdateRequest: Encodable {
    let sourceUrl: String
    let sourceSection: String
    let sourceLabel: String
    let answerBasis: String
    let answerRubric: AnswerRubric
    let canonicalQuestion: String
}

struct CardMaintenance: Codable, Identifiable, Equatable {
    let id: UUID
    let lifecycleStatus: String
    let canonicalQuestion: String
    let sourceUrl: String
    let sourceSection: String
    let sourceLabel: String
    let answerBasis: String
    let answerRubric: AnswerRubric
    let replacesCardId: UUID?
    let replacedByCardId: UUID?
}
