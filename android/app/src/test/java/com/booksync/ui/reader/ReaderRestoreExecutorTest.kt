package com.booksync.ui.reader

import com.booksync.data.sync.PositionHint
import com.booksync.data.sync.RestoreStep
import com.booksync.data.sync.StoredPosition
import com.booksync.data.sync.planRestore
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.floatOrNull
import kotlinx.serialization.json.int
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import kotlinx.serialization.json.longOrNull
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The Android *execution* of the restore ladder (issue #227).
 *
 * `PositionResolver.planRestore` decides which rungs to try and in what order;
 * that half is pinned to the shared golden vectors by
 * `PositionResolverParityTest`. This suite pins the other half — what
 * [ReaderRestoreExecutor] does with each rung against a spine — which used
 * to live inside `ReaderActivity`, where coverage excluded it and every
 * change was verified by opening books by hand.
 *
 * The first test drives the executor with the *same* fixture cases the parity
 * suite uses, over a fake spine built from each case's position, so the two
 * halves are asserted from one source of truth. The rest are the executor's
 * own edges: where the text search looks first, what it refuses to search
 * for, how an undecodable hint falls through, and the invariant that a plan
 * holding any anchor never comes back as "unread".
 */
class ReaderRestoreExecutorTest {

    // ---------------------------------------------------------------- fakes

    /** A spine whose chapters are plain strings; `null` is a chapter that failed to parse. */
    private class FakeSpine(
        val chapters: List<String?>,
        private val lengths: LongArray? = null,
    ) : SpineSource {
        val reads = mutableListOf<Int>()
        override val spineCount: Int get() = chapters.size
        override suspend fun plainTextAt(index: Int): String? {
            reads += index
            return chapters.getOrNull(index)
        }
        override suspend fun chapterLengths(): LongArray = lengths ?: super.chapterLengths()
    }

    /**
     * Mirrors what `Locator.fromJSON` does with the fixture's hint values: an
     * empty object (`{}`) and a CFI string both decode to nothing, so the hint
     * rung fails and the ladder moves on.
     */
    private fun readiumLikeHintDecodes(value: String): Boolean =
        value.trim().let { it.startsWith("{") && it.contains("\"href\"") }

    private fun filler(i: Int) = "Chapter $i filler text about nothing in particular, repeated for length. "

    private fun spineOf(count: Int, planted: Map<Int, String> = emptyMap()) = FakeSpine(
        List(count) { i -> filler(i).repeat(3) + (planted[i]?.let { " $it " } ?: "") + filler(i) },
    )

    // ------------------------------------------------ the shared golden vectors

    private fun loadCases() = javaClass.classLoader
        ?.getResourceAsStream("sync_parity/restore_cases.json")
        ?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }
        ?.let { Json.parseToJsonElement(it).jsonArray }
        ?: error("restore_cases.json not on the test classpath — copySyncParityFixtures must run")

    private fun parsePosition(raw: String): StoredPosition? {
        if (raw.trim() == "null") return null
        val o = Json.parseToJsonElement(raw).jsonObject
        return StoredPosition(
            anchorRevision = o["anchor_revision"]?.jsonPrimitive?.longOrNull ?: 0L,
            source = o["source"]?.jsonPrimitive?.contentOrNull,
            epubChapter = o["epub_chapter"]?.jsonPrimitive?.intOrNull,
            epubSentenceIndex = o["epub_sentence_index"]?.jsonPrimitive?.intOrNull,
            epubTextPreview = o["epub_text_preview"]?.jsonPrimitive?.contentOrNull,
            epubProgressPercent = o["epub_progress_percent"]?.jsonPrimitive?.floatOrNull,
            audioPositionMs = o["audio_position_ms"]?.jsonPrimitive?.intOrNull,
            hints = o["hints"]?.jsonArray?.map { h ->
                val ho = h.jsonObject
                PositionHint(
                    kind = ho["kind"]!!.jsonPrimitive.content,
                    deviceId = ho["device_id"]!!.jsonPrimitive.content,
                    value = ho["value"]!!.jsonPrimitive.content,
                    anchorRevision = ho["anchor_revision"]!!.jsonPrimitive.long,
                    audioPositionMs = ho["audio_position_ms"]?.jsonPrimitive?.intOrNull,
                )
            } ?: emptyList(),
        )
    }

    @Test
    fun `lands on the first rung of every golden plan that the spine can satisfy`() = runTest {
        val cases = loadCases()
        assertTrue("expected at least one golden vector", cases.size > 0)

        for (case in cases) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val ctx = obj["context"]!!.jsonObject
            val spineCount = ctx["spine_count"]!!.jsonPrimitive.int
            val position = parsePosition(obj["position"].toString())
            val expected = obj["expected"]!!.jsonArray.map { it.jsonPrimitive.content }

            val steps = planRestore(
                position,
                spineCount = spineCount,
                deviceId = ctx["device_id"]!!.jsonPrimitive.content,
                hintKind = ctx["hint_kind"]!!.jsonPrimitive.content,
            )
            assertEquals("$name: plan drifted from the fixture", expected, steps.map { it.kind })

            // A cooperative book: the preview really is in the chapter the
            // position names (or the last chapter, when the named one is past
            // the end), and the sync map answers an audio rung with text that
            // is in the book too.
            val preview = position?.epubTextPreview?.trim().orEmpty()
            val previewChapter = position?.epubChapter?.takeIf { it in 0 until spineCount }
                ?: (spineCount - 1)
            val audioPreview = "the sync map named this sentence for the audio position"
            val audioChapter = 7
            val spine = spineOf(
                spineCount,
                buildMap {
                    if (preview.isNotEmpty()) put(previewChapter, preview)
                    put(audioChapter, audioPreview)
                },
            )
            val executor = ReaderRestoreExecutor(
                spine,
                hintDecodes = ::readiumLikeHintDecodes,
                audio = AudioAnchorSource { audioChapter to audioPreview },
            )

            val result = executor.resolve(steps)

            if (expected.isEmpty()) {
                assertEquals("$name: an empty plan is an unread book", PositionSavePolicy.RestoreOutcome.Unread, result.outcome)
                assertNull("$name: nothing to land on", result.target)
                continue
            }

            // Every rung but an undecodable hint lands on this book.
            val landable = steps.first { it !is RestoreStep.Hint || readiumLikeHintDecodes(it.value) }
            assertEquals("$name: landed on the wrong rung", landable, result.landed)
            assertEquals(name, PositionSavePolicy.RestoreOutcome.Landed, result.outcome)

            val target = result.target
            when (landable) {
                is RestoreStep.Text -> {
                    val spineTarget = target as RestoreTarget.Spine
                    assertEquals("$name: text rung must land in the chapter holding the preview", previewChapter, spineTarget.index)
                    val p = spineTarget.progression!!
                    assertTrue("$name: progression $p must be inside the chapter", p > 0.0 && p < 1.0)
                }
                is RestoreStep.Chapter ->
                    assertEquals("$name: chapter rung is the top of that spine item", RestoreTarget.Spine(landable.chapter, null), target)
                is RestoreStep.Percent -> {
                    val spineTarget = target as RestoreTarget.Spine
                    assertTrue("$name: percent rung maps inside the spine", spineTarget.index in 0 until spineCount)
                }
                is RestoreStep.Audio -> {
                    val spineTarget = target as RestoreTarget.Spine
                    assertEquals("$name: audio rung lands where the sync map's text is", audioChapter, spineTarget.index)
                    assertEquals("$name: audio rung asks for its result to be persisted as a hint", landable.audioPositionMs, spineTarget.persistAsHintForAudioMs)
                }
                is RestoreStep.Hint -> assertEquals(RestoreTarget.Hint(landable.value), target)
            }
        }
    }

    @Test
    fun `a plan holding any anchor never resolves to unread, even on a hostile book`() = runTest {
        // Every chapter blank, no hint decodes, the sync map knows nothing:
        // the executor may fail to land, but it must report *unresolved* —
        // the verdict that withholds full saves — never *unread*, which is
        // what let chapter 0 be written over a real position.
        val hostile = FakeSpine(List(40) { "" })
        val executor = ReaderRestoreExecutor(hostile, hintDecodes = { false }, audio = AudioAnchorSource { 0 to "" })

        for (case in loadCases()) {
            val obj = case.jsonObject
            val name = obj["name"]!!.jsonPrimitive.content
            val ctx = obj["context"]!!.jsonObject
            val steps = planRestore(
                parsePosition(obj["position"].toString()),
                spineCount = ctx["spine_count"]!!.jsonPrimitive.int,
                deviceId = ctx["device_id"]!!.jsonPrimitive.content,
                hintKind = ctx["hint_kind"]!!.jsonPrimitive.content,
            )
            val result = executor.resolve(steps)
            if (steps.isEmpty()) {
                assertEquals(name, PositionSavePolicy.RestoreOutcome.Unread, result.outcome)
            } else {
                assertNotEquals("$name: an anchored plan is never 'unread'", PositionSavePolicy.RestoreOutcome.Unread, result.outcome)
            }
        }
    }

    @Test
    fun `an anchored plan whose every rung fails is unresolved with no target`() = runTest {
        val executor = ReaderRestoreExecutor(FakeSpine(List(5) { "nothing here" }), hintDecodes = { false })
        val result = executor.resolve(
            listOf(RestoreStep.Hint("{}"), RestoreStep.Text("this sentence is in no chapter at all", 2)),
        )
        assertNull(result.target)
        assertNull(result.landed)
        assertEquals(PositionSavePolicy.RestoreOutcome.Unresolved, result.outcome)
    }

    @Test
    fun `an empty plan is unread`() = runTest {
        val result = ReaderRestoreExecutor(spineOf(3)).resolve(emptyList())
        assertNull(result.target)
        assertEquals(PositionSavePolicy.RestoreOutcome.Unread, result.outcome)
    }

    // -------------------------------------------------------------- text rung

    @Test
    fun `text search finds the preview in the seed chapter first`() = runTest {
        val preview = "the harbour lay still under a copper sky"
        val spine = spineOf(10, mapOf(2 to preview, 6 to preview))
        val executor = ReaderRestoreExecutor(spine)

        val target = executor.executeStep(RestoreStep.Text(preview, seedChapter = 6))

        assertEquals(6, (target as RestoreTarget.Spine).index)
        assertEquals("the seed chapter is read before any other", 6, spine.reads.first())
    }

    @Test
    fun `text search walks outward from the seed and prefers the nearer chapter`() = runTest {
        val preview = "althea counted the ships at anchor"
        val spine = spineOf(12, mapOf(1 to preview, 9 to preview))
        val executor = ReaderRestoreExecutor(spine)

        // Seed 7: 9 is two away, 1 is six away.
        val target = executor.executeStep(RestoreStep.Text(preview, seedChapter = 7))

        assertEquals(9, (target as RestoreTarget.Spine).index)
    }

    @Test
    fun `text search without a hint starts from the middle of the spine`() = runTest {
        val preview = "somewhere in the middle of the book"
        val spine = spineOf(10, mapOf(5 to preview))
        val executor = ReaderRestoreExecutor(spine)

        assertEquals(5, executor.findSpineIndexForText(preview))
        assertEquals(5, spine.reads.first())
    }

    @Test
    fun `a text rung with no seed chapter searches from the front of the book`() = runTest {
        // planRestore leaves the seed null when the stored chapter is not
        // navigable; the rung then starts at chapter 0 rather than the middle.
        val preview = "somewhere in the middle of the book"
        val spine = spineOf(10, mapOf(5 to preview))
        val executor = ReaderRestoreExecutor(spine)

        val target = executor.executeStep(RestoreStep.Text(preview, seedChapter = null))

        assertEquals(5, (target as RestoreTarget.Spine).index)
        assertEquals(0, spine.reads.first())
    }

    @Test
    fun `text rung yields the in-chapter progression of the match`() = runTest {
        val preview = "exactly here is where the reader stopped"
        val before = "a".repeat(300) + " "
        val chapter = before + preview + " " + "b".repeat(700)
        val executor = ReaderRestoreExecutor(FakeSpine(listOf("front matter", chapter)))

        val target = executor.executeStep(RestoreStep.Text(preview, seedChapter = 1)) as RestoreTarget.Spine

        assertEquals(1, target.index)
        val expected = before.length.toDouble() / chapter.length
        assertEquals(expected, target.progression!!, 0.001)
        assertNull("the text rung never asks to persist anything", target.persistAsHintForAudioMs)
    }

    @Test
    fun `text search strips a leading chapter heading before looking`() = runTest {
        // The stored preview carries the heading the web reader captured; the
        // parsed chapter text does not have it in that shape.
        val body = "the wind had turned in the night and the fleet"
        val spine = spineOf(6, mapOf(3 to body))
        val executor = ReaderRestoreExecutor(spine)

        val target = executor.executeStep(RestoreStep.Text("CHAPTER 28\n$body", seedChapter = 3))

        assertEquals(3, (target as RestoreTarget.Spine).index)
    }

    @Test
    fun `text search matches on the first sixty characters, case-insensitively, across newlines`() = runTest {
        val head = "It was the best of times, it was the worst of times, it was the age of wisdom"
        val spine = spineOf(4, mapOf(2 to head.lowercase()))
        val executor = ReaderRestoreExecutor(spine)

        val stored = head.replace(", ", ",\n") + " and this tail is not in the book at all"
        val target = executor.executeStep(RestoreStep.Text(stored, seedChapter = 2))

        assertEquals(2, (target as RestoreTarget.Spine).index)
    }

    @Test
    fun `a preview shorter than ten characters after cleaning is not searched`() = runTest {
        val spine = spineOf(6, mapOf(3 to "the end"))
        val executor = ReaderRestoreExecutor(spine)

        assertNull(executor.executeStep(RestoreStep.Text("CHAPTER 3 the end", seedChapter = 3)))
        assertTrue("nothing was read", spine.reads.isEmpty())
    }

    @Test
    fun `a text miss returns null and the ladder moves to the chapter rung`() = runTest {
        val spine = spineOf(6)
        val executor = ReaderRestoreExecutor(spine)

        val result = executor.resolve(
            listOf(RestoreStep.Text("this passage does not appear anywhere", 3), RestoreStep.Chapter(3)),
        )

        assertEquals(RestoreStep.Chapter(3), result.landed)
        assertEquals(RestoreTarget.Spine(3, null), result.target)
        assertEquals("every chapter was searched before giving up", (0 until 6).toSet(), spine.reads.toSet())
    }

    @Test
    fun `chapters that failed to parse are skipped, not treated as a miss for the whole book`() = runTest {
        val preview = "past the unparseable chapter lies the text"
        val executor = ReaderRestoreExecutor(FakeSpine(listOf("a", null, null, preview, "b")))

        val target = executor.executeStep(RestoreStep.Text(preview, seedChapter = 1))

        assertEquals(3, (target as RestoreTarget.Spine).index)
    }

    @Test
    fun `progression falls back to the first twenty characters when the full preview differs`() = runTest {
        val start = "the reader's copy says one thing"
        val chapter = "x".repeat(100) + " " + start + " but the stored preview goes on to say another thing entirely"
        val executor = ReaderRestoreExecutor(FakeSpine(listOf(chapter)))

        val progression = executor.findTextProgressionInChapter(0, "$start and the server's copy says something else")

        assertEquals(101.0 / chapter.length, progression!!, 0.001)
    }

    @Test
    fun `progression is null when not even the first twenty characters match`() = runTest {
        val executor = ReaderRestoreExecutor(FakeSpine(listOf("nothing of the sort in here")))
        assertNull(executor.findTextProgressionInChapter(0, "an entirely different sentence"))
    }

    // -------------------------------------------------------------- hint rung

    @Test
    fun `a decodable hint is handed back verbatim for the adapter to decode`() = runTest {
        val executor = ReaderRestoreExecutor(spineOf(3), hintDecodes = { it.contains("href") })
        val value = """{"href":"/ch3.xhtml","locations":{"progression":0.4}}"""

        val result = executor.resolve(listOf(RestoreStep.Hint(value), RestoreStep.Chapter(2)))

        assertEquals(RestoreTarget.Hint(value), result.target)
        assertEquals(RestoreStep.Hint(value), result.landed)
    }

    @Test
    fun `an undecodable hint falls through to the next rung`() = runTest {
        val executor = ReaderRestoreExecutor(spineOf(3), hintDecodes = { false })

        val result = executor.resolve(listOf(RestoreStep.Hint("{}"), RestoreStep.Chapter(2)))

        assertEquals(RestoreTarget.Spine(2, null), result.target)
        assertEquals(PositionSavePolicy.RestoreOutcome.Landed, result.outcome)
    }

    // ----------------------------------------------------------- chapter rung

    @Test
    fun `chapter rung is the top of the spine item and refuses an index past the end`() = runTest {
        val executor = ReaderRestoreExecutor(spineOf(4))
        assertEquals(RestoreTarget.Spine(0, null), executor.executeStep(RestoreStep.Chapter(0)))
        assertEquals(RestoreTarget.Spine(3, null), executor.executeStep(RestoreStep.Chapter(3)))
        assertNull(executor.executeStep(RestoreStep.Chapter(4)))
        assertNull(executor.executeStep(RestoreStep.Chapter(-1)))
    }

    // ----------------------------------------------------------- percent rung

    @Test
    fun `percent rung maps a book fraction onto the spine weighted by chapter length`() = runTest {
        // Lengths 100 / 300 / 600: 50% of 1000 chars is char 500, which is
        // 100 chars into the 600-char third chapter.
        val spine = FakeSpine(listOf("", "", ""), lengths = longArrayOf(100, 300, 600))
        val executor = ReaderRestoreExecutor(spine)

        val target = executor.executeStep(RestoreStep.Percent(50f)) as RestoreTarget.Spine

        assertEquals(2, target.index)
        assertEquals(100.0 / 600, target.progression!!, 1e-9)
    }

    @Test
    fun `percent rung clamps to the ends of the book`() = runTest {
        val spine = FakeSpine(listOf("", ""), lengths = longArrayOf(100, 100))
        val executor = ReaderRestoreExecutor(spine)

        assertEquals(RestoreTarget.Spine(0, 0.0), executor.targetForProgress(0.0))
        val end = executor.targetForProgress(1.0)!!
        assertEquals(1, end.index)
        assertEquals(0.99, end.progression!!, 1e-9)
        assertEquals("over-range input is clamped, not an exception", 1, executor.targetForProgress(5.0)!!.index)
    }

    @Test
    fun `percent rung with no chapter lengths given weights every chapter by its text length`() = runTest {
        // Default lengths come from the plain text: 10 / 30 chars, so 25% of
        // 40 chars is char 10 — the very start of the second chapter.
        val spine = FakeSpine(listOf("a".repeat(10), "b".repeat(30)))
        val executor = ReaderRestoreExecutor(spine)

        val target = executor.executeStep(RestoreStep.Percent(25f)) as RestoreTarget.Spine

        assertEquals(1, target.index)
        assertEquals(0.0, target.progression!!, 1e-9)
    }

    @Test
    fun `percent rung on an empty spine does not land`() = runTest {
        assertNull(ReaderRestoreExecutor(FakeSpine(emptyList())).executeStep(RestoreStep.Percent(40f)))
    }

    // ------------------------------------------------------------- audio rung

    @Test
    fun `audio rung resolves through the sync map and asks for the result to be persisted`() = runTest {
        val sentence = "the sentence the sync map names for this second"
        val spine = spineOf(10, mapOf(4 to sentence))
        var asked: Int? = null
        val executor = ReaderRestoreExecutor(spine, audio = AudioAnchorSource { ms -> asked = ms; 4 to sentence })

        val target = executor.executeStep(RestoreStep.Audio(54_373_980)) as RestoreTarget.Spine

        assertEquals(54_373_980, asked)
        assertEquals(4, target.index)
        assertTrue(target.progression!! > 0.0)
        assertEquals(54_373_980, target.persistAsHintForAudioMs)
    }

    @Test
    fun `audio rung falls back to the sync chapter when the text is not found in the book`() = runTest {
        val spine = spineOf(10)
        val executor = ReaderRestoreExecutor(spine, audio = AudioAnchorSource { 4 to "text the book does not contain anywhere" })

        val target = executor.executeStep(RestoreStep.Audio(1_000)) as RestoreTarget.Spine

        assertEquals(4, target.index)
        assertEquals("no match inside the chapter either — top of it", 0.0, target.progression!!, 0.0)
    }

    @Test
    fun `audio rung does not land when the sync map has no text for the position`() = runTest {
        // audioToEpubText answers Pair(0, "") when there is no sync map yet —
        // the case that used to leave the old positionEstablished flag false forever.
        val executor = ReaderRestoreExecutor(spineOf(10), audio = AudioAnchorSource { 0 to "" })
        assertNull(executor.executeStep(RestoreStep.Audio(1_000)))
    }

    @Test
    fun `audio rung is skipped outright for a standalone ebook`() = runTest {
        // No sync map and no audiobook (issue #169): the rung must not even
        // query, which would have looked up sync points for pair id 0.
        val executor = ReaderRestoreExecutor(spineOf(10), audio = null)
        assertNull(executor.executeStep(RestoreStep.Audio(1_000)))
    }

    // ---------------------------------------------------------------- errors

    @Test
    fun `an exception while executing a rung reports unresolved rather than propagating`() = runTest {
        val broken = object : SpineSource {
            override val spineCount = 3
            override suspend fun plainTextAt(index: Int): String = throw IllegalStateException("resource gone")
        }
        val result = ReaderRestoreExecutor(broken).resolve(listOf(RestoreStep.Text("a sentence long enough to search", 1)))

        assertNull(result.target)
        assertEquals(PositionSavePolicy.RestoreOutcome.Unresolved, result.outcome)
    }
}
