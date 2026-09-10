// Top-level build file where you can add configuration options common to all sub-projects/modules.
plugins {
    // 9.0.x caps compileSdk at 36, so compileSdk 37 (issue #454) needs a newer
    // AGP — and each AGP pins a minimum Gradle, so the wrapper moves with it:
    // 9.1.1 wants Gradle 9.3.1, 9.4.0 wants 9.6.0. The wrapper had to move
    // either way, hence latest stable rather than the smallest step that
    // compiles. AGP 9.4 is Java 17 bytecode, so CI's JDK 17 still runs it.
    id("com.android.application") version "9.4.0" apply false
    id("com.google.dagger.hilt.android") version "2.60.1" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.4.20" apply false
    id("com.google.devtools.ksp") version "2.3.11" apply false
    id("org.jetbrains.kotlin.plugin.serialization") version "2.4.20" apply false
    // Coverage. Kover rather than JaCoCo: it is Kotlin/AGP-native (no hand-wiring of the
    // unit-test .exec file or per-variant class dirs on AGP 9) and attributes inline
    // functions correctly.
    id("org.jetbrains.kotlinx.kover") version "0.9.9" apply false
}
