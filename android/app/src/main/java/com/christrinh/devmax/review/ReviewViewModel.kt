package com.christrinh.devmax.review

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

class ReviewViewModel(
    private val repository: ReviewRepository,
    private val draftStore: ReviewDraftStore,
    private val ioDispatcher: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val _state = MutableStateFlow<ReviewUiState>(ReviewUiState.LoadingQuestion)
    val state: StateFlow<ReviewUiState> = _state.asStateFlow()

    private val speech = FixtureSpeechController(viewModelScope)
    private var speechTrace: SpeechTrace? = null
    private var nextCaptureId = 0L
    private var loadGeneration = 0L

    init {
        load()
    }

    fun retry() = load()

    fun resumeAnswer() = dispatch(ReviewEvent.ResumeAnswer)

    fun startOver() {
        speech.stop()
        dispatch(ReviewEvent.StartOver)
    }

    fun selectInput(mode: InputMode) {
        speech.stop()
        dispatch(ReviewEvent.SelectInput(mode))
    }

    fun updateDraft(text: String) = dispatch(ReviewEvent.DraftChanged(text))

    fun toggleListening() {
        val answering = _state.value as? ReviewUiState.Answering ?: return
        if (answering.captureId != null) {
            stopListening()
            return
        }
        if (answering.resumeAvailable) return

        val trace = speechTrace ?: return
        val captureId = ++nextCaptureId
        dispatch(ReviewEvent.StartListening(captureId))
        speech.start(
            captureId = captureId,
            trace = trace,
            onPartial = { id, text -> dispatch(ReviewEvent.SpeechPartial(id, text)) },
            onFinal = { id, text -> dispatch(ReviewEvent.SpeechFinal(id, text)) },
        )
    }

    fun onBackgrounded() {
        speech.stop()
        dispatch(ReviewEvent.AppBackgrounded)
    }

    private fun stopListening() {
        val answering = _state.value as? ReviewUiState.Answering ?: return
        val captureId = answering.captureId ?: return
        speech.stop()
        dispatch(ReviewEvent.SpeechEnded(captureId))
    }

    private fun load() {
        val generation = ++loadGeneration
        _state.value = ReviewUiState.LoadingQuestion
        viewModelScope.launch {
            val result = runCatching {
                val loaded = repository.loadScenario()
                val checkpoint = withContext(ioDispatcher) {
                    draftStore.read(loaded.card.id, loaded.session.sessionId)
                }
                loaded to checkpoint
            }

            // Cancellation is only an optimization. Every retry owns a unique
            // generation, so a slower earlier success or failure is harmless.
            if (generation != loadGeneration) return@launch

            result.fold(
                onSuccess = { (loaded, checkpoint) ->
                    speechTrace = loaded.speechTrace
                    dispatch(ReviewEvent.SessionLoaded(loaded, checkpoint))
                },
                onFailure = {
                    dispatch(ReviewEvent.LoadFailed("The fixture could not be decoded."))
                },
            )
        }
    }

    private fun dispatch(event: ReviewEvent) {
        val previous = _state.value
        val updated = ReviewReducer.reduce(previous, event)
        _state.value = updated
        val previousAnswering = previous as? ReviewUiState.Answering
        if (updated is ReviewUiState.Answering && updated.hasCheckpointChangeFrom(previousAnswering)) {
            // M0 intentionally favors loss prevention over write batching. This
            // tiny AtomicFile checkpoint is committed for every recognized
            // partial and keystroke; server backup arrives in M2.
            draftStore.write(
                ReviewCheckpoint(
                    cardId = updated.card.id,
                    sessionId = updated.session.sessionId,
                    draftText = updated.draft,
                    inputMode = updated.inputMode,
                ),
            )
        }
    }

    private fun ReviewUiState.Answering.hasCheckpointChangeFrom(
        previous: ReviewUiState.Answering?,
    ): Boolean = previous == null ||
        card.id != previous.card.id ||
        session.sessionId != previous.session.sessionId ||
        draft != previous.draft ||
        inputMode != previous.inputMode

    override fun onCleared() {
        speech.stop()
        super.onCleared()
    }

    companion object {
        fun factory(context: Context): ViewModelProvider.Factory {
            val appContext = context.applicationContext
            return viewModelFactory {
                initializer {
                    ReviewViewModel(
                        repository = FixtureReviewRepository(appContext),
                        draftStore = AndroidReviewDraftStore(appContext),
                    )
                }
            }
        }
    }
}
