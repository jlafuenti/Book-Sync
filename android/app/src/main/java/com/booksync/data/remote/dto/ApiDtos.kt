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
    val created_at: String
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
    val uploaded_at: String
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

// ============ Sync ============

@Serializable
data class SyncPointDto(
    val epub_chapter: Int,
    val epub_sentence_index: Int,
    val epub_text_preview: String? = null,
    val audio_start_ms: Int,
    val audio_end_ms: Int
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
    val audio_position_ms: Int? = null
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
    val updated_at: String,
    val synced_at: String? = null
)
