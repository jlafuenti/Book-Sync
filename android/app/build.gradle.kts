import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.dagger.hilt.android")
    id("com.google.devtools.ksp")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "com.booksync"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.booksync"
        minSdk = 26
        versionCode = 1
        versionName = "0.1.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
        isCoreLibraryDesugaringEnabled = true
    }

    kotlin {
        compilerOptions {
            jvmTarget.set(JvmTarget.JVM_17)
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    testOptions {
        // JVM unit tests link against a stub android.jar whose methods throw by default.
        // Returning defaults instead keeps ViewModel tests from blowing up on incidental
        // framework calls (android.util.Log and friends) they aren't asserting on.
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    // Core Android
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.7")
    implementation("androidx.activity:activity-compose:1.9.3")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")

    // Compose
    implementation(platform("androidx.compose:compose-bom:2025.05.00"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    implementation("androidx.navigation:navigation-compose:2.8.4")

    // Hilt (Dependency Injection)
    implementation("com.google.dagger:hilt-android:2.59")
    ksp("com.google.dagger:hilt-compiler:2.59")
    implementation("androidx.hilt:hilt-navigation-compose:1.2.0")

    // Networking (Retrofit + OkHttp)
    implementation("com.squareup.retrofit2:retrofit:2.11.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:logging-interceptor:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")
    implementation("com.jakewharton.retrofit:retrofit2-kotlinx-serialization-converter:1.0.0")

    // Room (Local Database)
    implementation("androidx.room:room-runtime:2.8.4")
    implementation("androidx.room:room-ktx:2.8.4")
    ksp("androidx.room:room-compiler:2.8.4")

    // Media3 / ExoPlayer (Audio Playback)
    implementation("androidx.media3:media3-exoplayer:1.7.1")
    implementation("androidx.media3:media3-session:1.7.1")
    implementation("androidx.media3:media3-ui:1.7.1")
    implementation("androidx.media3:media3-cast:1.7.1")

    // Google Cast Framework (Chromecast)
    implementation("com.google.android.gms:play-services-cast-framework:21.5.0")

    // Embedded HTTP server — serves locally-downloaded audiobooks to the Cast receiver over the
    // LAN. Avoids relying on public DNS for tandem.example.com (Google Home Mini hardcodes 8.8.8.8
    // and ignores the DHCP DNS, so it can't resolve a LAN-only domain).
    implementation("org.nanohttpd:nanohttpd:2.3.1")

    // WorkManager (Offline Sync)
    implementation("androidx.work:work-runtime-ktx:2.10.0")
    implementation("androidx.hilt:hilt-work:1.2.0")
    ksp("androidx.hilt:hilt-compiler:1.2.0")

    // DataStore (Preferences)
    implementation("androidx.datastore:datastore-preferences:1.1.1")

    // EPUB Reading (Readium)
    implementation("org.readium.kotlin-toolkit:readium-shared:3.1.2")
    implementation("org.readium.kotlin-toolkit:readium-streamer:3.1.2")
    implementation("org.readium.kotlin-toolkit:readium-navigator:3.1.2")
    
    // Image loading (cover art in cards)
    implementation("io.coil-kt:coil-compose:2.7.0")

    // HTML Parsing
    implementation("org.jsoup:jsoup:1.17.2")

    // Testing
    testImplementation("junit:junit:4.13.2")
    // mockk mocks final Kotlin classes (NetworkMonitor, TokenManager) and suspend
    // functions, which is what makes ViewModels unit-testable off-device.
    testImplementation("io.mockk:mockk:1.14.2")
    // Version must track the kotlinx-coroutines-core the app resolves to (1.10.2) —
    // a mismatch breaks Dispatchers.setMain.
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.10.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
    androidTestImplementation(platform("androidx.compose:compose-bom:2025.05.00"))
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-tooling")
    debugImplementation("androidx.compose.ui:ui-test-manifest")

    // Core library desugaring (required by Readium)
    coreLibraryDesugaring("com.android.tools:desugar_jdk_libs:2.1.5")
}

// Mirror the shared cross-platform sync-parity fixtures (single source of truth:
// the server) into the unit-test resources so SyncMatcherParityTest can load them.
// rootDir is the android/ project; the server lives one level up in the repo.
val copySyncParityFixtures by tasks.registering(Copy::class) {
    from("$rootDir/../server/tests/fixtures/sync_parity")
    into(layout.projectDirectory.dir("src/test/resources/sync_parity"))
}
tasks.named("preBuild") { dependsOn(copySyncParityFixtures) }
tasks.withType<Test>().configureEach { dependsOn(copySyncParityFixtures) }
