package com.booksync.data.remote

import android.util.Log
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.Authenticator
import okhttp3.Request
import okhttp3.Response
import okhttp3.Route
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
        // Two 401s for one call means the token we just supplied was refused as
        // well. Refreshing again would be the start of the old recursion.
        if (responseCount(response) >= 2) return null

        val attempted = response.request.header("Authorization")?.removePrefix("Bearer ")

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
                    tokenManager.clearTokens()
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

    private fun responseCount(response: Response): Int {
        var count = 1
        var prior = response.priorResponse
        while (prior != null) {
            count++
            prior = prior.priorResponse
        }
        return count
    }

    companion object {
        /** Qualifier for the isolated refresh client's API. */
        const val REFRESH_API = "refreshApi"
    }
}
