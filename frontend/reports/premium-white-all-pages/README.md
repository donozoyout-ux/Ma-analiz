# Premium White — tüm uygulama sayfaları

23 Kasım 2026. Main başlangıcı: `a0247be43f45972dc42a1ce3f91e6176616c7119`.
Çalışma dalı: `feature/premium-white-all-pages`. Ayrı Draft PR; merge/deploy yapılmaz.

## Ortak sistem

`globals.css` artık tek uygulama temasını taşır: `#F7F8FA` zemin, beyaz kartlar, `#15171C` metinler, `#D71932` vurgu/aktif navigasyon, okunabilir ikincil metin ve katmanlı gölgeler. Layout/sidebar, sekmeler, filtreler, tablolar, kayıt/istatistik/detay ve sistem yüzeyleri ortak kuralları kullanır. Semantik sağlıklı durum etiketleri yeşil kalır; grafikler kırmızı/gri ayrılır.

`motion.css` tek ortak hareket katmanıdır. `premium-dashboard.css` yalnız Genel Bakış kompozisyonu/KPI yerleşimini içerir. `:has()` ile route'a bağlı tema geçişi, eski koyu tema ve beyaz override tekrarları kaldırıldı. Yerel Inter, Space Grotesk ve Bebas Neue dosyaları korunur. Yeni dependency veya lockfile değişikliği yoktur.

Kök `ApplicationSurface` tüm rotalarda tek controller çalıştırır; eski iç içe DashboardSurface kaldırılır. Server children korunur. En fazla bir pending requestAnimationFrame, ±2° rotateX/rotateY, 1000px perspective, 4px yükselme, dinamik gölge ve kırmızı reflection kullanılır. React state ve idle animasyon döngüsü yoktur.

Filtreler, kaydırılabilir tablolar ve başka kart içeren kapsayıcılar eğilmez. Rota değişiminde effect cleanup; aynı pathname'de sekme değişimiyle kaldırılan kartta childList MutationObserver cleanup uygulanır. Pointer çıkışı/iptali, scroll/resize, blur, klavye odağı, gizli sekme ve unmount durumlarında frame/stil/listener temizlenir. Touch/pen/coarse input ve reduced-motion tilt'i kapatır; tercih değişimi açık kartı da sıfırlar.

Kırmızı focus halkaları ve mevcut Space/Escape menü davranışı korunur. Kaydırılabilir tablo/grafiklere isimli region ve tabIndex eklenir. RecordsPanel yükleniyor/hata yüzeyleri status/alert rollerine sahiptir.

## Testler

- `npm test`: **157 PASS, 0 FAIL, 0 SKIP**. Mevcut 154 teste tablo/kapsayıcı engeli ve observer/tab-removal yaşam döngüsü testleri eklenmiştir.
- `npm run typecheck`: **PASS**.
- `npm run build`: **PASS**, Next.js 16.4.5 standalone dahil.
- `npm run test:premium-pages-browser`: **372 kontrol PASS**; [JSON](browser-report.json).
- Mevcut dashboard browser regresyonu: **34 kontrol PASS**; [JSON](dashboard-regression.json). Diğer sayfanın koyu olması beklentisi bilinçli yeni ortak beyaz tema beklentisine güncellendi; veri/filter/motion/a11y kontrolleri korundu.
- `git diff --check`: **PASS**.

21 rota/sekme/yönlendirme 1440, 1024, 768, 390 ve 360px için tarandı: `/`, Maç Merkezi, oran sekmesi, SQL detay, aynı UUID history detay, AI Tahminleri, kupon sekmesi, İstatistik & Analiz, eğitim sekmesi, Veri Arşivi, veri deposu, Yönetim & Ayarlar, ayarlar sekmesi, yedi eski URL yönlendirmesi ve özel 404 görünümü. Arka plan, siyah başlık, kırmızı aktif navigasyon, beyaz kart ve yatay taşma testleri geçti.

Ek kontroller: farklı sayfalarda tilt/gölge/reflection ve ±2° sınırı; pointer çıkışı, rota ve aynı-path sekme temizliği; klavye/tablo erişimi; filtre/boş sonuç; altı veri ekranında empty/loading/error; source=history bağlantısı; lisans/attribution ve eksik analiz/istatistik gösterimi; mobil menü/Escape/focus; touch ve reduced-motion. Konsol/hydration/uncaught JS hatası veya izin verilmeyen dış istek görülmedi.

## Veri ve güvenlik

Testler yalnız localhost frontend production build'ini açar. Tüm `/api/` yanıtları browser interception ile açıkça sentetik fixture'dan gelir; gerçek backend'e iletilmez. Dış origin ve GET olmayan sayfa istekleri engellenir. `scripts/fixtures/premium-pages.mjs` uygulama koduna import edilmez veya fallback olmaz.

Skor, tahmin ve performans yüzdeleri yalnız **SENTETİK UI TESTİ** etiketli test örnekleridir; gerçek maç, analiz veya başarı kanıtı değildir. Matris saati `2026-11-25T12:00:00Z` değerine sabittir; ekrandaki tarih canlı gözlem zamanı değildir. Genel Bakış boş regresyon görüntüleri yerel sistem gününü ve boş API yanıtını kullanır.

Gerçek veri tipi, endpoint, hesaplama, filtre, source identity ve lisans/atıf davranışı değiştirilmedi. Backend/PostgreSQL/R2, migration/model, provider, retention, GitHub Actions ve Render dosyalarında **sıfır fark** vardır. Kapalı DELETE bayraklarına dokunulmadı; production environment okunmadı/değiştirilmedi.

`AUTO_RETENTION_DELETE_ENABLED=false` · `OPERATIONAL_DELETE_ENABLED=false` · `SAFE_TO_DELETE=NO` · `LIVE_DELETE=NOT_RUN`

Production bağlantısı, SQL/R2 yazma/silme, migration, import, workflow_dispatch, main merge ve deploy **NOT_RUN**. CI mevcut frontend workflow'u ile doğal PR olayından çalışır; son HEAD için bağlantı ve sonuç Draft PR açıklamasında raporlanır.

Paylaşılan checkout çalışma sırasında başka bir görev tarafından Android dalına geçirildi. Kaydedilmiş frontend değişiklikleri ve test kanıtları ayrı managed worktree'ye alındı. Diğer görevin backend/docs dosyaları bu PR'a taşınmadı; mevcut çalışması değiştirilmedi.

## Görüntüler

- Genel Bakış boş: [masaüstü](overview-empty-1440.png), [mobil](overview-empty-390.png)
- Maç Merkezi: [masaüstü](match-center-1440.png), [mobil](match-center-390.png)
- Maç detayı: [masaüstü](match-detail-1440.png), [mobil](match-detail-390.png)
- R2 geçmiş detay: [masaüstü](history-detail-1440.png), [mobil](history-detail-390.png)
- AI Tahminleri: [masaüstü](predictions-1440.png), [mobil](predictions-390.png)
- Performans: [masaüstü](performance-1440.png), [mobil](performance-390.png)
- Veri Arşivi: [masaüstü](archive-1440.png), [mobil](archive-390.png)
- Sistem durumu: [masaüstü](management-1440.png), [mobil](management-390.png)
- Ayarlar: [masaüstü](settings-1440.png), [mobil](settings-390.png)
- [3D tilt](match-card-tilt-1440.png), [reduced-motion](reduced-motion-detail.png), [mobil menü](mobile-menu-360.png)

## Tekrar çalıştırma ve sınırlar

Frontend dizininde `npm ci`, `npm test`, `npm run typecheck`, `npm run build`. Build sırasında eski yerel standalone sunucusunu kapatın. Ayrı terminalde:

```powershell
$env:PORT="3106"
$env:HOSTNAME="127.0.0.1"
node .next/standalone/server.js
```

Sonra `npm run test:premium-pages-browser`. Backend/credential gerekmez. Chrome konumu CHROME_PATH ile verilebilir. Test loopback HTTP adresi dışında çalışmaz; Chrome/geçici profil işlem sonunda kapanır. PNG/JSON çıktıları bu dizine yazılır.

Fiziksel iOS/Android, Safari/Firefox ve donanım CPU/pil/60FPS ölçümü yapılmadı. Touch kontrolleri Chrome emülasyonudur. Canlı API/SQL/R2 bu görevde denenmedi. Mevcut beta integration browser detay-tema beklentisi de beyaza güncellendi; backend harness bu görevde çalıştırılmadı/değiştirilmedi. Draft PR açık bırakılır.
