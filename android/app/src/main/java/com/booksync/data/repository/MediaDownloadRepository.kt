package com.booksync.data.repository

import android.content.Context
import com.booksync.data.local.dao.*
import com.booksync.data.util.localFileName
import com.booksync.data.local.entity.*
import com.booksync.data.remote.*
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import javax.inject.Inject
import javax.inject.Singleton

private const val REPO_TAG = "MediaDownloadRepository"

/**
 * Media files on disk and the cached sync map (issue #224): streaming a book's
 * ebook / audiobook into the app's files directory, the sync-map download the
 * reader and player depend on, and the path sanitising of issue #177 that
 * keeps a server-supplied filename inside its directory.
 *
 * `worker/DownloadWorker` drives the four download entry points and
 * [downloadSyncMapWithRetry]; the reader, player and Android Auto call
 * [ensureSyncMapCached] before reading sync points. [BookSyncRepository]
 * delegates to this class so existing callers are unchanged.
 */
@Singleton
class MediaDownloadRepository @Inject constructor(
    private val api: BookSyncApi,
    private val bookPairDao: BookPairDao,
    private val eBookDao: EBookDao,
    private val audioBookDao: AudioBookDao,
    private val syncPointDao: SyncPointDao,
    @param:ApplicationContext private val context: Context,
    private val diagnosticLogger: DiagnosticLogger,
) {
    private fun log(msg: String) = diagnosticLogger.i(LogChannel.APP, REPO_TAG, msg)
    private fun logW(msg: String) = diagnosticLogger.w(LogChannel.APP, REPO_TAG, msg)
    private fun logE(msg: String, t: Throwable? = null) = diagnosticLogger.e(LogChannel.APP, REPO_TAG, msg, t)

    /**
     * Shared streaming core for the four download entry points (issue #160):
     * status check, throw on empty body, buffered copy with de-duplicated
     * progress. The four functions were hand-copied ~35-line loops that had
     * already drifted — `downloadStandaloneAudiobook` lost the status check
     * entirely, so a 401/404/500 wrote nothing yet still flipped the
     * downloaded flag, and `downloadEbook` lost the progress de-dup.
     *
     * Streams into a `.part` sibling and renames on completion, so an
     * interrupted transfer never leaves a truncated file under the real name
     * (the player and buildCastMediaItem only check the file exists).
     */
    private suspend fun streamToFile(
        response: retrofit2.Response<okhttp3.ResponseBody>,
        target: File,
        onProgress: (Int) -> Unit,
    ): File {
        if (!response.isSuccessful) {
            logE("download failed — HTTP ${response.code()} for ${target.name}")
            throw Exception("HTTP ${response.code()}: ${response.errorBody()?.string()}")
        }
        val body = response.body() ?: throw Exception("Empty response body")
        target.parentFile?.mkdirs()
        val part = File(target.parentFile, target.name + ".part")
        withContext(Dispatchers.IO) {
            try {
                val contentLength = body.contentLength()
                body.byteStream().use { input ->
                    part.outputStream().use { output ->
                        val buffer = ByteArray(8 * 1024)
                        var bytesCopied = 0L
                        var lastProgress = -1
                        var bytes = input.read(buffer)
                        while (bytes >= 0) {
                            output.write(buffer, 0, bytes)
                            bytesCopied += bytes
                            if (contentLength > 0) {
                                val progress = (bytesCopied * 100 / contentLength).toInt()
                                if (progress != lastProgress) {
                                    lastProgress = progress
                                    onProgress(progress)
                                }
                            }
                            bytes = input.read(buffer)
                        }
                    }
                }
                if (!part.renameTo(target)) {
                    part.copyTo(target, overwrite = true)
                    part.delete()
                }
            } catch (e: Exception) {
                part.delete()
                throw e
            }
        }
        return target
    }

    /** Download the ebook file for a book pair. */
    suspend fun downloadEbook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadEbook — pairId=${pair.id} file=${pair.ebookFilename}")
        val file = streamToFile(
            api.downloadEbook(pair.ebookId),
            requireLocalFile("ebooks", pair.ebookFilename),
            onProgress,
        )
        bookPairDao.setEbookDownloaded(pair.id, true)
        log("downloadEbook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone ebook file. */
    suspend fun downloadStandaloneEbook(ebook: EBookEntity, onProgress: (Int) -> Unit = {}): File {
        val file = streamToFile(
            api.downloadEbook(ebook.id),
            requireLocalFile("ebooks", ebook.filename),
            onProgress,
        )
        eBookDao.setDownloaded(ebook.id, true)
        return file
    }

    /** Download the audiobook file for a book pair. */
    suspend fun downloadAudiobook(pair: BookPairEntity, onProgress: (Int) -> Unit = {}): File {
        log("downloadAudiobook — pairId=${pair.id} file=${pair.audiobookFilename}")
        val file = streamToFile(
            api.downloadAudiobook(pair.audiobookId),
            requireLocalFile("audiobooks", pair.audiobookFilename),
            onProgress,
        )
        bookPairDao.setAudiobookDownloaded(pair.id, true)
        log("downloadAudiobook complete — ${file.length() / 1024}KB")
        return file
    }

    /** Download a standalone audiobook file. */
    suspend fun downloadStandaloneAudiobook(audio: AudioBookEntity, onProgress: (Int) -> Unit = {}): File {
        val file = streamToFile(
            api.downloadAudiobook(audio.id),
            requireLocalFile("audiobooks", audio.filename),
            onProgress,
        )
        audioBookDao.setDownloaded(audio.id, true)
        return file
    }

    /** Reset the syncMapDownloaded flag so a re-download is triggered. */
    suspend fun resetSyncMapDownloaded(pairId: Int) {
        // Clear the version alongside the flag: a cache marked absent that still
        // claims a version would tell `refreshPairs` it is current.
        bookPairDao.setSyncMapCached(pairId, false, null)
    }

    /** Download the sync map for a book pair. */
    suspend fun downloadSyncMap(pairId: Int) {
        log("downloadSyncMap — pairId=$pairId")
        val syncMap = api.getSyncMap(pairId)

        // Clear old sync points
        syncPointDao.deletePointsForPair(pairId)

        // Save new sync points
        val entities = syncMap.sync_points.map { point ->
            SyncPointEntity(
                bookPairId = pairId,
                epubChapter = point.epub_chapter,
                epubSentenceIndex = point.epub_sentence_index,
                epubTextPreview = point.epub_text_preview,
                audioStartMs = point.audio_start_ms,
                audioEndMs = point.audio_end_ms,
                confidence = point.confidence,
            )
        }
        syncPointDao.insertPoints(entities)
        // Stamp the version in the same statement that marks the cache present:
        // a cache that reads as downloaded but doesn't say which map it holds is
        // exactly the state issue #55 is about.
        bookPairDao.setSyncMapCached(pairId, true, syncMap.version)
        log("downloadSyncMap complete — ${entities.size} sync points saved (v${syncMap.version})")
    }

    /**
     * Make sure this pair's cached sync points are present, fetching them if a
     * version bump caused [refreshPairs] to drop them. Best-effort: returns
     * false and never throws when the map can't be fetched.
     *
     * Called from the paths that are about to *read* sync points — the reader,
     * the player, Android Auto resume — so recovery from a re-transcription
     * needs no user action. Cheap when the cache is current: one Room read.
     *
     * Deliberately a *single* attempt rather than [downloadSyncMapWithRetry]:
     * these callers are opening a screen or starting playback, and the retry
     * wrapper's 1s/3s/10s backoff would sit in front of that. Persistence is the
     * `DownloadWorker`'s job; here a miss just means the next open tries again.
     */
    suspend fun ensureSyncMapCached(pairId: Int): Boolean {
        if (bookPairDao.getPairById(pairId)?.syncMapDownloaded == true) return true
        log("ensureSyncMapCached — pair $pairId has no cached sync map; fetching")
        return try {
            downloadSyncMap(pairId)
            true
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            logW("ensureSyncMapCached — pair $pairId fetch failed: ${e.message}")
            false
        }
    }

    /**
     * Best-effort sync-map fetch with exponential backoff (1s/3s/10s).
     * Returns true when the sync map landed, false when the server has no sync map
     * yet (404) or every attempt failed. Never throws — callers treat a false
     * return as "try again later" and move on.
     */
    suspend fun downloadSyncMapWithRetry(pairId: Int): Boolean {
        val delaysMs = longArrayOf(1_000L, 3_000L, 10_000L)
        repeat(delaysMs.size) { attempt ->
            try {
                downloadSyncMap(pairId)
                return true
            } catch (e: retrofit2.HttpException) {
                if (e.code() == 404) {
                    log("downloadSyncMapWithRetry — 404 for pair $pairId; sync map not ready yet")
                    return false
                }
                log("downloadSyncMapWithRetry — HTTP ${e.code()} (attempt ${attempt + 1}/${delaysMs.size}): ${e.message()}")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log("downloadSyncMapWithRetry — error (attempt ${attempt + 1}/${delaysMs.size}): ${e.message}")
            }
            if (attempt < delaysMs.size - 1) kotlinx.coroutines.delay(delaysMs[attempt])
        }
        return false
    }

    // ---------------------------------------------------------------------
    // Local file paths (issue #177)
    //
    // Every one of these names arrives in the server's JSON. Joined verbatim
    // they were a path traversal: "../datastore/booksync_prefs.preferences_pb"
    // as a filename let a hostile or mistyped server overwrite the token store
    // or the Room database with a book body, and the delete paths would remove
    // any file under filesDir. Writes cannot escape the sandbox on Android 10+,
    // so the blast radius is the app's own data — still the first thing a
    // reviewer files against a public repo.
    //
    // An honest server only ever sends basenames, so this is an identity
    // mapping for every file already on disk: no migration, and nothing that
    // works today stops working.
    // ---------------------------------------------------------------------

    /** [localFile] for the ebooks directory. Null when the name is not a plain filename. */
    fun localEbookFile(serverFilename: String): File? = localFile("ebooks", serverFilename)

    /** [localFile] for the audiobooks directory. Null when the name is not a plain filename. */
    fun localAudioFile(serverFilename: String): File? = localFile("audiobooks", serverFilename)

    /**
     * Resolve a server-supplied filename inside [dirName], or null.
     *
     * The containment assertion is belt and braces: [localFileName] has already
     * rejected anything with a separator, so a canonical path outside the
     * directory should be unreachable. That is exactly why it is worth
     * asserting — if the platform ever disagrees with the string checks, this
     * catches it rather than trusting them.
     */
    private fun localFile(dirName: String, serverFilename: String): File? {
        val safe = localFileName(serverFilename) ?: run {
            logW("rejected server filename for $dirName: ${serverFilename.take(80)}")
            return null
        }
        val dir = File(context.filesDir, dirName)
        val file = File(dir, safe)
        val root = dir.canonicalPath + File.separator
        if (!file.canonicalPath.startsWith(root)) {
            logW("path escaped $dirName after sanitising: ${serverFilename.take(80)}")
            return null
        }
        return file
    }

    /**
     * A path that cannot exist, for read accessors whose callers expect a
     * non-null File and already handle "the file is not there". Better than
     * throwing on a cover lookup or an artwork refresh.
     */
    private fun unusableFile(dirName: String): File =
        File(File(context.filesDir, dirName), ".rejected-by-localFileName")

    /** As [localEbookFile]/[localAudioFile], for write paths that must fail loudly. */
    private fun requireLocalFile(dirName: String, serverFilename: String): File =
        localFile(dirName, serverFilename)
            ?: throw IllegalArgumentException(
                "Server sent an unusable $dirName filename; refusing to write outside $dirName"
            )

    fun getEbookFile(pair: BookPairEntity): File =
        localEbookFile(pair.ebookFilename) ?: unusableFile("ebooks")

    fun getAudiobookFile(pair: BookPairEntity): File =
        localAudioFile(pair.audiobookFilename) ?: unusableFile("audiobooks")

    /**
     * The same file [downloadStandaloneEbook] writes — the standalone reader
     * needs to open it (issue #169). Kept beside [getEbookFile] so the two
     * naming schemes stay visibly identical; `deleteStandaloneEbook` below
     * builds the same path.
     */
    fun getStandaloneEbookFile(ebook: EBookEntity): File =
        localEbookFile(ebook.filename) ?: unusableFile("ebooks")

    suspend fun deleteEbook(pair: BookPairEntity) {
        getEbookFile(pair).delete()
        bookPairDao.setEbookDownloaded(pair.id, false)
    }

    suspend fun deleteAudiobook(pair: BookPairEntity) {
        getAudiobookFile(pair).delete()
        bookPairDao.setAudiobookDownloaded(pair.id, false)
    }

    suspend fun deleteStandaloneEbook(ebook: EBookEntity) {
        localEbookFile(ebook.filename)?.delete()
        eBookDao.setDownloaded(ebook.id, false)
    }

    suspend fun deleteStandaloneAudiobook(audio: AudioBookEntity) {
        localAudioFile(audio.filename)?.delete()
        audioBookDao.setDownloaded(audio.id, false)
    }
}
