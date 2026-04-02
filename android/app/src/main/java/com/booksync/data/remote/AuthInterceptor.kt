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
    private val api: Lazy<BookSyncApi>
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
