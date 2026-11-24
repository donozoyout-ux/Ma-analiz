# Stage 7D — tek fikstür için kontrollü canlı import hazırlığı

Başlangıç: **d9955b0** (Render stabilite düzeltmesi), önceki import aşaması **6234585**. İnceleme: **21 Kasım 2026**. Stage 3 journal/import, Stage 5 lifecycle/48 saat/advisory lock, Stage 6 scheduler/watchdog ve Stage 7A/B/C kaynak/inceleme kuralları yeniden kullanıldı.

**Pilot sonucu: BLOCKED. SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN, FINAL_IMPORT=BLOCKED.** Bu görev canlı persist yetkisi vermez; uygun aday bulunamadığından yazma onayı istenmedi ve persist çalıştırılmadı. Yeni ücretli servis, import schedule'ı veya Secret oluşturulmadı.

## Gerçek tek günlük aday ve kaynak kanıtı

[Gerçek kaynak dry-run JSON'u](reports/stage7d-live-source-dry-run.json), 21 Kasım 2026 **17:23:53–17:25 UTC** / **20:23:53–20:25 Türkiye** aralığında alınan yeni HTTP yanıtlarını içerir. Seçilen kapsam baştan **22 Kasım 2026 UTC, Bundesliga** olarak sabitlendi. Yalnız mevcut maç haftası kaynağı ve tek OpenFootball dosyası okundu; başka gün/lig veya geçmiş sezon taranmadı. Kaynak dosyalarının bir maçtan geniş gövde döndürmesi import kapsamını genişletmez: normalizasyon yalnız seçilmiş gündedir, aday sayısı birdir.

- Maç: **FC Augsburg – 1. FSV Mainz 05**.
- OpenLigaDB native maç ID: **83240**; takım ID'leri **95 / 81**; lig **4937**, `bl1:2026`, kanonik lig `de.bl1`, sezon `2026-2027`.
- Birincil açık UTC kickoff: **2026-11-22T14:30:00Z**.
- Kaynak beyanı: **NOT_FINISHED**; gözlem anında gelecekteki fikstür. FINAL üretilmedi; skor/teknik istatistik/oran doldurulmadı.
- Birincil kaynak: `https://api.openligadb.de/getmatchdata/bl1/2026/10`; current-group kaynağı önce okunarak hafta kimliği doğrulandı.
- İkincil kaynak: `https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/de.1.json`. Tam sıralı takım adları eşleşti; `time=15:30` alanında timezone belirtilmedi. Bare `time` alanından timezone tahmini yapılmaz; açık UTC teyidi yoktur. Bu nedenle **INDEPENDENT_FIXTURE_UTC** başarısızdır, `technical_eligibility=false`, `production_ready=false` kalır.

Bu kayıtlar belirtilen gözlem anının kanıtıdır; sonradan dosya açılması onları tazelemez. Her import denemesi yeni HTTP 200 yanıtı ve en fazla **10 dakikalık** kanıt gerektirir. Kayıtlı JSON hiçbir zaman yazma yetkisi değildir. Stage 7B'nin sekiz karantina gözlemi otomatik aktarılmadı. Bundesliga dışındaki ligler için yeni import yolu açılmadı; Premier League pl/pl1 ve Süper Lig kaynak engelleri devam eder.

## Trusted revalidation ve izin

`app/providers/trusted_fixture.py` yalnız bugün/yarın için açıkça seçilen tek UTC gününü destekler. Kaynaklar sabit allowlist'tedir. Stage 7B'nin GET-only `CollectorHTTP` transport'u robots denetimini, host başına pacing'i, OpenLigaDB için 60 saniyelik aralığı, gövde/istek bütçesini, TLS doğrulamasını ve erişim engellerini korur. 403/429, robots veya TLS engelleri aşılmaz. Kalıcı yerel state yalnız pacing/cooldown içindir; HTTP cache her yeniden doğrulamada temizlenir. 304/cache yanıtları import kanıtı olamaz.

OpenLigaDB lisans/kullanım koşulları ile OpenFootball lisans belgesinin yeni gövdeleri mevcut incelenmiş parmak izlerine karşı doğrulanır. HTML'de yalnız mevcut semantik fingerprint yöntemi kullanılır; veri gövdelerinde SHA-256 doğrudan ham byte'lar üzerinden hesaplanır. İzin değerlendirmesi **21 Aralık 2026** sonrasında yeniden inceleme gerektirir. Değişen belge otomatik onaylanmaz. ODbL-1.0 kaynak atfı ve OpenFootball CC0-1.0 provenance'ı korunur. Robots izni, lisans izni yerine geçmez; ODbL atıf/yeniden dağıtım yükümlülükleri devam eder.

Trusted proof normalizer'ın yeni gövdelerinden üretilen, değişiklik tespiti için mühürlenmiş süreç-içi nesnedir. Enjekte test transport'u PostgreSQL capability'si açamaz. Aynı native ID'ye çelişkili iki kimlik dönerse seçimden **önce** reddedilir. Tam ad eşitliği dışında fuzzy merge yapılmaz. Bağımsız UTC, takım ID'leri, lig/sezon, tazelik, ham yanıt hash bağı veya lisans şartı eksikse operatör bunu override edemez.

## Aşama A — PostgreSQL/R2 preflight

`python -m app.jobs.live_import --preflight`:

- Mevcut Stage 5 `storage_session(readonly=True)` ile `SET TRANSACTION READ ONLY`, 10 saniye connect ve 30 saniye statement sınırı; iş sonunda rollback/dispose.
- Alembic revision/table/column incelemesi; **migration/stamp/create_all yoktur**.
- Native maç, sonuç, archive ledger ve metadata anahtarlarının uniqueness denetimi; mevcut UUID, timezone-aware timestamp ve JSONB tiplerinin kontrolü.
- Kapasite ölçümü ve mevcut %70/%80/%90 uyarıları.
- R2 `HEAD_BUCKET`; yalnız eski Bayern–Köln journal'ının işaret ettiği nesnelerde HEAD/GET. SHA-256, boyut, satır sayısı, tam içerik ve SQL ledger/readback doğrulaması.
- Eski Bayern–Köln operasyonel kayıt/sonuç, UUID, source index ve journal için baseline hash'i; varsa doğrulanmış retention sonrası ARCHIVED_ONLY ayrımı. Eksik zincir PASS değildir.
- Her iki silme bayrağı false değilse durur. Yazma yetkileri **NOT_EXERCISED**; salt okunur başarı INSERT/PUT izninin denenmesi değildir.

[Yerel preflight](reports/stage7d-local-preflight.json): **BLOCKED / STORAGE_CONFIGURATION_INCOMPLETE**. DATABASE_URL, R2_ENDPOINT, R2_ACCESS_KEY_ID ve R2_SECRET_ACCESS_KEY eksik; değerleri yayımlanmadı. Yerelde gerçek PG/R2 bağlantısı **NOT_RUN**.

Gerçek ortam kontrolü için ayrı `.github/workflows/import-preflight.yml` yalnız **workflow_dispatch** kabul eder. Mevcut beş Actions Secret'ını kullanır; operatör Secret'ı yoktur. Önce secretsız tam güvenli testler, sonra salt okunur preflight ve 14 günlük güvenli JSON artifact'i üretir. SQL audit yazmaz; artifact ve Actions logu denetim kanıtıdır. Üretim import komutu, approve yolu veya schedule bu workflow'da yoktur.

**Gerçek Actions preflight PASS:** [44931543935](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44931543935), commit **11f5206**, `workflow_dispatch`, job **136929046863**, bütün adımlar success. [Kalıcı güvenli kanıt JSON'u](reports/stage7d-actions-preflight.json) job stdout'undaki preflight sonucundan alınmıştır; artifact ID **14718066543**. Kontrol başlangıcı **17:43:30 UTC / 20:43:30 Türkiye**:

- PostgreSQL: **CONNECTED_READ_ONLY**; şema **READY**, Alembic head **0010_active_statistics**, gerekli uniqueness/tip kontrolleri **PASS**.
- R2: HEAD_BUCKET **ACCESSIBLE**; eski matches/results nesneleri SHA-256, birer satır, metadata, tam içerik ve SQL ledger/readback bakımından **PASS**.
- Bayern–Köln zinciri **VERIFIED**; mevcut UUID **e168fa17-c0f1-4e42-96c6-adf260d16bd8**, 1–1 FINAL sonuç doğrulaması ve journal korunuyor.
- PostgreSQL **8.910.527 byte / 1.000.000.000 byte**, yaklaşık **8,91 MB / %0,89**, **HEALTHY**.
- **SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN**, iki silme bayrağı false; migration ve yazma izinlerini deneme **NOT_RUN / NOT_EXERCISED**.
- Aynı Linux runner'da tam secretsız test paketi: **520 passed, 14 warnings, 18,19 saniye**.

Salt okunur GitHub API kontrolünde `AUTO_RETENTION_DELETE_ENABLED` repository variable'ı **yoktu (404 / NOT_SET_DEFAULT_FALSE)**; mevcut scheduler bu durumda false kullanır. Variable veya Secret değiştirilmedi. Kod commit'inin tetiklediği mevcut [Retention dry-run 44931523049](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44931523049) da success tamamlandı; bu importer'ın çalıştırılması değildir.

Bu A aşamasının gerçek okuma kanıtıdır; bağımsız UTC şartını sağlayamayan B aşamasını geçerli hale getirmez. Kaynak ve preflight gözlemlerinin sonradan tazeliği varsayılmaz. Yazma izni/INSERT/PUT veya canlı import başarısı iddia edilmez.

## Aşama B — dry-run komutları

Backend klasöründe, uygulamanın mevcut DATABASE_URL ve beş R2 değişkeni güvenli ortamdan sağlanır. Değerleri terminal geçmişine/sohbete yazmayın.

```powershell
cd backend
python -m app.jobs.live_import --preflight --output stage7d-preflight.json
python -m app.jobs.live_import --dry-run --date 2026-11-22 --match-id 83240 --output stage7d-dry-run.json
```

Tarih örneği bu raporun tek günlük gözlemine aittir; sonraki günlerde otomatik geçmiş taraması yapılmaz, geçersiz gün reddedilir. `--date` olmadan varsayılan mod hiçbir kaynak isteği/yazma yapmaz. Dry-run kanıtı, SQL scope/baseline hash'leri, alan kontrolleri ve karantina nedenlerini gösterir; kalıcı review metadata'sı bile yazmaz. BLOCKED komutlar exit 1 döndürür; bu beklenen güvenli sonuçtur.

## Operatör onayı ve Aşama C — bu görevde çalıştırılmadı

Production adaptörü, Stage 7C'nin varsayılan SQLite-only kapısını yalnız **session/store'a bağlı kısa süreli capability** içinde açar. Eski `controlled_import --persist` komutu hâlâ kapalıdır. Yeni komut da kayıtlı collector JSON'u, `production_ready=true` veya bir Boolean env bayrağıyla açılamaz.

Sonraki ayrı kullanıcı onayından sonra güvenilen operatör ortamında `CONTROLLED_IMPORT_OPERATOR_ID` ve `CONTROLLED_IMPORT_OPERATOR_TOKEN_SHA256` yönetici tarafından güvenle ayarlanmalıdır. Token en az 32 karakterdir; gerçek token CLI argümanı, JSON veya logdan alınmaz, `getpass` ile gizli prompt kullanılır. Bu görevde bu konfigürasyon kurulmadı; hiçbir yeni Secret eklenmedi. Token hash'i güvenilen konfigürasyondur; kullanıcıdan gelen rapor içinden alınmaz.

Onay HMAC-SHA256 imzalı, tek aday/native ID, evidence hash, ham yanıtları içeren source version, gerekçe, operatör, oluşturulma/son kullanma zamanı ve salt okunur SQL scope hash'ine bağlıdır. En fazla 10 dakika geçerlidir. Kaynak byte'ları/kimlik veya SQL scope değişirse yeni onay gerekir. Yeni HTTP yanıtının gözlem zamanları değişebilir; onaylı kaynak sürümü değişemez. Onay dosyası yerelde exclusive-create ile yazılır; aynı dosya ezilmez. Token dosyaya konulmaz. Dosyayı koruyun ve Git'e eklemeyin.

**Aşağıdaki komutlar sadece gelecekteki, ayrıca açık kullanıcı izni verilen uygun bir aday içindir. Bu aşamada çalıştırılmadı; 83240 mevcut kanıtla onaylanamaz.**

```powershell
python -m app.jobs.live_import --approve --date <YYYY-MM-DD> --match-id <ID> --reason "İnceleme gerekçesi" --approval <yerel-onay.json>
python -m app.jobs.live_import --persist --approval <yerel-onay.json> --confirm-match-id <ID>
```

Persist token/imza/scope kontrolünden sonra kaynağı yeniden okur, salt okunur preflight yapar; PostgreSQL bağlantısı ancak bundan sonra açılır. Stage 3'ün **lifecycle global lock → verified-import global lock → fixture lock** sırası aynı bağlantıda korunur. Lock'lar archive tracker commit'leri boyunca yaşar; meşgul lock beklemeden durdurur. TLS/search_path bağlantı seçenekleri değiştirilmez. Statement/lock timeout'ları sınırlıdır.

Operatör audit'i, kaynak/evidence/review kayıtları, source-identity rezervasyonu, tek SCHEDULED ActiveMatch UUID'si ve SQL_COMMITTED journal'ı aynı ilk SQL transaction'ında kaydedilir. Bu yol MatchResult, odds, statistics veya prediction oluşturmaz. Mevcut native kayıt, bilinmeyen alias veya çelişkili yakın fikstür sessizce merge edilmez. Journal'sız R2 nesnesinden SQL kaydı diriltilmez.

## Değişmez R2 arşivi ve partial recovery

SQL kaydından mevcut lifecycle snapshot'ı üretilir. Mevcut `archive_partition` / `SQLArchiveTracker` ve lifecycle verifier kullanılır: Zstandard Parquet, `season=2026-2027/league=de.bl1/date=<UTC>/part=lifecycle-<reference>-<sha>.parquet`, tek satır. Yalnız hesaplanan bu key için `put-if-absent` capability'si vardır. Delete/overwrite metotları sabit hata verir. Var olan farklı byte'lar asla ezilmez.

SQL/R2 arasında dağıtık transaction varmış gibi davranılmaz. Upload/readback veya commit acknowledgement hatası **UNKNOWN_RECONCILE_REQUIRED / UNKNOWN_REVERIFY_REQUIRED** olur; başarı veya kesin rollback iddia edilmez. SQL journal + audit olmadan nesne yazılmaz. Archive ledger, SHA-256, boyut, row_count, tam içerik ve SQL snapshot readback tamamlanınca ARCHIVE_VERIFIED audit'i kaydedilir.

Aynı geçerli grant ile yeniden çalışma mevcut journal/UUID'yi doğrular; ikinci maç üretmez. Kanıt eskimişse yeni fikstür insert'i yerine yalnız daha önce commit edilmiş snapshot'ın recovery yolu kullanılmalıdır. Recovery, ayrıca operatör token'ı ve gerekçesiyle imzalanan kısa süreli journal/baseline snapshot onayı ister; kaynak verisi güncellemez, maç eklemez ve eski onayı yeni veri için uzatmaz. Review iptali/CONFLICT/yeni çelişkili kaynak sürümü, SQL değişimi veya identity proof eksikliği recovery'yi de durdurur.

```powershell
# Yalnız ayrı kullanıcı izniyle, önceden SQL_COMMITTED/ARCHIVE_VERIFIED journal'ı olan tek ID:
python -m app.jobs.live_import --approve-recovery --confirm-match-id <ID> --reason "Kısmi işlem incelemesi" --approval <yerel-recovery.json>
python -m app.jobs.live_import --recover --confirm-match-id <ID> --approval <yerel-recovery.json>
```

Recovery ve import audit metadata'sı mevcut korunan `system_settings` alanındadır; yeni migration gerekmez. Stage 7C evidence/review kapasite sınırları korunur; kapasite dolarsa durur, metadata silinmez. Uzun vadeli metadata arşivleme/compaction bu pilotta açılmadı; mevcut boyut/metadata uyarıları izlenmelidir.

## Testler ve korunmuş altyapı

Sentetik testler gerçek kaynak kanıtı değildir. SQLite + bellek object store ile yeni operator capability ve paylaşılan import/archive kodları uçtan uca çalıştırıldı. PostgreSQL advisory lock sırası/meşgul lock/engine override mocked bağlantıyla; gerçek PG şema/okuma ise ayrı preflight ile denetlenir. Canlı INSERT/PUT davranışı test edilmiş gibi sunulmaz.

```powershell
python -m pytest -q tests/test_live_import.py tests/test_controlled_import.py tests/test_verified_import.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Tam sonuç: **520 passed, 14 mevcut deprecation warning**. Kapsam: token/imza/expiry/scope, kaydedilmiş veya test transport kanıtının PG'yi açamaması, UTC/takım/native/lisans değişimi, uygun aday olmaması, duplicate native çatışması, transaction rollback/ack kaybı, upload/ack/readback arızası, açık recovery, revocation, idempotency, immutable arşiv, audit ve eski Bayern baseline'ının korunması.

Render Start Command, supervisor, Next/FastAPI health endpoint'leri, `/health/ready`, frontend ve mevcut collector/retention/watchdog/keepalive workflow'ları değiştirilmedi. Mevcut bakım otomasyonunun bağımsız çalışmaları bu pilot değildir. Yeni importer hiçbir DELETE/DROP/TRUNCATE, R2 delete/overwrite veya migration çalıştırmaz. AUTO_RETENTION_DELETE_ENABLED ve OPERATIONAL_DELETE_ENABLED varsayılan false kalır. SCHEDULED kaydın 48 saat geçti diye silinmesini engelleyen mevcut retention bağımlılık/durum kontrolleri korunur. R2 kalıcıdır.

## Canlı pilot için kalan koşullar

Bağımsız, güncel, izinli kaynakta aynı takım kimliği/isimleriyle **açık UTC** fikstür teyidi bulunmalı; kaynağın lisans/policy incelemesi geçerli olmalı. Yeni gerçek adayın A/B kontrolü başarılı olmalı; operatör ortamı güvenle kurulmalı ve kullanıcı **o tek ID için ayrıca açık persist izni** vermeli. Bunlar olmadan Stage 7D canlı yazma **BLOCKED** kalır. FINAL için ayrıca bağımsız bitiş/skor doğrulama adapter'ı gerekir; bu aşamada açılmadı.
