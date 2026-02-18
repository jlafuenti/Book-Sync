package com.booksync.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

// Brand colors
val Purple80 = Color(0xFFA78BFA)
val Purple60 = Color(0xFF7C3AED)
val Purple40 = Color(0xFF6D28D9)
val PurpleDark = Color(0xFF1A1A2E)

val Teal80 = Color(0xFF6EE7B7)
val Teal60 = Color(0xFF10B981)

val DarkBg = Color(0xFF0F0F1A)
val DarkCard = Color(0xFF16213E)
val DarkSurface = Color(0xFF1A1A2E)
val DarkBorder = Color(0xFF2A2A4A)
val TextPrimary = Color(0xFFE8E8F0)
val TextSecondary = Color(0xFFA0A0C0)
val TextMuted = Color(0xFF6A6A8A)

private val DarkColorScheme = darkColorScheme(
    primary = Purple60,
    onPrimary = Color.White,
    primaryContainer = Purple40,
    secondary = Teal60,
    onSecondary = Color.White,
    background = DarkBg,
    surface = DarkSurface,
    surfaceVariant = DarkCard,
    onBackground = TextPrimary,
    onSurface = TextPrimary,
    onSurfaceVariant = TextSecondary,
    outline = DarkBorder,
    error = Color(0xFFEF4444),
)

private val LightColorScheme = lightColorScheme(
    primary = Purple60,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE8DEF8),
    secondary = Teal60,
    onSecondary = Color.White,
)

@Composable
fun BookSyncTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    dynamicColor: Boolean = false,
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColorScheme
        else -> LightColorScheme
    }

    MaterialTheme(
        colorScheme = colorScheme,
        content = content,
    )
}
