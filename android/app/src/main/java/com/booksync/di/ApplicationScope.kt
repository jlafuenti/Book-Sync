package com.booksync.di

import javax.inject.Qualifier

/**
 * A `CoroutineScope` that lives as long as the process.
 *
 * Used to seed the singletons that back every request — server URL, tokens,
 * device id — off the main thread at construction (issue #318), without tying
 * that work to any screen's lifecycle.
 *
 * Qualified because an unqualified `CoroutineScope` binding is too easy to inject
 * by accident somewhere that should be using a `viewModelScope` and be cancelled
 * with its screen.
 */
@Qualifier
@Retention(AnnotationRetention.BINARY)
annotation class ApplicationScope
