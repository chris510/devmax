import SwiftUI

/// "Where am I in the journey?"
///
/// The density budget is the specification here, not a nice-to-have: at default
/// type on a 390×844 screen every phase header must be visible without
/// scrolling, and a collapsed phase is at most two lines. That is why there is
/// no NOW card, no phase description, no dependency prose, no internal id, and
/// exactly one forecast — each of those was on this screen in V3.4 and each was
/// removed because the map should be readable before any part of it is.
struct PlanOverviewScreen: View {
    let planID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.overviewLoad {
                    case .loading, .idle:
                        LoadingList(label: "LOADING PLAN", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await plan.loadOverview(planID) } }
                    case .ready:
                        if let overview = plan.overview { timeline(overview) }
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
        // Only fetch when this screen does not already hold this plan. `.task`
        // fires again on every pop back from Week detail, and the mutating paths
        // refresh the overview themselves — so without the guard, walking down
        // and back re-fetched a plan that had not changed.
        .task {
            if plan.overview?.id != planID { await plan.loadOverview(planID) }
        }
    }

    // MARK: - Header
    //
    // Four lines and no more: what this is, which plan, where you are, and when
    // it ends. That is the complete answer set for the five questions the
    // overview exists to answer.

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Today")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 5) {
                Text("Study plan")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)

                if let overview = plan.overview {
                    Text(overview.subject)
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textSecondary)
                    MetaText(text: overview.positionLine, font: WCFont.mono(11.5),
                             tracking: 0.35, color: Theme.metaAlt)
                    // The single forecast. Plan-week precision, never a day.
                    Text(overview.forecastLabel)
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textMuted)
                }
            }
            .padding(.top, 4)
            .padding(.bottom, 14)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    // MARK: - The map

    private func timeline(_ overview: PlanOverview) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(overview.phases) { phase in
                PhaseRow(
                    phase: phase,
                    isOpen: plan.openPhase == phase.index,
                    onToggle: {
                        withAnimation(Motion.fadeFast) { plan.togglePhase(phase.index) }
                    },
                    onWeek: { index in
                        state.path.append(.planWeek(planID, index))
                    }
                )
            }

            if let change = overview.latestChange {
                Button { state.path.append(.planUpdates(planID)) } label: {
                    MetaText(text: change.uppercased(), font: WCFont.mono(10),
                             tracking: 0.6, color: Theme.metaFaint)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                .padding(.top, 10)
                .accessibilityLabel("Latest change: \(change). Opens plan updates.")
            }
        }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            HStack(spacing: 10) {
                SecondaryButton(title: "Plans") { state.sheet = .plans }
                SecondaryButton(title: "Updates") { state.path.append(.planUpdates(planID)) }
            }
            .padding(.horizontal, Metrics.screenPadding)
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

/// Two lines collapsed: name and status, then the week range. Nothing else fits
/// the budget, and nothing else answers a question the overview is asked.
private struct PhaseRow: View {
    let phase: PlanPhaseRow
    let isOpen: Bool
    let onToggle: () -> Void
    let onWeek: (Int) -> Void

    private var isCurrent: Bool { phase.status == "Current" }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Hairline()
            Button(action: onToggle) {
                HStack(alignment: .top, spacing: 12) {
                    // The rail is decoration; the row carries the meaning.
                    Circle()
                        .fill(isCurrent ? Theme.accent : Theme.borderStrong)
                        .frame(width: 7, height: 7)
                        .padding(.top, 6)
                        .accessibilityHidden(true)

                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(phase.numberedTitle)
                                .font(WCFont.sans(16.5, weight: 500))
                                .foregroundStyle(Theme.text)
                            // Status is a word first. The colour only reinforces it.
                            Text(phase.status)
                                .font(WCFont.sans(13))
                                .foregroundStyle(
                                    isCurrent ? Theme.accentChipNote : Theme.textMuted
                                )
                        }
                        MetaText(text: phase.rangeLine, font: WCFont.mono(10.5),
                                 tracking: 0.6, color: Theme.metaDim)
                    }
                    Spacer(minLength: 0)
                    Text(isOpen ? "▲" : "▼")
                        .font(WCFont.mono(9))
                        .foregroundStyle(Theme.metaFaint)
                        .accessibilityHidden(true)
                }
                .padding(.vertical, 11)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)
            // The visual title is compressed; the accessible name is not.
            .accessibilityLabel(phase.accessibleLabel)
            .accessibilityHint(isOpen ? "Collapses this phase." : "Expands this phase.")
            .accessibilityAddTraits(isOpen ? [.isHeader, .isSelected] : .isHeader)

            if isOpen {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(phase.weeks) { week in
                        WeekRow(week: week) { onWeek(week.index) }
                    }
                }
                .padding(.leading, 19)
                .padding(.bottom, 6)
                .wcFade(Motion.fadeFast)
            }
        }
    }
}

/// One line: week number, concise title, status. No description, no estimate,
/// no item count — a count only earns a place when it signals a problem.
private struct WeekRow: View {
    let week: PlanWeekRow
    let onTap: () -> Void

    var body: some View {
        Button(action: onTap) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                MetaText(text: "WEEK \(week.index)", font: WCFont.mono(10),
                         tracking: 0.7,
                         color: week.isCurrent ? Theme.accentChipNote : Theme.metaFaint)
                    .frame(width: 52, alignment: .leading)
                Text(week.displayTitle)
                    .font(WCFont.sans(14.5))
                    .foregroundStyle(week.isCurrent ? Theme.text : Theme.textSecondary)
                Spacer(minLength: 0)
                Text(week.status)
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.textDim)
            }
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .frame(minHeight: Metrics.minTapTarget)
        // The full guide title, not the compressed one.
        .accessibilityLabel(
            "Week \(week.index). \(week.fullTitle). \(week.status). Opens week detail."
        )
    }
}
