package com.booksync.data.remote

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

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

/**
 * Normalize a stored server URL into a Retrofit base URL (always exactly one
 * trailing slash), falling back to [UNCONFIGURED_BASE_URL] for anything that is
 * not a usable http(s) origin.
 *
 * That fallback is the load-bearing half of issue #149. `Retrofit.Builder.baseUrl`
 * throws on a value it can't parse, and it is called from `AppModule.provideRetrofit`
 * — inside Hilt, during `MainActivity.onCreate`, before any screen exists. Anything
 * that reaches here unparseable (a value written by a build that predates
 * [normalizeServerUrl], say) must degrade to a placeholder that produces ordinary
 * connection errors, never an exception that makes the app un-launchable.
 */
fun retrofitBaseUrl(stored: String): String =
    normalizeServerUrl(stored)?.plus("/") ?: UNCONFIGURED_BASE_URL

/**
 * Whether the login screen's "Advanced" section should start expanded. With no
 * server configured the URL field is the only thing worth doing on that screen,
 * and it's the only way to configure one — so don't hide it behind a toggle.
 */
fun shouldExpandAdvanced(serverUrl: String): Boolean = serverUrl.isBlank()

/** Shown when the user types something that is not a usable server address. */
const val INVALID_SERVER_URL_MESSAGE =
    "Enter a server address like https://tandem.example.com"

/**
 * Turn whatever the user typed into a canonical `scheme://host[:port]` origin,
 * or null if it can't be one.
 *
 * Issue #149: this used to be nothing at all. `setServerUrl` persisted the raw
 * string and the app restarted; on the next launch `Retrofit.Builder.baseUrl()`
 * threw `IllegalArgumentException` inside Hilt — before any UI existed — so every
 * subsequent launch crashed too, and the only recovery was Clear storage or a
 * reinstall. The Play build ships with no default server, so "type a host" is
 * literally the first thing every new user does, and `tandem.example.com` or
 * `192.168.1.5:8000` are the natural things to type.
 *
 * Rules, all pinned by [ServerUrlPolicyTest]:
 *  - a bare host gets `https://` rather than being rejected — that is what people
 *    mean, and defaulting to the secure scheme is the safe guess;
 *  - only `http` and `https` survive (OkHttp's parser rejects every other scheme
 *    for us, which is also what kills `javascript:` and `ftp://`);
 *  - path, query, fragment and any embedded credentials are dropped — the field
 *    asks for an origin, and `docs/android.md` says so;
 *  - the default port for the scheme is dropped, everything else is kept;
 *  - no trailing slash (callers that need one add it, see [retrofitBaseUrl]).
 */
fun normalizeServerUrl(input: String): String? {
    val trimmed = input.trim()
    if (trimmed.isEmpty()) return null
    val withScheme = if (trimmed.contains("://")) trimmed else "https://$trimmed"
    val parsed = withScheme.toHttpUrlOrNull() ?: return null
    // Rebuilding through HttpUrl (rather than string-concatenating scheme/host/port)
    // is what gets IPv6 bracketing and default-port omission right.
    return parsed.newBuilder()
        .username("")
        .password("")
        .encodedPath("/")
        .query(null)
        .fragment(null)
        .build()
        .toString()
        .trimEnd('/')
}
