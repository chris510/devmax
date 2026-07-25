import SwiftUI

struct CardHistoryScreen: View {
    let cardID: UUID
    @EnvironmentObject private var state: AppState
    @State private var detail: CardDetail?
    /// One row open at a time.
    @State private var expanded: UUID?

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
        }
        .padding(.top, 6)
        .padding(.bottom, 26)
    }

    private func metaLine(_ detail: CardDetail) -> String {
        guard !detail.sessions.isEmpty else { return "New card" }
        let scores = detail.sessions.compactMap(\.score)
        let average = scores.isEmpty ? 0 : Double(scores.reduce(0, +)) / Double(scores.count)
        var parts = ["\(detail.sessions.count) sessions", String(format: "avg %.1f", average)]

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
                MetaText(text: "FIRST REVIEW · TODAY, NEXT IN QUEUE",
                         font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint)
            }
            .wcFade()
        } else {
            VStack(spacing: 0) {
                Hairline()
                ForEach(detail.sessions) { session in
                    SessionRow(
                        session: session,
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
}

private struct SessionRow: View {
    let session: SessionHistory
    let isExpanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .top, spacing: 14) {
                    Text(ScoreStyle.label(for: session.score))
                        .font(TypeRole.historyScoreNumeral)
                        .monospacedDigit()
                        .foregroundStyle(ScoreStyle.color(for: session.score))
                        .frame(width: 16, alignment: .leading)

                    VStack(alignment: .leading, spacing: 6) {
                        MetaText(text: Self.dateLabel(session.date), font: TypeRole.metaRow,
                                 tracking: 1.0, uppercased: true)
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
                }
                .padding(.leading, 30)
                .padding(.bottom, 22)
                .padding(.top, 4)
                .frame(maxWidth: .infinity, alignment: .leading)
                .wcFade(Motion.fadeFast)
            }
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
