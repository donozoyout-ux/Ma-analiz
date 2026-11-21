# Stage 7D.2 — depolama ve scheduler güvenilirliği

Başlangıç **ee38e18**, tarih **21 Kasım 2026**. Stage 3 verified-import/source identity, Stage 5 lifecycle/retention, Stage 6 scheduler/watchdog, Stage 7C/7D kontrollü import ve Stage 7D.1 doğrulama kodları incelendi. Mevcut arşivleme ve silme motoru yeniden yazılmadı.

**Bu görevde yeni production retry çalıştırılmadı. SQL_ARCHIVE_WRITE=NOT_RUN (yeni retry), R2_OVERWRITE=NOT_RUN, R2_DELETE=NOT_RUN, LIVE_DELETE=NOT_RUN; AUTO_RETENTION_DELETE_ENABLED=false, OPERATIONAL_DELETE_ENABLED=false.** Mevcut ayrı scheduled bakım işleri kendi yetkileriyle çalışır; push'un tetiklediği mevcut retention dry-run'ın SQL audit yazması yeni retry değildir. Gerçek kayıtların tarihleri değiştirilmedi.

## Canlı kanıt ve gözlem sınırı

Yeni kodun gerçek salt okunur storage/watchdog çalışmaları bu ilk kod teslimatında **NOT_VERIFIED / henüz NOT_RUN**; tamamlandıktan sonra sonuçlar aşağıya eklenecek. Stage 7D.1'in önceki kapasite/R2 sonucu yeni canlı gözlem gibi sunulmaz.

[GitHub API snapshot'ı](reports/stage7d2-actions-observation.json): gözlem **2026-11-21T18:23:22Z**. `schedule`, `workflow_dispatch` ve `push` ayrı kaydedildi. Son başarılı **scheduled archive**: [44927449780](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927449780), `16:58:19Z`, workflow success. Son başarılı **scheduled auto**: [44928703385](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44928703385), `17:11:45Z`, workflow success. Son **scheduled watchdog**: [44927261704](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927261704), `16:56:09Z`, failure. Bu eski watchdog logunun nedeni Stage 7D.1'de doğrulanan iki `NO_RECENT_SUCCESSFUL_*_SCHEDULE` uyarısıdır; SQL/R2 crash olduğu iddia edilmez.

Bu gözlemde archive başarısı yaklaşık **85**, auto yaklaşık **72 dakika** eskidir. Archive 45 dakikalık stale eşiğini aşmıştır; sürekli 15 dakika/saat çalışma kanıtlanamaz. Cron'un nominal hangi tetiklerini GitHub'ın atladığı kesin bilinmez. `created_at → run_started_at` görünen kuyruk süresi ölçülebilir, fakat bu, nominal cron → oluşturulma gecikmesinin tamamı değildir. Manuel/push başarıları schedule yerine kullanılmaz. API listesi workflow başına en fazla 100 run'dır; toplam sayı ve örnek kapsamı JSON'dadır. Eski manuel başarısız dry-run ve watchdog işleri de tarihleriyle korunur; düzeltilmiş eski hata güncel hataymış gibi sunulmaz.

## Read-only depolama ve audit kanıtı

`python -m app.jobs.retention_validation --output retention-validation.json` mevcut Stage 7D preflight'ını ve PostgreSQL **READ ONLY / REPEATABLE READ** transaction'ını kullanır. Mevcut beş Secret ile çalışan manuel preflight workflow'undaki `check=retention-validation` yolu korunur. Bu komut SQL audit bile yazmaz; R2'de yalnız HEAD/GET yapar.

Yeni eklenen `archive_runtime` alanı şunları gösterir:

- `archive_jobs` durum sayıları; PENDING/UPLOADING/UPLOADED/FAILED sayısı ve son **SQL ledger verified_at**. Bu timestamp tek başına yeni R2 readback kanıtı değildir.
- Archive/auto/manual-retry RUNNING işleri ve mevcut lease süresine göre IN_PROGRESS veya **UNKNOWN_RECONCILE_REQUIRED**. Timeout, rollback veya kesin başarısızlık varsayılmaz. 25 kayıt sınırı aşılırsa truncated bilgisiyle kanıt eksik sayılır.
- Son 25 lifecycle slotunun yalnız job türü, zamanları, güvenli durum/hata kodu, yazılan sayı ve idempotency anahtarının SHA-256 değeri. Ham hata, SQL, journal gövdesi, kullanıcı veya bağlantı adresi yayımlanmaz.

Önceki validation alanları aynen korunur: gerçek kapasite/table/index/dead tuple ölçümleri, metadata/prefix sayıları, source identity, her operasyonel kayıt için SHA-256/boyut/satır/metadata/tam içerik/SQL snapshot/ledger/R2 readback, yaş ve bağımlılık engelleri. Yeni doğrulama anı `plan.as_of` ile son SQL ledger zamanı ayrı raporlanır. Eksik/bozuk arşiv ya da tamamlanmamış tarama PASS değildir. `NO_ELIGIBLE_RECORDS`, silinebilecek kayıt bulunmadığını belirtir; canlı silme başarısı değildir.

## Watchdog durumları

Mevcut GitHub-only `assess` API'si ve eski PASS/FAILED alanı korunur; ek **state/states** alanları yanlış HEALTHY iddiasını engeller. Depolama kanıtı verilmeden legacy fonksiyonun PASS olması yalnız GitHub kontrolüdür; `state=INSUFFICIENT_EVIDENCE` kalır. Workflow'un gerçek main yolu depolama yoksa failure döndürür.

- **HEALTHY:** başarılı güncel schedule, ona run ID ile bağlı güncel SQL audit, tam/taze (<=10 dakika) gerçek readback, source identity ve korunmuş read-only metadata kanıtı birlikte gerekir. Başarılı fakat sıfır veri işleyen SKIPPED audit'i, doğrulanmış yeni arşiv yazımı diye sunulmaz.
- **SCHEDULE_DELAYED:** archive başarısı 30–45, auto başarısı 90–120 dakika yaşındaysa uyarı. Bunlar tolerans eşikleridir; cron garantisi değildir.
- **ARCHIVE_STALE / AUTO_STALE:** GitHub başarısı veya ilgili SQL audit 45/120 dakikayı aşarsa. Manuel audit scheduled başarının yerine geçmez; bağlantısı doğrulanamıyorsa INSUFFICIENT_EVIDENCE.
- **ARCHIVE_FAILED / AUTO_FAILED:** son scheduled iş failure/cancelled/timed_out vb. veya ilgili SQL audit FAILED/PARTIAL. Sonradan manuel başarı bu scheduled hatayı gizlemez.
- **STORAGE_RECONCILE_REQUIRED:** incomplete archive ledger, doğrulanamayan mevcut nesne/identity, expired/gelecek zamanlı RUNNING job veya R2 doğrulama hatası. Kritik kapasite de güvenli yazma durdurma incelemesi gerektirir.
- **INSUFFICIENT_EVIDENCE:** Secret/bağlantı, saat, tam tarama, runtime envanteri veya güncel kanıt eksikliği. E-posta/uptime/R2 yazma başarısı uydurulmaz.

Birden fazla durum `states` içinde korunur; öncelik storage-reconcile → failed → stale → insufficient → delayed → healthy. API/config/storage arızasında güvenli sabit kod ve JSON artifact üretilir; raw exception basılmaz.

`retention-watchdog.yml` aynı UTC `41 * * * *` zamanlamasını, actions/contents read iznini ve ayrı concurrency grubunu korur. Beş mevcut storage Secret'ı yalnız denetim adımında yeniden kullanılır; yeni credential oluşturulmadı. İki delete bayrağı adımda zorla false'dur. Kod/SQL transaction salt okunurdur; mevcut R2 anahtarının yetkileri bu işlemle teknik olarak daraltılmış olmaz. Ayrı RO credential istenirse ileride kullanıcı onayıyla oluşturulmalıdır. Workflow pinned dependency kurulumu, 8 dakikalık üst sınır ve 14 günlük güvenli artifact kullanır. Storage yavaşlığı/Secret eksikliği HEALTHY'ye çevrilmez. Watchdog **retry dispatch etmez**.

## Manuel archive retry — bu görevde production'da çalıştırılmadı

Yeni CLI, yalnız PostgreSQL'de zaten bulunan **tek maç UUID'si** için hazırlık yapar. Varsayılan salt okunur plandır:

```powershell
cd backend
python -m app.jobs.archive_retry --match-id <mevcut-maç-UUID>
```

İlk olarak gerçek Stage 7D.1 readonly preflight çalışır. Ardından native source identity, SQL snapshot, lifecycle partition/hash, izinler/teknik geçerlilik, child/FK sınırları, eski slotlar ve hedef arşiv makbuzları incelenir. R2'de aynı key varsa tam byte/içerik/metadata eşitliği zorunludur. Farklı içerik, farklı ledger checksum/boyut/satır veya DELETED ledger, güncel işler ve belirsiz kimlik **BLOCKED** olur. Eski timeout işi yalnız yaşına bakılarak rolled-back sayılmaz; gerçek SQL/R2 eşitliği yeniden incelenir.

**Yalnız sonraki açık manuel operatör işlemi için hazırlanmış komut; bu görevde ÇALIŞTIRILMADI:**

```powershell
python -m app.jobs.archive_retry --match-id <aynı-UUID> --execute --approve-plan <güncel-digest> --reason "İnceleme gerekçesi"
```

- Execute için ayrı açık argüman, güncel digest ve gerekçe gerekir. Her komut yeni readonly preflight yapar. Kritik kapasite (%90) yazma bağlantısından önce durdurur. Her iki silme bayrağı false değilse preflight durur.
- Mevcut Stage 5 **connection-scope global advisory lock**, SQLLedger ve `run_idempotent_job` kullanılır. Kilit archive tracker commit'leri boyunca tutulur. Claim sonrası ORM cache temizlenir, plan/snapshot tekrar karşılaştırılır.
- `archive_retry:<digest>` idempotency job'u ve korunan `archive-retry:<digest>` audit'i kullanılır. Başka bir scheduled slotun tüm kapsamı tamamlandı varsayılmaz veya RUNNING satırı sessizce SUCCESS'e çevrilmez. Yalnız tek maçın retry'si kanıtlanır; eski belirsiz slot gerekirse operatörce ayrıca tam kapsamla incelenmelidir.
- Aynı Stage 5 `archive_early` / `archive_partition` / SQLArchiveTracker kullanılır; yeni paralel bir Parquet formatı yoktur. Wrapper yalnız onaylı key/byte SHA için **put-if-absent** kabul eder; overwrite/delete metotları sabit hata verir. SQL guard yalnız metadata DML'sine ve okumalara izin verir; football INSERT/UPDATE, DELETE, WITH DELETE, DDL ve VACUUM bu yolda reddedilir. Bu, mevcut DB rolünün izinlerinin değiştirildiği iddiası değildir.
- Her komutta bir deneme; sonsuz veya otomatik retry yoktur. SQL ve R2 sonradan tekrar okunur; snapshot/receipt değişirse başarı verilmez. Commit/PUT acknowledgement kaybı ve partial sonuç **UNKNOWN_RECONCILE_REQUIRED** olur. Eski digest mevcut object/ledger durumu değişmişse yeniden kullanılamaz; yeni readonly plan incelenmelidir.
- SQL ledger VERIFIED fakat nesne eksikse mevcut immutable archive API ilk denemede makbuzu FAILED yapıp durabilir; sonraki **ayrı manuel komut ve yeni plan** aynı key'i put-if-absent ile güvenle yeniden kurabilir. Bu iki deneme otomatik döngüye dönüştürülmedi. Bozuk mevcut byte'lar hiçbir aşamada ezilmez.
- Aynı tamamlanmış plan/slot tekrarında duplicate job/maç/nesne oluşmaz. Başarıdan sonraki değişmiş ledger planı yalnız doğrulama yapan SKIPPED denemesine dönüşebilir; yeni nesne oluşturulmaz. Başarılı onarım, eski bütün scheduled slotun veya production DELETE'in doğrulandığı anlamına gelmez.

Retry için yeni GitHub schedule veya otomatik workflow eklenmedi. Yetkili operatör mevcut güvenli ortamında komutu manuel kullanabilir. CLI'ye bağlantı parolası/token argümanı verilmez; gerekçe loga taşınmaz. Secret değerleri sohbete/rapora yazılmaz.

## Bildirimler ve kapasite

GitHub annotations: stale/delayed archive/auto, storage-reconcile/R2 arızası, watchdog failure, partial repair ve PostgreSQL %70 WARNING / %80 HIGH / %90 CRITICAL için sabit kodlar kullanılır. Job failure ve güvenli artifact operatörün Actions ekranında görünür. Üçüncü taraf servis, e-posta/SMS/webhook gönderimi kurulmadı; gerçek e-posta teslimatı **NOT_VERIFIED**. Bildirim hesap tercihleri GitHub tarafında ayrıca yönetilir.

%90'da yeni manuel retry yazma bağlantısını açmaz. Veritabanına yeni operasyonel veri alımını durdurmak, arşivlenmemiş veriyi silmekten daha güvenlidir; Stage 7B/7D production importu hâlâ kapalıdır. Mevcut normal archive scheduler bu aşamada değiştirilmedi; kapsamlı kapasite backpressure/ingress politikası sonraki onaylı adımdır. Metadata tablo/index ve satır sayıları izlenir; job/audit/source identity/review kayıtları topluca budanmaz. Dead tuple/autovacuum yalnız okunur; normal VACUUM bile çalıştırılmadı. VACUUM FULL/DROP/TRUNCATE yoktur.

## Testler ve korunan akışlar

Mevcut **544** test korunur; yeni dosya **45 sentetik vaka** ekler. Yerel son tam sonuç **589 passed, 14 mevcut deprecation warning, 28,02 saniye**. SQLite + InMemoryObjectStore testleri gerçek kaynak kanıtı değildir.

Yeni testler normal arşiv, tekrar/idempotency, upload ve SQL acknowledgement kaybı, job timeout, ledger/object/identity/snapshot çelişkileri, eksik ve bozuk nesne, güncel ve stale RUNNING slot, CLI'nin varsayılan RO/manuel global lock yolu, destructive SQL/R2 reddi, kapasite pause, güvenli annotations/artifact ve watchdog durumlarını kapsar. Önceki Stage 7D.1 testleri aynı anda iki mocked PG runner, 48h sınırı, R2 doğrulanmayan/bekleyen maç/tahmin/analiz, rollback ve gerçek izole DELETE sonrası HistoricalRepository okumasını korur. Yeni production DELETE yapılmaz.

```powershell
python -m pytest -q tests/test_scheduler_reliability.py tests/test_actions_retention.py tests/test_retention_validation.py tests/test_auto_retention.py tests/test_lifecycle.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Render Free/web servisi, UptimeRobot health endpoint'leri, Start Command, frontend, collector, Stage 3/7D import yolları, retention delete motoru ve scheduler cron/concurrency değiştirilmedi. Watchdog yalnız readonly storage kanıtıyla güçlendirildi. Yeni migration/ücretli worker/cron/API veya Secret yoktur. Yeni retry production'da çalıştırılmadı; normal ayrı schedule'ların arşiv/audit faaliyetleri bu görevin retry yetkisi değildir.

## Canlı temizlik öncesindeki engeller ve kontrol listesi

- [ ] Ayrı açık kullanıcı onayı; bu görev canlı DELETE izni vermez.
- [ ] Güncel tam readonly plan, 48h dolan uygun kayıt ve bütün source identity/R2/ledger/dependency kontrolleri. Uygun kayıt yoksa NO_ELIGIBLE_RECORDS olarak kalır.
- [ ] Daha uzun gerçek schedule gözlemi; stale archive/auto ve başarısız watchdog uyarıları giderilmeli. Manuel başarı schedule'ın yerine sayılmamalı.
- [ ] Partial/RUNNING timeout slotları tam kapsamla incelenmeli; yalnız bir maçı onarmak bütün slotu başarılı saydırmamalı. İhtiyaç varsa yeni planla manuel retry; bozuk obje overwrite değil reconcile gerektirir.
- [ ] PostgreSQL/R2 yazma/DELETE izinleri ve gerçek production commit/readback sonraki ayrı onayda değerlendirilir. Bu görev salt okunur canlı kontrolü yazma başarısı saymaz.
- [ ] Metadata kapasitesi/dead tuple/autovacuum ve %70/%80/%90 uyarı takibi; storage kritikse güvenli ingress pause planı. Arşivlenmemiş veriyi zorla silmek hiçbir zaman çözüm değildir.
- [ ] Mevcut 48h ve kilitli hash/snapshot/R2 recheck/atomic cleanup audit korunmalı; SCHEDULED maç sırf yaşından dolayı silinmemeli. R2 kalıcı ve immutable kalmalı.

GitHub schedule gecikebilir/atlanabilir; public repo'da 60 günlük hareketsizlik schedule'ı kapatabilir. Aynı GitHub altyapısındaki watchdog kendi durmasını tamamen bağımsız algılayamaz. Kullanıcı kontrollü bağımsız izleme ve bildirim kontrolü gerekir; kesin 7/24 veya zorunlu 48 saat silme garantisi verilmez.
