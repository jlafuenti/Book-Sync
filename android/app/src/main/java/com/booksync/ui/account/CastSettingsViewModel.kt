package com.booksync.ui.account

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.booksync.cast.CastPortManager
import com.booksync.cast.CastSessionMonitor
import com.booksync.player.CAST_PORT_RANGE
import com.booksync.player.CastServerAddress
import com.booksync.player.WifiAddress
import com.booksync.player.parseCastPort
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

/**
 * The Account screen's "Casting" section (issue #931): the port the phone
 * serves a Cast speaker from, and what the server is actually doing right now.
 *
 * Its own view model rather than more of [AccountViewModel], which is already
 * the largest on the screen and whose every test constructs it by hand.
 */
@HiltViewModel
class CastSettingsViewModel @Inject constructor(
    private val castPortManager: CastPortManager,
    castSessionMonitor: CastSessionMonitor,
    @param:ApplicationContext private val appContext: Context,
) : ViewModel() {

    /** The saved cast port. */
    val port: StateFlow<Int> = castPortManager.port
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), castPortManager.currentPort)

    /**
     * Where the LAN server is serving right now, or null between Cast sessions.
     * [CastServerAddress.usingFallbackPort] is how the screen knows the saved
     * port was busy.
     */
    val serverAddress: StateFlow<CastServerAddress?> = castSessionMonitor.serverAddress

    private val _portError = MutableStateFlow<String?>(null)

    /** Non-null while the last entry was refused; the field shows the saved port again. */
    val portError: StateFlow<String?> = _portError.asStateFlow()

    fun clearPortError() {
        _portError.value = null
    }

    /** The phone's Wi-Fi IPv4, or null with no Wi-Fi; the same walk the service uses. */
    fun wifiIp(): String? = WifiAddress.detectIpv4(appContext)

    /**
     * Save what was typed, or refuse it. Returns false -- with [portError] set --
     * when it is not a whole number in 1024..65535; the caller then puts the
     * saved port back in the field. Takes effect at the next Cast session.
     */
    fun commitPort(text: String): Boolean {
        val parsed = parseCastPort(text)
        if (parsed == null) {
            _portError.value = "Enter a port from ${CAST_PORT_RANGE.first} to ${CAST_PORT_RANGE.last}."
            return false
        }
        _portError.value = null
        viewModelScope.launch { castPortManager.setPort(parsed) }
        return true
    }
}
