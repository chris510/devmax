import SwiftUI

/// Build the topic set before starting — closer to "tap Start" than "fill in a
/// form". The suggested set is already built when the screen opens; everything
/// on it is optional refinement.
struct SprintSetupScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()

            VStack(alignment: .leading, spacing: 0) {
                Button { state.path.removeLast() } label: {
                    Text("← Today")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                VStack(alignment: .leading, spacing: 6) {
                    Text("Review sprint")
                        .font(TypeRole.screenTitle)
                        .tracking(-0.6)
                        .foregroundStyle(Theme.text)
                    MetaText(text: state.setupStatus, font: WCFont.mono(11.5),
                             tracking: 0.35, color: Theme.metaAlt)
                }
                .padding(.top, 4)
                .padding(.bottom, 16)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Metrics.screenPadding)

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    // The chips stay live in every state, so a too-narrow selection
                    // can be widened without leaving the screen.
                    categoryChips
                    sessionSize

                    switch state.libraryLoad {
                    case .loading: SprintSkeleton()
                    case .error: LoadFailure { Task { await state.loadLibrary() } }
                    case .ready:
                        if state.setupEmpty { emptyPool } else { suggestedSet }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 8)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
    }

    // MARK: - Controls

    /// One chip per category, multi-select. No selection means the whole library.
    private var categoryChips: some View {
        FlowLayout(horizontalSpacing: 6, verticalSpacing: 6) {
            ForEach(state.categories, id: \.self) { name in
                let on = state.setupCats.contains(name)
                Button {
                    withAnimation(Motion.fadeFast) { state.toggleCategory(name) }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(name.uppercased())
                            .font(WCFont.mono(10))
                            .tracking(1.0)
                            .foregroundStyle(on ? Theme.accentSelectedText : Theme.meta)
                        // The category's most urgent count — the same tier data
                        // that powers Coverage, shown where it gets acted on.
                        Text(state.chipNote(for: name))
                            .font(WCFont.mono(9.5))
                            .tracking(0.76)
                            .foregroundStyle(on ? Theme.accentChipNote : Theme.metaFaint)
                    }
                    .fixedSize()
                    .padding(.horizontal, 10)
                    .padding(.top, 6)
                    .padding(.bottom, 7)
                    .background(
                        RoundedRectangle(cornerRadius: 9)
                            .fill(on ? Theme.accentWash : .clear)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 9)
                            .strokeBorder(on ? Theme.accent : Theme.borderStrong, lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.bottom, 18)
    }

    /// The Settings "reviews per day" stepper verbatim, ranged 4–10.
    private var sessionSize: some View {
        HStack(alignment: .center, spacing: 16) {
            VStack(alignment: .leading, spacing: 4) {
                Text("Session size")
                    .font(WCFont.sans(14.5))
                    .foregroundStyle(Theme.text)
                Text("Weakest and least recently reviewed first")
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.metaAlt)
            }
            Spacer(minLength: 0)
            StepperControl(
                value: state.setupSize,
                decrement: {
                    state.setupSize = max(AppState.minSessionSize, state.setupSize - 1)
                },
                increment: {
                    state.setupSize = min(AppState.maxSessionSize, state.setupSize + 1)
                }
            )
        }
        .padding(.vertical, 16)
        .overlay(alignment: .top) { Hairline() }
    }

    // MARK: - Suggested set

    private var suggestedSet: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                MetaText(text: "WALK ORDER", font: WCFont.mono(10),
                         tracking: 1.2, color: Theme.metaDimAlt)
                Spacer(minLength: 0)
                // Regenerates from the current filter and size. Instant — no
                // loading state, because nothing is fetched.
                Button {
                    withAnimation(Motion.fadeFast) { state.seed += 1 }
                } label: {
                    Text("Shuffle")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .buttonStyle(.plain)
            }
            .padding(.top, 14)
            .padding(.bottom, 4)
            .overlay(alignment: .top) { Hairline() }

            // Rows are a preview, not a queue — deliberately not tappable.
            ForEach(state.sprintSet) { card in
                SprintPreviewRow(card: card)
            }

            Button { state.path.append(.coverage) } label: {
                Text("View full coverage →")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
            .padding(.top, 6)
            .overlay(alignment: .top) { Hairline() }
        }
    }

    private var emptyPool: some View {
        Text("Not enough cards in these categories yet.")
            .font(WCFont.serif(20))
            .lineSpacing(20 * 1.4 - 20 * 1.2)
            .foregroundStyle(Theme.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 30)
            .overlay(alignment: .top) { Hairline() }
            .wcFade()
    }

    // MARK: - Bottom

    private var bottomBlock: some View {
        VStack(alignment: .leading, spacing: 12) {
            MetaText(text: "PRACTICE MODE · WON'T CHANGE YOUR REVIEW SCHEDULE",
                     font: WCFont.mono(10.5), tracking: 0.63, color: Theme.metaFaint)

            if state.setupReady {
                PrimaryButton(title: "Start — \(state.sprintSet.count) cards") {
                    state.startSprint()
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, 12)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

/// Today's row anatomy minus the meta line.
private struct SprintPreviewRow: View {
    let card: CardSummary

    var body: some View {
        HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(card.topic)
                        .font(TypeRole.rowTopic)
                        .tracking(-0.165)
                        .foregroundStyle(Theme.text)
                    MetaText(text: card.category, font: WCFont.mono(10), tracking: 1.0,
                             color: Theme.metaDim, uppercased: true)
                    Spacer(minLength: 0)
                }

                if !card.masterySummary.isEmpty {
                    Text(card.masterySummary)
                        .font(TypeRole.rowSummary)
                        .foregroundStyle(Theme.textDim)
                        .lineSpacing(13.5 * 1.45 - 13.5 * 1.2)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            ScoreColumn(score: card.lastScore)
        }
        .padding(.top, 15)
        .padding(.bottom, 16)
        .overlay(alignment: .top) { Hairline() }
    }
}

/// Today's skeleton geometry, three rows, no shimmer.
struct SprintSkeleton: View {
    private let widths: [[CGFloat]] = [[0.58, 0.84], [0.46, 0.72], [0.63, 0.79]]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(0..<3, id: \.self) { row in
                HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
                    GeometryReader { geo in
                        VStack(alignment: .leading, spacing: 9) {
                            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton1)
                                .frame(width: geo.size.width * widths[row][0], height: 12)
                            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton2)
                                .frame(width: geo.size.width * widths[row][1], height: 10)
                            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton3)
                                .frame(width: geo.size.width * 0.34, height: 8)
                        }
                    }
                    .frame(height: 48)

                    RoundedRectangle(cornerRadius: 3)
                        .fill(Theme.skeleton1)
                        .frame(width: 12, height: 12)
                        .frame(width: Metrics.scoreColumnWidth, alignment: .center)
                        .padding(.top, 2)
                }
                .padding(.top, 15)
                .padding(.bottom, 16)
                .overlay(alignment: .top) { Hairline() }
            }

            MetaText(text: "LOADING CARDS", font: WCFont.mono(10),
                     tracking: 1.2, color: Theme.metaFaint)
                .padding(.top, 16)
        }
    }
}

