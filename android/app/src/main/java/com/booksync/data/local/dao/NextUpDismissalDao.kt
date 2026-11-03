package com.booksync.data.local.dao

import androidx.room.Dao
import androidx.room.Query
import androidx.room.Transaction
import androidx.room.Upsert
import com.booksync.data.local.entity.NextUpDismissalEntity
import kotlinx.coroutines.flow.Flow

/** Series removed from the Home "Next up" row (issue #923). */
@Dao
interface NextUpDismissalDao {

    @Query("SELECT * FROM next_up_dismissals")
    fun observeAll(): Flow<List<NextUpDismissalEntity>>

    @Query("DELETE FROM next_up_dismissals")
    suspend fun clear()

    @Upsert
    suspend fun insertAll(rows: List<NextUpDismissalEntity>)

    /** The server's list replaces the local one in a single transaction. */
    @Transaction
    suspend fun replaceAll(rows: List<NextUpDismissalEntity>) {
        clear()
        insertAll(rows)
    }

    @Upsert
    suspend fun upsert(row: NextUpDismissalEntity)

    @Query("DELETE FROM next_up_dismissals WHERE seriesKey = :key")
    suspend fun deleteByKey(key: String)
}
