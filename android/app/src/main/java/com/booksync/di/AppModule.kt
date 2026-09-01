package com.booksync.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStore
import androidx.room.Room
import com.booksync.data.local.BookSyncDatabase
import com.booksync.data.local.MIGRATION_12_13
import com.booksync.data.local.dao.*
import com.booksync.BuildConfig
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DictionaryApi
import com.booksync.data.remote.httpLoggingLevel
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
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
import javax.inject.Named
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

    /**
     * The build-time default server URL (`tandem.defaultServerUrl`). Empty unless
     * the build sets it — see the buildConfigField in app/build.gradle.kts.
     */
    @Provides
    @Named(com.booksync.data.remote.DEFAULT_SERVER_URL_QUALIFIER)
    fun provideDefaultServerUrl(): String = com.booksync.BuildConfig.DEFAULT_SERVER_URL

    @Provides
    @Singleton
    fun provideRetryInterceptor(): com.booksync.data.remote.RetryInterceptor {
        return com.booksync.data.remote.RetryInterceptor(maxRetries = 3)
    }

    /**
     * Process-lifetime scope for seeding the singletons that back every request
     * (issue #318). IO because every current use is a DataStore read.
     */
    @Provides
    @Singleton
    @com.booksync.di.ApplicationScope
    fun provideApplicationScope(): kotlinx.coroutines.CoroutineScope =
        kotlinx.coroutines.CoroutineScope(
            kotlinx.coroutines.SupervisorJob() + kotlinx.coroutines.Dispatchers.IO
        )

    @Provides
    @Singleton
    fun provideOkHttpClient(
        baseUrlInterceptor: com.booksync.data.remote.BaseUrlInterceptor,
        authInterceptor: com.booksync.data.remote.AuthInterceptor,
        retryInterceptor: com.booksync.data.remote.RetryInterceptor,
        tokenAuthenticator: com.booksync.data.remote.TokenAuthenticator,
    ): OkHttpClient {
        return OkHttpClient.Builder()
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(300, TimeUnit.SECONDS) // Long timeout for large file downloads
            .writeTimeout(30, TimeUnit.SECONDS)
            // First: everything after it should see the real destination, and the
            // logger should not print the placeholder host (issue #228).
            .addInterceptor(baseUrlInterceptor)
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = httpLoggingLevel(BuildConfig.DEBUG)
            })
            .addInterceptor(retryInterceptor)
            .addInterceptor(authInterceptor)
            // Refresh-on-401 lives here rather than in the interceptor (issue
            // #143): OkHttp calls an authenticator once per 401, from outside the
            // chain, so it cannot re-enter the chain that triggered it.
            .authenticator(tokenAuthenticator)
            .build()
    }

    // ---------------------------------------------------------------------
    // Refresh client (issue #143) — deliberately carries no AuthInterceptor,
    // no RetryInterceptor and no authenticator. Refreshing through the main
    // client is what let a rejected refresh token recurse until every
    // dispatcher thread was parked and no request in the process completed.
    // Same shape as the dictionary client below, for a different reason.
    // ---------------------------------------------------------------------

    @Provides
    @Singleton
    @Named(com.booksync.data.remote.TokenAuthenticator.REFRESH_API)
    fun provideRefreshOkHttpClient(
        baseUrlInterceptor: com.booksync.data.remote.BaseUrlInterceptor,
    ): OkHttpClient =
        OkHttpClient.Builder()
            // The refresh runs under a process-wide mutex, so whatever it waits
            // for, everything else waits for too. Without a call timeout the
            // per-stage timeouts can still add up to 90s against a server that
            // accepts the connection and then says nothing -- an nginx upstream
            // stall does exactly that -- and for those 90s no request in the app
            // completes, which is the symptom #143 was reported for.
            .callTimeout(15, TimeUnit.SECONDS)
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            // The user can change servers mid-session; without this the refresh
            // client would keep renewing tokens against the previous one (#228).
            .addInterceptor(baseUrlInterceptor)
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = httpLoggingLevel(BuildConfig.DEBUG)
            })
            .build()

    @Provides
    @Singleton
    @Named(com.booksync.data.remote.TokenAuthenticator.REFRESH_API)
    fun provideRefreshRetrofit(
        @Named(com.booksync.data.remote.TokenAuthenticator.REFRESH_API) client: OkHttpClient,
        json: Json,
    ): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            // A constant placeholder: BaseUrlInterceptor rewrites scheme/host/port
            // per request, so this is never the address anything reaches (#228).
            .baseUrl(com.booksync.data.remote.UNCONFIGURED_BASE_URL)
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    @Provides
    @Singleton
    @Named(com.booksync.data.remote.TokenAuthenticator.REFRESH_API)
    fun provideAuthRefreshApi(
        @Named(com.booksync.data.remote.TokenAuthenticator.REFRESH_API) retrofit: Retrofit,
    ): com.booksync.data.remote.AuthRefreshApi =
        retrofit.create(com.booksync.data.remote.AuthRefreshApi::class.java)

    @Provides
    @Singleton
    fun provideRetrofit(
        client: OkHttpClient,
        json: Json,
    ): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            // A constant placeholder: BaseUrlInterceptor rewrites scheme/host/port
            // per request, so this is never the address anything reaches (#228).
            .baseUrl(com.booksync.data.remote.UNCONFIGURED_BASE_URL)
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    @Provides
    @Singleton
    fun provideApi(retrofit: Retrofit): BookSyncApi =
        retrofit.create(BookSyncApi::class.java)

    // ---------------------------------------------------------------------
    // Dictionary API (api.dictionaryapi.dev) — separate client so our
    // Bearer JWT doesn't leak to a third-party server.
    // ---------------------------------------------------------------------

    @Provides
    @Singleton
    @Named("dictionary")
    fun provideDictionaryOkHttpClient(): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = httpLoggingLevel(BuildConfig.DEBUG)
            })
            .build()

    @Provides
    @Singleton
    @Named("dictionary")
    fun provideDictionaryRetrofit(
        @Named("dictionary") client: OkHttpClient,
        json: Json,
    ): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl("https://api.dictionaryapi.dev/")
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    @Provides
    @Singleton
    fun provideDictionaryApi(@Named("dictionary") retrofit: Retrofit): DictionaryApi =
        retrofit.create(DictionaryApi::class.java)

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context): BookSyncDatabase =
        Room.databaseBuilder(
            context,
            BookSyncDatabase::class.java,
            "booksync.db"
        ).addMigrations(
            com.booksync.data.local.MIGRATION_12_13,
            com.booksync.data.local.MIGRATION_13_14,
            com.booksync.data.local.MIGRATION_14_15,
            com.booksync.data.local.MIGRATION_15_16,
            com.booksync.data.local.MIGRATION_16_17,
            com.booksync.data.local.MIGRATION_17_18,
            com.booksync.data.local.MIGRATION_18_19,
            com.booksync.data.local.MIGRATION_19_20,
        )
         // Destructive fallback ONLY for pre-position-work installs (< v12,
         // before MIGRATION_12_13 — those versions predate the migration
         // chain entirely). From v12 on, a missing migration must CRASH, not
         // silently drop every table: `bookmarks`/`user_progress` rows with
         // syncedToServer=false and the whole `pending_sync` offline queue
         // are user data the server has never seen (issue #168).
         // MigrationCoverageTest pins the chain and forbids the blanket form.
         .fallbackToDestructiveMigrationFrom(true, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
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
    fun provideAcknowledgedItemDao(db: BookSyncDatabase): AcknowledgedItemDao = db.acknowledgedItemDao()

    @Provides
    fun provideBookmarkLogDao(db: BookSyncDatabase): BookmarkLogDao = db.bookmarkLogDao()

    @Provides
    fun provideScopeAdoptionDao(db: BookSyncDatabase): ScopeAdoptionDao = db.scopeAdoptionDao()

    // Backs both ServerUrlManager and DeviceIdManager, which are each provided via their
    // own @Singleton @Inject constructor (no explicit @Provides needed) — Hilt resolves
    // them automatically once this DataStore binding is available.
    @Provides
    @Singleton
    fun provideDataStore(@ApplicationContext context: Context): DataStore<Preferences> =
        context.dataStore

    @Provides
    @Singleton
    fun provideNetworkMonitor(@ApplicationContext context: Context): NetworkMonitor =
        NetworkMonitor(context)

    @Provides
    @Singleton
    fun provideTranscriptionRepository(
        api: BookSyncApi,
        networkMonitor: NetworkMonitor,
    ): TranscriptionRepository = TranscriptionRepository(api, networkMonitor)
}
