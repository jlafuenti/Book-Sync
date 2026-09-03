package com.booksync.data.remote

import com.booksync.data.remote.dto.QueueItemResponse
import com.booksync.data.remote.dto.TranscriptionStatusResponse
import okhttp3.MultipartBody
import retrofit2.Response
import retrofit2.http.*

/**
 * Retrofit API interface for the BookSync server.
 */
interface BookSyncApi {

    // ============ Health ============

    /**
     * Readiness probe, used by the first-run "Check connection" (issue #175).
     *
     * Unauthenticated and cheap, which is what makes it the right thing to point
     * at a URL a stranger just typed: it answers "is there a Tandem server here"
     * before any credentials exist. Returns 503 when the server is up but its
     * database is not, which surfaces as an `HttpException` rather than success —
     * correct, since signing in would fail too.
     *
     * Takes an **absolute** URL, and carries [BYPASS_BASE_URL_HEADER] so
     * `BaseUrlInterceptor` leaves it alone. The address being probed is by
     * definition not the configured one — that is the question — and it must not
     * be stored to become reachable: storing an unverified address put a
     * mistyped host in DataStore and replaced the first-run screen with a login
     * form for a server that does not exist. Only a verified address is stored,
     * afterwards.
     */
    @Headers("$BYPASS_BASE_URL_HEADER: 1")
    @GET
    suspend fun getHealth(@Url url: String): HealthResponse

    // ============ Auth ============

    @POST("api/auth/login")
    suspend fun login(@Body request: LoginRequest): TokenResponse

    /**
     * Submit an access request. 201 with a bare `{"message": …}` — the account
     * is created pending an admin's approval, so there is no user to return and
     * no session. Was declared as `UserResponse` until issue #221 first called
     * it from a device; see [RegisterResponse].
     */
    @POST("api/auth/register")
    suspend fun register(@Body request: RegisterRequest): RegisterResponse

    // NOTE: /api/auth/refresh is deliberately NOT here. It lives on
    // AuthRefreshApi, which is built on a client carrying no AuthInterceptor and
    // no authenticator. Refreshing through this API is what let a rejected
    // refresh token recurse until every dispatcher thread was parked and no
    // request in the process could complete (issue #143). Putting it back would
    // restore the deadlock.

    @GET("api/auth/me")
    suspend fun getMe(): UserResponse

    @PUT("api/auth/me")
    suspend fun updateMe(@Body request: UpdateMeRequest): UserResponse

    @POST("api/auth/change-password")
    suspend fun changePassword(@Body request: PasswordChangeRequest): Response<Unit>

    @POST("api/auth/logout")
    suspend fun logout(): Response<Unit>

    // ============ Library ============

    // The list endpoints are paginated (issue #48): `{items,total,page,limit}`,
    // limit ≤ 500. The repository walks every page before touching Room —
    // see BookSyncRepository.fetchAllPages.
    @GET("api/library/ebooks")
    suspend fun getEbooks(@Query("page") page: Int, @Query("limit") limit: Int): PageResponse<EBookResponse>

    @GET("api/library/audiobooks")
    suspend fun getAudiobooks(@Query("page") page: Int, @Query("limit") limit: Int): PageResponse<AudioBookResponse>

    @GET("api/library/pairs")
    suspend fun getPairs(@Query("page") page: Int, @Query("limit") limit: Int): PageResponse<BookPairResponse>

    // ---- Per-book extended metadata (description, publisher, etc.) ----
    // These endpoints return richer metadata than the list endpoints. We use
    // them for the Book Details screen, which shows the description.

    @GET("api/library/ebooks/{ebookId}")
    suspend fun getEbookMetadata(@Path("ebookId") ebookId: Int): BookMetadataResponse

    @GET("api/library/audiobooks/{audiobookId}")
    suspend fun getAudiobookMetadata(@Path("audiobookId") audiobookId: Int): BookMetadataResponse

    @POST("api/library/scan")
    suspend fun scanLibrary(): Response<Unit>

    @POST("api/library/pairs")
    suspend fun createPair(@Body request: CreatePairRequest): BookPairResponse

    @DELETE("api/library/pairs/{pairId}")
    suspend fun deletePair(@Path("pairId") pairId: Int): Response<Unit>

    // ---- Acknowledging NEW items (issue #222) ----
    // `acknowledged` is a property of the item on the server, not of the
    // viewer, so these are the same two endpoints the web posts to
    // (web/src/api.js acknowledgeNewItems / acknowledgeNewPairs) and clearing
    // NEW here clears it everywhere.

    @POST("api/library/new-items/acknowledge")
    suspend fun acknowledgeNewItems(@Body request: AcknowledgeItemsRequest): AcknowledgeResponse

    @POST("api/library/new-pairs/acknowledge")
    suspend fun acknowledgeNewPairs(@Body request: AcknowledgePairsRequest): AcknowledgeResponse

    // ============ Search ============

    @GET("api/library/search")
    suspend fun searchLibrary(@Query("q") query: String): com.booksync.data.remote.SearchResponse

    // ============ Files ============

    @GET("api/files/ebook/{ebookId}")
    @Streaming
    suspend fun downloadEbook(@Path("ebookId") ebookId: Int): Response<okhttp3.ResponseBody>

    @GET("api/files/audiobook/{audiobookId}")
    @Streaming
    suspend fun downloadAudiobook(@Path("audiobookId") audiobookId: Int): Response<okhttp3.ResponseBody>

    // ============ Sync Map ============

    @GET("api/files/syncmap/{pairId}")
    suspend fun getSyncMap(@Path("pairId") pairId: Int): SyncMapResponse

    // ============ Bookmark ============
    //
    // Only the history log is left here. The bookmark GET/PUT were adapters
    // over the canonical position service and are gone (issue #102) — read and
    // write positions through getPosition/updatePosition below.

    @GET("api/sync/bookmark/{pairId}/log")
    suspend fun getBookmarkLog(
        @Path("pairId") pairId: Int,
        @Query("limit") limit: Int = 50
    ): List<BookmarkLogResponse>

    // ============ Canonical position ============

    // Response<T> so callers can tell 204 ("never opened") from 200. That
    // distinction matters: a position at chapter 0 and no position at all are
    // different things, and conflating them is how a failed restore came to
    // overwrite a real position.
    @GET("api/sync/position/{scope}/{id}")
    suspend fun getPosition(
        @Path("scope") scope: String,
        @Path("id") id: Int,
    ): Response<PositionResponse>

    // Response<T> again, this time so callers can detect HTTP 409 — the
    // multi-device conflict-resolution contract (issue #54) returns the current
    // authoritative server state with a 409 status when captured_at is stale,
    // rather than a normal 200. A plain suspend return type would make Retrofit
    // throw HttpException for that case, losing the response body.
    @PUT("api/sync/position/{scope}/{id}")
    suspend fun updatePosition(
        @Path("scope") scope: String,
        @Path("id") id: Int,
        @Body update: PositionUpdateRequest,
    ): Response<PositionResponse>

    // Deletes the canonical bookmark (+ hints) and every user_progress row for
    // this pair, server-side. Used by "Reset Progress" for a paired book —
    // the per-media PUT-with-zeros this replaced only pinned position at 0 and
    // left the old bookmark in place, which then re-seeded progress right back
    // (issue: reset buttons not actually resetting). Aliases
    // `DELETE /api/sync/position/pair/{id}`.
    @DELETE("api/sync/progress/pair/{pairId}")
    suspend fun resetPairProgress(@Path("pairId") pairId: Int): Response<Unit>

    // Scoped true reset for standalone (unpaired) media (issue #103): deletes
    // the canonical bookmark + hints + progress projection so GET /position
    // answers 204 ("unread") again, instead of the legacy zero-write's
    // 200-with-zeros. Scope is "ebook" or "audiobook".
    @DELETE("api/sync/position/{scope}/{id}")
    suspend fun resetPosition(
        @Path("scope") scope: String,
        @Path("id") id: Int,
    ): Response<Unit>

    // ============ Transcription ============

    /** Add a book pair to the transcription queue (user-role endpoint). */
    @POST("api/transcription/{pairId}/start")
    suspend fun startTranscription(@Path("pairId") pairId: Int): QueueItemResponse

    /** Get transcription status for a specific pair (status + optional progress). */
    @GET("api/transcription/{pairId}/status")
    suspend fun getTranscriptionStatus(@Path("pairId") pairId: Int): TranscriptionStatusResponse

    /** Cancel an in-progress or queued transcription job for this pair. */
    @POST("api/transcription/{pairId}/cancel")
    suspend fun cancelTranscription(@Path("pairId") pairId: Int): QueueItemResponse

    /** List all active queue items (pending + processing). Used by Home "In Queue" section. */
    @GET("api/transcription/queue")
    suspend fun getTranscriptionQueue(): List<QueueItemResponse>
}
