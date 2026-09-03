package com.booksync.data.remote

import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import okhttp3.Interceptor
import okhttp3.Response
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Points every request at the currently configured server (issue #228).
 *
 * Retrofit binds `baseUrl` once, when the singleton is built, so changing servers
 * used to require restarting the process — `AppRestart.restartApp()` called
 * `Runtime.getRuntime().exit(0)`. That is worse than it sounds:
 * `docs/position-sync-contract.md` requires the final position flush to run in an
 * application-scoped, non-cancellable coroutine, and `exit(0)` does not wait for
 * it. The restart could take a reading position with it.
 *
 * With this in front, Retrofit is given [UNCONFIGURED_BASE_URL] and never actually
 * uses it: the scheme, host and port are replaced per request from
 * [ServerUrlManager.currentUrl], which is `@Volatile` and already read this way by
 * the cover-image URL builder. `setServerUrl` therefore takes effect on the next
 * request, with no restart.
 *
 * **Not on the dictionary client**, which talks to a third party and must keep its
 * own host. It *is* on the refresh client: that one is invisible to the user, and
 * leaving it behind would mean tokens quietly renewing against the previous server.
 *
 * Leaves the request untouched when nothing is configured or the stored value will
 * not parse. `ServerUrlManager.repair` should make the second case unreachable, but
 * this runs on every request in the app and is the wrong place to throw — the
 * request then fails against the placeholder, which is a connection error the
 * login screen already handles, rather than being sent somewhere unintended.
 */
/**
 * Marks a request that already knows where it is going (issue #175).
 *
 * Only the first-run "Check connection" probe sets it. That probe asks whether
 * there is a Tandem server at an address the user has just typed and which is
 * deliberately *not* stored — storing an unverified address is what put a
 * mistyped host in DataStore and tore the welcome screen down mid-probe. So the
 * one request in the app that must keep its own host has to say so.
 *
 * A marker rather than "leave it alone when the host differs from the configured
 * one": Retrofit's base URL is [UNCONFIGURED_BASE_URL], a placeholder that
 * differs from the configured server on *every* request, so that rule would
 * exempt the whole app. Stripped below — it is an instruction to this
 * interceptor, not something a server should ever see.
 */
const val BYPASS_BASE_URL_HEADER = "X-Tandem-Absolute-Url"

@Singleton
class BaseUrlInterceptor @Inject constructor(
    private val serverUrlManager: ServerUrlManager,
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()

        if (request.header(BYPASS_BASE_URL_HEADER) != null) {
            return chain.proceed(
                request.newBuilder().removeHeader(BYPASS_BASE_URL_HEADER).build(),
            )
        }

        val configured = serverUrlManager.currentUrl

        if (configured.isBlank()) return chain.proceed(request)
        val target = configured.toHttpUrlOrNull() ?: return chain.proceed(request)

        val rewritten = request.url.newBuilder()
            .scheme(target.scheme)
            .host(target.host)
            .port(target.port)
            .build()

        return chain.proceed(request.newBuilder().url(rewritten).build())
    }
}
