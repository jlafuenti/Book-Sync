package com.booksync.data.remote

import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.runBlocking
import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject

class AuthInterceptor @Inject constructor(
    private val tokenManager: TokenManager
) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val requestBuilder = chain.request().newBuilder()
        
        // Skip auth for login/register
        if (chain.request().url.encodedPath.contains("auth/login") ||
            chain.request().url.encodedPath.contains("auth/register")) {
            return chain.proceed(chain.request())
        }

        val token = runBlocking {
            tokenManager.getAccessToken().firstOrNull()
        }
        
        if (!token.isNullOrEmpty()) {
            requestBuilder.addHeader("Authorization", "Bearer $token")
        }
        
        val response = chain.proceed(requestBuilder.build())

        // If server returns 401 Unauthorized, clear stored tokens
        // so the app knows to redirect to login
        if (response.code == 401) {
            runBlocking {
                tokenManager.clearTokens()
            }
        }

        return response
    }
}
