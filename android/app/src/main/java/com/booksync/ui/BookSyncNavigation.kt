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
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
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
import com.booksync.ui.library.LibraryFilter
import com.booksync.ui.library.LibraryScreen
import com.booksync.ui.library.LibrarySort
import com.booksync.ui.library.SearchScreen
import com.booksync.ui.player.PlayerScreen
import com.booksync.ui.reader.ReaderScreen
import com.booksync.ui.account.AccountScreen
import dagger.hilt.android.EntryPointAccessors
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.firstOrNull

/**
 * Hilt entry point to access TokenManager from a Composable context.
 */
@dagger.hilt.EntryPoint
@dagger.hilt.InstallIn(dagger.hilt.components.SingletonComponent::class)
interface TokenManagerEntryPoint {
    fun tokenManager(): TokenManager
}

// ------------------------------------------------------------
// Route constants — centralized so deep links stay consistent.
// Library accepts optional filter / series / sort / group args.
// ------------------------------------------------------------

object Routes {
    const val LOGIN       = "login"
    const val MAIN        = "main"
    const val HOME        = "home"
    const val LIBRARY     = "library"
    const val DOWNLOADED  = "downloaded"
    const val ACCOUNT     = "account"
    const val SEARCH      = "search"
    const val SETTINGS    = "settings"                // kept for legacy intents
    const val DIAGNOSTICS = "diagnostics/{channel}"
    const val READER               = "reader/{pairId}"
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

    fun reader(pairId: Int)  = "reader/$pairId"
    fun player(pairId: Int)  = "player/$pairId"
    fun playerStandalone(audiobookId: Int) = "player/standalone/$audiobookId"
    fun diagnostics(channel: String) = "diagnostics/$channel"
    fun bookDetailsPair(pairId: Int)           = "book_details/pair/$pairId"
    fun bookDetailsEbook(ebookId: Int)         = "book_details/ebook/$ebookId"
    fun bookDetailsAudiobook(audiobookId: Int) = "book_details/audiobook/$audiobookId"
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

    val tokenManager = remember {
        EntryPointAccessors.fromApplication(
            context.applicationContext,
            TokenManagerEntryPoint::class.java,
        ).tokenManager()
    }

    // Resolve start destination based on persisted auth token
    var startDestination by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        val existingToken = tokenManager.getAccessToken().firstOrNull()
        startDestination = if (!existingToken.isNullOrEmpty()) Routes.MAIN else Routes.LOGIN
    }

    // Observe token clears (logout / expiry) and bounce to login
    LaunchedEffect(Unit) {
        tokenManager.getAccessToken()
            .drop(1)
            .collect { token ->
                if (token.isNullOrEmpty()) {
                    navController.navigate(Routes.LOGIN) {
                        popUpTo(0) { inclusive = true }
                    }
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

        composable(Routes.SEARCH) {
            SearchScreen(
                onBack = { navController.popBackStack() },
                onBookSelect = { pairId -> navController.navigate(Routes.reader(pairId)) },
                onAudioSelect = { pairId -> navController.navigate(Routes.player(pairId)) },
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
            arguments = listOf(navArgument("pairId") { type = NavType.IntType }),
        ) { backStackEntry ->
            val pairId = backStackEntry.arguments?.getInt("pairId") ?: return@composable
            ReaderScreen(
                pairId = pairId,
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
                onSwitchToReader = {
                    navController.navigate(Routes.reader(pairId)) {
                        popUpTo(Routes.MAIN)
                    }
                },
            )
        }

        // Standalone audiobook player — no pair, no reader switch.
        // The audiobookId arg is read by PlayerViewModel from its SavedStateHandle.
        composable(
            Routes.PLAYER_STANDALONE,
            arguments = listOf(navArgument("audiobookId") { type = NavType.IntType }),
        ) {
            PlayerScreen(
                pairId = -1,            // sentinel: no pair (ViewModel uses audiobookId from SavedStateHandle)
                onBack = { navController.popBackStack() },
                onSwitchToReader = { }, // not applicable for standalone
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
                    onOpenEbook       = { /* standalone ebook reader — no pair-based reader support yet */ },
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
                    onEbookSelect     = { /* standalone ebook reader — no pair-based reader support yet */ },
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
