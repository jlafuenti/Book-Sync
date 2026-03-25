package com.booksync.data.remote

import okhttp3.Interceptor
import okhttp3.Response
import java.io.IOException

class RetryInterceptor(private val maxRetries: Int = 3) : Interceptor {
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        var response: Response? = null
        var exception: IOException? = null
        var tryCount = 0

        while (tryCount < maxRetries) {
            try {
                response = chain.proceed(request)
                if (response.isSuccessful) {
                    return response
                }
                // Only retry on server errors or non-200 if necessary, but here we simply retry failures.
                // If it's 401 Unauthorized, maybe AuthInterceptor will handle it.
                // Actually, let's only retry on actual IOExceptions (like UnknownHostException)
                // If we get a response, we just return it rather than blindly retrying 404s.
                return response
            } catch (e: IOException) {
                exception = e
                tryCount++
                if (tryCount >= maxRetries) {
                    break
                }
                // Exponential backoff or simple sleep
                try {
                    Thread.sleep((1000 * tryCount).toLong())
                } catch (interruptedException: InterruptedException) {
                    Thread.currentThread().interrupt()
                }
            }
        }

        if (response != null) {
            return response
        }
        throw exception ?: IOException("Unknown network error")
    }
}
