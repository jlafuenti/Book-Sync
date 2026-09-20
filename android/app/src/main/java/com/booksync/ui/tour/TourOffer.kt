package com.booksync.ui.tour

/**
 * Whether to show "Take the 5-minute tour?" right now (issue #597 §5). Pure,
 * so the composable that hosts the dialog is a one-liner: offer once, after a
 * real sign-in, and never again once answered either way.
 *
 * [offered] is `Boolean?` rather than `Boolean` (issue #642): DataStore
 * hasn't necessarily answered by the time this first gets evaluated, and
 * treating "don't know yet" as `false` flashed the dialog for a returning
 * user who had, in fact, already answered it. Unknown never offers.
 */
fun shouldOfferTour(offered: Boolean?, signedIn: Boolean): Boolean = signedIn && offered == false
