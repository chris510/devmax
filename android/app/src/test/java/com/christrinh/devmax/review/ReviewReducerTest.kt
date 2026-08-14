package com.christrinh.devmax.review

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ReviewReducerTest {
    @Test
    fun `local checkpoint wins only for the same session`() {
        val matching = checkpoint(draft = "  exact local draft\n")
        val loaded = ReviewReducer.reduce(
            ReviewUiState.LoadingQuestion,
            ReviewEvent.SessionLoaded(scenario(serverDraft = "server draft", resumed = true), matching),
        ) as ReviewUiState.Answering

        assertEquals("  exact local draft\n", loaded.draft)
        assertEquals(InputMode.Text, loaded.inputMode)
        assertTrue(loaded.resumeAvailable)

        val wrongSession = matching.copy(sessionId = "different")
        val serverWins = ReviewReducer.reduce(
            ReviewUiState.LoadingQuestion,
            ReviewEvent.SessionLoaded(
                scenario(serverDraft = "server draft", resumed = true),
                wrongSession,
            ),
        ) as ReviewUiState.Answering
        assertEquals("server draft", serverWins.draft)
    }

    @Test
    fun `speech partials replace one capture without duplication`() {
        var state = answering(draft = "Typed prefix")
        state = reduce(state, ReviewEvent.StartListening(7))
        state = reduce(state, ReviewEvent.SpeechPartial(7, "spoken"))
        assertEquals("Typed prefix spoken", state.draft)

        state = reduce(state, ReviewEvent.SpeechPartial(7, "spoken words"))
        assertEquals("Typed prefix spoken words", state.draft)

        state = reduce(state, ReviewEvent.SpeechFinal(7, "spoken words exactly"))
        assertEquals("Typed prefix spoken words exactly", state.draft)
        assertNull(state.captureId)
    }

    @Test
    fun `late and empty speech callbacks cannot erase a durable prefix`() {
        var state = reduce(answering(draft = "already safe"), ReviewEvent.StartListening(10))
        state = reduce(state, ReviewEvent.SpeechPartial(10, "new words"))
        assertEquals("already safe new words", state.draft)

        state = reduce(state, ReviewEvent.SpeechPartial(9, "late wrong capture"))
        assertEquals("already safe new words", state.draft)

        state = reduce(state, ReviewEvent.SpeechFinal(10, ""))
        assertEquals("already safe new words", state.draft)
        assertNull(state.captureId)
    }

    @Test
    fun `background stops capture and makes the exact draft resumable`() {
        var state = reduce(answering(), ReviewEvent.StartListening(12))
        state = reduce(state, ReviewEvent.SpeechPartial(12, "  leading and trailing  "))
        state = reduce(state, ReviewEvent.AppBackgrounded)

        assertEquals("  leading and trailing  ", state.draft)
        assertNull(state.captureId)
        assertTrue(state.resumeAvailable)
    }

    @Test
    fun `input changes carry text exactly once`() {
        var state = answering(draft = "one copy")
        state = reduce(state, ReviewEvent.StartListening(13))
        state = reduce(state, ReviewEvent.SpeechPartial(13, "spoken words"))
        state = reduce(state, ReviewEvent.SelectInput(InputMode.Text))
        state = reduce(state, ReviewEvent.SelectInput(InputMode.Voice))

        assertEquals("one copy spoken words", state.draft)
        assertEquals("one copy spoken words", state.storedPartial)
        assertEquals(InputMode.Voice, state.inputMode)
        assertFalse(state.resumeAvailable)
    }

    private fun reduce(
        state: ReviewUiState.Answering,
        event: ReviewEvent,
    ): ReviewUiState.Answering = ReviewReducer.reduce(state, event) as ReviewUiState.Answering

    private fun answering(draft: String = ""): ReviewUiState.Answering =
        ReviewReducer.reduce(
            ReviewUiState.LoadingQuestion,
            ReviewEvent.SessionLoaded(scenario(), null),
        ).let { it as ReviewUiState.Answering }.copy(
            draft = draft,
            storedPartial = draft,
            resumeAvailable = false,
        )

    private fun scenario(
        serverDraft: String = "",
        resumed: Boolean = false,
    ) = ReviewScenario(
        card = DueCardWire(
            id = CARD_ID,
            topic = "Raft leader election",
            category = "Distributed Systems",
            masterySummary = "fuzzy on log safety",
            lastScore = 1,
            recallScore = 1,
            scoreKind = "recall",
            scoringContractVersion = 1,
            dueLabel = "1 day overdue",
            resumable = resumed,
            missedCount = 2,
        ),
        session = SessionStartWire(
            sessionId = SESSION_ID,
            question = "What stops an incomplete log from winning?",
            isFollowUp = false,
            draftText = serverDraft,
            resumed = resumed,
        ),
        speechTrace = SpeechTrace(1, listOf("partial"), "partial"),
    )

    private fun checkpoint(draft: String) = ReviewCheckpoint(
        cardId = CARD_ID,
        sessionId = SESSION_ID,
        draftText = draft,
        inputMode = InputMode.Text,
    )

    private companion object {
        const val CARD_ID = "00000000-0000-0000-0000-0000000000c2"
        const val SESSION_ID = "00000000-0000-0000-0000-00000000a11f"
    }
}
