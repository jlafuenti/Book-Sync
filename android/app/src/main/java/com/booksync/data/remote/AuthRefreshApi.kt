package com.booksync.data.remote

import retrofit2.http.Body
import retrofit2.http.POST

/**
 * `/api/auth/refresh`, and nothing else, on a client that carries no
 * [AuthInterceptor] and no [TokenAuthenticator] (issue #143).
 *
 * The deadlock this exists to prevent came from refreshing through the *same*
 * client the interceptor was installed on: the refresh's own 401 re-entered the
 * interceptor and refreshed again, until every dispatcher thread was parked in
 * `runBlocking` and no request in the process could complete.
 *
 * Isolating the endpoint makes that structurally impossible rather than relying on
 * a guard someone must remember. It also removes the `Lazy<BookSyncApi>` cycle
 * that existed only to break the circular dependency the old design created.
 *
 * Same shape as the `@Named("dictionary")` client, which is already a second,
 * interceptor-free client for a different reason.
 */
interface AuthRefreshApi {
    @POST("api/auth/refresh")
    suspend fun refreshToken(@Body request: RefreshRequest): TokenResponse
}
