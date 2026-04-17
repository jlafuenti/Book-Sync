package com.booksync.ui.theme

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

// ============================================================
// Tandem — Theme definitions (5 dark colour schemes)
// Colors mirror web/src/themes.js so Android and Web stay in lockstep.
// ============================================================

enum class TandemTheme(val slug: String, val label: String) {
    BLUEPRINT("blueprint", "Blueprint"),
    FOREST_NIGHT("forest-night", "Forest Night"),
    EMBER("ember", "Ember"),
    AURORA("aurora", "Aurora"),
    SLATE("slate", "Slate");

    companion object {
        fun fromSlug(slug: String): TandemTheme =
            values().firstOrNull { it.slug == slug } ?: BLUEPRINT
    }
}

// ------------------------------------------------------------
// Extended semantic colors (beyond Material3's ColorScheme)
// These mirror the web app's CSS custom properties.
// ------------------------------------------------------------

@Immutable
data class TandemColors(
    // Background surfaces
    val bgPrimary: Color,      // app window background
    val bgSecondary: Color,    // raised surfaces (top bar, nav)
    val bgCard: Color,         // card background
    val bgCardHover: Color,    // card pressed / hover
    val bgInput: Color,        // input fields
    // Text
    val textPrimary: Color,
    val textSecondary: Color,
    val textMuted: Color,
    // Borders
    val border: Color,
    val borderLight: Color,
    // Accent / primary
    val accent: Color,
    val accentHover: Color,
    val accentLight: Color,    // tinted fill behind active pills (~15% alpha)
    val accentGlow: Color,     // colored drop-shadow (~40% alpha)
    val accentSecondary: Color,
    // Status palette (shared across all themes)
    val statusSuccess: Color = Color(0xFF10B981),
    val statusWarning: Color = Color(0xFFF59E0B),
    val statusError: Color   = Color(0xFFEF4444),
    val statusInfo: Color    = Color(0xFF3B82F6),
)

// ------------------------------------------------------------
// Per-theme semantic color tables
// ------------------------------------------------------------

private val BlueprintTandemColors = TandemColors(
    bgPrimary       = Color(0xFF0F0F1A),
    bgSecondary     = Color(0xFF1A1A2E),
    bgCard          = Color(0xFF16213E),
    bgCardHover     = Color(0xFF1A2744),
    bgInput         = Color(0xFF0D1B2A),
    textPrimary     = Color(0xFFE8E8F0),
    textSecondary   = Color(0xFFA0A0C0),
    textMuted       = Color(0xFF6A6A8A),
    border          = Color(0xFF2A2A4A),
    borderLight     = Color(0xFF3A3A5A),
    accent          = Color(0xFF7C3AED),
    accentHover     = Color(0xFF6D28D9),
    accentLight     = Color(0x267C3AED), // ~15% alpha
    accentGlow      = Color(0x667C3AED), // ~40% alpha
    accentSecondary = Color(0xFFA78BFA),
)

private val ForestNightTandemColors = TandemColors(
    bgPrimary       = Color(0xFF080F0A),
    bgSecondary     = Color(0xFF0D1F13),
    bgCard          = Color(0xFF0F2018),
    bgCardHover     = Color(0xFF142A1E),
    bgInput         = Color(0xFF0A1A10),
    textPrimary     = Color(0xFFE8F0EB),
    textSecondary   = Color(0xFF90B09A),
    textMuted       = Color(0xFF5A7A62),
    border          = Color(0xFF1A3A24),
    borderLight     = Color(0xFF2A4A34),
    accent          = Color(0xFF22C55E),
    accentHover     = Color(0xFF16A34A),
    accentLight     = Color(0x2622C55E),
    accentGlow      = Color(0x6622C55E),
    accentSecondary = Color(0xFF14B8A6),
)

private val EmberTandemColors = TandemColors(
    bgPrimary       = Color(0xFF0F0905),
    bgSecondary     = Color(0xFF1C1008),
    bgCard          = Color(0xFF1A1208),
    bgCardHover     = Color(0xFF221808),
    bgInput         = Color(0xFF120A04),
    textPrimary     = Color(0xFFF0E8E0),
    textSecondary   = Color(0xFFC0A080),
    textMuted       = Color(0xFF8A6A40),
    border          = Color(0xFF3A2010),
    borderLight     = Color(0xFF4A3020),
    accent          = Color(0xFFF59E0B),
    accentHover     = Color(0xFFD97706),
    accentLight     = Color(0x26F59E0B),
    accentGlow      = Color(0x66F59E0B),
    accentSecondary = Color(0xFFEA580C),
)

private val AuroraTandemColors = TandemColors(
    bgPrimary       = Color(0xFF07060F),
    bgSecondary     = Color(0xFF10102A),
    bgCard          = Color(0xFF0E1028),
    bgCardHover     = Color(0xFF141434),
    bgInput         = Color(0xFF080820),
    textPrimary     = Color(0xFFE8E8F8),
    textSecondary   = Color(0xFF9090C0),
    textMuted       = Color(0xFF6060A0),
    border          = Color(0xFF20204A),
    borderLight     = Color(0xFF30305A),
    accent          = Color(0xFF06B6D4),
    accentHover     = Color(0xFF0891B2),
    accentLight     = Color(0x2606B6D4),
    accentGlow      = Color(0x6606B6D4),
    accentSecondary = Color(0xFFD946EF),
)

private val SlateTandemColors = TandemColors(
    bgPrimary       = Color(0xFF0C0E12),
    bgSecondary     = Color(0xFF171C26),
    bgCard          = Color(0xFF1A1F2E),
    bgCardHover     = Color(0xFF1E2438),
    bgInput         = Color(0xFF0F1218),
    textPrimary     = Color(0xFFE8EAF0),
    textSecondary   = Color(0xFF9098B0),
    textMuted       = Color(0xFF606880),
    border          = Color(0xFF282E3A),
    borderLight     = Color(0xFF343C4A),
    accent          = Color(0xFF38BDF8),
    accentHover     = Color(0xFF0EA5E9),
    accentLight     = Color(0x1F38BDF8), // slate uses a lighter tint (~12%)
    accentGlow      = Color(0x4D38BDF8), // ~30%
    accentSecondary = Color(0xFFA78BFA),
)

fun tandemColorsFor(theme: TandemTheme): TandemColors = when (theme) {
    TandemTheme.BLUEPRINT    -> BlueprintTandemColors
    TandemTheme.FOREST_NIGHT -> ForestNightTandemColors
    TandemTheme.EMBER        -> EmberTandemColors
    TandemTheme.AURORA       -> AuroraTandemColors
    TandemTheme.SLATE        -> SlateTandemColors
}

// ------------------------------------------------------------
// Material3 ColorSchemes — re-derive from TandemColors so
// Material components automatically pick up theme changes.
// ------------------------------------------------------------

private fun colorSchemeFrom(c: TandemColors): ColorScheme = darkColorScheme(
    primary             = c.accent,
    onPrimary           = Color.White,
    primaryContainer    = c.accentHover,
    onPrimaryContainer  = Color.White,
    secondary           = c.accentSecondary,
    onSecondary         = Color.White,
    background          = c.bgPrimary,
    onBackground        = c.textPrimary,
    surface             = c.bgSecondary,
    onSurface           = c.textPrimary,
    surfaceVariant      = c.bgCard,
    onSurfaceVariant    = c.textSecondary,
    outline             = c.border,
    outlineVariant      = c.borderLight,
    error               = c.statusError,
    onError             = Color.White,
)

fun colorSchemeFor(theme: TandemTheme): ColorScheme =
    colorSchemeFrom(tandemColorsFor(theme))

// ------------------------------------------------------------
// Shape tokens — mirror web radii
// ------------------------------------------------------------

@Immutable
data class TandemShapesTokens(
    val button: RoundedCornerShape     = RoundedCornerShape(8.dp),
    val input: RoundedCornerShape      = RoundedCornerShape(8.dp),
    val card: RoundedCornerShape       = RoundedCornerShape(12.dp),
    val modal: RoundedCornerShape      = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 0.dp, bottomEnd = 0.dp),
    val pill: RoundedCornerShape       = RoundedCornerShape(20.dp),
    val badge: RoundedCornerShape      = RoundedCornerShape(4.dp),
)

val TandemShapes = TandemShapesTokens()

// Material3 Shapes — small/medium/large map to our button/card/modal
private val MaterialShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small      = RoundedCornerShape(8.dp),
    medium     = RoundedCornerShape(12.dp),
    large      = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(20.dp),
)

// ------------------------------------------------------------
// Elevation scale
// ------------------------------------------------------------

@Immutable
data class TandemElevationTokens(
    val cardRest: Dp        = 2.dp,
    val cardPressed: Dp     = 4.dp,
    val sheet: Dp           = 8.dp,
    val bottomNav: Dp       = 8.dp,
    val topBar: Dp          = 4.dp,
)

val TandemElevation = TandemElevationTokens()

// ------------------------------------------------------------
// CompositionLocals
// ------------------------------------------------------------

val LocalTandemColors = staticCompositionLocalOf { BlueprintTandemColors }
val LocalTandemShapes = staticCompositionLocalOf { TandemShapes }
val LocalTandemElevation = staticCompositionLocalOf { TandemElevation }

object Tandem {
    val colors: TandemColors
        @Composable
        @ReadOnlyComposable
        get() = LocalTandemColors.current

    val shapes: TandemShapesTokens
        @Composable
        @ReadOnlyComposable
        get() = LocalTandemShapes.current

    val elevation: TandemElevationTokens
        @Composable
        @ReadOnlyComposable
        get() = LocalTandemElevation.current
}

// ------------------------------------------------------------
// Theme wrapper
// ------------------------------------------------------------

@Composable
fun BookSyncTheme(
    appTheme: TandemTheme = TandemTheme.BLUEPRINT,
    content: @Composable () -> Unit,
) {
    val tandemColors = tandemColorsFor(appTheme)
    CompositionLocalProvider(
        LocalTandemColors to tandemColors,
        LocalTandemShapes to TandemShapes,
        LocalTandemElevation to TandemElevation,
    ) {
        MaterialTheme(
            colorScheme = colorSchemeFrom(tandemColors),
            shapes = MaterialShapes,
            content = content,
        )
    }
}
