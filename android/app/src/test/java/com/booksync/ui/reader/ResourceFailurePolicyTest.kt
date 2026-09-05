package com.booksync.ui.reader

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Issue #373. When a spine item fails to load, the reader used to be a dead
 * end: the toolbar and the page turns are both driven by Readium's injected
 * JavaScript, and a Chromium error page carries none of it, so tapping and
 * swiping did nothing and the system Back button was the only way out.
 *
 * [ResourceFailurePolicy] is the decision half of the escape hatch, lifted out
 * of [ReaderActivity] so it can be tested without a WebView: given a failing
 * href and what the UI currently looks like, it says whether to reveal the
 * bars, what to say, and whether "next chapter" is somewhere to go.
 */
class ResourceFailurePolicyTest {

    private val href = "/EPUB/Text/titlepage.xhtml"
    private val other = "/EPUB/Text/chapter1.xhtml"

    @Test
    fun `a failure with the bars hidden asks for them to be revealed`() {
        val action = ResourceFailurePolicy()
            .onResourceLoadFailed(href, barsVisible = false, hasNextResource = true)
        assertNotNull(action)
        assertTrue(action!!.revealBars)
    }

    @Test
    fun `a failure with the bars already up does not ask for them again`() {
        val action = ResourceFailurePolicy()
            .onResourceLoadFailed(href, barsVisible = true, hasNextResource = true)
        assertNotNull(action)
        assertEquals(false, action!!.revealBars)
    }

    @Test
    fun `the message is the user-facing one`() {
        val action = ResourceFailurePolicy()
            .onResourceLoadFailed(href, barsVisible = false, hasNextResource = true)
        assertEquals("This page could not be displayed", action!!.message)
        assertEquals(RESOURCE_FAILURE_MESSAGE, action.message)
    }

    @Test
    fun `a next resource is offered as Next chapter`() {
        val action = ResourceFailurePolicy()
            .onResourceLoadFailed(href, barsVisible = false, hasNextResource = true)
        assertEquals("Next chapter", action!!.nextChapterLabel)
        assertEquals(RESOURCE_FAILURE_ACTION_LABEL, action.nextChapterLabel)
    }

    @Test
    fun `the last resource in the book offers no next chapter`() {
        val action = ResourceFailurePolicy()
            .onResourceLoadFailed(href, barsVisible = false, hasNextResource = false)
        assertNotNull(action)
        assertNull(action!!.nextChapterLabel)
        // The bars still come up: leaving the book is the way out.
        assertTrue(action.revealBars)
    }

    @Test
    fun `the same failing resource is only reported once`() {
        val policy = ResourceFailurePolicy()
        assertNotNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
        assertNull(policy.onResourceLoadFailed(href, barsVisible = true, hasNextResource = true))
        assertNull(policy.onResourceLoadFailed(href, barsVisible = true, hasNextResource = true))
    }

    @Test
    fun `a different failing resource is reported again`() {
        val policy = ResourceFailurePolicy()
        assertNotNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
        val second = policy.onResourceLoadFailed(other, barsVisible = true, hasNextResource = true)
        assertNotNull(second)
        assertEquals(false, second!!.revealBars)
    }

    @Test
    fun `a resource that later displays clears the suppression`() {
        val policy = ResourceFailurePolicy()
        assertNotNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
        assertNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
        policy.onResourceDisplayed()
        assertNotNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
    }

    @Test
    fun `displaying a resource before any failure changes nothing`() {
        val policy = ResourceFailurePolicy()
        policy.onResourceDisplayed()
        assertNotNull(policy.onResourceLoadFailed(href, barsVisible = false, hasNextResource = true))
    }

    @Test
    fun `hasNextResource is computed from the spine position`() {
        assertTrue(hasNextResource(spineIndex = 0, spineSize = 3))
        assertTrue(hasNextResource(spineIndex = 1, spineSize = 3))
        assertEquals(false, hasNextResource(spineIndex = 2, spineSize = 3))
        // An unknown position (Readium reported an href not in the spine) is
        // treated as "there is somewhere to go" only when the book has more
        // than one resource — moving forward is still better than a dead page.
        assertTrue(hasNextResource(spineIndex = -1, spineSize = 3))
        assertEquals(false, hasNextResource(spineIndex = -1, spineSize = 1))
        assertEquals(false, hasNextResource(spineIndex = 0, spineSize = 0))
    }
}
