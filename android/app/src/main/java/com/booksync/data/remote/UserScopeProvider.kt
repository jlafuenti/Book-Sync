package com.booksync.data.remote

import com.booksync.data.local.dao.ScopeAdoptionDao
import kotlinx.coroutines.runBlocking
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Resolves which account the local cache is being read and written as (issue #314).
 *
 * Replaces the earlier "clear the previous user's rows when someone else signs in".
 * That was the wrong primitive twice over: it deleted `syncedToServer = 0` rows the
 * server has never seen — destroying reading positions to prevent them leaking —
 * and it only protected the tables it remembered to clear, while three other push
 * paths went on replaying whatever they found under the current token.
 *
 * Partitioning removes both problems. Nothing is deleted; the wrong account simply
 * cannot see or replay rows that are not its own, and that holds for every query
 * rather than every query someone remembered.
 *
 * The scope is (server, user), never user alone — see [UserScope].
 */
@Singleton
class UserScopeProvider @Inject constructor(
    private val tokenManager: TokenManager,
    private val serverUrlManager: ServerUrlManager,
    private val scopeAdoptionDao: ScopeAdoptionDao,
) {
    /**
     * The current scope, readable without suspending.
     *
     * Same shape as `ServerUrlManager.currentUrl` and for the same reason: the
     * scope is needed inside non-suspend `Flow` builders and at use-time in code
     * that cannot suspend. Resolved once at construction and refreshed whenever a
     * session is established; `@Volatile` publishes the write to the reader
     * threads (OkHttp, WorkManager, the main thread).
     *
     * Null means "no resolvable account" and must never be treated as "all rows".
     */
    @Volatile
    var currentKey: String? = null
        private set

    init {
        currentKey = runBlocking { current()?.key }
    }

    /**
     * The scope for the current session, or null when either half is unknown.
     *
     * Null is not a fallback to "everything" — callers must hold writes and skip
     * reads rather than attribute them to a guess. That is the whole failure mode
     * this issue is about.
     */
    suspend fun current(): UserScope? =
        UserScope.of(serverUrlManager.currentUrl, tokenManager.currentUserId())

    /**
     * Claim rows written before scoping existed, if any remain.
     *
     * Idempotent and cheap: the existence check short-circuits on every launch
     * after the first, so this can be called from the authentication path without
     * a flag to remember whether it has run.
     */
    suspend fun adoptLegacyRowsIfAny(scope: UserScope) {
        if (scope == UserScope.LEGACY) return
        if (!scopeAdoptionDao.hasLegacyBookmarks()) return
        scopeAdoptionDao.adoptAll(scope.key)
    }

    /**
     * Resolve, publish and adopt. Called from the login path *and* from app start
     * — app start matters because an install upgrading into this build has never
     * run the login path, so nothing would ever claim its legacy rows.
     */
    suspend fun onAuthenticated() {
        val scope = current()
        currentKey = scope?.key
        scope?.let { adoptLegacyRowsIfAny(it) }
    }
}
