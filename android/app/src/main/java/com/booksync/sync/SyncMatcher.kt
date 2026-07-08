package com.booksync.sync

/**
 * Pure, dependency-free text helpers for sync matching.
 *
 * normalizeForSearch MUST stay identical to the server's _normalize_for_search
 * (server/routers/sync.py). Parity is enforced by SyncMatcherParityTest, which runs
 * this against the shared golden vectors in server/tests/fixtures/sync_parity/.
 *
 * Note: the full sync-point *matching* (getSyncPointForEpubText in BookSyncRepository)
 * has diverged from the server (it adds fuzzy bigram matching) and is intentionally
 * NOT parity-tested here.
 */
object SyncMatcher {

    /** Normalize text for comparison: lowercase, convert ALL whitespace to spaces, strip punctuation. */
    fun normalizeForSearch(text: String): String {
        return text.lowercase()
            // Convert newlines and tabs to spaces FIRST (before stripping non-alphanumeric)
            .replace('\n', ' ')
            .replace('\r', ' ')
            .replace('\t', ' ')
            // Convert Unicode whitespace variants to regular spaces
            .replace('\u00A0', ' ')  // non-breaking space (very common in epubs)
            .replace('\u2002', ' ')  // en space
            .replace('\u2003', ' ')  // em space
            .replace('\u2009', ' ')  // thin space
            .replace('\u200B', ' ')  // zero-width space
            .replace('\u202F', ' ')  // narrow no-break space
            .replace(Regex("[^a-z0-9 ]"), "") // Keep ONLY a-z, digits, regular space
            .replace(Regex(" +"), " ")        // Collapse multiple spaces
            .trim()
    }
}
