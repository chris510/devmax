import SwiftUI

/// Paste a guide, pick a duration and a weekly capacity, preview.
///
/// The guide is written to disk on every keystroke pause. A study guide is often
/// assembled from several places and has never been sent anywhere before the
/// first preview, so losing it is unrecoverable in a way losing almost nothing
/// else in this app is.
struct PlanBuildScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @FocusState private var focused: Bool

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    guideField
                    durationRow
                    capacityRow
                    deadlineRow
                    MetaText(
                        text: "NOTHING IS CREATED UNTIL YOU REVIEW THE PLAN",
                        font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
                    )
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 16)
            }

            bottomBlock
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .onChange(of: plan.guideText) { _, _ in plan.persistGuide() }
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

            VStack(alignment: .leading, spacing: 5) {
                Text("Study plan")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                Text("Paste a study guide. Any subject.")
                    .font(WCFont.sans(15))
                    .foregroundStyle(Theme.textSecondary)
            }
            .padding(.top, 4)
            .padding(.bottom, 16)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    private var guideField: some View {
        VStack(alignment: .leading, spacing: 7) {
            MetaText(text: "YOUR GUIDE", font: WCFont.mono(10), tracking: 1.2,
                     color: Theme.meta)
            TextField("", text: $plan.guideText, axis: .vertical)
                .font(WCFont.sans(14.5))
                .foregroundStyle(Theme.text)
                .tint(Theme.accent)
                .focused($focused)
                .lineLimit(8...16)
                .padding(12)
                .background(
                    RoundedRectangle(cornerRadius: Metrics.inputRadius).fill(Theme.inputFill)
                )
                .accessibilityLabel("Study guide text")
            MetaText(
                text: plan.canPreview
                    ? "\(plan.guideText.count) CHARACTERS"
                    : "AT LEAST 200 CHARACTERS TO BUILD A PLAN",
                font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
            )
        }
    }

    private var durationRow: some View {
        VStack(alignment: .leading, spacing: 7) {
            MetaText(text: "DURATION", font: WCFont.mono(10), tracking: 1.2, color: Theme.meta)
            StepperControl(
                value: "\(plan.requestedWeeks) weeks",
                decrement: { plan.requestedWeeks = max(2, plan.requestedWeeks - 1) },
                increment: { plan.requestedWeeks = min(52, plan.requestedWeeks + 1) }
            )
        }
    }

    private var capacityRow: some View {
        VStack(alignment: .leading, spacing: 7) {
            MetaText(text: "WEEKLY CAPACITY", font: WCFont.mono(10), tracking: 1.2,
                     color: Theme.meta)
            StepperControl(
                value: "\(plan.weeklyCapacityHours)h · \(plan.capacityMinutes) min",
                decrement: { plan.weeklyCapacityHours = max(1, plan.weeklyCapacityHours - 1) },
                increment: { plan.weeklyCapacityHours = min(40, plan.weeklyCapacityHours + 1) }
            )
        }
    }

    /// Flexible is the default. A deadline is an extra constraint the user opts
    /// into, and it is the latest acceptable date rather than a target.
    private var deadlineRow: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Fixed deadline")
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.text)
                    Text("The latest date this can finish. Finishing early is fine.")
                        .font(WCFont.sans(12.5))
                        .foregroundStyle(Theme.textDim)
                }
                Spacer()
                Toggle34(isOn: $plan.fixedDeadline)
                    .accessibilityLabel("Fixed deadline")
            }
            if plan.fixedDeadline {
                DatePicker("", selection: $plan.deadline, displayedComponents: .date)
                    .datePickerStyle(.compact)
                    .labelsHidden()
                    .tint(Theme.accent)
                    .wcFade(Motion.fadeFast)
            }
        }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            PrimaryButton(
                title: plan.previewLoad == .loading ? "Building…" : "Preview plan",
                enabled: plan.canPreview && plan.previewLoad != .loading
            ) {
                Task {
                    await plan.runPreview()
                    if plan.preview != nil { state.path.append(.planPreview) }
                }
            }
            .padding(.horizontal, Metrics.screenPadding)
        }
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }
}

/// Review plan. Leads with the summary; each resolved check is one status line
/// with nowhere to go, and only the exceptions carry an arrow.
struct PlanPreviewScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    if let preview = plan.preview, preview.failed {
                        importFailure(preview)
                    } else if let preview = plan.preview {
                        summary(preview)
                        checks(preview)
                    } else {
                        LoadingList(label: "READING YOUR GUIDE", inset: 0, separator: .overlay)
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
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { state.path.removeLast() } label: {
                Text("← Edit guide")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            VStack(alignment: .leading, spacing: 5) {
                Text("Review plan")
                    .font(TypeRole.screenTitle)
                    .tracking(-0.6)
                    .foregroundStyle(Theme.text)
                    .accessibilityAddTraits(.isHeader)
                if let preview = plan.preview, !preview.failed {
                    Text(preview.subject)
                        .font(WCFont.sans(15))
                        .foregroundStyle(Theme.textSecondary)
                    Text(preview.summaryLine)
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textMuted)
                    Text(preview.forecastLabel)
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

    /// Nothing the user typed is lost here — the guide, the duration, the
    /// capacity, the mode, and the deadline are all still on the draft, which is
    /// why Try again is one tap and not a re-paste.
    private func importFailure(_ preview: PlanPreview) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            InlineNotice {
                VStack(alignment: .leading, spacing: 5) {
                    Text("Couldn't build the plan.")
                        .font(WCFont.sans(15, weight: 500))
                        .foregroundStyle(Theme.text)
                    Text("Your guide, duration, capacity, and deadline are still here.")
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textSecondary)
                }
            }
            .accessibilityAddTraits(.isStaticText)

            if !preview.error.isEmpty {
                MetaText(text: preview.error.uppercased(), font: WCFont.mono(10),
                         tracking: 0.6, color: Theme.metaFaint)
            }
        }
        .padding(.top, 8)
    }

    private func summary(_ preview: PlanPreview) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(preview.phaseRows) { phase in
                Hairline()
                VStack(alignment: .leading, spacing: 3) {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text("\(phase.index) · \(phase.displayTitle)")
                            .font(WCFont.sans(15, weight: 500))
                            .foregroundStyle(Theme.text)
                        Spacer()
                        MetaText(text: phase.weekRange.uppercased(), font: WCFont.mono(10),
                                 tracking: 0.6, color: Theme.metaFaint)
                    }
                    // The description earns its place here — plan creation is
                    // exactly where V3.5 relocated it to.
                    Text(phase.description)
                        .font(WCFont.sans(13))
                        .foregroundStyle(Theme.textDim)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .padding(.vertical, 11)
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(phase.fullTitle). \(phase.weekRange).")
            }
            Hairline()
        }
        .padding(.top, 4)
    }

    private func checks(_ preview: PlanPreview) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            MetaText(text: "CHECKS", font: WCFont.mono(10), tracking: 1.2, color: Theme.meta)
                .padding(.top, 20)
                .padding(.bottom, 4)
                .accessibilityAddTraits(.isHeader)

            ForEach(preview.checks) { check in
                Hairline()
                CheckRowView(check: check) {
                    guard let destination = check.destination else { return }
                    state.path.append(.planAudit(destination))
                }
            }
            Hairline()

            MetaText(
                text: preview.canCreate
                    ? "EVERY CHECK IS RESOLVED"
                    : "CREATE PLAN UNLOCKS WHEN THE FLAGGED CHECKS ARE RESOLVED",
                font: WCFont.mono(10), tracking: 0.6, color: Theme.metaFaint
            )
            .padding(.top, 12)
        }
    }

    private var bottomBlock: some View {
        VStack(spacing: 10) {
            Hairline()
            if plan.preview?.failed == true {
                PrimaryButton(
                    title: plan.previewLoad == .loading ? "Trying…" : "Try again",
                    enabled: plan.previewLoad != .loading
                ) {
                    Task { await plan.retryPreview() }
                }
                .padding(.horizontal, Metrics.screenPadding)
            } else {
                PrimaryButton(
                    title: plan.creating ? "Creating…" : "Create plan",
                    enabled: plan.preview?.canCreate == true && !plan.creating
                ) {
                    Task {
                        if let id = await plan.createPlan() {
                            state.path = [.planOverview(id)]
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

/// A resolved check is a status line. An unresolved one is a button.
private struct CheckRowView: View {
    let check: PlanCheck
    let onTap: () -> Void

    private var content: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(check.label)
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.text)
            Spacer(minLength: 8)
            Text(check.isResolved ? check.value : check.value.uppercased())
                .font(check.isResolved ? WCFont.sans(13.5) : WCFont.mono(10.5))
                .tracking(check.isResolved ? 0 : 0.6)
                .foregroundStyle(check.isResolved ? Theme.textMuted : Theme.scoreMid)
            if check.isTappable {
                Text("→").font(WCFont.mono(11)).foregroundStyle(Theme.scoreMid)
            }
        }
        .padding(.vertical, 12)
        .contentShape(Rectangle())
    }

    var body: some View {
        if check.isTappable {
            Button(action: onTap) { content }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
                .accessibilityLabel("\(check.label): \(check.value). \(check.detail)")
                .accessibilityHint("Opens the audit.")
        } else {
            content
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(check.label): \(check.value).")
        }
    }
}
