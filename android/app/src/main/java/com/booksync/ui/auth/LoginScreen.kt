package com.booksync.ui.auth

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.autofill.ContentType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.contentType
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.booksync.R
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.shouldExpandAdvanced
import com.booksync.data.remote.LoginRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.util.restartApp
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject
import android.content.Context

import com.booksync.data.remote.TokenManager

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val api: BookSyncApi,
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
) : ViewModel() {
    private val _isLoading = MutableStateFlow(false)
    val isLoading = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()

    val currentServerUrl = serverUrlManager.serverUrlFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), serverUrlManager.currentUrl)

    fun login(username: String, password: String, onSuccess: () -> Unit) {
        viewModelScope.launch {
            _isLoading.value = true
            _error.value = null
            try {
                val tokens = api.login(LoginRequest(username, password))
                tokenManager.saveTokens(tokens.access_token, tokens.refresh_token)
                onSuccess()
            } catch (e: Exception) {
                _error.value = e.message ?: "Login failed"
            } finally {
                _isLoading.value = false
            }
        }
    }

    fun saveServerUrlAndRestart(context: Context, url: String) {
        viewModelScope.launch {
            serverUrlManager.setServerUrl(url)
            restartApp(context)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun LoginScreen(
    onLoginSuccess: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    val isLoading by viewModel.isLoading.collectAsState()
    val error by viewModel.error.collectAsState()
    val currentServerUrl by viewModel.currentServerUrl.collectAsState()
    // With no server configured yet, the URL field is the only useful control on
    // this screen — start expanded rather than hidden behind the toggle.
    var showAdvanced by remember { mutableStateOf(shouldExpandAdvanced(currentServerUrl)) }
    var serverUrlEdit by remember(currentServerUrl) { mutableStateOf(currentServerUrl) }
    val context = LocalContext.current

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // Logo
            Icon(
                painter = painterResource(id = R.drawable.ic_launcher_foreground),
                contentDescription = "Tandem",
                tint = Color.Unspecified,
                modifier = Modifier
                    .size(72.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .padding(bottom = 8.dp)
            )
            Text(
                text = "Tandem",
                style = MaterialTheme.typography.headlineLarge,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                text = "Sync your reading & listening",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(bottom = 32.dp),
            )

            // Error
            error?.let { msg ->
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.errorContainer,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 16.dp),
                ) {
                    Text(
                        text = msg,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(12.dp),
                        textAlign = TextAlign.Center,
                    )
                }
            }

            // Username
            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text("Username") },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 12.dp)
                    .semantics { contentType = ContentType.Username },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            )

            // Password
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text("Password") },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 24.dp)
                    .semantics { contentType = ContentType.Password },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(
                    onDone = {
                        if (username.isNotBlank() && password.isNotBlank()) {
                            viewModel.login(username, password, onLoginSuccess)
                        }
                    }
                ),
            )

            // Login Button
            Button(
                onClick = { viewModel.login(username, password, onLoginSuccess) },
                enabled = username.isNotBlank() && password.isNotBlank() &&
                    currentServerUrl.isNotBlank() && !isLoading,
                modifier = Modifier
                    .fillMaxWidth()
                    .height(52.dp),
            ) {
                if (isLoading) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text("Sign In", fontWeight = FontWeight.SemiBold)
                }
            }

            // Advanced toggle — server URL configuration
            TextButton(
                onClick = { showAdvanced = !showAdvanced },
                modifier = Modifier.padding(top = 16.dp),
            ) {
                Text("Advanced")
                Icon(
                    imageVector = if (showAdvanced) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                )
            }

            AnimatedVisibility(visible = showAdvanced) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    OutlinedTextField(
                        value = serverUrlEdit,
                        onValueChange = { serverUrlEdit = it },
                        label = { Text("Server URL") },
                        singleLine = true,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(bottom = 12.dp),
                        keyboardOptions = KeyboardOptions(
                            keyboardType = KeyboardType.Uri,
                            imeAction = ImeAction.Done,
                        ),
                    )
                    Button(
                        onClick = { viewModel.saveServerUrlAndRestart(context, serverUrlEdit.trim()) },
                        enabled = serverUrlEdit.isNotBlank() && serverUrlEdit.trim() != currentServerUrl,
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                    ) {
                        Text("Save & Restart")
                    }
                    Text(
                        text = if (currentServerUrl.isBlank()) {
                            "No server configured yet — enter your Tandem server URL to sign in."
                        } else {
                            "Saving restarts the app to apply the new server."
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            }
        }
    }
}
