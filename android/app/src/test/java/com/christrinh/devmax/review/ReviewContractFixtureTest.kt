package com.christrinh.devmax.review

import kotlinx.serialization.decodeFromString
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.decodeFromJsonElement
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReviewContractFixtureTest {
    @Test
    fun `wire fixtures decode and resume the same session`() {
        val cards = ReviewJson.decodeFromString<List<DueCardWire>>(fixture("cards_due.raft.json"))
        val resumedCards = ReviewJson.decodeFromString<List<DueCardWire>>(
            fixture("cards_due.raft.resumed.json"),
        )
        val newSession = ReviewJson.decodeFromString<SessionStartWire>(
            fixture("session_start.raft.new.json"),
        )
        val resumedSession = ReviewJson.decodeFromString<SessionStartWire>(
            fixture("session_start.raft.resumed.json"),
        )
        val speech = ReviewJson.decodeFromString<SpeechTrace>(fixture("speech_trace.raft.json"))

        assertEquals(1, cards.size)
        assertEquals("Raft leader election", cards.single().topic)
        assertFalse(cards.single().resumable)
        assertTrue(resumedCards.single().resumable)
        assertEquals(cards.single().id, resumedCards.single().id)
        assertEquals(newSession.sessionId, resumedSession.sessionId)
        assertEquals(newSession.question, resumedSession.question)
        assertFalse(newSession.resumed)
        assertTrue(resumedSession.resumed)
        assertTrue(newSession.draftText.isEmpty())
        assertEquals(cards.single().resumable, newSession.resumed)
        assertEquals(resumedCards.single().resumable, resumedSession.resumed)
        assertEquals(speech.finalText, resumedSession.draftText)
        assertEquals(speech.finalText, speech.partials.last())
    }

    @Test
    fun `wire decoding tolerates additive backend fields`() {
        val original = ReviewJson.parseToJsonElement(fixture("session_start.raft.new.json"))
            as JsonObject
        val withAdditiveField = JsonObject(
            original + ("future_optional_field" to ReviewJson.parseToJsonElement("7")),
        )

        val decoded = ReviewJson.decodeFromJsonElement<SessionStartWire>(withAdditiveField)

        assertFalse(decoded.resumed)
        assertEquals(7, withAdditiveField.getValue("future_optional_field").jsonPrimitive.int)
    }

    private fun fixture(name: String): String {
        val url = checkNotNull(javaClass.classLoader?.getResource(name)) {
            "$name is missing from the shared contract fixture resources"
        }
        return url.readText()
    }
}
