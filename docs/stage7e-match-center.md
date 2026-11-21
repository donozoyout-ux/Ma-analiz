# Stage 7E — gerçek verili Maç Merkezi

Başlangıç c5e898d / 597 testlik CI düzeltmesi. Bu geliştirme yeni maç importu, SQL operasyonel yazma, R2 PUT/overwrite/delete veya migration içermez. **SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, R2_DELETE=NOT_RUN, LIVE_DELETE=NOT_RUN**. İki silme bayrağı false kalır. Archive/auto scheduler, watchdog, source identity, verified import, Render ayarları/Start Command ve health endpoint'leri değiştirilmedi.

## Ekranlar

`/matches` mevcut sidebar, beş ana bölüm, sekmeler ve responsive tasarım sistemi içinde aktif/tarihsel birleşik liste sunar. Kart/tablo, tarih (Europe/Istanbul), lig, takım, durum ve kapsam filtreleri; cursor sayfalama, loading/boş/hata/retry görünümü eklendi. Varsayılan son 31 gündür; eski maçlar tek gün seçilerek aranır. Sayfa başına en fazla 12 kayıt ve sorgu başına 32 katalog kimliği incelenir; sayı tüm veritabanı toplamı diye sunulmaz.

`/archive` aynı bileşeni doğrulanmış tarihsel kapsamla açar. `/matches/[id]` UUID'yi korur; SQL'de kalmayan maç R2'den okunur. Arşivden gelen link `?source=history` taşır; SQL kopyası hâlâ mevcutken de gerçek R2 okuması denenebilir. Doğrulanmış skor, sezon, kaynak/ODbL atfı, güncelleme/sonuç doğrulama/son arşiv okuma zamanları ayrıdır. İstatistik/analiz yoksa açık Türkçe boş durum vardır. Eski oran/istatistik paneli SQL kaydı için isteğe bağlı açılır; mevcut oran sekmesi/URL'leri korunur. Analiz POST'u çalıştırılmaz. Ana sayfa mevcut bileşenini korur; ortak maç kartı artık yalnız verified FINAL skoru gösterir.

## Read-only API

- `GET /api/v1/match-center`: `source=all|active|history`, `day=YYYY-MM-DD`, `league=de.bl1|en.pl|tr.sl`, `team` (en fazla 80 karakter), `status=scheduled|in_progress|finished`, `cursor` (UUID), `limit` (1–12).
- `GET /api/v1/match-center/{uuid}`: `source=all|history`.
- Eski `/api/v1/matches` sözleşmeleri aynen korunur. Yeni uçlar ORM/journal serialize etmez; yalnız public futbol alanlarını döndürür. User/ayar/ham AI/journal/R2 key veya credential dışa verilmez. Hatalar sabit 404/422/503 kodlarıdır; Cache-Control=no-store.

SQL bağlantısı küçük, lazy ayrı RO pool (1+1, pool wait 2s/connect 3s), READ ONLY + REPEATABLE READ ve statement_timeout=4s kullanır. Veri okuma dışında commit yoktur. Health başlangıcına PyArrow yüklenmez. Liste/değerlendirme süre bütçesi 8 saniye, R2 connect/read 2 saniye ve tek deneme; frontend 12 saniyede durur, otomatik sonsuz retry yoktur. History okuması aynı process'te en fazla iki eşzamanlı isteğe alınır. Ağ/SQL arızası maç görünümüne hata verir; liveness ve uygulama süreçleri sonlandırılmaz.

## PostgreSQL / R2 birleştirme ve güven

Katalog mevcut ActiveMatch UUID'leri ile korunan `source-identity:*` index'indeki UUID/ilk doğrulanmış parça referansının birleşimidir. Keyset sırası UUID'dir; SQL/R2 aynı UUID ise birleşik görünüm SQL'i tercih eder ve tek satır gösterir. Fuzzy takım eşleştirmesi yoktur. Eski Bayern Goaloo alias'ı sadece mevcut Stage 3 inceleme/journal kurallarıyla primary lisanslı adlara projekte edilir; PostgreSQL isim/UUID/alias değiştirilmez.

Bucket listelenmez. SQL archive ledger yalnız index'in işaret ettiği parça ve aynı UUID'nin sınırlı lifecycle revizyonlarını adresler. HistoricalRepository'ye eklenen bounded verified-part okuması mevcut archive verifier'ı yeniden kullanır: VERIFIED ledger, checksum, byte boyutu, row_count=1, metadata, tam içerik, UUID/native kimlik ve snapshot bağları denetlenir. Bilinmeyen format/çelişen eşzamanlı revizyon veya bozuk parça kabul edilmez. Parça sıkıştırılmış en fazla 1 MB, en fazla 100 kolon ve 8 MB decode boyutu olabilir. Veri gövdesi tek kez indirilir; request içi buffer dışında geçmişi toptan cache/download yapılmaz.

En fazla 8 match revizyonu; seçili lig/sezon/UTC günü için en fazla 16 result parçası ve detay için 8 analysis parçası okunur. Limit aşımı güvenli 503 verir; doğrulanmamış eski skora sessiz fallback yoktur. Bu ilk sınırlı pilot için uygundur; çok yoğun sezonlarda doğrudan result/analysis UUID index'i ve bütçeli katalog optimizasyonu sonraki aşamada değerlendirilmelidir. Yeni index/migration veya source identity yazımı bu görevde yapılmadı. Index'te ilk verified parça referansı olmayan eski kayıtlar tarihsel listeye alınmaz; güvenli backfill ayrı yetkili iş olmalıdır.

Skor için FINAL + mevcut verified_at + bitmiş maç + geçerli iki tam sayı ve kickoff/verify zaman sırası gerekir. Saat dilimi belirsizse tahmin yapılmaz. Analiz salt okunur kayıt özetidir; sonuç garantisi değildir. R2'de kayıtlı doğrulanmış analiz özeti okunabilir; tarihsel teknik istatistik ve oran gösterimi bu dar görünümde açılmadı. SQL teknik gözlemleri yalnız mevcut izin ve technical_valid kontrollerinden geçerse gösterilir.

OpenLigaDB/ODbL ve mevcut lisans atfı korunur. OpenFootball politikası değiştirilmez. Augsburg–Mainz 83240 BLOCKED olarak kalır; Premier League karantina ve Süper Lig NOT_AVAILABLE kapsamı genişletilmez. Bu görev kaynak taraması veya production importu açmaz.

## Test ve canlı okuma

Yerel sonuç: **614 backend testi geçti (14 mevcut warning)**; **26 frontend testi geçti**; TypeScript ve production build başarılı. Backend entegrasyonları SQLite/bellek R2 sentetik verisidir; 1–1 Bayern fixture testi canlı gözlem diye sunulmaz. İzole retention temizliği sonrasında aynı UUID ile R2 liste/detay okuması test edildi. Eksik/bozuk nesne, metadata/ledger/native identity/snapshot değişimi, skor eksikliği, filtreler/cursor, UTC→İstanbul ve public hata/alan güvenliği kapsandı.

```powershell
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
cd ../frontend
npm test
npm run typecheck
npm run build
```

Canlı okuma ve tarayıcı kanıtı bu ilk commit'te **NOT_VERIFIED / henüz NOT_RUN**. Mevcut beş Secret ile manuel read-only preflight `check=match-center`, aynı public projection fonksiyonlarıyla mevcut Bayern–Köln SQL/R2 kaydını kontrol etmek için hazırlanmıştır; hiçbir yazma/audit/repair yolu çağırmaz. Sonuçlar çalışmadan sonra eklenecek.

Manuel public tarayıcı testi (Chrome headless, ayrı geçici profil) için `node scripts/test-match-center-browser.mjs https://ma-analiz-2.onrender.com` hazırlandı. 1440px/390px, gerçek kaydın liste/detay/arşiv görünümü, boş takım araması, tarayıcıya özel ağ hatası ve retry'yi kontrol eder. Bu test production veri durumunu değiştirmez. Henüz gerçek ekran testi yapıldı iddiası yoktur.

Mobilde tek kolon filtre/kart, taşmayan skor, yatay kaydırılabilir semantik tablo, dokunmatik butonlar, aria-busy/status/alert, erişilebilir etiketler ve reduced-motion skeleton vardır. Otomatik canlı skor/güncelleme garantisi verilmez. Canlı temizlik/otomatik import sonraki ayrı kullanıcı onayının konusu olarak kalır.
