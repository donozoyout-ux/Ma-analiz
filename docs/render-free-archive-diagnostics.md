# Shell olmadan Render arşiv doğrulaması

Seçilen yol: **mevcut web servisinin Start Command değerini geçici tanılama sunucusuna çevirmek**. Yeni servis, instance, cron veya one-off job oluşturulmaz; mevcut servis planı değiştirilmez. Test bittiğinde normal Start Command geri yüklenir. Bu hazırlıkta Render'a deploy veya uzaktan ayar değişikliği yapılmadı.

## İncelenen yapı ve seçim nedeni

`render.yaml` web için `mac-analiz-single` (`1c-2g`), worker için `mac-analiz-worker` (`0.5c-512mb`) ve cron için `mac-analiz-daily-archive` (`0.5c-512mb`) tanımlıyor. Bunlar manifestin ücretli plan seçimleridir; Dashboard'daki gerçek mevcut planı bu dosyadan doğrulamak mümkün değildir. Ücretsiz kullanılan servise bu Blueprint'i topluca uygulamayın: ücretli servisler oluşturabilir veya planları değiştirebilir. Bu tanılama için yalnızca var olan web servisinin komutu değiştirilecektir.

Render [one-off job](https://render.com/docs/one-off-jobs) çalıştırmalarını süreye göre ayrıca ücretlendirir; [Free web servisleri](https://render.com/docs/free) one-off job ve Shell özelliklerini sunmaz. Worker/cron yolu da yeni kaynak açmayı gerektirebilir. Mevcut cron'un normal `daily_archive` komutu temizleme içerdiğinden bu test için kullanılmaz.

Web servisinin normal komutu `node frontend/scripts/run-single-service.mjs`. Bu launcher FastAPI ve Next.js başlatır. Geçici komut bunun yerine yalnızca küçük Python HTTP sunucusunu başlatır; normal API, frontend, worker ve cron başlatılmaz. Dolayısıyla test süresince normal web arayüzü kullanılamaz. Bu yöntem ek Render compute kaynağı gerektirmez; mevcut Free kullanım kotasını ve mevcut R2/PostgreSQL kaynaklarını kullanır. Yeni ücretsiz servis açma veya ek ücretli özellik etkinleştirme adımı yoktur.

## Ön koşul: otomatik migration'ı kapatın

Manifestteki eski `preDeployCommand: cd backend && alembic upgrade head` kaldırıldı. **Dashboard'da daha önce kaydedilmiş Pre-Deploy Command varsa ayrıca boşaltın.** Alan boş olmadan tanılama deploy'u başlatmayın. Build Command içinde de migration, backfill veya cleanup eklenmemelidir. Normal build komutu bağımlılıkları ve frontend'i derlemekle kalır.

Bu test canlı migration çalıştırmaz. Schema eskiyse `NEEDS_MIGRATION` raporlar ve gerçek gün arşivlemesini engeller. Migration bu prosedürün parçası değildir.

Başka mevcut worker/cron servisleri aktifse testten bağımsız normal işlerini çalıştırabilirler. Test penceresinde veritabanında hiçbir bakım çalışmamasını istiyorsanız mevcut worker ve archive cron'u Dashboard'dan duraklatın; yeni servis oluşturmayın. Tanılama komutu bu servisleri uzaktan yönetmez veya tetiklemez.

## 1. Yalnızca bağlantıları doğrulayın

Bu kod sürümünü mevcut web servisine deploy edin. `app/jobs/archive_diagnostic_service.py` içeren sürüm kullanılmalıdır. Servis Dashboard adı farklıysa normalde uygulamayı sunan mevcut web servisini seçin; manifestteki karşılığı `mac-analiz-single`.

Önce Dashboard'daki mevcut Start Command ve Health Check Path değerlerini geri dönüş için kendiniz not edin; sohbete veya loglara kopyalamayın. Mevcut planı koruyarak Start Command alanını geçici olarak şuna değiştirin (repository root çalışma dizini için):

```text
cd backend && python -m app.jobs.archive_diagnostic_service --mode connections
```

Servisin Root Directory alanı zaten `backend` ise `cd backend &&` bölümünü kullanmayın. Health Check Path `/health/live` olmalıdır; Dashboard'da farklıysa test için bu değeri ayarlayın. Sunucu Render'ın `PORT` değişkenine ve `0.0.0.0` adresine hemen bağlanır; ağ testleri arka planda tek denemeyle çalışır.

Gerekli Environment alanları: `DATABASE_URL`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`. `R2_REGION=auto` kullanılır. Değerleri komuta, kaynağa veya loglara yazmayın; serviste tanımlı değerler doğrudan kullanılır.

Deploy loglarındaki tek `archive_diagnostic` JSON sonucu şunları göstermelidir:

- `status=CONNECTIONS_PASS`
- `postgres.connection=PASS`, `postgres.schema=PASS`
- `r2.status=CONNECTED`; write/read/size/checksum/parquet_rows/delete alanları `PASS`
- `ready_for_smoke=true`
- `bulk`, `migration`, `postgres_cleanup`: `NOT_RUN`

PostgreSQL kontrolü salt okunur transaction içinde `SELECT 1` ve migration revision sorgularıdır. R2 testi yalnızca kendine ait `_smoke_tests/<uuid>/health.parquet` nesnesini oluşturur, indirir, doğrular ve siler. Tarihsel arşivler silinmez. Schema `NEEDS_MIGRATION` ise bağlantılar çalışsa bile `ready_for_smoke=false` olur; ikinci aşamaya geçmeyin.

Eksik ayarlar yalnızca isimleriyle raporlanır. Loglara endpoint, DSN, access key, token, bucket değeri, traceback veya bağımlılıkların ham çıktısı aktarılmaz. Kamuya açık `/archive-diagnostic` yolu yalnızca mod/çalışma durumu/sonucu verir; hiçbir HTTP isteği işi başlatamaz veya yeniden çalıştıramaz. `/health/live` yalnızca sunucunun ayakta olduğunu gösterir; veri testinin başarılı olduğunu göstermez.

## 2. Sadece sabit bir günlük smoke

Birinci aşama başarılıysa Environment kapsamında lig `36`, sezon `2025-2026` etkin olmalıdır; `HISTORICAL_START_DATE` en geç `2025-08-15` olmalıdır. `HISTORICAL_COLLECT_ODDS=false` önerilir; bu test her durumda oranları atlar. Diğer lig/sezonlar Environment içinde tanımlı olsa bile bu komut onları toplamaz.

Start Command alanını şuna değiştirip aynı servisi yeniden deploy edin:

```text
cd backend && python -m app.jobs.archive_diagnostic_service --mode smoke
```

Modun lig/sezon/tarihi kod içinde sabittir: **36 / 2025-2026 / 2025-08-15**. URL, Environment veya ilave CLI parametresiyle kapsam büyütülemez. Komut mevcut `archive_preflight --smoke` akışını çağırır: PostgreSQL ve R2'yi yeniden kontrol eder, yalnızca bu günün maç/sonuç/mevcut istatistiklerini arşivler, sonra ilgili R2 dosyalarını kayıtlı SHA-256, boyut, satır sayısı ve tam Parquet okumasıyla doğrular. Oran, migration, toplu backfill ve PostgreSQL cleanup çalışmaz.

Başarı sonucu `status=SMOKE_PASS` ve `verified_r2_rows` içinde doğrulanan maç/sonuç/istatistik sayılarıdır. `FAILED`, `BLOCKED` veya `NOT_CONFIGURED` sonucu başarı sayılmaz. `SMOKE_PASS` sonrasında da bu servis kendiliğinden başka bir iş başlatmaz.

## 3. Normal servise geri dönün

Testin sonucu görüldükten sonra Start Command alanına not ettiğiniz eski değeri geri yazın ve deploy edin. Manifestteki normal değer şudur:

```text
node frontend/scripts/run-single-service.mjs
```

Gerçek eski komut manifesttekinden farklıysa kendi not ettiğiniz komutu kullanın; değiştirdiyseniz Health Check Path'i de eski değerine döndürün. Pre-Deploy Command boş kalsın; eski otomatik migration komutunu geri eklemeyin. Test için duraklattığınız worker/cron varsa, geri açılmalarının normal bakım/temizleme işleri çalıştırabileceğini göz önünde bulundurun; bu smoke prosedürü bunları otomatik geri açmaz.

Tanılama sunucusu test başarılı veya başarısız olsa da process'i kapatmaz; Render'ın çökme/restart döngüsü oluşturmasını önlemek için health yanıtını vermeye devam eder. İş bir process içinde bir kez çalışır; HTTP health/status istekleri tekrar çalıştırmaz. Free spin-down, manuel restart veya yeniden deploy yeni process oluşturursa aynı sınırlı test tekrar çalışabilir. Bir günlük arşiv checkpoint/idempotency kontrolleri korunur. Sonuç alındığında normal komuta dönmek bu nedenle de gereklidir.

Bu hazırlık canlı Render/R2/PostgreSQL başarısı değildir. Gerçek sonuç yalnızca Render loglarındaki `CONNECTIONS_PASS` / `ready_for_smoke=true` ve ardından `SMOKE_PASS` ile doğrulanabilir.

Yerel doğrulama: **151 regresyon testi geçti**; canlı entegrasyon dosyaları bu çalıştırmadan hariç tutuldu. Yeni testler, başarısız tanılamadan sonra HTTP health'in ayakta kalmasını, health/status isteklerinin işi yeniden başlatmamasını, sabit smoke kapsamını, Render `PORT` değişkeninin okunmasını, PostgreSQL başarısızlığında R2 yazmasının engellenmesini ve sahte gizli değerlerin stdout/stderr/sonuç JSON'una sızmamasını kapsar. `git diff --check` temizdir.

## `No module named app.jobs.archive_diagnostic_service` hatası

Dosyanın yerelde bulunması Render'ın kullandığı GitHub commit'inde bulunduğu anlamına gelmez. Bu hatanın incelemesinde dosya yerelde mevcut, fakat Git tarafından henüz takip edilmiyor ve o sıradaki GitHub `main` commit'i `7fa2be2b8cbe13777ebb4ca6b1794ab51fcf2135` içinde bulunmuyordu. Tanılama modülü ve gereken arşiv bağımlılıkları birlikte sürümlenmelidir.

GitHub'a gönderilen düzeltme commit'i `[skip render]` içerir: otomatik deploy tetiklemeden kod teslim etmek için Render'ın [desteklediği skip ifadesi](https://render.com/docs/deploys#skipping-an-auto-deploy) kullanılır. Yeni kodu almak için Dashboard → Manual Deploy → Deploy latest commit seçilmelidir; yalnızca Restart eski build'i tekrar çalıştırabilir. Manuel deploy öncesinde eski migration Pre-Deploy Command'ın boş olduğunu doğrulayın. Yeni deploy'un Events ekranındaki commit SHA'sını GitHub `main` ile karşılaştırın; deploy ID (`dep-...`) Git commit SHA'sı değildir.

Modül import kontrolü işi başlatmaz:

```text
cd backend
python -c "import app.jobs.archive_diagnostic_service; print('IMPORT_PASS')"
```

Sunucu `PORT` değerini ortamdan okur ve `0.0.0.0` üzerinde dinler. Import kontrolü PostgreSQL/R2 bağlantısı açmaz. Root Directory proje kökü ise tanılama Start Command başında `cd backend &&` bulunmalıdır; Root Directory `backend` ise doğrudan `python -m app.jobs.archive_diagnostic_service --mode connections` kullanılmalıdır.
