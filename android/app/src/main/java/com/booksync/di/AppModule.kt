package com.booksync.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStore
import androidx.room.Room
import com.booksync.data.local.BookSyncDatabase
import com.booksync.data.local.corruptionLoggingOpenHelperFactory
import com.booksync.data.local.MIGRATION_12_13
import com.booksync.data.local.dao.*
import com.booksync.BuildConfig
import com.booksync.data.remote.BookSyncApi
import com.booksync.data.remote.DictionaryApi
import com.booksync.data.remote.WiktionaryApi
import com.booksync.data.remote.wiktionaryUserAgent
import com.booksync.data.remote.httpLoggingLevel
import com.booksync.data.repository.TranscriptionRepository
import com.booksync.data.util.NetworkMonitor
import com.booksync.diagnostics.DiagnosticLogger
import com.booksync.diagnostics.LogChannel
import com.jakewharton.retrofit2.converter.kotlinx.serialization.asConverterFactory
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.flow.map
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

    /**
     * The public demo login this build carries, or null (issue #147).
     *
     * Null in a clean clone — all three build settings default to empty — and
     * the first-run screen then offers no demo button. Assembled here rather
     * than read from `BuildConfig` in the ViewModel so "there is no demo" is an
     * ordinary injectable state a unit test can set.
     *
     * **This is the only optional thing in the demo wiring, and deliberately so.**
     * `DemoSignIn` is bound by its own `@Inject` constructor and injected
     * unconditionally; it is inert without an account, because nothing can start
     * it (`LoginViewModel.signInToDemo` returns early on a null account). The
     * version that tried to make it optional too —
     * `provideDemoSignIn(account: DemoAccount?, signIn: Provider<DemoSignIn>)` —
     * crashed every launch with a `StackOverflowError`: Dagger's key ignores
     * Kotlin nullability, so that function *was* the binding for `DemoSignIn`,
     * and asking it for a `Provider<DemoSignIn>` re-entered it forever. The
     * `Provider` indirection hides the cycle from Dagger's compile-time check,
     * so nothing failed the build. `DiGraphWiringTest` is the guard.
     */
    @Provides
    @Singleton
    fun provideDemoAccount(): com.booksync.data.remote.DemoAccount? =
        com.booksync.data.remote.demoAccountOrNull(
            url = com.booksync.BuildConfig.DEMO_URL,
            username = com.booksync.BuildConfig.DEMO_USER,
            password = com.booksync.BuildConfig.DEMO_PASSWORD,
        )

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
    // Dictionary lookup (issue #608) — Wiktionary (primary) and
    // api.dictionaryapi.dev (fallback for words Wiktionary's English section
    // doesn't cover). Separate clients, neither carrying our Bearer JWT, so
    // it can't leak to either third-party server. Timeouts raised modestly
    // from the original 10s: dictionaryapi.dev was measured taking ~20s to
    // respond on a bad day, and the old 10s timeout fired before that reply
    // ever arrived — see [com.booksync.data.repository.DictionaryRepository]
    // for the retry-once-then-fall-back behavior this pairs with.
    // ---------------------------------------------------------------------

    @Provides
    @Singleton
    @Named("wiktionary")
    fun provideWiktionaryOkHttpClient(): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            // Wikimedia answers 403 to OkHttp's default agent string — see
            // [com.booksync.data.remote.wiktionaryUserAgent].
            .addInterceptor { chain ->
                chain.proceed(
                    chain.request().newBuilder()
                        .header("User-Agent", wiktionaryUserAgent(BuildConfig.VERSION_NAME))
                        .build(),
                )
            }
            .addInterceptor(HttpLoggingInterceptor().apply {
                level = httpLoggingLevel(BuildConfig.DEBUG)
            })
            .build()

    @Provides
    @Singleton
    @Named("wiktionary")
    fun provideWiktionaryRetrofit(
        @Named("wiktionary") client: OkHttpClient,
        json: Json,
    ): Retrofit {
        val contentType = "application/json".toMediaType()
        return Retrofit.Builder()
            .baseUrl("https://en.wiktionary.org/")
            .client(client)
            .addConverterFactory(json.asConverterFactory(contentType))
            .build()
    }

    @Provides
    @Singleton
    fun provideWiktionaryApi(@Named("wiktionary") retrofit: Retrofit): WiktionaryApi =
        retrofit.create(WiktionaryApi::class.java)

    @Provides
    @Singleton
    @Named("dictionary")
    fun provideDictionaryOkHttpClient(): OkHttpClient =
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
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
    fun provideDatabase(
        @ApplicationContext context: Context,
        diagnosticLogger: DiagnosticLogger,
    ): BookSyncDatabase =
        Room.databaseBuilder(
            context,
            BookSyncDatabase::class.java,
            "booksync.db"
        )
         // Issue #364: when SQLite reports corruption the platform deletes the
         // file and Room opens an empty one, taking every unsynced position and
         // the whole offline queue with it and saying nothing. This does not
         // stop the deletion — refusing it would leave the app unable to open
         // its own database — it writes one line to the app diagnostics log so
         // the loss is explainable afterwards. Unconditional on purpose: nobody
         // has diagnostics capture running when this fires.
         .openHelperFactory(
             corruptionLoggingOpenHelperFactory(
                 onCorruption = { line -> diagnosticLogger.recordAlways(LogChannel.APP, line) },
             ),
         )
         .addMigrations(
            com.booksync.data.local.MIGRATION_12_13,
            com.booksync.data.local.MIGRATION_13_14,
            com.booksync.data.local.MIGRATION_14_15,
            com.booksync.data.local.MIGRATION_15_16,
            com.booksync.data.local.MIGRATION_16_17,
            com.booksync.data.local.MIGRATION_17_18,
            com.booksync.data.local.MIGRATION_18_19,
            com.booksync.data.local.MIGRATION_19_20,
            com.booksync.data.local.MIGRATION_20_21,
            com.booksync.data.local.MIGRATION_21_22,
            com.booksync.data.local.MIGRATION_22_23,
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

    @Provides
    fun provideLibraryCacheDao(db: BookSyncDatabase): LibraryCacheDao = db.libraryCacheDao()

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
        bookPairDao: BookPairDao,
    ): TranscriptionRepository = TranscriptionRepository(api, networkMonitor, bookPairDao)

    /**
     * Whether a session is currently signed in, as a `Flow<Boolean>` (issue #641).
     * Derived from [com.booksync.data.remote.TokenManager]'s existing stored-token
     * flow rather than a new persistence mechanism — this is the same signal
     * `BookSyncNavigation` already watches to bounce to the login screen on
     * sign-out. Exposed as a qualified `Flow<Boolean>` rather than injecting
     * `TokenManager` itself into [com.booksync.data.repository.LibraryLoader]:
     * that class needs to be constructible on the JVM test path without a live
     * `DataStore`, and all it actually needs from `TokenManager` is this one bit.
     */
    @Provides
    @Named(com.booksync.data.repository.SIGNED_IN_FLOW_QUALIFIER)
    fun provideSignedInFlow(
        tokenManager: com.booksync.data.remote.TokenManager,
    ): kotlinx.coroutines.flow.Flow<Boolean> =
        tokenManager.getAccessToken().map { !it.isNullOrEmpty() }

    /**
     * The guided-walkthrough engine (issue #597, revised by #642). A plain
     * `@Provides` rather than an `@Inject constructor` on
     * [com.booksync.ui.tour.TourController]: that class's `clock`/`settleMs`/
     * `hardCapMs`/`awaitLibrary`/`libraryWaitMs` constructor parameters carry
     * Kotlin default values for testability, but Dagger's generated factory
     * does not honour Kotlin defaults — every constructor parameter becomes a
     * required binding, and there is no `@Provides` for a bare `Long` or a
     * `() -> Long`. Calling the constructor directly here, as ordinary Kotlin
     * code, is what lets the defaults apply in production.
     *
     * `awaitLibrary` is the one parameter that must *not* be left at its
     * (always-ready) default here (issue #641): a walkthrough accepted right
     * after a fresh sign-in would otherwise pick its book from a Room cache the
     * first fetch has not filled yet, find nothing, and collapse the whole book
     * section into the "Skipped" card. Pairs are all the picker reads, so it
     * waits for that stage only. Pinned by `TourLibraryWiringTest`.
     */
    @Provides
    @Singleton
    fun provideTourController(
        registry: com.booksync.ui.tour.TourAnchorRegistry,
        prefs: com.booksync.ui.tour.TourPrefs,
        picker: com.booksync.ui.tour.TourPairPicker,
        @ApplicationScope scope: kotlinx.coroutines.CoroutineScope,
        loader: com.booksync.data.repository.LibraryLoader,
    ): com.booksync.ui.tour.TourController =
        com.booksync.ui.tour.TourController(
            registry = registry,
            prefs = prefs,
            picker = picker,
            scope = scope,
            awaitLibrary = { timeoutMs -> loader.awaitPairs(timeoutMs) },
        )
}
