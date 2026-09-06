package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.AcknowledgedItemDao
import com.booksync.data.local.dao.AudioBookDao
import com.booksync.data.local.dao.BookPairDao
import com.booksync.data.local.dao.BookmarkDao
import com.booksync.data.local.dao.BookmarkLogDao
import com.booksync.data.local.dao.EBookDao
import com.booksync.data.local.dao.PendingSyncDao
import com.booksync.data.local.dao.SyncPointDao
import com.booksync.data.local.dao.UserProgressDao
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DeviceIdManager
import com.booksync.data.remote.UserScopeProvider
import com.booksync.diagnostics.DiagnosticLogger
import io.mockk.mockk
import kotlinx.serialization.json.Json

/**
 * One place to construct a [BookSyncRepository] for a unit test (issue #224).
 *
 * Every argument defaults to what the repository tests were already passing —
 * a relaxed mock for each collaborator, the app's `ignoreUnknownKeys` [Json],
 * and a signed-in [testScopeProvider] — so a test names only the collaborators
 * it is actually about. Adding a constructor dependency to the repository is
 * then a one-line change here rather than an edit in every test file.
 */
internal fun buildRepository(
    api: BookSyncApi = mockk(relaxed = true),
    bookPairDao: BookPairDao = mockk(relaxed = true),
    eBookDao: EBookDao = mockk(relaxed = true),
    audioBookDao: AudioBookDao = mockk(relaxed = true),
    syncPointDao: SyncPointDao = mockk(relaxed = true),
    bookmarkDao: BookmarkDao = mockk(relaxed = true),
    pendingSyncDao: PendingSyncDao = mockk(relaxed = true),
    userProgressDao: UserProgressDao = mockk(relaxed = true),
    acknowledgedItemDao: AcknowledgedItemDao = mockk(relaxed = true),
    bookmarkLogDao: BookmarkLogDao = mockk(relaxed = true),
    context: Context = mockk(relaxed = true),
    diagnosticLogger: DiagnosticLogger = mockk(relaxed = true),
    deviceIdManager: DeviceIdManager = mockk(relaxed = true),
    json: Json = Json { ignoreUnknownKeys = true },
    userScopeProvider: UserScopeProvider = testScopeProvider(),
): BookSyncRepository = BookSyncRepository(
    api = api,
    bookPairDao = bookPairDao,
    eBookDao = eBookDao,
    audioBookDao = audioBookDao,
    syncPointDao = syncPointDao,
    bookmarkDao = bookmarkDao,
    pendingSyncDao = pendingSyncDao,
    userProgressDao = userProgressDao,
    acknowledgedItemDao = acknowledgedItemDao,
    bookmarkLogDao = bookmarkLogDao,
    context = context,
    diagnosticLogger = diagnosticLogger,
    deviceIdManager = deviceIdManager,
    json = json,
    userScopeProvider = userScopeProvider,
)
