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
    @Published private(set) var transcript = ""
    @Published private(set) var isRecording = false
    @Published private(set) var unavailable = false

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var simulationTimer: Timer?
    private var simulationTarget = ""

    /// Resumed with the final transcription once the recognizer has flushed the
    /// audio still in flight. Non-nil only while `finish()` is waiting.
    private var finalization: CheckedContinuation<String, Never>?
    private var finalizationTimeout: Task<Void, Never>?

    /// A recognition callback that arrives after its recording ended must not
    /// write into the next recording's transcript.
    private var generation = 0

    private enum CaptureError: Error { case unavailable }

    /// How long to wait for a final result before giving up and submitting the
    /// partials. A recognizer that never reports `isFinal` must not hang a submit.
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
        transcript = existing

        if simulated {
            isRecording = true
            startSimulation(fullText: text)
            return
        }

        // The screen flips to its recording state the instant the mic is tapped,
        // so anything spoken before capture actually begins is lost outright.
        // Once permission has been granted — every session after the first —
        // start synchronously rather than paying an await hop to re-learn an
        // answer already on disk. That gap is why answers arrived mid-sentence.
        if permissionsGranted {
            beginCaptureOrDegrade(vocabulary: vocabulary)
            return
        }

        Task {
            guard await requestPermissions() else {
                unavailable = true
                return
            }
            beginCaptureOrDegrade(vocabulary: vocabulary)
        }
    }

    /// Ends recording and waits for the recognizer's final transcription.
    ///
    /// `stop()` cannot serve the submit path: the caller reads the transcript
    /// immediately afterwards, while `SFSpeechRecognizer` delivers its last
    /// corrected result asynchronously *after* the audio ends. Reading
    /// synchronously truncated the tail of every spoken answer.
    func finish() async -> String {
        // Nothing is recording, so there is nothing to hand back. This used to
        // return the stored `transcript`, which is the *previous* turn's answer:
        // "Type instead" finalizes before swapping input modes, so tapping it on a
        // follow-up opened the text box already filled with the answer just given.
        guard isRecording else { return "" }
        isRecording = false

        simulationTimer?.invalidate()
        simulationTimer = nil

        // No recognizer task means nothing is in flight — the simulated
        // transcript is already whatever the typewriter reached.
        guard task != nil else {
            let typed = transcript
            teardown()
            return typed
        }

        stopEngine()

        // endAudio() tells the recognizer no more buffers are coming; it then
        // emits one last `isFinal` result. Releasing the task or deactivating the
        // audio session before that lands is what dropped the end of an answer.
        request?.endAudio()

        let text = await withCheckedContinuation { (continuation: CheckedContinuation<String, Never>) in
            finalization = continuation
            finalizationTimeout = Task { @MainActor [weak self] in
                try? await Task.sleep(for: Self.finalizationDeadline)
                self?.completeFinalization()
            }
        }

        teardown()
        return text
    }

    /// Ends recording and discards anything still in flight. For leaving the
    /// screen or swapping input modes — never for submitting, which needs
    /// `finish()`.
    func stop() {
        isRecording = false
        generation += 1
        simulationTimer?.invalidate()
        simulationTimer = nil
        task?.cancel()
        // Unblocks a `finish()` racing with this teardown.
        completeFinalization()
        teardown()
    }

    func reset() {
        stop()
        transcript = ""
    }

    /// Restores text verbatim after a submit failure, or when swapping input modes.
    func restore(_ text: String) { transcript = text }

    private var permissionsGranted: Bool {
        SFSpeechRecognizer.authorizationStatus() == .authorized
            && AVAudioApplication.shared.recordPermission == .granted
    }

    private func requestPermissions() async -> Bool {
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard speech == .authorized else { return false }
        return await AVAudioApplication.requestRecordPermission()
    }

    private func beginCaptureOrDegrade(vocabulary: [String]) {
        do {
            try beginCapture(vocabulary: vocabulary)
            isRecording = true
        } catch {
            unavailable = true
            isRecording = false
        }
    }

    private func completeFinalization() {
        finalizationTimeout?.cancel()
        finalizationTimeout = nil
        guard let continuation = finalization else { return }
        finalization = nil
        continuation.resume(returning: transcript)
    }

    private func stopEngine() {
        if engine.isRunning { engine.stop() }
        // Outside the isRunning check on purpose: the tap's closure retains the
        // recognition request, so leaving it installed keeps that request alive
        // long after teardown nils it.
        engine.inputNode.removeTap(onBus: 0)
    }

    private func teardown() {
        task = nil
        request = nil
        simulationTarget = ""
        // A capture that has ended owns no text. `AppState.draft` is the one copy
        // that outlives it — mirrored from every partial — so leaving the
        // transcript here only gave the next turn something stale to pick up.
        // Callers that mean to continue an answer say so: `start(continuing:)`
        // and `restore(_:)`.
        transcript = ""
        stopEngine()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func beginCapture(vocabulary: [String]) throws {
        guard let recognizer, recognizer.isAvailable else { throw CaptureError.unavailable }

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
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

        generation += 1
        let capture = generation
        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            Task { @MainActor in
                guard self.generation == capture else { return }
                if let result {
                    self.transcript = prefix + result.bestTranscription.formattedString
                }
                if result?.isFinal == true || error != nil {
                    self.completeFinalization()
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
