# WEB SCRAPING FIRST — üçüncü aşama: kontrollü tek maç importu

## Gerçek çalışma sonucu

Başlangıç commit'i `7077e96`. Kapsam yalnız **20 Kasım 2026, Bundesliga `de.bl1`, FC Bayern München – 1. FC Köln, 1–1 FINAL**, başlangıç `2026-11-20T19:30:00Z` / 22:30 Türkiye saati. OpenLigaDB `83237` birincil, Goaloo `3018869` doğrulama kaynağıdır. İkinci aşamanın kaynak kararları ve dar kimlik incelemesi korunur.

- [Gerçek dry-run çıktısı](reports/verified-import-2026-11-20-dry-run.json): `DRY_RUN_PASS`, güncel kaynak zinciri `RESULT_VERIFIED`, SQL/R2 `NOT_RUN`, çıkış 0.
- [Gerçek kontrollü persist denemesi](reports/verified-import-2026-11-20-persist.json): kaynak zinciri yeniden `RESULT_VERIFIED`; ardından `BLOCKED / STORAGE_NOT_CONFIGURED`, SQL/R2 **`NOT_RUN`**, çıkış 1.
- Eksik değişken adları: `DATABASE_URL`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`. Gizli değer veya bucket adı rapora yazılmaz.
- **Canlı PostgreSQL insert/readback ve canlı R2 upload/readback yapılmadı.** Gerçek R2 dosyası, dosya SHA-256 değeri veya SQL kayıt UUID'si bu çalışma için varmış gibi sunulmaz. Test adaptörlerinin başarıları canlı servis doğrulaması değildir.
- Her komut aynı tek günün kaynaklarını tekrar kontrol etti. Yeni lig/gün araması, toplu tarama veya odds endpoint erişimi yapılmadı. Açık bitiş, başlangıç ve skor uyuştu; OpenFootball'un eksik fikstürü güçlü final teyidi sayılmadı.

## Komutlar

Mevcut backend bağımlılıklarını kullanır. Proje kökündeki `.env`, mevcut `get_settings()` üzerinden yüklenir; yeni credential dosyası veya ayrı storage istemcisi yoktur.

```powershell
cd 'C:\Users\PC\Desktop\Maçanaliz\backend'
python -m app.jobs.verified_import --dry-run
```

Dry-run varsayılandır; seçenek verilmeden de aynı davranır. Gerçek kaynakları okur, doğrulamayı yeniden hesaplar, lisanslı kayıt adayını ve iki arşiv anahtarını gösterir. **SQL/R2 bağlantısı bile açmaz.** Hedef depodaki mevcut kayıt/nesne çelişkilerini denetlediği anlamına gelmez.

Gerekli ortam değişkenleri mevcut yapılandırmaya girildiğinde kontrollü yazma:

```powershell
python -m app.jobs.verified_import --persist
```

Bu seçenek kaynakları yeniden okur; eski JSON raporunu import etmez. İki canlı komut arasında OpenLigaDB için en az 60 saniye bırakın. Komut içindeki robots, User-Agent, hız sınırı, HTTPS ve yönlendirme/403/429 sonrası durma kuralları mevcut `result_probe` ile aynıdır. Kaynak isteğinin zamanı ve SHA-256 kanıtı gerekir; gözlemler en fazla 10 dakikalık olabilir. SQL hazırlığı öncesinde tazelik tekrar denetlenir.

`--date`, lig, sezon, maç ID, tarih aralığı, `--force`, kaynak dosyası veya cleanup seçeneği yoktur. Bu teslimat yalnız incelenmiş maça izin verir. Skor/UTC saati/kaynak kimliği değişirse yeni değeri sessizce kabul etmez.

Çıkış 0 yalnız `DRY_RUN_PASS` veya iki depoda `VERIFIED` durumudur. Diğer sonuçlar 1; desteklenmeyen CLI seçenekleri 2 döner. Başarılı dry-run canlı persist başarısı değildir.

## SQL modeli ve kaynak kimliği

Migration gerekmedi. Mevcut tablolar kullanılır:

- `active_matches`: tek operasyonel UUID, provider/native ID, lig, sezon, takımlar, kickoff ve `FINISHED` durumu.
- `match_results`: aynı UUID'ye bağlı tek `FINAL`, 1–1 skor, `provider=openligadb`, `provider_result_id=83237`, `verified_at` ve kısa ODbL atfı içeren `result_source`.
- `system_settings`: `verified-import:de.bl1:2026-11-20:openligadb:83237` anahtarında tam kaynak/provenance, lisans, değişmez import snapshot'ı, operasyonel UUID/alias bağlantısı, ilerleme ve arşiv makbuzları.
- `archive_jobs`: mevcut SQL tracker ile her Parquet nesnesinin geçişleri ve doğrulama makbuzu.

Önceden kayıt yoksa `openligadb/83237`, canonical lig `de.bl1` ile oluşturulur. Önceden Goaloo kaydı varsa yalnız incelenmiş native ID, sıralı takım isimleri, sezon, lig eşlemesi ve **tam UTC saati** uyumunda mevcut operasyonel UUID/provider/ID/isimler korunur; ikinci maç açılmaz. İki operasyonel kopya, farklı kickoff, lig, takım veya önceden journal dışında kaydedilmiş sonuç varsa otomatik birleştirme/üzerine yazma yapılmaz.

İkinci aşamadaki alias incelemesi sadece bu tek fikstür için günlük `fixture_key` kontrolüne bağlandı. Günlük ingestion da aynı canonical advisory lock'u kullanır ve sonradan ikinci Goaloo kopyası açmaz. İsimlerde genel fuzzy eşleştirme yapılmaz. İki kaynak ID'si dışında bir native ID'ye bu istisna uygulanmaz.

Match, result ve retry snapshot'ı **aynı SQL transaction'ında** commit edilir. `finished_at=null` kalır: gerçek düdük zamanı kaynaklarda doğrulanmadığı için uydurulmaz. `verified_at`, kickoff veya varsayılan 90 dakika değil, gerçek kaynak gözlemlerinden gelir. İlk doğrulama snapshot'ı tekrar denemede değişmez; güncel canlı tekrar kontrolünün kanıtı komut çıktısında ayrıca yer alır.

## R2 Parquet düzeni ve doğrulama

Mevcut `R2ObjectStore`, `archive_partition`, `SQLArchiveTracker` ve Zstandard Parquet kodu kullanılır. Tek sezon dosyasını indirip üzerine yazmak yerine mevcut append-only parça düzeni korunur:

```text
matches/season=2026-2027/league=de.bl1/date=2026-11-20/part=verified-openligadb-83237.parquet
results/season=2026-2027/league=de.bl1/date=2026-11-20/part=verified-openligadb-83237.parquet
```

Her parça 1 kayıt içerir; bunlar **aynı maçın** fikstür ve sonuç dataset'leridir. Bir sezon/lig altında yıllarca birlikte okunabilir. Eski günlük dosya veya başka parça değiştirilmez. Mevcut nesne yalnız aynı byte'lar ve doğrulama metadata'sı ile yeniden kullanılabilir; farklı nesne üzerine yazılmaz. Yazma `If-None-Match: *` ile koşulludur.

Yükleme sonrası HEAD + GET ile dosya boyutu, SHA-256, Parquet metadata kayıt sayısı, decode edilmiş kayıt sayısı, nesne metadata'sındaki hash/sayı/anahtar ve **decode edilmiş tüm alanların SQL journal snapshot'ına eşitliği** kontrol edilir. UTC zamanları Parquet'te timezone-aware timestamp olarak saklanır. SQL de commit sonrasında tekrar okunur. Her iki nesne ve SQL readback tamamlanmadan genel `VERIFIED` çıkmaz.

Arşivdeki takım/sonuç değerleri lisanslı OpenLigaDB gözleminden gelir. `provider=openligadb`, native ID, ODbL lisansı, atıf ve URL; kaynakların gözlem zamanları ve yanıt SHA-256 değerleri korunur. Goaloo ham sezon dosyası veya teknik istatistiği yeniden dağıtılmaz; doğrulama kaynak kimliği ve kanıt parmak izi tutulur. Mevcut operasyonel Goaloo kimliği varsa ayrı `operational_provider` alanlarıyla bağlanır.

Mevcut `HistoricalRepository` ile okunabilirlik ve doğrulama zamanından önce analize sızmaması test edildi. Günlük genel arşivleyici journal ile yönetilen bu tek kaydı ayrıca arşivlemez: böylece native alias veya doğrulama günü yüzünden ikinci kopya oluşmaz ve atıf kaybolmaz. Eksik controlled import genel arşivleyici tarafından tamamlanmaz; tekrar `--persist` gerekir.

## Kısmi hata, tekrar deneme ve saklama

SQL/R2 arasında dağıtık transaction varmış gibi davranılmaz. PostgreSQL bağlantısına ait advisory lock, tracker'ın ara commit'leri boyunca tutulur; ikinci importer beklemeden engellenir. Normal günlük ingestion ile ortak fikstür lock'u da tutulur. SQL'nin mevcut unique constraint'leri ek korumadır.

SQL commit edilip R2 başarısız olursa SQL sonuç ve immutable snapshot korunur. Mevcut `archive_jobs` ilerlemesiyle aynı `--persist` tekrar çalıştırılır; önce **yine canlı doğrulama** gerekir. İlk makbuzu tamamlanan parça tekrar indirilip doğrulanır, eksik parça tamamlanır. Yükleme gerçekleşip yanıt kaybolduysa aynı nesne doğrulanarak yeniden kullanılır. İkinci maç, sonuç veya farklı zaman damgalı arşiv yaratılmaz. Son journal commit'i başarısız olsa da aynı yol kullanılabilir.

Bozuk/çelişen mevcut nesne otomatik onarılmaz, silinmez ve üzerine yazılmaz; hata raporlanır. SQL commit yanıtının kaybolması gibi belirsiz sonuçlar `UNKNOWN_RECONCILE_ON_RETRY` olarak bildirilir; iki depoda geri alma yapılmış gibi gösterilmez. Hata metinlerinde SQL, bağlantı URL'si, token veya credential yayımlanmaz.

Bu komut **hiçbir SQL/R2 silme ve otomatik cleanup çalıştırmaz**. Yeni operasyonel kayıt için mevcut `active_state_retention_days` (varsayılan 14 gün, en az 1 gün) import anından itibaren korunur; arşiv başarılı diye süre kısaltılmaz. R2 nesnelerine TTL verilmez. Genel cleanup'ın mevcut canlı nesne doğrulaması ve retention korumaları değiştirilmez. Arşiv tarihi/operasyonel alias nedeniyle eski cleanup ile eşleşmeyen kayıtlar tutulur; bu aşama onları zorla silmez veya tüm controlled kayıtlar için otomatik silme garantisi vermez.

Oran ve teknik istatistik tablosu/dataset'i yazılmaz; bu maç için izinli ve doğrulanmış yeni veri yoktur. İkinci aşamadaki 29 eski teknik gözlem kullanılamaz olarak kalır.

## Otomatik testler

```powershell
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

**264 test geçti, 12 mevcut deprecation uyarısı.** Bu aşamanın 31 testi SQLite SQL adaptörü ve bellek nesne deposunda çalışır; gerçek Parquet kodu/SHA-256/readback kullanılır. PostgreSQL advisory lock çağrılarının alınması/bırakılması offline test edilir; gerçek PostgreSQL eşzamanlılık/bağlantı testi `NOT_RUN`.

Kapsam: dry-run'da bağlantı açılmaması; saved/stale/future/eksik hash/yanlış scope/bitmemiş/çelişkili kanıt engeli; iki kez çalıştırma; mevcut Goaloo UUID'sini koruma; çift kayıt/önceki sonuç çelişkisi; geç Goaloo ingestion kopyasını engelleme; SQL commit öncesi ve commit acknowledgement hatası; kısmi R2 ve kayıp upload yanıtı; bozuk mevcut nesne; son journal commit hatası; snapshot değişikliği; tarihsel okuyucu ve cutoff; günlük arşivleyiciyle mükerrer önleme; lock busy ve unlock.

Geniş canlı entegrasyon testleri tek maç/gün ve storage yazma sınırını korumak için çalıştırılmadı. Önceden çalışma alanında bulunan, bu teslimatla ilgisiz untracked backup testi de kapsam dışındadır. Render ayarları, Start Command, mevcut migration dosyaları ve arayüz değiştirilmedi. Sportmonks veya ücretli API kullanılmadı.
