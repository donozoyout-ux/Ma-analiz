# Stage 7G — Scheduler ve audit güvenilirliği

22 Kasım 2026 (TSİ). Issue [#11](https://github.com/donozoyout-ux/Ma-analiz/issues/11).

Bu değişiklik zamanlama/işlem/arşiv kanıtını ayırır. GitHub'ın cron teslimatını garanti etmez ve uzun çalışma boşluklarını yeşile çevirmek için eşikleri büyütmez. PR inceleme için açık bırakılır; canlı maintenance dispatch veya yeni SQL/R2 bağlantısı çalıştırılmadı.

## Gerçek gözlemler ve nedenler

[Güvenli kanıt özeti](reports/stage7g-scheduler-evidence.json), mevcut GitHub REST run listeleri, geçmiş job logları ve önceden üretilmiş read-only artifact'tan çıkarıldı. Aşağıdaki saatler **UTC**; TSİ = UTC+3. Sentetik unit testleri bu gözlemlerden ayrıdır.

- [44935839044](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44935839044), manuel watchdog, 21 Kasım 18:30 UTC: `ARCHIVE_STALE`, `ARCHIVE_SQL_AUDIT_STALE`. Son başarılı archive 16:58:19, yaklaşık **92 dakika** eski; audit run kimliği eşleşiyor. Storage readback VERIFIED. Bu alarmın nedeni güncel scheduled archive kanıtı olmamasıdır; checksum bozukluğu kanıtı değildir.
- [44956133644](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44956133644), scheduled watchdog, 21 Kasım 22:20 UTC: `AUTO_STALE`, `AUTO_SQL_AUDIT_STALE`. Auto 17:11:45'ten beri yaklaşık **308 dakika** eski; eşleşme true, readback VERIFIED.
- [44976919486](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44976919486), scheduled watchdog, 22 Kasım 02:37 UTC: auto yaklaşık **224 dakika** eski; aynı ayrım geçerli.
- Archive [44955084514](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44955084514) ve [44975402609](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44975402609): GitHub success, işlem/depoma durumu `SKIPPED`, scanned=0, archive_failures=0, audit RECORDED. **Yeni R2 yazımı kanıtı yok.**
- Auto [44978081445](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44978081445): GitHub success, ledger `SKIPPED`, işlem `DRY_RUN`, scanned=2, deleted=0. Temizlik yapılmış sayılmaz.
- Mevcut [read-only rapor 44955849151](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44955849151), 21 Kasım 22:17 UTC: `NO_ELIGIBLE_RECORDS`, over_48h=0, eligible=0, scan_complete=true, read_only=on, metadata unchanged, protected target VERIFIED, incomplete ledgers=0, capacity HEALTHY. Bu **geçmiş bir snapshot**, güncel canlı kapasite veya arşivin sürekli güncel olduğunun kanıtı değildir.

İncelenen API geçmişinde scheduled archive'lar arasında saatler süren boşluklar var. GitHub schedule **gecikebilir veya yoğunlukta düşebilir**; nominal cron tetiklenme zamanı run API'sinde bulunmadığından belirli bir eksik tick'in kesin düşme nedeni çıkarılamaz. [Resmî schedule koşulları](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule). Workflow active; incelenen scheduled retention run'ları success. Bu yüzden eldeki kanıtla bir process/SQL/R2 crash'i veya kesin GitHub iç nedenini iddia etmiyoruz.

## Kodda giderilen ayrımlar

- Watchdog yalnız `event=schedule` ve default branch için, en fazla üç sayfa/300 run okur. Push/manual yoğunluğu son 100 kaydı doldurup schedule kanıtını gizleyemez. Limit dolarsa `SCHEDULE_RUN_HISTORY_TRUNCATED` raporlanır.
- Mod adı tam eşleşir. Aynı run'ın tekrarlanan API satırları ve farklı attempt'leri birleştirilir; eski başarılı attempt yeni başarısız/pending attempt'i gizleyemez.
- Archive uyarı/stale **30/45 dakika**, auto **90/120 dakika** olarak kaldı. Kısa gecikme `SCHEDULE_DELAYED`; gerçek failure/cancel ayrı kod. Pending run başarı sayılmaz, stale saatini sıfırlamaz; 8+12 dakikalık job bütçesini aşarsa `*_RUN_EXCEEDED_BUDGET` çıkar. Bu, crash teşhisi değildir.
- `observed_gap_intervals` yalnız gözlenen run creation aralığını ölçer; kesin düşen cron adedi veya gecikme süresi değildir. Yakın zamanda gelen tek run, önceki tüm aralıkların başarılı olduğu iddiasına dönüştürülmez.
- SQL audit JSON v2: event, run_id, run_attempt, gerçek işlem başlangıç/bitiş zamanları, ledger ve storage durumları, invocation'ın çalışıp çalışmadığı, arşiv yazma sayısı/hataları. Eski kod completion alanına invocation başlangıcını yazıyordu.
- Mevcut `SystemSetting` anahtarı kullanılır; **migration yok**. Manuel run son genel görünümü güncellese de `scheduled_runs` altında her modun son 32 invocation'ı korunur. Kalıcı Stage 5 JobRun ledger'ı, slot anahtarları, advisory lock, idempotency ve kritik kayıtlar değiştirilmez/silinmez.
- SQL/GitHub eşleşmesi v2 için run **ve attempt ve schedule event** gerektirir. Geçişte eski kayıtlar yalnız run ID / attempt=1 ile sınırlı tanınır ve `*_LEGACY_AUDIT_RUN_ID_ONLY` uyarısı alır; tam v2 kanıtı gibi sunulmaz.
- Duplicate invocation önceki run'ın yazma sayısını sahiplenmez: `execution_status=NOT_EXECUTED`, `storage_status=NOT_RUN`, archive_records_written=0. `SUCCESS`, `SKIPPED`, `DRY_RUN`, `FAILED`, `IDEMPOTENT_DUPLICATE` ayrı yorumlanır. `REPORTED_WRITES` bile tek başına yeni R2 veri doğrulaması değildir; `new_r2_data_verified=false` kalır, readback kanıtı ayrı alandadır.
- Başarısız R2 doğrulaması `STORAGE_RECONCILE_REQUIRED` / `R2_VERIFICATION_FAILED` üretir; storage evidence VERIFIED olamaz. SQL audit stale ve link eksikliği ayrı kodlardır.
- Arşiv invocation özeti scrub edildikten sonra attempt'e özgü kısa ömürlü artifact olur. Watchdog artifact adı da attempt içerir.

## Uyarı sözleşmesi

Watchdog JSON'undaki `alert`: schema_version=1, severity (`ERROR`/`WARNING`/`INFO`), sabit makine kodları, state, sayısal run/attempt kimlikleri ve SHA-256 `dedup_key` içerir. Zaman/age değişimi tek başına yeni incident anahtarı üretmez; yeni run/attempt veya farklı hata kodu üretir.

Örnek kodlar: `ARCHIVE_SQL_AUDIT_STALE`, `AUTO_AUDIT_SCHEDULE_LINK_NOT_VERIFIED`, `ARCHIVE_RUN_PENDING`, `AUTO_RUN_EXCEEDED_BUDGET`, `R2_VERIFICATION_FAILED`. Gelecek Slack tüketicisi bu alanları kullanabilir. **Slack mesajı gönderilmez; yeni token/webhook/secret istenmez veya loglanmaz.** İletim ve kalıcı dedup tüketicisi bu PR'ın kapsamı dışında.

## Test ve çalışma sınırı

- **643 passed, 14 mevcut deprecation warning**: backend'in güvenli tam paketi; yeni Stage 7G dosyasında 27 senaryo. SQLite/bellek R2 ve sentetik saat/run/audit verileri kullanılır.
- Eksik schedule, kısa/geç çalışma, aşırı queue, eski/eksik/gelecek SQL audit, event/attempt uyuşmazlığı, R2 doğrulama hatası, duplicate ve tekrar attempt, status ayrımı, bounded pagination/history, secret içermeyen artifact/alert ve PR izolasyonu sınandı.
- YAML parse, unchanged cron/permissions/delete gates ve `git diff --check` PASS.
- Tam paket, tracked dosyaların ve yeni testin **izole geçici kopyasında**, boş `.env` ve production credential içermeyen process environment ile çalıştırıldı. İlk env-file override denemesi iki config-loading testini test kurulumundan dolayı başarısız kıldı; uygulama değiştirilmeden bu izole kopya yöntemiyle tüm paket geçti.

```bash
# Yalnız secretsız test checkout'unda; boş .env gerektiren config testleri var.
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Live integration/Goaloo testleri bilinçli dışarıda. `test_backup_before_migration.py` kullanıcıya ait görev dışı untracked dosya; değiştirilmedi ve pakete eklenmedi. Yerel gerçek `.env` değiştirilmedi.

PR tetikleyicisinde yalnız secretsız `validate` job'u çalışır; `retention` job'u açıkça engellidir. PR concurrency grubu `-pr-N` suffix'iyle production grubundan ayrılır. PR push'u maintenance veya SQL audit çalıştırmaz. Mevcut main push/manual/schedule davranışı, cron'lar ve delete gate ifadeleri korunur.

## İnceleme sonrası kalan riskler

PR açıkken yeni v2 audit'in production scheduled döngüsüyle eşleşmesi **NOT_RUN**. Merge sonrası doğal scheduled run'lar üzerinde birkaç ardışık döngü salt okunur izlenmeli; manuel archive/auto tetikleyerek alarm kapatılmamalı. Eski alarm nedenleri gerçek olduğundan bu PR uzun boşlukları otomatik çözmüş sayılmaz.

Watchdog da GitHub schedule'a bağlıdır; aynı platform kesintisini bağımsız ve anında tespit garantisi vermez. Public repo 60 günlük hareketsizlikte iki workflow da durabilir. Saat başına ping/tekrar dispatch veya 48 saat doldu diye doğrulanmamış veri silme çözüm değildir. Bağımsız read-only monitör/Slack tüketicisi ayrıca değerlendirilmeli.

Concurrency'de `cancel-in-progress: false` çalışan işi korur, tüm pending işleri koruma garantisi vermez; GitHub varsayılan olarak bir pending run tutar. [Resmî concurrency koşulları](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency). Queue politikası ve cron bu PR'da kanıtsız değiştirilmedi.

**Bu görev: SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN. AUTO_RETENTION_DELETE_ENABLED=false ve OPERATIONAL_DELETE_ENABLED=false korundu. Migration/import/cleanup/maintenance dispatch veya Render değişikliği yapılmadı.**
