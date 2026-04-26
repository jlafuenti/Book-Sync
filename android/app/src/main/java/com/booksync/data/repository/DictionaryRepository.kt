package com.booksync.data.repository

import com.booksync.data.remote.DictionaryApi
import com.booksync.data.remote.DictionaryEntry
import java.util.LinkedHashMap
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Thin wrapper around [DictionaryApi] with a tiny in-memory LRU cache so repeat
 * lookups of the same word don't hit the network.
 *
 * Cache is process-lifetime only — that's fine for "tap Define on the same word
 * twice in a reading session." Don't bother persisting.
 */
@Singleton
class DictionaryRepository @Inject constructor(
    private val api: DictionaryApi,
) {
    private val cacheCapacity = 16
    private val cache = object : LinkedHashMap<String, List<DictionaryEntry>>(
        cacheCapacity, 0.75f, /* accessOrder = */ true,
    ) {
        override fun removeEldestEntry(eldest: Map.Entry<String, List<DictionaryEntry>>): Boolean =
            size > cacheCapacity
    }

    /**
     * Returns the dictionary entries for [word], or an empty list if the word
     * isn't in the dictionary. Throws on network errors so the caller can
     * distinguish "offline" from "not a real word."
     */
    suspend fun lookup(word: String): List<DictionaryEntry> {
        val key = word.trim().lowercase()
        if (key.isEmpty()) return emptyList()

        synchronized(cache) { cache[key] }?.let { return it }

        val entries = try {
            api.lookup(key)
        } catch (e: retrofit2.HttpException) {
            if (e.code() == 404) emptyList() else throw e
        }

        synchronized(cache) { cache[key] = entries }
        return entries
    }
}
