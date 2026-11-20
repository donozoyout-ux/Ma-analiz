# R2 tarihsel arşiv işletimi

## Canlıya geçiş hazırlığı — güncel durum

20 Kasım 2026 tarihli tekrar kontrolde R2 bucket adı tanımlı; `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` ve `DATABASE_URL` eksik. Geçersiz tanımlanmış bir R2 alanı tespit edilmedi. Bu, bağlantı başarısı değildir: canlı PostgreSQL, R2 yazma/okuma, gerçek gün yüklemesi, toplu aktarım ve temizleme çalıştırılmadı.

Yerel yapılandırma yeri proje kökündeki `C:\Users\PC\Desktop\Maçanaliz\.env` dosyasıdır. Backend dizininden çalıştırılan komutlar da aynı dosyayı okur. Dosya Git tarafından yok sayılır; gerçek değerleri raporlara veya sohbete koymayın. Üretimde Render Dashboard → ilgili servis → Environment bölümünde `mac-analiz-single`, `mac-analiz-worker` ve `mac-analiz-daily-archive` için gerekli bağlantı değişkenlerini tanımlayın. Bu çalışmada uzak Render ortamının mevcut değerleri incelenmedi veya değiştirilmedi. `render.yaml` yalnızca hangi değişkenlerin sağlanacağını belirtir; `sync: false` alanları gerçek anahtar içermez.

R2 için endpoint, access key ID, secret access key ve bucket; PostgreSQL için `DATABASE_URL` gerekir. `R2_REGION=auto` desteklenir. Yalnızca maç/sonuç/istatistik arşivlemesi için Sportmonks anahtarı gerekmez. İlk gerçek gün örneğini çalıştırmak için `ENABLED_LEAGUES=36`, `ENABLED_SEASONS=2025-2026`, `HISTORICAL_START_DATE=2023-07-01` ve `HISTORICAL_COLLECT_ODDS=false` kullanın. Mevcut yerel sezon yalnızca `2026-2027` olduğundan 2025-26 ayrıca etkinleştirilmelidir. Gerçek `.env` bu hazırlıkta değiştirilmedi.

Komutlar `backend` dizininden:

```text
python -m app.jobs.archive_preflight
```

Bu komut yalnızca yapılandırma durumunu raporlar. Alanlar eksikse `NOT_CONFIGURED`; yalnızca biçimsel olarak hazırsa `CONFIGURED_NOT_VERIFIED` döner. Bağlantı veya yazma yapmaz. Bugünkü sonuç `NOT_CONFIGURED`; tüm canlı işlem alanları `NOT_RUN` oldu.

Bağlantılar ve hedef kapsam tanımlandığında yalnızca şu tek günlük testi çalıştırın:

```text
python -m app.jobs.archive_preflight --smoke --league 36 --season 2025-2026 --date 2025-08-15
```

Sıra sabittir: tek lig/sezon/tarih kontrolü → salt okunur PostgreSQL `SELECT 1` ve migration durumu → küçük R2 Zstandard Parquet yazma/okuma/checksum testi → yalnızca o gerçek günün maç/sonuç/istatistik backfill'i → ilgili R2 dosyalarının kayıtlı checksum, boyut, satır sayısı ve tam okuma ile tekrar doğrulanması. Test mevcut checkpoint'i atlayarak başarılı sayılamaz; arşivler yine okunur. Maç/sonuç içermeyen boş gün `SMOKE_PASS` sayılmaz. İstatistik sayısı ayrıca raporlanır; mevcut değilse üretilemez. Komut migration uygulamaz ve temizleme çağırmaz.

PostgreSQL bağlı fakat schema `NEEDS_MIGRATION` ise yazma testi durur. Hedef bağlantıyı yapılandırdıktan sonra `python -m alembic upgrade head` uygulanıp aynı smoke komutu yeniden çalıştırılmalıdır. Bu hazırlık sırasında canlı migration uygulanmadı.

### Oranlardan bağımsız çalışma

```text
python -m app.jobs.historical_probe --league 36 --season 2025-2026 --date 2025-08-15 --skip-odds
python -m app.jobs.historical_backfill --league 36 --season 2025-2026 --start-date 2025-08-15 --end-date 2025-08-15 --batch-size 1 --skip-odds --dry-run
```

`--skip-odds` kaynağa hiç oran isteği göndermez; maç, sonuç ve mevcut istatistikler tamamlanabilir. `HISTORICAL_COLLECT_ODDS=false` aynı ayrımı günlük tarihsel arşiv işi için sağlar; `.env.example` ve Render örneği bu değerle hazırlanmıştır. Bu ayar güncel aktif oran toplama işini değiştirmez. Kontrol noktasındaki `odds_requested=false`, oranların eksik olduğunu açıkça korur. Daha sonra oran toplama açıldığında gün yeniden ele alınır; doğrulanmış maç/sonuç/istatistik dosyaları çoğaltılmaz.

### Toplu aktarım için bekleyen karar

Canlı smoke henüz geçmediği için toplu aktarım **NOT_READY** durumundadır; indirme başlatılmadı. `SMOKE_PASS` elde edilince hedef 11 lig ve 2023-24'ten başlayan sezonlar etkinleştirilecek; önce `historical_backfill --skip-odds --batch-size 7 --dry-run` ile kesin lig/sezon/tarih kapsamı çıkarılacaktır. Ardından bir ligin yedi günlük dilimi doğrulanacak, sezon ve diğer ligler aynı kontrol noktalarıyla sırayla ilerletilecektir. Kaynakta olmayan istatistikler ayrı kapsam durumu olarak korunacaktır. Toplu kapsamın tamamlanma süresi veya satır sayısı kaynak kontrolü yapılmadan tahmin edilmez. Temizleme bu devreye alma adımlarına dahil değildir; sonraki bakım işinde yalnızca her SQL kaydının kendi doğrulanmış arşiv kanıtıyla çalışabilir.

Bu hazırlığın doğrulaması: `python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py` → **143 passed**, üç bağımlılık/deprecation uyarısı. Canlı entegrasyon testleri bu turda çalıştırılmadı. Yeni regresyonlar eksik yapılandırmada bağlantı/yazma yapılmamasını, PostgreSQL başarısızlığında R2'nin çağrılmamasını, R2 checksum hatasında gerçek arşivin başlamamasını, arşiv tekrar doğrulaması başarısızsa smoke'un başarısız sayılmasını ve temizlemenin hiç çağrılmamasını doğrular. Oran isteğinin hiç yapılmadığı moddan daha sonra tam kapsama geçiş de çoğaltma olmadan sınandı. Mevcut yerel kapsamla bir günlük oransız dry-run `writes=false`, `odds_requested=false` ve yalnızca `matches/results/statistics` döndürdü.

Bu değişiklik PostgreSQL aktif durumunu ve mevcut provider/worker mimarisini korur. Toplayıcı tarihsel maçları, sonuçları, gerçek istatistikleri ve izinli kaynaklardan doğrulanmış maç öncesi oranları R2 Parquet + Zstandard arşivine yazar. Goaloo oran otomasyonu erişim kuralı nedeniyle engellidir. Canlı toplu indirme henüz başlatılmadı.

## İncelemede bulunan ve giderilen eksikler

- Goaloo istatistik metodu yalnızca hata üretiyordu. Resmi maç sayfasındaki teknik istatistik bölümü artık ayrıştırılıyor. Alan adı, ev/deplasman değeri, ham değerler, dönem, kaynak URL ve indirme zamanı korunuyor. Kaynakta olmayan değerler sıfır yapılmıyor.
- Oran kimliği maç kimliğini içermediğinden aynı günün farklı maçları çakışabiliyordu. Kimlik artık maç kapsamını içeriyor; bookmaker kaynaktaki adı ve kimliğiyle tutuluyor.
- Kısmi tarih arşivi yeniden çalıştırıldığında değişen doğrulama/indirme zamanları dosya çakışmasına yol açıyordu. Tekrarlar mevcut dosyayı doğrular; eksik kimlikler değiştirilmeyen ek parçalara yazılır. Aynı kimlikte farklı kaynak içeriği sessizce kabul edilmez, hata olarak bildirilir.
- Günlük SQL arşivleri geç sona eren kayıtlar için mevcut dosyanın üzerine yazamıyordu. Geç gelen satırlar da ek parçalarda saklanır.
- Temizleme yalnızca gün/lig kapsamına bakarak arşivde bulunmayan veya süresi dolmayan oranları silebiliyordu. Artık arşivdeki SQL satır kimliği, kayıt alanları ve süre kontrol edilir; dosya aynı okunan baytlar üzerinden SHA-256, metadata, boyut, satır sayısı ve tam Parquet okumasıyla doğrulanır.
- Eski başlangıç migration'ı güncel modelleri doğrudan oluşturduğu için sonraki migration'lar aynı kolonları tekrar ekleyebiliyordu. Başlangıç şeması sabitlendi; yeni `0009_historical_coverage` migration'ı kapsam bilgisini ekler.

## Arşiv düzeni ve devam ettirme

İlk dosya mevcut düzendedir:

```text
matches/season=2025-2026/league=36/date=2025-08-15.parquet
```

Geç gelen kayıtlar aynı gün altında kimliklerden üretilen değişmez parçalara yazılır:

```text
odds/season=2025-2026/league=36/date=2025-08-15/part=<identity-sha256>.parquet
```

Tarihsel okuyucu her iki düzeni de okur. R2 arşiv yazımları `If-None-Match: *` kullanır; eş zamanlı bir yazım mevcut dosyayı değiştiremez. Cloudflare'ın [koşullu yazım sözleşmesi](https://developers.cloudflare.com/r2/api/s3/conditional-operations/) bu işlemin atomik olduğunu belirtir.

`historical_ingest_days` provider/lig/sezon/UTC gün bazında lease, deneme sayısı, hata kodu, başarısız maç kimlikleri ve alan kapsamını tutar. `FAILED` ve `PARTIAL` günler yeniden denenir. Eski kapsam bilgisi olmayan tamamlanmış günler yeni toplayıcı tarafından yeniden denetlenir. Başarısız HTTP/JSON/HTML indirmesi `EMPTY` sayılmaz. `SOURCE_EMPTY` yalnızca doğrulanmış yanıtın gerçekten boş olmasıdır.

PostgreSQL'den temizlenen kayıt için `archive_jobs.DELETED`, SQL kaynağının temizlendiği anlamına gelir; R2 dosyası korunur. Görev kontrol noktaları, arşiv doğrulama kayıtları, sistem ayarları, bookmaker tanımları ve bağlantılı tahmin/kupon/analiz kayıtları bu temizlemenin kapsamına girmez.

## Canlı kaynak kontrolü — 20 Kasım 2026

Kontroller tek ligde, ayrı ayrı bir günlük, salt okunur isteklerle yapıldı:

- Lig `36`, `2023-2024`, UTC `2023-08-11`: maç `2398985`, bir sonuç ve 26 gerçek istatistik alanı.
- Lig `36`, `2024-2025`, UTC `2024-08-16`: maç `2590898`, bir sonuç ve 32 gerçek istatistik alanı.
- Lig `36`, `2025-2026`, UTC `2025-08-15`: maç `2789129`, bir sonuç ve 32 istatistik alanı. İlk teknik incelemede AJAX kaynağı 25 bookmaker için 75 opening ve toplam 225 opening/latest/live satırı döndürdü. Ardından kontrol edilen robots kuralı `/ajax/` erişimini kapattığı için bu bulgu otomatik toplama izni olarak kabul edilmedi; yeni oran istekleri engellendi ve kalıcı oran yüklemesi yapılmadı.

Örnek [2025-26 maç sayfası](https://www.goaloo.com/football/match/live-2789129) ve [oran sayfası](https://www.goaloo.com/football/match/oddscomp-2789129) yeniden denetlenebilir. İzin verilen oran HTML sayfası fiyatlar yerine boş yer tutucular döndürdü. [Goaloo robots.txt](https://www.goaloo.com/robots.txt) `/ajax/` yolunu kapatıyor; provider bu kuralı her kaynak alanı için indirip önbelleğe alır ve yasak uç noktaya istek göndermeden `PROVIDER_ACCESS_RESTRICTED` üretir. Son tek günlük probe maç/sonuç/istatistik için başarılı, oranlar için bu hata koduyla `FAILED` döndü. `l/latest` için maç öncesi zamanlama da doğrulanmadı; kapanış olarak etiketlenmez. Kaynak oran zamanı sağlanmadığında `source_captured_at=null`, gerçek indirme zamanı `retrieved_at` olarak saklanır. Kaynakta var olmayan kapanış zamanı veya fiyatı üretilmez. Tarihsel analiz indirme zamanını da erişilebilirlik sınırı olarak kullanır.

Sportmonks için resmi [Premium Odds kapsamı](https://www.sportmonks.com/football-api/premium-odds-feed/) oran geçmişini maçtan sonra 7 günle sınırlar. Dolayısıyla `SPORTMONKS_API_TOKEN` tek başına 2025-26 sezonunun tamamını geri getirmez. Kullanıcı Sportmonks erişimini yapılandıracağını bildirmiştir; anahtar geldikten sonra `python -m app.jobs.sportmonks_probe` yalnızca kimlik doğrulama ve kapsamı sınar. Yakın tarihli, doğrulanmış bir Sportmonks maç kimliği `--fixture-id` ile ayrıca denenebilir. Goaloo maç kimliği Sportmonks kimliği olarak kullanılmaz. Tam eski sezon oranları için izinli, uzun süreli arşiv kaynağı gereklidir; bu gereksinim engelli durumdadır.

Bu örnekler tüm sezonun veya diğer on ligin alan kapsamını kanıtlamaz. Hedef lig listesi `36,30,31,8,11,16,7,27,103,113,2187` olarak korunmuştur. Her lig/sezonun mevcut olması ayrıca provider discovery ile denetlenir; eksiklikler raporda görünür. Resmi [kullanım koşulları](https://www.goaloo.com/disclaimer) ve [kaynak açıklaması](https://www.goaloo.com/faq) referans alınmıştır; bunlar sınırsız otomatik indirme veya yeniden dağıtım lisansı olarak yorumlanmaz. Provider içinde gerçek HTTP istekleri de hız sınırına tabidir; 403/429/şema hataları aşılmaya çalışılmaz.

## Küçük testten kalıcı arşive geçiş

Gizli değerleri kök `.env` dosyasında veya çalışma ortamında tanımlayın; çıktılarda yalnızca yapılandırılmış/eksik durumları gösterilir. `.env` Git tarafından yok sayılır. Gerekli değişkenler: `DATABASE_URL`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`; `R2_REGION=auto`.

İlk deneme için `ENABLED_LEAGUES=36`, `ENABLED_SEASONS=2025-2026`, `HISTORICAL_START_DATE=2023-07-01` yeterlidir. Komutları `backend` dizininden çalıştırın:

```text
python -m app.jobs.r2_smoke_test --parquet
python -m app.jobs.historical_probe --league 36 --season 2025-2026 --date 2025-08-15
python -m alembic upgrade head
python -m app.jobs.historical_backfill --league 36 --season 2025-2026 --start-date 2025-08-15 --end-date 2025-08-15 --batch-size 1 --dry-run
python -m app.jobs.historical_backfill --league 36 --season 2025-2026 --start-date 2025-08-15 --end-date 2025-08-15 --batch-size 1
```

R2 smoke yalnızca kendine ait `_smoke_tests/<uuid>/health.parquet` dosyasını oluşturur, okur, doğrular ve siler. Kalıcı bir günlük backfill temizleme çalıştırmaz. Aynı backfill komutunu tekrar çalıştırarak sıfır yeni satır/dosya üretildiğini doğrulayın. Goaloo'nun 2025-26 oran erişimi şu an `PROVIDER_ACCESS_RESTRICTED` nedeniyle günü `PARTIAL` bırakır; bunu tam kapsamlı başarı olarak yorumlamayın. Bu kontroller ve izinli oran kaynağı doğrulaması başarılı olmadan 11 liglik kapsamı başlatmayın.

Başarıdan sonra `.env.example`/`render.yaml` içindeki 11 lig ve `2023-2024,2024-2025,2025-2026,2026-2027` sezonları etkinleştirilebilir. `historical_backfill --dry-run` kapsamı gösterir; `--batch-size` gün sayısını sınırlar. Oranlar için 2025-07-01 öncesine istek yapılmaz. Operatör başlatmadıkça toplu backfill çalışmaz.

Günlük iş mevcut `python -m app.jobs.daily_archive` komutudur. Sonuçları işler, süresi dolan aktif kayıtları arşivler, doğrulanmış kapsamı temizler ve metrik üretir. Tek başına temizleme `python -m app.jobs.daily_cleanup` ile çalışır. Dosya veya doğrulama hatasında ilgili SQL satırı korunur. Tahmin, kupon ve saklanan analiz bağlantıları olan maçlar korunur.

## Doğrulama durumu

Son tam çalıştırma: `python -m pytest -q` → **135 passed, 4 skipped**, üç bağımlılık/deprecation uyarısı. Migration SQL üretimi ve başlangıç şemasının sonraki kolonları önceden oluşturmaması test edildi. Yeni testler kısmi günün devamını, birden çok maçta aynı oran kimliğini, eksik/bozuk istatistik yanıtlarını, geç gelen değişmez parçaları, bozulmuş arşivi, SQL satır kapsamını, saklanan analizlerin korunmasını, tamamlanmamış maçın sonradan tamamlanmasını, robots engelini, Sportmonks saklama sınırını ve Zstandard Parquet smoke işlemini kapsar.

Son ortam kontrolünde `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `DATABASE_URL` ve `SPORTMONKS_API_TOKEN` henüz tanımlı değildi. Yerel kapsam hâlâ yalnızca `36` / `2026-2027` idi; `.env.example` ve Render kapsamı hedef 11 lig ve 2023-24 başlangıcına güncellendi. Bir günlük dry-run `writes=false` döndü. Gerçek R2 yüklemesi, canlı PostgreSQL migration/temizleme ve tüm liglerin kalıcı tarihsel veri kapsamı henüz doğrulanmış sayılmaz. Yerel InMemoryObjectStore/SQLite testleri canlı R2/PostgreSQL başarısı olarak raporlanmaz. Toplu indirme veya canlı veri silme başlatılmadı. Ortam tanımlandıktan sonra önce yukarıdaki tek günlük sıra izlenmelidir.
