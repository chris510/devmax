import SwiftUI

/// One continuous thread; no screen change per turn.
struct ConversationScreen: View {
    @EnvironmentObject private var state: AppState
    @EnvironmentObject private var flags: DebugFlags
    @StateObject private var speech = SpeechService()
    @StateObject private var speaker = SpeakerService()
    @AppStorage(Preferences.readAloudKey) private var readAloud = true
    @FocusState private var draftFocused: Bool
    /// Set while `speech.finish()` waits for the recognizer's last result. The
    /// stage is still a recording one during that gap, so without this a second
    /// tap would submit the same answer twice.
    @State private var finalizing = false
    /// The last thread entry spoken. A stage change is also how the app comes
    /// *back* to a turn — a failed submit rewinds to the stage it was answering —
    /// so without this the question restarts over an answer already half given.
    @State private var lastReadID: UUID?

    /// Answer bubbles and the live transcript cap at 84% — of the thread's content
    /// width, not the screen's.
    private static var answerMaxWidth: CGFloat {
        (UIScreen.main.bounds.width - Metrics.conversationPadding * 2) * 0.84
    }

    var body: some View {
        VStack(spacing: 0) {
            StatusBar(rightText: speaker.isSpeaking ? "READING ALOUD" : "UNPROMPTED")
            chrome
            progressRail
            thread
            // Which footer, asked of the stage. `.hidden` is a stage with no session:
            // the control is absent rather than merely disabled, because a live mic
            // over a session that was never created records an answer the app has
            // nowhere to send.
            switch state.stage.footer {
            case .answer: inputArea
            case .result: resultActions
            case .hidden: EmptyView()
            }
        }
        .background(Theme.bg)
        .navigationBarHidden(true)
        // The stage is the only read trigger, because every question arrives with
        // the stage that makes it answerable — a card opening, each probe through
        // `.processing`, turn 3. The thread also grows when the *user* answers,
        // which is what read the question back over the scoring indicator.
        .onChange(of: state.stage) { _, stage in
            if stage.acceptsAnswer && !stage.isRecording { readLatestQuestion() }
        }
        // The transcript used to reach the draft only when recording stopped, so
        // backgrounding mid-answer persisted a stale draft and lost everything
        // spoken since the tap — the worst failure mode in the product. Mirror each
        // partial through; updateDraft debounces the writes.
        //
        // Asked of the recognizer, not the stage. They disagree in both
        // directions: "Type instead" finalizes without leaving a recording stage,
        // so a stage-gated mirror kept running after capture ended — and once
        // `endCapture` clears the transcript, that mirror wrote the empty string
        // back over the draft it had just been handed.
        .onChange(of: speech.transcript) { _, text in
            guard speech.isRecording else { return }
            state.updateDraft(text)
        }
        .onDisappear { speech.stop(); speaker.stop() }
    }

    // MARK: - Chrome

    private var chrome: some View {
        HStack {
            Button {
                speech.stop()
                speaker.stop()
                state.finish()
            } label: {
                Text("✕")
                    .font(.system(size: 19))
                    .foregroundStyle(Theme.metaAlt)
                    .frame(width: Metrics.minTapTarget, height: Metrics.minTapTarget, alignment: .leading)
            }
            .buttonStyle(.plain)
            Spacer()
            MetaText(text: state.conversationLabel, font: WCFont.mono(10.5), tracking: 1.05,
                     color: Theme.metaDimAlt, uppercased: true)
        }
        .padding(.horizontal, Metrics.conversationPadding)
    }

    // MARK: - Progress rail

    /// A second row beneath the chrome whenever a session has more than one card
    /// — any multi-card session, not just a Review Sprint. The ✕ row is left
    /// untouched: the rail is glanceable secondary information, not navigation,
    /// and the dots are not tappable in this pass.
    @ViewBuilder
    private var progressRail: some View {
        if !state.rail.isEmpty {
            Group {
                if flags.railStyle == .chips { chipRail } else { dotRail }
            }
            .padding(.top, 2)
            .padding(.bottom, 10)
            .padding(.horizontal, Metrics.conversationPadding)
        }
    }

    /// The shipped option. The current topic's name carries the literal
    /// information in the chrome slot, so the rail itself stays abstract.
    private var dotRail: some View {
        HStack(spacing: 8) {
            ForEach(state.rail) { stop in
                Circle()
                    .fill(railColor(stop, idle: .clear))
                    .overlay(Circle().strokeBorder(railColor(stop, idle: Theme.border), lineWidth: 1))
                    .frame(width: stop.isCurrent ? 9 : 7, height: stop.isCurrent ? 9 : 7)
                    .animation(Motion.fade, value: stop.coveredScore)
            }
            Spacer(minLength: 0)
        }
    }

    /// The prototyped alternative — more informative, visibly busier at 6–10
    /// items. Kept behind `WC_RAIL_STYLE` for side-by-side comparison only.
    private var chipRail: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(state.rail) { stop in
                        Text(Self.chipLabel(stop.topic).uppercased())
                            .font(WCFont.mono(9.5))
                            .tracking(0.95)
                            .foregroundStyle(railColor(stop, current: Theme.accentSelectedText, idle: Theme.metaDim))
                            .padding(.horizontal, 8)
                            .padding(.vertical, 5)
                            .background(
                                RoundedRectangle(cornerRadius: Metrics.chipRadius)
                                    .fill(stop.isCurrent ? Theme.accentWash : .clear)
                            )
                            .overlay(
                                RoundedRectangle(cornerRadius: Metrics.chipRadius)
                                    .strokeBorder(railColor(stop, idle: Theme.border), lineWidth: 1)
                            )
                            .fixedSize()
                            .id(stop.id)
                    }
                }
            }
            .onChange(of: state.cursor) { _, _ in
                guard let current = state.rail.first(where: \.isCurrent) else { return }
                withAnimation(Motion.fadeFast) { proxy.scrollTo(current.id, anchor: .center) }
            }
        }
    }

    private static func chipLabel(_ topic: String) -> String {
        topic.count > 16 ? String(topic.prefix(15)) + "…" : topic
    }

    /// Not yet reached, current, or covered — the covered stop always carries
    /// that card's score colour, which is the one rule worth writing once.
    private func railColor(
        _ stop: AppState.RailStop, current: Color = Theme.accent, idle: Color
    ) -> Color {
        if stop.isCurrent { return current }
        if let score = stop.coveredScore { return ScoreStyle.color(for: score) }
        return idle
    }

    // MARK: - Thread

    private var thread: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    if state.resumeAvailable { resumeBanner }

                    // Siblings in one switch, so neither can shadow the other —
                    // as a flag checked before `== .loadingQuestion`, the order of
                    // the two branches was load-bearing and invisible.
                    switch state.stage {
                    case .loadingQuestion: questionSkeleton
                    case .questionFailed(let note): questionFailure(note)
                    default: EmptyView()
                    }

                    ForEach(state.thread) { entry in
                        entryView(entry)
                            .frame(maxWidth: .infinity, alignment: alignment(for: entry))
                            .wcFade()
                    }

                    // Live transcript: same position as the answer bubble, but no
                    // bubble, with a trailing accent caret. The stored partial
                    // renders the same way, beneath the question.
                    if isRecording, !speech.transcript.isEmpty {
                        liveTranscript(speech.transcript)
                    } else if !state.draft.isEmpty, state.inputMode == .voice {
                        liveTranscript(state.draft)
                    } else if state.resumeAvailable, !state.storedPartial.isEmpty {
                        liveTranscript(state.storedPartial)
                    }

                    if state.stage == .processing { scoringIndicator }
                    if let result = state.result, state.stage == .result { scoreBlock(result) }

                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, Metrics.conversationPadding)
                .padding(.top, 18)
                .padding(.bottom, 24)
            }
            // Auto-scrolled to the bottom on every new turn or stage change.
            .onChange(of: state.thread.count) { _, _ in scrollToBottom(proxy) }
            .onChange(of: state.stage) { _, _ in scrollToBottom(proxy) }
            .onChange(of: speech.transcript) { _, _ in scrollToBottom(proxy) }
        }
    }

    private func scrollToBottom(_ proxy: ScrollViewProxy) {
        withAnimation(Motion.fadeFast) { proxy.scrollTo("bottom", anchor: .bottom) }
    }

    private func alignment(for entry: ThreadEntry) -> Alignment {
        entry.role == .answer ? .trailing : .leading
    }

    @ViewBuilder
    private func entryView(_ entry: ThreadEntry) -> some View {
        switch entry.role {
        case .question:
            Text(entry.text)
                .font(TypeRole.question)
                .tracking(-0.25)
                .lineSpacing(25 * 1.32 - 25 * 1.2)
                .foregroundStyle(Theme.textStrong)
                .fixedSize(horizontal: false, vertical: true)
        case .followUpQuestion, .reattemptQuestion, .coachingQuestion:
            // Both are prefaced so they read as probes rather than new cards, and
            // both use the same serif 21 — the preface carries the distinction,
            // not a new type role.
            Text(entry.text)
                .font(TypeRole.followUp)
                .tracking(-0.21)
                .lineSpacing(21 * 1.32 - 21 * 1.2)
                .foregroundStyle(Theme.textStrong)
                .fixedSize(horizontal: false, vertical: true)
        case .coachingFeedback:
            Text(entry.text)
                .font(TypeRole.scoreFeedback)
                .lineSpacing(18.5 * 1.5 - 18.5 * 1.2)
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)
        case .answer:
            Text(entry.text)
                .font(WCFont.sans(15))
                .lineSpacing(15 * 1.5 - 15 * 1.2)
                .foregroundStyle(Theme.textSecondary)
                .padding(.horizontal, 15)
                .padding(.vertical, 13)
                .background(Theme.bubble, in: RoundedRectangle(cornerRadius: Metrics.bubbleRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: Metrics.bubbleRadius)
                        .strokeBorder(Theme.bubbleBorder, lineWidth: 1)
                )
                .frame(maxWidth: Self.answerMaxWidth, alignment: .trailing)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    /// The question is generated on engagement, so it lands a beat after the
    /// screen opens. Static skeleton lines in question geometry — the same
    /// vocabulary as the Today loading state, rather than new motion.
    private var questionSkeleton: some View {
        VStack(alignment: .leading, spacing: 12) {
            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton1).frame(height: 18)
            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton2).frame(height: 18)
                .padding(.trailing, 60)
            RoundedRectangle(cornerRadius: 3).fill(Theme.skeleton3).frame(height: 18)
                .padding(.trailing, 150)
        }
    }

    /// The question never arrived. The app's offline treatment, in the one place
    /// the design had no state for — only the headline differs, so it is the shared
    /// body rather than a second copy of the vocabulary.
    private func questionFailure(_ note: String) -> some View {
        LoadFailureBody(title: "Couldn't load the question.", note: note) {
            Task { await state.retryQuestion() }
        }
        .wcFade()
    }

    private func liveTranscript(_ text: String) -> some View {
        // The caret is concatenated into the same Text so it trails the last word
        // inline, rather than being pushed to the edge of the block.
        (
            Text(text)
                // AA-compliant on #0d0f11 — deliberately not darkened to mark
                // "in progress".
                .foregroundColor(Theme.textMuted)
                + Text("▍").foregroundColor(Theme.accent)
        )
        .font(WCFont.sans(15))
        .lineSpacing(15 * 1.5 - 15 * 1.2)
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: Self.answerMaxWidth, alignment: .leading)
        .frame(maxWidth: .infinity, alignment: .trailing)
    }

    private var scoringIndicator: some View {
        HStack(spacing: 8) {
            MetaText(text: "SCORING", font: TypeRole.metaBody, tracking: 1.2, color: Theme.metaAlt)
            ScoringDots()
        }
        .wcFade(Motion.fadeFast)
    }

    private var resumeBanner: some View {
        Group {
            InlineNotice {
                VStack(alignment: .leading, spacing: 12) {
                    Text("You were mid-answer here 14 hours ago. Your partial answer was saved.")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.textSecondary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 10) {
                        Button { state.resumeAnswer() } label: {
                            Text("Resume answer")
                                .font(TypeRole.secondaryAction)
                                .foregroundStyle(Theme.accentInk)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                                .background(Theme.accent, in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain)
                        Button { state.startOver() } label: {
                            Text("Start over")
                                .font(TypeRole.secondaryAction)
                                .foregroundStyle(Theme.textMuted)
                                .padding(.horizontal, 14).padding(.vertical, 9)
                                .overlay(RoundedRectangle(cornerRadius: 10)
                                    .strokeBorder(Theme.border, lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .wcFade()
    }

    // MARK: - Score block

    private func scoreBlock(_ result: SessionResult) -> some View {
        VStack(alignment: .leading, spacing: 16) {
            Hairline().padding(.top, 10)

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text("\(result.score)")
                    .font(TypeRole.bigScoreNumeral)
                    .monospacedDigit()
                    .foregroundStyle(ScoreStyle.color(for: result.score))
                MetaText(text: "/ 5 RECALL", font: TypeRole.metaBody, tracking: 1.2, color: Theme.metaAlt)
            }

            Text(result.feedback)
                .font(TypeRole.scoreFeedback)
                .lineSpacing(18.5 * 1.5 - 18.5 * 1.2)
                .foregroundStyle(Theme.textSerif)
                .fixedSize(horizontal: false, vertical: true)

            MetaText(text: result.scheduleLine, font: TypeRole.metaBody, tracking: 1.0, color: Theme.metaFaint)
        }
        .padding(.top, 14)
        .wcSettle()
    }

    private var resultActions: some View {
        VStack(spacing: 14) {
            if state.hasMoreCards {
                HStack(spacing: 12) {
                    PrimaryButton(title: state.sessionEndLabel) { state.nextCard() }
                    SecondaryButton(title: "Done", fillsWidth: false) { state.finish() }
                }
            } else {
                // `See recap` on the last card of a multi-card run; `Done` alone
                // ends a single-card session.
                PrimaryButton(title: state.sessionEndLabel) { state.nextCard() }
            }

            // Turn 3, offered only when the mechanism was wrong — the one band where
            // the feedback above states the correct answer outright. A sibling of
            // the history link, not a new component: same type role, same color,
            // same tap target. The score block itself is unchanged.
            if state.result?.reattemptOffered == true {
                Button { state.beginReattempt() } label: {
                    Text("Say it back in your own words")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
            }

            if state.result?.coachingOffered == true {
                Button { state.beginCoaching() } label: {
                    Text("Go one level deeper")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.meta)
                }
                .buttonStyle(.plain)
                .frame(minHeight: Metrics.minTapTarget)
            }

            Button {
                if let card = state.currentCard {
                    state.path.append(.history(card.id))
                }
            } label: {
                Text("View history for this card")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)
        }
        .padding(.horizontal, Metrics.conversationPadding)
        .padding(.top, 14)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    // MARK: - Input

    private var isRecording: Bool { state.stage.isRecording }
    private var canAnswer: Bool { state.stage.acceptsAnswer }

    @ViewBuilder
    private var inputArea: some View {
        VStack(spacing: 14) {
            if state.submitError { submitFailureStrip }

            if state.inputMode == .voice {
                voiceInput
            } else {
                textInput
            }
        }
        .padding(.horizontal, Metrics.conversationPadding)
        .padding(.top, 12)
        .padding(.bottom, Metrics.bottomSafeArea)
        .background(Theme.bg)
    }

    /// Directly above the control, same treatment as the resume banner.
    private var submitFailureStrip: some View {
        InlineNotice {
            HStack(spacing: 12) {
                Text("Couldn't submit — your answer is saved.")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.textSecondary)
                Spacer(minLength: 0)
                Button { Task { await state.submit(state.draft) } } label: {
                    Text("Try again")
                        .font(TypeRole.secondaryAction)
                        .foregroundStyle(Theme.accent)
                }
                .buttonStyle(.plain)
            }
        }
        .wcFade(Motion.fadeFast)
    }

    private var voiceInput: some View {
        VStack(spacing: 12) {
            Button(action: toggleRecording) {
                ZStack {
                    Circle()
                        .fill(isRecording ? Theme.accentWash : Color.clear)
                        .overlay(
                            Circle().strokeBorder(
                                isRecording ? Theme.accent : Theme.dottedUnderline, lineWidth: 1
                            )
                        )
                        .frame(width: Metrics.micDiameter, height: Metrics.micDiameter)

                    if isRecording { PulsingRing().frame(width: Metrics.micDiameter, height: Metrics.micDiameter) }

                    // Recording turns the dot into a 20px stop glyph.
                    RoundedRectangle(cornerRadius: isRecording ? 4 : 10)
                        .fill(Theme.accent)
                        .frame(width: isRecording ? 20 : 18, height: isRecording ? 20 : 18)
                }
                .frame(width: Metrics.micHitArea, height: Metrics.micHitArea)
                .contentShape(Circle())
            }
            .buttonStyle(.plain)
            .disabled(!canAnswer)
            .opacity(canAnswer ? 1 : 0.4)

            MetaText(text: micLabel, font: TypeRole.metaRow, tracking: 1.2, color: Theme.metaAlt)

            Button {
                // Swapping input mode carries the text across and never navigates
                // away — so it has to finalize like a submit does, not discard.
                // Reading the transcript straight after stop() dropped whatever
                // was still in flight. On a turn with no live capture `finish()`
                // returns nothing, so `state.draft` is the value — not a fallback.
                Task {
                    let spoken = await speech.finish()
                    state.updateDraft(spoken.isEmpty ? state.draft : spoken)
                    state.inputMode = .text
                    draftFocused = true
                }
            } label: {
                Text("Type instead")
                    .font(TypeRole.secondaryAction)
                    .foregroundStyle(Theme.meta)
            }
            .buttonStyle(.plain)
            .frame(minHeight: Metrics.minTapTarget)
        }
    }

    private var micLabel: String {
        if isRecording { return "LISTENING — TAP TO STOP" }
        // A stored partial counts: the mic continues that answer rather than
        // starting a new one.
        if !state.draft.isEmpty || state.resumeAvailable { return "TAP TO KEEP GOING" }
        return "TAP TO ANSWER"
    }

    private func toggleRecording() {
        speaker.stop()
        if isRecording {
            // The stage must stay the recording one until `submit` reads it:
            // it carries which turn this answer belongs to, and `sendAnswer`
            // rewinds to it if the submit fails.
            guard !finalizing else { return }
            finalizing = true
            Task {
                // finish(), not stop(): the recognizer's last corrected result
                // arrives after the audio ends, and reading the transcript
                // synchronously cut the end off every spoken answer.
                let text = await speech.finish()
                finalizing = false
                state.updateDraft(text)
                // Submitting is the other moment a pending debounce can't be waited out.
                state.flushDraft()
                await state.submit(text)
            }
        } else {
            state.submitError = false
            state.stage = state.stage.recordingTwin
            speech.start(
                continuing: state.draft,
                vocabulary: SpeechVocabulary.terms(for: state.currentCard?.topic),
                simulated: flags.simulateSpeech,
                simulate: Self.simulatedAnswer(for: state.stage)
            )
        }
    }

    private var textInput: some View {
        VStack(spacing: 12) {
            // Only edits made through the control are drafts. `sendAnswer` clears
            // the in-memory copy while scoring so the committed answer is not
            // drawn twice, but the disk backup must survive until the response
            // lands. A blanket `onChange(of: state.draft)` could not distinguish
            // those two writes and erased that backup mid-request.
            TextEditor(text: Binding(
                get: { state.draft },
                set: { state.updateDraft($0) }
            ))
                .font(WCFont.sans(15))
                .foregroundStyle(Theme.textSecondary)
                .scrollContentBackground(.hidden)
                .focused($draftFocused)
                .frame(height: 84)  // three rows
                .padding(.horizontal, 12)
                .padding(.vertical, 10)
                .background(Theme.surface, in: RoundedRectangle(cornerRadius: Metrics.inputRadius))
                .overlay(
                    RoundedRectangle(cornerRadius: Metrics.inputRadius)
                        .strokeBorder(draftFocused ? Theme.accent : Theme.borderStrong, lineWidth: 1)
                )

            PrimaryButton(
                title: "Submit answer",
                enabled: canAnswer && !state.draft.trimmingCharacters(in: .whitespaces).isEmpty
            ) {
                state.submitError = false
                Task { await state.submit(state.draft) }
            }

            SecondaryButton(title: "Voice") {
                speech.restore(state.draft)
                state.inputMode = .voice
                draftFocused = false
            }
        }
    }

    // MARK: - TTS

    /// What may be read aloud: the four roles that ask the user something. An
    /// answer is the user's own words, and coaching feedback is prose the score
    /// block already shows — neither is a question waiting to be answered.
    private static let spokenRoles: [ThreadEntry.Role] = [
        .question, .followUpQuestion, .reattemptQuestion, .coachingQuestion,
    ]

    /// The entry to speak, or nil when the newest question was already spoken.
    /// Static and pure so the rule is exercised without a view: it decides what
    /// TTS says, and getting it wrong re-reads a question mid-answer.
    static func entryToSpeak(in thread: [ThreadEntry], lastSpoken: UUID?) -> ThreadEntry? {
        guard let entry = thread.last(where: { Self.spokenRoles.contains($0.role) }),
              entry.id != lastSpoken
        else { return nil }
        return entry
    }

    private func readLatestQuestion() {
        // Two independent gates. `WC_TTS` is the screenshot override and stays
        // debug-only; `readAloud` is the user's, and is the one that works in a
        // Release build — where `WC_TTS` reads its default and is always true.
        guard flags.ttsEnabled, readAloud,
              let entry = Self.entryToSpeak(in: state.thread, lastSpoken: lastReadID)
        else { return }
        lastReadID = entry.id
        speaker.speak(entry.text)
    }

    /// Fixture answers for the simulated-speech path, matching the prototype.
    ///
    /// The re-attempt case is not decoration: replaying turn 1 here would dictate a
    /// verbatim repeat of the answer the model just corrected, which is precisely
    /// the parroting `REATTEMPT_RUBRIC` scores as a 1.
    private static func simulatedAnswer(for stage: Stage) -> String {
        switch stage {
        case .recordingFollowUp:
            return "Each physical node gets many positions on the ring, so a new node picks up lots of small slices instead of one big one, which spreads the transfer across all the existing nodes."
        case .recordingReattempt:
            return "Right — so it's the arc, not the node name. Each node owns the stretch of hash space that ends at its own position, so a new node only takes over the part of its neighbour's stretch that now falls behind it."
        default:
            return "So the key space is a ring of hashes, and each node owns the arc that ends at its own position. When you add a node, it takes over part of one neighbour's arc, so only the keys in that slice move — everything else stays put. That's the whole point versus mod-N hashing, where changing N reshuffles nearly everything."
        }
    }
}
