import SwiftUI

/// "What exactly do I need to understand or produce?"
///
/// The one screen in the map that gets *more* explanatory as you go deeper, and
/// deliberately has no word limit. V3.5 changed exactly one thing here: the
/// internal item id left the metadata line. Everything else — Why this matters,
/// Done when, Source, Study block, Estimate, Retrieval support, Notes — stays.
struct PlanItemScreen: View {
    let planID: UUID
    let itemID: UUID
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @State private var editing = false

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch plan.itemLoad {
                    case .loading, .idle:
                        LoadingList(label: "LOADING ITEM", inset: 0, separator: .overlay)
                    case .error:
                        LoadFailure { Task { await plan.loadItem(planID, itemID: itemID) } }
                    case .ready:
                        if let item = plan.item { sections(item) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 16)
            }

            if let item = plan.item { bottomBlock(item) }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task {
            if plan.item?.id != itemID { await plan.loadItem(planID, itemID: itemID) }
        }
        .sheet(isPresented: $editing) {
            if let item = plan.item { PlanItemEditSheet(item: item) }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Week \(plan.item?.weekIndex ?? plan.week?.index ?? 1)")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            if let item = plan.item {
                VStack(alignment: .leading, spacing: 6) {
                    Text(item.fullTitle)
                        .font(WCFont.sans(23, weight: 600))
                        .tracking(-0.4)
                        .foregroundStyle(Theme.text)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityAddTraits(.isHeader)
                    // Phase, week, priority, state — and no internal id.
                    MetaText(text: item.metaLine, font: WCFont.mono(10.5),
                             tracking: 0.7, color: Theme.metaDim)
                }
                .padding(.top, 4)
                .padding(.bottom, 12)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    @ViewBuilder
    private func sections(_ item: PlanItemDetail) -> some View {
        if let error = plan.itemError {
            InlineNotice { Text(error).font(WCFont.sans(14)).foregroundStyle(Theme.text) }
                .padding(.bottom, 12)
        }

        if !item.blockedBy.isEmpty {
            Block(label: "AFTER") {
                Text(item.blockedBy.joined(separator: " · "))
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
            }
        }

        // Newsreader on the two editorial sections, per the handoff.
        if !item.whyItMatters.isEmpty {
            Block(label: "WHY THIS MATTERS") {
                Text(item.whyItMatters)
                    .font(WCFont.serif(17))
                    .foregroundStyle(Theme.textSerif)
                    .lineSpacing(17 * 1.5 - 17 * 1.2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        if !item.doneWhen.isEmpty {
            Block(label: "DONE WHEN") {
                Text(item.doneWhen)
                    .font(WCFont.serif(17))
                    .foregroundStyle(Theme.textSerif)
                    .lineSpacing(17 * 1.5 - 17 * 1.2)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        if !item.sourceExcerpt.isEmpty || !displaySourceLabel(item).isEmpty
            || !item.actionableResources.isEmpty {
            Block(label: "SOURCE") {
                VStack(alignment: .leading, spacing: 10) {
                    if item.actionableResources.isEmpty, !displaySourceLabel(item).isEmpty {
                        Text(displaySourceLabel(item))
                            .font(WCFont.sans(14))
                            .foregroundStyle(Theme.textSecondary)
                    }
                    ForEach(item.actionableResources) { resource in
                        resourceRow(resource)
                    }
                    if !item.sourceExcerpt.isEmpty {
                        Text("“\(item.sourceExcerpt)”")
                            .font(WCFont.sans(13.5))
                            .foregroundStyle(Theme.textDim)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
        }

        Block(label: "STUDY BLOCK") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text(
                        item.studyBlockLabel.isEmpty
                            ? "No study block" : item.studyBlockLabel
                    )
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    Spacer()
                    Toggle34(
                        isOn: Binding(
                            get: { item.studyBlockReminderOn },
                            set: { on in Task { await plan.setReminder(on: on) } }
                        )
                    )
                    .accessibilityLabel("Study block reminder")
                }
                MetaText(
                    text: "A LOCAL REMINDER · MISSING IT DOESN'T MOVE THE PLAN",
                    font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                )
            }
        }

        Block(label: "ESTIMATE") {
            Text(
                "\(item.estimateMinutes) min"
                    + (item.estimateSource == "user_edited" ? " · edited by you" : "")
            )
            .font(WCFont.sans(14))
            .foregroundStyle(Theme.textSecondary)
        }

        if let debrief = item.practiceDebrief, debrief.isSubmitted {
            Block(label: "DEBRIEF") {
                VStack(alignment: .leading, spacing: 8) {
                    Text(debrief.summary)
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                        .lineLimit(2)
                    MetaText(
                        text: debrief.submittedAt.map { "SAVED \($0.formatted(date: .abbreviated, time: .omitted).uppercased())" }
                            ?? "SAVED",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                    )
                }
            }
        } else if item.recallSupported, item.practiceDebriefEligible {
            Block(label: "DEBRIEF") {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Turn what broke down into focused recall cards.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                    Button {
                        plan.preparePracticeDebrief()
                        state.path.append(.practiceDebrief(planID, item.id, false))
                    } label: {
                        Text("Debrief practice →")
                            .font(TypeRole.secondaryAction)
                            .foregroundStyle(Theme.accent)
                    }
                    .buttonStyle(.plain)
                    .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                }
            }
        }

        unpromptedBlock(item)

        if !item.advisoryStretchActions.isEmpty {
            moreTimeBlock(item.advisoryStretchActions)
        }

        if !item.notes.isEmpty {
            Block(label: "NOTES") {
                Text(item.notes)
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func resourceRow(_ resource: PlanItemResource) -> some View {
        let title = resource.label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? resource.provider : resource.label
        let action = resource.actionLabel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "Open source" : resource.actionLabel

        return VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            if !resource.metaLine.isEmpty {
                MetaText(
                    text: resource.metaLine, font: WCFont.mono(10), tracking: 0.6,
                    color: Theme.metaDim, uppercased: true
                )
            }
            if let url = SafeExternalURL.parse(resource.url) {
                Link(destination: url) {
                    Text("\(action) ↗")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                        .underline()
                        .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                }
                .accessibilityLabel("\(action). \(title).")
                .accessibilityHint("Opens in your browser. Returning does not mark the item complete.")
            } else {
                MetaText(
                    text: "LINK UNAVAILABLE", font: WCFont.mono(10), tracking: 0.6,
                    color: Theme.metaFaint
                )
                .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                .accessibilityLabel("\(title). Link unavailable.")
            }
        }
    }

    private func displaySourceLabel(_ item: PlanItemDetail) -> String {
        let value = item.sourceLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.caseInsensitiveCompare("Curated first-party curriculum activity.") != .orderedSame,
              value.caseInsensitiveCompare("Curated first-party curriculum activity") != .orderedSame
        else { return "" }
        return value
    }

    private func unpromptedBlock(_ item: PlanItemDetail) -> some View {
        Block(label: "UNPROMPTED") {
            VStack(alignment: .leading, spacing: 8) {
                if !item.isComplete,
                   item.recallSupported || !item.recallMappings.isEmpty {
                    Text(
                        item.type == "practice"
                            ? "Finish the practice first. Then debrief a specific gap before choosing any recall card."
                            : "Finish the lesson first. Then review the mapped recall cards before they enter Today."
                    )
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    ForEach(item.recallMappings) { mapping in
                        mappedRecallRow(mapping, itemComplete: false)
                    }
                    MetaText(
                        text: "NOT READY · COMPLETE THE ITEM FIRST", font: WCFont.mono(10),
                        tracking: 0.6, color: Theme.metaFaint
                    )
                } else if !item.recallMappings.isEmpty {
                    Text("These concepts are mapped to delayed active recall.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                    ForEach(item.recallMappings) { mapping in
                        mappedRecallRow(mapping, itemComplete: true)
                    }
                    if item.isComplete, item.cardProposalsAvailable {
                        cardProposalButton
                    }
                    recallBoundary
                } else if !item.linkedCardIds.isEmpty {
                    Text(
                        "\(item.linkedCardIds.count) recall card"
                            + (item.linkedCardIds.count == 1 ? " is" : "s are")
                            + " linked to this item."
                    )
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    ForEach(Array(item.linkedCardIds.enumerated()), id: \.element) { index, id in
                        Button {
                            state.path.append(.history(id))
                        } label: {
                            HStack(spacing: 6) {
                                Text("Open recall card \(index + 1)")
                                    .font(TypeRole.secondaryAction)
                                    .foregroundStyle(Theme.meta)
                                Text("→")
                                    .font(WCFont.mono(11))
                                    .foregroundStyle(Theme.accent)
                            }
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                    }
                    recallBoundary
                } else if !item.recallSupported {
                    Text(
                        "This work does not create an Unprompted card automatically. "
                            + "Capture a specific gap only if it earns future review time."
                    )
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    MetaText(
                        text: "NO AUTOMATIC CARD", font: WCFont.mono(10),
                        tracking: 0.6, color: Theme.metaFaint
                    )
                } else if item.practiceDebriefEligible,
                          item.practiceDebrief?.isSubmitted != true {
                    Text("Debrief the practice gap before choosing a recall card.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                    MetaText(
                        text: "NOT READY · DEBRIEF FIRST", font: WCFont.mono(10),
                        tracking: 0.6, color: Theme.metaFaint
                    )
                } else if item.cardProposalsAvailable {
                    Text("The source-backed recall suggestions are ready for your review.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                    cardProposalButton
                    recallBoundary
                } else {
                    Text("This item is complete, but no mapped recall card is ready yet.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.textSecondary)
                    MetaText(
                        text: "CARD NOT READY", font: WCFont.mono(10),
                        tracking: 0.6, color: Theme.metaFaint
                    )
                }
            }
        }
    }

    private func mappedRecallRow(
        _ mapping: PlanMappedRecallCard, itemComplete: Bool
    ) -> some View {
        let actionable = itemComplete && mapping.cardId != nil
        let unavailableLabel = itemComplete ? "Card not ready." : "Complete item first."
        return Group {
            if actionable {
                Button {
                    state.openMappedRecallCard(mapping, itemComplete: itemComplete)
                } label: {
                    mappedRecallLabel(
                        mapping, actionable: true, itemComplete: itemComplete
                    )
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
                .accessibilityLabel(
                    "\(mapping.topic). \(mapping.availabilityLabel). Opens card history."
                )
            } else {
                mappedRecallLabel(
                    mapping, actionable: false, itemComplete: itemComplete
                )
                    .frame(minHeight: Metrics.minTapTarget)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(
                        "\(mapping.topic). \(unavailableLabel) \(mapping.availabilityLabel)"
                    )
            }
        }
    }

    private func mappedRecallLabel(
        _ mapping: PlanMappedRecallCard, actionable: Bool, itemComplete: Bool
    ) -> some View {
        let unavailableLabel = itemComplete ? "CARD NOT READY" : "COMPLETE ITEM FIRST"
        return VStack(alignment: .leading, spacing: 3) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text(mapping.topic)
                    .font(WCFont.sans(14))
                    .foregroundStyle(actionable ? Theme.textSecondary : Theme.textDim)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
                if actionable {
                    Text("→")
                        .font(WCFont.mono(11))
                        .foregroundStyle(Theme.accent)
                }
            }
            MetaText(
                text: actionable
                    ? mapping.availabilityLabel
                    : unavailableLabel
                        + (mapping.availabilityLabel.isEmpty
                            ? "" : " · \(mapping.availabilityLabel)"),
                font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint,
                uppercased: true
            )
        }
    }

    private var cardProposalButton: some View {
        Button {
            plan.prepareCardProposals()
            state.path.append(.planCards(planID, itemID))
        } label: {
            Text("Review source-based suggestions →")
                .font(TypeRole.secondaryAction)
                .foregroundStyle(Theme.accent)
        }
        .buttonStyle(.plain)
        .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
    }

    private var recallBoundary: some View {
        MetaText(
            text: "REVIEWS RUN IN TODAY AND DON'T CHANGE THIS PLAN",
            font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
        )
    }

    private func moreTimeBlock(_ actions: [PlanStretchAction]) -> some View {
        Block(label: "MORE TIME") {
            VStack(alignment: .leading, spacing: 12) {
                Text(
                    "Optional extra work. It is not scheduled or tracked, and it never "
                        + "blocks this plan."
                )
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

                ForEach(actions) { action in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(action.title)
                            .font(WCFont.sans(14, weight: 500))
                            .foregroundStyle(Theme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                        MetaText(
                            text: "\(action.minutes) MIN · NOT SCHEDULED",
                            font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                        )
                        if !action.doneWhen.isEmpty {
                            Text(action.doneWhen)
                                .font(WCFont.sans(13))
                                .foregroundStyle(Theme.textDim)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        stretchResourceLink(action)
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func stretchResourceLink(_ action: PlanStretchAction) -> some View {
        let rawURL = action.resourceUrl.trimmingCharacters(in: .whitespacesAndNewlines)
        let rawLabel = action.resourceLabel.trimmingCharacters(in: .whitespacesAndNewlines)
        if rawURL.isEmpty, rawLabel.isEmpty {
            EmptyView()
        } else if let url = SafeExternalURL.parse(rawURL) {
            Link(destination: url) {
                Text("\(rawLabel.isEmpty ? "Open resource" : rawLabel) ↗")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)
                    .underline()
                    .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
            }
            .accessibilityHint("Opens in your browser. This extra work is not tracked.")
        } else {
            MetaText(
                text: "RESOURCE LINK UNAVAILABLE", font: WCFont.mono(10),
                tracking: 0.6, color: Theme.metaFaint
            )
        }
    }

    private func bottomBlock(_ item: PlanItemDetail) -> some View {
        VStack(spacing: 10) {
            Hairline()
            if item.isComplete {
                MetaText(
                    text: "CHANGE PLAN PROGRESS FROM REOPEN",
                    font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                )
                .padding(.horizontal, Metrics.screenPadding)
                HStack(spacing: 10) {
                    SecondaryButton(title: "Edit") { editing = true }
                    SecondaryButton(title: "Reopen") {
                        state.path.append(.planReopen(planID, item.id))
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
            } else {
                HStack(spacing: 10) {
                    SecondaryButton(title: "Edit", fillsWidth: false) { editing = true }
                    PrimaryButton(title: "Mark complete", enabled: !plan.itemBusy) {
                        Task {
                            guard await plan.completeItem(), let completed = plan.item else { return }
                            if completed.type == "practice" {
                                guard completed.practiceDebriefEligible,
                                      completed.practiceDebrief == nil
                                else { return }
                                plan.preparePracticeDebrief()
                                state.path.append(.practiceDebrief(planID, item.id, true))
                            } else if completed.cardProposalsAvailable {
                                plan.prepareCardProposals()
                                state.path.append(.planCards(planID, item.id))
                            }
                        }
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
            }
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

/// A labelled section. One shape for all of them, so the rhythm is consistent
/// however many a given item has.
struct Block<Content: View>: View {
    let label: String
    @ViewBuilder let content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            MetaText(text: label, font: WCFont.mono(10), tracking: 1.2, color: Theme.meta)
                .accessibilityAddTraits(.isHeader)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 18)
    }
}

/// Item editing. Title, estimate, notes, and the study block — the four things
/// the user actually changes. The estimate stepper moves in 30-minute steps
/// because that is the grid the whole plan's arithmetic is on.
struct PlanItemEditSheet: View {
    let item: PlanItemDetail
    @EnvironmentObject private var plan: StudyPlanState
    @Environment(\.dismiss) private var dismiss

    @State private var title: String
    @State private var minutes: Int
    @State private var notes: String

    init(item: PlanItemDetail) {
        self.item = item
        _title = State(initialValue: item.fullTitle)
        _minutes = State(initialValue: item.estimateMinutes)
        _notes = State(initialValue: item.notes)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(alignment: .firstTextBaseline) {
                Text("Edit item")
                    .font(TypeRole.sheetTitle)
                    .foregroundStyle(Theme.text)
                Spacer()
                Button { dismiss() } label: {
                    Text("Close")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .buttonStyle(.plain)
            }

            VStack(alignment: .leading, spacing: 7) {
                MetaText(text: "TITLE", font: WCFont.mono(10), tracking: 1.2, color: Theme.meta)
                TextField("", text: $title, axis: .vertical)
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.text)
                    .tint(Theme.accent)
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: Metrics.inputRadius)
                            .fill(Theme.inputFill)
                    )
            }

            VStack(alignment: .leading, spacing: 7) {
                MetaText(text: "ESTIMATE", font: WCFont.mono(10), tracking: 1.2,
                         color: Theme.meta)
                StepperControl(
                    value: "\(minutes) min",
                    decrement: { minutes = max(30, minutes - 30) },
                    increment: { minutes = min(600, minutes + 30) }
                )
            }

            VStack(alignment: .leading, spacing: 7) {
                MetaText(text: "NOTES", font: WCFont.mono(10), tracking: 1.2, color: Theme.meta)
                TextField("", text: $notes, axis: .vertical)
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.text)
                    .tint(Theme.accent)
                    .lineLimit(3...6)
                    .padding(12)
                    .background(
                        RoundedRectangle(cornerRadius: Metrics.inputRadius)
                            .fill(Theme.inputFill)
                    )
            }

            PrimaryButton(title: "Save", enabled: !plan.itemBusy) {
                Task {
                    await plan.editItem(
                        PlanItemEdit(
                            fullTitle: title, estimateMinutes: minutes, notes: notes
                        )
                    )
                    dismiss()
                }
            }
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.top, Metrics.screenPadding)
        .padding(.bottom, Metrics.bottomSafeArea)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.surface)
        .presentationDetents([.height(520)])
        .presentationDragIndicator(.hidden)
        .presentationBackground(Theme.surface)
        .presentationCornerRadius(Metrics.sheetRadius)
    }
}
