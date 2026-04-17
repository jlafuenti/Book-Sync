package com.booksync.ui.components

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.ui.theme.Tandem

/**
 * Filter / sort pill. Matches web/src/components/FilterPill.jsx.
 *
 * Inactive: transparent bg, 1dp theme-border outline, secondary-text label.
 * Active:   accent-light tint bg, 1dp accent-color outline, primary-text label.
 * Optional trailing chevron for the dropdown variant.
 * Optional trailing count badge (used for the "NEW" pill showing unacknowledged count).
 */
@Composable
fun FilterPill(
    label: String,
    selected: Boolean,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    showChevron: Boolean = false,
    count: Int? = null,
) {
    val colors = Tandem.colors
    val shapes = Tandem.shapes

    Surface(
        modifier = modifier
            .clip(shapes.pill)
            .clickable(onClick = onClick),
        shape = shapes.pill,
        color = if (selected) colors.accentLight else androidx.compose.ui.graphics.Color.Transparent,
        border = BorderStroke(1.dp, if (selected) colors.accent else colors.border),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 14.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Text(
                text = label,
                fontSize = 12.sp,
                color = if (selected) colors.textPrimary else colors.textSecondary,
            )
            if (count != null && count > 0) {
                Surface(
                    shape = CircleShape,
                    color = colors.accent,
                    modifier = Modifier,
                ) {
                    Text(
                        text = count.toString(),
                        color = androidx.compose.ui.graphics.Color.White,
                        fontSize = 10.sp,
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp),
                    )
                }
            }
            if (showChevron) {
                Icon(
                    imageVector = Icons.Default.ArrowDropDown,
                    contentDescription = null,
                    tint = if (selected) colors.textPrimary else colors.textSecondary,
                )
            }
        }
    }
}

/**
 * Dropdown-style sort pill. Bigger touch target, always shows a chevron.
 * Caller is responsible for opening a menu/sheet on click.
 */
@Composable
fun SortPill(
    label: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
) {
    FilterPill(
        label = label,
        selected = false,
        onClick = onClick,
        modifier = modifier,
        showChevron = true,
    )
}
