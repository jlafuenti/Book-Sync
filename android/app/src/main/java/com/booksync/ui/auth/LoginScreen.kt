package com.booksync.ui.auth

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.automirrored.filled.OpenInNew
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.autofill.ContentType
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
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
import com.booksync.R
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.remote.UserScopeProvider
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.BYPASS_BASE_URL_HEADER
import com.booksync.data.remote.FirstRunGate
import com.booksync.data.remote.INVALID_SERVER_URL_MESSAGE
import com.booksync.data.remote.REGISTRATION_PENDING_MESSAGE
import com.booksync.data.remote.TANDEM_REPO_URL
import com.booksync.data.remote.normalizeServerUrl
import com.booksync.data.remote.serverDetail
import com.booksync.data.remote.shouldExpandAdvanced
import com.booksync.data.remote.LoginRequest
import com.booksync.data.remote.RegisterRequest
import com.booksync.data.remote.ServerUrlManager
import com.booksync.data.remote.ServerVersionGate
import com.booksync.data.remote.VersionBanner
import com.booksync.data.remote.VersionCompat
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import javax.inject.Inject
import android.content.Intent
import android.net.Uri

import com.booksync.data.remote.TokenManager
import com.booksync.ui.components.VersionMismatchBanner

/**
 * Result of the first-run "Check connection" probe (issue #175).
 *
 * A stranger typing an address cannot tell a typo from a server that is down, and
 * the old screen answered neither — it accepted anything and only failed later,
 * at sign-in, as "Login failed".
 */
sealed interface ConnectionState {
    /** Nothing tried yet, or the address was edited since the last attempt. */
    data object Idle : ConnectionState

    data object Checking : ConnectionState

    /**
     * `GET /api/health` answered — at [url], the normalized form of what was
     * typed. Nothing is stored yet; [url] is what
     * [LoginViewModel.acceptServer] will store if the user continues.
     */
    data class Connected(val url: String) : ConnectionState

    data class Failed(val message: String) : ConnectionState
}

@HiltViewModel
class LoginViewModel @Inject constructor(
    private val api: BookSyncApi,
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
    private val userScopeProvider: UserScopeProvider,
    private val firstRunGate: FirstRunGate,
    private val serverVersionGate: ServerVersionGate,
) : ViewModel() {
    private val _isLoading = MutableStateFlow(false)
    val isLoading = _isLoading.asStateFlow()

    private val _error = MutableStateFlow<String?>(null)
    val error = _error.asStateFlow()

    /** Non-failure notice — today only the pending-approval line after a register. */
    private val _message = MutableStateFlow<String?>(null)
    val message = _message.asStateFlow()

    /** Which form the single set of fields is currently acting as (issue #221). */
    private val _isRegistering = MutableStateFlow(false)
    val isRegistering = _isRegistering.asStateFlow()

    private val _connectionState = MutableStateFlow<ConnectionState>(ConnectionState.Idle)
    val connectionState = _connectionState.asStateFlow()

    /**
     * Version mismatch warning, or null (issue #174).
     *
     * The probe already holds the answer — `/api/health` carries the server's
     * `api_version` — and this is the one screen where someone points the app at
     * a server it has never contacted. Reading it here means a stranger who
     * types the address of a server two releases behind is told so now, instead
     * of discovering it later as a sync that 404s with no explanation.
     */
    val versionBanner: StateFlow<VersionBanner?> = serverVersionGate.verdict
        .map { VersionCompat.bannerFor(it) }
        .stateIn(
            viewModelScope,
            SharingStarted.Eagerly,
            VersionCompat.bannerFor(serverVersionGate.verdict.value),
        )

    val currentServerUrl = serverUrlManager.serverUrlFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), serverUrlManager.currentUrl)

    /**
     * Whether to show the welcome screen instead of the sign-in form (issue #175).
     *
     * Derived from [FirstRunGate], which is application-scoped, not from the
     * current URL: a successful probe stores the URL, that re-creates the login
     * destination, and anything held in the composable or in this ViewModel would
     * go with it — putting the user on a password prompt in the middle of the
     * flow. See the gate for the full account.
     */
    private val startedUnconfigured = firstRunGate.startedUnconfigured(serverUrlManager.currentUrl)

    val showFirstRun: StateFlow<Boolean> = firstRunGate.dismissed
        .map { dismissed -> startedUnconfigured && !dismissed }
        .stateIn(viewModelScope, SharingStarted.Eagerly, startedUnconfigured)

    /** The user skipped the welcome screen. Stores nothing — they configure it later. */
    fun dismissFirstRun() {
        firstRunGate.dismiss()
    }

    /**
     * The user tapped "Continue to sign in" after a successful check: store the
     * server they just verified, then leave the welcome screen.
     *
     * **This is the only place the first-run flow writes the URL**, and that is
     * the whole point. Writing it re-creates the login destination — twice now,
     * that re-creation has landed on a screen the user was still using: first
     * wiping a failure message, then wiping the "Connected" confirmation and the
     * address they had just typed, leaving an empty welcome screen. Deferring
     * the write to this tap means the re-creation lands on the sign-in form,
     * which is exactly where the tap was asking to go.
     *
     * A no-op unless a probe has actually succeeded; [ConnectionState.Connected]
     * carries the verified address, so what gets stored is what answered, not
     * whatever the text field says now.
     */
    fun acceptServer() {
        val verified = (_connectionState.value as? ConnectionState.Connected) ?: return
        viewModelScope.launch {
            if (serverUrlManager.setServerUrl(verified.url)) {
                firstRunGate.dismiss()
            } else {
                // Close to unreachable — normalizeServerUrl already passed — but
                // dismissing on a refused write would drop the user on a sign-in
                // form with no server, the state this screen exists to prevent.
                _connectionState.value = ConnectionState.Failed(INVALID_SERVER_URL_MESSAGE)
            }
        }
    }

    /** The error Card is shared with login failures; editing either field clears it. */
    fun clearError() {
        _error.value = null
    }

    /**
     * Switch between "Sign in" and "Request access".
     *
     * Clears both banners: a refusal or a confirmation from the other form is
     * about a request the user is no longer making, and leaving it up makes the
     * new form look like it has already failed or already succeeded.
     */
    fun setRegistering(registering: Boolean) {
        _isRegistering.value = registering
        _error.value = null
        _message.value = null
    }

    fun login(username: String, password: String, onSuccess: () -> Unit) {
        viewModelScope.launch {
            _isLoading.value = true
            _error.value = null
            _message.value = null
            try {
                val tokens = api.login(LoginRequest(username, password))
                tokenManager.saveTokens(tokens.access_token, tokens.refresh_token)
                // Claim any rows written before the cache was scoped, so an
                // upgrading install keeps its reading positions (issue #314).
                // After saveTokens — the scope is derived from the stored token —
                // and before onSuccess() renders anything from the cache.
                userScopeProvider.onAuthenticated()
                onSuccess()
            } catch (e: Exception) {
                _error.value = e.message ?: "Login failed"
            } finally {
                _isLoading.value = false
            }
        }
    }

    /**
     * Request an account (issue #221) — `POST /api/auth/register`.
     *
     * The account is created *pending*: the server will not issue tokens for it
     * until an admin approves, so success has to say so rather than looking like
     * a sign-in that silently did nothing.
     *
     * A refusal shows the server's own `detail`. That sentence is the only thing
     * that distinguishes "this server does not take requests at all"
     * (`ALLOW_PUBLIC_REGISTRATION=false` → 403 "Public registration is disabled")
     * from "try a different username" — `HttpException.message` is "HTTP 403
     * Forbidden", which tells the user nothing about which one they hit.
     */
    fun register(username: String, email: String, password: String) {
        viewModelScope.launch {
            _isLoading.value = true
            _error.value = null
            _message.value = null
            try {
                val submitted = api.register(RegisterRequest(username, email, password))
                // The server's own sentence when it sent one, as LoginPage.jsx
                // does, so an admin who reworded it is heard on both clients.
                // Falling back rather than requiring it: the 201 is the fact
                // that matters, and a body this build does not recognise must
                // not turn a created account into an error.
                _message.value = submitted.message?.takeIf { it.isNotBlank() }
                    ?: REGISTRATION_PENDING_MESSAGE
                // Back to the sign-in form, which is where the message belongs:
                // the next useful action is signing in, once approved.
                _isRegistering.value = false
            } catch (e: HttpException) {
                _error.value = e.serverDetail() ?: "Request failed (HTTP ${e.code()})"
            } catch (e: IOException) {
                _error.value = "Could not reach the server — check the address and your connection."
            } catch (e: Exception) {
                _error.value = e.message ?: "Request failed"
            } finally {
                _isLoading.value = false
            }
        }
    }

    /**
     * Set the server before signing in (issue #228).
     *
     * No restart: BaseUrlInterceptor reads the URL per request, so the very next
     * login attempt goes to the new host. Nothing to clear here — there is no
     * session yet.
     */
    fun saveServerUrl(url: String) {
        viewModelScope.launch {
            // Issue #149: only act if the URL was actually accepted.
            if (!serverUrlManager.setServerUrl(url)) {
                _error.value = INVALID_SERVER_URL_MESSAGE
            }
        }
    }

    /** The address was edited; the previous verdict is about a different server. */
    fun resetConnectionState() {
        _connectionState.value = ConnectionState.Idle
    }

    /**
     * Probe `{url}/api/health` and say whether there is a Tandem server there
     * (issue #175).
     *
     * **Probes, and stores nothing.** The first cut had it the other way round —
     * store, then let `BaseUrlInterceptor` route the probe at the configured
     * server — and it failed on a device twice over. Writing the URL re-creates
     * the login destination, so typing a host that does not resolve replaced the
     * welcome screen with a password prompt for a server that does not exist;
     * and once that was fixed by writing only on success, the success case
     * re-rendered this screen blank, wiping the "Connected" message and the
     * address the moment they appeared.
     *
     * So the request carries an absolute URL and opts out of the interceptor
     * ([BYPASS_BASE_URL_HEADER]), and the write moves to [acceptServer] — the
     * user's next tap, whose destination is the sign-in form anyway. Checking a
     * connection changes nothing but the message on screen.
     *
     * Nothing here may throw. Its input is a string a stranger just typed, so a
     * dead host, a wrong port, a captive portal answering HTML and a body the
     * converter cannot parse are all ordinary cases, and each has to end as a
     * sentence on screen — which is the entire reason this screen exists.
     */
    fun checkConnection(url: String) {
        viewModelScope.launch {
            _connectionState.value = ConnectionState.Checking

            // Validated before it is sent anywhere, let alone stored; issue #149
            // is what happens when it isn't.
            val normalized = normalizeServerUrl(url)
            if (normalized == null) {
                _connectionState.value = ConnectionState.Failed(INVALID_SERVER_URL_MESSAGE)
                return@launch
            }

            val failure = try {
                // The body, not just the fact that it answered: it carries the
                // server's API version, and this is the only place a fresh
                // install talks to a server before it has credentials (#174).
                serverVersionGate.record(api.getHealth("$normalized/api/health"))
                null
            } catch (e: HttpException) {
                // 404 means something answered but it is not Tandem — a router
                // admin page, say. Calling that "couldn't reach it" would send
                // the user off to check their wifi.
                val hint = if (e.code() == 404) {
                    "No Tandem server at $normalized"
                } else {
                    "$normalized answered HTTP ${e.code()}"
                }
                "$hint — check the address and that the server is running."
            } catch (e: IOException) {
                "Could not reach $normalized — check the address, that you are on the " +
                    "right network, and that the server is running."
            } catch (e: Exception) {
                "$normalized did not answer like a Tandem server."
            }

            _connectionState.value = if (failure != null) {
                ConnectionState.Failed(failure)
            } else {
                // Carry the verified address rather than re-reading the field
                // later: what gets stored is what actually answered.
                ConnectionState.Connected(normalized)
            }
        }
    }
}

@Composable
fun LoginScreen(
    onLoginSuccess: () -> Unit,
    viewModel: LoginViewModel = hiltViewModel(),
) {
    val currentServerUrl by viewModel.currentServerUrl.collectAsState()

    // Owned by the ViewModel, backed by an application-scoped gate — not by a
    // `rememberSaveable` here. A successful probe stores the URL, which
    // re-creates this destination and takes composable state (and any ViewModel
    // scoped to it) with it. See FirstRunGate.
    val showFirstRun by viewModel.showFirstRun.collectAsState()

    if (showFirstRun) {
        FirstRunScreen(viewModel = viewModel)
    } else {
        SignInScreen(
            onLoginSuccess = onLoginSuccess,
            viewModel = viewModel,
            currentServerUrl = currentServerUrl,
        )
    }
}

/**
 * What someone who installed from Play sees before anything asks for a password
 * (issue #175).
 *
 * Three things have to land, in this order: what Tandem does, that it needs a
 * server they run, and where to get one. Then the address field. The old screen
 * had only the field, behind an "Advanced" disclosure, with one line of hint —
 * which reads as a broken login form, not as a product.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FirstRunScreen(viewModel: LoginViewModel) {
    val connection by viewModel.connectionState.collectAsState()
    var serverUrlEdit by rememberSaveable { mutableStateOf("") }
    val context = LocalContext.current

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(horizontal = 28.dp, vertical = 32.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Icon(
                painter = painterResource(id = R.drawable.ic_launcher_foreground),
                contentDescription = stringResource(R.string.app_name),
                tint = Color.Unspecified,
                modifier = Modifier
                    .size(72.dp)
                    .clip(RoundedCornerShape(16.dp)),
            )
            Text(
                text = stringResource(R.string.first_run_title),
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(top = 8.dp, bottom = 16.dp),
            )

            Text(
                text = stringResource(R.string.first_run_what_it_is),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurface,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(bottom = 12.dp),
            )
            Text(
                text = stringResource(R.string.first_run_needs_server),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
                modifier = Modifier.padding(bottom = 4.dp),
            )

            TextButton(
                onClick = {
                    // No custom tabs dependency in this module; the chooser is
                    // enough, and a device with no browser must not crash the
                    // one screen a new install can reach.
                    runCatching {
                        context.startActivity(
                            Intent(Intent.ACTION_VIEW, Uri.parse(TANDEM_REPO_URL))
                                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                        )
                    }
                },
                modifier = Modifier.padding(bottom = 16.dp),
            ) {
                Text(stringResource(R.string.first_run_repo_link))
                Icon(
                    imageVector = Icons.AutoMirrored.Filled.OpenInNew,
                    contentDescription = null,
                    modifier = Modifier.padding(start = 6.dp).size(16.dp),
                )
            }

            OutlinedTextField(
                value = serverUrlEdit,
                onValueChange = {
                    serverUrlEdit = it
                    // The previous verdict was about a different address.
                    viewModel.resetConnectionState()
                },
                label = { Text(stringResource(R.string.first_run_server_label)) },
                placeholder = { Text("https://tandem.example.com") },
                singleLine = true,
                isError = connection is ConnectionState.Failed,
                supportingText = {
                    when (val state = connection) {
                        is ConnectionState.Failed -> Text(state.message)
                        // Inline, not only after pressing: the rule for what
                        // counts as an address is one function, `normalizeServerUrl`.
                        else -> if (serverUrlEdit.isNotBlank() &&
                            normalizeServerUrl(serverUrlEdit) == null
                        ) {
                            Text(INVALID_SERVER_URL_MESSAGE)
                        }
                    }
                },
                modifier = Modifier.fillMaxWidth(),
                keyboardOptions = KeyboardOptions(
                    keyboardType = KeyboardType.Uri,
                    imeAction = ImeAction.Done,
                ),
                keyboardActions = KeyboardActions(
                    onDone = { viewModel.checkConnection(serverUrlEdit) },
                ),
            )

            Button(
                onClick = { viewModel.checkConnection(serverUrlEdit) },
                enabled = normalizeServerUrl(serverUrlEdit) != null &&
                    connection !is ConnectionState.Checking,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(top = 12.dp)
                    .height(52.dp),
            ) {
                if (connection is ConnectionState.Checking) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(20.dp),
                        color = MaterialTheme.colorScheme.onPrimary,
                        strokeWidth = 2.dp,
                    )
                } else {
                    Text(
                        stringResource(R.string.first_run_check_connection),
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            AnimatedVisibility(visible = connection is ConnectionState.Connected) {
                Column(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        modifier = Modifier.padding(top = 16.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Default.CheckCircle,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(20.dp),
                        )
                        Text(
                            text = stringResource(R.string.first_run_connected),
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.padding(start = 8.dp),
                        )
                    }
                    Button(
                        // Stores the verified server, *then* leaves. Doing it
                        // here rather than in the probe is what stops the write
                        // from re-creating this screen out from under the user.
                        onClick = viewModel::acceptServer,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(top = 12.dp)
                            .height(52.dp),
                    ) {
                        Text(
                            stringResource(R.string.first_run_continue),
                            fontWeight = FontWeight.SemiBold,
                        )
                    }
                }
            }

            // Issue #174. The probe has just been told which API this server
            // speaks, and this is the moment the person choosing the server is
            // still standing in front of it. Below the "Continue" button on
            // purpose: a mismatch is a warning, not a reason to stop.
            val versionBanner by viewModel.versionBanner.collectAsState()
            VersionMismatchBanner(versionBanner)

            // The escape hatch. Someone re-installing already knows all of this,
            // and a probe can fail for reasons that do not stop a sign-in (a VPN
            // that only routes some traffic, say) — so the screen must never be
            // a wall.
            TextButton(
                // Leaves without storing anything — they'll set the server from
                // "Advanced" on the sign-in form.
                onClick = viewModel::dismissFirstRun,
                modifier = Modifier.padding(top = 8.dp),
            ) {
                Text(stringResource(R.string.first_run_skip))
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SignInScreen(
    onLoginSuccess: () -> Unit,
    viewModel: LoginViewModel,
    currentServerUrl: String,
) {
    var username by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    val isLoading by viewModel.isLoading.collectAsState()
    val error by viewModel.error.collectAsState()
    val message by viewModel.message.collectAsState()
    val isRegistering by viewModel.isRegistering.collectAsState()
    // With no server configured yet, the URL field is the only useful control on
    // this screen — start expanded rather than hidden behind the toggle.
    var showAdvanced by remember { mutableStateOf(shouldExpandAdvanced(currentServerUrl)) }
    var serverUrlEdit by remember(currentServerUrl) { mutableStateOf(currentServerUrl) }

    val canSubmit = username.isNotBlank() && password.isNotBlank() &&
        (!isRegistering || email.isNotBlank()) &&
        currentServerUrl.isNotBlank() && !isLoading

    fun submit() {
        if (!canSubmit) return
        if (isRegistering) {
            viewModel.register(username, email, password)
        } else {
            viewModel.login(username, password, onLoginSuccess)
        }
    }

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background,
    ) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(32.dp),
            verticalArrangement = Arrangement.Center,
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // Logo
            Icon(
                painter = painterResource(id = R.drawable.ic_launcher_foreground),
                contentDescription = stringResource(R.string.app_name),
                tint = Color.Unspecified,
                modifier = Modifier
                    .size(72.dp)
                    .clip(RoundedCornerShape(16.dp))
                    .padding(bottom = 8.dp)
            )
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.headlineLarge,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.primary,
            )
            Text(
                text = stringResource(R.string.login_tagline),
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

            // Pending-approval notice after a successful "Request access".
            message?.let { msg ->
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 16.dp),
                ) {
                    Text(
                        text = msg,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        modifier = Modifier.padding(12.dp),
                        textAlign = TextAlign.Center,
                    )
                }
            }

            // Username
            OutlinedTextField(
                value = username,
                onValueChange = { username = it },
                label = { Text(stringResource(R.string.login_username)) },
                singleLine = true,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 12.dp)
                    .semantics { contentType = ContentType.Username },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            )

            // Email — only while requesting access, mirroring LoginPage.jsx.
            AnimatedVisibility(visible = isRegistering) {
                OutlinedTextField(
                    value = email,
                    onValueChange = { email = it },
                    label = { Text(stringResource(R.string.login_email)) },
                    singleLine = true,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(bottom = 12.dp)
                        .semantics { contentType = ContentType.EmailAddress },
                    keyboardOptions = KeyboardOptions(
                        keyboardType = KeyboardType.Email,
                        imeAction = ImeAction.Next,
                    ),
                )
            }

            // Password
            OutlinedTextField(
                value = password,
                onValueChange = { password = it },
                label = { Text(stringResource(R.string.login_password)) },
                singleLine = true,
                visualTransformation = PasswordVisualTransformation(),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(bottom = 24.dp)
                    .semantics {
                        contentType = if (isRegistering) {
                            ContentType.NewPassword
                        } else {
                            ContentType.Password
                        }
                    },
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                keyboardActions = KeyboardActions(onDone = { submit() }),
            )

            // Submit — "Sign In" or "Request Access"
            Button(
                onClick = { submit() },
                enabled = canSubmit,
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
                    Text(
                        text = stringResource(
                            if (isRegistering) {
                                R.string.login_request_access
                            } else {
                                R.string.login_sign_in
                            },
                        ),
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            // Mode toggle (issue #221). Same gate as the button above: with no
            // server there is nothing to request an account from.
            TextButton(
                onClick = { viewModel.setRegistering(!isRegistering) },
                enabled = currentServerUrl.isNotBlank() && !isLoading,
                modifier = Modifier.padding(top = 8.dp),
            ) {
                Text(
                    text = stringResource(
                        if (isRegistering) {
                            R.string.login_have_account
                        } else {
                            R.string.login_need_account
                        },
                    ),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Text(
                    text = " " + stringResource(
                        if (isRegistering) {
                            R.string.login_sign_in
                        } else {
                            R.string.login_request_access
                        },
                    ),
                    fontWeight = FontWeight.SemiBold,
                )
            }

            AnimatedVisibility(visible = isRegistering) {
                Text(
                    text = stringResource(R.string.login_approval_note),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }

            // Advanced toggle — server URL configuration
            TextButton(
                onClick = { showAdvanced = !showAdvanced },
                modifier = Modifier.padding(top = 16.dp),
            ) {
                Text(stringResource(R.string.login_advanced))
                Icon(
                    imageVector = if (showAdvanced) Icons.Default.ExpandLess else Icons.Default.ExpandMore,
                    contentDescription = null,
                )
            }

            AnimatedVisibility(visible = showAdvanced) {
                Column(modifier = Modifier.fillMaxWidth()) {
                    OutlinedTextField(
                        value = serverUrlEdit,
                        onValueChange = {
                            serverUrlEdit = it
                            // Otherwise a refusal stays on screen while the user is
                            // busy correcting the very thing it complains about.
                            viewModel.clearError()
                        },
                        label = { Text(stringResource(R.string.login_server_url)) },
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
                        onClick = { viewModel.saveServerUrl(serverUrlEdit.trim()) },
                        // Compare the normalized form: "tandem.example.com" and
                        // "https://tandem.example.com" are the same server, and
                        // restarting the process to store an identical value is pure
                        // loss. Still enabled when it doesn't normalize at all, so
                        // pressing it produces the error rather than nothing.
                        enabled = serverUrlEdit.isNotBlank() &&
                            normalizeServerUrl(serverUrlEdit).let { it == null || it != currentServerUrl },
                        modifier = Modifier.fillMaxWidth().height(48.dp),
                    ) {
                        Text(stringResource(R.string.login_save))
                    }
                    Text(
                        text = stringResource(
                            if (currentServerUrl.isBlank()) {
                                R.string.login_server_unset_hint
                            } else {
                                R.string.login_server_set_hint
                            },
                        ),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            }
        }
    }
}
