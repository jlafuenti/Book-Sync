package com.booksync.data.remote

import kotlinx.serialization.Serializable

/**
 * Data Transfer Objects for the Tandem API.
 * These map to the server's Pydantic schemas.
 */

// ============ Auth ============

@Serializable
data class LoginRequest(
    val username: String,
    val password: String,
    /**
     * This install's device id (issue #250).
     *
     * The server stores it on the refresh session the login opens, so signing
     * out on the phone signs out the phone and leaves the browser — and its
     * unsynced reading positions — alone. Null-defaulted, and kotlinx omits
     * defaults, so the field simply is not sent by a build that has none.
     */
    val device_id: String? = null,
)

@Serializable
data class RegisterRequest(
    val username: String,
    val email: String,
    val password: String
)

/**
 * `POST /api/auth/register` (issue #221).
 *
 * A bare message, not a user. The route has no `response_model` and returns
 * `{"message": "Access request submitted. …"}` — the account is created
 * *pending*, so there is no session and nothing to describe. The declaration
 * used to say [UserResponse], which had simply never been exercised: on a
 * device the 201 decoded into "Fields [id, username, email, …] are required",
 * and a request that had succeeded was shown to the user as a red error.
 *
 * Nullable with a default for the same reason [HealthResponse] is. This runs on
 * the login screen, against whatever version of the server someone happens to
 * host, and the 201 is the fact that matters — a body this app does not
 * recognise must never turn a created account into a failure.
 */
@Serializable
data class RegisterResponse(
    val message: String? = null,
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
    /**
     * "superadmin" | "admin" | "editor" | "user" — see
     * [com.booksync.data.auth.hasMinRole] and `server/models/user.py`.
     *
     * The server has always sent this (`schemas.py` UserResponse.role) but this
     * class had no field for it, and the shared Json uses
     * `ignoreUnknownKeys = true`, so it was dropped silently — which is why the
     * app showed editor-only actions to everyone (issue #170).
     *
     * Defaults to "user", the least privileged value, so an older server that
     * omits it fails closed rather than being assumed to grant edit rights.
     */
    val role: String = "user",
    val is_admin: Boolean,
    val is_active: Boolean,
    val theme: String = "blueprint",
    val created_at: String,
    /**
     * Set by admin-create, admin password reset and the fresh-install bootstrap.
     * The server refuses everything but /auth/me, /auth/change-password and
     * /auth/logout while it is true (issue #209). Defaulted so the app still
     * deserializes a response from a server older than that change.
     */
    val must_reset_password: Boolean = false,
)

/**
 * `GET /api/health` (issue #175) — the first-run "Check connection" probe.
 *
 * Every field is optional on purpose. The probe's job is to tell a stranger
 * whether there is a Tandem server at the address they typed, and a server old
 * enough to answer with a payload we don't recognise is still a server they can
 * sign in to. A missing field must not become "connection failed": today's
 * server sends `{"status": "healthy"}` and nothing else, so the version fields
 * are absent from every deployment currently running.
 */
@Serializable
data class HealthResponse(
    val status: String? = null,
    val api_version: Int? = null,
    val app_version: String? = null,
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

/**
 * Body for `DELETE /api/auth/me` — self-service account deletion (issue #146).
 *
 * The password goes with the request even though the caller already holds a
 * valid token: this is the one action in the app that cannot be undone, and a
 * session left open on a borrowed device must not be enough to destroy the
 * account. Field name matches the server's Pydantic `AccountDelete`, which
 * `server/tests/test_schema_contract.py` pins.
 */
@Serializable
data class AccountDeleteRequest(
    val password: String,
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
    val uploaded_at: String,
    // Server-side "seen" flag (issue #222). Defaults to true so a server too old
    // to send it leaves the phone's own acknowledged_items table alone rather
    // than declaring the whole library new.
    val acknowledged: Boolean = true
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
    val cover_path: String? = null,
    // See EBookResponse.acknowledged (issue #222).
    val acknowledged: Boolean = true
)

@Serializable
data class BookPairResponse(
    val id: Int,
    val ebook: EBookResponse,
    val audiobook: AudioBookResponse,
    val status: String,
    val matched_at: String? = null,
    val synced_at: String? = null,
    // The server's live sync-map version (issue #55). Null means *unknown* —
    // either the pair has no map, or the endpoint didn't load it — so a null
    // must never be read as "the map went away" and must not drop the cache.
    val sync_map_version: Int? = null,
    // See EBookResponse.acknowledged (issue #222).
    val acknowledged: Boolean = true
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

/**
 * Bodies for the two acknowledge endpoints (issue #222). Field names match the
 * server's `AcknowledgeItemsRequest` / `AcknowledgePairsRequest`
 * (`server/schemas.py`), which the web already posts to.
 */
@Serializable
data class AcknowledgeItemsRequest(
    val ebook_ids: List<Int> = emptyList(),
    val audiobook_ids: List<Int> = emptyList(),
)

@Serializable
data class AcknowledgePairsRequest(
    val pair_ids: List<Int>,
)

/** Counts returned by both acknowledge endpoints. Only ever logged. */
@Serializable
data class AcknowledgeResponse(
    val acknowledged_ebooks: Int = 0,
    val acknowledged_audiobooks: Int = 0,
    val acknowledged_pairs: Int = 0,
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
//
// Only the history log has its own DTO. Position writes and reads use
// PositionUpdateRequest/PositionResponse below — the bookmark and progress
// request/response pair went with the legacy adapters (issue #102).

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

// ============ Pagination (issue #48) ============

/** One page of a paginated list endpoint. `page` is 1-based; `limit` is the
 *  page size the server applied, so `page * limit < total` means there is more. */
@Serializable
data class PageResponse<T>(
    val items: List<T>,
    val total: Int,
    val page: Int,
    val limit: Int,
)

// ============ Canonical position ============
//
// One record per book, written atomically. Replaced the pair of
// updateBookmark + updateProgress calls, which the server adjudicated
// separately — either could be rejected while the other applied, leaving two
// records describing different positions with nothing to reconcile them.

@Serializable
data class PositionHintDto(
    val kind: String,
    val value: String,
    val audio_position_ms: Int? = null,
)

@Serializable
data class PositionHintResponse(
    val kind: String,
    val device_id: String,
    val value: String,
    val anchor_revision: Long,
    val audio_position_ms: Int? = null,
    // False means the anchor moved after this hint was captured. Stale, not
    // useless: its device makes it current again by re-capturing.
    val current: Boolean,
)

@Serializable
data class PositionUpdateRequest(
    // Null (the default) omits `source` from the wire payload entirely —
    // kotlinx.serialization skips a property whose value equals its default
    // when `encodeDefaults = false` (see AppModule.provideJson). The server's
    // `PositionUpdate.source` treats omission as "keep whatever is stored"
    // (see server schemas.PositionUpdate), which is exactly what a save from
    // a paused/idle player needs: it must still move the position without
    // re-claiming which format opens next (issue: background saves hijacking
    // format routing — see BookSyncRepository.savePlaybackPosition's
    // `claimFormat` parameter).
    val source: String? = null,
    // Spine index — the axis both readers position by.
    val epub_chapter: Int? = null,
    val epub_sentence_index: Int? = null,
    // The sync-map version `epub_sentence_index` was resolved against (issue
    // #116) — the version the cached points came from, carried on the local
    // row so a deferred push attests what was true at resolution time. Null
    // means "unknown"; the server then stamps NULL rather than the live
    // version, and re-anchors a write whose version trails the live map.
    val sync_map_version: Int? = null,
    val epub_text_preview: String? = null,
    val epub_progress_percent: Float? = null,
    val audio_position_ms: Int? = null,
    val is_completed: Boolean? = null,
    val hint: PositionHintDto? = null,
    val append_to_log: Boolean = false,
    val captured_at: String? = null,
    val device_id: String? = null,
    val device_name: String? = null,
)

@Serializable
data class PositionResponse(
    val scope: String,
    val book_pair_id: Int? = null,
    val ebook_id: Int? = null,
    val audiobook_id: Int? = null,
    val source: String,
    val anchor_revision: Long,
    val epub_chapter: Int? = null,
    val epub_sentence_index: Int? = null,
    // Which map the stored index is expressed in; null = unknown. Kept on the
    // local row so pushing this position back attests the right version.
    val sync_map_version: Int? = null,
    val epub_text_preview: String? = null,
    val epub_progress_percent: Float? = null,
    val audio_position_ms: Int? = null,
    val is_completed: Boolean = false,
    val captured_at: String? = null,
    val updated_at: String,
    val device_id: String? = null,
    val device_name: String? = null,
    val hints: List<PositionHintResponse> = emptyList(),
)
