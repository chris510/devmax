import SwiftUI

struct CardHistoryScreen: View {
    let cardID: UUID
    @EnvironmentObject private var state: AppState
    @State private var detail: CardDetail?
    /// One row open at a time.
    @State private var expanded: UUID?
    @State private var maintenance: CardMaintenance?

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    Button { state.path.removeLast() } label: {
                        Text("← Today")
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.meta)
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                    if let detail {
                        heading(detail)
                        sessions(detail)
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { detail = try? await state.api.card(cardID) }
        .sheet(item: $maintenance) { value in
            CardMaintenanceSheet(
                value: value,
                updated: { maintenance = $0 },
                replaced: {
                    maintenance = nil
                    state.path.removeLast()
                    Task { await state.loadToday() }
                }
            )
            .environmentObject(state)
        }
    }

    private func heading(_ detail: CardDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(detail.topic)
                    .font(TypeRole.historyTitle)
                    .tracking(-0.4)
                    .foregroundStyle(Theme.text)
                MetaText(text: detail.category, font: WCFont.mono(10), tracking: 1.0,
                         color: Theme.metaDim, uppercased: true)
            }

            // The single most useful line — always above the fold.
            Text(detail.masterySummary.isEmpty ? "No signal yet." : detail.masterySummary)
                .font(TypeRole.masterySummary)
                .lineSpacing(19 * 1.45 - 19 * 1.2)
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)

            MetaText(text: metaLine(detail), font: TypeRole.metaBody, tracking: 1.0,
                     color: Theme.metaFaint, uppercased: true)

            Button {
                Task {
                    maintenance = try? await state.api.cardMaintenance(cardID)
                }
            } label: {
                MetaText(
                    text: "MAINTAIN CARD", font: TypeRole.metaBody,
                    tracking: 1.0, color: Theme.meta
                )
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
        }
        .padding(.top, 6)
        .padding(.bottom, 26)
    }

    private func metaLine(_ detail: CardDetail) -> String {
        var parts: [String]
        if detail.sessions.isEmpty {
            parts = ["New card"]
        } else {
            let scores = detail.sessions.compactMap { session in
                state.usesRecallContract ? session.recallScore : session.score
            }
            parts = ["\(detail.sessions.count) sessions"]
            if !scores.isEmpty {
                let average = Double(scores.reduce(0, +)) / Double(scores.count)
                parts.append(String(
                    format: state.usesRecallContract ? "avg recall %.1f" : "avg %.1f", average
                ))
            }
        }

        let recallNotBefore = state.recallNotBefore(
            cardID: cardID, fallback: detail.recallNotBeforeAt
        )
        if !detail.sessions.isEmpty, let label = RecallGate.label(recallNotBefore) {
            parts.append(label)
            return parts.joined(separator: " · ")
        }
        let parser = DateFormatter()
        parser.dateFormat = "yyyy-MM-dd"
        if let due = parser.date(from: detail.nextReviewAt) {
            let days = Calendar.current.dateComponents([.day], from: due, to: Date()).day ?? 0
            if days > 0 { parts.append("\(days) days overdue") }
        }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func sessions(_ detail: CardDetail) -> some View {
        if detail.sessions.isEmpty {
            VStack(alignment: .leading, spacing: 12) {
                Text("No sessions yet.")
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.textSecondary)
                availability(detail)
                cardActions(detail)
            }
            .wcFade()
        } else {
            VStack(spacing: 0) {
                cardActions(detail)
                    .padding(.bottom, 18)
                Hairline()
                ForEach(detail.sessions) { session in
                    SessionRow(
                        session: session,
                        score: state.usesRecallContract ? session.recallScore : session.score,
                        legacyOnly: state.usesRecallContract
                            && session.recallScore == nil
                            && session.legacyCompositeScore != nil,
                        isExpanded: expanded == session.id,
                        toggle: {
                            withAnimation(Motion.fadeFast) {
                                expanded = expanded == session.id ? nil : session.id
                            }
                        }
                    )
                    Hairline()
                }
            }
        }
    }

    @ViewBuilder
    private func availability(_ detail: CardDetail) -> some View {
        let value = state.recallNotBefore(cardID: cardID, fallback: detail.recallNotBeforeAt)
        if let label = RecallGate.label(value) {
            MetaText(
                text: label, font: TypeRole.metaBody,
                tracking: 1.0, color: Theme.metaFaint, uppercased: true
            )
        } else {
            MetaText(
                text: "CHOOSE THE HONEST NEXT STEP",
                font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint
            )
        }
    }

    @ViewBuilder
    private func cardActions(_ detail: CardDetail) -> some View {
        let recallOpen = state.recallIsAvailable(
            cardID: cardID, fallback: detail.recallNotBeforeAt
        )
        let due = recallOpen && state.queue.contains(where: { $0.id == cardID })
        if detail.learningAvailable == true || due {
            VStack(spacing: 10) {
                if detail.sessions.isEmpty {
                    if detail.learningAvailable == true {
                        PrimaryButton(title: "Learn this card") {
                            state.beginLearningFromHistory(cardID: cardID)
                        }
                    }
                    if due {
                        SecondaryButton(title: "Review now — I already know it") {
                            state.beginReviewFromHistory(cardID: cardID)
                        }
                    }
                } else {
                    if due {
                        PrimaryButton(title: "Review now") {
                            state.beginReviewFromHistory(cardID: cardID)
                        }
                    }
                    if detail.learningAvailable == true {
                        SecondaryButton(title: "Study source again") {
                            state.beginLearningFromHistory(cardID: cardID)
                        }
                    }
                }
            }
            .padding(.top, 8)
        }
    }
}

private struct CardMaintenanceSheet: View {
    @EnvironmentObject private var state: AppState
    @Environment(\.dismiss) private var dismiss
    let value: CardMaintenance
    let updated: (CardMaintenance) -> Void
    let replaced: () -> Void

    @State private var replacing = false
    @State private var question = ""
    @State private var schedule = "next"
    @State private var pending = false
    @State private var errorText = ""

    var body: some View {
        SheetChrome(title: replacing ? "Replace question" : "Card maintenance") {
            VStack(alignment: .leading, spacing: 18) {
                if replacing { replacementForm } else { actions }

                if !errorText.isEmpty {
                    InlineNotice {
                        Text(errorText)
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        }
        .onAppear { question = value.canonicalQuestion }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 20) {
            if value.lifecycleStatus == "active" {
                maintenanceAction(
                    "Archive card",
                    detail: "Removes it from reviews and Coverage. Every session stays here. You can restore it later."
                ) { Task { await archive() } }

                Hairline()

                maintenanceAction(
                    "Replace question",
                    detail: "Creates a new blank-history card and archives this one. Earlier scores keep the question they measured."
                ) { replacing = true }
            } else {
                maintenanceAction(
                    "Restore card",
                    detail: "Returns this card to the active library with its schedule and history intact."
                ) { Task { await restore() } }
            }
        }
    }

    private var replacementForm: some View {
        VStack(alignment: .leading, spacing: 16) {
            captureEditor("NEW CANONICAL QUESTION", text: $question, minHeight: 118, serif: true)
            ScheduleChoice(schedule: $schedule)
            PrimaryButton(
                title: pending ? "Replacing…" : "Create replacement",
                enabled: !pending && !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ) { Task { await replace() } }
            SecondaryButton(title: "Cancel") { replacing = false }
        }
    }

    private func maintenanceAction(
        _ title: String, detail: String, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                Text(title)
                    .font(TypeRole.bodyLarge)
                    .foregroundStyle(Theme.text)
                Text(detail)
                    .font(TypeRole.rowSummary)
                    .foregroundStyle(Theme.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(pending)
    }

    private func archive() async {
        await perform("Couldn't archive this card.") {
            let result = try await state.api.archiveCard(value.id)
            updated(result)
            state.queue.removeAll { $0.id == value.id }
            state.library.removeAll { $0.id == value.id }
            dismiss()
        }
    }

    private func restore() async {
        await perform("Couldn't restore this card.") {
            let result = try await state.api.restoreCard(value.id)
            updated(result)
            await state.loadToday()
            dismiss()
        }
    }

    private func replace() async {
        await perform("Couldn't create the replacement. This card is unchanged.") {
            _ = try await state.api.replaceCard(value.id, question: question, schedule: schedule)
            replaced()
        }
    }

    private func perform(_ message: String, operation: @escaping () async throws -> Void) async {
        pending = true
        errorText = ""
        do { try await operation() } catch { errorText = message }
        pending = false
    }
}

private struct SessionRow: View {
    let session: SessionHistory
    let score: Int?
    let legacyOnly: Bool
    let isExpanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .top, spacing: 14) {
                    Text(ScoreStyle.label(for: score))
                        .font(TypeRole.historyScoreNumeral)
                        .monospacedDigit()
                        .foregroundStyle(ScoreStyle.color(for: score))
                        .frame(width: 16, alignment: .leading)

                    VStack(alignment: .leading, spacing: 6) {
                        MetaText(text: Self.dateLabel(session.date), font: TypeRole.metaRow,
                                 tracking: 1.0, uppercased: true)
                        if legacyOnly {
                            MetaText(text: "LEGACY COMPOSITE · EXCLUDED FROM RECALL AVERAGE",
                                     font: TypeRole.metaLabel, tracking: 0.8,
                                     color: Theme.metaFaint)
                        }
                        Text(session.feedback)
                            .font(TypeRole.historyNote)
                            .foregroundStyle(Theme.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }

                    Text(isExpanded ? "▲" : "▼")
                        .font(.system(size: 9))
                        .foregroundStyle(Theme.metaDim)
                        .padding(.top, 2)
                }
                .padding(.vertical, 15)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            // Expands inline, indented 30px.
            if isExpanded {
                VStack(alignment: .leading, spacing: 18) {
                    ForEach(session.turns) { turn in
                        VStack(alignment: .leading, spacing: 7) {
                            MetaText(text: label(for: turn), font: TypeRole.metaLabel,
                                     tracking: 1.2, color: Theme.metaFaintAlt)
                            transcriptText(turn)
                        }
                    }
                    if let question = session.coachingQuestion,
                       let answer = session.coachingAnswer,
                       let feedback = session.coachingFeedback {
                        coachingTurn(question: question, answer: answer, feedback: feedback)
                    }
                }
                .padding(.leading, 30)
                .padding(.bottom, 22)
                .padding(.top, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
                .wcFade(Motion.fadeFast)
            }
        }
    }

    private func coachingTurn(question: String, answer: String, feedback: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            MetaText(
                text: session.coachingFocus == "boundaries"
                    ? "BOUNDARY PRACTICE" : "DEPTH PRACTICE",
                font: TypeRole.metaLabel, tracking: 1.2, color: Theme.metaFaintAlt
            )
            Text(question)
                .font(TypeRole.historyTranscriptQuestion)
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)
            Text(answer)
                .font(TypeRole.historyAnswer)
                .foregroundStyle(Theme.textMuted)
                .fixedSize(horizontal: false, vertical: true)
            Text(feedback)
                .font(TypeRole.historyAnswer)
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private func transcriptText(_ turn: Turn) -> some View {
        switch turn.role {
        case .question, .followUp:
            Text(turn.text)
                .font(TypeRole.historyTranscriptQuestion)
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        case .answer:
            Text(turn.text)
                .font(TypeRole.historyAnswer)
                .foregroundStyle(Theme.textMuted)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        case .score:
            Text(turn.text)
                .font(TypeRole.historyAnswer)
                .foregroundStyle(ScoreStyle.color(for: Int(turn.text.prefix(1))))
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func label(for turn: Turn) -> String {
        switch turn.role {
        case .question: return "Question"
        case .answer: return "Your answer"
        case .followUp: return "Follow-up"
        case .score: return "Score & feedback"
        }
    }

    private static func dateLabel(_ date: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "d MMM · HH:mm"
        return f.string(from: date)
    }
}
