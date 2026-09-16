package com.booksync.player

/**
 * How the cover published on the media session is sized (issue #570).
 *
 * The session's playing item used to carry the cover as
 * `content://<applicationId>.fileprovider/covers/<id>.jpg`. Media3 mirrors
 * `artworkUri` into the platform session metadata as `METADATA_KEY_ART_URI`
 * and `METADATA_KEY_DISPLAY_ICON_URI`, and SystemUI — which draws the shade's
 * media player, the lock screen and quick settings — opens that URI with its
 * own ContentResolver, in its own process. The provider is `exported="false"`
 * (correct: it fronts app-private files) and nothing ever calls
 * `grantUriPermission` for SystemUI, so every read threw a `SecurityException`
 * and the controls stayed blank.
 *
 * The route taken instead is the one Media3 documents for session artwork:
 * publish the cover as **bytes**. `BitmapLoader.loadBitmapFromMetadata` prefers
 * `artworkData` over `artworkUri`, and `LegacyConversions` turns the decoded
 * bitmap into `METADATA_KEY_ALBUM_ART` on the platform session, which every
 * consumer can read without a grant. Nothing has to enumerate consumer
 * packages, and nothing app-private leaves the process.
 *
 * Bytes cross a Binder, though, and the whole transaction buffer is about 1 MB
 * shared with everything else in flight — so a cover straight off disk (3000 px
 * and megabytes is ordinary) cannot simply be attached. That is what the plan
 * below is for.
 *
 * Pure, and deliberately not in `CoverArtHelper`: that class is excluded from
 * Kover as untestable framework glue (`app/build.gradle.kts`), so logic placed
 * there cannot be covered. Same reason as [coverArtPlan] and
 * [HeartbeatThrottle].
 */

/**
 * The largest edge the session's cover is decoded to.
 *
 * SystemUI materialises this as an ARGB_8888 bitmap in its own process: at
 * 512 px that is 1 MB of its heap, which is the usual notification-artwork
 * ballpark. Larger buys nothing — the shade and lock-screen slots are a few
 * hundred px — and costs every consumer memory.
 */
const val MEDIA_ARTWORK_MAX_PX = 512

/**
 * The largest encoded cover that may go on the session.
 *
 * Kept to a quarter of the ~1 MB Binder transaction buffer, because the
 * metadata is not the only thing in flight and the failure mode when it does
 * not fit is a `TransactionTooLargeException` that takes the session with it,
 * not a missing image. A 512 px JPEG lands around 40–80 KB, so this is slack
 * rather than a limit anything real hits.
 */
const val MEDIA_ARTWORK_MAX_BYTES = 256 * 1024

/** One attempt at re-encoding a cover for the session: decode bound, then JPEG quality. */
data class ArtworkEncode(val maxPx: Int, val quality: Int)

/**
 * The ordered attempts at getting a cover under [MEDIA_ARTWORK_MAX_BYTES] —
 * the same "plan" shape as [coverArtPlan], for the same reason: whether an
 * encoding fits cannot be known without doing it, so the caller walks the rungs
 * and stops at the first that yields usable bytes.
 *
 * The descent is quality first, then size: dropping quality on a photographic
 * cover costs far less visibly than halving its resolution.
 */
fun mediaArtworkEncodePlan(): List<ArtworkEncode> = listOf(
    ArtworkEncode(MEDIA_ARTWORK_MAX_PX, 85),
    ArtworkEncode(MEDIA_ARTWORK_MAX_PX, 60),
    ArtworkEncode(MEDIA_ARTWORK_MAX_PX / 2, 60),
)

/** Whether an encoding is worth publishing: non-empty, and within the Binder budget. */
fun mediaArtworkFits(byteCount: Int, maxBytes: Int = MEDIA_ARTWORK_MAX_BYTES): Boolean =
    byteCount in 1..maxBytes

/**
 * Walk [plan] with [encode] and return the first result that fits.
 *
 * Null when nothing does — and null means *no artwork*, never "fall back to the
 * URI". An unreadable FileProvider URI on the session is the bug itself
 * (issue #570); offering it as a fallback would bring the `SecurityException`
 * back for exactly the covers most likely to be worth showing.
 *
 * [encode] is the only impure part (BitmapFactory and a JPEG compress), so it
 * is a parameter: `CoverArtHelper` passes the real one, the JVM suite passes a
 * fake and can hold the walk itself.
 */
fun encodeMediaArtwork(
    plan: List<ArtworkEncode> = mediaArtworkEncodePlan(),
    maxBytes: Int = MEDIA_ARTWORK_MAX_BYTES,
    encode: (ArtworkEncode) -> ByteArray?,
): ByteArray? {
    for (attempt in plan) {
        val bytes = encode(attempt) ?: continue
        if (mediaArtworkFits(bytes.size, maxBytes)) return bytes
    }
    return null
}
