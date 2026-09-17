package com.booksync.ui.tour

/**
 * Whether to show "Take the 5-minute tour?" right now (issue #597 §5). Pure,
 * so the composable that hosts the dialog is a one-liner: offer once, after a
 * real sign-in, and never again once answered either way.
 */
fun shouldOfferTour(offered: Boolean, signedIn: Boolean): Boolean = signedIn && !offered
