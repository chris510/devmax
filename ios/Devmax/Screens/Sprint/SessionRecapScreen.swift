import SwiftUI

/// What a multi-card run produced. Replaces the silent "last card finishes →
/// back to Today"; reached by tapping **See recap**, never by an automatic swap.
///
/// No celebration, no streaks, no share, no confetti — the feedback keeps the
/// scoring rubric's tone.
struct SessionRecapScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(state.run) { entry in
                        RecapRow(
                            entry: entry,
                            isExpanded: state.recapOpen == entry.id,
                            toggle: {
                                withAnimation(Motion.fadeFast) {
                                    state.recapOpen = state.recapOpen == entry.id ? nil : entry.id
                                }
                            }
                        )
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
    }

    /// The single-card score block's treatment, applied to the run's average.
    /// No new number presentation, no chart.
    private var header: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Session recap")
                .font(WCFont.serif(24))
                .tracking(-0.24)
                .foregroundStyle(Theme.textStrong)

            HStack(alignment: .lastTextBaseline, spacing: 10) {
                Text(averageLabel)
                    .font(TypeRole.bigScoreNumeral)
                    .monospacedDigit()
                    .foregroundStyle(averageColor)
                MetaText(text: state.usesRecallContract ? "/ 5 AVG RECALL" : "/ 5 AVERAGE", font: WCFont.mono(13),
                         tracking: 0, color: Theme.metaDimAlt)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, 20)
        .padding(.bottom, 16)
    }

    private var averageLabel: String {
        guard let average = state.runAverage else { return "—" }
        return String(format: "%.1f", average)
    }

    /// Coloured by the band the *rounded* average falls in — the same mapping a
    /// single score uses, so 3.4 and a 3 read alike.
    private var averageColor: Color {
        guard let average = state.runAverage else { return Theme.scoreNone }
        return ScoreStyle.color(for: Int(average.rounded()))
    }

    private var bottomBlock: some View {
        VStack(spacing: 12) {
            // The handoff prints the practice footnote unconditionally, but it
            // also routes *any* multi-card session here — and after a daily run
            // "SCHEDULE UNCHANGED" is simply false, on the one screen whose job
            // is to be trusted. Shown only when it's true; `Run another` is
            // likewise a sprint action, not a queue one.
            if state.runWasPractice {
                MetaText(text: "PRACTICE MODE · SCORES SAVED TO HISTORY, SCHEDULE UNCHANGED",
                         font: WCFont.mono(10.5), tracking: 0.63, color: Theme.metaFaint)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }

            PrimaryButton(title: "Done") { state.finish() }

            if state.runWasPractice {
                // Returns to Setup with the filter and session size preserved.
                Button { state.runAnother() } label: {
                    Text("Run another")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
            }
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, 12)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

/// Setup's row anatomy with this run's score, expanding to that card's feedback.
/// Card History's accordion behaviour, reused exactly — one row open at a time.
private struct RecapRow: View {
    let entry: RunEntry
    let isExpanded: Bool
    let toggle: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button(action: toggle) {
                HStack(alignment: .top, spacing: 14) {
                    TopicWithCategory(topic: entry.topic, category: entry.category)

                    HStack(alignment: .firstTextBaseline, spacing: 10) {
                        Text("\(entry.score)")
                            .font(TypeRole.scoreNumeral)
                            .monospacedDigit()
                            .foregroundStyle(ScoreStyle.color(for: entry.score))
                        Text(isExpanded ? "▲" : "▼")
                            .font(.system(size: 11))
                            .foregroundStyle(Theme.metaDimAlt)
                    }
                    .padding(.top, 2)
                }
                .padding(.top, Metrics.rowTopPadding)
                .padding(.bottom, Metrics.rowBottomPadding)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if isExpanded {
                Text(entry.feedback)
                    .font(WCFont.serif(17))
                    .lineSpacing(17 * 1.5 - 17 * 1.2)
                    .foregroundStyle(Theme.textSerif)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.bottom, 20)
                    .wcFade(Motion.fadeFast)
            }
        }
        .overlay(alignment: .top) { Hairline() }
    }
}
