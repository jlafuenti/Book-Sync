package com.booksync.data.remote

import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException

class RetryInterceptor(private val maxRetries: Int = 3) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        var lastException: IOException? = null

        repeat(maxRetries) { attempt ->
            try {
                val response = chain.proceed(request)
                // Only retry on server errors (5xx), not client errors
                if (response.isSuccessful || response.code < 500) {
                    return response
                }
                response.close()
            } catch (e: IOException) {
                lastException = e
                if (attempt == maxRetries - 1) throw e
            }
        }

        throw lastException ?: IOException("Request failed after $maxRetries attempts")
    }
}
