# Stage 7C — kontrollü SQL/R2 import hazırlığı

Başlangıç commit'i **01c487a**. Stage 7A/7B raporları, Stage 3 verified import/journal, Stage 5 lifecycle/retention ve Stage 6 Actions bakım mimarisi incelendi. Bu teslimat salt okunur hazırlık ve offline yürütme doğrulamasıdır; production import açılışı değildir.

**SQL_WRITE=NOT_RUN — R2_WRITE=NOT_RUN — LIVE_DELETE=NOT_RUN.** Collector/retention workflow'ları, Secret'lar, Render ayarları ve Start Command değiştirilmedi. Otomatik import zamanlaması eklenmedi. Yeni CLI'nin ortam değişkenleriyle açılabilen yazma modu yoktur; AUTO_RETENTION_DELETE_ENABLED=false kalır.

## Importa hazır olan ve olmayan ligler

- **Bundesliga (`de.bl1`)**: kontrollü hazırlık destekleniyor, gerçek production adayı **yok**. OpenLigaDB `bl1`, lig kimliği `4937`, sezon `2026-2027` denetlenir. Tam native maç/takım kimliği, açık UTC, seçilen UTC gün/sezon uyumu, taze kaynak yanıtı ve bağımsız fikstür teyidi gerekir. Mevcut OpenFootball gözlemlerinde açık UTC bulunmadığından gerçek adaylar REVIEW_REQUIRED kalır.
- **Premier League (`en.pl`)**: production import kapalı. `pl/5996` ile `pl1/5993` güncellik/kimlik farkı çözülmedi; birincil sağlayıcı seçilmedi. Belirsiz saatler ve takım adları otomatik eşleştirilmez.
- **Süper Lig (`tr.sl`)**: NOT_AVAILABLE. Yeni kaynak etkinleştirilmedi; başka lig verisi kullanılmadı.

Yeni akışın offline pozitif senaryosu yalnız **gelecekteki SCHEDULED fikstür** içindir. Kaynağın NOT_FINISHED beyanı başlangıçtan sonra kesin bir LIVE/SCHEDULED bilgisi sayılmaz. FINAL gözlemleri ayrı etiketlenir; yeni maçlar için bağımsız final adapter'ı onaylanmadığından generic FINAL importu desteklenmez. Hiçbir yeni MatchResult üretilmez. Eski Stage 3'ün dar, önceden incelenmiş Bayern–Köln final yolu korunur.

## Güvenilir kaynak ve kimlik kanıtları

Girdi, [Stage 7B'nin gerçek Actions raporu](reports/stage7b-live-collector.json) olabilir; ancak **JSON raporu yazma yetkisi değildir**. Dry-run kaydedilmiş gözlemin alan tutarlılığını yeniden değerlendirir. Rapor içindeki yanıt hash'i ile normalleştirilmiş alanların aynı ham gövdeden üretildiğini dosya tek başına kanıtlayamaz. Production için trusted downloader ile yeni ham yanıt alımı, hash hesaplama ve normalizer üzerinden yeniden doğrulama Stage 7D koşuludur.

Kaydedilmiş örneklerde Bundesliga native ID'leri **83241** (Union Berlin–RB Leipzig) ve **83238** (Leverkusen–Paderborn), UTC kickoff `2026-11-21T14:30:00Z`; İngiltere'de **86635** ve **86628** bulunur. Bunlar 7B gözlem anının fikstür bilgileridir, güncel sonuç beyanı değildir. Güncel veri teyidi gibi yeniden etiketlenmediler.

Her adayda native provider ID, lig/sezon, sıralı takım ID/adları, açık UTC kickoff, kaynak güncellemesinin ham/UTC hali, gözlem zamanı, yanıt SHA-256, atıf/lisans, collector config hash'i ve teyit kaynakları sürümlenir. Eksik timezone tahmin edilmez. Kaynak URL'si yalnız incelenmiş public adreslerden olabilir; userinfo/query/credential içeren veya bilinmeyen URL'ler REJECTED özetine dönüşür, girdinin hassas metni rapora taşınmaz.

İzin kontrolleri mevcut `collector_policies.json` inceleme parmak izlerini ve sürelerini kullanır. İlgili belgelerin güncel 200 yanıtları/hash bağları, doğrudan 200 veri yanıtı ve en fazla **10 dakika** gözlem yaşı gerekir. Eski gövdenin checked_at alanını yenilemek veya 304 cache teyidi bu import tazeliği şartını sağlamaz. Bu, cache kullanan collector'ı değiştirmez; kontrollü import daha sıkı bir yeniden doğrulama ister. Yeni hukuki izin veya ticari kaynak araştırması yapıldığı iddia edilmez.

Tam sıralı isim/UTC uyuşması yalnız teyit adayıdır; fuzzy merge yoktur. UTC, skor veya native kimlik çelişkisi CONFLICT olur. Aynı kaynak ID'si farklı kimlikle dönerse önceki baseline korunur; yeni onayla sessizce değiştirilmez. OpenFootball fingerprint'i native ID veya kalıcı kanonik maç kimliği yapılmaz. Fikstür, kaynakça bildirilmiş skor ve doğrulanmış sonuç birbirinden ayrıdır; verified_final_score her gerçek aday için null kalır. Oran, teknik istatistik, korner, kart, şut ve tahmin yüzdesi oluşturulmaz.

## Kalıcı inceleme modeli

`app/storage/import_review.py`, mevcut ve retention tarafından korunan **system_settings JSONB** tablosunu kullanır:

- `review-source:<hash>`: native kimlik baseline'ı, evidence sürümleri ve son inceleme başlığı.
- `review-evidence:<hash>`: allowlist ile temizlenmiş gözlem/provenance, collector rapor hash'i, alan kontrolleri ve karantina nedenleri.
- `review-audit:<hash>`: REVIEW_REQUIRED, CONFLICT, REJECTED veya APPROVED_FIXTURE kararı; reviewer, gerekçe, kaynak/evidence sürümü, karar zamanı ve önceki karar bağlantısı.
- `controlled-import:<hash>`: SQL_COMMITTED / ARCHIVE_VERIFIED journal'ı; değişmez SQL snapshot, UUID, review/evidence bağlantısı ve R2 makbuzları.
- `import-audit:<hash>`: SQL commit ve arşiv doğrulama aşamalarının ayrı, tekrar çalıştırılabilir audit kayıtları.
- Mevcut `source-identity:<hash>`: operational UUID/native ID rezervasyonu; inceleme bağlantısı ve ilk doğrulanmış Parquet anahtarı.

Kayıtlar collector'ın geçici cache'ine dayanmaz. Dosya SQLite ile session kapatılıp açılarak kalıcılık testi yapıldı. **Canlı PostgreSQL'de bu kayıtlar oluşturulmadı.** Bu aşamada tüm mutasyon girişleri PostgreSQL bağlantısını reddeder; yazma testleri SQLite ve bellek object store ile sınırlıdır.

İnceleme audit'i append-only hash zinciri ve compare-and-set başlık kontrolü kullanır. Önceki karar silinmez; REJECTED kararı önceki onayın yeniden kullanılmasını engeller. Hash, imza veya operatör kimlik doğrulaması değildir. `reviewer` audit alanıdır; production operatör yetkisi Stage 7D'de ayrıca doğrulanmalıdır. Teknik engeller, eksik izin veya çelişkiler insanın yalnızca APPROVED yazmasıyla aşılamaz.

İnceleme evidence sürümü ile kaynak içerik sürümü ayrıdır. Aynı içerik taze olarak tekrar okunursa gözlem audit'i yenilenebilir; kimlik, yanıt hash'i, kaynak durumu veya kaynak/config sürümü değişirse eski onay kullanılamaz. Onay en fazla 10 dakikalıktır; importer yeni planı yeniden üretir ve kalıcı, güncel review başlığına bağlar. Eski bir hash/ready boolean yeterli değildir.

Metadata büyümesi için kaynak başına 32 evidence ve 64 karar; toplam 1000 evidence ve 2000 review sınırı vardır. Sınıra gelince yeni kayıt **durur**, eskiler silinmez. Bu sınırlar PostgreSQL boyut ölçümü yerine geçmez. Stage 7D, ayrıntılı inceleme geçmişini değişmez R2 metadata arşivine taşıma ve kritik UUID/hash başlıklarını SQL'de koruma politikasını ayrıca değerlendirmelidir. Mevcut 1 GB kapasite uyarı mekanizması korunur.

## Dry-run sonuçları ve komutlar

Backend klasöründe varsayılan mod salt okunurdur; SQL/R2 bağlantısı ve uygulama settings yüklemesi bile yapmaz:

```powershell
python -m app.jobs.controlled_import --report ../docs/reports/stage7b-live-collector.json
# Açık yazımı:
python -m app.jobs.controlled_import --dry-run --report ../docs/reports/stage7b-live-collector.json
```

[Kaydedilmiş gerçek kaynaklarla dry-run](reports/stage7c-dry-run.json): **DRY_RUN_COMPLETE, 8 aday, 0 teknik olarak uygun aday, 0 production-ready aday**. Bunlar yeni canlı kaynak gözlemleri değildir. Kaynak yanıtları ve belge kontrolleri artık 10 dakikalık pencerenin dışındadır; bu nedenle FRESH_HTTP_200 ve LICENCE_AND_TERMS_CURRENT engelleri vardır. İkinci kod lisansın değiştiği iddiası değil, güncel belge teyidinin eksikliğidir. Bundesliga'da ayrıca bağımsız UTC teyidi; İngiltere'de pl/pl1 seçimi ve kimlik/saat sorunları sürer. Süper Lig NOT_AVAILABLE.

Bu komut **bilerek BLOCKED / çıkış 1** döner; rapor dosyasını okumadan önce durur:

```powershell
python -m app.jobs.controlled_import --persist
```

[Persist kapısı çıktısı](reports/stage7c-persist-blocked.json): STAGE7C_PRODUCTION_PERSIST_DISABLED. Secret, ortam bayrağı veya collector raporundaki production_ready alanı bunu açamaz. SQL_WRITE, R2_WRITE ve LIVE_DELETE değerleri NOT_RUN'dır. JSON okunamıyorsa sabit güvenli hata kodu kullanılır; ham traceback yayımlanmaz.

## PostgreSQL/R2 güvenlik testleri

Offline importer'ın pozitif akışı:

1. Yalnız SQLite ve InMemoryObjectStore kabul edilir; gerçek bağlantı/nesne istemcisi callback'ten önce reddedilir. İkinci offline importer aynı test kilidini alamaz.
2. Kaynak planı yeniden hesaplanır. Kalıcı evidence, geçerli onay, source version ve review head kontrol edilir. Tam bir tek aday seçilir.
3. Mevcut native ID, source identity rezervasyonu ve yakın zamandaki eşleşen/çözümlenmemiş çapraz sağlayıcı kayıtları kontrol edilir. Bilinmeyen alias otomatik bağlanmaz. SQL satırı retention sonrası yoksa eski UUID rezervasyonu ikinci maç yaratmak için kullanılmaz; arşiv/cleanup incelemesi istenir.
4. Yeni SCHEDULED maç, kickoff + 48 saat expires_at, native identity rezervasyonu, SQL journal/snapshot ve SQL_COMMITTED audit'i aynı SQL commit'inde yazılır. Kullanıcı/ayar/önceki veri değiştirilmez.
5. **Stage 5 make_record/verify_record** ile aynı lifecycle-v1 Parquet kaydı ve anahtarı üretilir. **Stage 3'ün kullandığı archive_partition + SQLArchiveTracker** yolundan put-if-absent, SHA-256, boyut, satır sayısı, tüm içerik ve SQL snapshot karşılaştırması yapılır. season/league/UTC date partition düzeni korunur.
6. SQL ve R2 yeniden okunur; ARCHIVE_VERIFIED journal/audit'i commit edilir. Arşivin VERIFIED olması maç sonucunun bağımsız doğrulandığı anlamına gelmez.

[Sentetik offline import/readback örneği](reports/stage7c-offline-import.json): tek maç, **sıfır sonuç kaydı**, tek Parquet parçası, ikinci çalışmada created=false ve aynı UUID/nesne. Gerçek SHA-256 ve satır sayısı yalnız bu **bellek R2 / SQLite deneyi** içindir; canlı R2/PG kanıtı değildir.

SQL başarılı/R2 başarısız veya upload acknowledgement kayıp ise SQL journal/snapshot kalır. R2 başarılı/son SQL acknowledgement kayıp ise yeniden okuma ile durum uzlaştırılır; başarı veya tam rollback varsayılmaz. Başarısızlık çıktısı UNKNOWN_RECONCILE_REQUIRED / UNKNOWN_REVERIFY_REQUIRED kullanabilir. Recovery yeni kaynak olmadan **önceden commit edilmiş ve onaylı snapshot'ı** arşivleyebilir; yeni operasyonel satır oluşturmaz. Journal olmadan yalnız R2 nesnesinden SQL geri yaratılmaz. Bozuk/değişmiş nesne üzerine yazılmaz ve silinmez.

Mevcut Stage 3 PostgreSQL advisory-lock context'i aynı global lifecycle/import kilitlerini koruyarak farklı **event_identity** kabul edecek şekilde hazırlanmıştır. Varsayılan Bayern davranışı değişmez. `openligadb/bl1` ile `openligadb/de.bl1` aynı mevcut fixture key'ine normalize edilir; yeni takım alias'ı/fuzzy kural eklenmez. Mock PostgreSQL testleri global kilit sırasını, farklı event kapsamını, üç seviyede kilit meşguliyetini ve ters sırada bırakmayı denetler. Offline test kilidi, gerçek PostgreSQL concurrency doğrulaması gibi sunulmaz. Stage 7D üretim adaptörü aynı context'e ve mevcut fixture_key'e bağlanmadan açılmamalıdır.

SQL/R2 güvenlik kapsamı ayrıca izin/tazelik engelleri, reddedilmiş/geri alınmış onay, değiştirilmiş ready flag, kimlik çakışması, yinelenen import, ilk SQL commit acknowledgement belirsizliği, iki yönlü partial recovery, bozuk Parquet'i koruma, eksik SQL'den diriltmeme, metadata kapasite sınırı ve Bayern–Köln UUID/alias/journal/arşiv korumasını kapsar.

```powershell
python -m pytest -q tests/test_controlled_import.py tests/test_verified_import.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Odaklı paket: **76 test geçti**. Tüm güvenli paket: **458 test geçti, 14 mevcut deprecation uyarısı**; [makine raporu](reports/stage7c-tests.json). Live integration dosyaları çalıştırılmadı. Testler gerçek veri doğrulaması gibi gösterilmez.

## Yeni migration gereksinimi

**Migration gerekmiyor ve oluşturulmadı.** Mevcut ActiveMatch, SystemSetting ve ArchiveJob modelleri, native ID uniqueness ve kalıcı metadata kullanılıyor. Mevcut PostgreSQL şeması canlı okunmadı/değiştirilmedi. Yeni tablo, DROP/TRUNCATE, canlı migration, R2 overwrite/delete veya kullanıcı verisi temizliği yoktur.

Stage 5 retention tek silme sahibidir. 48 saat politikası, VERIFIED ledger ve içerik doğrulaması, kaynak identity, güncel plan/hash/snapshot, bağımlılık, tamamlanmamış maç/tahmin/analiz korumaları değiştirilmedi. Yeni SCHEDULED fikstür arşivlense bile terminal değildir; mevcut retention onu yalnız yaş nedeniyle silemez. Kritik review/source/audit kayıtları protected system_settings içinde kalır.

## Stage 7D canlı pilot için gerekli koşullar

1. Tek gerçek Bundesliga maçı seçilmeli; kaynak/kimlik, UTC programı ve izinler güncel gerçek GET/robots/rate-limit kontrolleriyle tekrar doğrulanmalı. Mevcut kayıtlar ve arşivlerdeki UUID/alias rezervasyonları salt okunur kontrol edilmeli. Çözümlenmemiş çapraz sağlayıcı kimlikleri otomatik import edilmemeli.
2. Yetkisi doğrulanmış operatörün gerekçeli, sürüme bağlı kararı ve gerçekten yeni trusted revalidation gereklidir. Kaydedilmiş collector dosyası ya da offline test çıktısı production yetkisi değildir. Karantina çözümü ve operatör yetkilendirmesi tamamlanmalıdır.
3. Fixture ile FINAL ayrı izinlere sahip olmalı. Yeni FINAL için bağımsız, güvenilir ve kullanım koşulları uygun teyit adapter'ı onaylanmalı; bu yokken sonuç importu kapalı kalmalıdır. Eski Bayern–Köln importu generic yola taşınmamalıdır.
4. SQLite-only laboratuvar kapısı ancak açıkça onaylanan Stage 7D uygulamasıyla gerçek PostgreSQL session + mevcut ortak advisory lock context'ine bağlanmalı. Kilitler archive tracker commit'leri boyunca aynı bağlantıda tutulmalı; source identity ve kalıcı journal retry girişte tekrar denetlenmelidir. Düşük ayrıcalıklı dry-run/preflight ile şema, izin, kapasite ve gerçek SQL/R2 partial recovery doğrulanmalıdır.
5. Açık operatör komutu ve tek aday/sürüm kapsamı dışında production persist olmamalı. Yeni Secret veya otomatik mod kullanıcı onayı olmadan eklenmemeli; import hiçbir workflow schedule'ına bağlanmamalıdır.
6. Metadata büyüme planı, tek maç rollback yerine güvenli reconcile prosedürü ve R2 readback makbuzu incelenmeli. Arşiv doğrulanmadan/ilişkiler tamamlanmadan operational veri silinmemeli; temizlik mevcut retention'a bırakılmalıdır. AUTO_RETENTION_DELETE_ENABLED=false korunmalıdır.
