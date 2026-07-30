import SwiftUI

/// Every screen that asks "what changes if I approve this".
///
/// These are deliberately the wordiest screens in the product. The map screens
/// were compressed because they answer "where am I"; a decision screen answers
/// "what will this do to my plan", and length is not the constraint on informed
/// consent. The structure is fixed: outcome first, then the arithmetic, then
/// what stays the same, then what Apply will do.
struct PlanProposalScreen: View {
    let planID: UUID
    /// `replan`, `reopen`, `capacity`, or `resume`. The proposal these produce
    /// has the same shape, so they are one screen; only which preview to fetch
    /// and which verb Apply carries differ.
    let kind: String

    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.proposalLoad {
                    case .loading, .idle:
                        LoadingList(label: "WORKING IT OUT", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await load() } }
                    case .ready:
                        if let proposal = plan.proposal { body(proposal) }
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
        .task { await load() }
    }

    private func load() async {
        switch kind {
        case "reopen": await plan.previewReopen()
        case "resume": await plan.previewResume(planID)
        default: await plan.previewReplan(planID)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Back")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    @ViewBuilder
    private func body(_ proposal: PlanProposal) -> some View {
        // Outcome first. Always.
        VStack(alignment: .leading, spacing: 8) {
            Text(proposal.headline)
                .font(WCFont.sans(23, weight: 600))
                .tracking(-0.4)
                .foregroundStyle(Theme.text)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text(proposal.body)
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(15 * 1.5 - 15 * 1.2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 4)
        .padding(.bottom, 6)

        // Then the arithmetic, week by week.
        if !proposal.affectedWeeks.isEmpty {
            Block(label: "AFFECTED WEEKS") {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(proposal.affectedWeeks) { week in
                        WeekChangeRow(week: week, moves: proposal.moves)
                    }
                }
            }
        }

        if !proposal.unresolved.isEmpty {
            Block(label: "NOWHERE TO GO") {
                VStack(alignment: .leading, spacing: 8) {
                    ForEach(proposal.unresolved) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("\(item.title) · \(item.minutes) min")
                                .font(WCFont.sans(14))
                                .foregroundStyle(Theme.text)
                            Text(item.explanation)
                                .font(WCFont.sans(13))
                                .foregroundStyle(Theme.textDim)
                        }
                    }
                    MetaText(
                        text: "\(proposal.unresolvedMinutes) MIN UNRESOLVED",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.scoreMid
                    )
                }
            }
        }

        Block(label: "FORECAST") {
            VStack(alignment: .leading, spacing: 4) {
                Text(proposal.forecastLabel)
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.text)
                if proposal.displacedMinutes > 0 {
                    Text(
                        "\(proposal.displacedMinutes) min displaced · "
                            + "\(proposal.unresolvedMinutes) unresolved"
                    )
                    .font(WCFont.sans(13))
                    .foregroundStyle(Theme.textSecondary)
                }
            }
        }

        // Then what does not change. Half the answer to the question the screen
        // is asking, and the half a user is most likely to be worried about.
        Block(label: "UNCHANGED") {
            VStack(alignment: .leading, spacing: 5) {
                ForEach(proposal.unchanged, id: \.self) { line in
                    Text(line)
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }

        if !proposal.canApply {
            Block(label: "WHY APPLY IS OFF") {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(proposal.blockingReasons, id: \.self) { reason in
                        Text(reason)
                            .font(WCFont.sans(13.5))
                            .foregroundStyle(Theme.scoreMid)
                    }
                }
            }
        }

        if let error = plan.applyError {
            InlineNotice {
                Text(error).font(WCFont.sans(14)).foregroundStyle(Theme.text)
            }
            .padding(.top, 16)
        }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            MetaText(
                text: plan.proposal?.canApply == true
                    ? "NOTHING CHANGES UNTIL YOU APPLY"
                    : "PICK ANOTHER OPTION OR EDIT THE PROPOSAL",
                font: WCFont.mono(10), tracking: 0.6,
                color: plan.proposal?.canApply == true ? Theme.metaFaint : Theme.scoreMid
            )
            .padding(.horizontal, Metrics.screenPadding)

            HStack(spacing: 10) {
                SecondaryButton(title: "Keep current plan") { state.path.removeLast() }
                PrimaryButton(
                    title: plan.applying ? "Applying…" : applyLabel,
                    // Native disabled. Nothing focusable carries a fake one.
                    enabled: plan.proposal?.canApply == true && !plan.applying
                ) {
                    Task {
                        let ok = kind == "reopen"
                            ? await plan.applyReopen()
                            : await plan.applyProposal(planID)
                        if ok { state.path.removeLast() }
                    }
                }
            }
            .padding(.horizontal, Metrics.screenPadding)
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    private var applyLabel: String {
        switch kind {
        case "reopen": return "Reopen"
        case "resume": return "Apply and resume"
        default: return "Apply adjustment"
        }
    }
}

private struct WeekChangeRow: View {
    let week: PlanWeekPlacement
    let moves: [PlanItemMove]

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Hairline()
            HStack(alignment: .firstTextBaseline) {
                MetaText(text: week.label, font: WCFont.mono(10), tracking: 0.6,
                         color: Theme.metaFaint)
                Spacer()
                MetaText(
                    text: week.fit, font: WCFont.mono(10.5), tracking: 0.6,
                    color: week.overCapacity ? Theme.scoreMid : Theme.text
                )
            }
            let landing = moves.filter { $0.toWeek == week.index }
            if !landing.isEmpty {
                Text(
                    landing.map { "\($0.title) (\($0.minutes) min)" }
                        .joined(separator: " · ")
                )
                .font(WCFont.sans(13))
                .foregroundStyle(Theme.textDim)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(.vertical, 9)
        .accessibilityElement(children: .combine)
    }
}

/// The capacity editor. A 44×44 stepper, a live readout, and a warning that says
/// what will happen rather than preventing it.
struct PlanCapacitySheet: View {
    let planID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        SheetChrome(title: "Week \(plan.capacityWeek) capacity", height: 400) {
            VStack(alignment: .leading, spacing: 18) {
                StepperControl(
                    value: "\(plan.capacityHours)h · \(plan.capacityHours * 60) min",
                    decrement: { plan.capacityHours = max(1, plan.capacityHours - 1) },
                    increment: { plan.capacityHours = min(40, plan.capacityHours + 1) }
                )
                .accessibilityLabel("Weekly capacity for week \(plan.capacityWeek)")

                Text(
                    plan.capacityHours * 60 < (plan.week?.plannedMinutes ?? 0)
                        ? "Lowering capacity may need work to carry forward. You'll "
                            + "review a proposal before anything changes."
                        : "No work needs to carry forward at this capacity."
                )
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                // Announced as it changes, because the readout above it is the
                // thing being adjusted.
                .accessibilityAddTraits(.updatesFrequently)

                MetaText(
                    text: "THIS WEEK ONLY · THE PLAN DEFAULT DOESN'T MOVE",
                    font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                )

                PrimaryButton(title: "Review the change") {
                    state.sheet = nil
                    Task {
                        await plan.previewCapacity(planID, hours: plan.capacityHours)
                        state.path.append(.planProposal(planID, "capacity"))
                    }
                }
            }
        }
    }
}
