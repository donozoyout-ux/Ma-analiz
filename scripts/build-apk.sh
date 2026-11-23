#!/usr/bin/env bash
set -e

echo "=== Maç Analiz Android APK Derleme Kontrolü ==="

# 1. Java Kontrolü
echo -e "\n[1/4] Java Ortamı:"
java -version || true

# 2. Android SDK Kontrolü
echo -e "\n[2/4] Android SDK Kontrolü:"
SDK_PATH="${ANDROID_HOME:-${ANDROID_SDK_ROOT}}"
if [ -n "$SDK_PATH" ] && [ -d "$SDK_PATH" ]; then
    echo "Android SDK bulundu: $SDK_PATH"
else
    echo "UYARI: Android SDK bulunamadı (ANDROID_HOME / ANDROID_SDK_ROOT ayarlanmamış)."
fi

# 3. Bubblewrap Kontrolü
echo -e "\n[3/4] Google Bubblewrap Kontrolü:"
if command -v bubblewrap >/dev/null 2>&1; then
    echo "Bubblewrap kurulu: $(command -v bubblewrap)"
else
    echo "Bubblewrap bulunamadı. Kurmak için: npm install -g @bubblewrap/cli"
fi

# 4. Derleme Denemesi
echo -e "\n[4/4] APK Derleme Durumu:"
if [ -z "$SDK_PATH" ] || [ ! -d "$SDK_PATH" ]; then
    echo "DERLEME BAŞLATILAMADI: Sistemde Android SDK bulunmadığı için APK doğrudan derlenemiyor."
    echo "Android TWA projesi 'android/' dizininde derlemeye hazır şekilde oluşturulmuştur."
    echo "Ayrıntılı adımlar için 'android/README.md' dosyasını inceleyin."
    exit 0
fi

echo "Android ortamı mevcut, derleme başlatılıyor..."
cd "$(dirname "$0")/../android"
if [ -f "./gradlew" ]; then
    ./gradlew assembleDebug
else
    echo "Gradle wrapper bulunamadı. 'gradle wrapper' çalıştırarak oluşturabilirsiniz."
fi
