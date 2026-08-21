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

            planNavigation
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
    // One label, one title, one position line. Flexible plans are ongoing, so a
    // forecast does not earn permanent space in the everyday header.

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Back")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 5) {
                MetaText(text: "STUDY PLAN", font: WCFont.mono(10),
                         tracking: 1.2, color: Theme.meta)

                if let overview = plan.overview {
                    Text(overview.subject)
                        .font(TypeRole.screenTitle)
                        .tracking(-0.6)
                        .foregroundStyle(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    MetaText(text: overview.positionLine, font: WCFont.mono(11.5),
                             tracking: 0.35, color: Theme.metaAlt)
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
            ForEach(Array(overview.phases.enumerated()), id: \.element.id) { offset, phase in
                PhaseRow(
                    phase: phase,
                    isOpen: plan.openPhase == phase.index,
                    isFirst: offset == overview.phases.startIndex,
                    isLast: offset == overview.phases.count - 1,
                    onToggle: {
                        withAnimation(Motion.fadeFast) { plan.togglePhase(phase.index) }
                    },
                    onWeek: { index in
                        state.path.append(.planWeek(planID, index))
                    }
                )
            }

        }
    }

    private var planNavigation: some View {
        VStack(spacing: 0) {
            Hairline()
            let currentWeek = plan.overview?.weekIndex ?? 1
            PlanNavigationPill(
                selection: .timeline,
                weekLabel: "Week \(currentWeek)",
                onWeek: { state.path.append(.planWeek(planID, currentWeek)) },
                onTimeline: {},
                onPlans: { state.sheet = .plans },
                onUpdates: { state.path.append(.planUpdates(planID)) }
            )
            .padding(.horizontal, Metrics.screenPadding)
            .padding(.top, 10)
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
    let isFirst: Bool
    let isLast: Bool
    let onToggle: () -> Void
    let onWeek: (Int) -> Void

    private var isCurrent: Bool { phase.status == "Current" }
    private var isContained: Bool { isCurrent && isOpen }

    // The header geometry is fixed: 1pt rule, 11pt top inset, 6pt node inset,
    // then half of the 7pt node. Local rail segments meet at each row boundary,
    // making one uninterrupted line while still stopping at the first and last
    // phase nodes.
    private static let nodeCenterY: CGFloat = 21.5
    private static let nodeCenterX: CGFloat = 3.5

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if isContained {
                Color.clear
                    .frame(maxWidth: .infinity)
                    .frame(height: 1)
            } else {
                Hairline()
            }
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
                                .fixedSize(horizontal: false, vertical: true)
                            // Status is a word first. The colour only reinforces it.
                            Text(phase.status)
                                .font(WCFont.sans(13))
                                .foregroundStyle(
                                    isCurrent ? Theme.accentChipNote : Theme.textMuted
                                )
                                .fixedSize()
                        }
                        MetaText(text: phase.rangeLine, font: WCFont.mono(10.5),
                                 tracking: 0.6, color: Theme.metaDim)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: isOpen ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.metaFaint)
                        .accessibilityHidden(true)
                }
                .padding(.vertical, 11)
                .padding(.trailing, isContained ? 14 : 0)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)
            // The visual title is compressed; the accessible name is not.
            .accessibilityLabel(phase.accessibleLabel)
            .accessibilityValue(isOpen ? "Expanded" : "Collapsed")
            .accessibilityHint(isOpen ? "Collapses this phase." : "Expands this phase.")
            .accessibilityAddTraits(.isHeader)

            if isOpen {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(phase.weeks) { week in
                        WeekRow(week: week) { onWeek(week.index) }
                    }
                }
                .padding(.leading, 19)
                .padding(.trailing, isContained ? 14 : 0)
                .padding(.bottom, isContained ? 12 : 6)
                .wcFade(Motion.fadeFast)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .topLeading) {
            ZStack(alignment: .topLeading) {
                if isContained {
                    RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                        .fill(Theme.surface.opacity(0.55))
                        .overlay(
                            RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                                .strokeBorder(Theme.hairline, lineWidth: 1)
                        )
                        // Leave the timeline itself outside the panel. The
                        // panel contains the current phase's task detail only.
                        .padding(.leading, 7)
                        .padding(.vertical, 6)
                }

                GeometryReader { proxy in
                    Path { path in
                        let startY = isFirst ? Self.nodeCenterY : 0
                        let endY = isLast ? Self.nodeCenterY : proxy.size.height
                        path.move(to: CGPoint(x: Self.nodeCenterX, y: startY))
                        path.addLine(to: CGPoint(x: Self.nodeCenterX, y: endY))
                    }
                    .stroke(Theme.hairline, lineWidth: 1)
                }
                .accessibilityHidden(true)
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
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                Text(week.status)
                    .font(WCFont.sans(12.5))
                    .foregroundStyle(Theme.textDim)
                    .fixedSize()
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
