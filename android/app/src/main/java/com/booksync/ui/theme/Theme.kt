package com.booksync.ui.theme

import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// ============================================================
// Tandem — Theme definitions (5 dark colour schemes)
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

// ── Blueprint (navy / blue / violet) ────────────────────────
private val BlueprintColors = darkColorScheme(
    primary          = Color(0xFF7C3AED),
    onPrimary        = Color.White,
    primaryContainer = Color(0xFF6D28D9),
    secondary        = Color(0xFFA78BFA),
    onSecondary      = Color.White,
    background       = Color(0xFF0F0F1A),
    surface          = Color(0xFF1A1A2E),
    surfaceVariant   = Color(0xFF16213E),
    onBackground     = Color(0xFFE8E8F0),
    onSurface        = Color(0xFFE8E8F0),
    onSurfaceVariant = Color(0xFFA0A0C0),
    outline          = Color(0xFF2A2A4A),
    error            = Color(0xFFEF4444),
)

// ── Forest Night (dark green / emerald / teal) ───────────────
private val ForestNightColors = darkColorScheme(
    primary          = Color(0xFF22C55E),
    onPrimary        = Color.White,
    primaryContainer = Color(0xFF16A34A),
    secondary        = Color(0xFF14B8A6),
    onSecondary      = Color.White,
    background       = Color(0xFF080F0A),
    surface          = Color(0xFF0D1F13),
    surfaceVariant   = Color(0xFF0F2018),
    onBackground     = Color(0xFFE8F0EB),
    onSurface        = Color(0xFFE8F0EB),
    onSurfaceVariant = Color(0xFF90B09A),
    outline          = Color(0xFF1A3A24),
    error            = Color(0xFFEF4444),
)

// ── Ember (dark brown / amber / orange) ──────────────────────
private val EmberColors = darkColorScheme(
    primary          = Color(0xFFF59E0B),
    onPrimary        = Color(0xFF1C1008),
    primaryContainer = Color(0xFFD97706),
    secondary        = Color(0xFFEA580C),
    onSecondary      = Color.White,
    background       = Color(0xFF0F0905),
    surface          = Color(0xFF1C1008),
    surfaceVariant   = Color(0xFF1A1208),
    onBackground     = Color(0xFFF0E8E0),
    onSurface        = Color(0xFFF0E8E0),
    onSurfaceVariant = Color(0xFFC0A080),
    outline          = Color(0xFF3A2010),
    error            = Color(0xFFEF4444),
)

// ── Aurora (deep black / cyan / fuchsia) ─────────────────────
private val AuroraColors = darkColorScheme(
    primary          = Color(0xFF06B6D4),
    onPrimary        = Color.White,
    primaryContainer = Color(0xFF0891B2),
    secondary        = Color(0xFFD946EF),
    onSecondary      = Color.White,
    background       = Color(0xFF07060F),
    surface          = Color(0xFF10102A),
    surfaceVariant   = Color(0xFF0E1028),
    onBackground     = Color(0xFFE8E8F8),
    onSurface        = Color(0xFFE8E8F8),
    onSurfaceVariant = Color(0xFF9090C0),
    outline          = Color(0xFF20204A),
    error            = Color(0xFFEF4444),
)

// ── Slate (charcoal / ice blue / lavender) ───────────────────
private val SlateColors = darkColorScheme(
    primary          = Color(0xFF38BDF8),
    onPrimary        = Color(0xFF0C0E12),
    primaryContainer = Color(0xFF0EA5E9),
    secondary        = Color(0xFFA78BFA),
    onSecondary      = Color.White,
    background       = Color(0xFF0C0E12),
    surface          = Color(0xFF171C26),
    surfaceVariant   = Color(0xFF1A1F2E),
    onBackground     = Color(0xFFE8EAF0),
    onSurface        = Color(0xFFE8EAF0),
    onSurfaceVariant = Color(0xFF9098B0),
    outline          = Color(0xFF282E3A),
    error            = Color(0xFFEF4444),
)

fun colorSchemeFor(theme: TandemTheme): ColorScheme = when (theme) {
    TandemTheme.BLUEPRINT    -> BlueprintColors
    TandemTheme.FOREST_NIGHT -> ForestNightColors
    TandemTheme.EMBER        -> EmberColors
    TandemTheme.AURORA       -> AuroraColors
    TandemTheme.SLATE        -> SlateColors
}

@Composable
fun BookSyncTheme(
    appTheme: TandemTheme = TandemTheme.BLUEPRINT,
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = colorSchemeFor(appTheme),
        content = content,
    )
}
