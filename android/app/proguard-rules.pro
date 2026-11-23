# ProGuard rules for Maç Analiz TWA application

-keepattributes *Annotation*
-keepattributes JavascriptInterface
-keepattributes SourceFile,LineNumberTable

# AndroidX and Android Browser Helper classes
-keep class androidx.browser.trusted.** { *; }
-keep class androidx.browser.customtabs.** { *; }
-keep class com.google.androidbrowserhelper.trusted.** { *; }

-dontwarn androidx.browser.trusted.**
-dontwarn com.google.androidbrowserhelper.trusted.**
