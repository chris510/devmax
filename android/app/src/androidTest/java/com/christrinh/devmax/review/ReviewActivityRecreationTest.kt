package com.christrinh.devmax.review

import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import com.christrinh.devmax.MainActivity
import org.junit.Rule
import org.junit.Test

class ReviewActivityRecreationTest {
    @get:Rule
    val rule = createAndroidComposeRule<MainActivity>()

    @Test
    fun recognizedPrefixSurvivesActivityRecreationWithMicStopped() {
        rule.waitUntil(timeoutMillis = 5_000) {
            rule.onAllNodesWithText(QUESTION).fetchSemanticsNodes().isNotEmpty()
        }
        val startOver = rule.onAllNodesWithText("Start over")
        if (startOver.fetchSemanticsNodes().isNotEmpty()) {
            startOver[0].performClick()
        }

        rule.onNodeWithContentDescription("Start fixture speech").performClick()
        rule.waitUntil(timeoutMillis = 5_000) {
            rule.onAllNodesWithText(PARTIAL, substring = true).fetchSemanticsNodes().isNotEmpty()
        }
        rule.onNodeWithContentDescription("Stop fixture speech").fetchSemanticsNode()

        rule.activityRule.scenario.recreate()

        rule.onNodeWithText(QUESTION).fetchSemanticsNode()
        rule.onNodeWithText(PARTIAL, substring = true).fetchSemanticsNode()
        rule.onNodeWithText("TAP TO KEEP GOING").fetchSemanticsNode()
        rule.onNodeWithContentDescription("Start fixture speech").fetchSemanticsNode()
    }

    private companion object {
        const val QUESTION =
            "A follower stops hearing heartbeats and starts an election. What stops it from becoming leader with an incomplete log?"
        const val PARTIAL =
            "Okay so each server has a term number"
    }
}
