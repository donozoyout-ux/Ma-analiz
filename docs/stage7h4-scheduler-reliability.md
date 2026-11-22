# Stage 7H.4 — Retention scheduler güvenilirliği

22 Kasım 2026 (TSİ). [Draft PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14), [issue #11](https://github.com/donozoyout-ux/Ma-analiz/issues/11). Başlangıç commit'i `3a19847`; Stage 7H.2/H.3 çalışmaları korunur. PR merge edilmez.

## İnceleme sınırı

Yalnız mevcut GitHub run/job metadata'sı, geçmiş loglar ve sentetik/offline testler kullanıldı. Production SQL bağlantısı açılmadı, R2 SDK çalıştırılmadı; maintenance workflow dispatch, import/migration/cleanup/Render değişikliği yok. Testler production credential ve gerçek `.env` içermeyen geçici checkout'ta yürütüldü.

[Makine tarafından okunabilir kanıt](reports/stage7h4-scheduler-evidence.json), gözlem anındaki tam **44 run** listesinin güvenli alanlarını içerir: **7 schedule, 6 workflow_dispatch, 14 push, 17 pull_request**. API total_count=44, tek sayfada 44 kayıt; bu incelemenin listesi kesilmiş değildir. Daha sonraki run'lar bu snapshot'a dahil sayılmaz. Saatler aşağıda **UTC**; TSİ=UTC+3.

## AUTO_STALE için doğrulanmış neden ve belirsiz kök neden

[Doğal watchdog 45011345910](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45011345910), **22 Kasım 10:13:48 UTC / 13:13:48 TSİ**:

- `state=AUTO_STALE`, `NO_RECENT_SUCCESSFUL_AUTO_SCHEDULE`, `AUTO_SQL_AUDIT_STALE`.
- Son doğal auto [44978081445](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44978081445), **02:52:43 UTC**: run_attempt=1, conclusion=success. Oluşturulma yaşı **26.465,9 saniye ≈ 441 dakika**; 120 dakikalık sınır gerçekten aşılmış.
- Auto'nun işlem özeti `storage_status=DRY_RUN`, ledger `SKIPPED`, deleted=0; yeni R2 yazımı veya cleanup başarısı değildir. Bu run Stage 7G öncesi olduğundan audit bağlantısı yalnız `LEGACY_RUN_ID_ONLY`; **auto v2 doğrulaması yapılmış değildir**.
- Yakın archive [45009618881](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45009618881), **09:52:09 UTC**: attempt=1, SQL v2 `RUN_AND_ATTEMPT` eşleşmesi true, `SKIPPED`, archive_records_written=0. Watchdog sırasında yaşı yaklaşık 21,7 dakika. Archive success auto'nun yerini almaz.
- Aynı watchdog snapshot'ında storage readback VERIFIED, kapasite HEALTHY, incomplete ledger=0, running job=0. Alarmın kanıtlanan nedeni **güncel doğal auto başarısı ve güncel auto audit bulunmaması**; arşiv kaybı/SQL veya R2 crash kanıtı yok.

İncelenen yedi doğal run'ın dördü archive, üçü auto; hepsi attempt=1 ve success. Auto creation zamanları 21 Kasım 17:11:45, 22:53:34 ve 22 Kasım 02:52:43 UTC. Sonraki gözlenen aralıkta yeni doğal auto kaydı yok. PR, push veya manuel başarı bu boşluğu doldurmaz. Nominal cron tetiklenme timestamp'i API'de olmadığından bu kayıp gözlem aralığı kesin "N tick düştü" ya da kesin gecikme süresi olarak sunulmaz.

**Platformun iç kök nedeni kanıtlanamadı.** GitHub schedule yoğunlukta gecikebilir/düşebilir, yalnız default branch'te çalışır; public repo hareketsizliğinde devre dışı kalabilir. Bunlar belgelenmiş olasılıklardır; bu olayın kesin nedeni olarak gösterilmez. [Resmî schedule koşulları](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).

## Concurrency/queue kanıtı

Production archive ve auto aynı `ma-analiz-retention` grubundadır; `cancel-in-progress: false`. PR grubu `-pr-N` ile ayrıdır. Scheduler/watchdog YAML dosyaları, cron, production grubu, job izinleri ve delete gate ifadeleri **bu aşamada değiştirilmedi**. SQL advisory lock ve slot idempotency de aynen kalır.

- Son auto'nun ilk job'u creation'dan 2 saniye sonra başladı, workflow yaklaşık 72 saniyede tamamlandı. Son archive ilk job'u 3 saniye sonra başladı, yaklaşık 81 saniyede tamamlandı. Bu gözlenen süreler saatler süren boşluğu açıklamıyor.
- 21 Kasım archive [44927449780](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927449780) retention job'u **16:59:29** bitti. 16:58:37'de oluşan push [44927475175](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927475175) ilk job'u **16:59:31** başladı: gözlenen başlangıç gecikmesi **54 saniye**. O commit'in workflow'u da aynı production grubunu kullanıyor. Seri çalışma/queue ile tutarlı; gecikmenin tümünün concurrency olduğu kanıtlanmış değildir (runner tahsisi de dahil olabilir).
- Bir iptal edilmiş push [44952787603](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44952787603) var. İptal edilmiş **doğal auto** kaydı gözlenmedi. Bu iptali auto açlığının nedeni diye sunmuyoruz. Queue nedeni ve GitHub'ın hiç üretmediği run'a ait bekleme süresi bu API kanıtından çıkarılamaz.

GitHub'ın varsayılan concurrency davranışında çalışan bir iş korunurken önceki pending iş yenisiyle değişebilir; sıralama/teslim garantisi yoktur. [Resmî concurrency koşulları](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency). Kanıt olmadan grup bölmek, archive/auto'yu paralelleştirmek, cron veya queue politikasını değiştirmek uygulanmadı. Böyle bir değişiklik kayıp schedule üretimini de garanti etmez.

## Kanıtlanabilir salt okunur kod düzeltmeleri

Yeni collector/worker veya production yazma yolu eklenmedi. Düzeltmeler sadece evidence projeksiyonu ve pure watchdog yorumlayıcısındadır:

1. **Run teslimatı ile attempt aktivitesi ayrıldı.** `created_at` doğal teslimat yaşını belirlemeye devam eder. Eski bir run'ın yeni başarısız attempt'i `run_started_at` üzerinden son aktivite olarak görünür; daha yeni oluşturulmuş eski bir başarı bunu gizleyemez. Başarılı manuel rerun eski creation saatini taze doğal schedule yapmaz. Pending rerun bütçesi de orijinal creation yaşına göre yanlış hesaplanmaz. İlk attempt'in queue beklemesi ise creation saatinden hesaplanmaya devam eder; bütçe genişletilmez.
2. **Duplicate son satır kazanır davranışı kaldırıldı.** Aynı run/attempt için birebir aynı API/audit satırları idempotenttir. Çelişen durumlar, satır sırasından bağımsız `*_SCHEDULE_OBSERVATION_CONFLICT` / `*_AUDIT_OBSERVATION_CONFLICT` ile reddedilir; crash teşhisi veya HEALTHY yapılmaz.
3. **V2 zaman penceresi doğrulanır.** SQL metadata'da zaten saklanan `started_at` read-only projeksiyonda artık kaybolmaz. GitHub `created_at <= run_started_at <= updated_at <= observed_at`; audit için `attempt_started_at <= audit.started_at <= audit.completed_at <= GitHub.updated_at` gerekir. Eksik, naive, gelecek veya ters saatler `*_TIME_WINDOW_NOT_VERIFIED` üretir. Yeni saat toleransı eklenmedi; farklı clock kaynağının ölçülmüş sapması ayrı değerlendirme gerektirir.
4. **Kimlik/sürüm fail-closed.** Eksik/geçersiz GitHub run kimliği veya attempt'i başarı kanıtı değildir. Boolean attempt, yanlış event veya farklı audit attempt kabul edilmez. Bilinmeyen SQL audit schema_version artık sessizce v1'e düşürülmez. Gerçek eski v1 kayıtlar açık legacy uyarısını korur ve v2 sayılmaz.
5. Watchdog normal/hata sonuçları advisory `SAFE_TO_DELETE=NO` içerir. Bu alan kapı ayarlarını değiştirmez. Stage 7H readiness sonucu hâlâ `SAFE_TO_DELETE_NO`; production silme yürütücüsüne bağlanmaz.

Archive 30/45 dakika, auto 90/120 dakika uyarı/stale sınırları korunur. Workflow yeşil, SKIPPED, DRY_RUN, duplicate veya bildirilen yazma sayısı tek başına yeni R2 verisi doğrulaması değildir.

## Test kanıtı

- Yeni dosya `backend/tests/test_stage7h4_scheduler.py`: **54 sentetik senaryo**. Hiç doğal run olmaması, diğer event'lerle yanlış iyileşme, sınırlar, geç completion, pending/failed/successful rerun, duplicate ve çelişki sıraları, kimlik/attempt/sürüm ve saat tutarsızlıkları, stale audit ve read-only projeksiyon sınandı.
- Başlangıç `3a19847` yorumlayıcısına karşı seçilen iki regression ailesi **4 beklenen başarısızlık** üretti: başarısız eski rerun ve çelişkili SQL attempt. Aynı durumlar düzeltmede geçiyor. Regression denemesi yalnız geçici, secretsız test kopyasında yapıldı. Yeni `SAFE_TO_DELETE` alanının eski sürümde olmaması regression nedeni olarak kullanılmadı; asıl state/conflict assertion'ları başarısızdı.
- Mevcut sentetik fixture'lar gerçek GitHub API'nin `id/run_attempt/status/run_started_at/updated_at` alanlarını taşır; uygulama güvenliği fixture'a uymak için gevşetilmedi. Önceki Stage 7H.2/H.3 schema ve recovery testleri korunur.
- Yerel tam güvenli paket: **757 passed, 15 skipped, 14 mevcut deprecation warning**; production secrets içermeyen checkout'ta çalıştı; konteyner testleri yerelde Docker olmadığı için ayrıca SKIPPED sayılır, başarı gibi sunulmaz. Son GitHub CI run bağlantısı ve job sonuçları PR açıklamasında ayrıca doğrulanır.

```bash
# Production secrets içermeyen checkout ve boş .env:
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

## Kalan riskler ve güvenli devam

Bu değişiklik **441 dakikalık doğal auto açlığını platformda çözmüş sayılmaz**. Kesin platform teşhisi veya 7/24 schedule garantisi yoktur. Mevcut gerçek loglarda yeni attempt>1 bulunmadığından rerun/conflict düzeltmeleri sentetik regression kanıtıdır; yaşanan boşluğun bunlardan kaynaklandığı iddia edilmez.

Yeni kod draft PR'da olduğu için production'da genişletilmiş v2 saat doğrulaması NOT_RUN. İleride açık inceleme/onay sonrası doğal archive/auto/watchdog döngülerinde karşılaştırma yapılmalı; alarm kapatmak için manuel production dispatch yapılmamalı. Tam v2 metadata'sı veya zaman penceresi eksikse sonuç konservatif olarak reddedilir.

Watchdog da GitHub üzerinde olduğundan aynı platformdaki schedule aksamasını her zaman ve anında yakalayamaz. Bağımsız bir platformdaki **salt okunur** monitör; son doğal auto/archive kimliği/yaşı, audit eşleşmesi ve güvenli rapor kodlarını izleyip alarm vermelidir. SQL/R2 yazma veya workflow dispatch yetkisi verilmemeli. Böyle bir dış monitör bu görevde oluşturulmadı; credentials/Slack mesajı gönderilmedi. Capacity ve unknown/partial archive blokları korunur; 48 saat doğrulanmamış veriyi silme gerekçesi değildir.

**SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN, SAFE_TO_DELETE=NO. AUTO_RETENTION_DELETE_ENABLED=false, OPERATIONAL_DELETE_ENABLED=false korunur.**
