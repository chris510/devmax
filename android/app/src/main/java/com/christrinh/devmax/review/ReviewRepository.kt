package com.christrinh.devmax.review

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.decodeFromString

interface ReviewRepository {
    suspend fun loadScenario(): ReviewScenario
}

class FixtureReviewRepository(
    private val context: Context,
) : ReviewRepository {
    override suspend fun loadScenario(): ReviewScenario = withContext(Dispatchers.IO) {
        val cards = ReviewJson.decodeFromString<List<DueCardWire>>(read("cards_due.raft.json"))
        val session = ReviewJson.decodeFromString<SessionStartWire>(
            read("session_start.raft.new.json"),
        )
        val speechTrace = ReviewJson.decodeFromString<SpeechTrace>(read("speech_trace.raft.json"))
        ReviewScenario(cards.single(), session, speechTrace)
    }

    private fun read(name: String): String = context.assets.open(name).bufferedReader().use {
        it.readText()
    }
}
