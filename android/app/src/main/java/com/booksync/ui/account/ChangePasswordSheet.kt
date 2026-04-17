package com.booksync.ui.account

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CloudOff
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material.icons.filled.VisibilityOff
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.ui.theme.Tandem

/**
 * Bottom sheet for changing the current user's password.
 *
 * Offline policy: when [isOnline] is false the submit button is disabled and a
 * banner explains why — the server round-trip is mandatory for this action.
 * Client-side validation: new password must be ≥ 8 chars and match confirm.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChangePasswordSheet(
    isOnline: Boolean,
    onDismiss: () -> Unit,
    onSuccess: () -> Unit,
    viewModel: AccountViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)

    var currentPw    by remember { mutableStateOf("") }
    var newPw        by remember { mutableStateOf("") }
    var confirmPw    by remember { mutableStateOf("") }
    var showCurrent  by remember { mutableStateOf(false) }
    var showNew      by remember { mutableStateOf(false) }
    var showConfirm  by remember { mutableStateOf(false) }
    var clientError  by remember { mutableStateOf<String?>(null) }

    val state by viewModel.changePasswordState.collectAsState()

    // Bubble success back to the parent and close.
    LaunchedEffect(state) {
        if (state is ChangePasswordState.Success) {
            viewModel.resetChangePasswordState()
            onSuccess()
        }
    }

    val serverError = (state as? ChangePasswordState.Error)?.message
    val submitting = state is ChangePasswordState.Submitting

    val newPasswordValid = newPw.length >= 8
    val passwordsMatch   = newPw == confirmPw && newPw.isNotEmpty()
    val formValid        = currentPw.isNotEmpty() && newPasswordValid && passwordsMatch

    fun submit() {
        clientError = when {
            currentPw.isEmpty()   -> "Enter your current password."
            !newPasswordValid     -> "New password must be at least 8 characters."
            !passwordsMatch       -> "New passwords do not match."
            else                  -> null
        }
        if (clientError == null) viewModel.changePassword(currentPw, newPw)
    }

    ModalBottomSheet(
        onDismissRequest = {
            viewModel.resetChangePasswordState()
            onDismiss()
        },
        sheetState = sheetState,
        containerColor = colors.bgSecondary,
        shape = Tandem.shapes.modal,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 20.dp)
                .padding(bottom = 32.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Header
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(
                    Icons.Default.Lock,
                    contentDescription = null,
                    tint = colors.accent,
                    modifier = Modifier.size(22.dp),
                )
                Spacer(Modifier.size(10.dp))
                Text(
                    "Change password",
                    color = colors.textPrimary,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.SemiBold,
                )
            }
            Text(
                "New password must be at least 8 characters.",
                color = colors.textSecondary,
                fontSize = 13.sp,
            )

            if (!isOnline) {
                OfflineChip()
            }

            PasswordField(
                value = currentPw,
                onChange = { currentPw = it; clientError = null; viewModel.resetChangePasswordState() },
                label = "Current password",
                revealed = showCurrent,
                onToggleReveal = { showCurrent = !showCurrent },
                imeAction = ImeAction.Next,
                enabled = !submitting,
            )
            PasswordField(
                value = newPw,
                onChange = { newPw = it; clientError = null; viewModel.resetChangePasswordState() },
                label = "New password",
                revealed = showNew,
                onToggleReveal = { showNew = !showNew },
                imeAction = ImeAction.Next,
                enabled = !submitting,
            )
            PasswordField(
                value = confirmPw,
                onChange = { confirmPw = it; clientError = null; viewModel.resetChangePasswordState() },
                label = "Confirm new password",
                revealed = showConfirm,
                onToggleReveal = { showConfirm = !showConfirm },
                imeAction = ImeAction.Done,
                onImeAction = { submit() },
                enabled = !submitting,
                isError = confirmPw.isNotEmpty() && !passwordsMatch,
            )

            val errorToShow = clientError ?: serverError
            if (errorToShow != null) {
                Text(errorToShow, color = colors.statusError, fontSize = 13.sp)
            }

            Spacer(Modifier.height(4.dp))

            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.End,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                TextButton(
                    onClick = {
                        viewModel.resetChangePasswordState()
                        onDismiss()
                    },
                ) {
                    Text("Cancel", color = colors.textSecondary)
                }
                Spacer(Modifier.size(8.dp))
                Button(
                    onClick = { submit() },
                    enabled = isOnline && formValid && !submitting,
                    shape = Tandem.shapes.button,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = colors.accent,
                        contentColor = Color.White,
                        disabledContainerColor = colors.bgInput,
                        disabledContentColor = colors.textMuted,
                    ),
                ) {
                    if (submitting) {
                        CircularProgressIndicator(
                            color = Color.White,
                            strokeWidth = 2.dp,
                            modifier = Modifier.size(16.dp),
                        )
                        Spacer(Modifier.size(8.dp))
                        Text("Saving…")
                    } else {
                        Text("Update password")
                    }
                }
            }
        }
    }
}

@Composable
private fun PasswordField(
    value: String,
    onChange: (String) -> Unit,
    label: String,
    revealed: Boolean,
    onToggleReveal: () -> Unit,
    imeAction: ImeAction,
    enabled: Boolean,
    onImeAction: (() -> Unit)? = null,
    isError: Boolean = false,
) {
    val colors = Tandem.colors
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label, color = colors.textSecondary, fontSize = 13.sp) },
        singleLine = true,
        enabled = enabled,
        isError = isError,
        visualTransformation = if (revealed) VisualTransformation.None else PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(
            keyboardType = KeyboardType.Password,
            imeAction = imeAction,
        ),
        keyboardActions = KeyboardActions(onDone = { onImeAction?.invoke() }),
        trailingIcon = {
            IconButton(onClick = onToggleReveal) {
                Icon(
                    imageVector = if (revealed) Icons.Default.VisibilityOff else Icons.Default.Visibility,
                    contentDescription = if (revealed) "Hide password" else "Show password",
                    tint = colors.textSecondary,
                )
            }
        },
        modifier = Modifier.fillMaxWidth(),
        shape = Tandem.shapes.input,
        colors = OutlinedTextFieldDefaults.colors(
            focusedBorderColor = colors.accent,
            unfocusedBorderColor = colors.border,
            focusedContainerColor = colors.bgInput,
            unfocusedContainerColor = colors.bgInput,
            focusedTextColor = colors.textPrimary,
            unfocusedTextColor = colors.textPrimary,
            cursorColor = colors.accent,
            errorBorderColor = colors.statusError,
            errorTextColor = colors.textPrimary,
        ),
    )
}

@Composable
private fun OfflineChip() {
    val colors = Tandem.colors
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Icon(
            Icons.Default.CloudOff,
            contentDescription = null,
            tint = colors.statusWarning,
            modifier = Modifier.size(16.dp),
        )
        Spacer(Modifier.size(8.dp))
        Text(
            "Connect to the server to change your password.",
            color = colors.statusWarning,
            fontSize = 12.sp,
        )
    }
}
