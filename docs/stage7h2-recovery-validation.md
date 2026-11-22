# Stage 7H.2 — İzole PostgreSQL 16 kurtarma doğrulaması

22 Kasım 2026 (TSİ). [PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14), [issue #13](https://github.com/donozoyout-ux/Ma-analiz/issues/13).

Bu çalışma production yedeği almaz. Yalnız PR-only PostgreSQL 16 CI konteynerinde oluşturulan sentetik veritabanını başka boş test veritabanlarına geri yükler. Yeni uygulama backup/restore komutu veya production yazma yolu eklenmez. Test harness'i `backend/tests` altındadır. PR merge edilmez.

## İzolasyon

- Explicit `STAGE7H_RECOVERY_ENABLED=true` ve Docker service container kimliği gerekir. Varsayılan tam test paketinde konteyner testleri SKIPPED olur.
- Yalnız loopback host, bilinen test rolü/parolası, `stage7h_ci` kontrol DB'si ve queriesiz bağlantı kabul edilir. `DATABASE_URL`, dolu `R2_*`, libpq override ortamı veya açık delete gate reddedilir.
- Docker inspect ile `postgres:16` image, sentetik init env, çalışan konteyner ve loopback port mapping doğrulanır. SQL bağlantısından ve konteyner içinden okunan PostgreSQL cluster system identifier eşleşmeden veri oluşturulmaz. Server ve dump/restore araçlarının major sürümü 16 olmalıdır.
- Fixture yalnız kendi rastgele token'ı ile oluşturduğu `stage7h2_<token>_*` veritabanlarını yönetir. Kaynak ve hedef farklıdır; dolu hedefe restore engellenir. Teardown yalnız bu sahiplik listesindeki test DB'lerini kaldırır. Force terminate/production DROP yoktur.
- SQL modelleri `Base.metadata.create_all` ile yalnız boş fixture kaynak DB'sinde oluşturulur. Alembic migration, importer, scheduler veya retention cleanup başlatılmaz. Önceden var olan 4 lifecycle testi de aynı geçici CI servisi içinde kalır.

## Sentetik fixture ve karşılaştırma

Bir operasyonel ve bir yalnız arşivden okunan maç UUID'si kullanılır. Kaynak sağlayıcı alanları açık `SYNTHETIC-RECOVERY-*` kimlikleridir; gerçek bir maçın yeni doğrulaması değildir.

Fixture; final skor, analiz, sonuçlanmış tahmin, kupon ve kupon öğesi, sentetik kullanıcı, source identity/review metadata, job audit ve 9 archive ledger içerir. Lifecycle'ın mevcut **salt veri formatlayıcısı** ile 9 Parquet nesnesi bellekte oluşturulur; gerçek R2 istemcisi kullanılmaz. Tarihsel fixture'ın operasyonel SQL satırları yalnız bu disposable kaynak DB'sinden kaldırılır. Böylece restore sonrası history okuması aktif SQL satırlarına dayanamaz.

`pg_dump --format=custom --no-owner --no-privileges` konteyner içindeki PostgreSQL 16 aracıyla çalışır. Dump'ın SHA-256 değeri kaydedilir. `pg_restore --single-transaction --exit-on-error --no-owner --no-privileges` ayrı ve boş hedefe çalışır; `--clean`/`--create` kullanılmaz. [pg_dump 16](https://www.postgresql.org/docs/16/app-pgdump.html), [pg_restore 16](https://www.postgresql.org/docs/16/app-pgrestore.html).

Sadece tool exit=0 yeterli değildir. Tüm public tabloların tam satır içeriği, satır sayısı, kolon tanımı, PK/unique/check/FK tanımları ve indexler karşılaştırılır. Satırlar UUID, timezone, numeric ve JSON alanlarıyla canonical hash'e dönüştürülür; rapora satır verisi yazılmaz. Üç orphan ilişki güncellemesi gerçek PostgreSQL FK ihlali (`23503`) ile reddedilmelidir.

Restore edilen ledger üzerinden her sentetik Parquet nesnesinde SHA-256, byte size, row_count, metadata ve tam decoded içerik eşitliği sınanır. Match Center HTTP testleri dependency override ile yalnız restore edilmiş test DB'sini **READ ONLY** okur: active/history/all listeleri ve hem SQL hem R2-backed detay, aynı UUID/skor/analizle çalışmalıdır. Production read engine çağrısı yasaklanır.

## Negatif ve tekrar senaryoları

- Eksik dosya: güvenlik kapısı ve gerçek `pg_restore` nonzero sonucu; hedef boş kalır.
- Truncated custom dump: gerçek `pg_restore` nonzero; single transaction sonrasında hedef boş kalır.
- Geçerli schema-only dump: tool exit=0 olsa bile eksik satır içeriği nedeniyle `RESTORE_CONTENT_MISMATCH`; recovery başarısı sayılmaz.
- Değişmiş backup SHA-256: SQL restore öncesi reddedilir.
- Dolu hedefe tekrar restore: overwrite yapılmadan reddedilir. Aynı yedek ikinci boş hedefe tekrar başarıyla yüklenmelidir.
- Restore sonrası eksik/bozuk sentetik Parquet: ledger VERIFIED olsa bile Match Center 503 döndürmelidir.

Raporun PASS olması için tüm 11 recovery kontrolünün tamamlanması ve test DB teardown'unun başarılı olması gerekir. Assertion/setup/restore hatası, eksik kontrol veya başarısız teardown PASS raporu üretemez. Artifact yalnız sayı/hash/durum JSON'udur; SQL dump, Parquet payload veya satır verisi yüklenmez.

## Çalıştırma ve sonuçların kapsamı

Yerel Docker daemon kapalı olduğundan gerçek PostgreSQL testleri yerelde çalıştırılmış sayılmaz. Boş `.env` ve production credential içermeyen izole test kopyasında genel paket: **686 passed, 15 skipped, 14 mevcut deprecation warning**. Atlananlar 4 eski PostgreSQL contract + 11 yeni recovery testidir. 18 yeni offline guard kontrolü genel pakete dahildir.

[Gerçek CI 45011430138](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45011430138), test edilen commit `1d9529d`:

- `validate`: **686 passed, 15 skipped**, 14 mevcut deprecation warning.
- `postgres-contract`: **15 passed**, 1 mevcut warning; 11 yeni recovery + 4 önceki lifecycle contract testi.
- Production `retention` job'u **SKIPPED**, step çalışmadı.
- Kurtarma artifact'ı `status=PASS`, server_major=16, **17 tablo / 20 sentetik SQL satırı / 9 Parquet nesnesi**. Her recovery kontrolü PASS, disposable DB teardown PASS.
- **9 FK** tanımı karşılaştırıldı; 3 gerçek orphan güncellemesi 23503, 2 geçersiz status güncellemesi 23514 ile reddedildi. Restore edilmiş DB'de 3 HTTP liste ve 2 detay okuması PASS.
- Eksik/truncated/checksum/schema-only dump sonuçları raporda `restore_status=FAILED`; bu beklenen redlerin test PASS'i, bozuk backup'ın başarıyla restore edildiği anlamına gelmez.

[Commit/run kimliği ve güvenli artifact özeti](reports/stage7h2-recovery-ci.json) yalnız sayı/hash/durum projeksiyonudur. SHA ve UUID hash'leri bu belirli sentetik çalışmaya aittir; başka bir CI run'ında yeni fixture UUID'leri nedeniyle değişebilir.

İlk iki CI denemesi tool exit=0 sonrasında snapshot uyuşmazlığı nedeniyle fail-closed olarak başarısız oldu; başarılı kurtarma diye sunulmadı. Güvenli tanı yalnız `archive_jobs`/`job_runs` CHECK kısıtlarında PostgreSQL'in eşdeğer `varchar[]::text[]` ve eleman başına `::text` yazım farkını gösterdi; tüm tablo ve kolon hash'leri eşleşiyordu. Yalnız bu iki bilinen kısıtın kesin SQL biçimleri için izinli status kümesi normalize edilir; bilinmeyen predicate, status kümesi, validation bayrağı veya başka constraint farkları korunur ve reddedilir. Restore edilmiş DB üzerinde gerçek 23514 CHECK ihlali de sınanır.

```bash
# Yalnız belirtilen test servisi / production secrets içermeyen checkout:
cd backend
python -m pytest -q tests/test_stage7h_postgres.py tests/test_stage7h_recovery.py
```

GitHub workflow'u gerekli `TEST_POSTGRES_URL`, `STAGE7H_RECOVERY_ENABLED`, `STAGE7H_POSTGRES_CONTAINER` ve false delete gate'leri yalnız PR test adımına verir. Konteyner olmadan bu opt-in'i production URL ile kullanmayın; test zaten reddeder.

## Kalan riskler

- Sentetik küçük fixture, production boyutunda restore süresi/kapasitesi veya gerçek kullanıcı verisinin kurtarılabildiğini kanıtlamaz. Production bağlantısı ve export bu görevde NOT_RUN.
- `pg_dump` mantıksal tek DB yedeğidir; WAL/PITR, cluster rolleri/tablespace'ler veya sürekli RPO/RTO kanıtı değildir. Kaynak kod ZIP'i SQL backup değildir.
- R2 nesneleri SQL dump'a girmez. Bu test, SQL ledger/source identity geri geldikten sonra **mevcut sentetik nesnelerin** eksiksiz okunabildiğini gösterir; gerçek R2 disaster recovery veya bağımsız object backup doğrulaması değildir.
- Test şeması mevcut SQLAlchemy modellerinden kurulur. Production migration geçmişi/extension/rol farkları için ayrı, açıkça yetkilendirilmiş değerlendirme gerekir.
- Readiness raporu `SAFE_TO_DELETE_NO` kalır. Bu kurtarma testi canlı DELETE veya import izni sağlamaz; gerçek schedule/v2 audit sürekliliği ayrı kapıdır.

**Production SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN. AUTO_RETENTION_DELETE_ENABLED=false ve OPERATIONAL_DELETE_ENABLED=false. Render ayarları değişmedi.**
