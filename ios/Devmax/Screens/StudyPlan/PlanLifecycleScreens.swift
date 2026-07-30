import SwiftUI

/// Four lifecycle states, listed separately.
///
/// Completed is not archived, and both are groups of their own — one means the
/// work was finished and the other means it was filed. Zero active plans is a
/// valid state the sheet says out loud rather than hiding.
struct PlansSheet: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        SheetChrome(title: "Plans", height: 520) {
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.plansLoad {
                    case .loading, .idle:
                        LoadingList(label: "LOADING PLANS", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await plan.loadPlans() } }
                    case .ready:
                        if let plans = plan.plans { groups(plans) }
                    }

                    MetaText(
                        text: "AT MOST ONE ACTIVE PLAN · ZERO IS FINE · DUPLICATE COPIES "
                            + "GUIDE PROVENANCE, PHASES, ITEMS, DEPENDENCIES, ESTIMATES, "
                            + "AND YOUR EDITS · IT RESETS COMPLETION, DATES, AND REMINDERS "
                            + "· IT COPIES NO CARDS, SCORES, SESSIONS, SM-2 STATE, OR PLAN "
                            + "HISTORY",
                        font: WCFont.mono(9.5), tracking: 0.6, color: Theme.metaFaint
                    )
                    .padding(.top, 16)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .task { await plan.loadPlans() }
    }

    @ViewBuilder
    private func groups(_ plans: PlanList) -> some View {
        ForEach(plans.groups, id: \.label) { group in
            MetaText(text: group.label, font: WCFont.mono(10), tracking: 1.2,
                     color: Theme.meta)
                .padding(.top, 14)
                .padding(.bottom, 2)
                .accessibilityAddTraits(.isHeader)

            if group.entries.isEmpty {
                Text(
                    group.label == "ACTIVE"
                        ? "No active plan. Today shows PLAN · ADD A STUDY GUIDE until you "
                            + "make one active."
                        : "None."
                )
                .font(WCFont.sans(13))
                .foregroundStyle(Theme.textDim)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 8)
            } else {
                ForEach(group.entries) { entry in
                    Hairline()
                    PlanEntryRow(entry: entry) {
                        state.sheet = nil
                        state.path.append(.planOverview(entry.id))
                    } onAction: { action in
                        Task { await plan.lifecycle(action, planID: entry.id) }
                    }
                }
                Hairline()
            }
        }
    }
}

private struct PlanEntryRow: View {
    let entry: PlanListEntry
    let onOpen: () -> Void
    let onAction: (String) -> Void

    /// One action per row, and it is the one that state affords: a paused plan
    /// can be made active, a finished one can be duplicated.
    private var action: (label: String, key: String)? {
        switch entry.status {
        case "paused": return ("Make active", "activate")
        case "completed", "archived": return ("Duplicate", "duplicate")
        default: return nil
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button(action: onOpen) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(entry.title)
                        .font(WCFont.sans(15, weight: 500))
                        .foregroundStyle(
                            entry.status == "archived" ? Theme.textMuted : Theme.text
                        )
                        .multilineTextAlignment(.leading)
                    MetaText(text: entry.meta.uppercased(), font: WCFont.mono(10),
                             tracking: 0.6, color: Theme.metaFaint)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("\(entry.title). \(entry.meta). Opens this plan.")

            if let action {
                Button { onAction(action.key) } label: {
                    Text(action.label)
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.accent)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(action.label) · \(entry.title)")
            }
        }
        .padding(.vertical, 11)
        .frame(minHeight: Metrics.minTapTarget)
    }
}

/// Plan updates. Every material schedule change, newest first.
struct PlanUpdatesScreen: View {
    let planID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            VStack(alignment: .leading, spacing: 0) {
                Button { state.path.removeLast() } label: {
                    Text("← Study plan")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                Text("Plan updates")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .padding(.top, 4)
                    .padding(.bottom, 14)
                    .accessibilityAddTraits(.isHeader)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Metrics.screenPadding)

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(plan.revisions) { revision in
                        Hairline()
                        VStack(alignment: .leading, spacing: 3) {
                            Text(revision.summary)
                                .font(WCFont.sans(14.5))
                                .foregroundStyle(Theme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            MetaText(
                                text: "\(revision.kind.uppercased()) · "
                                    + Self.stamp.string(from: revision.createdAt).uppercased(),
                                font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                            )
                        }
                        .padding(.vertical, 12)
                        .accessibilityElement(children: .combine)
                    }
                    Hairline()

                    MetaText(
                        text: "PLAN CHANGES ONLY · YOUR CARDS, SCORES, AND REVIEW "
                            + "SCHEDULE ARE NOT LISTED HERE BECAUSE STUDY PLAN NEVER "
                            + "CHANGES THEM",
                        font: WCFont.mono(9.5), tracking: 0.6, color: Theme.metaFaint
                    )
                    .padding(.top, 14)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { await plan.loadRevisions(planID) }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            if let error = plan.applyError {
                InlineNotice {
                    Text(error)
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.text)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.horizontal, Metrics.screenPadding)
            }
            HStack(spacing: 8) {
                SecondaryButton(title: "Pause", fillsWidth: false) {
                    Task { await plan.lifecycle("pause", planID: planID) }
                }
                SecondaryButton(title: "Complete", fillsWidth: false) {
                    Task { await plan.lifecycle("complete", planID: planID) }
                }
                SecondaryButton(title: "Archive", fillsWidth: false) {
                    Task {
                        guard await plan.lifecycle("archive", planID: planID) else { return }
                        state.path.removeAll()
                        state.sheet = .plans
                    }
                }
                SecondaryButton(title: "Duplicate", fillsWidth: false) {
                    Task { await plan.lifecycle("duplicate", planID: planID) }
                }
            }
            .disabled(plan.lifecycleBusy)
            .opacity(plan.lifecycleBusy ? 0.55 : 1)
            .padding(.horizontal, Metrics.screenPadding)
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    private static let stamp: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "d MMM"
        return f
    }()
}

/// The completed-plan recap. Plan work only.
///
/// Estimated, not measured — Study Plan does not track actual study time — and
/// global reviews are deliberately absent, because they were never part of this
/// plan's capacity in the first place.
struct PlanRecapScreen: View {
    let planID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            VStack(alignment: .leading, spacing: 0) {
                Button { state.path.removeLast() } label: {
                    Text("← Plans")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                VStack(alignment: .leading, spacing: 5) {
                    Text("Plan complete")
                        .font(TypeRole.screenTitle)
                        .tracking(-0.6)
                        .foregroundStyle(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    if let recap = plan.recap {
                        Text(recap.title)
                            .font(WCFont.sans(15))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
                .padding(.top, 4)
                .padding(.bottom, 14)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Metrics.screenPadding)

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let recap = plan.recap { stats(recap) }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task { await plan.loadRecap(planID) }
    }

    @ViewBuilder
    private func stats(_ recap: PlanRecap) -> some View {
        Block(label: "CORE WORK") {
            Text("\(recap.coreComplete) of \(recap.coreTotal) completed")
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.text)
        }
        Block(label: "OPTIONAL WORK") {
            Text("\(recap.optionalDeferred) deferred")
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
        }
        Block(label: "PLANNED STUDY") {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(PlanFormat.hours(recap.estimatedMinutes)) estimated in total")
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.text)
                Text(
                    "\(PlanFormat.hours(recap.learnPracticeMinutes)) Learn and Practice · "
                        + "\(PlanFormat.hours(recap.retrievalMinutes)) plan retrieval"
                )
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textSecondary)
                Text("Estimated, not measured. Global reviews aren't counted here.")
                    .font(WCFont.sans(13))
                    .foregroundStyle(Theme.textDim)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        Block(label: "RETRIEVAL & PRACTICE") {
            VStack(alignment: .leading, spacing: 3) {
                Text("\(recap.retrievalCompleted) closed-book recalls")
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                Text("\(recap.practiceCompleted) practice sets")
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
            }
        }
        if !recap.remainingGaps.isEmpty {
            Block(label: "REMAINING GAPS") {
                VStack(alignment: .leading, spacing: 3) {
                    ForEach(recap.remainingGaps, id: \.self) { gap in
                        Text(gap)
                            .font(WCFont.sans(14))
                            .foregroundStyle(Theme.textSecondary)
                    }
                }
            }
        }
    }
}

/// The three audits and source coverage, behind one screen.
///
/// They are the same shape — a list of things the import is unsure about, each
/// with what it read and what it decided — so they are one screen with a
/// destination key rather than four near-identical ones.
struct PlanAuditScreen: View {
    let destination: String
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    /// One table rather than three parallel switches. The switch version had
    /// drifted: the server emits eight destinations and only five had a title,
    /// four had an explanation, so `deadline` and `titles` rendered the capacity
    /// copy under a "Review" heading.
    private static let copy: [String: (title: String, explanation: String)] = [
        "estimate-audit": (
            "Workload estimates",
            "These were estimated from the scope of the work rather than read from "
                + "your guide. Confirm them or edit the estimate."
        ),
        "retrieval-audit": (
            "Retrieval activities",
            "These retrieval activities were proposed by the import rather than found "
                + "in your guide. They consume plan capacity and don't block a week. "
                + "Approve the ones you want."
        ),
        "dependency-audit": (
            "Dependencies",
            "Only uncertain, contradictory, or schedule-changing links appear here. An "
                + "unconfirmed one uses the conservative order and says so."
        ),
        "source-coverage": (
            "Source coverage",
            "Parts of your guide the import couldn't place, and any quote whose link "
                + "back to the guide didn't resolve."
        ),
        "capacity": (
            "Capacity",
            "One or more weeks is over its capacity. Raise the weekly capacity, "
                + "lengthen the plan, or edit the estimates."
        ),
        "deadline": (
            "Deadline",
            "The plan would finish after the date you fixed. The deadline doesn't move "
                + "— shorten the plan, raise the weekly capacity, or reduce scope."
        ),
        "titles": (
            "Short titles",
            "Some generated week or phase labels are too long, too vague, or repeated "
                + "within a phase. Edit them before creating the plan."
        ),
    ]

    private var title: String { Self.copy[destination]?.title ?? "Review" }

    private var explanation: String {
        Self.copy[destination]?.explanation
            ?? "Review this before creating the plan."
    }

    private var check: PlanCheck? {
        plan.preview?.checks.first {
            $0.destination == destination || $0.key == destination
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            VStack(alignment: .leading, spacing: 0) {
                Button { state.path.removeLast() } label: {
                    Text("← Review plan")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

                VStack(alignment: .leading, spacing: 5) {
                    Text(title)
                        .font(TypeRole.screenTitle)
                        .tracking(-0.6)
                        .foregroundStyle(Theme.text)
                        .accessibilityAddTraits(.isHeader)
                    if let check {
                        MetaText(text: check.value.uppercased(), font: WCFont.mono(11),
                                 tracking: 0.6, color: Theme.scoreMid)
                    }
                }
                .padding(.top, 4)
                .padding(.bottom, 14)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, Metrics.screenPadding)

            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(explanation)
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textSecondary)
                        .lineSpacing(15 * 1.5 - 15 * 1.2)
                        .fixedSize(horizontal: false, vertical: true)

                    if let detail = check?.detail, !detail.isEmpty {
                        Text(detail)
                            .font(WCFont.sans(13.5))
                            .foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    MetaText(
                        text: "CONFIRMING HERE CHANGES THE PLAN THAT WILL BE CREATED · "
                            + "NOTHING EXISTS YET",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                    )
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }

            VStack(spacing: 10) {
                Hairline()
                PrimaryButton(title: "Accept these") {
                    Task {
                        await plan.resolveCheck(check?.key ?? destination)
                        state.path.removeLast()
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
            }
            .padding(.bottom, Metrics.bottomSafeArea)
            .background(Theme.bg)
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
    }
}
