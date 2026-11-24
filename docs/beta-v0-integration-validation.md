# Beta v0 — PR14 + PR15 güvenli entegrasyon doğrulaması

Rapor tarihi: 23 Kasım 2026 (Europe/Istanbul). Bu çalışma production yayını veya silme yetkisi değildir.

## Kaynak referansları ve entegrasyon

- Güncel main: `dae3d305ee8cc72462d14a719de69bcd89255235`.
- PR #14 HEAD: `e3f41b27a3d22a8bb887877f4a2f6c6bcdc9cfe4`.
- PR #15 HEAD: `8ffcb9192178ab68ba49bf6e1041c0e8bd41f30e`.
- GitHub üzerinden iki HEAD doğrulandı; verilen başlangıç referansları değişmemişti. Her iki PR Draft ve açıktır.
- Entegrasyon dalı: `test/beta-v0-pr14-pr15-integration`.
- PR14 yerel merge commit'i: `47ca203`.
- PR15 yerel merge/çakışma çözüm commit'i: `1ab8318364943e941900acf60284143e83fa0324`.
- Rapor/test harness commit'i bu entegrasyon dalının son HEAD'idir; kesin SHA ve bu HEAD'e ait GitHub CI bağlantıları Draft PR açıklamasında verilir. İki kaynak PR'ın değişiklikleri tekrar cherry-pick edilmedi; her iki kaynak SHA dalın ancestor'ıdır.

Main, kaynak PR dalları ve `rollback/6657bc5` dalına push/merge yapılmadı. Korunan rollback referansı: `6657bc58faa457b958fb7807a0c1c04328fd4ea0`.

## Çakışmalar ve çözümü

İki dosyada metinsel çakışma çıktı:

- `frontend/package.json`: PR14 Stage7I3/I4/I6 testleri ve read-only probe komutu ile PR15 tilt testleri ve dashboard browser komutu birlikte korundu. Aynı test dosyası iki kez çalıştırılmaz. Yeni yerel entegrasyon komutları test amaçlıdır; npm test/build içinde canlı probe çalıştırılmaz.
- `frontend/src/components/football-home.tsx`: PR15 `DashboardSurface`, premium başlık ve bağlantılarıyla PR14 `BETA / TEST SÜRÜMÜ` uyarısı birlikte korundu. Endpoint, hesaplama, skor, isabet, filtre ve veri davranışları değiştirilmedi.

Çözülemeyen kod çakışması yok. İş mantığı veya production API düzeltmesi gerekmedi. Bu entegrasyona özel yeni kod yalnız test harness/guard/browser script'leridir.

## Yerel test sonuçları

- `npm ci --no-audit --no-fund`: PASS; lockfile/dependency değişikliği yok.
- `npm test`: **154 PASS, 0 FAIL, 0 SKIP**. PR14'ün 134 testi ile PR15'in 20 tilt testi birlikte çalışır.
- `npm run typecheck`: PASS.
- `npm run build`: PASS; Next.js 16.4.5 standalone dahil.
- `npm run test:dashboard-browser -- http://127.0.0.1:3106 reports/beta-v0-integration/dashboard-regression`: **34 kontrol PASS**. Birleşik kaynak üzerinde PR15'in mevcut tarayıcı regresyonu korunur; API yanıtları bu ayrı testte açıkça sentetiktir.
- `npm run test:beta-integration`: **38 kontrol PASS**. Gerçek yerel Next.js → FastAPI → SQLite + bellek Parquet zinciri; API yanıt interception/fallback yok.
- İzole backend güvenli paket: **785 PASS, 15 SKIP, 14 mevcut deprecation uyarısı**. PR14 paketine ek olarak 8 test harness güvenlik reddi testi vardır.
- PostgreSQL contract/recovery: **yerelde NOT_RUN**. Docker Linux engine pipe mevcut değil. Atlanan 15 test başarılı sayılmadı; PostgreSQL 16 PR-only CI job'u aynı entegrasyon HEAD'i için ayrıca çalıştırılmalıdır.

Backend paketi: `python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py`. Temiz git archive kopyası, boş `.env`, credential içermeyen child env, `STAGE7I6_NODE_CONTRACT=true` ve dış socket/DNS engeliyle çalıştırıldı. Kullanıcının ilgisiz untracked dosyaları teste veya commit'e alınmadı.

CI sonucu eski PR'ların yeşil durumundan devralınmaz. Bu entegrasyon HEAD'inin frontend ve backend/PostgreSQL CI bağlantıları, sonuçları ve production retention job'unun SKIPPED kanıtı Draft PR'ın güncel açıklamasında yer alır. CI yalnız normal PR olaylarıyla çalışır; workflow_dispatch kullanılmaz.

## Veri sözleşmesi ve uçtan uca kanıt

Tüm yeni veriler **SENTETİK** olarak adlandırılır. Kullanıcının verdiği production Bayern–Köln UUID'si sorgulanmadı; production bağlantısı kurulmadı. Entegrasyon fixture UUID'si `a1111111-1111-4111-8111-111111111111`, skor 1–1 ve Bundesliga kapsamı yalnız testtir. Backend ile tarayıcı saati test için `2026-11-25T12:00:00Z` değerine sabitlenir; görüntüdeki tarih canlı gözlem tarihi değildir.

Kanıt zinciri:

1. Boş SQL/R2 kataloglarında gerçek yerel endpoint'ler boş listeler döndürür; UI maç veya başarı oranı üretmez.
2. İki sentetik fixture SQL'de ve mevcut lifecycle formatıyla bellek Parquet nesnelerinde hazırlanır. Bu yalnız fixture seed işlemidir; provider/retention worker veya gerçek arşivleme işi başlatılmaz.
3. Seed bittikten sonra SQLite `query_only=ON` olur ve SELECT dışı SQL engellenir. Bellek nesne deposunun put/delete/list metotları engellenir.
4. Gerçek Match Center aktif liste, SQL detay, geçmiş liste ve `?source=history` detay HTTP yanıtları karşılaştırılır. UUID, takım, UTC başlangıcı, lig/sezon, durum, final skor ve kaynak nesnesi eşittir. SQL/R2 kopyaları birleşik listede mükerrer maça dönüşmez.
5. OpenLigaDB kaynak adı, ODbL-1.0 lisansı, attribution ve lisans bağlantısı korunur. Her iki detayda `analysis_summary=null`, `statistics=[]` olur. Analiz/istatistik/oran üretilmez.
6. Dashboard gerçek yerel legacy `matches` endpoint'inden bugünün tek test maçını gösterir: maç sayısı 1, analiz sayısı 0, doğrulanmış isabet `—`. Yenileme tekrar gerçek yerel GET üretir. Buradaki 1–1 bir fixture skorudur; HTTP 200 bağımsız gerçek dünya doğrulaması olarak sunulmaz.
7. Dashboard → aynı UUID SQL detay → Veri Arşivi → aynı UUID R2 detay bağlantıları çalışır; `?source=history` korunur.
8. Eksik/bozuk/timeout arşivde API 503 ve `MATCH_ARCHIVE_NOT_VERIFIED`; UI güvenli hata durumu gösterir. Kısmi arşiv `PARTIAL` ve açık uyarı üretir. Bilinmeyen UUID 404'tür.
9. Test sonunda SQL write attempt ve bellek object write attempt sayıları sıfırdır; seed sonrası nesne checksum kümesi aynıdır. Kalıcı/production veri yoktur.

SQLite fixture okumalarında UTC offset'leri test-only load adapter ile geri verilir; bu PostgreSQL timestamptz DTO davranışını taklit eder. PostgreSQL sürücüsü veya production serializer değiştirilmedi. Yalnız sayısal UUID'nin SQLite native UUID affinity sorununu önlemek için açıkça sentetik hexadecimal UUID kullanıldı; production kimlikleri değişmedi.

Makine raporları:

- [Gerçek yerel HTTP zinciri — 38 kontrol](../frontend/reports/beta-v0-integration/integration-browser-report.json)
- [Dashboard regresyonu — 34 kontrol](../frontend/reports/beta-v0-integration/dashboard-regression/browser-report.json)
- [Config/workflow fark güvenliği](../frontend/reports/beta-v0-integration/safety-review.json)

## Responsive, animasyon ve erişilebilirlik

1440, 1024, 768, 390 ve 360px boyutlarında dashboard ve gerçek yerel geçmiş detay ekranında yatay taşma bulunmadı. Ekran görüntüleri görsel olarak incelendi. Mobil menü ve navigasyon, klavye focus, tarih/lig/durum/takım filtreleri, yenileme ve kart/tablo geçişleri geçti.

KPI/maç/özet tilt, ±2° sınırı, dinamik gölge, kırmızı reflection, çıkış sıfırlama, touch engeli, reduced-motion ve olay/frame cleanup test edildi. Hızlı pointer hareketlerinde tek pending frame ve React state kullanmayan DOM controller korunur. Hareket yokken sürekli animasyon çalışmadığı kontrol edildi. Donanım CPU, pil ve gerçek cihaz 60 FPS ölçümü yapılmadı.

Beyaz tema ve kırmızı aktif menü yalnız Genel Bakış'ta kalır. SQL/R2 detay ve arşiv sayfalarında mevcut koyu tema geri gelir; premium CSS sızıntısı görülmedi. Yakalanmamış JavaScript/console/hydration hatası **0**. Arşiv hata senaryolarına ait 3 beklenen HTTP 503 ayrı sayılır; mevcut favicon 404'ü zararsız ayrı kalemdir. Beklenmeyen HTTP hata sayısı **0**.

## Güvenlik ve beklenen farklar

- `render.yaml` main'e göre farklıdır: PR14'ten gelen iki DELETE bayrağının açık `false` tanımıdır. Dosya PR14 ile aynıdır; Start Command değişmedi. Render API/ayar/deploy işlemi yapılmadı.
- Workflow farkı yalnız PR14'ten gelen retention scheduler test hazırlığıdır: PR-only Node köprüsü ve izole PostgreSQL 16 contract/recovery job'u. Production retention job'u, schedule, concurrency ve tetikleyiciler main ile yapısal olarak aynıdır. PR olayı production job'unu engeller.
- Test job'larına production secret aktarımı yoktur. PostgreSQL servisinde yalnız geçici test kimlikleri vardır. Watchdog/collector/keepalive/import/source snapshot workflow'larında yeni fark yoktur.
- Migration/model/DB/API dosyalarında entegrasyonun eklediği fark yoktur. Kaynak kimlikleri, SQL/R2 verified import, advisory lock, idempotency ve 48 saat politikası değiştirilmedi.
- GitHub repository variables sorgusunda iki DELETE bayrağı tanımlı değildi; mevcut scheduler ifadesi yokluğu `false` olarak değerlendirir. Gerçek Render environment değerleri okunmadı/değiştirilmedi; yalnız kaynak kod/default ve izole test ortamı doğrulandı.
- Production SQL/R2, kullanıcı verisi veya arşiv dosyası okunmadı/yazılmadı/silinmedi. Gerçek DELETE, migration, otomatik ingest, workflow_dispatch ve deploy NOT_RUN.
- Yerel test sunucuları/Chrome süreçleri işlem sonunda durdurulur. Üretim endpoint'ine proxy yapabilecek frontend build test başlamadan reddedilir. Test harness opt-in, temiz `.env` ve kapalı DELETE bayrakları gerektirir; credential/PG/R2 ortamında uygulamayı import etmeden reddeder.

`AUTO_RETENTION_DELETE_ENABLED=false` · `OPERATIONAL_DELETE_ENABLED=false` · `SAFE_TO_DELETE=NO` · `LIVE_DELETE=NOT_RUN`

## Ekran görüntüleri

Bütün görüntüler **İZOLE ENTEGRASYON TESTİ · SENTETİK VERİ** etiketi taşır. Hiçbiri canlı production kanıtı değildir.

- [Masaüstü boş veri](../frontend/reports/beta-v0-integration/desktop-empty.png)
- [Masaüstü etiketli test maçı](../frontend/reports/beta-v0-integration/desktop-fixture.png)
- [Masaüstü 3D tilt](../frontend/reports/beta-v0-integration/desktop-tilt.png)
- [768px tablet](../frontend/reports/beta-v0-integration/tablet-768.png)
- [Mobil dashboard](../frontend/reports/beta-v0-integration/mobile-dashboard.png)
- [Mobil menü](../frontend/reports/beta-v0-integration/mobile-menu.png)
- [Geçmiş detay masaüstü](../frontend/reports/beta-v0-integration/history-detail-desktop.png)
- [Geçmiş detay mobil](../frontend/reports/beta-v0-integration/history-detail-mobile.png)
- [Reduced-motion](../frontend/reports/beta-v0-integration/reduced-motion.png)

## Yeniden çalıştırma ve sınırlar

Frontend dizininde `npm ci`, `npm test`, `npm run typecheck`, `npm run build` çalıştırın. Build için `API_INTERNAL_URL` vermeyin; varsayılan loopback hedefi gerekir. Sonra `npm run test:beta-integration` geçici git archive kopyasında boş `.env` ile test backend'ini ve yerel frontend'i başlatır, HTTP zincirini doğrular ve süreçleri temizler. Python backend bağımlılıkları ve Chrome gereklidir; `CHROME_PATH` ile yerel tarayıcı konumu verilebilir. 8000/3106 portu doluysa test güvenle durur.

`npm run test:dashboard-browser` ayrıca PR15'in API interception kullanan regresyonudur ve açık bir yerel frontend sunucusu gerektirir; bu test ile gerçek HTTP zinciri birbirinin yerine kanıt sayılmaz.

Fiziksel iOS/Android ve Safari/Firefox test edilmedi. Modern `:has()` desteği gereklidir. Production performansı, gerçek depolama bağlantısı, deployment ve canlı veri güncelliği bu görevde doğrulanmadı. Mevcut 14 Python deprecation uyarısı entegrasyon hatası değildir. Yerel PostgreSQL NOT_RUN sonucu CI'ın yerini tutmaz; PostgreSQL CI sonucu ayrı sunulur.

## Karar

**INTEGRATION_PASS_WITH_WARNINGS** — yerel bileşik testler başarılıdır; fiziksel cihaz/performance ve production doğrulaması yapılmamıştır. PostgreSQL 16 için yerel eksik doğrulama, ayrı izole CI kanıtıyla tamamlanmalıdır ve o sonucun bağlantısı PR açıklamasında raporlanır. Bu karar production deploy veya DELETE açma onayı değildir. Hiçbir PR merge edilmez.
