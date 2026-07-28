// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    id("com.android.application") version "9.0.1" apply false
    id("com.google.dagger.hilt.android") version "2.59" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.2.10" apply false
    id("com.google.devtools.ksp") version "2.2.10-2.0.2" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.2.10" apply false
    // Coverage. Kover rather than JaCoCo: it is Kotlin/AGP-native (no hand-wiring of the
    // unit-test .exec file or per-variant class dirs on AGP 9) and attributes inline
    // functions correctly.
    id("org.jetbrains.kotlinx.kover") version "0.9.9" apply false
}
