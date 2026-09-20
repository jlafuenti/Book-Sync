package com.booksync.ui

import androidx.navigation.NavController

/**
 * Whether a "this screen closed" callback for [route] should still pop the back stack.
 *
 * The reader is a second Activity, so its close reaches the nav host late, as an activity
 * result delivered when `MainActivity` resumes. The walkthrough can pop the reader's route
 * on its own before that (`TourNav.PopToMain`, leaving the reader/player block), and an
 * unconditional `popBackStack()` from the late callback then removed `MAIN` — the only entry
 * left — and with it every screen: a blank app that quitting the tour did not bring back
 * (issue #642 follow-up, found on the emulator). Only the route that is still on top may
 * pop itself.
 */
internal fun shouldPopRoute(route: String, currentRoute: String?): Boolean = currentRoute == route

/** [NavController.popBackStack], but only while [route] is the current destination. */
internal fun NavController.popBackStackIfCurrent(route: String) {
    if (shouldPopRoute(route, currentDestination?.route)) popBackStack()
}
