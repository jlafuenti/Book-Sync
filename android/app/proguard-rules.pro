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
