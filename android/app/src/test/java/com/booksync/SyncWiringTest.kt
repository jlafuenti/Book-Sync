package com.booksync

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Wiring guards for two bugs that were invisible to every other kind of test:
 * code that was *correct* but never *called*.
 *
 *  1. `SyncWorker.enqueuePeriodicSync` / `triggerImmediateSync` existed, were
 *     correct, and had zero call sites — so the offline `pending_sync` queue
 *     only drained if the user happened to open the Library screen.
 *  2. `PlayerViewModel` ran a 5-second save loop identical to
 *     `AudioPlayerService`'s, so every heartbeat produced two server writes
 *     that then raced each other into 409s.
 *
 * Neither is reachable from a JVM unit test: one lives in `Application.onCreate`
 * and the other in a `MediaController` polling loop, and this module has no
 * Robolectric or `work-testing` dependency to drive them. Rather than add a
 * heavyweight dependency for two assertions, these read the source. That is a
 * blunt instrument — it pins the call, not the behaviour — but the failure mode
 * here is precisely "the call disappeared", which it catches exactly.
 *
 * If Robolectric ever arrives, replace these with a real
 * `WorkManagerTestInitHelper` assertion on the enqueued unique work.
 */
class SyncWiringTest {

    private fun source(relativePath: String): String {
        // Gradle runs unit tests with the module directory as the working dir,
        // but walk upward anyway so this survives being run from the repo root.
        var dir = File("").absoluteFile
        repeat(4) {
            val candidate = File(dir, "app/src/main/java/$relativePath")
            if (candidate.exists()) return candidate.readText()
            val direct = File(dir, "src/main/java/$relativePath")
            if (direct.exists()) return direct.readText()
            dir = dir.parentFile ?: return@repeat
        }
        throw AssertionError("Could not locate $relativePath from ${File("").absolutePath}")
    }

    /** Comment lines don't count as call sites — that is how this regressed. */
    private fun codeLines(text: String): List<String> =
        text.lines().map { it.trim() }.filter { !it.startsWith("//") && !it.startsWith("*") }

    @Test
    fun `the application schedules the sync worker on startup`() {
        val app = codeLines(source("com/booksync/BookSyncApp.kt"))

        assertTrue(
            "BookSyncApp must call SyncWorker.enqueuePeriodicSync(this) — without it " +
                "nothing ever schedules SyncWorker and offline writes only replay " +
                "when the Library screen happens to open.",
            app.any { it.contains("SyncWorker.enqueuePeriodicSync") },
        )
        assertTrue(
            "BookSyncApp must also call SyncWorker.triggerImmediateSync(this) so a " +
                "queue built up while offline drains as soon as the device is back " +
                "online, rather than waiting up to 15 minutes for the periodic run.",
            app.any { it.contains("SyncWorker.triggerImmediateSync") },
        )
        assertTrue(
            "The immediate sync must hang off NetworkMonitor.isOnline, not fire " +
                "unconditionally at startup: an unconditional call races the " +
                "periodic run that also starts then, and the two replay the whole " +
                "queue twice over.",
            app.any { it.contains("networkMonitor.isOnline") },
        )
    }

    @Test
    fun `draining the offline queue is serialised`() {
        val repo = codeLines(source("com/booksync/data/repository/PositionRepository.kt"))

        // SyncWorker (periodic + connectivity-triggered) and LibraryViewModel can
        // all call processPendingSync at once, and each reads the whole queue up
        // front. Observed on a device: 713 replays of a 366-row queue.
        assertTrue(
            "processPendingSync must hold pendingSyncMutex for its whole body, or " +
                "concurrent callers replay the same rows against the server.",
            repo.any { it.contains("suspend fun processPendingSync() = pendingSyncMutex.withLock") },
        )
    }

    @Test
    fun `only the service runs a periodic position save`() {
        val screen = codeLines(source("com/booksync/ui/player/PlayerScreen.kt"))

        // The screen may still save at boundaries it alone can recognise (a
        // deliberate pause, switching to the reader). What it must not do is
        // run its own repeating timer alongside the service's.
        val heartbeatState = screen.filter {
            it.contains("SAVE_INTERVAL_MS") || it.contains("lastSaveTimeMs") ||
                it.contains("LOG_INTERVAL_MS") || it.contains("lastLogTimeMs")
        }
        assertEquals(
            "PlayerScreen must not keep heartbeat / 30-min-tick timer state: " +
                "AudioPlayerService.startAutoPositionSave owns the one periodic " +
                "save, and a second loop doubles every write and makes the app " +
                "race itself into 409s. Found: $heartbeatState",
            emptyList<String>(),
            heartbeatState,
        )
    }

    @Test
    fun `the service seeds its continuous-playback timer instead of comparing against zero`() {
        val service = codeLines(source("com/booksync/player/AudioPlayerService.kt"))

        assertTrue(
            "AudioPlayerService must drive the 30-min history tick through " +
                "ContinuousPlaybackLog, which represents 'never started' as null. " +
                "A bare `0L` seed compares against the whole Unix epoch, so the " +
                "tick fired on the first heartbeat of every session.",
            service.any { it.contains("continuousPlaybackLog.isDue") },
        )
        assertTrue(
            "The timer must be seeded when playback starts, or the first tick is due immediately.",
            service.any { it.contains("continuousPlaybackLog.onPlaybackStarted") },
        )
        assertTrue(
            "No stray `0L`-seeded log timer should remain.",
            service.none { it.contains("lastAutoLogTimeMs") },
        )
    }

    @Test
    fun `navigation acts on the forced-password-reset gate`() {
        // Issue #209. PasswordResetGate is raised from AuthInterceptor, on an
        // OkHttp thread with no access to a NavController, so the two halves are
        // joined only by a flow collection in BookSyncNavigation. Every other
        // test here can pass with that collection deleted: the gate would rise,
        // nothing would watch it, and a user holding a temporary password would
        // sit in an app where every screen fails with 403 and none of them say
        // why. Exactly the "correct but never called" shape this file exists for.
        val nav = codeLines(source("com/booksync/ui/BookSyncNavigation.kt"))

        assertTrue(
            "BookSyncNavigation must collect PasswordResetGate.required — without " +
                "it AuthInterceptor raises a gate nobody is watching.",
            nav.any { it.contains("passwordResetGate.required") },
        )
        assertTrue(
            "BookSyncNavigation must navigate to Routes.FORCE_PASSWORD_RESET when " +
                "the gate is raised.",
            nav.any { it.contains("Routes.FORCE_PASSWORD_RESET") },
        )
        assertTrue(
            "The forced-reset destination must be registered in the NavHost, or " +
                "navigating to it throws.",
            nav.any { it.contains("ForcePasswordResetScreen") },
        )
        // The effect reads `startDestination`, which resolves asynchronously from
        // DataStore. If it is not also a *key*, an already-raised gate — raised by
        // SyncWorker through the same interceptor, from WorkManager, with no
        // Activity alive — fires the effect once while startDestination is still
        // null, the guard swallows it, and StateFlow conflation means it never
        // emits again. The user lands on MAIN with every screen 403ing and no way
        // out. Every assertion above passes in that state, which is why this one
        // exists.
        assertTrue(
            "LaunchedEffect must key on startDestination as well as resetRequired, " +
                "or a gate raised before the start destination resolves is lost.",
            nav.any { it.contains("LaunchedEffect(resetRequired, startDestination)") },
        )
        assertTrue(
            "Clearing tokens must lower the gate: otherwise a 403 arriving after " +
                "logout drags the login screen to the reset screen.",
            nav.any { it.contains("passwordResetGate.clear()") },
        )
    }

    @Test
    fun `the forced-reset screen offers no way out but changing the password`() {
        // A ModalBottomSheet was rejected for this precisely because it can be
        // swiped away; a back gesture would do the same thing. Leaving either
        // open drops the user onto a screen where every call 403s, with no route
        // back to the one screen that works.
        val screen = codeLines(
            source("com/booksync/ui/account/ForcePasswordResetScreen.kt")
        )

        assertTrue(
            "ForcePasswordResetScreen must block the system back gesture.",
            screen.any { it.contains("BackHandler") },
        )
        assertTrue(
            "It must pass onCancel = null to ChangePasswordForm, which is what " +
                "suppresses the Cancel button.",
            screen.any { it.contains("onCancel = null") },
        )
        assertTrue(
            "It must not be a dismissible sheet.",
            screen.none { it.contains("ModalBottomSheet") },
        )
        // /api/auth/logout is on the server's allow-list precisely so this escape
        // hatch can bump token_version. A local-only token wipe would leave the
        // temporary password's access token valid server-side for a further 24h.
        assertTrue(
            "The sign-out affordance must call the view-model logout (which hits " +
                "/api/auth/logout), not merely clear tokens locally.",
            screen.any { it.contains("viewModel.logout()") },
        )
    }

    @Test
    fun `both authentication paths claim pre-scoping rows`() {
        // Issue #314. The login path alone is not enough, and that gap is the
        // whole bug for existing installs: a device upgrading into the scoped
        // build already holds a token and never logs in again, so nothing would
        // ever claim its pre-scoping rows and the user would silently lose every
        // local position and bookmark. Both call sites have to exist.
        val login = codeLines(source("com/booksync/ui/auth/LoginScreen.kt"))
        val nav = codeLines(source("com/booksync/ui/BookSyncNavigation.kt"))

        assertTrue(
            "LoginViewModel.login must call userScopeProvider.onAuthenticated().",
            login.any { it.contains("userScopeProvider.onAuthenticated") },
        )
        assertTrue(
            "BookSyncNavigation must also call it at start-up for a stored token — " +
                "an upgraded install never runs the login path again.",
            nav.any { it.contains("userScopeProvider.onAuthenticated") },
        )
    }

    @Test
    fun `every cache path is scoped to one account, never read wholesale`() {
        // The corruption half of #314. It was not enough to scope the queue:
        // processPendingSync also pushed `bookmarks` and `user_progress` rows with
        // syncedToServer = 0, and syncAllBookmarksAndProgress does the same on
        // every library load. All of them sent under whatever token was stored.
        val repo = codeLines(
            source("com/booksync/data/repository/PositionRepository.kt")
        )

        for (call in listOf(
            "pendingSyncDao.getPendingForScope",
            "bookmarkDao.getUnsyncedBookmarks(scope)",
            "userProgressDao.getUnsyncedProgress(scope)",
        )) {
            assertTrue("$call must be the scoped form", repo.any { it.contains(call) })
        }
        assertTrue(
            "No unscoped queue read may exist on any path.",
            repo.none { it.contains("getAllPending()") },
        )
        assertTrue(
            "Queued writes must carry the scope, or the filters have nothing to " +
                "filter on.",
            repo.any { it.contains("scopeKey = scope") },
        )
    }

    @Test
    fun `refresh never runs on the authenticated client`() {
        // Issue #143. Every other test here can pass while the refresh sits back
        // on the main API: the deadlock only appears when a *rejected* refresh
        // token meets a real dispatcher, which no unit test reproduces. So pin
        // the structural property instead — the endpoint lives on a client that
        // has no interceptor to re-enter.
        val api = codeLines(source("com/booksync/data/remote/BookSyncApi.kt"))
        val interceptor = codeLines(source("com/booksync/data/remote/AuthInterceptor.kt"))
        val module = codeLines(source("com/booksync/di/AppModule.kt"))

        assertTrue(
            "api/auth/refresh must not be declared on BookSyncApi — that API is " +
                "built on the client AuthInterceptor is installed on, so refreshing " +
                "through it recurses.",
            api.none { it.contains("api/auth/refresh") },
        )
        assertTrue(
            "AuthInterceptor must not refresh; that belongs to TokenAuthenticator, " +
                "which OkHttp calls once per 401 from outside the chain.",
            interceptor.none { it.contains("refreshToken") },
        )
        assertTrue(
            "The authenticator must actually be attached to the client, or nothing " +
                "refreshes at all and every expired session looks like a logout.",
            module.any { it.contains(".authenticator(") },
        )
        // Naming the provider proves nothing; what matters is what it builds. Read
        // the body, because adding one .addInterceptor(authInterceptor) line here
        // restores the deadlock and every other test in the repo still passes.
        val refreshClient = module
            .dropWhile { !it.contains("fun provideRefreshOkHttpClient") }
            .drop(1)
            .takeWhile { !it.contains("@Provides") }
        assertTrue(
            "provideRefreshOkHttpClient must exist and build a client.",
            refreshClient.any { it.contains("OkHttpClient.Builder()") },
        )
        assertTrue(
            "The refresh client must carry no AuthInterceptor and no authenticator " +
                "-- that isolation is the entire fix for #143.",
            refreshClient.none {
                it.contains("authInterceptor") || it.contains(".authenticator(")
            },
        )
        assertTrue(
            "The refresh client needs a callTimeout. Its per-stage timeouts can " +
                "still add up to 90s, and the refresh is held under a global mutex, " +
                "so an unresponsive server parks every other request behind it.",
            refreshClient.any { it.contains(".callTimeout(") },
        )
    }

    // ---------------------------------------------------------------------
    // Issue #231. These cannot be behavioural tests: AudioPlayerService and
    // LocalCastHttpServer are excluded from Kover and are Android-framework
    // glue that JVM unit tests cannot instantiate, and there is no way to
    // assert on what logcat received from here anyway.
    //
    // So read the source. The failure mode is exactly "someone interpolated a
    // secret into a log line again", which a text scan catches precisely.
    // ---------------------------------------------------------------------

    @Test
    fun `no log line carries the LAN cast token`() {
        // The token gates the phone's local HTTP server while casting. Logging
        // it hands anyone with adb, a bug-report bundle, or OEM diagnostics a
        // working audiobook stream URL for the life of the session.
        val offenders = mutableListOf<String>()
        for (path in listOf(
            "com/booksync/player/AudioPlayerService.kt",
            "com/booksync/player/LocalCastHttpServer.kt",
        )) {
            codeLines(source(path)).forEachIndexed { i, line ->
                if (!line.contains("Log.")) return@forEachIndexed
                // $token, ${...token...}, or a URL built from one.
                if (Regex("""\$\{?[A-Za-z.]*(token|streamUrl)""", RegexOption.IGNORE_CASE)
                        .containsMatchIn(line)
                ) {
                    offenders += "${path.substringAfterLast('/')}:${i + 1} -> $line"
                }
            }
        }

        assertTrue(
            "Log lines interpolating the cast token or a URL containing it: $offenders. " +
                "Log the host and port instead.",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `every OkHttp client gates its logging on the build type`() {
        // Three clients today (main, refresh, dictionary). The interesting
        // failure is a fourth being added with a bare Level.BASIC, which no
        // other test would notice — AppModule is excluded from coverage.
        val module = codeLines(source("com/booksync/di/AppModule.kt"))

        val bare = module.filter { it.contains("HttpLoggingInterceptor.Level.") }
        assertTrue(
            "HttpLoggingInterceptor level set directly instead of via " +
                "httpLoggingLevel(BuildConfig.DEBUG): $bare",
            bare.isEmpty(),
        )

        val gated = module.count { it.contains("httpLoggingLevel(") }
        val clients = module.count { it.contains("OkHttpClient.Builder()") }
        assertEquals(
            "every OkHttpClient must gate its logging; found $clients clients " +
                "and $gated gated logging levels",
            clients, gated,
        )
    }

    @Test
    fun `the base-url interceptor is registered on both server-facing clients`() {
        // BaseUrlInterceptorTest builds the interceptor directly, so it passes
        // whether or not anything installs it — removing the registration failed
        // nothing until this existed. AppModule is excluded from Kover too, so
        // reading the source is the only thing that can hold this.
        val module = codeLines(source("com/booksync/di/AppModule.kt"))

        val registrations = module.count { it.contains(".addInterceptor(baseUrlInterceptor)") }
        assertEquals(
            "Both the main and the refresh client must rewrite the base URL. " +
                "Without it on the refresh client, tokens keep renewing against " +
                "the previous server after a switch (issue #228).",
            2, registrations,
        )

        val placeholders = module.count {
            it.contains("baseUrl(com.booksync.data.remote.UNCONFIGURED_BASE_URL)")
        }
        assertEquals(
            "Both server-facing Retrofit builders must use the placeholder; a real " +
                "URL baked in at construction is the restart-on-switch bug.",
            2, placeholders,
        )

        assertTrue(
            "getServerUrlBlocking() has no remaining callers — the base URL is read " +
                "per request now.",
            module.none { it.contains("getServerUrlBlocking()") },
        )
    }

    @Test
    fun `nothing restarts the process to change servers`() {
        // Runtime.getRuntime().exit(0) does not wait for the application-scoped,
        // non-cancellable position flush the sync contract requires, so it could
        // take a reading position with it (issue #228).
        for (path in listOf(
            "com/booksync/ui/account/AccountViewModel.kt",
            "com/booksync/ui/auth/LoginScreen.kt",
        )) {
            val src = codeLines(source(path))
            assertTrue(
                "$path must not restart the app to apply a server change.",
                src.none { it.contains("restartApp") || it.contains("Runtime.getRuntime()") },
            )
        }
    }

    @Test
    fun `no singleton blocks on disk in its constructor`() {
        // Issue #318. Hilt builds these inside Application.onCreate, so a
        // runBlocking in an init block is a disk read on the main thread before
        // the first frame. Behavioural tests cannot see it — they construct these
        // off the main thread and never notice — so read the source.
        val offenders = mutableListOf<String>()
        for (path in listOf(
            "com/booksync/data/remote/ServerUrlManager.kt",
            "com/booksync/data/remote/TokenManager.kt",
            "com/booksync/data/remote/DeviceIdManager.kt",
            "com/booksync/data/remote/UserScopeProvider.kt",
        )) {
            val src = codeLines(source(path))
            // `init {` followed by a runBlocking before the block closes.
            var inInit = false
            src.forEach { line ->
                if (line.startsWith("init {")) inInit = true
                else if (inInit && line == "}") inInit = false
                if (inInit && line.contains("runBlocking")) {
                    offenders += path.substringAfterLast('/')
                }
            }
            // Belt and braces: a property initialiser can block just as well.
            src.forEach { line ->
                if (line.contains("= runBlocking") || line.contains("=runBlocking")) {
                    offenders += "${path.substringAfterLast('/')} (property initialiser)"
                }
            }
        }

        assertTrue(
            "These seed themselves with a blocking DataStore read at construction, " +
                "which runs on the main thread inside Application.onCreate. Use " +
                "SeededValue instead: $offenders",
            offenders.isEmpty(),
        )
    }

    @Test
    fun `the seeded singletons use the shared single-flight helper`() {
        // SeededValue's memoisation is what stops DeviceIdManager minting two
        // device ids on a first launch. A class that hand-rolls its own seeding
        // would pass the guard above while reintroducing exactly that race.
        for (path in listOf(
            "com/booksync/data/remote/ServerUrlManager.kt",
            "com/booksync/data/remote/TokenManager.kt",
            "com/booksync/data/remote/DeviceIdManager.kt",
            "com/booksync/data/remote/UserScopeProvider.kt",
        )) {
            val src = codeLines(source(path))
            assertTrue(
                "${path.substringAfterLast('/')} must seed through SeededValue.",
                src.any { it.contains("SeededValue(") },
            )
        }
    }
}
