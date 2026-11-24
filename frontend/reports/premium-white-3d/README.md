# Genel Bakış — Premium White 3D UI

Başlangıç: güncel `main`, `dae3d305ee8cc72462d14a719de69bcd89255235`.
Çalışma dalı: `feature/premium-white-3d-ui`. Bu çalışma ayrı Draft PR olarak sunulur; main merge veya Render deploy yapılmaz.

## Tasarım ve kapsam

Yalnız Genel Bakış ekranı ve o ekrandaki ortak navigasyon kabuğu yenilendi. Beyaz kartlar, `#F7F8FA` zemin, `#D71932` aktif öğeler, siyah başlıklar, geniş boşluklar ve katmanlı gölgeler kullanıldı. Mevcut yerel Inter, Space Grotesk ve Bebas Neue dosyaları korunur; paket eklenmedi.

`premium-dashboard.css` kuralları `.premium-dashboard` ve `:has(.premium-dashboard)` ile sınırlıdır. Ana sayfadan çıkıldığında ortak sidebar ve sayfa renkleri mevcut tasarıma döner. Bu geçiş tarayıcıda doğrulandı. Diğer ekranlar yeniden tasarlanmadı.

Maç, tahmin ve performans endpoint'leri; filtre, skor, isabet hesabı, detay bağlantıları, veri kaynakları ve mevcut lisans/atıf davranışı değiştirilmedi. Veri yoksa boş durum, API başarısızsa hata ve yeniden deneme görünümü kullanılır. Uygulama koduna örnek maç, skor, oran veya başarı oranı eklenmedi.

## Animasyon ve erişilebilirlik

- KPI, maç ve özet kartlarında en fazla ±2° fare eğimi, 1000px perspective ve 4px yükselme.
- Fare konumuna bağlı sınırlı gölge ve sabit gradyan katmanının transform ile hareket eden kırmızı yansıması.
- Dashboard başına en fazla bir bekleyen `requestAnimationFrame`; hareket yokken sürekli döngü yok. Hareket React state/render üretmez.
- Kart sınırları girişte ölçülür; dönüşmüş sınırların tekrar tekrar ölçülmesinden doğabilecek geri besleme önlenir. Scroll/resize/çıkışta sıfırlanır.
- Fare çıkışı, sürükleme başlangıcı, iptal, blur, gizlenen sekme, klavye odağı ve unmount işlemlerinde frame/stil/listener temizliği.
- Touch/pen, coarse pointer, hover olmayan cihazlar ve reduced-motion tercihinde 3D tilt kapalıdır. Tercih değişikliği açık kartı da sıfırlar.
- Kısa fade/slide girişleri, sıralı kart görünümü, hover ve basma mikro etkileşimleri. Sürekli dekoratif animasyon veya yeni animasyon kütüphanesi yok.
- Görünür kırmızı focus halkaları, mevcut menü Space/Escape davranışı, skip-link ve aria-current korunur. Küçük ikincil metinlerde açık zeminde daha okunabilir koyu gri kullanılır.

## Test sonuçları

Yerel production build üzerinde 22 Kasım 2026 tarihinde:

- `npm test`: **54 başarılı, 0 başarısız**. Mevcut 34 test korunur; 20 yeni DOM controller testi eklendi.
- `npm run typecheck`: **PASS**.
- `npm run build`: **PASS**, Next.js 16.4.5 standalone üretimi dahil.
- `npm run test:dashboard-browser`: **34 kontrol PASS**. Makine tarafından okunabilir sonuç: [browser-report.json](browser-report.json).

Headless Chrome kontrolleri: 1440/1024/390/360px yatay taşma, yerel fontlar, boş durum, API 503 ve yeniden deneme, sentetik 0–0 ve henüz doğrulanmamış skor, KPI/maç/özet kartı 3D hareketi, başlangıç konumuna dönüş, buton basma, klavye odağı, lig/takım/tarih/durum filtreleri, tablo/kart geçişi, mobil menü ve Escape, reduced-motion, touch cihazında tilt engeli, route geçişinde eski temaya dönüş ve sidebar gösterge hizası. Yakalanmamış JavaScript istisnası veya izin verilmeyen istek görülmedi.

## Ekran görüntüleri ve kanıt ayrımı

Bu görüntüler **canlı production verisi kanıtı değildir**. Browser testi sadece localhost production frontend'i açar. `/api/` istekleri Chrome interception ile yanıtlanır; gerçek backend, PostgreSQL veya R2'ye iletilmez. Yerel origin dışı veya GET olmayan sayfa istekleri engellenir.

Boş API yanıtı ile tasarım:

- [1440px masaüstü / tam sayfa](desktop-1440-empty.png)
- [1024px tablet / tam sayfa](tablet-1024-empty.png)
- [390px mobil](mobile-390-empty.png), [tam sayfa](mobile-390-full.png)
- [360px küçük mobil](mobile-360-empty.png)
- [Mobil menü](mobile-menu.png), [klavye odağı](keyboard-focus-mobile.png)

Yalnız izole test fixture'ları; görüntülerde **İZOLE UI TESTİ · SENTETİK VERİ** etiketi bulunur:

- [Masaüstü kart eğimi](desktop-1440-fixture-tilt.png)
- [390px maç kartı](mobile-390-fixture-card.png)
- [360px maç kartı](mobile-360-fixture-card.png)
- [Reduced-motion](reduced-motion.png)

Sentetik maçlar yalnız test script'inde bulunur. Production UI'ye fallback olarak eklenmez; hiçbir veritabanına veya arşive yazılmaz.

## Yeniden çalıştırma

`frontend` dizininde önce `npm test`, `npm run typecheck` ve `npm run build` çalıştırın. Build sırasında eski yerel standalone sunucusunu kapatın; Windows çalışan dizinin üzerine build yazılmasını kilitleyebilir.

Ardından ayrı PowerShell terminalinde yalnız yerel frontend'i açın:

```powershell
$env:PORT="3106"
$env:HOSTNAME="127.0.0.1"
node .next/standalone/server.js
```

Diğer terminalde, yine `frontend` dizininde:

```powershell
npm run test:dashboard-browser
```

Chrome farklı konumdaysa `CHROME_PATH` belirtilebilir. Test runner yalnız HTTP localhost/127.0.0.1 origin kabul eder. Backend başlatmak veya credential sağlamak gerekmez. Tarayıcı ve geçici profil işlem sonunda temizlenir. Ekran görüntüleri varsayılan olarak bu rapor dizinine yazılır.

## Sınırlar ve güvenlik

60 FPS bir tasarım hedefidir; gerçek mobil donanımda GPU/pil ve uzun süreli FPS ölçümü yapılmadı. Chrome responsive/touch emülasyonu başarılıdır; fiziksel iOS/Android ve Safari/Firefox kontrolleri yapılmadı. `:has()` destekleyen modern tarayıcılar hedeflenir. Gerçek production maç verileriyle canlı uçtan uca doğrulama bu görsel çalışmanın kapsamında çalıştırılmadı.

Backend, PostgreSQL/R2, migration, retention, sağlayıcı ayarları, Render ve GitHub Actions workflow dosyaları değiştirilmedi. Production yazma/silme/import, migration, deploy ve workflow dispatch çalıştırılmadı. Mevcut güvenlik bayraklarına dokunulmadı.

`SQL_WRITE=NOT_RUN` · `R2_WRITE=NOT_RUN` · `LIVE_DELETE=NOT_RUN` · `SAFE_TO_DELETE=NO`

`AUTO_RETENTION_DELETE_ENABLED=false` · `OPERATIONAL_DELETE_ENABLED=false`
