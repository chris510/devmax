package com.christrinh.devmax.review

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ReviewViewModelTest {
    @get:Rule
    val mainDispatcherRule = MainDispatcherRule()

    @Test
    fun `stale load failure cannot replace a newer successful session`() = runTest {
        val repository = ControlledRepository()
        val viewModel = ReviewViewModel(
            repository = repository,
            draftStore = InMemoryDraftStore(),
            ioDispatcher = StandardTestDispatcher(testScheduler),
        )
        runCurrent()
        val first = repository.requests.single()

        viewModel.retry()
        runCurrent()
        val second = repository.requests.last()

        second.complete(scenario("newer-session"))
        runCurrent()
        assertEquals(
            "newer-session",
            (viewModel.state.value as ReviewUiState.Answering).session.sessionId,
        )

        first.completeExceptionally(IllegalStateException("late older failure"))
        runCurrent()

        val final = viewModel.state.value
        assertTrue(final is ReviewUiState.Answering)
        assertEquals("newer-session", (final as ReviewUiState.Answering).session.sessionId)
    }

    @Test
    fun `only checkpoint changes write to disk`() = runTest {
        val repository = ControlledRepository()
        val draftStore = InMemoryDraftStore()
        val viewModel = ReviewViewModel(
            repository = repository,
            draftStore = draftStore,
            ioDispatcher = StandardTestDispatcher(testScheduler),
        )
        runCurrent()
        repository.requests.single().complete(scenario("session"))
        runCurrent()
        assertEquals(1, draftStore.writes)

        viewModel.toggleListening()
        assertEquals(1, draftStore.writes)
        viewModel.toggleListening()
        assertEquals(1, draftStore.writes)

        viewModel.updateDraft("exact partial")
        assertEquals(2, draftStore.writes)
        viewModel.updateDraft("exact partial")
        assertEquals(2, draftStore.writes)

        viewModel.selectInput(InputMode.Text)
        assertEquals(3, draftStore.writes)
    }

    private class ControlledRepository : ReviewRepository {
        val requests = mutableListOf<CompletableDeferred<ReviewScenario>>()

        override suspend fun loadScenario(): ReviewScenario {
            return CompletableDeferred<ReviewScenario>().also(requests::add).await()
        }
    }

    private class InMemoryDraftStore : ReviewDraftStore {
        private var checkpoint: ReviewCheckpoint? = null
        var writes = 0
            private set

        override fun read(cardId: String, sessionId: String): ReviewCheckpoint? = checkpoint
            ?.takeIf { it.cardId == cardId && it.sessionId == sessionId }

        override fun write(checkpoint: ReviewCheckpoint) {
            this.checkpoint = checkpoint
            writes += 1
        }

        override fun clear(cardId: String, sessionId: String) {
            if (checkpoint?.cardId == cardId && checkpoint?.sessionId == sessionId) {
                checkpoint = null
            }
        }
    }

    private fun scenario(sessionId: String) = ReviewScenario(
        card = DueCardWire(
            id = "card",
            topic = "Raft leader election",
            category = "Distributed Systems",
            masterySummary = "",
            lastScore = null,
            recallScore = null,
            scoreKind = "unrated",
            scoringContractVersion = null,
            dueLabel = "due today",
            resumable = false,
            missedCount = 0,
        ),
        session = SessionStartWire(
            sessionId = sessionId,
            question = "What stops an incomplete log from winning?",
            isFollowUp = false,
            draftText = "",
            resumed = false,
        ),
        speechTrace = SpeechTrace(1, listOf("partial"), "partial"),
    )
}
