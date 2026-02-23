package com.booksync.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.booksync.ui.auth.LoginScreen
import com.booksync.ui.library.LibraryScreen
import com.booksync.ui.library.SearchScreen
import com.booksync.ui.reader.ReaderScreen
import com.booksync.ui.player.PlayerScreen
import com.booksync.ui.ebooks.EbooksScreen
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.LibraryBooks
import androidx.compose.material.icons.filled.Book
import androidx.compose.material3.*
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.navigation.compose.currentBackStackEntryAsState

/**
 * Navigation graph for the BookSync app.
 */
@Composable
fun BookSyncNavigation() {
    val navController = rememberNavController()

    NavHost(navController = navController, startDestination = "login") {

        composable("login") {
            LoginScreen(
                onLoginSuccess = {
                    navController.navigate("main") {
                        popUpTo("login") { inclusive = true }
                    }
                }
            )
        }

        composable("main") {
            val bottomNavController = rememberNavController()
            val navBackStackEntry by bottomNavController.currentBackStackEntryAsState()
            val currentRoute = navBackStackEntry?.destination?.route

            Scaffold(
                bottomBar = {
                    NavigationBar {
                        NavigationBarItem(
                            icon = { Icon(Icons.Default.LibraryBooks, contentDescription = "Pairs") },
                            label = { Text("Pairs") },
                            selected = currentRoute == "library",
                            onClick = {
                                bottomNavController.navigate("library") {
                                    popUpTo(bottomNavController.graph.startDestinationId) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        )
                        NavigationBarItem(
                            icon = { Icon(Icons.Default.Book, contentDescription = "Ebooks") },
                            label = { Text("Ebooks") },
                            selected = currentRoute == "ebooks",
                            onClick = {
                                bottomNavController.navigate("ebooks") {
                                    popUpTo(bottomNavController.graph.startDestinationId) {
                                        saveState = true
                                    }
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        )
                    }
                }
            ) { padding ->
                NavHost(navController = bottomNavController, startDestination = "library", modifier = Modifier.padding(padding)) {
                    composable("library") {
                        LibraryScreen(
                            onBookSelect = { pairId ->
                                navController.navigate("reader/$pairId")
                            },
                            onAudioSelect = { pairId ->
                                navController.navigate("player/$pairId")
                            },
                            onSearchClick = {
                                navController.navigate("search")
                            }
                        )
                    }
                    composable("ebooks") {
                        EbooksScreen(
                            onBookSelect = { ebookId ->
                            },
                            onSearchClick = {
                                navController.navigate("search")
                            }
                        )
                    }
                }
            }
        }

        composable("search") {
            SearchScreen(
                onBack = { navController.popBackStack() },
                onBookSelect = { pairId ->
                    navController.navigate("reader/$pairId")
                },
                onAudioSelect = { pairId ->
                    navController.navigate("player/$pairId")
                }
            )
        }

        composable(
            "reader/{pairId}",
            arguments = listOf(navArgument("pairId") { type = NavType.IntType })
        ) { backStackEntry ->
            val pairId = backStackEntry.arguments?.getInt("pairId") ?: return@composable
            ReaderScreen(
                pairId = pairId,
                onBack = { navController.popBackStack() },
                onSwitchToAudio = {
                    navController.navigate("player/$pairId") {
                        popUpTo("library")
                    }
                }
            )
        }

        composable(
            "player/{pairId}",
            arguments = listOf(navArgument("pairId") { type = NavType.IntType })
        ) { backStackEntry ->
            val pairId = backStackEntry.arguments?.getInt("pairId") ?: return@composable
            PlayerScreen(
                pairId = pairId,
                onBack = { navController.popBackStack() },
                onSwitchToReader = {
                    navController.navigate("reader/$pairId") {
                        popUpTo("library")
                    }
                }
            )
        }
    }
}
