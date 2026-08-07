import SwiftUI
import UIKit

/// One unscored reflection after an eligible Practice item.
///
/// Completion has already committed before this screen is pushed. The only
/// durable product this flow may create is the debrief record and, after the
/// existing gate plus explicit acceptance, ordinary recall cards.
struct PracticeDebriefScreen: View {
    private enum Stage: Equatable {
        case offer, idle, resume, recording, text, saving, saveFailed, checking, checkFailed
    }

    let planID: UUID
    let itemID: UUID
    let showCompletionOffer: Bool

    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var plan: StudyPlanState
    @EnvironmentObject private var flags: DebugFlags
    @StateObject private var speech = SpeechService()
    @State private var stage: Stage

    init(planID: UUID, itemID: UUID, showCompletionOffer: Bool) {
        self.planID = planID
        self.itemID = itemID
        self.showCompletionOffer = showCompletionOffer
        _stage = State(initialValue: showCompletionOffer ? .offer : .idle)
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            header

            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    switch stage {
                    case .offer: offer
                    case .idle: prompt
                    case .resume: resume
                    case .recording: recording
                    case .text, .saving, .saveFailed: editor
                    case .checking: checking
                    case .checkFailed: checkFailed
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 20)
            }

            footer
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .task {
            if plan.item?.id != itemID { await plan.loadItem(planID, itemID: itemID) }
            await plan.loadPracticeDebrief(planID, itemID: itemID)
            if !showCompletionOffer, plan.debrief?.isSubmitted != true,
               !plan.debriefDraft.isEmpty {
                stage = .resume
            }
            switch flags.route {
            case "study-plan-debrief-idle", "study-plan-debrief-mic-unavailable":
                stage = .idle
            case "study-plan-debrief-recording":
                startRecording()
            case "study-plan-debrief-text":
                if plan.debriefDraft.isEmpty {
                    plan.updatePracticeDebriefDraft(
                        "I got the request order right, but I froze on retries and drew them outside the timeout."
                    )
                }
                stage = .text
            case "study-plan-debrief-save-failure":
                if plan.debriefDraft.isEmpty {
                    plan.updatePracticeDebriefDraft("I froze on retries and drew them outside the timeout.")
                }
                stage = .saveFailed
            case "study-plan-debrief-checking": stage = .checking
            case "study-plan-debrief-check-failure": stage = .checkFailed
            default: break
            }
        }
        .onChange(of: speech.transcript) { _, text in
            guard stage == .recording else { return }
            plan.updatePracticeDebriefDraft(text)
        }
        .onDisappear {
            speech.stop()
            plan.flushPracticeDebriefDraft()
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button { close() } label: {
                Text(stage == .offer ? "← Practice complete" : "← Back")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)

            if stage != .offer {
                MetaText(
                    text: plan.item?.fullTitle.uppercased() ?? "PRACTICE DEBRIEF",
                    font: WCFont.mono(10), tracking: 0.8, color: Theme.metaDim
                )
                .lineLimit(1)
                .padding(.top, 4)
                .padding(.bottom, 12)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, Metrics.screenPadding)
    }

    private var offer: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let item = plan.item {
                MetaText(
                    text: "WEEK \(item.weekIndex) · PRACTICE · \(item.estimateMinutes) MIN",
                    font: WCFont.mono(10), tracking: 0.8, color: Theme.metaDim
                )
                Text(item.fullTitle)
                    .font(WCFont.sans(18, weight: 600))
                    .foregroundStyle(Theme.text)
                Text(item.doneWhen)
                    .font(WCFont.sans(14))
                    .foregroundStyle(Theme.textSecondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer().frame(height: 112)
            Text("Practice complete.")
                .font(WCFont.sans(25, weight: 600))
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            Text("Anything break down while you were doing it?")
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
            MetaText(
                text: "UNSCORED · MAY SUGGEST FOCUSED RECALL CARDS",
                font: WCFont.mono(10), tracking: 0.7, color: Theme.metaFaint
            )
        }
    }

    private var prompt: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("What went wrong, felt shaky, or surprised you?")
                .font(WCFont.serif(25))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityAddTraits(.isHeader)
            Text("Say what you attempted and where your reasoning broke down.")
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 14)

            if speech.unavailable || flags.route == "study-plan-debrief-mic-unavailable" {
                InlineNotice {
                    Text("Voice input isn’t available. Your debrief can still be typed.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.text)
                }
                .padding(.top, 22)
            } else {
                Button { startRecording() } label: {
                    VStack(spacing: 12) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 34, weight: .medium))
                            .foregroundStyle(Theme.bg)
                            .frame(width: 108, height: 108)
                            .background(Theme.accent, in: Circle())
                        MetaText(
                            text: "TAP TO SPEAK", font: WCFont.mono(10),
                            tracking: 1, color: Theme.metaFaint
                        )
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .padding(.top, 92)
                .accessibilityLabel("Start recording")
            }
        }
    }

    private var recording: some View {
        VStack(alignment: .leading, spacing: 14) {
            MetaText(
                text: "● RECORDING", font: WCFont.mono(10), tracking: 0.8,
                color: Theme.accent
            )
            Text("What went wrong, felt shaky, or surprised you?")
                .font(WCFont.serif(20))
                .foregroundStyle(Theme.textSerif)
            Text(plan.debriefDraft.isEmpty ? "Listening…" : plan.debriefDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(plan.debriefDraft.isEmpty ? Theme.textMuted : Theme.textSerif)
                .lineSpacing(9)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
            Spacer().frame(height: 180)
            MetaText(
                text: "SAVED", font: WCFont.mono(10), tracking: 0.8,
                color: Theme.metaFaint
            )
        }
    }

    private var editor: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("What went wrong, felt shaky, or surprised you?")
                .font(WCFont.serif(21))
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)

            TextEditor(text: Binding(
                get: { plan.debriefDraft },
                set: { plan.updatePracticeDebriefDraft($0) }
            ))
            .font(WCFont.serif(18))
            .foregroundStyle(Theme.textSerif)
            .scrollContentBackground(.hidden)
            .frame(minHeight: 300)
            .padding(12)
            .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
            .disabled(stage == .saving)
            .accessibilityLabel("What broke down?")

            if stage == .saveFailed {
                InlineNotice {
                    Text("Couldn’t save — your debrief is safe on this phone.")
                        .font(WCFont.sans(14))
                        .foregroundStyle(Theme.text)
                }
            } else {
                MetaText(
                    text: "SAVED", font: WCFont.mono(10), tracking: 0.8,
                    color: Theme.metaFaint
                )
            }
        }
    }

    private var resume: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Unfinished debrief")
                .font(WCFont.sans(25, weight: 600))
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            Text("Everything you said is still here.")
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.textSecondary)
            Text(plan.debriefDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(9)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 16)
        }
    }

    private var checking: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Debrief saved.")
                .font(WCFont.sans(25, weight: 600))
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            HStack(spacing: 10) {
                ScoringDots()
                MetaText(
                    text: "CHECKING FOR FOCUSED GAPS", font: WCFont.mono(10),
                    tracking: 0.8, color: Theme.meta
                )
            }
            .padding(.top, 18)
            Text("Your debrief is safe if you leave; check again from the practice item.")
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var checkFailed: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Debrief saved.")
                .font(WCFont.sans(25, weight: 600))
                .foregroundStyle(Theme.text)
                .accessibilityAddTraits(.isHeader)
            Text("Couldn’t check for focused cards.")
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
            Text("Your debrief is unchanged. You can try again now or from the practice item.")
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var footer: some View {
        VStack(spacing: 10) {
            Hairline()
            switch stage {
            case .offer:
                VStack(spacing: 8) {
                    PrimaryButton(title: "Debrief practice") { stage = .idle }
                    Button("Not now") { close() }
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.metaAlt)
                        .frame(minHeight: Metrics.minTapTarget)
                }
            case .idle:
                if speech.unavailable || flags.route == "study-plan-debrief-mic-unavailable" {
                    HStack(spacing: 10) {
                        SecondaryButton(title: "Open Settings") { openSettings() }
                        PrimaryButton(title: "Type debrief") { stage = .text }
                    }
                } else {
                    SecondaryButton(title: "Type instead") { stage = .text }
                }
            case .resume:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Discard") {
                        plan.discardPracticeDebriefDraft()
                        stage = .idle
                    }
                    PrimaryButton(title: "Resume") { stage = .text }
                }
            case .recording:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Type", fillsWidth: false) {
                        speech.stop()
                        plan.flushPracticeDebriefDraft()
                        stage = .text
                    }
                    PrimaryButton(title: "Stop") { finishRecording() }
                }
            case .text, .saveFailed:
                VStack(spacing: 4) {
                    PrimaryButton(
                        title: stage == .saveFailed ? "Try again" : "Submit debrief",
                        enabled: !plan.debriefDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    ) { submit() }
                    Button("Discard") {
                        plan.discardPracticeDebriefDraft()
                        close()
                    }
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
                    .frame(minHeight: Metrics.minTapTarget)
                }
            case .saving:
                PrimaryButton(title: "Saving…", enabled: false) {}
            case .checking:
                EmptyView()
            case .checkFailed:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Done") { close() }
                    PrimaryButton(title: "Try again") { checkCards() }
                }
            }
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    private func startRecording() {
        stage = .recording
        speech.start(
            continuing: plan.debriefDraft,
            vocabulary: SpeechVocabulary.terms(for: plan.item?.fullTitle),
            simulated: flags.simulateSpeech,
            simulate: "I got the request order right, but I froze on retries and drew them outside the timeout."
        )
    }

    private func finishRecording() {
        Task {
            let text = await speech.finish()
            plan.updatePracticeDebriefDraft(text)
            plan.flushPracticeDebriefDraft()
            stage = .text
        }
    }

    private func submit() {
        stage = .saving
        Task {
            if await plan.submitPracticeDebrief() {
                stage = .checking
                await runCardCheck()
            } else {
                stage = .saveFailed
            }
        }
    }

    private func checkCards() {
        stage = .checking
        Task { await runCardCheck() }
    }

    private func runCardCheck() async {
        plan.prepareCardProposals()
        if await plan.checkPracticeDebriefCards() {
            if !state.path.isEmpty { state.path.removeLast() }
            state.path.append(.planCards(planID, itemID))
        } else {
            stage = .checkFailed
        }
    }

    private func close() {
        speech.stop()
        plan.flushPracticeDebriefDraft()
        if !state.path.isEmpty { state.path.removeLast() }
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}
