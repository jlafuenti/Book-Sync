package com.booksync.data.remote

import dagger.Lazy
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class AuthInterceptor @Inject constructor(
    private val tokenManager: TokenManager,
    private val api: Lazy<BookSyncApi>,
    private val passwordResetGate: PasswordResetGate,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val token = runBlocking { tokenManager.getAccessToken().firstOrNull() }
        val request = if (!token.isNullOrEmpty()) {
            chain.request().newBuilder()
                .header("Authorization", "Bearer $token")
                .build()
        } else {
            chain.request()
        }

        val response = chain.proceed(request)

        // The server refuses every route but /auth/me, /auth/change-password and
        // /auth/logout while must_reset_password is set (issue #209). Without
        // this the refusal would surface as an unexplained failure on whatever
        // screen happened to be open — an admin can set the flag while the app is
        // running, so it is not enough to check only at launch.
        //
        // peekBody, not body(): consuming it here would leave the caller with an
        // empty stream and no error message. 403 is also what require_role
        // returns, hence matching on the detail rather than the status.
        if (response.code == 403) {
            val peeked = runCatching { response.peekBody(512).string() }.getOrNull()
            if (peeked?.contains("password_reset_required") == true) {
                passwordResetGate.raise()
            }
            return response
        }

        if (response.code != 401) return response

        // 401 received — attempt token refresh
        response.close()

        val refreshToken = runBlocking { tokenManager.getRefreshToken().firstOrNull() }
        if (refreshToken.isNullOrEmpty()) {
            runBlocking { tokenManager.clearTokens() }
            return chain.proceed(chain.request())
        }

        return try {
            val newTokens = runBlocking {
                api.get().refreshToken(RefreshRequest(refreshToken))
            }
            runBlocking { tokenManager.saveTokens(newTokens.access_token, newTokens.refresh_token) }
            val retryRequest = chain.request().newBuilder()
                .header("Authorization", "Bearer ${newTokens.access_token}")
                .build()
            chain.proceed(retryRequest)
        } catch (e: Exception) {
            runBlocking { tokenManager.clearTokens() }
            chain.proceed(chain.request())
        }
    }
}
