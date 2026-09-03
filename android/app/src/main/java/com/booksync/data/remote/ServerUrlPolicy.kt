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

/**
 * Whether to show the first-run screen instead of the sign-in form (issue #175).
 *
 * Someone who installs from Play without already running a server used to land on
 * a username box and an "Advanced" section, with nothing saying that Tandem is a
 * client for a server they have to run. Asking a stranger for credentials to a
 * thing they have never heard of is the uninstall.
 *
 * Same input as [shouldExpandAdvanced] and the same answer today, deliberately
 * kept separate: that one decides whether a *returning* user's Advanced section
 * starts open, this one decides whether to explain the product at all.
 */
fun shouldShowFirstRun(serverUrl: String): Boolean = serverUrl.isBlank()

/**
 * Where the first-run screen sends someone who does not have a server yet.
 *
 * The repo, not a running instance: there is no public Tandem to point at, and
 * linking anybody's personal host from a Play build is exactly what issue #58
 * took out of the sources.
 */
const val TANDEM_REPO_URL = "https://github.com/jlafuenti/Book-Sync"

/** Shown when the user types something that is not a usable server address. */
const val INVALID_SERVER_URL_MESSAGE =
    "Enter a server address like https://tandem.example.com"

/**
 * Shown after a successful "Request access" (issue #221).
 *
 * `POST /api/auth/register` creates a *pending* account — the server will not
 * issue tokens for it until an admin approves. Without this sentence the flow
 * looks like it worked and the next sign-in looks like a wrong password.
 */
const val REGISTRATION_PENDING_MESSAGE =
    "Request submitted — an admin must approve it before you can sign in"

/**
 * The public demo server a build was pointed at, or nothing (issue #147).
 *
 * Tandem is useless without a server, and Play's reviewer has none: they install
 * the app, meet an empty address field, and mark it non-functional. So a build
 * can carry one read-only demo login, offered on the first-run screen as a
 * single tap — the pattern Grocy uses for the same problem.
 *
 * All three values are machine-local build settings (`tandem.demoUrl`,
 * `tandem.demoUser`, `tandem.demoPassword`) that default to empty, exactly like
 * `tandem.defaultServerUrl` (issue #58). A clean clone therefore has no demo
 * host and no demo credentials in it, and shows no button.
 */
data class DemoAccount(
    /** Normalized by [demoAccountOrNull]; never the raw build setting. */
    val url: String,
    val username: String,
    val password: String,
)

/** Shown when the demo server itself is not answering. */
const val DEMO_UNREACHABLE_MESSAGE =
    "The demo server is not answering right now. Try again in a moment, or " +
        "enter your own Tandem server address above."

/**
 * The build's demo login, or null if this build has no complete one.
 *
 * **All three or nothing.** A URL with no password produces a button that cannot
 * finish what it starts, on the one screen a brand-new install can reach —
 * strictly worse than no button. The URL goes through [normalizeServerUrl] like
 * every other address in the app, so a typo in a build setting is caught here
 * rather than becoming a stored server nobody typed.
 */
fun demoAccountOrNull(url: String, username: String, password: String): DemoAccount? {
    if (username.isBlank() || password.isBlank()) return null
    val normalized = normalizeServerUrl(url) ?: return null
    return DemoAccount(normalized, username, password)
}

/**
 * Anything that looks like it was *trying* to name a scheme but isn't `http://`
 * or `https://`. Two shapes: a scheme word followed by a colon that is not the
 * start of a port number (`https:/host`, `https:host`, `ftp://x`,
 * `javascript:alert(1)`), and the missing-colon typo (`https//host`).
 *
 * The digit lookahead is what keeps `host.com:8000` out of this: by RFC 3986
 * grammar `host.com` is a perfectly valid scheme, so the only thing separating
 * "scheme with an opaque part" from "host with a port" is what follows the colon.
 */
private val MALFORMED_SCHEME =
    Regex("""^[a-zA-Z][a-zA-Z0-9+.\-]*:(?!\d)|^[a-zA-Z][a-zA-Z0-9+.\-]*//""")

/**
 * Turn whatever the user typed into a canonical `scheme://host[:port][/path]`,
 * or null if it can't be one.
 *
 * Issue #149: this used to be nothing at all. `setServerUrl` persisted the raw
 * string and the app restarted; on the next launch `Retrofit.Builder.baseUrl()`
 * threw `IllegalArgumentException` inside Hilt — before any UI existed — so every
 * subsequent launch crashed too, and the only recovery was Clear storage or a
 * reinstall. The Play build ships with no default server, so "type a host" is
 * literally the first thing every new user does, and `tandem.example.com` or
 * `203.0.113.5:8000` are the natural things to type.
 *
 * Rules, all pinned by [ServerUrlPolicyTest]:
 *  - a bare host gets `https://` — that is what people mean, and the secure
 *    scheme is the safe guess;
 *  - anything that names a scheme must name `http` or `https` *properly*. A
 *    one-character typo is rejected rather than repaired: reading `https:/host`
 *    as a host named `https` is worse than useless, because it saves, restarts,
 *    and then shows the user a URL they never typed with no error to explain it;
 *  - the path is **kept**. `retrofitBaseUrl` used to be `stored.trimEnd('/') + "/"`,
 *    so anyone reverse-proxying Tandem under a sub-path has one stored, and
 *    dropping it on upgrade would break every request silently;
 *  - query, fragment and embedded credentials are dropped — never part of a base URL;
 *  - the default port for the scheme is dropped, any other port is kept;
 *  - no trailing slash (callers that need one add it, see [retrofitBaseUrl]).
 */
fun normalizeServerUrl(input: String): String? {
    val trimmed = input.trim()
    if (trimmed.isEmpty()) return null
    val lower = trimmed.lowercase()
    val withScheme = when {
        lower.startsWith("http://") || lower.startsWith("https://") -> trimmed
        MALFORMED_SCHEME.containsMatchIn(trimmed) -> return null
        else -> "https://$trimmed"
    }
    val parsed = withScheme.toHttpUrlOrNull() ?: return null
    // Rebuilding through HttpUrl (rather than string-concatenating scheme/host/port)
    // is what gets IPv6 bracketing, IDN punycoding and default-port omission right.
    return parsed.newBuilder()
        .username("")
        .password("")
        .query(null)
        .fragment(null)
        .build()
        .toString()
        .trimEnd('/')
}
