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
        .task { await plan.loadItem(planID, itemID: itemID) }
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
        if !item.sourceExcerpt.isEmpty || !item.sourceLabel.isEmpty {
            Block(label: "SOURCE") {
                VStack(alignment: .leading, spacing: 5) {
                    if !item.sourceLabel.isEmpty {
                        Text(item.sourceLabel)
                            .font(WCFont.sans(14))
                            .foregroundStyle(Theme.textSecondary)
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

        if item.cardProposalsAvailable || !item.linkedCardIds.isEmpty {
            Block(label: "RETRIEVAL SUPPORT") {
                VStack(alignment: .leading, spacing: 8) {
                    Text(
                        item.linkedCardIds.isEmpty
                            ? "No recall cards yet"
                            : "\(item.linkedCardIds.count) recall card"
                                + (item.linkedCardIds.count == 1 ? "" : "s") + " available"
                    )
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)

                    if item.isComplete, item.cardProposalsAvailable {
                        Button { state.path.append(.planCards(planID, item.id)) } label: {
                            Text("Suggest recall cards →")
                                .font(TypeRole.secondaryAction)
                                .foregroundStyle(Theme.accent)
                        }
                        .buttonStyle(.plain)
                        .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
                    }
                    MetaText(
                        text: "THEIR REVIEWS RUN IN TODAY AND DON'T CHANGE THIS PLAN",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                    )
                }
            }
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
                        Task { await plan.completeItem() }
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
