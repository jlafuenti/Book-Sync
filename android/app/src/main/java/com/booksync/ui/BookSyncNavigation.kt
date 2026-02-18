package com.booksync.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.booksync.ui.auth.LoginScreen
import com.booksync.ui.library.LibraryScreen
import com.booksync.ui.reader.ReaderScreen
import com.booksync.ui.player.PlayerScreen

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
                    navController.navigate("library") {
                        popUpTo("login") { inclusive = true }
                    }
                }
            )
        }

        composable("library") {
            LibraryScreen(
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
