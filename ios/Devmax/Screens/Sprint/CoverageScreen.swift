import SwiftUI

/// A standing, category-grouped view of mastery across the whole library — for
/// deciding where the study guide needs more cards, fewer cards, or rebalancing.
/// Not a daily habit screen.
///
/// Category tiers remain read-only. The weakest global depth axis can start a
/// Practice-mode repair run; it never changes SM-2.
struct CoverageScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch state.libraryLoad {
                    case .loading:
                        LoadingList(
                            label: "LOADING CARDS", inset: 0,
                            showsScoreColumn: false, separator: .overlay
                        )
                    case .error: LoadFailure { Task { await state.loadLibrary() } }
                    case .ready: sections
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Review sprint")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 6) {
                Text("Coverage")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                MetaText(text: state.coverageStatus, font: WCFont.mono(11.5),
                         tracking: 0.35, color: Theme.metaAlt)
                axisRollup
            }
            .padding(.top, 4)
            .padding(.bottom, 14)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    /// One mono line: `ACCURACY 4.1 · DEPTH 2.8 · BOUNDARIES 3.2`.
    ///
    /// No bars or colour. Only the weakest depth axis is actionable; mechanism
    /// stays under normal scheduling rather than getting a second practice path.
    @ViewBuilder
    private var axisRollup: some View {
        let items = state.axisRollup
        if !items.isEmpty {
            FlowLayout(horizontalSpacing: 6, verticalSpacing: 2) {
                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    if item.isActionable, let axis = item.depthAxis {
                        Button { state.enterDepthRepair(axis) } label: {
                            MetaText(
                                text: "\(item.text) →\(index < items.count - 1 ? " ·" : "")",
                                font: WCFont.mono(10), tracking: 0.4, color: Theme.meta
                            )
                            .fixedSize()
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("\(item.text). Start depth repair.")
                    } else {
                        MetaText(
                            text: index < items.count - 1 ? "\(item.text) ·" : item.text,
                            font: WCFont.mono(10), tracking: 0.4, color: Theme.metaDim
                        )
                        .fixedSize()
                    }
                }
            }
            .padding(.top, 1)
        }
    }

    private var sections: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(state.coverageSections, id: \.category) { section in
                CoverageSection(category: section.category, cards: section.cards)
            }

            MetaText(text: "TAP A TIER TO LIST ITS CARDS",
                     font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint)
                .padding(.top, 16)
                .overlay(alignment: .top) { Hairline() }
        }
    }
}

private struct CoverageSection: View {
    @EnvironmentObject private var state: AppState
    let category: String
    let cards: [CardSummary]

    /// Tiers present here, in fixed order, zero-count tiers omitted.
    private var tiers: [(tier: ScoreStyle.Tier, count: Int)] {
        ScoreStyle.Tier.allCases.compactMap { tier in
            let count = AppState.tally(cards, [tier])
            return count > 0 ? (tier, count) : nil
        }
    }

    private var openTier: ScoreStyle.Tier? {
        state.covOpen?.category == category ? state.covOpen?.tier : nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(category)
                    .font(TypeRole.rowTopic)
                    .tracking(-0.165)
                    .foregroundStyle(Theme.text)
                Spacer(minLength: 0)
                MetaText(text: "\(cards.count) CARD\(cards.count == 1 ? "" : "S")",
                         font: WCFont.mono(10), tracking: 1.0, color: Theme.metaDim)
            }

            tierLine

            if let openTier {
                VStack(alignment: .leading, spacing: 9) {
                    ForEach(cards.filter { ScoreStyle.Tier.of($0.lastScore) == openTier }) { card in
                        cardRow(card)
                    }
                }
                .padding(.top, 6)
                .wcFade(Motion.fadeFast)
            }
        }
        .padding(.top, 14)
        .padding(.bottom, 15)
        .overlay(alignment: .top) { Hairline() }
    }

    /// Today's mastery-band line, re-sliced by category — the same control, one
    /// tier open at a time across the whole screen.
    private var tierLine: some View {
        CountSegments(
            segments: tiers.map { entry in
                CountSegments.Segment(
                    id: entry.tier.rawValue,
                    text: "\(entry.count) \(entry.tier.rawValue)",
                    color: entry.tier.color,
                    isActive: openTier == entry.tier
                )
            }
        ) { segment in
            guard let tier = ScoreStyle.Tier(rawValue: segment.id) else { return }
            withAnimation(Motion.fadeFast) {
                state.covOpen = segment.isActive
                    ? nil
                    : AppState.OpenTier(category: category, tier: tier)
            }
        }
    }

    private func cardRow(_ card: CardSummary) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(card.topic)
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                MetaText(text: note(for: card), font: WCFont.mono(9.5),
                         tracking: 0.76, color: Theme.metaFaint)
            }
            Spacer(minLength: 0)
            Text(ScoreStyle.label(for: card.lastScore))
                .font(WCFont.sans(14, weight: 600))
                .monospacedDigit()
                .foregroundStyle(ScoreStyle.color(for: card.lastScore))
        }
    }

    /// Days since the card was last answered, or its due label if it's in the
    /// queue and has never been answered.
    private func note(for card: CardSummary) -> String {
        guard let days = card.daysSinceReview else { return card.dueLabel.uppercased() }
        return "\(days)D SINCE REVIEW"
    }
}
