# Maç Analiz — Android APK (TWA) Derleme ve Kurulum Rehberi

Bu dizin, **Maç Analiz** uygulamasının Android Trusted Web Activity (TWA) projesini, Gradle Wrapper derleyicisini ve derleme araçlarını içerir.

- **Uygulama Adı:** Maç Analiz
- **Paket Kimliği (Package ID):** `com.macanaliz.app`
- **Hedef Canlı Web Adresi:** `https://ma-analiz-2.onrender.com`
- **Tasarım:** Premium Beyaz (`#FFFFFF`) zemin & Maç Analiz Kırmızısı (`#DC2626`)
- **Bildirim Durumu:** Bu APK sürümünde push bildirimleri ve izinleri kapsam dışıdır (yalnızca hafif, güvenli ve hızlı web uygulaması).

---

## 1. Gerekli Sistem Araçları (Yerel Derleme İçin)

Yerel Windows veya Linux makinenizde APK derlemek için şu iki araç gereklidir:

1. **Java Development Kit (JDK 17 veya 21):**
   - Android Gradle Plugin 8.2+ Java 17+ gerektirir.
   - Windows kurulumu:
     ```powershell
     winget install EclipseAdoptium.Temurin.17.JDK
     ```
   - Ortam Değişkeni: `JAVA_HOME=C:\Program Files\Eclipse Adoptium\jdk-17...`

2. **Android SDK & Build Tools:**
   - Platform: `android-34`
   - Build-Tools: `34.0.0`
   - Kurulum: Android Studio üzerinden "SDK Platforms -> Android 14.0 (UpsideDownCake)" ve "SDK Build-Tools 34.0.0" seçilerek kurulur.
   - Ortam Değişkeni: `ANDROID_HOME=$env:LOCALAPPDATA\Android\Sdk`

> **NOT:** Eğer yerel bilgisayarınızda Android SDK veya JDK 17 kurulu değilse, **bölüm 6**'daki GitHub Actions otomatik derleyicisini kullanabilirsiniz.

---

## 2. Debug APK Derleme (Yerel Bilgisayarda)

Gradle Wrapper dosyaları projeye eklenmiştir:

```powershell
cd android

# Windows üzerinde:
.\gradlew.bat assembleDebug

# Linux / macOS üzerinde:
chmod +x gradlew
./gradlew assembleDebug
```

### APK Çıktı Konumu
Başarılı derleme sonucunda APK dosyası şuraya üretilir:
`android/app/build/outputs/apk/debug/app-debug.apk`

---

## 3. APK'yı Android Telefona Kurma Adımları

Üretilen `app-debug.apk` dosyasını telefonunuza yüklemek için 2 yöntem vardır:

### Yöntem A: USB Kablosu ve ADB ile (Hızlı)
1. Telefonunuzda **Ayarlar > Geliştirici Seçenekleri > USB Hata Ayıklama**'yı açın.
2. Telefonu USB ile bilgisayara bağlayın.
3. Şu komutu çalıştırın:
   ```powershell
   adb install -r android/app/build/outputs/apk/debug/app-debug.apk
   ```

### Yöntem B: Doğrudan Telefona İndirip Kurma (Dosya Transferi)
1. `app-debug.apk` dosyasını Google Drive, WhatsApp, Telegram veya USB kablosu ile telefonunuza aktarın.
2. Telefonda dosyaya dokunun.
3. "Bilinmeyen uygulamaları yükle" uyarısı çıkarsa izin verin ve **Yükle** butonuna basın.
4. Ana ekranda kırmızı logolu beyaz **Maç Analiz** ikonu belirecektir.

---

## 4. TWA Doğrulaması ve Adres Çubuğu Uyarısı

> **ÖNEMLİ:** Android TWA (Trusted Web Activity) mimarisinde, uygulamanın tarayıcı adres çubuğu olmadan **tam ekran (native app gibi)** çalışabilmesi için `https://ma-analiz-2.onrender.com/.well-known/assetlinks.json` adresi ile APK'nın imza parmak izinin birebir eşleşmesi gerekir.
>
> Eğer canlı sitedeki `assetlinks.json` henüz operatörün imzasıyla güncellenmediyse:
> - Uygulama **sorunsuz açılır** ve `https://ma-analiz-2.onrender.com` sitesini yükler.
> - Ancak üst kısımda güvenli Chrome CCT adres çubuğu görünür.
> - Adres çubuğunu tamamen gizlemek için aşağıdaki 5. adımı izleyerek imza parmak izinizi canlı siteye ekleyin.

---

## 5. İleride Yayınlanacak Release APK İçin Güvenli İmzalama

Gerçek bir release APK oluşturmak ve Google Play Store'a yüklemek için:

1. **Keystore Üretin (Yerel Makinede):**
   ```bash
   keytool -genkey -v -keystore android/macanaliz.keystore \
     -alias macanaliz \
     -keyalg RSA \
     -keysize 2048 \
     -validity 10000 \
     -storepass "OPERATOR_PAROLASI" \
     -keypass "OPERATOR_PAROLASI" \
     -dname "CN=Mac Analiz, OU=Mobile, O=Mac Analiz, L=Istanbul, ST=Istanbul, C=TR"
   ```
   > ⚠️ **GÜVENLİK UYARISI:** `macanaliz.keystore` dosyasını ve parolasını **asla git reposuna commit etmeyin**. Projedeki `.gitignore` dosyası `*.keystore` uzantılarını korumaya almıştır.

2. **SHA-256 Parmak İzini Alın:**
   ```bash
   keytool -list -v -keystore android/macanaliz.keystore -alias macanaliz
   ```
   Çıktıdaki `SHA256: XX:XX:XX...` değerini kopyalayın.

3. **`assetlinks.json` Dosyasına Ekleyin:**
   [`frontend/public/.well-known/assetlinks.json`](file:///c:/Users/PC/Desktop/Maçanaliz/frontend/public/.well-known/assetlinks.json) dosyasındaki placeholder yerine yapıştırın ve siteyi deploy edin.

4. **Release APK Derleyin:**
   ```powershell
   $env:ANDROID_KEYSTORE_PATH = "macanaliz.keystore"
   $env:ANDROID_KEYSTORE_PASSWORD = "OPERATOR_PAROLASI"
   $env:ANDROID_KEY_ALIAS = "macanaliz"
   $env:ANDROID_KEY_PASSWORD = "OPERATOR_PAROLASI"

   cd android
   .\gradlew.bat assembleRelease
   ```
   Çıktı konumu: `android/app/build/outputs/apk/release/app-release.apk`

---

## 6. GitHub Actions ile Otomatik APK Derleme ve İndirme

Yerel bilgisayarda Android SDK kurulu olmasa bile, projenin `.github/workflows/android-apk-build.yml` iş akışı sayesinde GitHub üzerinde otomatik olarak derlenebilir:

1. PR #18 veya `feature/android-app-push` dalına commit gönderildiğinde workflow otomatik tetiklenir.
2. Veya GitHub reposunda **Actions > Android APK Build (TWA) > Run workflow** butonuna tıklanarak manuel başlatılabilir.
3. Derleme tamamlandığında (yaklaşık 2-3 dakika), sayfanın altındaki **Artifacts** bölümünden `mac-analiz-debug-apk.zip` dosyasını tek tıkla bilgisayarınıza indirebilirsiniz.
