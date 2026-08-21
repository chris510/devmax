package com.christrinh.devmax.review

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ReviewDraftStoreTest {
    private val context = ApplicationProvider.getApplicationContext<android.content.Context>()
    private val store = AndroidReviewDraftStore(context)

    @Before
    fun clearBefore() {
        store.clear(CARD_ID, SESSION_ID)
        store.clear(OTHER_CARD_ID, OTHER_SESSION_ID)
    }

    @After
    fun clearAfter() {
        store.clear(CARD_ID, SESSION_ID)
        store.clear(OTHER_CARD_ID, OTHER_SESSION_ID)
    }

    @Test
    fun freshStoreInstanceRestoresExactCheckpointWithListeningStopped() {
        val expected = ReviewCheckpoint(
            cardId = CARD_ID,
            sessionId = SESSION_ID,
            draftText = "  exact partial\nwith punctuation—untouched  ",
            inputMode = InputMode.Voice,
        )

        store.write(expected)

        val restored = AndroidReviewDraftStore(context).read(CARD_ID, SESSION_ID)
        assertEquals(expected, restored)

        val state = ReviewReducer.reduce(
            ReviewUiState.LoadingQuestion,
            ReviewEvent.SessionLoaded(scenario(), restored),
        ) as ReviewUiState.Answering
        assertEquals(null, state.captureId)
    }

    @Test
    fun checkpointsForDifferentSessionsDoNotOverwriteEachOther() {
        val first = checkpoint(CARD_ID, SESSION_ID, "first partial")
        val second = checkpoint(OTHER_CARD_ID, OTHER_SESSION_ID, "second partial")

        store.write(first)
        store.write(second)

        assertEquals(first, AndroidReviewDraftStore(context).read(CARD_ID, SESSION_ID))
        assertEquals(
            second,
            AndroidReviewDraftStore(context).read(OTHER_CARD_ID, OTHER_SESSION_ID),
        )
    }

    private fun scenario() = ReviewScenario(
        card = DueCardWire(
            id = CARD_ID,
            topic = "Raft leader election",
            category = "Distributed Systems",
            masterySummary = "",
            lastScore = null,
            recallScore = null,
            scoreKind = "unrated",
            scoringContractVersion = null,
            dueLabel = "due today",
            resumable = true,
            missedCount = 0,
        ),
        session = SessionStartWire(
            sessionId = SESSION_ID,
            question = "What stops an incomplete log from winning?",
            isFollowUp = false,
            draftText = "server partial",
            resumed = true,
        ),
        speechTrace = SpeechTrace(1, listOf("partial"), "partial"),
    )

    private fun checkpoint(cardId: String, sessionId: String, draft: String) = ReviewCheckpoint(
        cardId = cardId,
        sessionId = sessionId,
        draftText = draft,
        inputMode = InputMode.Voice,
    )

    private companion object {
        const val CARD_ID = "00000000-0000-0000-0000-0000000000c2"
        const val SESSION_ID = "00000000-0000-0000-0000-00000000a11f"
        const val OTHER_CARD_ID = "00000000-0000-0000-0000-0000000000c3"
        const val OTHER_SESSION_ID = "00000000-0000-0000-0000-00000000a120"
    }
}
