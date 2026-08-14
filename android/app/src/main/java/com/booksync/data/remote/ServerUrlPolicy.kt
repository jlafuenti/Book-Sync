package com.booksync.data.remote

/**
 * Pure decisions about the configured server URL, kept free of Android and DI
 * types so they're unit-testable (same pattern as `PositionSavePolicy`).
 */

/** Hilt qualifier for the build-time default server URL (`BuildConfig.DEFAULT_SERVER_URL`). */
const val DEFAULT_SERVER_URL_QUALIFIER = "defaultServerUrl"

/** Old production hostname; its DNS record no longer exists. Stored values are migrated. */
const val LEGACY_SERVER_URL = "https://booksync.lafuenti.com"

/**
 * Base URL Retrofit is built with while no server is configured. Retrofit rejects
 * a blank base URL at construction time, which would crash the app on first launch
 * of a build that ships no default. The placeholder keeps DI alive; requests
 * against it fail as ordinary connection errors until the user enters a real URL.
 */
const val UNCONFIGURED_BASE_URL = "http://localhost:8000/"

/** Normalize a stored server URL into a Retrofit base URL (always exactly one trailing slash). */
fun retrofitBaseUrl(stored: String): String =
    if (stored.isBlank()) UNCONFIGURED_BASE_URL else stored.trim().trimEnd('/') + "/"

/**
 * Whether the login screen's "Advanced" section should start expanded. With no
 * server configured the URL field is the only thing worth doing on that screen,
 * and it's the only way to configure one — so don't hide it behind a toggle.
 */
fun shouldExpandAdvanced(serverUrl: String): Boolean = serverUrl.isBlank()
