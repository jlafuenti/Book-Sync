package com.booksync.data.remote

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Whether the server is refusing this session until the password is changed
 * (issue #209).
 *
 * Two things raise it: `getMe()` reporting `must_reset_password`, and any request
 * coming back `403 password_reset_required` — the second matters because an admin
 * can reset a password while the app is running, and without it every screen
 * would simply start failing with no explanation.
 *
 * Deliberately in-memory rather than DataStore-backed. It is re-derived from the
 * server on every launch, and a stale persisted `true` would strand the app on a
 * reset screen the server no longer requires — with no way back, since the reset
 * screen is the one thing the gate does not block.
 *
 * `AuthInterceptor` raises it from an OkHttp thread; navigation collects it. That
 * indirection exists because an interceptor has no access to a NavController —
 * the same reason the token-clear observer in `BookSyncNavigation` watches a flow
 * instead of being called directly.
 */
@Singleton
class PasswordResetGate @Inject constructor() {
    private val _required = MutableStateFlow(false)
    val required: StateFlow<Boolean> = _required.asStateFlow()

    fun raise() {
        _required.value = true
    }

    /** Called once the password has actually been changed. */
    fun clear() {
        _required.value = false
    }
}
