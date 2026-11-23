# Maç Analiz — Android TWA & APK Yapılandırma Rehberi

Bu dizin, **Maç Analiz** uygulamasının Android Trusted Web Activity (TWA) altyapısını ve Google Bubblewrap yapılandırmasını içerir.

## Mimari Özellikleri

- **Paket Kimliği (Package ID):** `com.macanaliz.app` (sabit ve tutarlı)
- **TWA Kütüphaneleri:** `com.google.androidbrowserhelper:androidbrowserhelper:2.5.0` & `androidx.browser:browser:1.8.0`
- **Tasarım:** Premium Beyaz (#FFFFFF) & Kırmızı (#DC2626)
- **Web Güncellemeleri:** Canlı web deploy'ları (Next.js) APK yeniden derlemeye gerek kalmadan otomatik olarak uygulamaya yansır.
- **Push Bildirim Desteği:** TWA DelegationService ve Web Push (VAPID) üzerinden tam entegre.
- **Güvenli Çevrimdışı Mod:** Canlı API yanıtları (skorlar, oranlar, analizler) yanıltıcı veri sunmamak için çevrimdışı önbelleklenmez; zarif beyaz-kırmızı `offline.html` hata sayfası gösterilir.

---

## 1. Gerekli Sistem Araçları (Derleme Önkoşulları)

APK derlemek için sistemde aşağıdaki araçların kurulu olması gerekir:

1. **Java Development Kit (JDK 17 veya 21):**
   - Android Gradle Plugin 8.x, Java 17+ gerektirir.
   - Kurulum: `winget install EclipseAdoptium.Temurin.17.JDK` veya `https://adoptium.net/`
   - Ortam Değişkeni: `JAVA_HOME`

2. **Android SDK Command-Line Tools & Build Tools:**
   - `platforms;android-34`
   - `build-tools;34.0.0`
   - Kurulum: Android Studio veya `sdkmanager` CLI
   - Ortam Değişkeni: `ANDROID_HOME` veya `ANDROID_SDK_ROOT`

3. **Google Bubblewrap CLI (Alternatif Hızlı Derleme):**
   ```bash
   npm install -g @bubblewrap/cli
   ```

---

## 2. İmzalama Anahtarı (Keystore) Oluşturma

Güvenli bir üretim anahtarı oluşturmak için aşağıdaki komutu çalıştırın:

```bash
keytool -genkey -v -keystore android/macanaliz.keystore \
  -alias macanaliz \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000 \
  -storepass "GUVENLI_PAROLA" \
  -keypass "GUVENLI_PAROLA" \
  -dname "CN=Mac Analiz, OU=Mobile, O=Mac Analiz, L=Istanbul, ST=Istanbul, C=TR"
```

> **ÖNEMLİ:** Keystore dosyasını ve parolalarını asla açık git reposuna commit etmeyin. CI/CD ortamında `ANDROID_KEYSTORE_PATH`, `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD` ortam değişkenlerini kullanın.

---

## 3. Digital Asset Links Doğrulama (Adres Çubuğunu Gizleme)

TWA uygulamasının Android'de tam ekran ve adres çubuğu olmadan çalışması için web sitesi ile Android uygulaması arasında Digital Asset Links doğrulaması yapılmalıdır:

1. Keystore SHA-256 parmak izini alın:
   ```bash
   keytool -list -v -keystore android/macanaliz.keystore -alias macanaliz
   ```
2. Çıktıdaki `SHA256: XX:XX:XX...` parmak izini kopyalayın.
3. `frontend/public/.well-known/assetlinks.json` dosyasındaki `sha256_cert_fingerprints` listesine ekleyin.
4. Web uygulamasını deploy edin. Google DAL test aracı ile doğrulayın:
   `https://digitalassetlinks.googleapis.com/v1/statements:check?source.web.site=https://mac-analiz.onrender.com&relation=delegate_permission/common.handle_all_urls&target.android_app.package_name=com.macanaliz.app&target.android_app.certificate.sha256_fingerprint=PARMAK_IZI`

---

## 4. APK Derleme Komutları

### Seçenek A: Gradle ile Derleme (Önerilen)
```bash
cd android
# Debug APK:
./gradlew assembleDebug

# Üretim İmzalı APK:
./gradlew assembleRelease
```
Üretilen APK konumu: `android/app/build/outputs/apk/release/app-release.apk`

### Seçenek B: Bubblewrap CLI ile Derleme
```bash
cd android
bubblewrap build
```

---

## 5. Android Cihaz Üzerinde Son Testler

1. **Yükleme:**
   ```bash
   adb install -r android/app/build/outputs/apk/release/app-release.apk
   ```
2. **Doğrulama Adımları:**
   - **Splash & İkon:** Açılışta kırmızı logolu beyaz splash ekranı görünmeli.
   - **Tam Ekran (Adres Çubuğu Yok):** Asset Links başarılıysa üstte URL çubuğu görünmez.
   - **Çevrimdışı Testi:** Cihazı uçak moduna alın; `İnternet Bağlantısı Kesildi` ekranı gelmeli, API sahte veri sunmamalıdır.
   - **Push Bildirim İzni:** Yönetim > Bildirimler sekmesinden "Bildirimleri Etkinleştir" butonuna basarak Android 13+ bildirim izninin açıldığını test edin.
   - **Test Bildirimi:** "Test Bildirimi Gönder" butonuna basarak gol uyarısının bildirim çubuğunda ses ve titreşimle belirdiğini doğrulayın.
