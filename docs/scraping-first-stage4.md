# 4. aşama — 48 saat PostgreSQL, kalıcı R2

## Teslimat ve gerçek canlı durum

PostgreSQL artık operasyonel önbellek, R2 kalıcı geçmiş deposudur. Varsayılan politika **48 saat**, yapılandırılabilir üst sınır da 48 saattir. Yaş sınırı tek başına silme izni değildir.

- [Salt okunur gerçek komut çıktısı](reports/lifecycle-48h-live-readonly.json): `NOT_CONFIGURED`, `LIVE_DELETE=NOT_RUN`, gerçekten silinen kayıt **0**.
- Eksik değişkenler: `DATABASE_URL`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Değerleri veya bucket adı yayımlanmadı.
- PostgreSQL boyutu, canlı silinebilir/korunacak satır sayıları ve Bayern–Köln SQL/R2 zinciri **NOT_RUN**. Bu sayılar sıfırmış gibi gösterilmez. Bağlantı kurulamadığı için uzaktaki kaydın varlığı veya yokluğu hakkında sonuç çıkarılamaz.
- [Otomatik test çıktısı](reports/lifecycle-48h-tests.json): **291 test geçti**, 12 mevcut deprecation uyarısı. Yeni lifecycle testleri SQLite bellek veritabanı ve bellek R2 adaptörü kullanır; gerçek Parquet/SHA-256 kodunu çalıştırır. Testlerdeki silmeler canlı silme değildir.
- Yeni `0010_active_statistics` migration dosyası yalnız hazırlandı; **canlı migration çalıştırılmadı**. Eski migration dosyaları, Render ayarları, Start Command ve frontend değiştirilmedi.

Önceki aşamanın dry-run raporu canlı PostgreSQL/R2 başarı kanıtı sayılmadı. Bayern–Köln için mevcut SQL kayıtları, kaynak kimlikleri, journal ve R2 nesneleri doğrudan kontrol edilir; bağlantı olmadan başarılı denmez.

## Komutlar

Mevcut backend bağımlılıkları ve proje kökündeki `.env` kullanılır:

```powershell
cd 'C:\Users\PC\Desktop\Maçanaliz\backend'
python -m app.jobs.retention --dry-run --target
python -m app.jobs.retention --dry-run
```

`--target` yalnız OpenLigaDB `83237` / Goaloo `3018869`, Bayern München–1. FC Köln kaydını inceler. Genel komut mevcut SQL verisini sınırlar içinde inceler; scraping veya geçmiş sezon taraması başlatmaz.

Dry-run PostgreSQL transaction'ını `READ ONLY` yapar. R2 için yalnız HEAD/GET kullanır; nesne, SQL kayıtları, iş günlükleri veya cursor yazmaz. Varsayılan moddur. Eksik yapılandırma/şema/hata durumunda çıkış 1, başarılı plan için 0 döner.

Yeni şema ve gerekli bağlantılar ayrı bakım sürecinde hazır olduğunda erken arşivleme komutu:

```powershell
python -m app.jobs.retention --archive --target
```

`--archive` açıkça R2 ve SQL arşiv metadata'sı yazar; hiçbir operasyonel SQL satırını silmez. **Bu aşamada canlıda çalıştırılmadı.** Aynı dosyayı yeniden yazmak yerine eksik nesneyi koşullu oluşturur veya mevcut doğru nesneyi yeniden doğrular.

Gerçek temizlik için gelecekte ayrı operatör onayı gerekir. İki koşul birlikte sağlanmalıdır:

1. `OPERATIONAL_DELETE_ENABLED=true` açıkça ayarlanmış olmalı. Varsayılan `false`.
2. Aynı kapsam ve batch seçenekleriyle alınan dry-run'ın `approval_digest` değeri `--execute --approve-plan <digest>` ile verilmelidir.

Bu aşamada `--execute` **çalıştırılmadı**. Onaylı mod bile yeni bir kilitli plan oluşturur, SQL snapshot'larını ve R2 içeriklerini yeniden doğrular. Plan değişmişse eski onay geçersizdir. Onay, başarısız arşivi veya ilişki engelini aşmaz. Uygun kayıt yoksa gerçek DELETE yapılmış gibi raporlamaz. Belirsiz commit sonucunda `deleted=null / UNKNOWN_RECONCILE_REQUIRED` gösterilir.

## Politika saatleri ve rapor

Süreler takvim günü veya eski `expires_at` değerine göre değil, UTC saat farkıyla değerlendirilir:

- Maç: başlangıç zamanı; tamamlanmamış maç ayrıca korunur.
- Sonuç: gerçek `verified_at`; doğrulanmış FINAL ve tam skor gereklidir.
- Oran: gerçek `captured_at`.
- Teknik gözlem: gerçek `observed_at`; final doğrulamasından sonraki izinli full-time gözlem olmalıdır.
- Tahmin ve kupon: sonuçlanma zamanı; sonuçlanmamış kayıt korunur.
- Analiz: analiz zamanı; devam eden analiz korunur.

Tam 48 saat sınırı test edilmiştir. Eski `ACTIVE_STATE_RETENTION_DAYS=14` / `ODDS_RETENTION_DAYS=30` değerleri yeni saatlik politikayı uzatmaz. Günlük match ingestion 48 saatten eski fikstürleri yeniden operasyonel tabloya getirmez. Eski satırlara toplu expiry UPDATE yapılmaz; mevcut satırlar yeni kuralla incelenir.

Dry-run her dataset için `scanned`, `eligible`, `retained`, `overdue_retained`, `deleted` verir. `pending_approval` onay bekleyenleri, `retained_for_safety` engel nedeniyle korunanları ayırır. Her kayıtta yaş, arşiv anahtarı, snapshot hash'i ve engel kodları vardır. Paylaşılan kuponlar sayımda iki kez sayılmaz. Canlı tarama yapılamadıysa bu sayıların yerine NOT_RUN durumu kalır.

`WITHIN_RETENTION_WINDOW`, `ARCHIVE_LEDGER_NOT_VERIFIED`, `ARCHIVE_READBACK_FAILED`, `SOURCE_IDENTITY_INDEX_MISSING/CONFLICT`, `REUSE_PERMISSION_NOT_CONFIRMED`, `FINAL_RESULT_MISSING_OR_UNVERIFIED`, `PREDICTION_UNSETTLED`, `RESULT_EVALUATION_DEPENDENCY`, `EXTERNAL_COUPON_DEPENDENCY`, `DEPENDENCY_SCAN_LIMIT` gibi nedenler açıkça görünür. 48 saati aşan ancak arşiv/ilişki engeli bulunan kayıtlar zorla silinmez.

## Erken, değişmez ve kayıpsız arşiv

Mevcut `R2ObjectStore`, `archive_partition`, `SQLArchiveTracker` ve Zstandard Parquet altyapısı kullanılır:

```text
<dataset>/season=<sezon>/league=<lig>/date=<maçın-UTC-günü>/part=lifecycle-<kimlik-hash>-<dosya-SHA256>.parquet
```

Dataset'ler: `matches`, `results`, `odds`, `statistics`, `predictions`, `analyses`, `coupons`, `coupon_items`. Her parça bir SQL satırının değişmez snapshot'ıdır. Bir sezondaki dosyalar birlikte okunur; sezon dosyası indirip üzerine yazılmaz. Önceden var olan günlük dosyalar ve 3. aşamanın `part=verified-openligadb-83237.parquet` dosyaları korunur. Uygulama R2 silme veya TTL işlemi yapmaz.

Tüm SQL kolonları `sql_snapshot_json` içinde UUID, timezone ve Decimal değerleri kayıpsız korunarak saklanır. Tarihsel analiz için mevcut düzleştirilmiş alanlar da bulunur. Kaynak/native ID, operasyonel alias, gözlem/doğrulama zamanları, lisans ve atıf korunur. OpenLigaDB verisi ODbL atfını taşır; Goaloo doğrulama kimliği lisanslı birincil kaynağın yerine geçirilmez.

HEAD/GET üzerinden boyut, SHA-256, Parquet metadata kayıt sayısı, decode edilmiş kayıt sayısı, nesne metadata'sı/anahtarı ve **tüm alanların güncel SQL snapshot'ına eşitliği** doğrulanır. SQL `archive_jobs` makbuzu da eşleşmelidir. Başarısız arşiv VERIFIED sayılmaz. Kaybolan upload yanıtı veya yarıda kalan metadata commit'i aynı değişmez anahtarla tekrar doğrulanabilir; çelişkili nesne üzerine yazılmaz.

Kaynak kimlikleri için `system_settings` içinde kaynak fikstürü başına tek, küçük `source-identity:<hash>` kaydı tutulur. Operasyonel UUID'nin başka kayda sessizce atanması engellenir. Onaylı temizlikten sonra kimlik bağlantısı ve kaldırma makbuzu kalır.

## İlişkiler, silme ve metadata koruması

Bir maç, bağımlılıklarıyla birlikte değerlendirilir. Match/result/odds/teknik gözlem/prediction/analysis/coupon-item/coupon snapshot'larının tamamı uygun yaşta, izinli ve doğrulanmış olmadan ilgili bundle silinemez. Sonuçlanmamış tahmin, devam eden analiz veya farklı maça bağlı kupon korunur. Kapsam dışında kalan kupon ilişkileri otomatik çözülmez; raporda engel olarak kalır. Bilinmeyen gelen foreign key, gerçek temizliği engeller.

Onaylı yürütmede parent ve child SQL satırları kilitlenir. R2 tekrar okunur; çocuk kayıtlar açıkça önce silinir. FK `CASCADE` / `SET NULL` mekanizması doğrulanmamış veri silmek için kullanılmaz. PK bazında gerçek satır yokluğu transaction içinde kontrol edilir; hata varsa rollback olur. Silme ve `retention_cleanup:<digest>` iş günlüğü/kimlik makbuzu aynı SQL commit'ini paylaşır. R2 nesneleri ve arşiv makbuzları silinmez.

Kullanıcılar, sistem ayarları, kaynak kimlikleri, bookmaker kimlikleri, iş günlükleri, arşiv metadata'sı, historical ingest kayıtları, günlük metrikler, lessons ve bildirim metadata'sı bu temizlikte silinmez. Eski otomatik cleanup ve SQL job-history prune yolları kapatıldı; sadece VERIFIED partition bulunması artık silme yetkisi değildir. Bellek test ledger'ının davranışı canlı SQL politikası sayılmaz.

Metadata için tablo kayıt sayıları ve boyutları raporlanır; 100.000 satır üzerindeki korunan tablolara büyüme uyarısı verilir. Metadata sınırsızmış gibi değerlendirilmez; büyümesi ayrı inceleme gerektirir. Bu aşama kritik metadata'yı otomatik budamaz.

## Zamanlama ve kapasite

Mevcut worker'a 15 dakikada bir erken arşivleme, saatte bir temizlik **dry-run** eklendi. Günlük arşiv görevi artık SQL'deki veriyi arşivler; otomatik geçmiş sezon scraping'i başlatmaz. Worker temizlikte, ortamda delete bayrağı açık olsa bile `execute` çağırmaz. Canlı otomatik silme bu teslimatta bilerek etkinleştirilmedi; her gerçek batch ayrı plan onayı gerektirir.

Mevcut SQL iş ledger'ı slotları ve hataların tekrar denenmesini tutar. Bağlantıya ait PostgreSQL advisory lock arşiv, onaylı temizlik ve 3. aşama importunu birbirinden ayırır; tracker ara commit'lerinde bırakılmaz. Kilit alınamazsa ikinci yazar ilerlemez. Satır kilitleri temizleme sırasında kaynak güncellemelerine ve yeni ilişkilere karşı ek korumadır.

Varsayılan batch 25 maç, ilişki limiti dataset/maç başına 500'dür. Arşivleyici tek kalıcı cursor ile batch'ler arasında döner; engelli maç daha sonraki turda tekrar değerlendirilir. Plan `truncated` ve devam için `next_after_match_id` verir. `--after-match-id` ile sonraki batch incelenebilir. İlişki limiti aşılırsa kısmi tarama tam arşivmiş gibi kabul edilmez; limit incelemesi gerekir. Rapor yalnız taranan batch'in kesin sayılarını verir.

`pg_database_size(current_database())` ve en büyük tablo boyutları salt okunur ölçülür. **1 GB = 1.000.000.000 byte** ihtiyatlı sınırı kullanılır: %70 WARNING, %80 HIGH, %90 CRITICAL. Mevcut monitor API'sine kapasite bilgisi eklenmiştir. Uyarı arşivlenmemiş kayıtları zorla silmez; bu bilgisayarda gerçek boyut ölçümü NOT_RUN'dır.

## Teknik veri ve şema

Yeni `active_statistics` tablosu yalnız izinli teknik gözlemler için hazırlanmıştır. `0010_active_statistics` mevcut tabloları değiştirmez; sadece bu tablo, unique constraint ve zaman/FK indekslerini ekler. Otomatik destructive downgrade içermez. **Migration çalıştırılmadı.** Yeni lifecycle işleri beklenen revision uygulanmamışsa `SCHEMA_NOT_READY` döner; migration'ı kendileri çalıştırmaz. Mevcut SQL/R2 verisi korunur.

Teknik toplamada kaynak isteğinden önce açık `source-reuse-policy:<provider>` onayı aranır. Policy provider, dataset, lisans, atıf ve HTTPS izin referansı içermelidir. OpenLigaDB sonucu lisanslı diye oran veya teknik veri sunduğu varsayılmaz. Kaynak/native ID, bilinen metrik ve birim, full-time dönem, gerçek gözlem zamanı ve sonlu/geçerli değerler gerekir. Eksikler sıfırla doldurulmaz; aynı gözlem sonradan değiştirilmez. İzin yoksa kaynak endpoint'i çağrılmaz. Eski 29 Goaloo teknik gözlemi bu tabloya taşınmadı.

## SQL sonrasında analiz ve Bayern–Köln kontrolü

`HistoricalRepository` R2 dosyalarını hash/kayıt sayısı doğrulamasıyla okur. Doğrulama metadata'sı olmayan eski dosya analiz kanıtı olarak sessizce kabul edilmez; dosya değiştirilmeden hata bildirilir. Değişmez lifecycle sürümleri kaydın kimliği ve **analiz anında kullanılabilir son sürümü** ile birleştirilir; aynı maç tekrar sayılmaz ve sonradan gelen düzeltme geçmiş analize sızmaz.

Analiz motoru canonical lig dizinini ve eski native lig dizinini okuyabilir. Yalnız incelenmiş Bayern–Köln alias'ı, kalıcı kanıtla operasyonel Goaloo kimliği/isimlerine projekte edilir; genel fuzzy takım eşleştirmesi yapılmaz. ODbL atfı kaybolmaz.

`--target` canlı SQL final skoru, verification zamanı, alias/journal ve 3. aşamanın R2 makbuzlarını karşılaştırır. İleride onaylı cache temizliğinden sonra SQL'de maç kalmaması otomatik olarak veri kaybı sayılmaz: R2, kaynak kimlik indeksi ve atomik silme günlüğü doğrulanırsa `ARCHIVED_ONLY` döner. Açıklanamayan SQL yokluğu başarılı temizlik sayılmaz.

Bayern–Köln zinciri, R2'den analiz, FK kontrollü silme ve SQL sonrasında R2 okuma **yerel adaptörlerde** test edildi. Gerçek uzak depolara erişilemediği için canlı uçtan uca sonuç `NOT_RUN` olarak kalır.

## Test komutu

```powershell
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Geniş gerçek servis testleri canlı yazma/çoklu kaynak kapsamını korumak için çalıştırılmadı. Önceden untracked bulunan backup testi bu değişikliğe ait olmadığı için kapsam dışında tutuldu. Yeni testler 48 saat sınırını, tam içerik doğrulamasını, kısmi upload/commit hatasını, yeniden çalıştırmayı, bozuk arşivi, değişen planı, kritik metadata korumasını, yetersiz izinleri, dependency/coupon engellerini, pre-delete yeniden doğrulamayı, yalnız onaylı yolu ve R2 üzerinden geçmiş okumayı kapsar. Sportmonks veya ücretli API kullanılmadı.
