# Sezonluk futbol veri aktarımı

## Çalıştırma

Backend dizininden sezonluk kaynak önizlemesi için `python -m app.jobs.season_bulk --league uefa.cl --season 2024-2025` çalıştırılır. Parametresiz çağrı Avrupa kupalarının üç sezonunu kapsar. `--isolated` aynı kaynak üzerinden geçici SQLite ve bellek içi R2 simülasyonuyla aktarımı prova eder. Kullanılan girdiler public sezon dosyalarıdır. CLI production bağlantısı kurmaz.

Kodun PostgreSQL/R2 aktarım arayüzü `app.historical.season_transfer.transfer_source` fonksiyonudur. Çağıran işlem PostgreSQL transaction'ını yönetir. Yalnız katalogdaki kaynağı ve açık aktarım onayını kabul eder. Dağıtımda migration ayrı onay almalıdır. Sabit kaynak sürümünü, SQL makbuzundan adreslenen R2 orijinalini boyut ve SHA-256 ile doğrulayıp yeniden kullanır. Sezon dosyası, Parquet ve manifest içerik adreslidir. Başarısız bir SQL işleminden sonra R2 nesneleri yeniden doğrulanabilir. SQL işleminde maç, takım, oran ve makbuz kayıtları birlikte tamamlanır.

Yeni tablolar operasyonel saklama/silme sürecinin dışındadır. Maç kimliği kaynakta yerel kimlik varsa ona bağlanır. OpenFootball ana turnuvalarında lig, sezon, tur ve yönlü takım eşleşmesine bağlanır. Ev/deplasman adları kaynak bazında eşleştirilir. Kaynaklar arasında takım birleştirmesi ancak doğrulanmış eşleme ile yapılmalıdır. Mevcut maç, sonuç ve arşiv tabloları bu aktarımın hedefi değildir.

## Kaynakların kapsamı ve izinler

- **OpenFootball CC0** — 2024–2025 ana turnuvalarında Şampiyonlar Ligi 189 maç, Avrupa Ligi 189 maç, Konferans Ligi 153 maç. 2025–2026 Şampiyonlar Ligi 189 maç. Bu dört dosyada 720 normal süre sonucu ve sıfır oran var. UTC dönüşümünde Orta Avrupa saati ve yaz saati kullanılır. [OpenFootball kaynak ve lisansı](https://github.com/openfootball/champions-league/blob/abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef/LICENSE.md) · [UEFA Şampiyonlar Ligi saatleri](https://www.uefa.com/uefachampionsleague/news/0299-1dc300759948-6eb1b2f357ae-1000--when-does-the-champions-league-final-start-where-you-are-w/)
- **OpenLigaDB ODbL 1.0** — 2026–2027 lig aşaması sırasıyla 144/144/108 fikstür ve 90/89/72 kullanılabilir normal süre skoru sundu. Üç çelişkili alan boş bırakıldı. 2025–2026 Avrupa Ligi yanıtında 17 kısmi eleme fikstürü vardı ve güvenle ayrıştırılan 90 dakika skoru yoktu. Atıf ve yeniden dağıtılan türetilmiş veritabanı için aynı lisans koşulları geçerlidir. [Lisans](https://www.openligadb.de/lizenz) · [API koşulları](https://www.openligadb.de/nutzungsbedingungen)
- **Eksik kapsam** — 2025–2026 Konferans Ligi için doğrulanmış toplu lisans kaynağı yok. Diğer yayımlanmış 2025–2026 Avrupa ve Konferans Ligi dosyalarının ticari depolama ve yeniden kullanım izinleri teyit edilene kadar `BLOCKED_LICENSE_REVIEW`. Avrupa kupası ön eleme kaynakları bu aktarım kapsamına alınmadı. [Fixture Download Avrupa Ligi](https://fixturedownload.com/results/europa-league-2025)
- **Aşama B** — OpenFootball JSON adaptörleri Süper Lig, Bundesliga ve Belçika Pro League için hazır. Kaynaklarda bazı sezonlar kısmi, saat dilimli başlama zamanı eksik ve dönem belirtmeyen skor dizileri var. Bu alanlar boş kalır. [OpenFootball JSON lisansı](https://github.com/openfootball/football.json/blob/e6744429ee395bc86f247348c6184bb08d4eb361/LICENSE.md)
- **Oranlar** — Ölçülen kaynaklarda sıfır uygun oran var. Football-Data kullanım amacı sınırlı. Footiqo özel arşiv izni gerektiren bir aday. The Odds API ücretli ve zaman damgalı geçmiş oran sağlar. Koşulları dahili saklama ve türetilmiş analizlere izin veriyor ancak bağımsız ham veri dağıtımı sınırlandırılmış. Abonelik, kapsam ve maliyet onayı ile örnek dosya doğrulaması bekleniyor. [Football-Data](https://football-data.co.uk/data) · [Footiqo lisansı](https://footiqo.com/premium/historical-football-odds/) · [The Odds API koşulları](https://the-odds-api.com/terms-and-conditions.html) · [Geçmiş kapsam](https://the-odds-api.com/historical-odds-data/)

## Üretim yüklemesi için onaylar

Önce migration ve staging provası ile yazma kimliklerinin yeni tablo/prefix kapsamı ve nesne makbuzu kontrol edilir. Başlangıç yükü tek onaylı 2024–2025 Şampiyonlar Ligi sürümüyle sınırlandırılır. Kaynaktan bildirilen skor tek başına bağımsız doğrulama sayılmaz. Oranlar için sağlayıcı hakkı, maliyeti, UTC gözlem zamanı ve maç eşleştirmesi ayrıca doğrulanır. Düzenli güncelleme istenirse OpenLigaDB değişiklik kontrolü ve merkezi önbellek onaylı operasyon planına alınır.

Analiz en az 200 doğrulanmış, pre-match 1-X-2 gözlemi olan maç ister. En yakın tam oran gözlemi kullanılır. Beraberlik dahil ters oranlar normalize edilir. Önceki sezonlarda hesaplanan oran aralığı başarıları sonraki sezonda sınanır. Düşük sürpriz oranı kârlılığı garanti etmez. Aşama C sıralaması için veri yeterli değil.


## Migration ve ilk yükleme kontrol listesi (onay gerektirir)

- **Ortam ayrımı:** `--isolated` yalnızca geçici SQLite ve bellekte R2 simülasyonu kullanır; production/staging R2 aktarımı değildir. İzole PostgreSQL CI da production veritabanına bağlanmaz.
- **Gerçek production DB kimliği:** Render servisinin `DATABASE_URL` hedefi (şifreyi/logları ifşa etmeden) servis yapılandırmasında ayrı doğrulanmalıdır. Workspace'de görünen bir Postgres instance'ı otomatik olarak bu servisin bağlı olduğu instance sayılmaz.
- **Mevcut sürüm:** Belirlenen hedef DB salt okunur olarak Alembic `0010_active_statistics` ise yeni `0011_season_bulk` için migration gerekir. Mevcut `production_readiness.py` içindeki eski veritabanı kimliği reddetme davranışı sezonluk yükleyici tarafından aşılmamalıdır.
- **Staging provası:** Production kopyası olmayan yalıtılmış PostgreSQL'de önce `0010_active_statistics` şemasıyla başla, sonra `0011_season_bulk` migration'ını uygula. Yeni dört tabloyu doğrula, mevcut tabloların veri ve satır sayılarını önce/sonra karşılaştır. İzole R2 veya bellek deposuna 2024–2025 UCL aktarımını yap; aynı aktarımı iki kez çalıştırıp aynı SHA-256/manifest makbuzunu aldığını kontrol et.
- **Rollback:** `0011_season_bulk` otomatik downgrade'i veri kaybını önlemek için kasıtlı olarak engeller. Production öncesi bağımsız PostgreSQL yedeği, yedek geri yükleme provası, kaynak sürümü kaydı ve R2 manifest kontrolü gereklidir. Eski şemaya dönüş ancak ayrıca onaylanan manuel kurtarma planıyla yapılabilir.
- **İlk production yüklemesi:** Yalnız `uefa.cl`, `2024-2025`, açık onaylı ve sabitlenmiş CC0 kaynak revizyonu. Önce kaynak doğrulaması, ardından migration onayı, sonrasında ayrı ingest onayı. Birbirinden bağımsız bu üç aşama tamamlanmadan canlı yazma yapılmamalıdır.
- **Oran verisi:** Ölçülen %0 kapsama nedeniyle analiz sonuçlarında favori/sürpriz oranı üretilmez; sonraki kaynaklar için ayrıca hukuki ve mali inceleme gerekir.

## Production öncesi uygulama planı

Render servisi Ma-analiz-2 ve PostgreSQL instance cbcxbcxbcv aynı workspace içinde görülüyor. Servisin DATABASE_URL hedefi Render servis detayları aracından okunamadı. Servis ortamında veya yetkili Render yönetim ekranında URL'nin sadece sunucu ve veritabanı kimliğini PostgreSQL instance bilgileriyle karşılaştırın. Parola ve tam bağlantı dizesini kaydetmeyin. Kimlik eşleşmezse durun.

Salt okunur gözlemde cbcxbcxbcv public şemasında 18 tablo ve Alembic 0010_active_statistics bulundu. Migration öncesinde transaction tutarlı tam PostgreSQL yedeği için pg_dump custom format ve uygun sürüm istemcisi kullanın. Yedeği şifreli ve erişimi sınırlandırılmış depoya alın, SHA-256 ve pg_restore --list ile bütünlüğünü doğrulayın. Ayrı, boş ve kimliği teyit edilmiş PostgreSQL ortamına pg_restore ile geri yükleyin. Alembic sürümü, tablo ve satır sayıları, doğrulanmış sonuçlar ve verified_at değerleri ile arşiv makbuzlarının önce/sonra döküm özetlerini karşılaştırın. Gerçek yedek ve geri yükleme ortamı ayrı onay gerektirir.

R2 staging için production bucket ve erişim anahtarlarından ayrı bir bucket ve sadece bu bucket'a yetkili kimlik gerekir. Beklenen depolama bir kaynak dosyası, bir Parquet ve bir manifest ile küçüktür. İstek, depolama, minimum ücret ve yaşam döngüsü maliyetleri Cloudflare hesabının güncel fiyatlandırmasından işlem öncesi hesaplanmalıdır. Ayrı onay ardından Parquet yüklemesi, HEAD ve GET ile byte sayısı ve SHA-256, manifest içeriği, iki kez aktarımda aynı nesne ve tek SQL makbuzu ile kesinti sonrası yeniden deneme doğrulanır. Mevcut CI bellekteki R2 simülasyonunu kullanır, gerçek R2 yetkisini doğrulamaz.

İlk yük için kaynak revizyonu abfaeddc2ee3d14f99ecc163c9ddb46cb4d67cef, kaynak SHA-256 c2692b22af02c5df4587b69c5f430aebf7d936845b5337aee462ce8e649eb1b5 ve 189 maç/189 normal süre skoru ön koşuldur. Genel transfer arayüzü başka lig ve sezonları da kabul eder. Bu nedenle production operatörü için bu değerleri zorunlu tutan, SQL ve gerçek R2 doğrulamasını ayrı raporlayan bir çalıştırma yolu ve bağımsız staging provası tamamlanmadan ilk yük hazır sayılmaz.


## Render Free PostgreSQL: servis bağlantısı ve gerçek yedek kapısı

**Kritik:** Render bağlayıcısı servis ortam değişkenini okumuyor. PostgreSQL instance'ının aynı workspace'te olması, `Ma-analiz-2` servisinin bu DB'yi kullandığını kanıtlamaz. Yetkili operatör Render Dashboard'da `Ma-analiz-2 > Environment` altındaki `DATABASE_URL` referansının sunucu ve veritabanı adını (parola veya tam URL'yi dışarı aktarmadan) `cbcxbcxbcv > Connect` verileriyle karşılaştırmalıdır. Eşleşme doğrulanmadan üretim migration'ı veya ingest yoktur.

**Free plan:** Render'ın kendi on-demand logical backup/PITR özelliği Free Postgres için mevcut değildir. 2026-11-28 itibarıyla API'deki instance `expiresAt` değeri `2026-12-16T19:41:50Z` idi. Gerçek yedek henüz alınmadı. Hemen ayrı bir operatör bilgisayarında güvenli `pg_dump` yedeği planlanmalıdır.

**Windows yerel yedek:** `scripts/backup_render_postgres.ps1`, sadece beklenen Render instance/username kabul eder, PostgreSQL komut satırı araçlarını kullanır, parolayı PowerShell'de güvenli prompt ile alır ve en sonda ortamdan siler. Önce Render Dashboard > `cbcxbcxbcv` > Connect ekranında *External Hostname* ve kullanıcı adını doğrulayın. Sonra örnek komut:

```powershell
pwsh -File scripts/backup_render_postgres.ps1 -HostName "<Render External Hostname>" -DatabaseUser "cbcxbcxbcv_user"
```

Bu örnek parola veya URL içermez. Script, döküm dosyasının boyutunu, SHA-256'sını ve `pg_restore --list` başarısını raporlar; gerçek veri geri yüklemesi yapmaz. Döküm dosyasını GitHub'a, herkese açık depoya veya sohbete göndermeyin. `PGSSLMODE=require` taşıma şifrelemesini zorunlu kılar; ek sertifika doğrulaması altyapı imkânlarına göre ele alınmalıdır. Tam bağımsız `pg_restore` provası, şifreli/offline yedek saklama ve DB kaynağı eşleştirmesi ayrı kontrol kapılarıdır.


## Alternatif: PostgreSQL'e dokunmadan R2-only sezon arşivi

`python -m app.jobs.first_ucl_r2_archive` varsayılan olarak yalnızca 2024–2025 Şampiyonlar Ligi dosyasını indirir; SHA-256, kaynak revizyonu, 189 fikstür ve 189 normal süre skorunu kontrol eder. Veritabanına **hiç bağlanmaz**, R2'ye hiçbir istek göndermez.

Gerçek R2 arşivleme ayrı ve açık operatör izni gerektirir:
```bash
python -m app.jobs.first_ucl_r2_archive --live --approval ARCHIVE_UCL_2024_2025_R2_ONLY
```

Komut `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` yapılandırması olan **yetkili yürütme ortamında** çalıştırılmalıdır; gizli anahtarları GitHub'a/sohbete taşımayın. Bu özellik PR #57 Draft iken mevcut canlı servise dağıtılmış değildir. Ayrı bucket testi veya açık production bucket yetkisi olmadan çalıştırılmamalıdır.

Yazma davranışı üç değişmez nesneyle sınırlıdır: orijinal kaynak dosyası, 189 satırlık Parquet, doğrulama manifesti. İçerik-adresli anahtarlarda önceden nesne varsa tekrar yazmaz; HEAD+GET+SHA-256 kontrolü yapar; uyuşmazlıkta durur. Hata sonrası geride kalan nesneler bir sonraki çağrıda doğrulanarak yeniden kullanılabilir. Silme yoktur. Bu mod **PostgreSQL tablosu veya arayüz maç listesi oluşturmaz**; sonradan arşivden veritabanına aktarma ayrıca planlanabilir. Gerçek yüklemenin tamamlandığı ancak gerçek R2 okumalı SHA-256 doğrulaması ve bağımsız makbuzlarla ilan edilir.


## R2 arşivini arayüzde açma ve günlük maç toplama (henüz deploy edilmedi)

- `GET /api/v1/archive/r2/champions-league?page=1&page_size=25&team=` R2 manifesti + ham kaynak + Parquet SHA-256 ve 189 maç/189 skor kontrolleriyle okur. PostgreSQL kullanmaz. Arşiv eksik veya bozuksa `503 R2_ARCHIVE_UNAVAILABLE` döndürür; veriyi uydurmaz.
- `/europe` sayfasında 2024–25 Şampiyonlar Ligi R2 arşivi için ayrı filtreli ve sayfalı tablo bulunur. Bu değişiklik PR #57 Draft branch'indedir; Render servisi farklı branch'te ve auto-deploy kapalıdır. Canlı sitede görüntü henüz aktif **değildir**.
- Günlük pilot: `python -m app.jobs.daily_r2_matches --date 2026-11-28` yalnızca Bundesliga 2026–27 için **dry-run** yapar. Ek izinli kaynaklar: `--league bundesliga --league champions-league --league europa-league --league conference-league`. Kaynak OpenLigaDB'dir; kapsam kaynakta maç bulunan liglerle sınırlıdır, tüm dünyadaki maçları içermez. TSİ günü kullanır, skorlar bağımsız doğrulama değildir, oran yoktur.
- **Yazma yetkilendirmesi ayrı:** `python -m app.jobs.daily_r2_matches --date 2026-11-28 --live --approval ARCHIVE_DAILY_2026_2027_R2_ONLY` yalnızca açıkça onaylı operator ortamında kullanılmalıdır. İçerik-adresli günlük snapshot'lar R2'de `daily/openligadb/season=2026-2027/league=.../date=YYYY-MM-DD/<sha256>.json` yolunda saklanır. Var olan snapshot değiştirilmez, SHA256 ile geri okunarak kontrol edilir. Kaynak güncellenirse yeni snapshot eklenir.
- **Otomatik zamanlama henüz etkin değil.** Günlük işin gerçekten kendiliğinden işlemesi için güvenilir, secret'ları gizli tutan bir scheduler/worker ve ayrıca dağıtım/yazma yetkisi gerekir. Kaynağın döndürdüğü 0 maç, "tüm maçlar arşivlendi" anlamına gelmez.
- **Daha önce onaylanmış 189 maçlık R2 yazma izni, 28 Kasım 2026 canlı günlük veri yazmalarını otomatik olarak kapsamaz.** Yeni canlı iş için ayrı açık operasyon onayı alınmalıdır.


## Günlük R2 otomasyon kapısı — henüz devreye alınmadı

`daily_r2_matches` işinin canlı modunda bundan böyle `DAILY_R2_EXPECTED_BUCKET` ortam değişkeni zorunludur. İzin verilmiş bucket ile R2 istemcisinin gerçek bucket'ı eşleşmezse **hiç yazmadan** durur. İlk hedef bucket `sdfgdsgdg`; bu ad, Codex'e güvenli biçimde yapılandırılır. `--live` ve ayrı onay token'ı yine gereklidir.

28 Kasım 2026 Bundesliga snapshot'ı 5 planlanmış maç/0 skorla R2'ye arşivlenmiş olarak Codex tarafından raporlandı; sonraki gerçek sonuçlar yeni SHA256 nesne anahtarına kaydedilmeli, eskiler değiştirilmemelidir. Bu noktada saatlik/15 dakikalık canlı görev **başlatılmamıştır**.

Gelecekteki zamanlayıcının önerilen teknik gereksinimleri:
- TSİ tarihli günü ve gece yarısı sonrası bir önceki günü ele al; kaynak çağrılarını lig başına >=60 saniye arayla sınırla, 429 yanıtında geriye çekil, boş sonuçları eksiksiz arşiv sayma.
- Sadece açıkça yetkilendirilmiş Bundesliga 2026–27 kapsamını ilk aşamada aç; UEFA ligleri, yeni sezonlar ve diğer kaynaklar için ayrı inceleme gerekir.
- Tekrar çağrılarında aynı içerik hash'i değişmez nesneyi kullan; skor/durum değiştiğinde yeni nesne oluştur; zaman damgası gibi kaynaktan bağımsız değişken alanları hash'e dahil etme.
- Her denemede gizli bilgileri yazdırmadan kaynak durumu, 5 maç gibi doğrulanmış kayıt sayısı, skor sayısı, yeni/reuse nesneler ve SHA256 doğrulamasını raporla; hatalar görünür olsun.
- GitHub Actions, Render Cron veya worker'da gerçek zamanlayıcı kullanmak ayrı **deploy/cron** ve devam eden gerçek R2 yazma/onay kapsamıdır. Otomatik etkinleştirme, herhangi bir zamanlayıcı workflow'u veya secret güncellemesi bu PR değişikliğiyle yapılmadı.

## Bundesliga günlük takip tasarımı

- Başlangıç önerisi TSİ 09.07 ve 12.07 bugün için sezon kaynağını kontrol etmek, 01.07 önceki TSİ gününü sonuçlandırmaktır. Maç başlangıcından 15 dakika öncesinden son başlama saatinden 120 dakika sonrasına kadar yalnız maç varsa 15 dakikada bir çalıştırma, ardından 30 ve 90 dakika sonra son kontrol önerilir. Saatler başlangıç tasarımıdır. Geciken maçlarda zaman penceresi gözden geçirilmelidir.
- Kaynak endpoint'i bir sezondaki bütün maçları döndürür. Aynı işlemde bugün ve dün filtrelenecekse tek yanıt paylaşılmalıdır. Mevcut günlük komut her tarih için ayrı sorgu yapar ve bu paylaşım henüz uygulanmadı. 15 dakikalık 2–3 saatlik tek kesintisiz maç aralığında 8–12 sorgu, iki gündüz kontrolü, iki maç sonrası kontrolü ve bir gece kontrolü ile yaklaşık 13–17 kaynak isteği/gün gerekir. Birden fazla ayrı maç aralığı veya tekrar denemesi sayıyı artırır.
- Mevcut Bundesliga SQL/R2 iş akışının günlük üç istekli PostgreSQL kotası ile bu R2 işi arasında ortak kota veya süreçler arası hız sınırlaması yoktur. Canlıya geçmeden tek sahipli bir işleyici, kalıcı paylaşılan istek bütçesi, eşzamanlılık kilidi ve kaynak güncelleme kontrolü tasarlanmalı ve diğer işler ile birlikte değerlendirilmelidir. Kota artırımı ayrı güvenlik incelemesine bağlıdır.
- R2 koşullu PUT ve HEAD/GET ile kontrol edilen SHA-256 içerik adresleri kullanır. Değişmeyen içerikte yeni PUT yoktur. Kaynak güncelleme zamanı tek başına sürüm oluşturmaz. Maç, durum veya skor değişirse yeni nesne yazılır ve önceki nesne kalır. Yeni normalleştirme öncesinde yazılmış snapshot ile ilk yeni snapshot aynı maçları içerse de farklı anahtar taşıyabilir. Boş kaynak için nesne yazılmaz, boş yanıt kapsamın tamamlandığını kanıtlamaz. Ağ hatası R2 yazımından önce işi durdurur. Belirsiz PUT sonucunda tekrar çalışma mevcut nesneyi tam doğruladıktan sonra kullanır. Her çalışma kaynak isteği ve R2 HEAD/GET işlemleri doğurabilir.
- Sağlayıcının LIVE durumu bitmemiş ve başlama zamanı geçmiş maçlardan türetilir. Erteleme veya kesin canlı durum teyidi değildir. Eksik skor null kalır, bağımsız doğrulanmış skor sayılmaz.
- Açık operator onayı, DAILY_R2_EXPECTED_BUCKET=sdfgdsgdg ve eşleşen R2_BUCKET gerekir. Gerçek R2 staging provası, toplam istek ve depolama maliyetinin değerlendirilmesi, güvenlik incelemesi, deploy ve zamanlayıcının etkinleştirilmesi ayrı onay gerektirir. Bu tasarım cron veya zamanlayıcı oluşturmaz.

## Ortak kota değerlendirmesi — BLOCKED

Bundesliga live pipeline PostgreSQL UTC kotası kullanır. Günlük R2 arşivi, kaynak bülteni, günlük kaynak snapshot'ı, Avrupa API'sinin Bundesliga endpoint'i ve Goaloo kolektörü ayrı süreçlerde doğrudan OpenLigaDB çağırır. Sağlayıcı içindeki kilit ve bülten önbelleği yalnız kendi sürecindedir. Bunlar üç istekli PostgreSQL kotasına dahil değildir. PR #56 henüz ayrı daldadır ve tüm tüketicileri de kapsamaz. GitHub Actions concurrency grupları Render işçileri veya diğer iş akışlarıyla ortak kilit değildir. PostgreSQL dışı paylaşımlı ve atomik sayaç hizmeti konfigürasyonu mevcut olmadığından tüm sistem için güvenli ortak bütçe, kilit, 429 koordinasyonu ve kota sonrası retry zamanı doğrulanmış değildir. Yeni zamanlayıcı açılmamalıdır. Önerilen çözüm merkezi tek istek aracısı veya atomik koşullu güncelleme ve TTL sağlayan ortak bir koordinatör, tüm çağrıları geçmeye zorlayan dağıtım ve bypass kontrolleridir. Günlük üç istek limiti onaysız değiştirilmemelidir.

Günlük modülde --include-previous-day TSİ takvim günü ve önceki gün için tek sezon yanıtını ayrı tarih snapshot'larına böler. Her biri kaynak maçı varsa arşivlenir. Saat geçmesine dayalı LIVE durumu arşivde IN_PROGRESS_UNCONFIRMED olarak gösterilir. Bu özellik paylaşımlı kota sağlamaz.

## R2 staging provası ve günlük tahmin

Ayrı bucket ve yalnız bu bucket'a sınırlı credentials ile kimlik kontrolü, HEAD, koşullu PUT, GET/SHA-256, eski sürüm korunması, PUT sonrası bağlantı kesilmesi ve tekrar denemede PUT yapılmaması test edilmelidir. Bu PR'daki testler yalnız bellek içi deposuyla çalışmıştır. Gerçek staging için kaynak/bucket kimliği ve yazma yetkisi ayrıca onaylanmalıdır.

Önceki zamanlama tasarımında günde 13–17 kaynak çağrısı ve her seferinde değişen bir günlük snapshot varsayımıyla yaklaşık 13–17 R2 Class A PUT ve 39–51 Class B HEAD/GET hesaplanır. Her snapshot yaklaşık 3.4 KB ise 17 sürüm yaklaşık 57 KB/gün ekler. Standard ücretleri ücretsiz kota dışındaki 17 PUT ve 51 okuma için yaklaşık 0.000095 USD/gün, depolama hariçtir. Standard depolama 0.015 USD/GB-aydır. Boş kaynak, değişmeyen içerik, hata ve eşzamanlılık maliyeti değiştirir. 404, yeniden denemeler, diğer ligler, koordinasyon, diğer uygulamalar ve vergiler hesaba dahil değildir. Cloudflare kotası hesap genelinde paylaşılır, gerçek fatura hesap kullanımına bağlıdır. Kaynaklar: https://developers.cloudflare.com/r2/pricing/ ve https://www.openligadb.de/nutzungsbedingungen .

## PostgreSQL dışı ortak kota tasarımı — BLOCKED

Bütün süreçlerin başvuracağı tek, atomik ve kalıcı bir koordinasyon hizmeti gerekir. Öneri, doğrusal tutarlı ve veri kaybında erişimi kapatan bir koordinatörde tek işlemle UTC günü için sayaç ve son istek zamanını güncellemektir. İşlem, önceki kullanım ve mevcut en düşük geçerli sınır üzerinden üç isteği aşmadan rezervasyon yapmalı, 60 saniye aralık sağlamalı ve her HTTP denemesi öncesi çalışmalıdır. 429 dönüşü merkezi retry-after süresini uzatmalı, saat sınırı ve servis kesintisinde işleyiciler durmalıdır. Bir işleyici yanıtını beklerken başkası rezervasyonu geçemez. Rezervasyonlar başarısız HTTP için de tutulmalıdır. UTC gün değişiminde sayaç yenilenirken 60 saniye sınırı korunmalıdır.

Provider'ın tüm Bundesliga girişleri, scheduled GitHub Actions ve Render süreçleri aynı koordinatöre zorunlu bağlanmalı. Erişim ve yedekli düğüm davranışı test edilmeli. Bellek içi kilit, yerel dosya veya sadece GitHub concurrency farklı makineleri koordine edemez. R2 koşullu PUT tek başına sayaç tutarlılığı, sahiplik ve güvenli failover için kanıt değildir. Mevcut PostgreSQL üç istek sayacı ile geçiş sırasında toplam kullanımın aşılmaması için geçiş penceresi ve eski işler kapatılmadan yeni koordinasyonun kullanımı ayrıca doğrulanmalıdır. Kullanılabilir paylaşımlı koordinatör ve tüm tüketicilerin dağıtımı doğrulanmadığından implementasyon ve otomasyon BLOCKED kalır.

## Test secret güvenliği

Pytest conftest başlamadan gerçek proje .env dosyasını test yapılandırması olarak yüklemez. Test alt süreçleri test modunu devralır. Test database URL'leri yalnızca kodda listelenen yerel ve sentetik CI değerlerine izin verir. Başarısız test ve test toplama raporlarında hata ayrıntısı ve yakalanmış çıktılar, secret sızıntısını engellemek için bastırılır. Bu sınır tüm uygulama günlüklerini veya dış servis günlüklerini kapsamaz.
