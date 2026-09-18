package com.booksync.ui.tour

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInWindow
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * The walkthrough's overlay (issue #597 §4): a draw-only scrim with a
 * rounded-rect hole over the spotlighted control, four blocker strips that
 * consume every other tap, and a card explaining the step. Rendered next to
 * the outer `NavHost` in `BookSyncNavigation`, and — once Track C wires the
 * reader's own overlay host — inside `ReaderActivity` too.
 *
 * Not unit-tested: this is the one Compose-only piece of the tour (see
 * `TourGeometry.kt` for the placement math that *is* tested, and the Kover
 * exclude added for this file in `app/build.gradle.kts`).
 */
@Composable
fun TourOverlay(
    state: TourState.Running,
    onNext: () -> Unit,
    onBack: () -> Unit,
    onSkip: () -> Unit,
    onQuit: () -> Unit,
) {
    val colors = Tandem.colors
    val density = LocalDensity.current
    var screenSize by remember { mutableStateOf(Size.Zero) }
    // Follow the control as it moves: the Library scrolls the picked pair into
    // view *after* the step is entered, and any list can scroll under the card.
    // The controller's snapshot is only the fallback while the registry has no
    // live rect for this anchor.
    val liveRects by LocalTourRegistry.current.rects.collectAsState()
    // Anchors are registered in window coordinates; this overlay may be hosted
    // anywhere in the window (the nav host, the reader's ComposeView, inside a
    // bottom sheet's content), so express the hole relative to its own origin.
    var origin by remember { mutableStateOf(Offset.Zero) }
    val hole = (state.step.anchor?.let { liveRects[it] } ?: state.anchor)
        ?.takeIf { it.width > 0f && it.height > 0f }
        ?.translate(-origin.x, -origin.y)

    // The reader selection step must not eat the long-press-and-drag gesture
    // it is teaching, so it blocks nothing and the "hole" is the full page.
    val blockNothing = state.step.id == READER_SELECTION_STEP_ID
    val blockEverything = state.step.advance is Advance.Next && !blockNothing

    Box(
        modifier = Modifier
            .fillMaxSize()
            .onGloballyPositioned {
                screenSize = Size(it.size.width.toFloat(), it.size.height.toFloat())
                origin = it.positionInWindow()
            },
    ) {
        if (!blockNothing) {
            // Forces an offscreen compositing layer so BlendMode.Clear actually
            // punches a hole instead of just painting black over the hole too.
            Canvas(modifier = Modifier.fillMaxSize().graphicsLayer(alpha = 0.99f)) {
                drawRect(color = Color.Black.copy(alpha = 0.6f))
                if (hole != null) {
                    drawRoundRect(
                        color = Color.Transparent,
                        topLeft = Offset(hole.left, hole.top),
                        size = androidx.compose.ui.geometry.Size(hole.width, hole.height),
                        cornerRadius = androidx.compose.ui.geometry.CornerRadius(12.dp.toPx(), 12.dp.toPx()),
                        blendMode = BlendMode.Clear,
                    )
                }
            }
        }

        if (blockEverything || hole == null) {
            Box(
                Modifier
                    .fillMaxSize()
                    .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null) {},
            )
        } else if (!blockNothing) {
            TourBlockers(hole = hole, screenSize = screenSize, density = density)
        }

        // Measured on first layout; the estimate only serves the very first frame.
        var cardHeightPx by remember { mutableStateOf(with(density) { 220.dp.toPx() }) }
        val placement = cardPlacement(hole, screenSize, cardHeightPx)
        val cardAlignment = when (placement) {
            Placement.Below, Placement.Above -> Alignment.TopCenter
            Placement.Center -> Alignment.Center
        }
        val cardOffsetY = cardOffsetY(placement, hole, screenSize, cardHeightPx)
            ?.let { with(density) { it.toDp() } } ?: 0.dp

        Box(Modifier.fillMaxSize(), contentAlignment = cardAlignment) {
            TourCard(
                state = state,
                modifier = Modifier
                    .padding(horizontal = 20.dp)
                    .offset(y = if (placement == Placement.Center) 0.dp else cardOffsetY)
                    .onSizeChanged { cardHeightPx = it.height.toFloat() },
                onNext = onNext,
                onBack = onBack,
                onSkip = onSkip,
                onQuit = onQuit,
            )
        }
    }
}

/** Four strips around [hole] that consume taps so only the spotlighted control is reachable. */
@Composable
private fun TourBlockers(hole: androidx.compose.ui.geometry.Rect, screenSize: Size, density: androidx.compose.ui.unit.Density) {
    val noIndication = remember { MutableInteractionSource() }
    fun Float.toDpPx(): Dp = with(density) { this@toDpPx.toDp() }

    // Above the hole
    Box(
        Modifier
            .fillMaxWidth()
            .size(width = screenSize.width.toDpPx(), height = hole.top.coerceAtLeast(0f).toDpPx())
            .clickable(interactionSource = noIndication, indication = null) {},
    )
    // Below the hole
    Box(
        Modifier
            .offset(y = hole.bottom.toDpPx())
            .size(
                width = screenSize.width.toDpPx(),
                height = (screenSize.height - hole.bottom).coerceAtLeast(0f).toDpPx(),
            )
            .clickable(interactionSource = noIndication, indication = null) {},
    )
    // Left of the hole
    Box(
        Modifier
            .offset(y = hole.top.toDpPx())
            .size(width = hole.left.coerceAtLeast(0f).toDpPx(), height = hole.height.toDpPx())
            .clickable(interactionSource = noIndication, indication = null) {},
    )
    // Right of the hole
    Box(
        Modifier
            .offset(x = hole.right.toDpPx(), y = hole.top.toDpPx())
            .size(
                width = (screenSize.width - hole.right).coerceAtLeast(0f).toDpPx(),
                height = hole.height.toDpPx(),
            )
            .clickable(interactionSource = noIndication, indication = null) {},
    )
}

@Composable
private fun TourCard(
    state: TourState.Running,
    modifier: Modifier = Modifier,
    onNext: () -> Unit,
    onBack: () -> Unit,
    onSkip: () -> Unit,
    onQuit: () -> Unit,
) {
    val colors = Tandem.colors
    val step = state.step
    val body = if (state.degraded) (step.emptyBody ?: step.body) else step.body
    // The Done card doesn't know at script-writing time whether this run will clean up its
    // pair — that depends on whether it was untouched when the tour opened it (issue #597
    // tester feedback) — so the extra sentence is appended here from live state rather than
    // duplicated into a second "done" step.
    val displayBody = if (step.id == "done" && state.willCleanUp) {
        "$body The book we used has been put back the way it was."
    } else {
        body
    }

    Column(
        modifier = modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(16.dp))
            .background(colors.bgSecondary)
            .padding(20.dp),
    ) {
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = step.title,
                color = colors.textPrimary,
                fontSize = 16.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = onQuit) {
                Icon(Icons.Default.Close, contentDescription = "Quit the walkthrough", tint = colors.textMuted)
            }
        }
        Spacer(Modifier.padding(top = 6.dp))
        Text(text = displayBody, color = colors.textSecondary, fontSize = 14.sp)
        Spacer(Modifier.padding(top = 14.dp))
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = "${state.index + 1} of ${state.total}",
                color = colors.textMuted,
                fontSize = 12.sp,
                modifier = Modifier.weight(1f),
            )
            if (state.index > 0) {
                if (state.canGoBack) {
                    TextButton(onClick = onBack) { Text("Back", color = colors.textSecondary) }
                }
                Spacer(Modifier.width(4.dp))
            }
            when (val advance = step.advance) {
                Advance.Next -> TextButton(onClick = onNext) { Text("Next", color = colors.accent) }
                is Advance.TapAnchor -> Text(
                    "Tap the highlighted control",
                    color = colors.textMuted,
                    fontSize = 12.sp,
                )
                is Advance.WaitFor -> if (advance.skippable) {
                    TextButton(onClick = onSkip) { Text("Skip", color = colors.accent) }
                } else {
                    Text("Waiting…", color = colors.textMuted, fontSize = 12.sp)
                }
            }
        }
    }
}
