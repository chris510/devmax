package com.christrinh.devmax.review

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

// Store-delivered clients must tolerate additive response fields. Exact fixture
// shape is enforced by the shared backend contract test instead of making an
// older installed APK reject a backward-compatible server response.
internal val ReviewJson = Json {
    encodeDefaults = true
    ignoreUnknownKeys = true
}

@Serializable
data class DueCardWire(
    val id: String,
    val topic: String,
    val category: String,
    @SerialName("mastery_summary") val masterySummary: String,
    @SerialName("last_score") val lastScore: Int?,
    @SerialName("recall_score") val recallScore: Int?,
    @SerialName("score_kind") val scoreKind: String,
    @SerialName("scoring_contract_version") val scoringContractVersion: Int?,
    @SerialName("due_label") val dueLabel: String,
    val resumable: Boolean,
    @SerialName("missed_count") val missedCount: Int,
)

@Serializable
data class SessionStartWire(
    @SerialName("session_id") val sessionId: String,
    val question: String,
    @SerialName("is_follow_up") val isFollowUp: Boolean,
    @SerialName("draft_text") val draftText: String,
    val resumed: Boolean,
)

@Serializable
data class SpeechTrace(
    @SerialName("interval_ms") val intervalMs: Long,
    val partials: List<String>,
    @SerialName("final_text") val finalText: String,
)

@Serializable
enum class InputMode {
    @SerialName("voice") Voice,
    @SerialName("text") Text,
}

@Serializable
data class ReviewCheckpoint(
    val cardId: String,
    val sessionId: String,
    val draftText: String,
    val inputMode: InputMode = InputMode.Voice,
)

data class ReviewScenario(
    val card: DueCardWire,
    val session: SessionStartWire,
    val speechTrace: SpeechTrace,
)
