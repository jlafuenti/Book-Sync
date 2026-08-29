package com.booksync.data.remote

import android.util.Log
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
import java.net.HttpURLConnection.HTTP_UNAUTHORIZED
import javax.inject.Inject
import javax.inject.Named
import javax.inject.Singleton

/**
 * Refreshes the access token when the server answers 401 (issue #143).
 *
 * An [Authenticator] rather than logic inside [AuthInterceptor], for three reasons
 * that were each a bug:
 *
 *  - OkHttp calls this **after** a 401 response, once, and walking
 *    [responseCount] gives a structural bound on retries. The old code recursed
 *    until every dispatcher thread was parked and the whole process stopped
 *    making network calls — including logout, so the user could not even leave.
 *  - It runs outside the interceptor chain, so the refresh cannot re-enter the
 *    chain that triggered it. The refresh also goes out on its own client
 *    ([AuthRefreshApi]), which makes re-entry impossible rather than merely
 *    unlikely.
 *  - Returning null here is how you say "I cannot authenticate this" — OkHttp then
 *    surfaces the 401 to the caller, which is the honest answer. The old code
 *    re-issued the request with no bearer at all and handed back a second 401 that
 *    told the caller nothing.
 *
 * The refresh is single-flighted: when an access token expires, every in-flight
 * request 401s at once, and without the mutex each would POST its own refresh and
 * they would race one another into DataStore.
 */
@Singleton
class TokenAuthenticator(
    private val tokenManager: TokenManager,
    private val refreshApi: () -> AuthRefreshApi,
) : Authenticator {

    @Inject
    constructor(
        tokenManager: TokenManager,
        @Named(REFRESH_API) refreshApi: dagger.Lazy<AuthRefreshApi>,
    ) : this(tokenManager, { refreshApi.get() })

    private val refreshMutex = Mutex()

    override fun authenticate(route: Route?, response: Response): Request? {
        // A 401 from login or register means the password was wrong, not that the
        // token expired. Refreshing and replaying would double the failed attempt
        // against any server-side rate limiter, and would sign the user back in as
        // whoever the still-cached session belonged to.
        val path = response.request.url.encodedPath
        if (path.endsWith("/auth/login") || path.endsWith("/auth/register")) return null

        // Two 401s for one call means the token we just supplied was refused as
        // well. Refreshing again would be the start of the old recursion.
        //
        // Count 401s, not responses: OkHttp attaches priorResponse for every
        // follow-up it makes -- redirects, 408, 503-with-Retry-After -- and it does
        // so before calling this. Counting all of them meant that on a server which
        // redirects (the app accepts an http:// URL, and proxies answer those with
        // a 301) every 401 already carried a priorResponse, so the bound tripped
        // immediately and the token was never refreshed at all. That failure is
        // quieter than the deadlock and just as terminal: bare 401s on every
        // screen, no clearTokens, so nothing routes the user back to login.
        if (authFailureCount(response) >= 2) return null

        // No Authorization on the failed request means either there was no session
        // to begin with, or OkHttp stripped the header because a redirect crossed
        // hosts. Attaching one now would hand the session token to whatever host
        // the redirect named -- a worse bug than the one this class fixes.
        val attempted = response.request.header("Authorization")
            ?.removePrefix("Bearer ")
            ?: return null

        return runBlocking {
            refreshMutex.withLock {
                // Someone may have refreshed while this call waited for the lock.
                // Retrying with their token is both correct and one fewer POST.
                val current = tokenManager.cachedAccessToken()
                if (!current.isNullOrEmpty() && current != attempted) {
                    return@withLock response.request.withBearer(current)
                }

                val refreshToken = tokenManager.currentRefreshToken()
                if (refreshToken.isNullOrEmpty()) {
                    // Nothing to refresh with. End the session only if one still
                    // looks live: when several requests 401 together, the waiters
                    // arrive here *after* the holder's failed refresh already
                    // cleared, and getAccessToken() has no distinctUntilChanged, so
                    // each redundant clear is another null emission and another
                    // navigate(LOGIN) { popUpTo(0) }.
                    if (!tokenManager.cachedAccessToken().isNullOrEmpty()) {
                        tokenManager.clearTokens()
                    }
                    return@withLock null
                }

                try {
                    val fresh = refreshApi().refreshToken(RefreshRequest(refreshToken))
                    tokenManager.saveTokens(fresh.access_token, fresh.refresh_token)
                    response.request.withBearer(fresh.access_token)
                } catch (e: Exception) {
                    // The refresh token is dead: password changed, signed out on
                    // another device, or simply expired. End the session once and
                    // let the 401 through — BookSyncNavigation watches the token
                    // clear and routes to login.
                    Log.i("TokenAuthenticator", "refresh rejected; ending session (${e.message})")
                    tokenManager.clearTokens()
                    null
                }
            }
        }
    }

    private fun Request.withBearer(token: String): Request =
        newBuilder().header("Authorization", "Bearer $token").build()

    /** How many 401s this call has collected, including the one being handled. */
    private fun authFailureCount(response: Response): Int =
        generateSequence(response) { it.priorResponse }
            .count { it.code == HTTP_UNAUTHORIZED }

    companion object {
        /** Qualifier for the isolated refresh client's API. */
        const val REFRESH_API = "refreshApi"
    }
}
