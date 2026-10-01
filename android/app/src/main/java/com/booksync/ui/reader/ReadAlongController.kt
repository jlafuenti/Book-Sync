package com.booksync.ui.reader

import com.booksync.data.local.entity.SyncPointEntity
import com.booksync.sync.SyncMatcher

/**
 * Where a read-along start puts the audio (issue #772). One sealed type so the
 * three entry points (the player's handoff, the toolbar toggle, a text
 * selection) share a single start path in `ReaderActivity.startFollowing`.
 */
sealed interface FollowStart {
    /** Follow from wherever the audio already is (the player's Read along handoff). */
    object KeepAudio : FollowStart

    /** Seek to the sentence on the visible page first (the toolbar's Follow audio). */
    object VisiblePage : FollowStart

    /** Seek to [ms] first (the sentence a selection matched). */
    data class AudioMs(val ms: Int) : FollowStart
}

/**
 * How the reader gets the audio to a read-along start (issue #772).
 *
 * A seek only works on an item the session has already loaded. When the
 * reader has to load the audiobook itself (the audio service was not running),
 * the session resolves the item asynchronously and picks its own start
 * position; a seek issued in between is lost, and with no saved position the
 * book starts at zero. So the start position has to travel with the item.
 */
sealed interface AudioStartPlan {
    /** The item is loaded: seek to [seekMs], or leave the audio where it is when null. */
    data class Seek(val seekMs: Long?) : AudioStartPlan

    /** The item must be loaded at [startMs]; null lets the service resume where the book was left. */
    data class Load(val startMs: Long?) : AudioStartPlan
}

/**
 * [targetMs] is the sentence the start is anchored on (0 when there is none),
 * [savedMs] the pair's saved audio position. A target always wins; an item
 * that must be loaded without one starts at the saved position rather than
 * at the top of the book.
 */
fun planAudioStart(itemLoaded: Boolean, targetMs: Int, savedMs: Int?): AudioStartPlan {
    val target = targetMs.takeIf { it > 0 }?.toLong()
    if (itemLoaded) return AudioStartPlan.Seek(target)
    return AudioStartPlan.Load(target ?: savedMs?.takeIf { it > 0 }?.toLong())
}

/**
 * Read-along's decision logic (issue #762), kept free of Android so it is
 * JVM-testable; [ReaderActivity] owns the MediaController, the poll and the
 * Readium calls and feeds this class.
 *
 * Audio drives the page: every poll tick hands [onAudioPosition] the
 * playback position, which resolves to a sync point through the shared
 * matcher (no new matching rule — parity fixtures untouched). A change of
 * sentence yields a [Action.Decorate]; the activity then asks the page
 * whether that sentence is on screen and reports back through
 * [onSentenceVisibility], which yields a [Action.Jump] only when it is not.
 *
 * Readium echoes every programmatic `go` as a `currentLocator` emission with
 * no user input behind it. The reader's usual echo test compares the
 * emission against a target progression, but a text-anchored jump lands at
 * a progression nobody knows in advance, so here an emission within
 * [jumpEchoWindowMs] of our own jump is the echo. Anything later is only a
 * *suspect*: Readium also emits late settle locators with nobody touching
 * the page (seen on a slow device, well after the open), so the activity
 * waits for the page to settle, checks whether the current sentence is
 * still on screen and reports through [onSuspectVerified]: gone means a
 * manual turn, which pauses following without touching playback
 * ([State.Paused]); back on screen while paused means the user returned,
 * and following resumes. [onBackToAudio] resumes from anywhere else.
 */
class ReadAlongController(
    private val points: List<SyncPointEntity>,
    private val jumpEchoWindowMs: Long = 1500L,
) {
    enum class State { Off, Following, Paused }

    sealed interface Action {
        data class Decorate(val point: SyncPointEntity) : Action
        data class Jump(val point: SyncPointEntity) : Action
    }

    enum class LocatorVerdict { Ignored, Echo, Suspect }

    var state: State = State.Off
        private set
    val isFollowing: Boolean get() = state == State.Following
    val isPaused: Boolean get() = state == State.Paused

    var currentPoint: SyncPointEntity? = null
        private set
    private var lastJumpAtMs: Long = Long.MIN_VALUE / 2

    fun start(nowMs: Long) {
        state = State.Following
        currentPoint = null
        lastJumpAtMs = Long.MIN_VALUE / 2
    }

    fun stop() {
        state = State.Off
        currentPoint = null
        lastJumpAtMs = Long.MIN_VALUE / 2
    }

    fun onAudioPosition(audioMs: Int, nowMs: Long): Action.Decorate? {
        if (state == State.Off) return null
        val point = SyncMatcher.pointForAudioPosition(points, audioMs) { it.audioStartMs } ?: return null
        val changed = !sameSentence(point, currentPoint)
        currentPoint = point
        return if (changed && state == State.Following) Action.Decorate(point) else null
    }

    fun onSentenceVisibility(point: SyncPointEntity, visible: Boolean, nowMs: Long): Action.Jump? {
        if (state != State.Following || visible || !sameSentence(point, currentPoint)) return null
        lastJumpAtMs = nowMs
        return Action.Jump(point)
    }

    fun onLocatorEmitted(nowMs: Long): LocatorVerdict {
        if (state == State.Off) return LocatorVerdict.Ignored
        if (nowMs - lastJumpAtMs <= jumpEchoWindowMs) return LocatorVerdict.Echo
        return LocatorVerdict.Suspect
    }

    /**
     * The page's answer to a [LocatorVerdict.Suspect], once it has settled:
     * following with the sentence gone means the user turned the page
     * ([State.Paused]); paused with the sentence back on screen means they
     * returned to it, so following resumes by itself. Returns true when the
     * state changed.
     */
    fun onSuspectVerified(visible: Boolean): Boolean {
        val next = when {
            state == State.Following && !visible -> State.Paused
            state == State.Paused && visible -> State.Following
            else -> return false
        }
        state = next
        return true
    }

    fun onBackToAudio(nowMs: Long): List<Action> {
        val point = currentPoint
        if (state != State.Paused || point == null) return emptyList()
        state = State.Following
        lastJumpAtMs = nowMs
        return listOf(Action.Decorate(point), Action.Jump(point))
    }

    private fun sameSentence(a: SyncPointEntity?, b: SyncPointEntity?): Boolean =
        a != null && b != null &&
            a.epubChapter == b.epubChapter && a.epubSentenceIndex == b.epubSentenceIndex

    companion object {
        /**
         * The text to look for on the page for a sync point. A preview can
         * span two paragraphs (the server's tokenizer joins a dangling
         * fragment like `“His mother …”` to the next sentence), and a quote
         * across a block boundary never matches the page text, so only the
         * first line is used. Null when there is nothing to quote.
         */
        fun quoteFor(preview: String?): String? =
            preview?.lineSequence()?.map { it.trim() }?.firstOrNull { it.isNotEmpty() }
    }
}
