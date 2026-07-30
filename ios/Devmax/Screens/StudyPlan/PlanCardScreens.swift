import SwiftUI

/// Card suggestions from a completed plan item.
///
/// Two sections and no bridge between them. A candidate that failed any one of
/// the five gate questions appears under NOT SUGGESTED with the question it
/// failed and why — and with no action at all. There is no "add back", because
/// a gate with an override is a suggestion, and the whole point of the gate is
/// that the review budget is scarce.
struct PlanCardsScreen: View {
    let planID: UUID
    let itemID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.cardLoad {
                    case .loading, .idle:
                        LoadingList(label: "CHECKING THE GATE", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await plan.loadCardProposals(generate: true) } }
                    case .ready:
                        if let cards = plan.cards { content(cards) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 16)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task {
            if plan.cards == nil { await plan.loadCardProposals(generate: true) }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Item")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 5) {
                Text(plan.cards?.headline ?? "Recall cards")
                    .font(WCFont.sans(23, weight: 600))
                    .tracking(-0.4)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                if let cards = plan.cards {
                    Text(
                        cards.supportsRecallCards
                            ? "Nothing is created until you add it."
                            : cards.note
                    )
                    .font(WCFont.sans(14.5))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(.top, 4)
            .padding(.bottom, 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    @ViewBuilder
    private func content(_ cards: CardProposalList) -> some View {
        if let error = plan.cardError {
            InlineNotice {
                VStack(alignment: .leading, spacing: 4) {
                    Text(error)
                        .font(WCFont.sans(14.5))
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    MetaText(
                        text: "NO CARD ROWS AND NO ACCEPTANCE RECORD WERE COMMITTED",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                    )
                }
            }
            // `role="alert"` in the prototype: a failure the user did not ask for
            // has to announce itself.
            .accessibilityAddTraits(.isStaticText)
            .accessibilityLabel("Error. \(error)")
            .padding(.bottom, 12)
        }

        ForEach(cards.suggested) { proposal in
            ProposalCard(
                proposal: proposal,
                selected: plan.selectedProposals.contains(proposal.id),
                added: plan.addedProposals.contains(proposal.id),
                gateOpen: plan.expandedGate == proposal.id,
                onToggleSelect: { plan.toggleProposal(proposal.id) },
                onToggleGate: {
                    withAnimation(Motion.fadeFast) {
                        plan.expandedGate =
                            plan.expandedGate == proposal.id ? nil : proposal.id
                    }
                },
                onResolve: { action in
                    Task { await plan.resolveOverlap(proposal.id, action: action) }
                }
            )
        }

        if !cards.notSuggested.isEmpty {
            MetaText(text: "NOT SUGGESTED", font: WCFont.mono(10), tracking: 1.2,
                     color: Theme.meta)
                .padding(.top, 22)
                .padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)

            ForEach(cards.notSuggested) { proposal in
                Hairline()
                VStack(alignment: .leading, spacing: 4) {
                    Text(proposal.topic)
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textMuted)
                    MetaText(text: proposal.failureLine, font: WCFont.mono(10),
                             tracking: 0.6, color: Theme.scoreMid)
                    if let failed = proposal.gate.first(where: { !$0.passed }) {
                        Text(failed.reason)
                            .font(WCFont.sans(13))
                            .foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(.vertical, 12)
                // Deliberately not a button. There is nothing to do here.
                .accessibilityElement(children: .combine)
            }
            Hairline()
            MetaText(
                text: "A FAILED CANDIDATE IS NEVER SELECTABLE AND NEVER COUNTED",
                font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
            )
            .padding(.top, 10)
        }

        if cards.supportsRecallCards {
            MetaText(text: cards.note.uppercased(), font: WCFont.mono(10),
                     tracking: 0.6, color: Theme.metaFaint)
                .padding(.top, 18)
        }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            HStack(spacing: 10) {
                SecondaryButton(title: "Not now") { state.path.removeLast() }
                if plan.cards?.supportsRecallCards == true, !plan.selectedProposals.isEmpty {
                    PrimaryButton(
                        title: plan.addingCards ? "Adding…" : plan.addLabel,
                        enabled: !plan.addingCards
                    ) {
                        Task { await plan.addSelectedCards() }
                    }
                }
            }
            .padding(.horizontal, Metrics.screenPadding)
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

private struct ProposalCard: View {
    let proposal: CardProposal
    let selected: Bool
    let added: Bool
    let gateOpen: Bool
    let onToggleSelect: () -> Void
    let onToggleGate: () -> Void
    let onResolve: (String) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Hairline()
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(proposal.topic)
                        .font(WCFont.sans(16.5, weight: 500))
                        .foregroundStyle(Theme.text)
                    Spacer(minLength: 8)
                    // ADDED renders only after a committed response.
                    MetaText(
                        text: added ? "ADDED"
                            : proposal.isPossibleOverlap ? "POSSIBLE EXISTING CARD"
                            : selected ? "SELECTED" : "NOT SELECTED",
                        font: WCFont.mono(10), tracking: 0.6,
                        color: added ? Theme.scoreHigh
                            : proposal.isPossibleOverlap ? Theme.scoreMid
                            : selected ? Theme.accentChipNote : Theme.metaFaint
                    )
                }

                Text(proposal.canonicalQuestion)
                    .font(WCFont.serif(16))
                    .foregroundStyle(Theme.textSerif)
                    .lineSpacing(16 * 1.5 - 16 * 1.2)
                    .fixedSize(horizontal: false, vertical: true)

                if !proposal.reason.isEmpty {
                    Text(proposal.reason)
                        .font(WCFont.sans(13))
                        .foregroundStyle(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if !proposal.existingCardContext.isEmpty {
                    MetaText(text: proposal.existingCardContext.uppercased(),
                             font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint)
                }

                Button(action: onToggleGate) {
                    HStack(spacing: 6) {
                        MetaText(text: proposal.gateLine, font: WCFont.mono(10),
                                 tracking: 0.6, color: Theme.metaDim)
                        Text(gateOpen ? "▲" : "▼")
                            .font(WCFont.mono(9))
                            .foregroundStyle(Theme.metaFaint)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                .accessibilityLabel(proposal.gateLine)
                .accessibilityHint(gateOpen ? "Collapses the gate." : "Expands the gate.")

                if gateOpen {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(proposal.gate) { result in
                            Text("Q\(result.questionIndex) · \(result.reason)")
                                .font(WCFont.sans(12.5))
                                .foregroundStyle(
                                    result.passed ? Theme.textDim : Theme.scoreMid
                                )
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    .wcFade(Motion.fadeFast)
                }

                if added {
                    EmptyView()
                } else if proposal.isPossibleOverlap {
                    // Never decided silently. Four choices, all the user's.
                    HStack(spacing: 8) {
                        SecondaryButton(title: "Keep the new one", fillsWidth: false) {
                            onResolve("keep_new")
                        }
                        SecondaryButton(title: "Use existing", fillsWidth: false) {
                            onResolve("use_existing")
                        }
                        SecondaryButton(title: "Skip", fillsWidth: false) {
                            onResolve("skip")
                        }
                    }
                } else {
                    SecondaryButton(
                        title: selected ? "Deselect" : "Select", fillsWidth: false
                    ) { onToggleSelect() }
                }
            }
            .padding(.vertical, 14)
        }
    }
}
