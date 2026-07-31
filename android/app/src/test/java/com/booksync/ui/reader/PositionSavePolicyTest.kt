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
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `Landed outcome yields FullSave`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Landed)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `Unresolved outcome yields LocalMetadataOnly`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `Unresolved transitions to Landed and the verdict upgrades to FullSave`() {
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave(atStartOfBook = false))

        policy.onRestoreOutcome(RestoreOutcome.Landed)
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
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
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `audio-only ladder, no sync map -- Unresolved -- LocalMetadataOnly until user page-turn -- FullSave after page-turn`() {
        val policy = PositionSavePolicy()
        // Audio-only restore ladder: repository.audioToEpubText returns
        // Pair(0, "") because there is no sync map yet, so every rung fails.
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave(atStartOfBook = true))

        // User turns a page — this view is now something they chose, not a
        // failed guess. The restore itself never resolved. The page-turn also
        // moves the reader off spine 0, so atStartOfBook flips to false.
        policy.onUserNavigation()
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `user navigation upgrades the verdict even before any restore outcome is recorded`() {
        val policy = PositionSavePolicy()
        policy.onUserNavigation()
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    // ---------- atStartOfBook safety net (Fix 1b) ----------

    @Test
    fun `unresolved + settle-emission-misread-as-navigation + still at start yields LocalMetadataOnly`() {
        // Readium's currentLocator StateFlow emits a second time right after
        // the initial (restored) locator is displayed — a "settle" emission
        // with a computed totalProgression/position, but no user input at
        // all. If that gets misread as onUserNavigation() (see ReaderActivity
        // echo-detection, which this policy input backs up), an Unresolved
        // restore must still refuse to FullSave as long as the displayed
        // position is still spine 0 / progression ~0 — writing that over a
        // real server position is exactly the chapter-0 data-loss bug.
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        policy.onUserNavigation() // the misread settle emission
        assertEquals(SaveVerdict.LocalMetadataOnly, policy.verdictForSave(atStartOfBook = true))
    }

    @Test
    fun `unresolved + real page-turn moved off start yields FullSave`() {
        // A genuine forward page-turn moves the reader off spine 0, so
        // atStartOfBook is false by the time this save runs — the safety net
        // only suppresses the false positive, it never blocks a real one.
        val policy = PositionSavePolicy()
        policy.onRestoreOutcome(RestoreOutcome.Unresolved)
        policy.onUserNavigation()
        assertEquals(SaveVerdict.FullSave, policy.verdictForSave(atStartOfBook = false))
    }

    @Test
    fun `atStartOfBook has no effect on Landed or Unread outcomes`() {
        val landed = PositionSavePolicy()
        landed.onRestoreOutcome(RestoreOutcome.Landed)
        assertEquals(SaveVerdict.FullSave, landed.verdictForSave(atStartOfBook = true))

        val unread = PositionSavePolicy()
        unread.onRestoreOutcome(RestoreOutcome.Unread)
        assertEquals(SaveVerdict.FullSave, unread.verdictForSave(atStartOfBook = true))
    }
}
