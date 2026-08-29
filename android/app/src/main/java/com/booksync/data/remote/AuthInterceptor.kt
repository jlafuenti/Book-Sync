package com.booksync.data.remote

import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Attaches the bearer token, and notices when the server says the password must be
 * changed. Nothing else.
 *
 * Refreshing used to live here too, and that was the whole of issue #143: the
 * refresh went out on the same client this interceptor is installed on, so its own
 * 401 re-entered here and refreshed again, parking a dispatcher thread per level
 * until nothing in the process could complete. Refresh now belongs to
 * [TokenAuthenticator], which OkHttp calls once per 401 from outside the chain.
 *
 * The token is read from [TokenManager.cachedAccessToken], an in-memory value —
 * the old code did a blocking DataStore read on the OkHttp thread for every single
 * request.
 */
@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenManager: TokenManager,
    private val passwordResetGate: PasswordResetGate,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val token = tokenManager.cachedAccessToken()
        val request = if (!token.isNullOrEmpty()) {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            chain.request()
        }

        val response = chain.proceed(request)

        // The server refuses every route but /auth/me, /auth/change-password and
        // /auth/logout while must_reset_password is set (issue #209). Without this
        // the refusal surfaces as an unexplained failure on whatever screen is
        // open — an admin can set the flag while the app is running, so checking
        // only at launch is not enough.
        //
        // peekBody, not body(): consuming it would leave the caller with an empty
        // stream and no error message. require_role also answers 403, hence
        // matching on the detail rather than the status.
        if (response.code == 403) {
            val peeked = runCatching { response.peekBody(512).string() }.getOrNull()
            if (peeked?.contains("password_reset_required") == true) {
                passwordResetGate.raise()
            }
        }

        return response
    }
}
