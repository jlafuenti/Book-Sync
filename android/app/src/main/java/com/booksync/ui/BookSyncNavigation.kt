package com.booksync.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.LibraryBooks
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.DownloadDone
import androidx.compose.material.icons.filled.Home
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.navigation.NavHostController
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.booksync.data.remote.TokenManager
import com.booksync.ui.auth.LoginScreen
import com.booksync.ui.components.MiniPlayerBar
import com.booksync.ui.components.decodePairIdFromMediaId
import com.booksync.ui.diagnostics.DiagnosticsScreen
import com.booksync.ui.downloaded.DownloadedScreen
import com.booksync.ui.home.HomeScreen
import com.booksync.ui.home.HomeSeeAll
import com.booksync.data.repository.PairOpenTarget
import com.booksync.ui.library.LibraryFilter
import com.booksync.ui.library.LibraryScreen
import com.booksync.ui.library.LibrarySort
import com.booksync.ui.library.SearchResultItem
import com.booksync.ui.library.SearchScreen
import com.booksync.ui.player.PlayerScreen
import com.booksync.ui.reader.ReaderScreen
import com.booksync.ui.account.ForcePasswordResetScreen
import com.booksync.ui.account.AccountScreen
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.firstOrNull

/**
 * Hilt entry point to access TokenManager from a Composable context.
 */
@dagger.hilt.EntryPoint
@dagger.hilt.InstallIn(dagger.hilt.components.SingletonComponent::class)
interface TokenManagerEntryPoint {
    fun tokenManager(): TokenManager
}

/** Hilt entry point for the forced-password-reset gate (issue #209). */
@dagger.hilt.EntryPoint
@dagger.hilt.InstallIn(dagger.hilt.components.SingletonComponent::class)
interface PasswordResetGateEntryPoint {
    fun passwordResetGate(): com.booksync.data.remote.PasswordResetGate
}

/** Hilt entry point for the cache scope (issue #314). */
@dagger.hilt.EntryPoint
@dagger.hilt.InstallIn(dagger.hilt.components.SingletonComponent::class)
interface UserScopeProviderEntryPoint {
    fun userScopeProvider(): com.booksync.data.remote.UserScopeProvider
}

// ------------------------------------------------------------
// Route constants — centralized so deep links stay consistent.
// Library accepts optional filter / series / sort / group args.
// ------------------------------------------------------------

object Routes {
    const val LOGIN       = "login"
    const val MAIN        = "main"
    /** Forced password change; the app is unusable until it succeeds (issue #209). */
    const val FORCE_PASSWORD_RESET = "force_password_reset"
    const val HOME        = "home"
    const val LIBRARY     = "library"
    const val DOWNLOADED  = "downloaded"
    const val ACCOUNT     = "account"
    const val SEARCH      = "search"
    const val SETTINGS    = "settings"                // kept for legacy intents
    const val DIAGNOSTICS = "diagnostics/{channel}"
    /**
     * `handoffAudioMs` is set only by "Switch to Reader" in the player: the
     * audiobook's position at the moment the user switched, so the reader can
     * open the page that goes with it instead of the stored ebook coordinate,
     * which only a reader save updates and can be hours stale. Absent (0) for
     * every ordinary open.
     */
    const val READER               = "reader/{pairId}?handoffAudioMs={handoffAudioMs}"
    /** A standalone (unpaired) ebook — no pair, no sync map (issue #169). */
    const val READER_STANDALONE    = "reader/standalone/{ebookId}"
    const val PLAYER               = "player/{pairId}"
    const val PLAYER_STANDALONE    = "player/standalone/{audiobookId}"
    const val BOOK_DETAILS_PAIR      = "book_details/pair/{pairId}"
    const val BOOK_DETAILS_EBOOK     = "book_details/ebook/{ebookId}"
    const val BOOK_DETAILS_AUDIOBOOK = "book_details/audiobook/{audiobookId}"

    /**
     * Build a library URL with optional filter / series / sort / group parameters.
     * Omitted params are dropped from the query string.
     */
    fun library(
        filter: String? = null,
        series: String? = null,
        sort: String? = null,
        groupBySeries: Boolean = false,
    ): String {
        val params = buildList {
            filter?.let { add("filter=$it") }
            series?.let { add("series=${java.net.URLEncoder.encode(it, "UTF-8")}") }
            sort?.let { add("sort=$it") }
            if (groupBySeries) add("group=series")
        }
        return if (params.isEmpty()) LIBRARY else "$LIBRARY?${params.joinToString("&")}"
    }

    // Only the handoff adds the argument; every ordinary open builds exactly
    // the route it always did, which `SearchDestinationTest` pins. The query
    // parameter is optional on the pattern, so both forms match.
    fun reader(pairId: Int, handoffAudioMs: Long = 0L) =
        if (handoffAudioMs > 0) "reader/$pairId?handoffAudioMs=$handoffAudioMs" else "reader/$pairId"
    fun readerStandalone(ebookId: Int) = "reader/standalone/$ebookId"
    fun player(pairId: Int)  = "player/$pairId"
    fun playerStandalone(audiobookId: Int) = "player/standalone/$audiobookId"
    fun diagnostics(channel: String) = "diagnostics/$channel"
    fun bookDetailsPair(pairId: Int)           = "book_details/pair/$pairId"
    fun bookDetailsEbook(ebookId: Int)         = "book_details/ebook/$ebookId"
    fun bookDetailsAudiobook(audiobookId: Int) = "book_details/audiobook/$audiobookId"

    /**
     * Where a Search result opens (issue #119).
     *
     * Search deals in three kinds of row and used to route all of them through
     * the pair routes, so a standalone ebook's id was read as a pair id — the
     * reader then opened an unrelated pair, or none, and its Reset / Mark
     * Complete actions silently did nothing (or worse, acted on that other
     * book). Standalone media gets the same destinations Library gives it:
     * details for an ebook and the standalone player for an audiobook.
     *
     * A standalone ebook goes to **details, not the reader**, even though a
     * standalone reader now exists (issue #169). [SearchResultItem] carries no
     * downloaded flag, so search cannot tell whether the file is on the device,
     * and opening a reader with nothing to read is worse than landing on the page
     * that has the Download button. Home and Library gate their Read entry points
     * on `isDownloaded` for exactly that reason.
     *
     * [pairTarget] is what `resolvePairOpenTarget` said about this pair — the
     * format the user last actually consumed (issue #220). Search used to ignore
     * it and always open the reader, and because reader saves claim `ebook`
     * (docs/position-sync-contract.md, "Who may claim `source`"), that one tap
     * flipped the book's routing to the ebook for every later open from Home or
     * Library. Null means the lookup did not resolve; the reader was the
     * behaviour before this change and stays the fallback.
     *
     * Returns null when the row names nothing openable.
     */
    fun searchDestination(
        item: SearchResultItem,
        pairTarget: PairOpenTarget? = null,
    ): String? {
        item.pairId?.let { pairId ->
            // Details is a real destination here (issue #484). It used to fall
            // in with Reader on the stated grounds that search had no pair
            // details route — `bookDetailsPair` has existed all along, and
            // Library has always used it. That was not a cosmetic mistake:
            // `ReaderScreen` fetches the EPUB on open (issue #171), so sending a
            // nothing-downloaded pair to the reader began a download nobody had
            // asked for.
            //
            // An unresolved lookup still falls back to the reader: `null` means
            // the suspend call against Room failed or has not finished, which is
            // not the same as knowing there is nothing on the device.
            return when (pairTarget) {
                PairOpenTarget.Player -> player(pairId)
                PairOpenTarget.Details -> bookDetailsPair(pairId)
                else -> reader(pairId)
            }
        }
        val id = item.numericId ?: return null
        return when {
            item.isEbook     -> bookDetailsEbook(id)
            item.isAudiobook -> playerStandalone(id)
            else             -> null
        }
    }
}

/**
 * Bottom-tab destination definition. Keeping this as a small data class lets us iterate
 * over the tabs instead of copy/pasting five identical NavigationBarItem blocks.
 */
private data class BottomTab(
    val route: String,
    val label: String,
    val icon: androidx.compose.ui.graphics.vector.ImageVector,
)

private val BOTTOM_TABS = listOf(
    BottomTab(Routes.HOME,       "Home",       Icons.Default.Home),
    BottomTab(Routes.LIBRARY,    "Library",    Icons.AutoMirrored.Filled.LibraryBooks),
    BottomTab(Routes.DOWNLOADED, "Downloaded", Icons.Default.DownloadDone),
    BottomTab(Routes.ACCOUNT,    "Account",    Icons.Default.AccountCircle),
)

/**
 * Navigation graph for the BookSync app.
 *
 * Structure:
 *   - Outer [NavHost] handles login + overlay routes (search, settings, diagnostics,
 *     reader, player).
 *   - Inner [NavHost] inside the "main" route runs the 4-tab shell with a
 *     [MiniPlayerBar] pinned above the [NavigationBar].
 */
@Composable
fun BookSyncNavigation() {
    val navController = rememberNavController()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    val tokenManager = remember {
        EntryPointAccessors.fromApplication(
            context.applicationContext,
            TokenManagerEntryPoint::class.java,
        ).tokenManager()
    }

    val userScopeProvider = remember {
        EntryPointAccessors.fromApplication(
            context.applicationContext,
            UserScopeProviderEntryPoint::class.java,
        ).userScopeProvider()
    }

    val passwordResetGate = remember {
        EntryPointAccessors.fromApplication(
            context.applicationContext,
            PasswordResetGateEntryPoint::class.java,
        ).passwordResetGate()
    }

    // Resolve start destination based on persisted auth token.
    //
    // Note there is no getMe() probe here for must_reset_password (issue #209).
    // The gate is raised by AuthInterceptor instead, from the 403 the server
    // returns on the first real call — one mechanism covering both launch and
    // mid-session, rather than two that can disagree. It also keeps the app
    // usable offline: a probe would either block launch or fail closed, and a
    // user with a temporary password can still read what they have downloaded.
    // They are gated the moment they reach the server.
    var startDestination by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        val existingToken = tokenManager.getAccessToken().firstOrNull()
        if (!existingToken.isNullOrEmpty()) {
            // Also here, not only on the login path (issue #314): an install
            // upgrading into this build has a stored token and never logs in
            // again, so nothing would ever claim its pre-scoping rows and the
            // user would silently lose every local position and bookmark.
            userScopeProvider.onAuthenticated()
        }
        startDestination = if (!existingToken.isNullOrEmpty()) Routes.MAIN else Routes.LOGIN
    }

    // Observe token clears (logout / expiry) and bounce to login
    LaunchedEffect(Unit) {
        tokenManager.getAccessToken()
            .drop(1)
            .collect { token ->
                if (token.isNullOrEmpty()) {
                    // No session means no reset to force. Without this an
                    // in-flight 403 landing after logout would raise the gate and
                    // drag the login screen off to the reset screen (issue #209).
                    passwordResetGate.clear()
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(0) { inclusive = true }
                    }
                }
            }
    }

    // The gate is raised by AuthInterceptor, off an OkHttp thread, when any call
    // comes back 403 password_reset_required (issue #209). This is the half that
    // acts on it; popUpTo(0) leaves no back stack to a screen where every request
    // now fails.
    //
    // `startDestination` must be a key, not just a read. It resolves
    // asynchronously from DataStore, and the gate can already be up before this
    // composable ever runs — SyncWorker replays the offline queue through the same
    // interceptor from WorkManager, with no Activity alive. Keyed only on
    // `resetRequired`, that ordering fires the effect once while startDestination
    // is still null, the guard swallows it, and StateFlow conflation means an
    // already-true gate never emits again: the user lands on MAIN with every
    // screen 403ing and no route out.
    val resetRequired by passwordResetGate.required.collectAsState()
    LaunchedEffect(resetRequired, startDestination) {
        if (resetRequired && startDestination != null) {
            navController.navigate(Routes.FORCE_PASSWORD_RESET) {
                popUpTo(0) { inclusive = true }
            }
        }
    }

    if (startDestination == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            CircularProgressIndicator()
        }
        return
    }

    NavHost(navController = navController, startDestination = startDestination!!) {

        composable(Routes.LOGIN) {
            LoginScreen(
                onLoginSuccess = {
                    navController.navigate(Routes.MAIN) {
                        popUpTo(Routes.LOGIN) { inclusive = true }
                    }
                },
            )
        }

        composable(Routes.MAIN) {
            MainScaffold(outerNavController = navController)
        }

        composable(Routes.FORCE_PASSWORD_RESET) {
            ForcePasswordResetScreen(
                // `change_password` bumps token_version server-side, so by the time
                // it returns 200 the tokens in hand are already dead — navigating
                // to MAIN would 401 on the first call, fail the refresh, and bounce
                // to login anyway, via a detour through a broken screen. Clearing
                // the tokens routes there directly through the observer above, and
                // signing in with the new password is the honest next step.
                onPasswordChanged = {
                    // AccountViewModel.changePassword clears the tokens, for both
                    // screens that change a password rather than only this one.
                    // Clearing again here is a second null emission from a flow
                    // with no distinctUntilChanged, and so a second
                    // navigate(LOGIN) { popUpTo(0) }.
                    passwordResetGate.clear()
                },
                onLogout = { /* handled by the screen's own view-model */ },
            )
        }

        composable(Routes.SEARCH) {
            SearchScreen(
                onBack = { navController.popBackStack() },
                onResultSelect = { item, pairTarget ->
                    Routes.searchDestination(item, pairTarget)?.let { navController.navigate(it) }
                },
            )
        }

        // Legacy route kept for any intents that still point at "settings".
        // Delegates to AccountScreen (the Account tab replacement for SettingsScreen).
        composable(Routes.SETTINGS) {
            AccountScreen(
                onDiagnosticsAuto   = { navController.navigate(Routes.diagnostics("AUTO")) },
                onDiagnosticsApp    = { navController.navigate(Routes.diagnostics("APP")) },
                onClearAllDownloads = { /* no-op in overlay context */ },
            )
        }

        composable(
            Routes.DIAGNOSTICS,
            arguments = listOf(navArgument("channel") { type = NavType.StringType }),
        ) {
            DiagnosticsScreen(onBack = { navController.popBackStack() })
        }

        composable(
            Routes.READER,
            arguments = listOf(
                navArgument("pairId") { type = NavType.IntType },
                navArgument("handoffAudioMs") { type = NavType.LongType; defaultValue = 0L },
            ),
        ) { backStackEntry ->
            val pairId = backStackEntry.arguments?.getInt("pairId") ?: return@composable
            ReaderScreen(
                pairId = pairId,
                handoffAudioMs = backStackEntry.arguments?.getLong("handoffAudioMs") ?: 0L,
                onBack = { navController.popBackStack() },
                onSwitchToAudio = {
                    navController.navigate(Routes.player(pairId)) {
                        popUpTo(Routes.MAIN)
                    }
                },
            )
        }

        composable(
            Routes.PLAYER,
            arguments = listOf(navArgument("pairId") { type = NavType.IntType }),
        ) { backStackEntry ->
            val pairId = backStackEntry.arguments?.getInt("pairId") ?: return@composable
            PlayerScreen(
                pairId = pairId,
                onBack = { navController.popBackStack() },
                onSwitchToReader = { audioMs ->
                    // Carry the position the user is actually at, so the reader
                    // opens the matching page rather than the stored (possibly
                    // hours-stale) ebook coordinate.
                    navController.navigate(Routes.reader(pairId, audioMs)) {
                        popUpTo(Routes.MAIN)
                    }
                },
            )
        }

        // Standalone audiobook player — no pair, no reader switch.
        // The audiobookId arg is read by PlayerViewModel from its SavedStateHandle.
        composable(
            Routes.READER_STANDALONE,
            arguments = listOf(navArgument("ebookId") { type = NavType.IntType }),
        ) { backStackEntry ->
            val ebookId = backStackEntry.arguments?.getInt("ebookId") ?: return@composable
            com.booksync.ui.reader.StandaloneReaderScreen(
                ebookId = ebookId,
                onBack = { navController.popBackStack() },
            )
        }

        composable(
            Routes.PLAYER_STANDALONE,
            arguments = listOf(navArgument("audiobookId") { type = NavType.IntType }),
        ) {
            PlayerScreen(
                pairId = -1,            // sentinel: no pair (ViewModel uses audiobookId from SavedStateHandle)
                onBack = { navController.popBackStack() },
                onSwitchToReader = { _ -> }, // not applicable for standalone
            )
        }

        // ---- Book Details (3 route variants for pair / standalone ebook / standalone audiobook) ----
        composable(
            Routes.BOOK_DETAILS_PAIR,
            arguments = listOf(navArgument("pairId") { type = NavType.IntType }),
        ) {
            com.booksync.ui.details.BookDetailsScreen(
                onBack = { navController.popBackStack() },
                onRead = { pairId -> navController.navigate(Routes.reader(pairId)) },
                onReadStandalone = { ebookId -> navController.navigate(Routes.readerStandalone(ebookId)) },
                onListen = { pairId -> navController.navigate(Routes.player(pairId)) },
                onListenStandalone = { audiobookId -> navController.navigate(Routes.playerStandalone(audiobookId)) },
            )
        }
        composable(
            Routes.BOOK_DETAILS_EBOOK,
            arguments = listOf(navArgument("ebookId") { type = NavType.IntType }),
        ) {
            com.booksync.ui.details.BookDetailsScreen(
                onBack = { navController.popBackStack() },
                onRead = { pairId -> navController.navigate(Routes.reader(pairId)) },
                onReadStandalone = { ebookId -> navController.navigate(Routes.readerStandalone(ebookId)) },
                onListen = { pairId -> navController.navigate(Routes.player(pairId)) },
                onListenStandalone = { audiobookId -> navController.navigate(Routes.playerStandalone(audiobookId)) },
            )
        }
        composable(
            Routes.BOOK_DETAILS_AUDIOBOOK,
            arguments = listOf(navArgument("audiobookId") { type = NavType.IntType }),
        ) {
            com.booksync.ui.details.BookDetailsScreen(
                onBack = { navController.popBackStack() },
                onRead = { pairId -> navController.navigate(Routes.reader(pairId)) },
                onReadStandalone = { ebookId -> navController.navigate(Routes.readerStandalone(ebookId)) },
                onListen = { pairId -> navController.navigate(Routes.player(pairId)) },
                onListenStandalone = { audiobookId -> navController.navigate(Routes.playerStandalone(audiobookId)) },
            )
        }
    }
}

/**
 * Inner Scaffold for the authenticated area. Hosts the 4-tab bottom nav and pins the
 * [MiniPlayerBar] directly above it so audio controls stay reachable across tabs.
 */
@Composable
private fun MainScaffold(outerNavController: NavHostController) {
    val bottomNavController = rememberNavController()
    val navBackStackEntry by bottomNavController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route
        ?.substringBefore("?") // strip query args so selection matches "library?filter=NEW"

    Scaffold(
        // Don't let the outer shell consume the status-bar inset — each screen's own
        // TopAppBar handles it. Without this, the inner Scaffold's TopAppBar adds status-bar
        // height on top of the outer Scaffold's top padding, causing double status-bar space.
        contentWindowInsets = WindowInsets(0),
        bottomBar = {
            Column {
                MiniPlayerBar(
                    onExpand = { mediaId ->
                        decodePairIdFromMediaId(mediaId)?.let { pairId ->
                            outerNavController.navigate(Routes.player(pairId))
                        }
                    },
                )
                NavigationBar {
                    BOTTOM_TABS.forEach { tab ->
                        NavigationBarItem(
                            icon = { Icon(tab.icon, contentDescription = tab.label) },
                            label = { Text(tab.label) },
                            selected = currentRoute == tab.route,
                            onClick = {
                                bottomNavController.navigate(tab.route) {
                                    popUpTo(bottomNavController.graph.startDestinationId) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            },
                        )
                    }
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = bottomNavController,
            startDestination = Routes.HOME,
            modifier = Modifier.padding(padding),
        ) {
            composable(Routes.HOME) {
                HomeScreen(
                    onSearchClick     = { outerNavController.navigate(Routes.SEARCH) },
                    onOpenPairReader  = { outerNavController.navigate(Routes.reader(it)) },
                    onOpenPairPlayer  = { outerNavController.navigate(Routes.player(it)) },
                    onOpenEbook       = { ebookId -> outerNavController.navigate(Routes.readerStandalone(ebookId)) },
                    onOpenAudiobook   = { outerNavController.navigate(Routes.playerStandalone(it)) },
                    onOpenPairDetails      = { outerNavController.navigate(Routes.bookDetailsPair(it)) },
                    onOpenEbookDetails     = { outerNavController.navigate(Routes.bookDetailsEbook(it)) },
                    onOpenAudiobookDetails = { outerNavController.navigate(Routes.bookDetailsAudiobook(it)) },
                    onSeeAll          = { target ->
                        val libraryRoute = when (target) {
                            HomeSeeAll.CONTINUE        -> Routes.library(sort = "RecentlyOpened")
                            HomeSeeAll.RECENTLY_ADDED  -> Routes.library(sort = "RecentlyAdded")
                            HomeSeeAll.NEW             -> Routes.library(filter = "NEW")
                            HomeSeeAll.QUEUE           -> Routes.library(filter = "NEW")
                        }
                        bottomNavController.navigate(libraryRoute) {
                            popUpTo(bottomNavController.graph.startDestinationId) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    },
                )
            }

            composable(
                route = "${Routes.LIBRARY}?filter={filter}&series={series}&sort={sort}&group={group}",
                arguments = listOf(
                    navArgument("filter") { type = NavType.StringType; nullable = true; defaultValue = null },
                    navArgument("series") { type = NavType.StringType; nullable = true; defaultValue = null },
                    navArgument("sort")   { type = NavType.StringType; nullable = true; defaultValue = null },
                    navArgument("group")  { type = NavType.StringType; nullable = true; defaultValue = null },
                ),
            ) { entry ->
                val args = entry.arguments
                val filterArg = args?.getString("filter")?.let { runCatching { LibraryFilter.valueOf(it) }.getOrNull() }
                val sortArg   = args?.getString("sort")?.let   { runCatching { LibrarySort.valueOf(it)   }.getOrNull() }
                val seriesArg = args?.getString("series")?.let { java.net.URLDecoder.decode(it, "UTF-8") }
                val groupArg  = args?.getString("group")?.equals("series", ignoreCase = true)

                LibraryScreen(
                    onBookSelect             = { outerNavController.navigate(Routes.reader(it)) },
                    onAudioSelect            = { outerNavController.navigate(Routes.player(it)) },
                    onStandaloneAudioSelect  = { outerNavController.navigate(Routes.playerStandalone(it)) },
                    onOpenDetails            = { item ->
                        val route = when {
                            item.pair != null      -> Routes.bookDetailsPair(item.pair.id)
                            item.ebook != null     -> Routes.bookDetailsEbook(item.ebook.id)
                            item.audiobook != null -> Routes.bookDetailsAudiobook(item.audiobook.id)
                            else -> null
                        }
                        route?.let { outerNavController.navigate(it) }
                    },
                    onSearchClick            = { outerNavController.navigate(Routes.SEARCH) },
                    onSettingsClick          = { outerNavController.navigate(Routes.SETTINGS) },
                    initialFilter            = filterArg,
                    initialSeries            = seriesArg,
                    initialSort              = sortArg,
                    initialGroupBySeries     = groupArg,
                )
            }

            composable(Routes.DOWNLOADED) {
                DownloadedScreen(
                    onPairBookSelect  = { outerNavController.navigate(Routes.reader(it)) },
                    onPairAudioSelect = { outerNavController.navigate(Routes.player(it)) },
                    onEbookSelect     = { ebookId -> outerNavController.navigate(Routes.readerStandalone(ebookId)) },
                    onAudiobookSelect = { outerNavController.navigate(Routes.playerStandalone(it)) },
                    onOpenPairDetails     = { outerNavController.navigate(Routes.bookDetailsPair(it)) },
                    onOpenEbookDetails    = { outerNavController.navigate(Routes.bookDetailsEbook(it)) },
                    onOpenAudiobookDetails = { outerNavController.navigate(Routes.bookDetailsAudiobook(it)) },
                )
            }

            composable(Routes.ACCOUNT) {
                AccountScreen(
                    onDiagnosticsAuto   = { outerNavController.navigate(Routes.diagnostics("AUTO")) },
                    onDiagnosticsApp    = { outerNavController.navigate(Routes.diagnostics("APP")) },
                    onClearAllDownloads = { /* wired in a future Phase E.6 */ },
                )
            }
        }
    }
}
