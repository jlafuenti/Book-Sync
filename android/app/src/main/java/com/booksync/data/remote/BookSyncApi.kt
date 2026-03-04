package com.booksync.data.remote

import okhttp3.MultipartBody
import retrofit2.Response
import retrofit2.http.*

/**
 * Retrofit API interface for the BookSync server.
 */
interface BookSyncApi {

    // ============ Auth ============

    @POST("api/auth/login")
    suspend fun login(@Body request: LoginRequest): TokenResponse

    @POST("api/auth/register")
    suspend fun register(@Body request: RegisterRequest): UserResponse

    @POST("api/auth/refresh")
    suspend fun refreshToken(@Body request: RefreshRequest): TokenResponse

    @GET("api/auth/me")
    suspend fun getMe(): UserResponse

    // ============ Library ============

    @GET("api/library/ebooks")
    suspend fun getEbooks(): List<EBookResponse>

    @GET("api/library/audiobooks")
    suspend fun getAudiobooks(): List<AudioBookResponse>

    @GET("api/library/pairs")
    suspend fun getPairs(): List<BookPairResponse>

    @POST("api/library/scan")
    suspend fun scanLibrary(): Response<Unit>

    @POST("api/library/pairs")
    suspend fun createPair(@Body request: CreatePairRequest): BookPairResponse

    @DELETE("api/library/pairs/{pairId}")
    suspend fun deletePair(@Path("pairId") pairId: Int): Response<Unit>

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

    @GET("api/sync/bookmark/{pairId}")
    suspend fun getBookmark(@Path("pairId") pairId: Int): BookmarkResponse

    @PUT("api/sync/bookmark/{pairId}")
    suspend fun updateBookmark(
        @Path("pairId") pairId: Int,
        @Body update: BookmarkUpdateRequest
    ): BookmarkResponse

    @GET("api/sync/bookmark/{pairId}/log")
    suspend fun getBookmarkLog(
        @Path("pairId") pairId: Int,
        @Query("limit") limit: Int = 50
    ): List<BookmarkLogResponse>

    // ============ User Progress ============

    @GET("api/sync/progress/{mediaType}/{mediaId}")
    suspend fun getProgress(
        @Path("mediaType") mediaType: String,
        @Path("mediaId") mediaId: Int
    ): ProgressResponse

    @PUT("api/sync/progress/{mediaType}/{mediaId}")
    suspend fun updateProgress(
        @Path("mediaType") mediaType: String,
        @Path("mediaId") mediaId: Int,
        @Body update: ProgressUpdateRequest
    ): ProgressResponse
}
