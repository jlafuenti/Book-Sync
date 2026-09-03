package com.booksync.ui.account

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.hilt.navigation.compose.hiltViewModel
import com.booksync.R
import com.booksync.ui.theme.Tandem

/**
 * Typed-confirmation dialog for deleting the signed-in account (issue #146).
 *
 * Two gates, not one. The password is the server's check — it is sent in the
 * request body and re-verified there, so a session left open on a borrowed
 * device is not by itself enough. Typing the word is the *client's* check, and
 * it exists because this dialog sits a few millimetres from "Log out" in a list
 * of settings rows: a mis-tap that reaches a single confirm button is a
 * plausible way to destroy an account, and a mis-tap that reaches a text field
 * is not.
 *
 * There is deliberately no success path here. On 204 the ViewModel clears the
 * tokens, `BookSyncNavigation` observes the clear and routes to Login, and this
 * dialog goes with the screen — so a "your account was deleted" state would
 * only ever flash. A refusal is what stays on screen, in the server's words.
 */
@Composable
fun DeleteAccountDialog(
    isOnline: Boolean,
    onDismiss: () -> Unit,
    viewModel: AccountViewModel = hiltViewModel(),
) {
    val colors = Tandem.colors
    val state by viewModel.deleteAccountState.collectAsState()

    var password by remember { mutableStateOf("") }
    var typed by remember { mutableStateOf("") }

    val confirmWord = stringResource(R.string.delete_account_type_to_confirm)
    val submitting = state is DeleteAccountState.Submitting
    val serverError = (state as? DeleteAccountState.Error)?.message

    // The state is process-lived (it sits on the ViewModel), so a dialog opened
    // a second time would otherwise reopen showing the previous refusal.
    LaunchedEffect(Unit) { viewModel.resetDeleteAccountState() }

    val canSubmit = isOnline &&
        !submitting &&
        password.isNotEmpty() &&
        typed.trim() == confirmWord

    AlertDialog(
        onDismissRequest = { if (!submitting) onDismiss() },
        title = {
            Text(
                stringResource(R.string.delete_account_dialog_title),
                color = colors.textPrimary,
                fontWeight = FontWeight.SemiBold,
            )
        },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text(
                    stringResource(R.string.delete_account_warning),
                    color = colors.textSecondary,
                    fontSize = 13.sp,
                )
                OutlinedTextField(
                    value = password,
                    onValueChange = { password = it; viewModel.resetDeleteAccountState() },
                    label = {
                        Text(
                            stringResource(R.string.delete_account_password_label),
                            color = colors.textSecondary,
                            fontSize = 13.sp,
                        )
                    },
                    singleLine = true,
                    enabled = !submitting,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Password,
                        imeAction = ImeAction.Next,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                    shape = Tandem.shapes.input,
                    colors = deleteFieldColors(),
                )
                OutlinedTextField(
                    value = typed,
                    onValueChange = { typed = it },
                    label = {
                        Text(
                            stringResource(R.string.delete_account_confirm_label, confirmWord),
                            color = colors.textSecondary,
                            fontSize = 13.sp,
                        )
                    },
                    singleLine = true,
                    enabled = !submitting,
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    modifier = Modifier.fillMaxWidth(),
                    shape = Tandem.shapes.input,
                    colors = deleteFieldColors(),
                )
                if (!isOnline) {
                    Text(
                        stringResource(R.string.delete_account_offline),
                        color = colors.statusWarning,
                        fontSize = 12.sp,
                    )
                }
                if (serverError != null) {
                    Text(serverError, color = colors.statusError, fontSize = 13.sp)
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = { viewModel.deleteAccount(password) },
                enabled = canSubmit,
            ) {
                if (submitting) {
                    CircularProgressIndicator(
                        color = colors.statusError,
                        strokeWidth = 2.dp,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.size(8.dp))
                    Text(
                        stringResource(R.string.delete_account_deleting),
                        color = colors.statusError,
                    )
                } else {
                    Text(
                        stringResource(R.string.delete_account_confirm_button),
                        color = if (canSubmit) colors.statusError else colors.textMuted,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss, enabled = !submitting) {
                Text(
                    stringResource(R.string.delete_account_cancel),
                    color = colors.textSecondary,
                )
            }
        },
        containerColor = colors.bgSecondary,
    )
}

@Composable
private fun deleteFieldColors() = OutlinedTextFieldDefaults.colors(
    focusedBorderColor = Tandem.colors.statusError,
    unfocusedBorderColor = Tandem.colors.border,
    focusedContainerColor = Tandem.colors.bgInput,
    unfocusedContainerColor = Tandem.colors.bgInput,
    focusedTextColor = Tandem.colors.textPrimary,
    unfocusedTextColor = Tandem.colors.textPrimary,
    cursorColor = Tandem.colors.statusError,
)
