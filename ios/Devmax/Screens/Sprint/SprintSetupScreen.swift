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
                    Text(state.usesRecallContract || state.sprintKind == .review
                         ? "Review sprint" : "Depth repair")
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
                    case .loading: LoadingList(label: "LOADING CARDS", inset: 0, separator: .overlay)
                    case .error: LoadFailure { Task { await state.loadLibrary() } }
                    case .ready:
                        if state.setupReady { suggestedSet } else { emptyPool }
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
            ForEach(state.sprintCategories, id: \.self) { name in
                let on = state.setupCats.contains(name)
                Button {
                    withAnimation(Motion.fadeFast) { state.toggleCategory(name) }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        MetaText(text: name, font: WCFont.mono(10), tracking: 1.0,
                                 color: on ? Theme.accentSelectedText : Theme.meta,
                                 uppercased: true)
                        // The category's most urgent count — the same tier data
                        // that powers Coverage, shown where it gets acted on.
                        // The prototype uppercases the whole chip in CSS, so this
                        // is uppercased here while Coverage's identical-looking
                        // tier line stays in the case the design writes it.
                        MetaText(text: state.chipNote(for: name), font: WCFont.mono(9.5),
                                 tracking: 0.76,
                                 color: on ? Theme.accentChipNote : Theme.metaFaint,
                                 uppercased: true)
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
                Text(
                    state.sprintKind == .review || state.usesRecallContract
                        ? (state.usesRecallContract
                           ? "Lowest Recall and least recently reviewed first"
                           : "Weakest and least recently reviewed first")
                        : "Thin depth signal first"
                )
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
                    withAnimation(Motion.fadeFast) {
                        state.sprintExcluded = []
                        state.seed += 1
                    }
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
                SprintPreviewRow(
                    card: card,
                    remove: state.sprintKind == .review
                        ? nil : { state.removeFromSprint(card.id) }
                )
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
        Text(
            state.setupWaitingOnRecall
                ? "Not enough cards ready for recall yet."
                : "Not enough cards in these categories yet."
        )
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
    @EnvironmentObject private var state: AppState
    let card: CardSummary
    let remove: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: Metrics.scoreColumnGap) {
            VStack(alignment: .leading, spacing: 5) {
                TopicWithCategory(topic: card.topic, category: card.category)

                if !card.masterySummary.isEmpty {
                    Text(card.masterySummary)
                        .font(TypeRole.rowSummary)
                        .foregroundStyle(Theme.textDim)
                        .lineSpacing(13.5 * 1.45 - 13.5 * 1.2)
                        .fixedSize(horizontal: false, vertical: true)
                }

                if let remove {
                    Button("Remove from this run", action: remove)
                        .font(WCFont.sans(12.5))
                        .foregroundStyle(Theme.meta)
                        .buttonStyle(.plain)
                        .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                }
            }

            ScoreColumn(score: state.displayScore(card))
        }
        .padding(.top, Metrics.rowTopPadding)
        .padding(.bottom, Metrics.rowBottomPadding)
        .overlay(alignment: .top) { Hairline() }
    }
}
