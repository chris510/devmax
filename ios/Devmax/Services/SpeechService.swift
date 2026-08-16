import AVFoundation
import Foundation
import Speech

/// Builds one answer from the replaceable hypotheses emitted by consecutive
/// recognition tasks.
///
/// A task's partial result is a hypothesis, not an append-only delta: Apple may
/// revise it until the task reports `isFinal`. Once final, that utterance becomes
/// a stable chunk and a fresh task may transcribe speech after a thinking pause
/// without being allowed to replace anything that came before it.
struct SpeechTranscriptSegment: Equatable {
    let text: String
    let audioStart: TimeInterval
    let audioEnd: TimeInterval
}

struct SpeechTranscriptAccumulator: Equatable {
    private var prefix: String
    private var committedChunks: [String] = []
    private var currentTaskSegments: [SpeechTranscriptSegment] = []
    private var untimedActiveHypothesis = ""

    /// A recognizer may let adjacent word ranges touch or overlap slightly.
    /// Treat a prior segment as revised only when a new snapshot covers most of
    /// that segment, so timing jitter cannot erase the word before a pause.
    private static let replacementCoverage = 0.5
    private static let minimumReplacementOverlap: TimeInterval = 0.05

    init(continuing existing: String = "") {
        prefix = existing
    }

    var text: String {
        Self.join([prefix] + committedChunks + activeParts)
    }

    var hasActiveHypothesis: Bool {
        activeParts.contains {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    var hasFinalizedHypothesis: Bool {
        committedChunks.contains {
            !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
    }

    mutating func updateActiveHypothesis(
        _ text: String,
        segments: [SpeechTranscriptSegment] = []
    ) {
        // An empty interim callback is not evidence that already-recognized
        // speech vanished. Ignoring it keeps the live transcript monotonic while
        // still allowing non-empty corrections inside the current utterance.
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let validSegments = segments.filter {
            !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && $0.audioEnd >= $0.audioStart
        }
        guard let snapshotStart = validSegments.map(\.audioStart).min(),
              let snapshotEnd = validSegments.map(\.audioEnd).max() else {
            // A nonempty transcription normally has timestamped segments. If a
            // callback temporarily omits them, never throw away the timed words
            // we already have; a later timestamped callback can still revise or
            // extend them. Untimed-only recognizers retain the old behavior.
            if currentTaskSegments.isEmpty { untimedActiveHypothesis = text }
            return
        }

        untimedActiveHypothesis = ""
        currentTaskSegments.removeAll { existing in
            let overlap = min(existing.audioEnd, snapshotEnd)
                - max(existing.audioStart, snapshotStart)
            let duration = existing.audioEnd - existing.audioStart
            if duration <= 0 {
                return abs(existing.audioStart - snapshotStart) < 0.03
            }
            return overlap >= Self.minimumReplacementOverlap
                && overlap / duration >= Self.replacementCoverage
        }
        currentTaskSegments.append(contentsOf: validSegments)
        currentTaskSegments.sort {
            if $0.audioStart == $1.audioStart { return $0.audioEnd < $1.audioEnd }
            return $0.audioStart < $1.audioStart
        }
    }

    mutating func finalizeActiveHypothesis() {
        guard hasActiveHypothesis else { return }
        committedChunks.append(Self.join(activeParts))
        currentTaskSegments = []
        untimedActiveHypothesis = ""
    }

    private var activeParts: [String] {
        currentTaskSegments.isEmpty
            ? [untimedActiveHypothesis]
            : currentTaskSegments.map(\.text)
    }

    private static func join(_ parts: [String]) -> String {
        parts.reduce(into: "") { result, part in
            guard !part.isEmpty else { return }
            guard !result.isEmpty else {
                result = part
                return
            }
            if result.last?.isWhitespace == true || part.first?.isWhitespace == true {
                result += part
            } else {
                result += " " + part
            }
        }
    }
}

/// The audio tap lives for the whole user recording while recognition requests
/// roll over between utterances. Swapping the destination under a lock avoids
/// stopping the engine (and losing the first resumed word) after a long pause.
private final class SpeechAudioBufferRouter: @unchecked Sendable {
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var holdingForNextRequest = false
    private var heldBuffers: [AVAudioPCMBuffer] = []
    private var droppedAudio = false
    private static let maximumHeldBuffers = 128

    func replaceRequest(with request: SFSpeechAudioBufferRecognitionRequest) {
        lock.lock()
        self.request = request
        holdingForNextRequest = true
        var buffers = heldBuffers
        heldBuffers = []
        lock.unlock()

        while true {
            buffers.forEach(request.append)
            lock.lock()
            guard !heldBuffers.isEmpty else {
                holdingForNextRequest = false
                lock.unlock()
                return
            }
            buffers = heldBuffers
            heldBuffers = []
            lock.unlock()
        }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        if holdingForNextRequest {
            if heldBuffers.count < Self.maximumHeldBuffers, let copy = Self.copy(buffer) {
                heldBuffers.append(copy)
            } else {
                droppedAudio = true
            }
        } else {
            request?.append(buffer)
        }
        lock.unlock()
    }

    /// Called synchronously from the recognition callback, before its MainActor
    /// hop. Resumed speech waits here until the next request is installed.
    func holdIncomingAudio() {
        lock.lock()
        holdingForNextRequest = true
        lock.unlock()
    }

    var isHoldingForNextRequest: Bool {
        lock.lock()
        defer { lock.unlock() }
        return holdingForNextRequest
    }

    var hasDroppedAudio: Bool {
        lock.lock()
        defer { lock.unlock() }
        return droppedAudio
    }

    func endAudio() {
        lock.lock()
        holdingForNextRequest = false
        heldBuffers = []
        request?.endAudio()
        request = nil
        lock.unlock()
    }

    func clear() {
        lock.lock()
        holdingForNextRequest = false
        heldBuffers = []
        droppedAudio = false
        request = nil
        lock.unlock()
    }

    private static func copy(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(
            pcmFormat: buffer.format,
            frameCapacity: buffer.frameLength
        ) else { return nil }
        copy.frameLength = buffer.frameLength

        let sourceBuffers = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
        let destinationBuffers = UnsafeMutableAudioBufferListPointer(copy.mutableAudioBufferList)
        for (source, destination) in zip(sourceBuffers, destinationBuffers) {
            guard let sourceData = source.mData, let destinationData = destination.mData else { continue }
            memcpy(
                destinationData,
                sourceData,
                min(Int(source.mDataByteSize), Int(destination.mDataByteSize))
            )
        }
        return copy
    }
}

/// Lets the result-handler thread close the audio handoff gap without allowing a
/// stale task to put the current request back into hold mode.
private final class SpeechRecognitionCallbackGate: @unchecked Sendable {
    private let lock = NSLock()
    private var active = true

    func deactivate() {
        lock.lock()
        active = false
        lock.unlock()
    }

    func holdIncomingAudio(in router: SpeechAudioBufferRouter) {
        lock.lock()
        if active { router.holdIncomingAudio() }
        lock.unlock()
    }
}

/// Streaming transcription, kept on device whenever Apple's recognizer supports it.
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

    enum RecognitionBoundary: Equatable {
        case none
        case rollOver
        case retryAfterSilence
        case complete(Finalization.Status)
    }

    @Published private(set) var transcript = ""
    @Published private(set) var captureState: CaptureState = .idle
    @Published private(set) var unavailable = false

    var isRecording: Bool { captureState == .recording }
    var isPreparing: Bool { captureState == .preparing }

    private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private let engine = AVAudioEngine()
    private var task: SFSpeechRecognitionTask?
    private var audioRouter: SpeechAudioBufferRouter?
    private var callbackGate: SpeechRecognitionCallbackGate?
    private var tapInstalled = false
    private var simulationTimer: Timer?
    private var simulationTarget = ""
    private var captureVocabulary: [String] = []
    private var transcriptAccumulator = SpeechTranscriptAccumulator()

    /// Resumed with the final transcription once the recognizer has flushed the
    /// audio still in flight. Non-nil only while `finishResult()` is waiting.
    private var finalization: CheckedContinuation<Finalization, Never>?
    private var finalizationTimeout: Task<Void, Never>?
    private var permissionTask: Task<Void, Never>?
    private var recognitionRestartTask: Task<Void, Never>?
    private var terminalStatus: Finalization.Status?

    /// A recognition callback that arrives after its recording ended must not
    /// write into the next recording's transcript.
    private var generation = 0
    private var endedCaptureTexts: [Int: String] = [:]

    /// Several recognition tasks may belong to one recording. A late callback
    /// from the utterance before a pause must not mutate the next utterance.
    private var recognitionGeneration = 0

    private enum CaptureError: Error { case unavailable }

    /// How long to wait for a final result before handing the latest partial to
    /// editable review. A recognizer that never reports `isFinal` must not hang.
    private static let finalizationDeadline = Duration.seconds(3)

    /// Recording resumes onto existing text rather than replacing it, so
    /// "Tap to keep going" continues the transcript where it stopped.
    ///
    /// `vocabulary` biases recognition toward the card under review — see
    /// `SpeechVocabulary`. It remains scoped to this capture so every internal
    /// recognition-task rollover uses the same terms.
    func start(
        continuing existing: String = "",
        vocabulary: [String] = [],
        simulated: Bool = false,
        simulate text: String = ""
    ) {
        // A previous permission request may still be returning after the screen
        // moved on. Invalidate it before this capture gets its own generation.
        rememberCurrentCapture()
        // If a caller starts again while an earlier finish is still awaiting its
        // last callback, hand that finish its own snapshot before replacing any
        // capture state. Its generation guard below prevents it ending this one.
        completeFinalization(status: .failed)
        permissionTask?.cancel()
        permissionTask = nil
        task?.cancel()
        teardown()
        generation += 1
        let capture = generation

        transcriptAccumulator = SpeechTranscriptAccumulator(continuing: existing)
        transcript = transcriptAccumulator.text
        captureVocabulary = vocabulary
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
        let finishingGeneration = generation
        if let result = consumeNeedsReviewResult() { return result }
        if captureState == .preparing {
            let text = transcript
            endCapture(ifGeneration: finishingGeneration)
            return Finalization(text: text, status: .noCapture)
        }
        guard isRecording else { return Finalization(text: "", status: .noCapture) }
        let finishingText = transcript
        let simulatedCapture = audioRouter == nil

        simulationTimer?.invalidate()
        simulationTimer = nil

        // A successful-final or retry callback can briefly hold resumed audio
        // between recognition requests. Stop the tap first, then let that
        // handoff install its new request and replay everything already queued
        // before ending audio. Otherwise a quick resume-then-Stop loses the tail
        // while still reporting the earlier phrase as a trustworthy final.
        if !simulatedCapture {
            stopEngine()
            await settlePendingAudioHandoff(capture: finishingGeneration)
            if generation != finishingGeneration,
               let endedText = endedCaptureTexts[finishingGeneration] {
                return Finalization(text: endedText, status: .failed)
            }
            if let result = consumeNeedsReviewResult() { return result }
            guard generation == finishingGeneration, isRecording else {
                return Finalization(
                    text: endedCaptureTexts[finishingGeneration] ?? finishingText,
                    status: .failed
                )
            }
        }

        captureState = .finalizing
        recognitionRestartTask?.cancel()
        recognitionRestartTask = nil

        // A simulated transcript is already whatever the typewriter reached.
        // Real capture can temporarily have no task during a silence retry, so
        // task absence is not itself evidence that no audio is in flight.
        //
        // One exit, so the text is bound before `endCapture` clears it rather than
        // by an ordering a later tidy-up could quietly reverse.
        let result: Finalization
        if simulatedCapture {
            result = Finalization(text: transcript, status: .final)
        } else {
            audioRouter?.endAudio()
            let recognized: Finalization = await withCheckedContinuation { continuation in
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
            result = audioRouter?.hasDroppedAudio == true
                ? Finalization(text: recognized.text, status: .failed)
                : recognized
        }

        // `start()` or `stop()` may have superseded this capture while the
        // recognizer was flushing. Never let the old waiter tear down the new
        // generation after it resumes.
        endCapture(ifGeneration: finishingGeneration)
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
    func restore(_ text: String) {
        transcriptAccumulator = SpeechTranscriptAccumulator(continuing: text)
        transcript = text
    }

    static func recognitionBoundary(
        captureState: CaptureState,
        isFinal: Bool,
        hasError: Bool,
        hasFinalizedHypothesis: Bool = false,
        hasActiveHypothesis: Bool = false
    ) -> RecognitionBoundary {
        if hasError {
            if captureState == .finalizing {
                return hasFinalizedHypothesis && !hasActiveHypothesis
                    ? .complete(.final)
                    : .complete(.failed)
            }
            if captureState == .recording,
               hasFinalizedHypothesis,
               !hasActiveHypothesis {
                return .retryAfterSilence
            }
            return .complete(.failed)
        }
        guard isFinal else { return .none }
        switch captureState {
        case .recording: return .rollOver
        case .finalizing: return .complete(.final)
        default: return .none
        }
    }

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

    private func consumeNeedsReviewResult() -> Finalization? {
        guard captureState == .needsReview else { return nil }
        let result = Finalization(text: transcript, status: terminalStatus ?? .failed)
        endCapture()
        return result
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
        // Outside the isRunning check on purpose: leaving the tap installed keeps
        // its router alive long after teardown nils it.
        if tapInstalled {
            engine.inputNode.removeTap(onBus: 0)
            tapInstalled = false
        }
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
    private func endCapture(ifGeneration expectedGeneration: Int? = nil) {
        if let expectedGeneration, generation != expectedGeneration { return }
        rememberCurrentCapture()
        generation += 1
        permissionTask?.cancel()
        permissionTask = nil
        transcript = ""
        transcriptAccumulator = SpeechTranscriptAccumulator()
        captureVocabulary = []
        terminalStatus = nil
        captureState = .idle
        teardown()
    }

    private func teardown() {
        let hadAudioResources = task != nil || audioRouter != nil
            || engine.isRunning || tapInstalled
        recognitionGeneration += 1
        callbackGate?.deactivate()
        callbackGate = nil
        recognitionRestartTask?.cancel()
        recognitionRestartTask = nil
        if hadAudioResources { stopEngine() }
        audioRouter?.clear()
        audioRouter = nil
        task = nil
        simulationTarget = ""
        guard hadAudioResources else { return }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func rememberCurrentCapture() {
        guard captureState != .idle else { return }
        endedCaptureTexts[generation] = transcript
        if endedCaptureTexts.count > 4, let oldest = endedCaptureTexts.keys.min() {
            endedCaptureTexts.removeValue(forKey: oldest)
        }
    }

    private func beginCapture(vocabulary: [String], capture: Int) throws {
        guard recognizer?.isAvailable == true else { throw CaptureError.unavailable }

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let router = SpeechAudioBufferRouter()
        audioRouter = router
        let input = engine.inputNode
        input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buffer, _ in
            router.append(buffer)
        }
        tapInstalled = true

        engine.prepare()
        do {
            try startRecognitionTask(vocabulary: vocabulary, capture: capture)
            try engine.start()
        } catch {
            task?.cancel()
            teardown()
            throw error
        }
    }

    private func startRecognitionTask(vocabulary: [String], capture: Int) throws {
        guard let recognizer, recognizer.isAvailable, let audioRouter else {
            throw CaptureError.unavailable
        }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.taskHint = .dictation
        // Require local recognition where this language/device supports it.
        // Apple may otherwise use its speech-recognition service, while Devmax
        // itself still receives and uploads only the resulting text transcript.
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        // Biases every post-pause task toward the same curriculum vocabulary.
        request.contextualStrings = vocabulary
        callbackGate?.deactivate()
        audioRouter.replaceRequest(with: request)

        recognitionGeneration += 1
        let recognition = recognitionGeneration
        let callbackGate = SpeechRecognitionCallbackGate()
        self.callbackGate = callbackGate

        task = recognizer.recognitionTask(with: request) { [weak self] result, error in
            if result?.isFinal == true || error != nil {
                // Close the callback-to-MainActor gap: the audio tap buffers any
                // speech that resumes before the next request is installed.
                callbackGate.holdIncomingAudio(in: audioRouter)
            }
            guard let self else { return }
            Task { @MainActor in
                guard self.generation == capture,
                      self.recognitionGeneration == recognition else { return }
                if let result {
                    let transcription = result.bestTranscription
                    let segments = transcription.segments.map {
                        SpeechTranscriptSegment(
                            text: $0.substring,
                            audioStart: $0.timestamp,
                            audioEnd: $0.timestamp + $0.duration
                        )
                    }
                    self.transcriptAccumulator.updateActiveHypothesis(
                        transcription.formattedString,
                        segments: segments
                    )
                    self.transcript = self.transcriptAccumulator.text
                }

                switch Self.recognitionBoundary(
                    captureState: self.captureState,
                    isFinal: result?.isFinal == true,
                    hasError: error != nil,
                    hasFinalizedHypothesis: self.transcriptAccumulator.hasFinalizedHypothesis,
                    hasActiveHypothesis: self.transcriptAccumulator.hasActiveHypothesis
                ) {
                case .none:
                    break
                case .rollOver:
                    // Silence completed one recognizer utterance, not the user's
                    // answer. Freeze that chunk and immediately listen for more.
                    self.transcriptAccumulator.finalizeActiveHypothesis()
                    self.transcript = self.transcriptAccumulator.text
                    do {
                        try self.startRecognitionTask(vocabulary: vocabulary, capture: capture)
                    } catch {
                        self.handleRecognitionTermination(status: .failed)
                    }
                case .retryAfterSilence:
                    self.retryRecognitionAfterSilence(
                        vocabulary: vocabulary,
                        capture: capture
                    )
                case let .complete(status):
                    self.handleRecognitionTermination(status: status)
                }
            }
        }
    }

    private func retryRecognitionAfterSilence(vocabulary: [String], capture: Int) {
        recognitionGeneration += 1
        callbackGate?.deactivate()
        callbackGate = nil
        task = nil
        audioRouter?.holdIncomingAudio()
        recognitionRestartTask?.cancel()
        recognitionRestartTask = Task { @MainActor [weak self] in
            do {
                try await Task.sleep(for: .milliseconds(500))
            } catch {
                return
            }
            guard let self,
                  capture == self.generation,
                  self.captureState == .recording else { return }
            self.recognitionRestartTask = nil
            do {
                try self.startRecognitionTask(vocabulary: vocabulary, capture: capture)
            } catch {
                self.handleRecognitionTermination(status: .failed)
            }
        }
    }

    /// Waits for the result callback or scheduled silence retry to replace a
    /// held request. The normal path takes one MainActor turn; the bounded
    /// fallback preserves the latest partial and forces a request so queued
    /// audio is transcribed instead of discarded.
    private func settlePendingAudioHandoff(capture: Int) async {
        guard let audioRouter else { return }
        for _ in 0..<80 {
            guard audioRouter.isHoldingForNextRequest,
                  generation == capture,
                  captureState == .recording else { return }
            do {
                try await Task.sleep(for: .milliseconds(10))
            } catch {
                break
            }
        }
        guard audioRouter.isHoldingForNextRequest,
              generation == capture,
              captureState == .recording else { return }

        recognitionRestartTask?.cancel()
        recognitionRestartTask = nil
        transcriptAccumulator.finalizeActiveHypothesis()
        transcript = transcriptAccumulator.text
        task?.cancel()
        do {
            try startRecognitionTask(vocabulary: captureVocabulary, capture: capture)
        } catch {
            handleRecognitionTermination(status: .failed)
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
