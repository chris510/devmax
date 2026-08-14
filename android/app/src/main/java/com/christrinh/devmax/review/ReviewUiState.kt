package com.christrinh.devmax.review

sealed interface ReviewUiState {
    data object LoadingQuestion : ReviewUiState

    data class QuestionFailed(val note: String) : ReviewUiState

    data class Answering(
        val card: DueCardWire,
        val session: SessionStartWire,
        val inputMode: InputMode,
        val captureId: Long?,
        val draft: String,
        val storedPartial: String,
        val resumeAvailable: Boolean,
    ) : ReviewUiState
}

sealed interface ReviewEvent {
    data class SessionLoaded(
        val scenario: ReviewScenario,
        val localCheckpoint: ReviewCheckpoint?,
    ) : ReviewEvent

    data class LoadFailed(val note: String) : ReviewEvent
    data object ResumeAnswer : ReviewEvent
    data object StartOver : ReviewEvent
    data class SelectInput(val mode: InputMode) : ReviewEvent
    data class DraftChanged(val text: String) : ReviewEvent
    data class StartListening(val captureId: Long) : ReviewEvent
    data class SpeechPartial(val captureId: Long, val text: String) : ReviewEvent
    data class SpeechFinal(val captureId: Long, val text: String) : ReviewEvent
    data class SpeechEnded(val captureId: Long) : ReviewEvent
    data object AppBackgrounded : ReviewEvent
}
