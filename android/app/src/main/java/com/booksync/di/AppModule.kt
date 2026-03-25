package com.booksync.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStore
import androidx.room.Room
import com.booksync.data.local.BookSyncDatabase
import com.booksync.data.local.dao.*
import com.booksync.data.remote.BookSyncApi
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.serialization.json.Json
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import java.util.concurrent.TimeUnit
import javax.inject.Singleton

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "booksync_prefs")

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideJson(): Json = Json {
        ignoreUnknownKeys = true
        coerceInputValues = true
    }

    @Provides
    @Singleton
    fun provideRetryInterceptor(): com.booksync.data.remote.RetryInterceptor {
        return com.booksync.data.remote.RetryInterceptor(maxRetries = 3)
    }

    @Provides
    @Singleton
    fun provideOkHttpClient(
        authInterceptor: com.booksync.data.remote.AuthInterceptor,
        retryInterceptor: com.booksync.data.remote.RetryInterceptor
    ): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(300, TimeUnit.SECONDS) // Long timeout for large file downloads
            .writeTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = HttpLoggingInterceptor.Level.BASIC
            })
            .addInterceptor(retryInterceptor)
            .addInterceptor(authInterceptor)
            .build()
    }

    @Provides
    @Singleton
    fun provideRetrofit(client: OkHttpClient, json: Json): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl("https://booksync.lafuenti.com/") // BookSync server via HTTPS
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    @Provides
    @Singleton
    fun provideApi(retrofit: Retrofit): BookSyncApi =
        retrofit.create(BookSyncApi::class.java)

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context): BookSyncDatabase =
        Room.databaseBuilder(
            context,
            BookSyncDatabase::class.java,
            "booksync.db"
        ).fallbackToDestructiveMigration(dropAllTables = true)
         .build()

    @Provides
    fun provideBookPairDao(db: BookSyncDatabase): BookPairDao = db.bookPairDao()

    @Provides
    fun provideEBookDao(db: BookSyncDatabase): EBookDao = db.eBookDao()

    @Provides
    fun provideAudioBookDao(db: BookSyncDatabase): AudioBookDao = db.audioBookDao()

    @Provides
    fun provideSyncPointDao(db: BookSyncDatabase): SyncPointDao = db.syncPointDao()

    @Provides
    fun provideBookmarkDao(db: BookSyncDatabase): BookmarkDao = db.bookmarkDao()

    @Provides
    fun providePendingSyncDao(db: BookSyncDatabase): PendingSyncDao = db.pendingSyncDao()

    @Provides
    fun provideUserProgressDao(db: BookSyncDatabase): UserProgressDao = db.userProgressDao()

    @Provides
    @Singleton
    fun provideDataStore(@ApplicationContext context: Context): DataStore<Preferences> =
        context.dataStore
}
