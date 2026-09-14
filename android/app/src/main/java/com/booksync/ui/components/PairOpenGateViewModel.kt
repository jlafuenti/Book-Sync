package com.booksync.ui.components

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.data.auth.hasMinRole
import com.booksync.data.auth.userFacingError
import com.booksync.data.remote.TokenManager
import com.booksync.data.remote.dto.TranscriptionStatus
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * A pair open/switch that is being held back by [TranscriptionStatusDialog]
 * (issue #536): [status] is what to show, [continueLabel] is the confirm
 * button's label ("Open anyway" / "Switch anyway"), and [proceed] is the
 * navigation this open would otherwise have run immediately.
 */
data class PendingOpen(
    val pairId: Int,
    val status: TranscriptionStatus,
    val continueLabel: String,
    val proceed: () -> Unit,
)

/**
 * The state machine behind the "not synced yet" dialog (issue #536).
 *
 * Every pair-opening lambda in `BookSyncNavigation` and the player's
 * "Switch to Reader" button call [requestOpen] instead of navigating
 * directly. A ready pair ([TranscriptionRepository.readiness] returns null)
 * proceeds at once with no dialog; anything else is held in [pending] until
 * the user confirms ([continueAnyway]), asks to transcribe ([transcribe]),
 * or backs out ([dismiss]).
 *
 * One instance is shared across the whole nav host (obtained on the
 * activity's `ViewModelStoreOwner`) so every open/switch surface sees the
 * same [pending] dialog and [message] snackbar.
 */
@HiltViewModel
class PairOpenGateViewModel @Inject constructor(
    private val transcriptionRepository: TranscriptionRepository,
    networkMonitor: NetworkMonitor,
    tokenManager: TokenManager,
) : ViewModel() {

    /** Editor and above (matching the server's gate on the transcription start endpoint). */
    val canTranscribe: StateFlow<Boolean> =
        tokenManager.getRole()
            .map { hasMinRole(it, "editor") }
            .stateIn(viewModelScope, SharingStarted.Eagerly, false)

    val isOnline: StateFlow<Boolean> = networkMonitor.isOnline

    private val _pending = MutableStateFlow<PendingOpen?>(null)
    val pending: StateFlow<PendingOpen?> = _pending.asStateFlow()

    /** One-shot snackbar/toast text — see [clearMessage]. */
    private val _message = MutableStateFlow<String?>(null)
    val message: StateFlow<String?> = _message.asStateFlow()
    fun clearMessage() { _message.value = null }

    /**
     * Ask to open/switch to [pairId]. Runs [proceed] immediately when the pair
     * is ready; otherwise publishes a [PendingOpen] carrying [continueLabel]
     * and holds [proceed] until [continueAnyway] runs it.
     */
    fun requestOpen(pairId: Int, continueLabel: String, proceed: () -> Unit) {
        viewModelScope.launch {
            val status = transcriptionRepository.readiness(pairId)
            if (status == null) {
                proceed()
            } else {
                _pending.value = PendingOpen(pairId, status, continueLabel, proceed)
            }
        }
    }

    /** "Open anyway" / "Switch anyway" — runs the held navigation exactly once. */
    fun continueAnyway() {
        val current = _pending.value ?: return
        _pending.value = null
        current.proceed()
    }

    /** Outside tap / back — stay put, run nothing. */
    fun dismiss() {
        _pending.value = null
    }

    /**
     * "Transcribe" / "Try again". Queues [PendingOpen.pairId] and re-reads
     * readiness so the dialog reflects the pair's new state (typically
     * [TranscriptionStatus.Queued]) without another round trip through
     * [requestOpen]. On failure the pending dialog is left exactly as it was.
     */
    fun transcribe() {
        val current = _pending.value ?: return
        viewModelScope.launch {
            transcriptionRepository.addToQueue(current.pairId)
                .onSuccess {
                    _message.value = "Added to transcription queue"
                    val refreshed = transcriptionRepository.readiness(current.pairId)
                    _pending.value = refreshed?.let { current.copy(status = it) }
                }
                .onFailure { e -> _message.value = userFacingError(e) }
        }
    }
}
