package com.booksync.data.remote

import kotlinx.serialization.Serializable

/**
 * Data Transfer Objects for the BookSync API.
 * These map to the server's Pydantic schemas.
 */

// ============ Auth ============

@Serializable
data class LoginRequest(
    val username: String,
    val password: String
)

@Serializable
data class RegisterRequest(
    val username: String,
    val email: String,
    val password: String
)

@Serializable
data class TokenResponse(
    val access_token: String,
    val refresh_token: String,
    val token_type: String = "bearer"
)

@Serializable
data class RefreshRequest(
    val refresh_token: String
)

@Serializable
data class UserResponse(
    val id: Int,
    val username: String,
    val email: String,
    val is_admin: Boolean,
    val is_active: Boolean,
    val theme: String = "blueprint",
    val created_at: String
)

@Serializable
data class UpdateMeRequest(
    val theme: String? = null
)

/**
 * Body for `POST /api/auth/change-password`.
 * Field names match the server's Pydantic `PasswordChange` model (snake_case).
 */
@Serializable
data class PasswordChangeRequest(
    val old_password: String,
    val new_password: String,
)

// ============ Library ============

@Serializable
data class EBookResponse(
    val id: Int,
    val title: String,
    val author: String? = null,
    val filename: String,
    val file_size: Long? = null,
    val format: String,
    val series: String? = null,
    val series_index: Float? = null,
    val uploaded_at: String
)

@Serializable
data class AudioBookResponse(
    val id: Int,
    val title: String,
    val author: String? = null,
    val filename: String,
    val file_size: Long? = null,
    val duration_seconds: Int? = null,
    val format: String,
    val series: String? = null,
    val series_index: Float? = null,
    val uploaded_at: String,
    val cover_path: String? = null
)

@Serializable
data class BookPairResponse(
    val id: Int,
    val ebook: EBookResponse,
    val audiobook: AudioBookResponse,
    val status: String,
    val matched_at: String? = null,
    val synced_at: String? = null
)

/**
 * Lightweight slice of the server's `EBookDetailResponse` / `AudioBookDetailResponse`.
 * Used by the Book Details screen to pull description + metadata that aren't
 * in the local cache. We only decode the fields we render — kotlinx ignores
 * the rest thanks to `ignoreUnknownKeys = true` on the Json instance.
 */
@Serializable
data class BookMetadataResponse(
    val id: Int,
    val title: String,
    val author: String? = null,
    val series: String? = null,
    val series_index: Float? = null,
    val description: String? = null,
    val publisher: String? = null,
    val publish_year: Int? = null,
    val language: String? = null,
    val narrators: String? = null,
)

@Serializable
data class CreatePairRequest(
    val ebook_id: Int,
    val audiobook_id: Int
)

// ============ Search ============

@Serializable
data class SearchResponse(
    val query: String,
    val ebooks: List<EBookResponse>,
    val audiobooks: List<AudioBookResponse>,
    val book_pairs: List<BookPairResponse>
)

// ============ Sync ============

@Serializable
data class SyncPointDto(
    val epub_chapter: Int,
    val epub_sentence_index: Int,
    val epub_text_preview: String? = null,
    val audio_start_ms: Int,
    val audio_end_ms: Int,
    // Default keeps compatibility with servers that don't send confidence yet
    val confidence: Float = 0f
)

@Serializable
data class SyncMapResponse(
    val id: Int,
    val book_pair_id: Int,
    val version: Int,
    val total_sentences: Int,
    val total_chapters: Int,
    val created_at: String,
    val sync_points: List<SyncPointDto> = emptyList()
)

// ============ Bookmark ============

@Serializable
data class BookmarkUpdateRequest(
    val source: String,
    val epub_chapter: Int? = null,
    val epub_sentence_index: Int? = null,
    val audio_position_ms: Int? = null,
    val epub_locator: String? = null,
    // Audio position the locator was captured at; travels with it so a second
    // device can judge whether the locator is still usable (issue #40).
    val locator_audio_ms: Int? = null,
    // When false (default), server updates the bookmark but does NOT append
    // a BookmarkLog row. Clients set true only on pause / stop / 30-min
    // boundaries. Mirrors server BookmarkUpdate.append_to_log.
    val append_to_log: Boolean = false,
    // Multi-device conflict resolution (issue #54). When captured_at is provided
    // and older than what's stored, the server rejects the write with HTTP 409
    // instead of applying it. Omitting captured_at preserves legacy last-write-wins.
    val captured_at: String? = null,
    val device_id: String? = null,
    val device_name: String? = null
)

@Serializable
data class BookmarkResponse(
    val id: Int,
    val user_id: Int,
    val book_pair_id: Int,
    val source: String,
    val epub_chapter: Int? = null,
    val epub_sentence_index: Int? = null,
    val audio_position_ms: Int? = null,
    val epub_locator: String? = null,
    val locator_audio_ms: Int? = null,
    val updated_at: String,
    val synced_at: String? = null,
    val device_id: String? = null,
    val device_name: String? = null,
    val captured_at: String? = null
)

@Serializable
data class BookmarkLogResponse(
    val id: Int,
    val source: String,
    val prev_epub_chapter: Int? = null,
    val prev_epub_sentence_index: Int? = null,
    val prev_audio_position_ms: Int? = null,
    val new_epub_chapter: Int? = null,
    val new_epub_sentence_index: Int? = null,
    val new_audio_position_ms: Int? = null,
    val changed_at: String,
    val device_id: String? = null,
    val device_name: String? = null,
    val captured_at: String? = null
)

// ============ User Progress ============

@Serializable
data class ProgressUpdateRequest(
    val book_pair_id: Int? = null,
    val epub_cfi: String? = null,
    val epub_chapter: Int? = null,
    val epub_progress_percent: Float? = null,
    val audio_position_ms: Int? = null,
    val is_completed: Boolean? = null,
    val device_id: String? = null,
    val device_name: String? = null,
    // See BookmarkUpdateRequest.captured_at — same multi-device conflict contract.
    val captured_at: String? = null
)

@Serializable
data class ProgressResponse(
    val id: Int,
    val user_id: Int,
    val media_type: String,
    val book_pair_id: Int? = null,
    val ebook_id: Int? = null,
    val audiobook_id: Int? = null,
    val epub_cfi: String? = null,
    val epub_chapter: Int? = null,
    val epub_progress_percent: Float? = null,
    val audio_position_ms: Int? = null,
    val is_completed: Boolean,
    val updated_at: String,
    val device_id: String? = null,
    val device_name: String? = null,
    val captured_at: String? = null
)
