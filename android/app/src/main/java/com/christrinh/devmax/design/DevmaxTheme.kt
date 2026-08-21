package com.christrinh.devmax.design

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.christrinh.devmax.R

object DevmaxColors {
    val Background = Color(0xFF0D0F11)
    val Surface = Color(0xFF14171A)
    val BubbleBorder = Color(0xFF1F2427)
    val Border = Color(0xFF21262A)
    val BorderStrong = Color(0xFF23282C)
    val TextStrong = Color(0xFFF2F4F5)
    val TextSecondary = Color(0xFFCFD4D8)
    val TextMuted = Color(0xFFA8AFB5)
    val Meta = Color(0xFF8B9299)
    val MetaDim = Color(0xFF6B7378)
    val Accent = Color(0xFF57B6C2)
    val AccentInk = Color(0xFF06232A)
    val AccentWash = Color(0x1A57B6C2)
    val InputFill = Color(0xFF0F1214)
}

val Newsreader = FontFamily(
    Font(R.font.newsreader, weight = FontWeight.Normal),
)

val PlexSans = FontFamily(
    Font(R.font.ibm_plex_sans, weight = FontWeight.Normal),
    Font(R.font.ibm_plex_sans, weight = FontWeight.Medium),
    Font(R.font.ibm_plex_sans, weight = FontWeight.SemiBold),
)

val PlexMono = FontFamily(
    Font(R.font.ibm_plex_mono_regular, weight = FontWeight.Normal),
    Font(R.font.ibm_plex_mono_medium, weight = FontWeight.Medium),
)

private val DevmaxTypography = Typography(
    displaySmall = TextStyle(
        fontFamily = Newsreader,
        fontWeight = FontWeight.Normal,
        fontSize = 25.sp,
        lineHeight = 33.sp,
        letterSpacing = (-0.25).sp,
    ),
    bodyLarge = TextStyle(
        fontFamily = PlexSans,
        fontWeight = FontWeight.Normal,
        fontSize = 15.sp,
        lineHeight = 22.5.sp,
    ),
    bodyMedium = TextStyle(
        fontFamily = PlexSans,
        fontWeight = FontWeight.Normal,
        fontSize = 13.sp,
        lineHeight = 18.sp,
    ),
    labelLarge = TextStyle(
        fontFamily = PlexSans,
        fontWeight = FontWeight.Medium,
        fontSize = 15.5.sp,
    ),
    labelSmall = TextStyle(
        fontFamily = PlexMono,
        fontWeight = FontWeight.Normal,
        fontSize = 10.sp,
        letterSpacing = 1.sp,
    ),
)

private val DevmaxColorScheme = darkColorScheme(
    primary = DevmaxColors.Accent,
    onPrimary = DevmaxColors.AccentInk,
    background = DevmaxColors.Background,
    onBackground = DevmaxColors.TextStrong,
    surface = DevmaxColors.Surface,
    onSurface = DevmaxColors.TextSecondary,
    outline = DevmaxColors.Border,
)

@Composable
fun DevmaxTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DevmaxColorScheme,
        typography = DevmaxTypography,
        shapes = Shapes(
            small = RoundedCornerShape(8.dp),
            medium = RoundedCornerShape(12.dp),
            large = RoundedCornerShape(20.dp),
        ),
        content = content,
    )
}
