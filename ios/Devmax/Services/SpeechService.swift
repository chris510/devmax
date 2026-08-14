import AVFoundation
import Foundation
import Speech

/// On-device streaming transcription.
///
/// `SFSpeechRecognizer` emits partial results as you speak, which is exactly
/// what the design's live transcript with a trailing caret depicts. Every
/// failure path degrades to the text input rather than blocking the session —
/// the handoff requires a full text-only path for every voice interaction.
@MainActor
final class SpeechService: ObservableObject {
    enum CaptureState: Equatable {
        case idle
        case preparing
        case recording
        case finalizing
        /// Capture ended without a user-initiated, trustworthy final result. The
        /// transcript is deliberately left intact so the caller can move it into
        /// the existing editable text path instead of scoring a silent partial.
        case needsReview
    }

    struct Finalization: Equatable {
        enum Status: Equatable { case final, timedOut, failed, noCapture }

        let text: String
        let status: Status

        var isSafeToSubmit: Bool {
            status == .final && !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    @Published private(set) var transcript = ""
    @Published private(set) var captureState: CaptureState = .idle
    @Published private(set) var unavailable = false

    var isRecording: Bool { captureState == .recording }
    var isPreparing: Bool { captureState == .preparing }

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var simulationTimer: Timer?
    private var simulationTarget = ""

    /// Resumed with the final transcription once the recognizer has flushed the
    /// audio still in flight. Non-nil only while `finishResult()` is waiting.
    private var finalization: CheckedContinuation<Finalization, Never>?
    private var finalizationTimeout: Task<Void, Never>?
    private var permissionTask: Task<Void, Never>?
    private var terminalStatus: Finalization.Status?

    /// A recognition callback that arrives after its recording ended must not
    /// write into the next recording's transcript.
    private var generation = 0

    private enum CaptureError: Error { case unavailable }

    /// How long to wait for a final result before handing the latest partial to
    /// editable review. A recognizer that never reports `isFinal` must not hang.
    private static let finalizationDeadline = Duration.seconds(3)

    /// Recording resumes onto existing text rather than replacing it, so
    /// "Tap to keep going" continues the transcript where it stopped.
    ///
    /// `vocabulary` biases recognition toward the card under review — see
    /// `SpeechVocabulary`. It is threaded through rather than stored: it is only
    /// meaningful for the capture it starts.
    func start(
        continuing existing: String = "",
        vocabulary: [String] = [],
        simulated: Bool = false,
        simulate text: String = ""
    ) {
        // A previous permission request may still be returning after the screen
        // moved on. Invalidate it before this capture gets its own generation.
        permissionTask?.cancel()
        permissionTask = nil
        task?.cancel()
        teardown()
        generation += 1
        let capture = generation

        transcript = existing
        terminalStatus = nil
        unavailable = false

        if simulated {
            captureState = .recording
            startSimulation(fullText: text)
            return
        }

        // The screen flips to its recording state the instant the mic is tapped,
        // so anything spoken before capture actually begins is lost outright.
        // Once permission has been granted — every session after the first —
        // start synchronously rather than paying an await hop to re-learn an
        // answer already on disk. That gap is why answers arrived mid-sentence.
        if permissionsGranted {
            beginCaptureOrDegrade(vocabulary: vocabulary, capture: capture)
            return
        }

        captureState = .preparing
        permissionTask = Task { [weak self] in
            let granted = await Self.requestPermissions()
            guard let self, capture == generation, !Task.isCancelled else { return }
            guard granted else {
                self.unavailable = true
                self.captureState = .needsReview
                return
            }
            self.beginCaptureOrDegrade(vocabulary: vocabulary, capture: capture)
        }
    }

    /// Ends recording and waits for the recognizer's final transcription.
    ///
    /// `stop()` cannot serve the submit path: the caller reads the transcript
    /// immediately afterwards, while `SFSpeechRecognizer` delivers its last
    /// corrected result asynchronously *after* the audio ends. Reading
    /// synchronously truncated the tail of every spoken answer.
    ///
    /// Nothing recording means nothing to hand back — see `endCapture`.
    func finishResult() async -> Finalization {
        if captureState == .needsReview {
            let result = Finalization(text: transcript, status: terminalStatus ?? .failed)
            endCapture()
            return result
        }
        if captureState == .preparing {
            let text = transcript
            endCapture()
            return Finalization(text: text, status: .noCapture)
        }
        guard isRecording else { return Finalization(text: "", status: .noCapture) }
        captureState = .finalizing

        simulationTimer?.invalidate()
        simulationTimer = nil

        // No recognizer task means nothing is in flight — the simulated
        // transcript is already whatever the typewriter reached. Otherwise
        // endAudio() tells the recognizer no more buffers are coming and it emits
        // one last `isFinal` result; releasing the task or deactivating the audio
        // session before that lands is what dropped the end of an answer.
        //
        // One exit, so the text is bound before `endCapture` clears it rather than
        // by an ordering a later tidy-up could quietly reverse.
        let result: Finalization
        if task == nil {
            result = Finalization(text: transcript, status: .final)
        } else {
            stopEngine()
            request?.endAudio()
            result = await withCheckedContinuation { continuation in
                finalization = continuation
                finalizationTimeout = Task { @MainActor [weak self] in
                    do {
                        try await Task.sleep(for: Self.finalizationDeadline)
                    } catch {
                        return
                    }
                    self?.completeFinalization(status: .timedOut)
                }
            }
        }

        endCapture()
        return result
    }

    /// Compatibility seam for unscored voice capture such as Practice Debrief.
    /// Conversation uses `finishResult()` because only it must decide whether the
    /// transcript is trustworthy enough to reach a score.
    func finish() async -> String {
        (await finishResult()).text
    }

    /// Ends recording and discards anything still in flight. For leaving the
    /// screen or swapping input modes — never for submitting, which needs
    /// `finishResult()`.
    func stop() {
        permissionTask?.cancel()
        permissionTask = nil
        simulationTimer?.invalidate()
        simulationTimer = nil
        task?.cancel()
        // Unblocks a `finish()` racing with this stop. It resumes with the
        // transcript, so it has to run before `endCapture` clears it.
        completeFinalization(status: .failed)
        endCapture()
    }

    /// Restores text verbatim after a submit failure, or when swapping input modes.
    func restore(_ text: String) { transcript = text }

    private var permissionsGranted: Bool {
        SFSpeechRecognizer.authorizationStatus() == .authorized
            && AVAudioApplication.shared.recordPermission == .granted
    }

    private static func requestPermissions() async -> Bool {
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard speech == .authorized else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }

    private func beginCaptureOrDegrade(vocabulary: [String], capture: Int) {
        guard capture == generation else { return }
        do {
            try beginCapture(vocabulary: vocabulary, capture: capture)
            captureState = .recording
        } catch {
            unavailable = true
            terminalStatus = .failed
            captureState = .needsReview
        }
    }

    private func completeFinalization(status: Finalization.Status) {
        finalizationTimeout?.cancel()
        finalizationTimeout = nil
        guard let continuation = finalization else { return }
        finalization = nil
        continuation.resume(returning: Finalization(text: transcript, status: status))
    }

    private func handleRecognitionTermination(status: Finalization.Status) {
        terminalStatus = status
        if finalization != nil {
            completeFinalization(status: status)
        } else {
            captureState = .needsReview
            generation += 1
            teardown()
        }
    }

    private func stopEngine() {
        if engine.isRunning { engine.stop() }
        // Outside the isRunning check on purpose: the tap's closure retains the
        // recognition request, so leaving it installed keeps that request alive
        // long after teardown nils it.
        engine.inputNode.removeTap(onBus: 0)
    }

    /// Ends a capture: forget the text, then release the recognizer.
    ///
    /// A capture that has ended owns no text. `AppState.draft` is the copy that
    /// outlives it — mirrored from every partial — so a transcript left behind was
    /// something the *next* turn could pick up: `finish()` handed it to "Type
    /// instead" on a follow-up, which opened pre-filled with the answer already
    /// submitted. Continuing an answer is explicit, through `start(continuing:)`.
    ///
    /// Separate from `teardown()`, which owns AV resources only. Folding the two
    /// together made the read-before-clear ordering in `finish()` and `stop()`
    /// load-bearing but invisible.
    private func endCapture() {
        generation += 1
        permissionTask?.cancel()
        permissionTask = nil
        transcript = ""
        terminalStatus = nil
        captureState = .idle
        teardown()
    }

    private func teardown() {
        let hadAudioResources = task != nil || request != nil || engine.isRunning
        task = nil
        request = nil
        simulationTarget = ""
        guard hadAudioResources else { return }
        stopEngine()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func beginCapture(vocabulary: [String], capture: Int) throws {
        guard let recognizer, recognizer.isAvailable else { throw CaptureError.unavailable }

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        // Keeps audio on device — this is a private, single-user app.
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        // Biases recognition toward this curriculum's vocabulary, which is
        // exactly what a general-purpose language model mishears.
        request.contextualStrings = vocabulary
        self.request = request

        let prefix = transcript.isEmpty ? "" : transcript + " "
        let input = engine.inputNode
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            request.append(buffer)
        }

        engine.prepare()
        try engine.start()

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                guard self.generation == capture else { return }
                if let result {
                    self.transcript = prefix + result.bestTranscription.formattedString
                }
                if error != nil {
                    // An early task failure used to leave the UI saying
                    // LISTENING while later speech went nowhere. Preserve the
                    // latest partial and hand it to editable text instead.
                    self.handleRecognitionTermination(status: .failed)
                } else if result?.isFinal == true {
                    // A recognizer may close an utterance before the stop tap.
                    // It is complete recognition, but not an explicit submit.
                    self.handleRecognitionTermination(status: .final)
                }
            }
        }
    }

    /// The simulator has no usable microphone, so the transcript types itself
    /// out — the same stand-in the prototype uses for streaming STT.
    private func startSimulation(fullText: String) {
        simulationTarget = transcript + (transcript.isEmpty ? "" : " ") + fullText
        simulationTimer?.invalidate()
        simulationTimer = Timer.scheduledTimer(withTimeInterval: 0.045, repeats: true) { [weak self] timer in
            Task { @MainActor in
                guard let self else { return }
                guard self.transcript.count < self.simulationTarget.count else {
                    timer.invalidate()
                    return
                }
                let next = self.simulationTarget.index(
                    self.simulationTarget.startIndex, offsetBy: self.transcript.count + 1
                )
                self.transcript = String(self.simulationTarget[..<next])
            }
        }
    }
}
