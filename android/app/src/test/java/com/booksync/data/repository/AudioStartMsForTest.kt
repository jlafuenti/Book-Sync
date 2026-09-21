package com.booksync.data.repository

import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.entity.BookmarkEntity
import com.booksync.data.local.entity.SyncPointEntity
import io.mockk.coEvery
import io.mockk.mockk
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [BookSyncRepository.audioStartMsFor] — the audio-start ladder's executor
 * (issue #643).
 *
 * The reported failure: read the ebook, get in the car, pick the book, and
 * Android Auto starts wherever the audiobook was last played. The player read
 * `bookmark.audioPositionMs` and nothing else, and `saveReaderPosition` keeps
 * the *previous* audio position whenever its own sync-point lookup misses — so
 * the row describes a current page beside a stale listening position.
 *
 * [AudioStartResolverTest] pins which rung is tried first; these pin what each
 * rung actually resolves to against the cached sync map.
 */
@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class AudioStartMsForTest {

    private val syncPointDao = mockk<SyncPointDao>(relaxed = true)

    private val pairId = 42

    // Long enough for SyncMatcher's exact pass, and stored verbatim as the
    // sync point's own preview so an exact substring match is trivially found.
    private val pageText =
        "It was a dark and stormy page and nothing in particular happened for quite a while there truly."

    private fun point(
        chapter: Int,
        sentence: Int,
        audioStartMs: Int,
        preview: String? = null,
    ) = SyncPointEntity(
        bookPairId = pairId,
        epubChapter = chapter,
        epubSentenceIndex = sentence,
        epubTextPreview = preview,
        audioStartMs = audioStartMs,
        audioEndMs = audioStartMs + 5_000,
    )

    private fun givenPoints(vararg points: SyncPointEntity) {
        coEvery { syncPointDao.getPointsForPair(pairId) } returns points.toList()
    }

    private fun bookmark(
        source: String = "ebook",
        audioPositionMs: Int? = null,
        epubChapter: Int? = null,
        epubSentenceIndex: Int? = null,
        epubTextPreview: String? = null,
    ) = BookmarkEntity(
        scopeKey = TEST_SCOPE,
        bookPairId = pairId,
        source = source,
        epubChapter = epubChapter,
        epubSentenceIndex = epubSentenceIndex,
        audioPositionMs = audioPositionMs,
        epubTextPreview = epubTextPreview,
        updatedAt = "1788220800000",
    )

    private fun repository() = buildRepository(syncPointDao = syncPointDao)

    // --- The reported bug ---------------------------------------------------

    @Test
    fun `a reading position is converted instead of replaying the old audio position`() = runTest {
        givenPoints(point(chapter = 4, sentence = 12, audioStartMs = 600_000, preview = pageText))

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(
                source = "ebook",
                audioPositionMs = 3_600_000, // an hour in, from the last listening session
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = pageText,
            ),
        )

        // The sync point, minus the same 5 s rewind a resume uses.
        assertEquals(595_000L, ms)
    }

    @Test
    fun `listening keeps its own position`() = runTest {
        givenPoints(point(chapter = 4, sentence = 12, audioStartMs = 600_000, preview = pageText))

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(
                source = "audiobook",
                audioPositionMs = 3_600_000,
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = pageText,
            ),
        )

        assertEquals(3_600_000L, ms)
    }

    // --- Rung 2: chapter + sentence ----------------------------------------

    @Test
    fun `no preview falls through to the chapter and sentence`() = runTest {
        // What a row written before the preview column existed looks like, and
        // what a save whose text lookup missed leaves behind.
        givenPoints(
            point(chapter = 4, sentence = 0, audioStartMs = 500_000),
            point(chapter = 4, sentence = 12, audioStartMs = 600_000),
            point(chapter = 4, sentence = 20, audioStartMs = 700_000),
        )

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(audioPositionMs = 3_600_000, epubChapter = 4, epubSentenceIndex = 12),
        )

        assertEquals(595_000L, ms)
    }

    @Test
    fun `a sentence with no exact point takes the last one before it`() = runTest {
        givenPoints(
            point(chapter = 4, sentence = 0, audioStartMs = 500_000),
            point(chapter = 4, sentence = 20, audioStartMs = 700_000),
        )

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(epubChapter = 4, epubSentenceIndex = 12),
        )

        assertEquals(495_000L, ms)
    }

    @Test
    fun `a sentence index from another chapter never walks out of its chapter`() = runTest {
        // The mismatched anchor of issue #644: on a lookup miss
        // saveReaderPosition writes the NEW chapter and inherits the PREVIOUS
        // chapter's sentence index. The server's epub_to_audio would walk back
        // into chapter 3 and seek there with full confidence. Staying inside
        // chapter 4 bounds the error to the top of the right chapter.
        givenPoints(
            point(chapter = 3, sentence = 200, audioStartMs = 400_000),
            point(chapter = 4, sentence = 0, audioStartMs = 900_000),
            point(chapter = 4, sentence = 5, audioStartMs = 950_000),
        )

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(epubChapter = 4, epubSentenceIndex = 200),
        )

        // The last point in chapter 4 at or before sentence 200 — not chapter 3.
        assertEquals(945_000L, ms)
    }

    @Test
    fun `a sentence index below every point in the chapter takes the chapter's first`() = runTest {
        givenPoints(
            point(chapter = 3, sentence = 0, audioStartMs = 100_000),
            point(chapter = 4, sentence = 40, audioStartMs = 900_000),
        )

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(epubChapter = 4, epubSentenceIndex = 2),
        )

        assertEquals(895_000L, ms)
    }

    @Test
    fun `a chapter with no sentence index lands at the top of the chapter`() = runTest {
        givenPoints(
            point(chapter = 4, sentence = 0, audioStartMs = 900_000),
            point(chapter = 4, sentence = 9, audioStartMs = 950_000),
        )

        val ms = repository().audioStartMsFor(pairId, bookmark(epubChapter = 4))

        assertEquals(895_000L, ms)
    }

    // --- Falling back -------------------------------------------------------

    @Test
    fun `no sync map at all falls back to the stored position, not to zero`() = runTest {
        // A pair whose map was never cached — the common case behind the
        // report, since SyncMapAutoFetch only fires for a synced, downloaded
        // pair. A stale listening position beats the start of the book.
        givenPoints()

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(
                audioPositionMs = 3_600_000,
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = pageText,
            ),
        )

        assertEquals(3_600_000L, ms)
    }

    @Test
    fun `a chapter the map does not cover falls back to the stored position`() = runTest {
        givenPoints(point(chapter = 1, sentence = 0, audioStartMs = 10_000))

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(audioPositionMs = 3_600_000, epubChapter = 9, epubSentenceIndex = 2),
        )

        assertEquals(3_600_000L, ms)
    }

    @Test
    fun `an empty record starts at the beginning`() = runTest {
        givenPoints()

        assertEquals(0L, repository().audioStartMsFor(pairId, bookmark()))
    }

    @Test
    fun `no bookmark at all starts at the beginning`() = runTest {
        assertEquals(0L, repository().audioStartMsFor(pairId, null))
    }

    @Test
    fun `a point earlier than the rewind clamps at zero rather than going negative`() = runTest {
        givenPoints(point(chapter = 0, sentence = 0, audioStartMs = 2_000))

        val ms = repository().audioStartMsFor(pairId, bookmark(epubChapter = 0))

        assertEquals(0L, ms)
    }

    // --- The Stored rung must stay rewind-free (issue #656) -----------------

    @Test
    fun `the stored rung is never rewound a second time`() = runTest {
        // saveReaderPosition now rewinds by RESUME_REWIND_MS before writing
        // audioPositionMs (issue #656), so a value read back off the Stored
        // rung is already correct. If this rung applied the rewind again, an
        // already-rewound reader save would creep 5s earlier every time the
        // ladder fell through to it.
        givenPoints() // no sync map cached — only the stored rung can answer

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(source = "ebook", audioPositionMs = 595_000, epubChapter = 4, epubSentenceIndex = 12),
        )

        assertEquals(595_000L, ms)
    }

    @Test
    fun `a stored position of exactly zero falls through instead of being treated as found`() = runTest {
        // What a rewind-to-zero automatic save leaves behind: a sync point
        // inside the first RESUME_REWIND_MS clamps to a stored 0 (issue #656).
        // planAudioStart's `takeIf { it > 0 }` excludes a 0 from the Stored
        // rung entirely, so the ladder still gets a chance to place the reader
        // more precisely via the text/sentence rungs rather than stopping at
        // "start of book".
        givenPoints(point(chapter = 4, sentence = 12, audioStartMs = 600_000, preview = pageText))

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(
                source = "ebook",
                audioPositionMs = 0,
                epubChapter = 4,
                epubSentenceIndex = 12,
                epubTextPreview = pageText,
            ),
        )

        // Falls through to the text rung's rewound match rather than stopping
        // at the stored zero.
        assertEquals(595_000L, ms)
    }

    @Test
    fun `a stored zero with nothing else to fall back on starts at the beginning`() = runTest {
        // The other half of the edge case above: when the text/sentence rungs
        // also have nothing to offer, a stored 0 still means "start of book" —
        // the same answer an empty record gives.
        givenPoints()

        val ms = repository().audioStartMsFor(
            pairId,
            bookmark(source = "ebook", audioPositionMs = 0),
        )

        assertEquals(0L, ms)
    }
}
