package com.booksync.ui.reader

/** What the snackbar says when a spine item will not load. */
const val RESOURCE_FAILURE_MESSAGE = "This page could not be displayed"

/** The snackbar's only control: skip past the resource that failed. */
const val RESOURCE_FAILURE_ACTION_LABEL = "Next chapter"

/**
 * The reader's response to one failed resource load — issue #373.
 *
 * @property revealBars true when the caller must show the top and bottom bars.
 *   An error page runs none of Readium's injected JavaScript, so the tap
 *   listener that normally toggles them never fires; without this the user has
 *   no toolbar, no display settings and no way back except the system Back
 *   button.
 * @property message what to put in the snackbar.
 * @property nextChapterLabel the label for the skip-forward action, or null
 *   when there is no next resource to skip to.
 */
data class ResourceFailureAction(
    val revealBars: Boolean,
    val message: String,
    val nextChapterLabel: String?,
)

/**
 * Decides what to do when Readium reports a resource it could not load
 * (`Navigator.Listener.onResourceLoadFailed`) — issue #373.
 *
 * Lifted out of [ReaderActivity], the way [PositionSavePolicy] and
 * [DuplicatePositionFilter] were, so the decision can be tested on the JVM:
 * the activity owns the views and the navigator, this owns the "have we
 * already told the user about this one" state.
 *
 * The suppression matters because the WebView re-requests a failing resource
 * (reload, adjacent-page preloading, a rotation re-layout), and a snackbar per
 * request would be its own kind of unusable.
 */
class ResourceFailurePolicy {

    private var reportedHref: String? = null

    /**
     * Records a failure and returns what the UI should do, or null when this
     * exact resource was already reported and nothing has been displayed
     * since.
     */
    fun onResourceLoadFailed(
        href: String,
        barsVisible: Boolean,
        hasNextResource: Boolean,
    ): ResourceFailureAction? {
        if (reportedHref == href) return null
        reportedHref = href
        return ResourceFailureAction(
            revealBars = !barsVisible,
            message = RESOURCE_FAILURE_MESSAGE,
            nextChapterLabel = RESOURCE_FAILURE_ACTION_LABEL.takeIf { hasNextResource },
        )
    }

    /**
     * Called when a resource actually rendered. Clears the suppression so that
     * a later failure of the same href — the user navigated away and came back
     * — is reported again rather than silently swallowed.
     */
    fun onResourceDisplayed() {
        reportedHref = null
    }
}

/**
 * Whether there is a resource after [spineIndex] to skip forward to.
 *
 * [spineIndex] is -1 when the failing href is not in the reading order at all
 * (a resource referenced only from a link, say). That is still worth offering
 * a way out of, as long as the book has more than the one resource.
 */
fun hasNextResource(spineIndex: Int, spineSize: Int): Boolean =
    if (spineIndex < 0) spineSize > 1 else spineIndex < spineSize - 1
