package com.christrinh.devmax.review

object ReviewReducer {
    fun reduce(state: ReviewUiState, event: ReviewEvent): ReviewUiState {
        if (event is ReviewEvent.SessionLoaded) {
            return loaded(event.scenario, event.localCheckpoint)
        }
        if (event is ReviewEvent.LoadFailed) {
            return ReviewUiState.QuestionFailed(event.note)
        }

        val answering = state as? ReviewUiState.Answering ?: return state
        return when (event) {
            ReviewEvent.ResumeAnswer -> answering.copy(resumeAvailable = false)
            ReviewEvent.StartOver -> answering.copy(
                captureId = null,
                draft = "",
                storedPartial = "",
                resumeAvailable = false,
            )
            is ReviewEvent.SelectInput -> answering.copy(
                inputMode = event.mode,
                captureId = null,
                storedPartial = answering.draft,
            )
            is ReviewEvent.DraftChanged -> answering.copy(
                draft = event.text,
                storedPartial = event.text,
            )
            is ReviewEvent.StartListening -> {
                if (answering.resumeAvailable) answering else answering.copy(
                    inputMode = InputMode.Voice,
                    captureId = event.captureId,
                    storedPartial = answering.draft,
                )
            }
            is ReviewEvent.SpeechPartial -> speechText(answering, event.captureId, event.text, final = false)
            is ReviewEvent.SpeechFinal -> speechText(answering, event.captureId, event.text, final = true)
            is ReviewEvent.SpeechEnded -> {
                if (event.captureId != answering.captureId) answering else answering.copy(
                    captureId = null,
                    storedPartial = answering.draft,
                )
            }
            ReviewEvent.AppBackgrounded -> answering.copy(
                captureId = null,
                storedPartial = answering.draft,
                resumeAvailable = answering.draft.isNotEmpty(),
            )
            is ReviewEvent.SessionLoaded,
            is ReviewEvent.LoadFailed -> answering
        }
    }

    private fun loaded(
        scenario: ReviewScenario,
        localCheckpoint: ReviewCheckpoint?,
    ): ReviewUiState.Answering {
        val matchingLocal = localCheckpoint?.takeIf {
            it.cardId == scenario.card.id && it.sessionId == scenario.session.sessionId
        }
        val draft = matchingLocal?.draftText ?: scenario.session.draftText
        val inputMode = matchingLocal?.inputMode ?: InputMode.Voice
        return ReviewUiState.Answering(
            card = scenario.card,
            session = scenario.session,
            inputMode = inputMode,
            captureId = null,
            draft = draft,
            storedPartial = draft,
            resumeAvailable = draft.isNotEmpty() && (matchingLocal != null || scenario.session.resumed),
        )
    }

    private fun speechText(
        state: ReviewUiState.Answering,
        captureId: Long,
        recognized: String,
        final: Boolean,
    ): ReviewUiState.Answering {
        if (captureId != state.captureId) return state

        // Recognizers may emit an empty teardown callback. It must never erase a
        // non-empty prefix that has already reached the durable draft.
        val combined = if (recognized.isEmpty()) state.draft else joinCapture(
            prefix = state.storedPartial,
            utterance = recognized,
        )
        return state.copy(
            draft = combined,
            captureId = if (final) null else state.captureId,
            storedPartial = if (final) combined else state.storedPartial,
        )
    }

    private fun joinCapture(prefix: String, utterance: String): String {
        if (prefix.isEmpty()) return utterance
        return if (prefix.last().isWhitespace() || utterance.first().isWhitespace()) {
            prefix + utterance
        } else {
            "$prefix $utterance"
        }
    }
}
