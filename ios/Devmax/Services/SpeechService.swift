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

    /// Recording resumes onto existing text rather than replacing it, so
    /// "Tap to keep going" continues the transcript where it stopped.
    func start(continuing existing: String = "", simulated: Bool = false, simulate text: String = "") {
        transcript = existing
        isRecording = true

        if simulated {
            startSimulation(fullText: text)
            return
        }

        Task {
            guard await requestPermissions() else {
                unavailable = true
                isRecording = false
                return
            }
            do { try beginCapture() } catch {
                unavailable = true
                isRecording = false
            }
        }
    }

    func stop() {
        isRecording = false
        simulationTimer?.invalidate()
        simulationTimer = nil
        task?.finish()
        task = nil
        request?.endAudio()
        request = nil
        if engine.isRunning {
            engine.stop()
            engine.inputNode.removeTap(onBus: 0)
        }
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func reset() {
        stop()
        transcript = ""
    }

    /// Restores text verbatim after a submit failure, or when swapping input modes.
    func restore(_ text: String) { transcript = text }

    private func requestPermissions() async -> Bool {
        let speech = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { continuation.resume(returning: $0) }
        }
        guard speech == .authorized else { return false }
        return await withCheckedContinuation { continuation in
            AVAudioSession.sharedInstance().requestRecordPermission { continuation.resume(returning: $0) }
        }
    }

    private func beginCapture() throws {
        guard let recognizer, recognizer.isAvailable else {
            unavailable = true
            return
        }

        let audioSession = AVAudioSession.sharedInstance()
        try audioSession.setCategory(.playAndRecord, mode: .measurement, options: .duckOthers)
        try audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        // Keeps audio on device — this is a private, single-user app.
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
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
                if let result {
                    self.transcript = prefix + result.bestTranscription.formattedString
                }
                if error != nil { self.stop() }
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
