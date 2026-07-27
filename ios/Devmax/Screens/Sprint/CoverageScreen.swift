import SwiftUI

/// A standing, category-grouped view of mastery across the whole library — for
/// deciding where the study guide needs more cards, fewer cards, or rebalancing.
/// Not a daily habit screen.
///
/// **Read-only.** It surfaces the gap, it doesn't fix it: card authoring stays in
/// Quick Add or the seed data. Reached only from Review Sprint Setup, which is
/// the moment someone is already thinking in category gaps.
struct CoverageScreen: View {
    @EnvironmentObject private var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch state.libraryLoad {
                    case .loading: CoverageSkeleton()
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

    /// One mono line: `MECHANISM 4.1 · TRADE-OFFS 2.8 · FAILURE MODES 3.2`.
    ///
    /// No bars, no colour, not tappable. Hidden until something has been scored —
    /// three axes at 0.0 would read as a finding rather than as no data.
    @ViewBuilder
    private var axisRollup: some View {
        let parts = state.axisRollup
        if !parts.isEmpty {
            FlowLayout(horizontalSpacing: 6, verticalSpacing: 2) {
                ForEach(Array(parts.enumerated()), id: \.offset) { index, text in
                    MetaText(
                        text: index < parts.count - 1 ? "\(text) ·" : text,
                        font: WCFont.mono(10), tracking: 0.4, color: Theme.metaDim
                    )
                    .fixedSize()
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

            MetaText(text: "TAP A TIER TO LIST ITS CARDS · READ ONLY",
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
            let count = cards.filter { ScoreStyle.Tier.of($0.lastScore) == tier }.count
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

    /// Today's mastery-band idiom, re-sliced by category. The open segment
    /// switches from `#7c848b` to its tier colour with a 1px underline.
    private var tierLine: some View {
        FlowLayout(horizontalSpacing: 7, verticalSpacing: 3) {
            ForEach(Array(tiers.enumerated()), id: \.element.tier) { index, entry in
                let open = openTier == entry.tier
                Button {
                    withAnimation(Motion.fadeFast) {
                        state.covOpen = open
                            ? nil
                            : AppState.OpenTier(category: category, tier: entry.tier)
                    }
                } label: {
                    VStack(spacing: 1) {
                        Text("\(entry.count) \(entry.tier.rawValue)\(index < tiers.count - 1 ? " ·" : "")")
                            .font(WCFont.mono(11))
                            .tracking(0.33)
                            .foregroundStyle(open ? entry.tier.color : Theme.metaAlt)
                        Rectangle()
                            .fill(open ? entry.tier.color : .clear)
                            .frame(height: 1)
                    }
                    .fixedSize()
                }
                .buttonStyle(.plain)
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

/// The same static skeleton as Review Sprint Setup, without the score column.
struct CoverageSkeleton: View {
    private let widths: [[CGFloat]] = [[0.58, 0.84], [0.46, 0.72], [0.63, 0.79]]

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(0..<3, id: \.self) { row in
                GeometryReader { geo in
                    VStack(alignment: .leading, spacing: 9) {
                        RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton1)
                            .frame(width: geo.size.width * widths[row][0], height: 12)
                        RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton2)
                            .frame(width: geo.size.width * widths[row][1], height: 10)
                    }
                }
                .frame(height: 31)
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
