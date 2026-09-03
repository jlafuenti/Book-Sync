package com.booksync.data.remote

import com.booksync.di.ApplicationScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import javax.inject.Inject
import javax.inject.Singleton

/** Where the "Try the demo" sign-in has got to (issue #147). */
sealed interface DemoSignInState {
    data object Idle : DemoSignInState

    data object Running : DemoSignInState

    /** Signed in. The screen navigates on this, then calls [DemoSignIn.consume]. */
    data object Succeeded : DemoSignInState

    data class Failed(val message: String) : DemoSignInState
}

/**
 * The whole "Try the demo" sign-in, run outside any screen's lifetime
 * (issue #147).
 *
 * **This is application-scoped for the same reason [FirstRunGate] is, and it was
 * learned the same way.** The first cut ran the flow in `viewModelScope`, and on
 * a device it died in the middle: logcat showed `/api/health` answering 200,
 * `POST /api/auth/login` answering 200 — and then nothing at all, with the
 * welcome screen still up a minute later. Storing the server URL re-creates the
 * login destination, that takes the ViewModel and its scope with it, and every
 * continuation after the in-flight request was cancelled: no tokens saved, no
 * dismissal, no navigation, and no error either, because a cancelled coroutine
 * has nothing to report. A flow that reconfigures the thing that owns it cannot
 * live in that thing.
 *
 * Two independent fixes, because either alone leaves a window:
 *
 *  1. The work runs on the process-lifetime [ApplicationScope], and the result
 *     lands on [state] — a `@Singleton` `StateFlow` that a freshly built
 *     ViewModel re-reads. Destroying the screen mid-flight now loses nothing.
 *  2. Nothing is stored until the credentials have been accepted. The probe and
 *     the sign-in both travel to an absolute URL ([BookSyncApi.loginAt]), so the
 *     one write that can disturb the UI happens after the only two requests that
 *     can fail for ordinary reasons.
 *
 * The order below is therefore: verify the server, verify the credentials, store
 * the server, save the session, dismiss the welcome screen. Each step's failure
 * leaves the user exactly where they were, with a sentence explaining it.
 */
@Singleton
class DemoSignIn @Inject constructor(
    private val api: BookSyncApi,
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
    private val userScopeProvider: UserScopeProvider,
    private val firstRunGate: FirstRunGate,
    private val serverVersionGate: ServerVersionGate,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow<DemoSignInState>(DemoSignInState.Idle)
    val state: StateFlow<DemoSignInState> = _state.asStateFlow()

    /**
     * Back to [DemoSignInState.Idle].
     *
     * Called by the screen once it has acted on a result — navigated away, or
     * shown the failure. Without it a returning composition would re-navigate on
     * a `Succeeded` from minutes ago.
     */
    fun consume() {
        _state.value = DemoSignInState.Idle
    }

    /** Ignores a second tap while the first is still in flight. */
    fun start(demo: DemoAccount) {
        if (_state.value is DemoSignInState.Running) return
        _state.value = DemoSignInState.Running
        scope.launch {
            _state.value = attempt(demo)
        }
    }

    private suspend fun attempt(demo: DemoAccount): DemoSignInState {
        // 1. Is there a Tandem server there at all? Cheap, unauthenticated, and
        //    it carries the API version, which is worth recording before the
        //    first real call (issue #174).
        try {
            serverVersionGate.record(api.getHealth("${demo.url}/api/health"))
        } catch (e: Exception) {
            return DemoSignInState.Failed(DEMO_UNREACHABLE_MESSAGE)
        }

        // 2. Do the demo credentials still work? Absolute URL: the demo server is
        //    not the configured one yet, and must not become it until this
        //    answers.
        val tokens = try {
            api.loginAt("${demo.url}/api/auth/login", LoginRequest(demo.username, demo.password))
        } catch (e: HttpException) {
            // The server's own sentence: "HTTP 401" does not distinguish a
            // rotated demo password from a suspended demo account, and only one
            // of those is worth trying again.
            return DemoSignInState.Failed(
                e.serverDetail() ?: "Demo sign-in failed (HTTP ${e.code()})",
            )
        } catch (e: IOException) {
            return DemoSignInState.Failed(DEMO_UNREACHABLE_MESSAGE)
        } catch (e: Exception) {
            return DemoSignInState.Failed(e.message ?: "Demo sign-in failed")
        }

        // 3. Only now is there something worth storing the address for. This is
        //    the write that re-creates the login destination; everything below it
        //    survives that only because this coroutine is not owned by the screen.
        if (!serverUrlManager.setServerUrl(demo.url)) {
            // Close to unreachable — demoAccountOrNull normalized the URL when
            // the build settings were read — but a session with no server to
            // spend it on is worse than a message.
            return DemoSignInState.Failed(INVALID_SERVER_URL_MESSAGE)
        }

        return try {
            tokenManager.saveTokens(tokens.access_token, tokens.refresh_token)
            // Claim rows written before the cache was scoped (issue #314), and
            // learn the role before any screen renders (issue #170). The role is
            // best-effort: a failure here must not undo a sign-in that worked,
            // and an unknown role is treated as no permissions.
            userScopeProvider.onAuthenticated()
            runCatching { tokenManager.saveRole(api.getMeAt("${demo.url}/api/auth/me").role) }
            firstRunGate.dismiss()
            DemoSignInState.Succeeded
        } catch (e: Exception) {
            DemoSignInState.Failed(e.message ?: "Demo sign-in failed")
        }
    }
}
