package com.christrinh.devmax.review

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

class FixtureSpeechController(
    private val scope: CoroutineScope,
) {
    private var job: Job? = null

    fun start(
        captureId: Long,
        trace: SpeechTrace,
        onPartial: (Long, String) -> Unit,
        onFinal: (Long, String) -> Unit,
    ) {
        job?.cancel()
        job = scope.launch {
            trace.partials.forEach { partial ->
                delay(trace.intervalMs)
                onPartial(captureId, partial)
            }
            delay(trace.intervalMs)
            onFinal(captureId, trace.finalText)
        }
    }

    fun stop() {
        job?.cancel()
        job = null
    }
}
