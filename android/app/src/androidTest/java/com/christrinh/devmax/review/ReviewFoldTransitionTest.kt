package com.christrinh.devmax.review

import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.espresso.device.EspressoDevice
import androidx.test.espresso.device.DeviceInteraction.Companion.setBookMode
import androidx.test.espresso.device.DeviceInteraction.Companion.setFlatMode
import com.christrinh.devmax.MainActivity
import org.junit.Rule
import org.junit.Test

class ReviewFoldTransitionTest {
    @get:Rule
    val rule = createAndroidComposeRule<MainActivity>()

    @Test
    fun recognizedPrefixSurvivesBookFoldAndUnfoldWithMicStopped() {
        EspressoDevice.onDevice().setFlatMode()
        waitForQuestion()
        startClean()

        rule.onNodeWithContentDescription("Start fixture speech").performClick()
        rule.waitUntil(timeoutMillis = 5_000) {
            rule.onAllNodesWithText("Okay", substring = true).fetchSemanticsNodes().isNotEmpty()
        }

        EspressoDevice.onDevice().setBookMode()
        waitForQuestion()
        rule.waitUntil(timeoutMillis = 10_000) {
            runCatching {
                val root = rule.onNodeWithTag("review_root").fetchSemanticsNode().boundsInRoot
                val question = rule.onNodeWithText(QUESTION).fetchSemanticsNode().boundsInRoot
                question.right <= root.center.x + 1f
            }.getOrDefault(false)
        }
        rule.onNodeWithText("Okay", substring = true).fetchSemanticsNode()
        rule.onNodeWithText("TAP TO KEEP GOING").fetchSemanticsNode()
        rule.onNodeWithContentDescription("Start fixture speech").fetchSemanticsNode()

        EspressoDevice.onDevice().setFlatMode()
        waitForQuestion()
        rule.onNodeWithText("Okay", substring = true).fetchSemanticsNode()
        rule.onNodeWithText("TAP TO KEEP GOING").fetchSemanticsNode()
    }

    private fun startClean() {
        val startOver = rule.onAllNodesWithText("Start over")
        if (startOver.fetchSemanticsNodes().isNotEmpty()) {
            startOver[0].performClick()
        }
    }

    private fun waitForQuestion() {
        rule.waitUntil(timeoutMillis = 10_000) {
            runCatching {
                rule.onAllNodesWithText(QUESTION).fetchSemanticsNodes().isNotEmpty()
            }.getOrDefault(false)
        }
    }

    private companion object {
        const val QUESTION =
            "A follower stops hearing heartbeats and starts an election. What stops it from becoming leader with an incomplete log?"
    }
}
