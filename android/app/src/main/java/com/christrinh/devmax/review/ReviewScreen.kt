package com.christrinh.devmax.review

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.window.layout.FoldingFeature
import com.christrinh.devmax.design.DevmaxColors
import com.christrinh.devmax.design.PlexMono
import kotlinx.coroutines.flow.StateFlow

private val ContentMaxWidth = 390.dp
private val ConversationPadding = 24.dp

@Composable
fun ReviewRoute(
    viewModel: ReviewViewModel,
    foldingFeatureFlow: StateFlow<FoldingFeature?>,
) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    val foldingFeature by foldingFeatureFlow.collectAsStateWithLifecycle()
    ReviewScreen(
        state = state,
        foldingFeature = foldingFeature,
        onRetry = viewModel::retry,
        onResume = viewModel::resumeAnswer,
        onStartOver = viewModel::startOver,
        onSelectInput = viewModel::selectInput,
        onDraftChanged = viewModel::updateDraft,
        onToggleListening = viewModel::toggleListening,
    )
}

@Composable
fun ReviewScreen(
    state: ReviewUiState,
    foldingFeature: FoldingFeature? = null,
    onRetry: () -> Unit,
    onResume: () -> Unit,
    onStartOver: () -> Unit,
    onSelectInput: (InputMode) -> Unit,
    onDraftChanged: (String) -> Unit,
    onToggleListening: () -> Unit,
) {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .background(DevmaxColors.Background)
            .testTag("review_root"),
    ) {
        // Window changes only alter this container. Card/session/draft identity
        // remains in the reducer, so folding cannot accidentally start a review.
        HingeAwareReviewPane(foldingFeature) {
            Box(
                modifier = Modifier
                    .windowInsetsPadding(WindowInsets.safeDrawing)
                    .fillMaxHeight()
                    .widthIn(max = ContentMaxWidth)
                    .fillMaxWidth(),
            ) {
                when (state) {
                    ReviewUiState.LoadingQuestion -> LoadingQuestion()
                    is ReviewUiState.QuestionFailed -> QuestionFailed(state.note, onRetry)
                    is ReviewUiState.Answering -> AnsweringReview(
                        state = state,
                        onResume = onResume,
                        onStartOver = onStartOver,
                        onSelectInput = onSelectInput,
                        onDraftChanged = onDraftChanged,
                        onToggleListening = onToggleListening,
                    )
                }
            }
        }
    }
}

@Composable
private fun HingeAwareReviewPane(
    foldingFeature: FoldingFeature?,
    content: @Composable () -> Unit,
) {
    BoxWithConstraints(modifier = Modifier.fillMaxSize()) {
        val feature = foldingFeature?.takeIf { it.isSeparating }
        if (feature == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { content() }
            return@BoxWithConstraints
        }

        val density = LocalDensity.current
        if (feature.orientation == FoldingFeature.Orientation.VERTICAL) {
            val leftWidth = with(density) { feature.bounds.left.toDp() }.coerceIn(0.dp, maxWidth)
            val hingeWidth = with(density) { feature.bounds.width().toDp() }
                .coerceIn(0.dp, maxWidth - leftWidth)
            val rightWidth = (maxWidth - leftWidth - hingeWidth).coerceAtLeast(0.dp)
            val useLeft = leftWidth >= rightWidth

            Row(Modifier.fillMaxSize()) {
                Box(
                    Modifier.width(leftWidth).fillMaxHeight(),
                    contentAlignment = Alignment.Center,
                ) {
                    if (useLeft) content()
                }
                Spacer(Modifier.width(hingeWidth))
                Box(
                    Modifier.width(rightWidth).fillMaxHeight(),
                    contentAlignment = Alignment.Center,
                ) {
                    if (!useLeft) content()
                }
            }
        } else {
            val topHeight = with(density) { feature.bounds.top.toDp() }.coerceIn(0.dp, maxHeight)
            val hingeHeight = with(density) { feature.bounds.height().toDp() }
                .coerceIn(0.dp, maxHeight - topHeight)
            val bottomHeight = (maxHeight - topHeight - hingeHeight).coerceAtLeast(0.dp)
            val useTop = topHeight >= bottomHeight

            Column(Modifier.fillMaxSize()) {
                Box(
                    Modifier.height(topHeight).fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    if (useTop) content()
                }
                Spacer(Modifier.height(hingeHeight))
                Box(
                    Modifier.height(bottomHeight).fillMaxWidth(),
                    contentAlignment = Alignment.Center,
                ) {
                    if (!useTop) content()
                }
            }
        }
    }
}

@Composable
private fun LoadingQuestion() {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(horizontal = ConversationPadding, vertical = 44.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "M0 · LOADING FIXTURE",
            style = MaterialTheme.typography.labelSmall,
            color = DevmaxColors.MetaDim,
        )
        Spacer(Modifier.height(20.dp))
        listOf(18.dp, 18.dp, 18.dp).forEachIndexed { index, height ->
            Box(
                Modifier
                    .fillMaxWidth(if (index == 0) 1f else if (index == 1) 0.82f else 0.58f)
                    .height(height)
                    .clip(RoundedCornerShape(3.dp))
                    .background(DevmaxColors.BubbleBorder),
            )
        }
    }
}

@Composable
private fun QuestionFailed(note: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(ConversationPadding),
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "Couldn't load the question.",
            style = MaterialTheme.typography.displaySmall,
            color = DevmaxColors.TextStrong,
        )
        Spacer(Modifier.height(12.dp))
        Text(note, style = MaterialTheme.typography.bodyMedium, color = DevmaxColors.TextMuted)
        Spacer(Modifier.height(20.dp))
        Button(onClick = onRetry) { Text("Try again") }
    }
}

@Composable
private fun AnsweringReview(
    state: ReviewUiState.Answering,
    onResume: () -> Unit,
    onStartOver: () -> Unit,
    onSelectInput: (InputMode) -> Unit,
    onDraftChanged: (String) -> Unit,
    onToggleListening: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxSize()) {
        ReviewChrome(state)

        Column(
            modifier = Modifier
                .weight(1f)
                .verticalScroll(rememberScrollState())
                .padding(horizontal = ConversationPadding)
                .padding(top = 18.dp, bottom = 24.dp),
            verticalArrangement = Arrangement.spacedBy(24.dp),
        ) {
            if (state.resumeAvailable) {
                ResumeNotice(onResume = onResume, onStartOver = onStartOver)
            }

            Text(
                text = state.session.question,
                modifier = Modifier.testTag("question"),
                style = MaterialTheme.typography.displaySmall,
                color = DevmaxColors.TextStrong,
            )

            if (state.draft.isNotEmpty()) {
                LiveTranscript(
                    text = state.draft,
                    showCaret = state.captureId != null,
                )
            }

            Spacer(Modifier.height(1.dp))
        }

        when (state.inputMode) {
            InputMode.Voice -> VoiceInput(
                state = state,
                onToggleListening = onToggleListening,
                onTypeInstead = { onSelectInput(InputMode.Text) },
            )
            InputMode.Text -> TextInput(
                state = state,
                onDraftChanged = onDraftChanged,
                onVoice = { onSelectInput(InputMode.Voice) },
            )
        }
    }
}

@Composable
private fun ReviewChrome(state: ReviewUiState.Answering) {
    Column {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .height(44.dp)
                .padding(horizontal = ConversationPadding),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "M0 · FIXTURE",
                style = MaterialTheme.typography.labelSmall,
                color = DevmaxColors.MetaDim,
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = "UNPROMPTED",
                style = MaterialTheme.typography.labelSmall,
                color = DevmaxColors.MetaDim,
            )
        }
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = ConversationPadding),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = "×",
                fontSize = 24.sp,
                color = DevmaxColors.Meta,
                modifier = Modifier.semantics { contentDescription = "Close review" },
            )
            Spacer(Modifier.weight(1f))
            Text(
                text = state.card.topic.uppercase(),
                fontFamily = PlexMono,
                fontSize = 10.5.sp,
                letterSpacing = 1.05.sp,
                color = DevmaxColors.MetaDim,
            )
        }
    }
}

@Composable
private fun ResumeNotice(onResume: () -> Unit, onStartOver: () -> Unit) {
    val shape = MaterialTheme.shapes.medium
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .testTag("resume_banner")
            .clip(shape)
            .background(DevmaxColors.Surface)
            .border(1.dp, DevmaxColors.Border, shape)
            .padding(14.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(
            text = "You were mid-answer here. Your partial answer was saved.",
            style = MaterialTheme.typography.bodyMedium,
            color = DevmaxColors.TextSecondary,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Button(
                onClick = onResume,
                colors = ButtonDefaults.buttonColors(
                    containerColor = DevmaxColors.Accent,
                    contentColor = DevmaxColors.AccentInk,
                ),
            ) {
                Text("Resume answer", style = MaterialTheme.typography.bodyMedium)
            }
            OutlinedButton(
                onClick = onStartOver,
                colors = ButtonDefaults.outlinedButtonColors(contentColor = DevmaxColors.TextMuted),
                border = BorderStroke(1.dp, DevmaxColors.Border),
            ) {
                Text("Start over", style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
}

@Composable
private fun LiveTranscript(text: String, showCaret: Boolean) {
    val transcript: AnnotatedString = buildAnnotatedString {
        append(text)
        if (showCaret) {
            pushStyle(SpanStyle(color = DevmaxColors.Accent))
            append("▍")
            pop()
        }
    }
    Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
        Text(
            text = transcript,
            modifier = Modifier
                .fillMaxWidth(0.84f)
                .testTag("draft_text"),
            style = MaterialTheme.typography.bodyLarge,
            color = DevmaxColors.TextMuted,
        )
    }
}

@Composable
private fun VoiceInput(
    state: ReviewUiState.Answering,
    onToggleListening: () -> Unit,
    onTypeInstead: () -> Unit,
) {
    val listening = state.captureId != null
    val enabled = !state.resumeAvailable
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(DevmaxColors.Background)
            .padding(horizontal = ConversationPadding)
            .padding(top = 12.dp, bottom = 30.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(
            modifier = Modifier
                .size(84.dp)
                .semantics {
                    contentDescription = if (listening) "Stop fixture speech" else "Start fixture speech"
                }
                .clip(CircleShape)
                .clickable(enabled = enabled, role = Role.Button, onClick = onToggleListening)
                .testTag("voice_control"),
            contentAlignment = Alignment.Center,
        ) {
            Box(
                modifier = Modifier
                    .size(76.dp)
                    .clip(CircleShape)
                    .background(if (listening) DevmaxColors.AccentWash else DevmaxColors.Background)
                    .border(
                        1.dp,
                        if (listening) DevmaxColors.Accent else DevmaxColors.BorderStrong,
                        CircleShape,
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Box(
                    modifier = Modifier
                        .size(if (listening) 20.dp else 18.dp)
                        .clip(RoundedCornerShape(if (listening) 4.dp else 10.dp))
                        .background(DevmaxColors.Accent),
                )
            }
        }
        Text(
            text = when {
                listening -> "LISTENING — TAP TO STOP"
                state.draft.isNotEmpty() -> "TAP TO KEEP GOING"
                else -> "TAP TO ANSWER"
            },
            style = MaterialTheme.typography.labelSmall,
            color = DevmaxColors.Meta,
        )
        Text(
            text = "Type instead",
            modifier = Modifier
                .height(44.dp)
                .clickable(enabled = enabled, role = Role.Button, onClick = onTypeInstead)
                .padding(horizontal = 12.dp, vertical = 12.dp),
            style = MaterialTheme.typography.bodyMedium,
            color = DevmaxColors.Meta,
        )
    }
}

@Composable
private fun TextInput(
    state: ReviewUiState.Answering,
    onDraftChanged: (String) -> Unit,
    onVoice: () -> Unit,
) {
    val enabled = !state.resumeAvailable
    val shape = MaterialTheme.shapes.medium
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(DevmaxColors.Background)
            .padding(horizontal = ConversationPadding)
            .padding(top = 12.dp, bottom = 30.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        BasicTextField(
            value = state.draft,
            onValueChange = onDraftChanged,
            enabled = enabled,
            modifier = Modifier
                .fillMaxWidth()
                .height(104.dp)
                .testTag("text_input")
                .clip(shape)
                .background(DevmaxColors.InputFill)
                .border(1.dp, DevmaxColors.BorderStrong, shape)
                .padding(horizontal = 12.dp, vertical = 10.dp),
            textStyle = MaterialTheme.typography.bodyLarge.copy(color = DevmaxColors.TextSecondary),
            cursorBrush = SolidColor(DevmaxColors.Accent),
        )
        Text(
            text = "Voice",
            modifier = Modifier
                .height(44.dp)
                .clickable(enabled = enabled, role = Role.Button, onClick = onVoice)
                .padding(horizontal = 12.dp, vertical = 12.dp),
            style = MaterialTheme.typography.bodyMedium,
            color = DevmaxColors.Meta,
        )
    }
}
