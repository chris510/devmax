package com.christrinh.devmax.review

import android.content.Context
import android.util.AtomicFile
import androidx.core.util.readText
import androidx.core.util.writeText
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import java.io.File
import java.security.MessageDigest

interface ReviewDraftStore {
    fun read(cardId: String, sessionId: String): ReviewCheckpoint?
    fun write(checkpoint: ReviewCheckpoint)
    fun clear(cardId: String, sessionId: String)
}

class AndroidReviewDraftStore(context: Context) : ReviewDraftStore {
    private val directory = File(context.filesDir, "review-drafts").apply { mkdirs() }

    override fun read(cardId: String, sessionId: String): ReviewCheckpoint? = runCatching {
        ReviewJson.decodeFromString<ReviewCheckpoint>(fileFor(cardId, sessionId).readText())
    }.getOrNull()?.takeIf { it.cardId == cardId && it.sessionId == sessionId }

    override fun write(checkpoint: ReviewCheckpoint) {
        fileFor(checkpoint.cardId, checkpoint.sessionId).writeText(
            ReviewJson.encodeToString(checkpoint),
        )
    }

    override fun clear(cardId: String, sessionId: String) {
        fileFor(cardId, sessionId).delete()
    }

    private fun fileFor(cardId: String, sessionId: String): AtomicFile {
        val digest = MessageDigest.getInstance("SHA-256")
            .digest("$cardId:$sessionId".encodeToByteArray())
            .joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
        return AtomicFile(File(directory, "$digest.json"))
    }
}
