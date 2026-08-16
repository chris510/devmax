import SwiftUI

/// Immediate formation and delayed transfer stay outside Conversation.
///
/// This screen has no Session id, no result block, no run entry, and no numeric
/// score. The first scheduler-driving turn remains the ordinary due-card route.
struct LessonCheckScreen: View {
    @Environment(\.scenePhase) private var scenePhase
    @EnvironmentObject private var flow: PublicOnboardingState
    @EnvironmentObject private var app: AppState
    @EnvironmentObject private var auth: AuthState
    @EnvironmentObject private var flags: DebugFlags
    @StateObject private var speech = SpeechService()

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: statusLabel)
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    content
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, 24)
            }
            footer
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        .onChange(of: speech.transcript) { _, text in
            guard [.recording, .transferRecording].contains(flow.lessonCheckStage) else {
                return
            }
            flow.updateLessonCheckDraft(text)
        }
        .onChange(of: speech.captureState) { _, captureState in
            guard captureState == .needsReview,
                  [.recording, .transferRecording].contains(flow.lessonCheckStage)
            else { return }
            let transfer = flow.activeLessonCheck?.kind == .transfer
            let text = speech.transcript
            speech.stop()
            if !text.isEmpty { flow.updateLessonCheckDraft(text) }
            flow.flushLessonCheckDraft()
            flow.lessonCheckStage = transfer ? .transferText : .text
        }
        .onChange(of: scenePhase) { _, phase in
            if phase != .active { flow.flushLessonCheckDraft() }
        }
        .onAppear {
            if flow.lessonCheckStage == .held,
               flow.lessonRecallNotBeforeAt.map({ $0 <= Date() }) == true {
                flow.lessonCheckStage = .recallReady
            }
        }
        .onDisappear {
            speech.stop()
            flow.flushLessonCheckDraft()
        }
    }

    private var statusLabel: String {
        switch flow.lessonCheckStage {
        case .preview: "FORMATION · UNSCORED"
        case .attempt, .resume, .recording, .text, .submitting, .submitFailed:
            "SOURCE CLOSED · UNSCORED"
        case .restudying, .authority: "SOURCE AUTHORITY"
        case .confirming: "CREATING HELD RECALL"
        case .confirmationFailed: "LESSON SAFE"
        case .held: "RECALL HELD"
        case .recallReady: "RECALL READY"
        case .completeNoCards: "NO RECALL CREATED"
        case .transfer, .transferResume, .transferRecording, .transferText,
             .transferSubmitting, .transferFailed, .transferSubmitted,
             .transferDebrief:
            "RESEARCH CHECK · UNSCORED"
        case .loading: "PREPARING"
        case .loadFailed: "LESSON SAFE"
        }
    }

    private var header: some View {
        Button { backOrClose() } label: {
            Text(flow.lessonCheckStage == .preview ? "← Concepts" : "← Back")
                .font(TypeRole.secondaryAction)
                .foregroundStyle(Theme.metaAlt)
                .frame(maxWidth: .infinity, minHeight: Metrics.minTapTarget, alignment: .leading)
        }
        .buttonStyle(.plain)
        .padding(.horizontal, Metrics.screenPadding)
    }

    @ViewBuilder
    private var content: some View {
        switch flow.lessonCheckStage {
        case .preview: preview
        case .loading: loading("Preparing the source-closed activity")
        case .loadFailed: loadFailed
        case .attempt: attempt
        case .resume: resume
        case .recording: recording
        case .text, .submitting, .submitFailed: editor(transfer: false)
        case .restudying: loading("Opening the grounded explanation")
        case .authority: authority(transferDebrief: false)
        case .confirming: loading("Creating held Recall")
        case .confirmationFailed: confirmationFailed
        case .held: held
        case .recallReady: recallReady
        case .completeNoCards: completeNoCards
        case .transfer: transfer
        case .transferResume: transferResume
        case .transferRecording: transferRecording
        case .transferText, .transferSubmitting, .transferFailed:
            editor(transfer: true)
        case .transferSubmitted: transferSubmitted
        case .transferDebrief: authority(transferDebrief: true)
        }
    }

    private var preview: some View {
        VStack(alignment: .leading, spacing: 18) {
            title("Make sense of it before Recall.")
            Text(
                "Formation is immediate and qualitative. It does not create a score or move your schedule."
            )
            .lessonBody()

            ForEach(flow.displayedLessonTopicPreviews) { topic in
                VStack(alignment: .leading, spacing: 10) {
                    MetaText(
                        text: topic.sectionTitle.isEmpty ? "SOURCE SECTION" : topic.sectionTitle,
                        font: WCFont.mono(9.5), tracking: 0.7, color: Theme.metaFaint
                    )
                    Text(topic.topic)
                        .font(WCFont.sans(17, weight: 500))
                        .foregroundStyle(Theme.text)
                    if topic.hasTransferEntryPoint {
                        Text(
                            topic.transferState == "submitted"
                                ? "Your blind response is saved. Reopen its quiet completion state."
                                : "Apply the concept under a frozen, human-reviewed varied cue."
                        )
                            .font(WCFont.serif(18))
                            .foregroundStyle(Theme.textSerif)
                            .lineSpacing(5)
                            .fixedSize(horizontal: false, vertical: true)
                        pilotNote(
                            topic.transferState == "submitted"
                                ? "RESEARCH CHECK SUBMITTED · UNSCORED"
                                : "DAY-SEVEN RESEARCH CHECK · UNSCORED"
                        )
                    } else if let question = topic.formationQuestion, !question.isEmpty {
                        Text(question)
                            .font(WCFont.serif(18))
                            .foregroundStyle(Theme.textSerif)
                            .lineSpacing(5)
                            .fixedSize(horizontal: false, vertical: true)
                        pilotNote("ATTEMPT FIRST · SOURCE CLOSED · UNSCORED")
                    } else {
                        Text("The grounded explanation opens without a pre-shown retrieval cue.")
                            .font(WCFont.sans(13.5))
                            .foregroundStyle(Theme.textMuted)
                        pilotNote("SOURCE-BACKED RESTUDY · UNSCORED")
                    }
                    PrimaryButton(
                        title: topic.hasTransferEntryPoint
                            ? topic.transferState == "submitted"
                                ? "Open submitted response"
                                : "Begin research check"
                            : topic.formationQuestion == nil
                                ? "Open grounded restudy"
                                : "Attempt from memory"
                    ) {
                        Task {
                            if topic.hasTransferEntryPoint {
                                await flow.beginTransferCheck(topic)
                            } else {
                                await flow.beginLessonActivity(topic)
                            }
                        }
                    }
                }
                .padding(15)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Metrics.inlineRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: Metrics.inlineRadius)
                        .stroke(Theme.border, lineWidth: 1)
                )
            }

            if flow.displayedLessonTopicPreviews.isEmpty {
                Text("No pilot concept is available from this source.")
                    .lessonBody()
            }
            if !flow.error.isEmpty { errorNotice(flow.error) }
        }
    }

    private var attempt: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("ATTEMPT FIRST · SOURCE CLOSED")
            title(flow.activeLessonCheck?.promptText ?? flow.currentLessonTopicPreview?.formationQuestion ?? "Explain the concept from memory.")
            Text("Say what happens, why it happens, and where the account stops being true.")
                .lessonBody()
            pilotNote("NO ANSWER OR RUBRIC IS LOADED ON THIS SCREEN")

            if speech.unavailable {
                notice("Voice input isn't available. Your explanation can still be typed.")
            } else {
                Button { startRecording(transfer: false) } label: {
                    VStack(spacing: 12) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 32, weight: .medium))
                            .foregroundStyle(Theme.bg)
                            .frame(width: 104, height: 104)
                            .background(Theme.accent, in: Circle())
                        pilotNote("TAP TO EXPLAIN")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .padding(.top, 62)
                .accessibilityLabel("Start source-closed explanation")
            }
        }
    }

    private var resume: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("SOURCE CLOSED · LOCAL DRAFT")
            title("Your unfinished explanation is still here.")
            Text(flow.lessonCheckDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
            notice("Nothing has been scored or exposed. Resume exactly where you stopped.")
        }
    }

    private var recording: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("● RECORDING · SOURCE CLOSED")
            Text(flow.activeLessonCheck?.promptText ?? "Explain the concept from memory.")
                .font(WCFont.serif(20))
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)
            Text(flow.lessonCheckDraft.isEmpty ? "Listening…" : flow.lessonCheckDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(flow.lessonCheckDraft.isEmpty ? Theme.textMuted : Theme.textSerif)
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 10)
            pilotNote("SAVED ON THIS PHONE")
        }
    }

    private func editor(transfer: Bool) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote(transfer ? "RESEARCH CHECK · NO SCORE" : "SOURCE CLOSED · NO SCORE")
            Text(flow.activeLessonCheck?.promptText ?? "Explain the concept.")
                .font(WCFont.serif(21))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true)
            TextEditor(
                text: Binding(
                    get: { flow.lessonCheckDraft },
                    set: { flow.updateLessonCheckDraft($0) }
                )
            )
            .font(WCFont.serif(18))
            .foregroundStyle(Theme.textSerif)
            .scrollContentBackground(.hidden)
            .frame(minHeight: 280)
            .padding(12)
            .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
            .overlay(
                RoundedRectangle(cornerRadius: Metrics.inputRadius)
                    .stroke(Theme.border, lineWidth: 1)
            )
            .disabled(flow.busy)
            .accessibilityLabel(transfer ? "Transfer response" : "Formation explanation")

            if [.submitFailed, .transferFailed].contains(flow.lessonCheckStage) {
                errorNotice(flow.error)
            } else {
                pilotNote("SAVED ON THIS PHONE")
            }
        }
    }

    private var transfer: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("RESEARCH CHECK · VARIED CUE · UNSCORED")
            title(flow.activeLessonCheck?.promptText ?? "Apply the concept under a changed condition.")
            Text(
                "This response is locked for blinded review. It does not change Recall, mastery, or your schedule."
            )
            .lessonBody()
            if speech.unavailable {
                notice("Voice input isn't available. Your research response can still be typed.")
            } else {
                Button { startRecording(transfer: true) } label: {
                    VStack(spacing: 12) {
                        Image(systemName: "mic.fill")
                            .font(.system(size: 32, weight: .medium))
                            .foregroundStyle(Theme.bg)
                            .frame(width: 104, height: 104)
                            .background(Theme.accent, in: Circle())
                        pilotNote("TAP TO RESPOND")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(.plain)
                .padding(.top, 54)
            }
        }
    }

    private var transferResume: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("RESEARCH CHECK · LOCAL DRAFT")
            title("Your unfinished response is still here.")
            Text(flow.lessonCheckDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(Theme.textSerif)
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)
            notice("No score or feedback has been produced.")
        }
    }

    private var transferRecording: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("● RECORDING · RESEARCH CHECK")
            Text(flow.activeLessonCheck?.promptText ?? "Apply the concept.")
                .font(WCFont.serif(20))
                .foregroundStyle(Theme.textSerif)
            Text(flow.lessonCheckDraft.isEmpty ? "Listening…" : flow.lessonCheckDraft)
                .font(WCFont.serif(18))
                .foregroundStyle(flow.lessonCheckDraft.isEmpty ? Theme.textMuted : Theme.textSerif)
                .lineSpacing(8)
                .fixedSize(horizontal: false, vertical: true)
            pilotNote("SAVED ON THIS PHONE")
        }
    }

    private func authority(transferDebrief: Bool) -> some View {
        VStack(alignment: .leading, spacing: 18) {
            if let authority = flow.lessonAuthority {
                pilotNote(
                    transferDebrief
                        ? "TRANSFER DEBRIEF · NEW EXPOSURE BOUNDARY"
                        : authority.check.condition == .restudy
                            ? "SOURCE-BACKED RESTUDY · UNSCORED"
                            : "FORMATION FEEDBACK · UNSCORED"
                )
                title(
                    transferDebrief
                        ? "Source-backed debrief"
                        : authority.confirmationTitle
                )
                if !flow.error.isEmpty {
                    errorNotice(flow.error)
                }
                if let outcome = authority.check.qualitativeOutcome {
                    labelled("QUALITATIVE CHECK", outcome.label)
                }
                if !authority.feedback.isEmpty {
                    labelled(
                        authority.check.condition == .restudy
                            ? "GROUNDED EXPLANATION"
                            : "HIGHEST-VALUE CORRECTION",
                        authority.feedback
                    )
                }
                labelled("LITERAL SOURCE EXCERPT", authority.sourceExcerpt)
                labelled("ANSWER BASIS", authority.answerBasis)
                labelled("CANONICAL RECALL QUESTION", authority.canonicalQuestion)
                pilotNote("ANSWER RUBRIC")
                ForEach(
                    Array(rubricRows(authority.answerRubric).enumerated()),
                    id: \.offset
                ) { _, row in
                    labelled(row.label.uppercased(), row.value)
                }
                if !authority.recallQuestions.isEmpty {
                    pilotNote("RECALL CANDIDATES")
                    ForEach(authority.recallQuestions) { prompt in
                        labelled(prompt.levelLabel.uppercased(), prompt.question)
                    }
                }
                notice(
                    "Grounded to \(authority.sourceTitle) · \(provenanceLabel(authority.contentProvenance)). Grounding checks correspondence to this source, not universal truth."
                )
                if !transferDebrief {
                    Text(authority.confirmationMessage)
                        .font(WCFont.sans(13.5))
                        .foregroundStyle(Theme.textSecondary)
                }
            } else {
                loading("Reopening source authority")
            }
        }
    }

    private var held: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("FORMATION COMPLETE · NO SCORE")
            title("Recall is held.")
            Text(
                "The first scored question will appear through Today only after the server-owned hold opens."
            )
            .lessonBody()
            if let date = flow.lessonRecallNotBeforeAt {
                labelled("RECALL AVAILABLE", Self.holdFormatter.string(from: date))
            }
            notice("Formation did not write review history, mastery, or SM-2 state.")
        }
    }

    private var completeNoCards: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("FORMATION COMPLETE · NO CARD CREATED")
            title("No Recall was created.")
            Text(
                "You excluded the concept after reviewing its source authority. The source remains in Study material."
            )
            .lessonBody()
            notice("No score, review history, mastery, or schedule state changed.")
        }
    }

    private var confirmationFailed: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("CONFIRMATION NOT SAVED")
            title("Your formation work is safe.")
            Text(flow.error)
                .lessonBody()
            notice("Try again to finish the source record. No Recall card was created yet.")
        }
    }

    private var recallReady: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("DELAYED RECALL · ORDINARY DUE FLOW")
            title("Recall is ready in Today.")
            Text(
                "Open it from the ordinary queue. That closed-book response, not formation, can establish Recall and move the schedule."
            )
            .lessonBody()
            notice("This screen does not start a Session or reveal the question again.")
        }
    }

    private var transferSubmitted: some View {
        VStack(alignment: .leading, spacing: 16) {
            pilotNote("RESEARCH CHECK SUBMITTED · NO SCORE")
            title("Response locked for blind review.")
            Text(
                "No correction was revealed, and nothing changed the canonical question, mastery, or schedule."
            )
            .lessonBody()
            notice("Opening the source-backed debrief creates a fresh exposure boundary first.")
        }
    }

    private var loadFailed: some View {
        VStack(alignment: .leading, spacing: 14) {
            pilotNote("LESSON SAFE")
            title("This activity couldn't open.")
            Text(flow.error.isEmpty ? "Try again when the service is reachable." : flow.error)
                .lessonBody()
        }
    }

    private func loading(_ label: String) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 10) {
                ScoringDots()
                pilotNote(label.uppercased())
            }
            Text("You can leave; the lesson and any local draft stay safe.")
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
        }
        .padding(.top, 18)
    }

    @ViewBuilder
    private var footer: some View {
        VStack(spacing: 9) {
            Hairline()
            switch flow.lessonCheckStage {
            case .preview, .loading, .restudying, .confirming,
                 .submitting, .transferSubmitting:
                EmptyView()
            case .confirmationFailed:
                PrimaryButton(title: "Try confirming again") {
                    Task { await flow.retryPilotLessonConfirmation() }
                }
            case .loadFailed:
                PrimaryButton(title: "Try again") {
                    if let check = flow.activeLessonCheck, check.status == .exposed {
                        Task { await flow.reopenCurrentLessonAuthority() }
                    } else {
                        Task { await flow.openLessonPilotPreview() }
                    }
                }
            case .attempt:
                SecondaryButton(title: "Type instead") { flow.lessonCheckStage = .text }
            case .resume:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Discard") {
                        flow.discardLessonCheckDraft()
                        flow.lessonCheckStage = .attempt
                    }
                    PrimaryButton(title: "Resume") { flow.lessonCheckStage = .text }
                }
            case .recording:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Type", fillsWidth: false) {
                        stopIntoText(transfer: false)
                    }
                    PrimaryButton(title: "Stop") { finishRecording(transfer: false) }
                }
            case .text, .submitFailed:
                PrimaryButton(
                    title: flow.lessonCheckStage == .submitFailed
                        ? "Try check again"
                        : "Check explanation",
                    enabled: !flow.lessonCheckDraft
                        .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ) {
                    Task { await flow.submitLessonAttempt() }
                }
            case .authority:
                VStack(spacing: 9) {
                    PrimaryButton(title: "Approve held Recall") {
                        Task { await flow.acceptLessonAuthority() }
                    }
                    SecondaryButton(title: "Exclude") {
                        if let id = flow.lessonAuthority?.proposalId {
                            Task { await flow.excludeLessonProposal(id) }
                        }
                    }
                }
            case .held, .recallReady, .completeNoCards:
                PrimaryButton(title: "Return to Today") { closeToToday() }
            case .transfer:
                SecondaryButton(title: "Type instead") {
                    flow.lessonCheckStage = .transferText
                }
            case .transferResume:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Discard") {
                        flow.discardLessonCheckDraft()
                        flow.lessonCheckStage = .transfer
                    }
                    PrimaryButton(title: "Resume") { flow.lessonCheckStage = .transferText }
                }
            case .transferRecording:
                HStack(spacing: 10) {
                    SecondaryButton(title: "Type", fillsWidth: false) {
                        stopIntoText(transfer: true)
                    }
                    PrimaryButton(title: "Stop") { finishRecording(transfer: true) }
                }
            case .transferText, .transferFailed:
                PrimaryButton(
                    title: flow.lessonCheckStage == .transferFailed
                        ? "Try submit again"
                        : "Submit research check",
                    enabled: !flow.lessonCheckDraft
                        .trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                ) {
                    Task { await flow.submitLessonTransfer() }
                }
            case .transferSubmitted:
                PrimaryButton(title: "Open source-backed debrief") {
                    Task { await flow.openTransferDebrief() }
                }
                Button("Done") { closeToToday() }
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.metaAlt)
                    .frame(minHeight: Metrics.minTapTarget)
            case .transferDebrief:
                PrimaryButton(title: "Done") { closeToToday() }
            }
        }
        .padding(.horizontal, Metrics.screenPadding)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    private func startRecording(transfer: Bool) {
        flow.lessonCheckStage = transfer ? .transferRecording : .recording
        speech.start(
            continuing: flow.lessonCheckDraft,
            vocabulary: SpeechVocabulary.terms(for: flow.currentLessonTopicPreview?.topic),
            simulated: flags.simulateSpeech,
            simulate: transfer
                ? "Sequencing, acknowledgements, retransmission, and duplicate handling must sit above best-effort IP."
                : "IP routes packets without delivery or ordering guarantees, so transport adds the guarantees the application needs."
        )
    }

    private func finishRecording(transfer: Bool) {
        Task {
            let text = await speech.finish()
            if !text.isEmpty { flow.updateLessonCheckDraft(text) }
            flow.flushLessonCheckDraft()
            flow.lessonCheckStage = transfer ? .transferText : .text
        }
    }

    private func stopIntoText(transfer: Bool) {
        let text = speech.transcript
        speech.stop()
        if !text.isEmpty { flow.updateLessonCheckDraft(text) }
        flow.flushLessonCheckDraft()
        flow.lessonCheckStage = transfer ? .transferText : .text
    }

    private func backOrClose() {
        speech.stop()
        flow.flushLessonCheckDraft()
        switch flow.lessonCheckStage {
        case .attempt, .restudying:
            flow.lessonCheckStage = .preview
        case .resume, .recording, .text, .submitting, .submitFailed,
             .transfer, .transferResume, .transferRecording, .transferText,
             .transferSubmitting, .transferFailed:
            // Leaving preserves the disk/server draft and the proposal-owned check.
            closeToToday()
        case .authority, .transferDebrief:
            // Authority has already been exposed; a later reopen must use POST
            // and restamp the boundary rather than relying on a cached GET.
            closeToToday()
        case .preview:
            flow.step = .importReady
        default:
            closeToToday()
        }
    }

    private func closeToToday() {
        speech.stop()
        flow.flushLessonCheckDraft()
        if auth.profile?.onboardingCompleted == true, !app.path.isEmpty {
            app.path.removeLast()
        } else {
            flow.step = .empty
        }
    }

    private func title(_ text: String) -> some View {
        Text(text)
            .font(WCFont.serif(27))
            .foregroundStyle(Theme.textStrong)
            .lineSpacing(7)
            .fixedSize(horizontal: false, vertical: true)
            .accessibilityAddTraits(.isHeader)
    }

    private func labelled(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            pilotNote(label)
            Text(value.isEmpty ? "Not supplied." : value)
                .font(WCFont.sans(14))
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func pilotNote(_ text: String) -> some View {
        MetaText(
            text: text, font: WCFont.mono(9.5), tracking: 0.65,
            color: Theme.metaFaint
        )
    }

    private func notice(_ text: String) -> some View {
        InlineNotice {
            Text(text)
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(3)
        }
    }

    private func errorNotice(_ text: String) -> some View {
        InlineNotice {
            Text(text)
                .font(WCFont.sans(13.5))
                .foregroundStyle(Theme.textSecondary)
                .lineSpacing(3)
        }
    }

    private func rubricRows(_ rubric: [String: String]) -> [(label: String, value: String)] {
        let fields = [
            ("Mechanism", ["mechanism", "essential_account"]),
            ("Acceptable alternative", ["acceptable_alternative"]),
            ("Trade-off / depth", ["trade_off", "depth_extension"]),
            ("Failure boundary", ["failure_mode", "boundary_extension"]),
            ("Misconception", ["misconception"])
        ]
        return fields.compactMap { label, keys in
            guard let value = keys.compactMap({ rubric[$0] }).first(where: { !$0.isEmpty })
            else { return nil }
            return (label, value)
        }
    }

    private func provenanceLabel(_ raw: String) -> String {
        LessonContentProvenance(rawValue: raw)?.label
            ?? raw.replacingOccurrences(of: "_", with: " ")
    }

    private static let holdFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE d MMM · HH:mm"
        return formatter
    }()
}

private extension View {
    func lessonBody() -> some View {
        font(WCFont.serif(17))
            .foregroundStyle(Theme.textSerif)
            .lineSpacing(5)
            .fixedSize(horizontal: false, vertical: true)
    }
}
