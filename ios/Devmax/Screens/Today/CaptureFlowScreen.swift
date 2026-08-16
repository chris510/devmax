import SwiftUI

struct CaptureFlowScreen: View {
    @EnvironmentObject private var state: AppState
    @State private var route: AppState.CaptureRoute
    private let inboxBackTitle: String
    private let closeAction: (() -> Void)?

    init(
        route: AppState.CaptureRoute,
        inboxBackTitle: String = "← Today",
        close: (() -> Void)? = nil
    ) {
        _route = State(initialValue: route)
        self.inboxBackTitle = inboxBackTitle
        closeAction = close
    }

    var body: some View {
        Group {
            switch route {
            case .inbox:
                CaptureInboxScreen(
                    open: { route = .review($0) },
                    backTitle: inboxBackTitle,
                    close: closeAction ?? state.closeCaptureFlow
                )
            case .review(let id):
                CaptureReviewScreen(
                    captureID: id,
                    back: { route = .inbox },
                    close: closeAction ?? state.closeCaptureFlow
                )
            }
        }
        .background(Theme.bg.ignoresSafeArea())
    }
}

private struct CaptureInboxScreen: View {
    @EnvironmentObject private var state: AppState
    let open: (UUID) -> Void
    let backTitle: String
    let close: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            captureHeader("Card inbox", backTitle: backTitle, back: close)

            ScrollView {
                LazyVStack(spacing: 0) {
                    if state.captures.isEmpty {
                        Text("No captured gaps.")
                            .font(TypeRole.bodyLarge)
                            .foregroundStyle(Theme.textSecondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.top, 24)
                    } else {
                        ForEach(state.captures) { capture in
                            Hairline()
                            Button { open(capture.id) } label: {
                                VStack(alignment: .leading, spacing: 7) {
                                    HStack(alignment: .firstTextBaseline) {
                                        Text(capture.topic)
                                            .font(TypeRole.rowTopic)
                                            .foregroundStyle(Theme.text)
                                        Spacer(minLength: 12)
                                        Text("→")
                                            .font(WCFont.mono(11))
                                            .foregroundStyle(Theme.accent)
                                    }
                                    if !capture.context.isEmpty {
                                        Text(capture.context)
                                            .font(TypeRole.rowSummary)
                                            .foregroundStyle(Theme.textDim)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                    MetaText(
                                        text: statusText(capture),
                                        font: TypeRole.metaBody, tracking: 0.7,
                                        color: Theme.metaFaint
                                    )
                                }
                                .padding(.vertical, 16)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                        Hairline()
                    }
                }
                .padding(.horizontal, Metrics.screenPadding)
                .padding(.bottom, Metrics.bottomSafeArea)
            }
        }
        .task { await state.refreshCaptures() }
    }

    private func statusText(_ capture: CaptureSummary) -> String {
        if capture.status == "ready_to_review" { return "Ready to review" }
        return capture.needsSource ? "Needs source" : "Continue review"
    }
}

private struct CaptureReviewScreen: View {
    private enum Step { case source, rubric, question, missingBasis }

    @EnvironmentObject private var state: AppState
    let captureID: UUID
    let back: () -> Void
    let close: () -> Void

    @State private var capture: PendingCapture?
    @State private var step: Step = .source
    @State private var sourceUrl = ""
    @State private var sourceSection = ""
    @State private var sourceLabel = ""
    @State private var answerBasis = ""
    @State private var rubric = AnswerRubric()
    @State private var question = ""
    @State private var schedule = "next"
    @State private var rubricOpen = false
    @State private var pending = false
    @State private var errorText = ""

    var body: some View {
        VStack(spacing: 0) {
            StatusBar()
            captureHeader(title, backTitle: "← Inbox", back: back)

            if let capture {
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        topicBlock(capture)
                        switch step {
                        case .source: sourceStep
                        case .rubric: rubricStep
                        case .question: questionStep
                        case .missingBasis: missingBasisStep
                        }
                        if !errorText.isEmpty { errorNotice }
                    }
                    .padding(.horizontal, Metrics.screenPadding)
                    .padding(.bottom, Metrics.bottomSafeArea)
                }
            } else {
                Spacer()
                ProgressView().tint(Theme.meta)
                Spacer()
            }
        }
        .task { await loadCapture() }
    }

    private var title: String {
        switch step {
        case .source: return "Review gap · Source"
        case .rubric: return "Review gap · Rubric"
        case .question: return "Review gap · Question"
        case .missingBasis: return "Missing basis"
        }
    }

    private func loadCapture() async {
        guard let value = try? await state.api.capture(captureID) else { back(); return }
        capture = value
        sourceUrl = value.sourceUrl
        sourceSection = value.sourceSection
        sourceLabel = value.sourceLabel
        answerBasis = value.answerBasis
        rubric = value.answerRubric
        question = value.canonicalQuestion
        if value.status == "ready_to_review" && !value.canonicalQuestion.isEmpty {
            step = .question
        } else if !value.needsSource && !value.answerBasis.isEmpty {
            step = .rubric
        } else {
            step = .source
        }
    }

    private func topicBlock(_ capture: PendingCapture) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(capture.topic)
                .font(TypeRole.historyTitle)
                .foregroundStyle(Theme.text)
            if !capture.context.isEmpty {
                Text(capture.context)
                    .font(TypeRole.rowSummary)
                    .foregroundStyle(Theme.textDim)
            }
        }
        .padding(.bottom, 4)
    }

    private var sourceStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            captureField("SOURCE URL", text: $sourceUrl)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
            captureField("SOURCE LABEL", text: $sourceLabel)
            captureField("SECTION · OPTIONAL", text: $sourceSection)
            captureEditor("TRUSTED ANSWER BASIS", text: $answerBasis, minHeight: 120)

            PrimaryButton(title: pending ? "Saving…" : "Continue", enabled: !pending) {
                guard hasSource, hasBasis else {
                    step = .missingBasis
                    return
                }
                Task { await saveSource() }
            }

            Button("Discard capture") { Task { await discard() } }
                .font(TypeRole.secondaryAction)
                .foregroundStyle(Theme.meta)
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
        }
    }

    private var rubricStep: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let label = capture?.sourceDisplay, !label.isEmpty {
                MetaText(
                    text: "SOURCE · \(label)", font: TypeRole.metaBody,
                    tracking: 0.8, color: Theme.metaFaint
                )
            }
            rubricEditor("REQUIRED MECHANISM", text: $rubric.mechanism)
            rubricEditor("ACCEPTABLE ALTERNATIVE", text: $rubric.acceptableAlternative)
            rubricEditor("KEY TRADE-OFF", text: $rubric.tradeOff)
            rubricEditor("KEY FAILURE MODE", text: $rubric.failureMode)
            rubricEditor("COMMON MISCONCEPTION", text: $rubric.misconception)

            PrimaryButton(
                title: pending ? "Preparing…" : "Review question",
                enabled: rubric.isComplete && !pending
            ) { Task { await prepareQuestion() } }
        }
    }

    private var questionStep: some View {
        VStack(alignment: .leading, spacing: 16) {
            captureEditor("CANONICAL QUESTION", text: $question, minHeight: 112, serif: true)

            Button { rubricOpen.toggle() } label: {
                HStack {
                    MetaText(
                        text: "ANSWER RUBRIC", font: TypeRole.metaBody,
                        tracking: 1.0, color: Theme.meta
                    )
                    Spacer()
                    Text(rubricOpen ? "▲" : "▼")
                        .font(.system(size: 9))
                        .foregroundStyle(Theme.metaDim)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)

            if rubricOpen {
                VStack(alignment: .leading, spacing: 12) {
                    rubricReadout("Mechanism", rubric.mechanism)
                    rubricReadout("Alternative", rubric.acceptableAlternative)
                    rubricReadout("Trade-off", rubric.tradeOff)
                    rubricReadout("Failure mode", rubric.failureMode)
                    rubricReadout("Misconception", rubric.misconception)
                }
                .wcFade(Motion.fadeFast)
            }

            ScheduleChoice(schedule: $schedule)

            PrimaryButton(
                title: pending ? "Adding…" : "Add to reviews",
                enabled: !pending && !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ) { Task { await activate() } }
        }
    }

    private var missingBasisStep: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("This needs a source before it can become a review card.")
                .font(TypeRole.bodyLarge)
                .foregroundStyle(Theme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            PrimaryButton(title: "Add source") { step = .source }
            SecondaryButton(title: "Keep in inbox", action: back)
        }
    }

    private var errorNotice: some View {
        InlineNotice {
            Text(errorText)
                .font(TypeRole.secondaryAction)
                .foregroundStyle(Theme.textSecondary)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .wcFade(Motion.fadeFast)
    }

    private var hasSource: Bool {
        [sourceUrl, sourceSection, sourceLabel]
            .contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    }

    private var hasBasis: Bool {
        !answerBasis.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private var update: CaptureUpdateRequest {
        CaptureUpdateRequest(
            sourceUrl: sourceUrl, sourceSection: sourceSection, sourceLabel: sourceLabel,
            answerBasis: answerBasis, answerRubric: rubric, canonicalQuestion: question
        )
    }

    private func saveSource() async {
        await perform("Couldn't save the source. Your edits are still here.") {
            let saved = try await state.api.updateCapture(captureID, update: update)
            adopt(saved)
            step = .rubric
        }
    }

    private func prepareQuestion() async {
        await perform("Couldn't prepare the question. Your rubric is still here.") {
            let saved = try await state.api.updateCapture(captureID, update: update)
            adopt(saved)
            let prepared = try await state.api.prepareCaptureQuestion(captureID, regenerate: false)
            adopt(prepared)
            step = .question
        }
    }

    private func activate() async {
        await perform("Couldn't add the card. The grounded capture is still in your inbox.") {
            let saved = try await state.api.updateCapture(captureID, update: update)
            adopt(saved)
            let activated = try await state.api.activateCapture(captureID, schedule: schedule)
            if state.libraryLoad == .ready {
                state.library.removeAll { $0.id == activated.id }
                state.library.append(activated)
                state.library.sort { $0.nextReviewAt < $1.nextReviewAt }
            }
            async let refreshed = try? state.api.captures()
            async let due = try? state.api.due()
            if let value = await refreshed { state.captures = value }
            if let value = await due { state.queue = value }
            close()
        }
    }

    private func discard() async {
        await perform("Couldn't discard this capture.") {
            try await state.api.discardCapture(captureID)
            state.captures.removeAll { $0.id == captureID }
            back()
        }
    }

    private func perform(_ message: String, operation: @escaping () async throws -> Void) async {
        pending = true
        errorText = ""
        do { try await operation() } catch { errorText = message }
        pending = false
    }

    private func adopt(_ value: PendingCapture) {
        capture = value
        question = value.canonicalQuestion
        if let index = state.captures.firstIndex(where: { $0.id == value.id }) {
            state.captures[index] = value.summary
        } else if value.status != "activated" {
            state.captures.insert(value.summary, at: 0)
        }
    }

    private func rubricReadout(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            MetaText(text: label.uppercased(), font: WCFont.mono(9.5), tracking: 0.8)
            Text(value)
                .font(TypeRole.rowSummary)
                .foregroundStyle(Theme.textDim)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func rubricEditor(_ label: String, text: Binding<String>) -> some View {
        captureEditor(label, text: text, minHeight: 76)
    }
}

struct ScheduleChoice: View {
    @Binding var schedule: String

    var body: some View {
        HStack(spacing: 10) {
            button("Now", value: "now")
            button("Next review", value: "next")
        }
    }

    private func button(_ label: String, value: String) -> some View {
        let selected = schedule == value
        return Button { schedule = value } label: {
            Text(label)
                .font(TypeRole.secondaryAction)
                .foregroundStyle(selected ? Theme.accentSelectedText : Theme.textMuted)
                .frame(maxWidth: .infinity)
                .padding(.vertical, 11)
                .background(
                    RoundedRectangle(cornerRadius: 10)
                        .fill(selected ? Theme.accentWash : .clear)
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 10)
                        .strokeBorder(selected ? Theme.accent : Theme.border, lineWidth: 1)
                )
        }
        .buttonStyle(.plain)
    }
}

private func captureHeader(
    _ title: String, backTitle: String, back: @escaping () -> Void
) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        Button(backTitle, action: back)
            .font(TypeRole.secondaryAction)
            .foregroundStyle(Theme.meta)
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget, alignment: .leading)
        Text(title)
            .font(TypeRole.screenTitle)
            .tracking(-0.5)
            .foregroundStyle(Theme.text)
    }
    .frame(maxWidth: .infinity, alignment: .leading)
    .padding(.horizontal, Metrics.screenPadding)
    .padding(.bottom, 18)
}

private func captureField(_ label: String, text: Binding<String>) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        MetaText(text: label, font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint)
        TextField("", text: text)
            .font(TypeRole.rowSummary)
            .foregroundStyle(Theme.text)
            .padding(.horizontal, 13)
            .padding(.vertical, 12)
            .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
            .overlay(
                RoundedRectangle(cornerRadius: Metrics.inputRadius)
                    .strokeBorder(Theme.borderStrong, lineWidth: 1)
            )
    }
}

func captureEditor(
    _ label: String, text: Binding<String>, minHeight: CGFloat, serif: Bool = false
) -> some View {
    VStack(alignment: .leading, spacing: 8) {
        MetaText(text: label, font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint)
        TextEditor(text: text)
            .font(serif ? TypeRole.masterySummary : TypeRole.rowSummary)
            .foregroundStyle(Theme.text)
            .scrollContentBackground(.hidden)
            .frame(minHeight: minHeight)
            .padding(10)
            .background(Theme.inputFill, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
            .overlay(
                RoundedRectangle(cornerRadius: Metrics.inputRadius)
                    .strokeBorder(Theme.borderStrong, lineWidth: 1)
            )
    }
}
