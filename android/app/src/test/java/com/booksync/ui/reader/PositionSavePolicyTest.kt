package com.booksync.ui.reader

import com.booksync.ui.reader.PositionSavePolicy.RestoreOutcome
import com.booksync.ui.reader.PositionSavePolicy.SaveVerdict
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * [PositionSavePolicy] replaces [ReaderActivity]'s old `positionEstablished`
 * boolean latch (issue #61/#40). That flag had two defects: an unresolved
 * restore left it false forever (suppressing every later save, including
 * ones after real user page-turns), and the catch block in
 * `getInitialLocator` reset it to false even after an earlier rung had
 * already landed. These tests pin the replacement's monotonic contract.
 */
class PositionSavePolicyTest {

    @Test
    fun `Unread outcome yields FullSave`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unread)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }

    @Test
    fun `Landed outcome yields FullSave`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Landed)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }

    @Test
    fun `Unresolved outcome yields LocalMetadataOnly`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave())
    }

    @Test
    fun `Unresolved transitions to Landed and the verdict upgrades to FullSave`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave())

        policy.onRestoreOutcome(RestoreOutcome.Landed)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }

    @Test
    fun `Landed can never be demoted back to Unresolved`() {
        // This is exactly the bug in getInitialLocator's old catch block: an
        // exception thrown AFTER a rung had already landed and set the flag
        // true used to reset it to false, re-suppressing saves for a session
        // that actually had a good restore.
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Landed)
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }

    @Test
    fun `audio-only ladder, no sync map -- Unresolved -- LocalMetadataOnly until user page-turn -- FullSave after page-turn`() {
        val policy = PositionSavePolicy()
        // Audio-only restore ladder: repository.audioToEpubText returns
        // Pair(0, "") because there is no sync map yet, so every rung fails.
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave())

        // User turns a page — this view is now something they chose, not a
        // failed guess. The restore itself never resolved.
        policy.onUserNavigation()
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }

    @Test
    fun `user navigation upgrades the verdict even before any restore outcome is recorded`() {
        val policy = PositionSavePolicy()
        policy.onUserNavigation()
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave())
    }
}
