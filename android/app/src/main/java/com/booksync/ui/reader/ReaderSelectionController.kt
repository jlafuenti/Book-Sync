package com.booksync.ui.reader

import android.app.Activity
import android.content.Context
import android.graphics.Rect
import android.util.Log
import android.view.ActionMode
import android.view.Menu
import android.view.MenuItem
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import com.booksync.R

private const val TAG = "ReaderSelection"

/**
 * The reader's text-selection toolbar plumbing (issue #227), lifted out of
 * `ReaderActivity` so the WebView menu surgery is not interleaved with the
 * sync logic. The decisions it acts on live in `ReaderSelection.kt` and are
 * unit-tested; this class is the Android glue around them.
 *
 * The floating selection toolbar (Copy / Share / Select all / etc.) is
 * driven by an `ActionMode.Callback` that the WebView starts when the user
 * long-presses text. To strip noise items and inject our own BEFORE the
 * toolbar takes its menu snapshot, we have to intercept the *creation* of
 * the ActionMode — not mutate the menu after.
 *
 * System Chrome WebView does NOT route this through
 * `Window.Callback.onWindowStartingActionMode`; tested empirically and
 * also documented behavior. Instead, the WebView calls
 * `View.startActionMode(callback, TYPE_FLOATING)`, which walks up via
 * `ViewParent.startActionModeForChild(...)`. Each ancestor ViewGroup gets
 * a chance to intercept. So we install a custom intercepting FrameLayout
 * between the WebView and its current parent at runtime — see [install].
 *
 * [onActionModeStarted] is also kept as a defensive fallback: it adds
 * Define / Sync-to-Audio post-hoc so they're at least available even if
 * the interceptor wasn't installed (e.g. WebView was recreated, etc.).
 * Mutate-after-snapshot can't refresh the rendered toolbar, so the noise
 * strip path lives only inside the wrapper. See plan in
 * `.claude/plans/playful-painting-salamander.md`.
 *
 * Until issue #582 this class also tracked *what* was selected: it injected
 * JavaScript into a WebView found by walking the view tree and cached the
 * result in `lastSelectedText` for a later menu click to read. Readium keeps
 * three chapter WebViews alive (previous/current/next) and the walk always
 * returned the first one, which after any adjacent-chapter move is the
 * chapter the reader just left — so Define and Sync to Audio read an empty
 * or stale selection instead of the one on screen. That tracking is gone:
 * [Host.onDefine] and [Host.onSyncToAudio] take no text and are expected to
 * ask Readium's navigator for the current selection themselves, at the
 * moment the menu item is clicked — see `ReaderActivity.defineSelectedWord`.
 */
class ReaderSelectionController(
    private val activity: Activity,
    private val host: Host,
) {

    /** What the activity provides: the two selection actions. */
    interface Host {
        /** Whether "Sync to Audio" has anything to scrub to (a downloaded paired audiobook). */
        val syncToAudioAvailable: Boolean

        /**
         * [dismiss] ends the ActionMode ([ActionMode.finish]) — call it once
         * the navigator's current selection has been read, not before.
         * Finishing the ActionMode tears down the WebView's native text
         * selection, and `currentSelection()` is asynchronous (it awaits a
         * JavaScript round trip); calling `mode.finish()` synchronously
         * alongside that call, the way the old cached-text design did, races
         * the read against the teardown and can hand back an empty
         * selection even though the user's highlight was read correctly a
         * moment before.
         */
        fun onDefine(dismiss: () -> Unit)
        fun onSyncToAudio(dismiss: () -> Unit)
    }

    private var hasInstalledInterceptor: Boolean = false

    /**
     * Install ONE [SelectionInterceptingFrameLayout] around the activity's
     * content root, so we intercept TYPE_FLOATING ActionMode creation via
     * `startActionModeForChild`.
     *
     * Key fact: `startActionModeForChild` PROPAGATES UP the whole view
     * hierarchy (each ViewGroup delegates to its parent until the DecorView
     * creates the FloatingActionMode). So we don't need to wrap each of
     * Readium's per-page WebViews — a single wrapper around the activity
     * content root sees every selection from every WebView, including pages
     * created later by the pager. The content root exists from setContentView
     * and is never recreated. Idempotent; onResume() re-calls as a no-op.
     */
    fun install() {
        if (hasInstalledInterceptor) return
        val content = activity.findViewById<ViewGroup>(android.R.id.content) ?: return
        val root = content.getChildAt(0) ?: return
        if (root is SelectionInterceptingFrameLayout) {
            hasInstalledInterceptor = true
            return
        }
        val params = root.layoutParams
        content.removeView(root)
        val interceptor = SelectionInterceptingFrameLayout(activity).apply {
            // Don't consume touches ourselves.
            isClickable = false
            isFocusable = false
            addView(
                root,
                FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                ),
            )
        }
        content.addView(interceptor, params)
        hasInstalledInterceptor = true
        Log.d(TAG, "Selection interceptor installed at activity content root")
    }

    /**
     * Defensive fallback for `Activity.onActionModeStarted`: if the
     * interceptor isn't installed (e.g. WebView lifecycle edge case, or
     * device WebView routes through a different code path), this still
     * injects our custom items so they're at least reachable. We don't
     * bother trimming here because mutate-after-snapshot doesn't refresh
     * the rendered toolbar.
     */
    fun onActionModeStarted(mode: ActionMode?) {
        if (mode == null) return
        val menu = mode.menu ?: return
        trimSelectionMenu(menu)
        injectCustomItems(mode, menu)
        // Re-render the floating toolbar so our injected items are visible.
        // Without this, items added after the initial onCreateActionMode snapshot
        // are silently ignored by the FloatingToolbar.
        mode.invalidate()
    }

    /**
     * Custom ViewGroup ancestor of the WebView. Chrome WebView calls
     * `parent.startActionModeForChild(view, callback, TYPE_FLOATING)` when a
     * text selection happens. By overriding here, we get to wrap the callback
     * BEFORE the ActionMode is created and BEFORE the FloatingToolbar takes
     * its menu snapshot — which is the only point at which menu mutations
     * actually affect the rendered toolbar.
     */
    private inner class SelectionInterceptingFrameLayout(context: Context) : FrameLayout(context) {

        override fun startActionModeForChild(
            originalView: View,
            callback: ActionMode.Callback,
            type: Int,
        ): ActionMode? {
            if (type == ActionMode.TYPE_FLOATING) {
                Log.d(TAG, "Intercepting startActionModeForChild type=$type (selection toolbar)")
                return super.startActionModeForChild(
                    originalView,
                    SelectionCallbackWrapper(callback),
                    type,
                )
            }
            return super.startActionModeForChild(originalView, callback, type)
        }

        // Older overload — WebView always passes a type on API 23+, so this
        // typically isn't hit, but override defensively.
        override fun startActionModeForChild(
            originalView: View,
            callback: ActionMode.Callback,
        ): ActionMode? {
            return super.startActionModeForChild(originalView, callback)
        }
    }

    /**
     * Wraps the WebView's selection ActionMode callback so we can:
     *   - Strip Share / Select All / Translate / Web Search from the Menu
     *     before the floating toolbar ever snapshots it.
     *   - Re-strip on `onPrepareActionMode` for Android 14+ async text-classifier
     *     items (those arrive later via a second prepare pass).
     *   - Inject our own "Define" + "Sync to Audio" items.
     *   - Delegate Copy / positioning / destroy to the original callback.
     *
     * Must extend `Callback2`, not the plain `Callback` interface, so
     * `onGetContentRect` is forwarded — otherwise the toolbar mis-positions
     * away from the selection.
     */
    private inner class SelectionCallbackWrapper(
        private val delegate: ActionMode.Callback,
    ) : ActionMode.Callback2() {

        override fun onCreateActionMode(mode: ActionMode, menu: Menu): Boolean {
            // Let the WebView populate first so we can edit the result.
            val keep = delegate.onCreateActionMode(mode, menu)
            trimSelectionMenu(menu)
            injectCustomItems(mode, menu)
            return keep || true
        }

        override fun onPrepareActionMode(mode: ActionMode, menu: Menu): Boolean {
            // Async TextClassifier items (API 29+) come in via a follow-up prepare
            // cycle. Re-strip + re-inject so the rendered toolbar stays clean.
            delegate.onPrepareActionMode(mode, menu)
            trimSelectionMenu(menu)
            injectCustomItems(mode, menu)
            return true
        }

        override fun onActionItemClicked(mode: ActionMode, item: MenuItem): Boolean {
            // Our injected items consume their clicks via setOnMenuItemClickListener,
            // so this only fires for Copy / Read Aloud / etc. — delegate as-is.
            return delegate.onActionItemClicked(mode, item)
        }

        override fun onDestroyActionMode(mode: ActionMode) {
            delegate.onDestroyActionMode(mode)
        }

        override fun onGetContentRect(mode: ActionMode, view: View?, outRect: Rect) {
            // Forward when possible so the toolbar anchors to the selection.
            // Falling back to super positions the toolbar at the view origin,
            // which looks broken — but better than crashing.
            if (delegate is ActionMode.Callback2) {
                delegate.onGetContentRect(mode, view, outRect)
            } else {
                super.onGetContentRect(mode, view, outRect)
            }
        }
    }

    /**
     * Insert the reader's two custom selection actions: Define (order 0,
     * leftmost) and Sync to Audio (order 1). Idempotent via `findItem` —
     * safe to call from both `onCreateActionMode` and `onPrepareActionMode`,
     * and from the [onActionModeStarted] fallback.
     *
     * Neither action is handed any selected text here (issue #582): both ask
     * Readium's navigator for the current selection themselves, at click
     * time, so they always read the resource actually on screen. Each is
     * given a `dismiss` callback rather than having this method call
     * `mode.finish()` itself — see [Host.onDefine].
     *
     * Sync to Audio is only injected when there is a downloaded paired
     * audiobook, since it has nothing to scrub to otherwise.
     */
    private fun injectCustomItems(mode: ActionMode, menu: Menu) {
        if (menu.findItem(R.id.action_define) == null) {
            menu.add(0, R.id.action_define, 0, "Define").setOnMenuItemClickListener {
                host.onDefine { mode.finish() }
                true
            }
            Log.d(TAG, "Added 'Define' to ActionMode menu")
        }
        if (host.syncToAudioAvailable && menu.findItem(R.id.action_sync_selection) == null) {
            menu.add(0, R.id.action_sync_selection, 1, "Sync to Audio").setOnMenuItemClickListener {
                host.onSyncToAudio { mode.finish() }
                true
            }
            Log.d(TAG, "Added 'Sync to Audio' to ActionMode menu")
        }
    }

    /** Remove the noise items [selectionNoiseItemIds] names from the floating toolbar. */
    private fun trimSelectionMenu(menu: Menu?) {
        menu ?: return
        val items = (0 until menu.size()).mapNotNull { i ->
            val item = menu.getItem(i) ?: return@mapNotNull null
            Log.v(TAG, "Selection menu item: id=0x${item.itemId.toString(16)} title='${item.title}'")
            SelectionMenuItem(item.itemId, item.title?.toString())
        }
        val itemsToRemove = selectionNoiseItemIds(items)
        itemsToRemove.forEach { menu.removeItem(it) }
        if (itemsToRemove.isNotEmpty()) {
            Log.d(TAG, "Stripped ${itemsToRemove.size} noise item(s) from selection toolbar")
        }
    }
}
