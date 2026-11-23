# Draft PR: Android APK (TWA/Bubblewrap) & Kullanıcı İzinli Push Bildirim Altyapısı

## 📌 Özet
Bu PR, Maç Analiz platformuna Google Bubblewrap & Trusted Web Activity (TWA) mimarisinde Android APK derleme altyapısı, PWA manifest, offline hata ekranı ve kullanıcı onaylı Web Push (VAPID) bildirim sistemini ekler.

## 🎯 Hedefler ve Çözümler
1. **Android Altyapısı (TWA & Bubblewrap):**
   - Sabit paket kimliği: `com.macanaliz.app`
   - Android Gradle projesi (`android/`) ve Bubblewrap yapılandırması (`android/twa-manifest.json`)
   - Otomatik Web Güncellemeleri: Web deploy edildikçe uygulama içi otomatik güncellenir.
   - Digital Asset Links: `frontend/public/.well-known/assetlinks.json`
   - Çevrimdışı Ekran: Premium beyaz-kırmızı `offline.html` ve `/offline` rotası (canlı API'ler kesinlikle önbelleğe alınmaz).

2. **Kullanıcı Kontrollü Push Bildirimleri (Web Push / VAPID):**
   - Web Push API + Service Worker + VAPID (RFC 8292)
   - 5 Bildirim Türü: Maç başlama hatırlatması, doğrulanmış gol, doğrulanmış maç sonucu, favori takım güncellemeleri, AI analiz tamamlanması.
   - Bildirim Ayarları Ekranı (`/management?tab=notifications` ve `/settings/notifications`)
   - İlk açılışta zorunlu izin sormama; kullanıcı açıkça "Bildirimleri Etkinleştir" butonuna basınca izin talep etme.
   - Takım takip listesi ve tek tek bildirim türü filtreleme.

3. **Backend Güvenliği & Bütünlük:**
   - İstemci anahtarı (client token) ile abonelik erişim kontrolü (başkalarının abonelikleri değiştirilemez).
   - TTL bazlı mükerrer bildirim engelleme (`DedupStore`).
   - Eksik veya sahte veri doğrulaması (`verified=True` zorunluluğu).
   - Günlüklerde endpoint ve anahtar maskeleme (`sanitize_endpoint`).
   - Varsayılan olarak `PUSH_NOTIFICATIONS_ENABLED=false` (güvenli kuru deneme / dry-run modu).

4. **Korumalar:**
   - PostgreSQL verileri, R2 arşivi ve migration tablolarına dokunulmadı.
   - `AUTO_RETENTION_DELETE_ENABLED=false` ve `OPERATIONAL_DELETE_ENABLED=false` korundu.
