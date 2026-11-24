# Ücretsiz GitHub Actions retention scheduler

## Kurulum ve güvenli başlangıç

Render'da yalnız mevcut web servisi kullanılır. `render.yaml` içindeki ücretli worker ve cron tanımları kaldırıldı; web tanımı/Start Command ve `.github/workflows/render-keepalive.yml` değiştirilmedi. Bu dosya düzenlemesi Render API'sinde servis oluşturma/silme işlemi çalıştırmaz.

Repo public olduğundan standart `ubuntu-latest` GitHub-hosted runner kullanımı ücretsizdir. Private repoya geçilirse kota/ücretlendirme yeniden değerlendirilmelidir; büyük runner veya ücretli Render scheduler kullanılmaz. Mevcut PostgreSQL/R2 giderleri ayrı kalır. [GitHub resmi ücretlendirme](https://docs.github.com/en/actions/concepts/billing-and-usage).

Repository Settings → Secrets and variables → Actions → **Repository secrets** alanına şunları ekleyin:

- `DATABASE_URL`: GitHub-hosted runner'ın erişebildiği dış PostgreSQL bağlantısı; Render'ın yalnız özel ağdan erişilen internal URL'si değil.
- `R2_ENDPOINT`
- `R2_ACCESS_KEY_ID`
- `R2_SECRET_ACCESS_KEY`
- `R2_BUCKET`

Değerleri kod, issue, log, workflow YAML veya bu dokümana yazmayın. Gerekli DB/R2 erişim yetkileri mevcut Stage 5 ile aynıdır; ağ/SSL engelleri bypass edilmez. Bu secret'lar unit-test job'una verilmez; yalnız bakım adımının ortamına aktarılır. Harici action sürümleri doğrulanmış commit SHA'larına sabitlendi ve checkout credential kalıcılığı kapatıldı.

İlk kurulumda **DELETE kapalıdır**. Repository variable `AUTO_RETENTION_DELETE_ENABLED` tanımlanmaz veya `false` kalır. `OPERATIONAL_DELETE_ENABLED` workflow'da false'tur. `push` ile gelen ilk çalışma ve varsayılan workflow_dispatch girdisi her zaman dry-run'dır; repository variable yanlışlıkla true olsa da dry-run iki silme bayrağını kapatır.

## Zamanlamalar

`.github/workflows/retention-scheduler.yml`:

- UTC `7,22,37,52 * * * *`: SQL'deki mevcut veriyi erken R2 arşivleme (`archive`).
- UTC `2 * * * *`: Stage 5 otomatik retention kontrolü (`auto`). Auto bayrağı kapalıyken yalnız plan üretir.
- `workflow_dispatch`: varsayılan `dry-run`; ayrıca `archive` veya `auto` seçilebilir.
- İlgili kod/workflow main'e push edildiğinde: yerel adaptör güvenlik testleri ardından zorunlu gerçek storage dry-run.

Türkiye saati UTC+3'tür; dakika değerleri aynıdır. Bunlar Render web servisine HTTP bakım çağrısı değildir; runner beş secret ile doğrudan mevcut PostgreSQL/R2 altyapısına bağlanır. Yeni scraping, Sportmonks, ücretli API veya geçmiş sezon taraması başlamaz.

Her çalışmada önce secretsız otomatik test job'u geçmelidir. Auto bayrağı ileride true yapılırsa aynı Actions çalışmasında ayrıca zorunlu dry-run geçmeden auto adımı başlamaz. Ardından kısa ömürlü retention runner çalışır. Production veri/dizin raporlarının tamamı loglanmaz: durum, sayı, plan hash'i, kapasite, tutma nedenleri ve eksik değişken **adları** beyaz listeyle yazılır. Exception traceback/URL/token/nesne anahtarı/raw satırlar yayımlanmaz; ortamdaki secret değerleri ayrıca string çıktılardan redakte edilir.

## Stage 5 güvenliği ve audit

Yeni `app.jobs.actions_retention` yalnız mevcut `retention.run` modlarını çağırır. Archive/auto için SQL ledger anahtarları worker ile aynı UTC slot biçimini kullanır. Dry-run audit anahtarı GitHub run/attempt ile ayrılır. SQL audit alanında GitHub run ID'si de tutulur. Aynı tamamlanmış slot ikinci kez veri işlemez; başarısız/partial slot retry edilebilir.

Core dry-run operational veriye salt okunur bağlanır. Actions wrapper'ı mümkünse ayrı `JobRun` audit'i ve `scheduler:github-actions-retention` durum kaydını yazar; bu metadata audit'i operational DELETE değildir. Bağlantı/secret yoksa audit yazılmış gibi raporlanmaz. Dry-run'ın sıfır kayıt yazması nedeniyle SQL job status'ü `SKIPPED` olabilir; GitHub job'unun başarılı tamamlanması ve `storage_status=DRY_RUN`, kontrolün yapıldığını gösterir. Bu, atlanmış schedule ile aynı şey değildir.

Concurrency group `ma-analiz-retention` ve `cancel-in-progress:false`, çalışan arşiv/temizlik run'ının yeni tetikleme yüzünden iptalini önler. PostgreSQL advisory lock ayrıca runner, eski worker veya başka bakım sürecinin eşzamanlı veri işlemesini engeller. Stage 5'in bağımsız auto cursor'ı, güncel kilitli plan/hash üretimi, ORM yenileme, iki snapshot kontrolü, kaynak kimliği, izin, 48 saat, bağımlılık, VERIFIED ledger, SHA-256/sayı/tam içerik ve pre-delete yeniden okuma koşulları değişmedi.

Eksik bölüm önce değişmez arşivleme yoluyla tamamlanmaya çalışılır. R2 doğrulanamazsa o bundle silinmez. Tamamlanmamış maç, sonuçlanmamış tahmin, devam eden analiz ve ilişki engelleri korunur. Silme ve asıl `retention_cleanup:<hash>` audit'i aynı SQL transaction'ındadır; GitHub scheduler audit'i bunun yerine geçmez. Metadata, kaynak kimlikleri, kullanıcılar, ayarlar ve R2 nesneleri silinmez. Migration, DROP/TRUNCATE, VACUUM FULL veya arşiv overwrite işlemi yoktur.

## İleride gerçek DELETE'i açma

Önce yerel testler ve Actions `dry-run` tamamlanmalı, gerçek bağlantı/şema ve arşiv raporu incelenmelidir. Ardından yalnız **Repository variable** olarak bir kez:

```text
AUTO_RETENTION_DELETE_ENABLED=true
```

Sonraki saatlik auto çalışması manuel `--approve-plan` istemez. Bu aşamada variable true yapılmaz ve **LIVE_DELETE=NOT_RUN** kalır. Disabled auto veya dry-run, açık bir manuel DELETE'e dönüşmez. Durdurmak için variable'ı false yapın; Render web servisini değiştirmek gerekmez. Devam eden SQL transaction'ı zorla iptal etmek yerine yeni run'ların kapalı flag ile başlaması tercih edilir.

## Gecikme, başarısızlık ve atlanan işler

`.github/workflows/retention-watchdog.yml`, UTC `41 * * * *` çalışır. DB/R2 secret'larını almaz; yalnız read-only GitHub API token'ı kullanır. Scheduled archive başarısının 45, auto başarısının 120 dakika içinde olup olmadığını ve son schedule'ın failure/cancelled/timed_out olup olmadığını kontrol eder. Manuel başarılı dry-run, eksik scheduled run'ı maskelemez. Başlangıç grace period'u ve repo son push'u 50 günü aşınca bakım uyarısı vardır.

Hatalar/partial sonuçlar workflow exit 1 ve Actions `::error::` annotation üretir. Korunan kayıtlar `::warning::` ve güvenli Step Summary ile görünür. GitHub hesabında Actions failure e-posta/web bildirimlerini etkinleştirin; scheduled bildirimlerin alıcısı workflow/cron aktörüne bağlıdır. Bu teslimat doğrudan e-posta/Slack/issue mesajı göndermez. [GitHub bildirim davranışı](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs).

**Garanti sınırları:** GitHub schedule yoğunlukta gecikebilir, bazı işler atlanabilir; default branch'ten çalışır. Aynı concurrency grubunda bir bekleyen run yerine yenisi gelebilir; `cancel-in-progress:false` sınırsız kuyruk sağlamaz. Timeout veya runner kesintisinde metadata/commit sonucu belirsiz olabilir; sonraki deneme Stage 5 audit/snapshot ile güvenli uzlaşma yapar. [Schedule sınırları](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows), [concurrency davranışı](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency).

Public repoda 60 gün repository aktivitesi olmazsa scheduled workflow'lar otomatik devre dışı kalabilir. Aynı repodaki watchdog da durabileceğinden bu watchdog tek başına kesintisiz alarm garantisi değildir. Bağımsız dış izleme veya periyodik owner kontrolü gerekir; workflow disabled durumunda Actions ekranından yeniden etkinleştirin. Scheduled run veya keepalive çalışması repo aktivitesini kalıcı olarak garanti ediyormuş gibi sunulmaz; yapay/boş commit üretilmez. [GitHub workflow enable/disable belgesi](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).

48 saat güvenlik pahasına zorunlu silme süresi değildir. Saatlik kontrol, schedule gecikmesi ve arşiv/ilişki engeli yüzünden eski kayıt kalabilir. Uyarı, doğrulanmamış kaydı zorla silmek veya kritik metadata'yı budamak için yetki vermez. 1 GB %70/%80/%90 uyarıları ve güvenli VACUUM değerlendirmesi Stage 5'teki gibi korunur.

## Doğrulama

Yerel test komutu:

```powershell
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Bu geliştirmede **316 yerel test geçti**; iki workflow YAML olarak da parse edildi. Yeni testler secret'sız preflight, zorunlu dry-run, idempotent slot/audit, partial başarısızlık, redaksiyon, stale/disabled watchdog ve workflow güvenlik kısıtlarını kapsar. Test operasyonel silmeleri yalnız bellek SQLite'tadır.

İlk gerçek [Actions dry-run](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44890606821) çalıştırıldı: secretsız CI job'unda 315 test geçti; storage adımı eksik beş repository secret nedeniyle `ACTIONS_SECRETS_NOT_CONFIGURED` ile durdu. `LIVE_DELETE=NOT_RUN`. Bu sonuç gerçek PostgreSQL/R2 kontrolü başarılıymış gibi sunulmaz. Secret değerleri rapora eklenmez. Watchdog gerçek koşusunda yakalanan repository API kök URL 404 sorunu giderildi ve regresyon testi eklendi; son doğrulama sonucu [makine raporunda](reports/actions-retention-validation.json) izlenir.

Son [Actions doğrulama turu](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44891049207) secretsız test job’unda **316 test geçti**; storage dry-run aynı eksik repository secret engelinde kaldı. Düzeltilmiş [watchdog gerçek koşusu](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44891067934) başarılıdır; ilk schedule henüz oluşmadığı için bootstrap uyarılarını doğru biçimde raporladı. Bu, henüz gerçekleşmemiş scheduled arşiv/cleanup başarısı anlamına gelmez.
