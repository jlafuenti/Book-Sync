package com.booksync.auto

import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The Flow filter behind Android Auto's browse-node change notifications
 * (issue #583): "emit a notify on distinct change, skip the initial and
 * duplicate emissions."
 *
 * `AudioPlayerService` is excluded from Kover (it needs a `MediaLibrarySession`
 * and a car head unit — see `AutoWiringTest`), so this logic has to live where a
 * JVM test can hold it, same as the rest of `com.booksync.auto`.
 */
class AutoChangeNotifierTest {

    @Test
    fun `a single emission is the initial load and is not a change`() = runTest {
        assertEquals(emptyList<Int>(), flowOf(1).changesToNotify().toList())
    }

    @Test
    fun `an empty flow notifies nothing`() = runTest {
        assertEquals(emptyList<Int>(), flowOf<Int>().changesToNotify().toList())
    }

    @Test
    fun `a genuinely different second emission is a change`() = runTest {
        assertEquals(listOf(2), flowOf(1, 2).changesToNotify().toList())
    }

    @Test
    fun `a re-emission of the same value is not a change`() = runTest {
        // Room re-runs a query on any write to a watched table, including one
        // that leaves the result identical — that must not fire a notify.
        assertEquals(emptyList<Int>(), flowOf(1, 1, 1).changesToNotify().toList())
    }

    @Test
    fun `duplicates collapse but every genuine change after the first still fires`() = runTest {
        assertEquals(listOf(2, 3), flowOf(1, 1, 2, 2, 3).changesToNotify().toList())
    }

    @Test
    fun `a value returning to an earlier one still counts as a change`() = runTest {
        // A, B, A: once the initial A is dropped, both B and the later A are
        // real transitions — this is not "back where we started".
        assertEquals(listOf("B", "A"), flowOf("A", "B", "A").changesToNotify().toList())
    }
}
