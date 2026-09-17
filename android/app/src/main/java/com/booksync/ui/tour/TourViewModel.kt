package com.booksync.ui.tour

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import javax.inject.Inject

/**
 * Thin Compose-facing holder around the app-scoped [TourController] (issue
 * #597 §3) — the reader is a second Activity, so the controller itself can't
 * live in a MainActivity-scoped ViewModel, but a ViewModel is still the
 * normal way for `BookSyncNavigation` and `AccountScreen` to reach it via
 * `hiltViewModel(context as ComponentActivity)`.
 */
@HiltViewModel
class TourViewModel @Inject constructor(
    val controller: TourController,
    private val prefs: TourPrefs,
) : ViewModel() {

    /** Whether the first-sign-in "Take the tour?" dialog has already been shown. */
    val offered: StateFlow<Boolean> =
        prefs.offered.stateIn(viewModelScope, SharingStarted.Eagerly, false)

    /** Records the first-sign-in dialog as answered, either way — never shown again after. */
    fun markOffered() {
        viewModelScope.launch { prefs.markOffered() }
    }
}
