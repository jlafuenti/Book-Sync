# ProGuard rules for BookSync

# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt
-keepclassmembers class kotlinx.serialization.json.** { *** Companion; }
-keepclasseswithmembers class kotlinx.serialization.json.** { kotlinx.serialization.KSerializer serializer(...); }
-keep,includedescriptorclasses class com.booksync.**$$serializer { *; }
-keepclassmembers class com.booksync.** { *** Companion; }
-keepclasseswithmembers class com.booksync.** { kotlinx.serialization.KSerializer serializer(...); }

# Keep data classes for serialization
-keep class com.booksync.data.remote.** { *; }

# ---------------------------------------------------------------------------
# Dependencies that ship no consumer ProGuard rules (issue #145).
#
# Media3, Cast, Room and Retrofit each carry their own proguard.txt inside the
# resolved artifact, so R8 already knows how to treat them and they need nothing
# here. These three carry none — verified with `unzip -l` against the Gradle
# cache — and they are the reader, the cast server and the HTML parser: exactly
# the paths where a stripped class surfaces as a crash in someone's hands rather
# than an error at build time.
#
# Deliberately broad. A conservative keep with a device-tested release build in
# hand beats a tight rule set that crashes; tighten later against a real build,
# never speculatively.
# ---------------------------------------------------------------------------

# Readium — the EPUB reader. Parsing, navigation and its serialized model.
-keep class org.readium.** { *; }
-keep,includedescriptorclasses class org.readium.**$$serializer { *; }
-dontwarn org.readium.**

# nanohttpd — LocalCastHttpServer extends fi.iki.elonen.NanoHTTPD to serve
# audiobooks to a Chromecast on the LAN.
-keep class fi.iki.elonen.** { *; }
-dontwarn fi.iki.elonen.**

# jsoup — HTML parsing for chapter text.
-keep class org.jsoup.** { *; }
-dontwarn org.jsoup.**
