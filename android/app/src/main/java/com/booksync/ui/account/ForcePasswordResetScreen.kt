package com.booksync.ui.account

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.activity.compose.BackHandler
import com.booksync.ui.theme.Tandem

/**
 * The change-password form as a destination the user cannot leave (issue #209).
 *
 * `must_reset_password` is set by admin-create, admin password reset and the
 * fresh-install bootstrap, and the server now refuses everything except
 * `/auth/me`, `/auth/change-password` and `/auth/logout` while it is set. So this
 * is not advisory: every other screen in the app would be a wall of failures.
 *
 * Deliberately *not* the existing `ChangePasswordSheet`. A `ModalBottomSheet` can
 * be swiped away, which would leave the user in an app where nothing works and
 * no route back to the one screen that does. [BackHandler] blocks the system back
 * gesture for the same reason, and there is no Cancel button — [ChangePasswordForm]
 * renders one only when given an `onCancel`.
 *
 * Logging out is the intended escape, which is why it stays on the server's
 * allow-list.
 */
@Composable
fun ForcePasswordResetScreen(
    onPasswordChanged: () -> Unit,
    onLogout: () -> Unit,
    viewModel: AccountViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val isOnline by viewModel.isOnline.collectAsState()

    // There is nowhere to go back to; without this, back lands on whatever
    // screen was underneath and every call on it fails with 403.
    BackHandler(enabled = true) { /* intentionally inert */ }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(colors.bgPrimary)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 24.dp, vertical = 32.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Default.Lock,
                contentDescription = null,
                tint = colors.accent,
                modifier = Modifier.size(24.dp),
            )
            Spacer(Modifier.size(10.dp))
            Text(
                "Choose a new password",
                color = colors.textPrimary,
                fontSize = 20.sp,
                fontWeight = FontWeight.SemiBold,
            )
        }
        Text(
            "Your account is using a temporary password. Set your own before " +
                "continuing — the rest of the app stays locked until you do.",
            color = colors.textSecondary,
            fontSize = 14.sp,
        )
        Text(
            "New password must be at least 8 characters.",
            color = colors.textMuted,
            fontSize = 13.sp,
        )

        Spacer(Modifier.size(8.dp))

        ChangePasswordForm(
            isOnline = isOnline,
            onSuccess = onPasswordChanged,
            onCancel = null,
            viewModel = viewModel,
        )

        Spacer(Modifier.size(16.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.Center,
        ) {
            TextButton(onClick = onLogout) {
                Text(
                    "Sign out instead",
                    color = colors.textSecondary,
                    fontSize = 13.sp,
                )
            }
        }
    }
}
