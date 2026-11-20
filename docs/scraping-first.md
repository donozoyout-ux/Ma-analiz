# Ma-analiz: WEB SCRAPING FIRST

## İlk teslimat ve çalıştırma

Python 3.11+ ve `backend/requirements.txt` bağımlılıkları hazırken backend klasöründe:

```powershell
cd 'C:\Users\PC\Desktop\Maçanaliz\backend'
python -m app.jobs.scraping_probe --league 8 --date 2026-11-20 --max-matches 1
```

Komut yalnız bir lig ve bir İstanbul takvim günü kabul eder. İlk incelenen lig Bundesliga (`8`), diğer desteklenen tekil seçim Premier League (`36`). Virgüllü lig listesi, tarih aralığı, sezon taraması ve `--persist` seçeneği yoktur. Ayrıntılar en fazla üç maçla sınırlıdır. Varsayılan hız kaynak başına aynı adaptörde en çok 6 istek/dakika; robots crawl-delay/request-rate daha yavaşsa o sınır uygulanır. İstek bütçesi 12; engellenen yollar, 401/403/429 ve yönlendirmeler izlenmez/yeniden denenmez. TLS doğrulaması kapatılmaz. Kaynaklarda başka host veya proxy üzerinden dolaşma yoktur.

Goaloo fikstürü tek sezonluk JSON dosyası olarak yayımlıyor. Komut yalnız seçilen lig/sezona ait tek dosyayı okur, çıktıyı seçilen güne süzer; diğer sezonlara/liglere geçmez, geçmiş sezon taraması veya toplu yükleme başlatmaz. Bu kaynak granülaritesi raporda belirtilir. Gün boşsa başka güne otomatik geçilmez. `live_test=false` yalnız test adaptörü enjekte edildiğinde kullanılır; otomatik test verisi canlı örnek sayılmaz.

JSON çıktısı:

- `scope`: tek lig, sezon, tarih ve `Europe/Istanbul`.
- `match_count`: seçilen günün normalize edilmiş gerçek kaynak kayıtları; ağ hatasında null.
- `matches_tested` ve `untested_match_details`: sınırlı detay kontrolünün kapsamı.
- `fields`: fikstür, skor, oran ve teknik istatistiklerin ayrı sonuçları.
- `odds`: varsa bookmaker, market, seçim, decimal oran, kaynak, gözlem zamanı, dönem, gerçek snapshot türü. Erişim engelinde boş liste.
- `requests`: salt-okunur istek adresleri, HTTP durumları, SHA-256 ve gözlem zamanları; robots metni ayrıca tutulur.
- `writes=false`, `sql=NOT_USED`, `r2=NOT_USED`, `paid_fallback=false`: bu komutun canlı kalıcı veri yazmadığını belirtir.

Çıkış kodu 0 yalnız gerekli erişim kontrolleri geçmiş, 1 başarısız/kısmi, 2 seçili günde boş kaynak demektir. `PARTIAL` çalışmayan komut değil; eksik veri erişimini başarılı göstermeyen sonuçtur. JSON ASCII kaçışları Windows terminal kodlamalarında veri kaybını önler; JSON okuyucuları Unicode metinleri geri açar.

## Gerçek canlı test: 20 Kasım 2026 Bundesliga

Makine tarafından üretilen [kanıt ve küçük örnek](reports/scraping-2026-11-20-bundesliga.json) saklandı. Kapsam 20 Kasım olarak sabit tutuldu; gözlem 20 Kasım 21:02–21:03 UTC, Türkiye saatinde **21 Kasım 00:02–00:03** aralığında yapıldı. Gece yarısı geçtiğinde ikinci bir gün taranmadı.

- Fikstür: HTTP 200, **1 maç**. Goaloo kimliği `3018869`, `Bayern Munchen` – `FC Koln`, başlangıç `2026-11-20 19:30 UTC` (22:30 TSİ).
- Kaynağın sezon kaydında durum **SCHEDULED**, skor boş. Saat geçmiş olsa da bitmiş maç veya 0–0 sonucu üretilmedi. Doğrulanmış sonuç sayısı **0**.
- Teknik maç HTML sayfası: HTTP 200, **29 gözlem satırı**. Örneğin kaynak o anda şutları 4–1, kornerleri 1–0 gösteriyordu. Bunlar final istatistiği olarak onaylanmadı.
- Sezon sayfası henüz planlandı derken teknik sayfa değer içerdiği için kaynak durumu tutarsız kabul edildi. `statistics=OBSERVED_UNVERIFIED`; **modelde kullanılabilir teknik satır 0**. Ham gözlemler kaynak adresleriyle korundu, analiz girdisine aktarılmadı. Rapordaki son doğrulama notu yerel değerlendirmedir; yeni ağ isteği değildir.
- Oran endpoint'i `/ajax/soccerajax`: `www.goaloo.com/robots.txt` içindeki `Disallow: /ajax/` nedeniyle **hiç çağrılmadı**. Gerçek oran sayısı **0**, örnek oran listesi boş. Açılış ve kapanış **NOT_AVAILABLE**.
- Toplam dört izinli GET: iki robots belgesi, tek fikstür dosyası ve tek teknik maç sayfası. Oran, skor veya isabet yüzdesi uydurulmadı.
- Sonuç: **PARTIAL**. İzinli bir oran kaynağından doğrulanmış örnek elde edilemedi; bu teslimat bütün veri alanları için başarı iddia etmez.

## Kaynak kapsamı ve erişim kararları

### Goaloo

[Robots: fikstür hostu](https://football.goaloo.com/robots.txt), [robots: ana host](https://www.goaloo.com/robots.txt), [kullanım metni](https://www.goaloo.com/disclaimer), [ücretsiz feed açıklaması](https://www.goaloo.com/freemap).

Fikstür JSON'u ve maç teknik HTML'i yukarıdaki dar kapsamda gerçekten okundu. `/ajax/` yolundaki oran erişimi yasak. Ücretsiz feed açıklaması bir iframe gömme hizmetidir; ham verinin ticari olarak yeniden yayımlanması veya arşivlenmesine genel izin sayılmadı. Robots izni telif/veri lisansı değildir. Geniş ölçekli ürün kullanımı ve yeniden dağıtım izni **doğrulanmış değildir**; bu çalışma salt-okunur kaynak değerlendirmesidir. Kaynak sözleşmesi ve güncellik garantisi yoktur. Eski veya tutarsız durumlar düzeltilmiş gibi sunulmaz.

### football-data.co.uk: CSV değerlendirmesi, indirme kapalı

[Yayımlayıcının veri ve kullanım açıklaması](https://football-data.co.uk/data.php), [alan açıklamaları](https://football-data.co.uk/notes.txt).

Yayımlayıcı 22 ana Avrupa lig düzeyi ile 16 ek ülke üst ligi için dönemlere göre değişen sonuç, istatistik ve oran kapsamı bildiriyor; Türkiye de ana ülke listesinde. Ana kapsam İngiltere, İskoçya, Almanya, İtalya, İspanya, Fransa, Hollanda, Belçika, Portekiz, Türkiye ve Yunanistan. Teknik alanlar ve bookmaker kapsamı sezon/lig bazında aynı değil. İlk oran seti açılıştan sonra belirli toplama saatlerinde alınır; **açılış oranı diye etiketlenemez**. Kapanış için ancak kaynağın açıkça C olarak tanımladığı sütun kullanılabilir. Pinnacle'ın 23 Temmuz 2025 sonrası güncelliği hakkında yayımlayıcı ayrıca uyarıyor.

Güncel kullanım açıklaması özel bireysel kullanımı hedefliyor; otomatik bot/scraper/AI kullanan ticari veya veri eğitimi ürünlerini dışlıyor. Ma-analiz için otomatik CSV indirme/ithal etme **etkinleştirilmedi**; örnek CSV veya sezon taraması yapılmadı. Robots sorgusunda yerel TLS/ağ doğrulama sorunu yaşandı; sertifika kontrolü kapatılmadı. İzin kısıtı nedeniyle robots erişiminin açılması da bu ürün için indirmeyi tek başına uygun kılmaz.

### Açık tarihsel veri adayları: veri indirilmedi

[OpenFootball football.json](https://github.com/openfootball/football.json) ve [CC0 lisansı](https://github.com/openfootball/football.json/blob/master/LICENSE.md): açık fikstür/sonuç verileri; İngiltere, Almanya, İspanya, İtalya, Fransa ve başka topluluk ligleri. Oran/teknik istatistik kaynağı olarak gösterilmez. JSON üretiminin günlük olması, kaynak sonuçlarının her gün güncellendiğini garanti etmez. TXT/JSON açık veri adayı olarak değerlendirildi; tek lig/gün sınırını genişletmek için arka planda sezon indirilmedi.

[OpenLigaDB lisansı](https://www.openligadb.de/lizenz) ve [erişim şartları](https://www.openligadb.de/nutzungsbedingungen): ücretsiz topluluk fikstür/sonuç hizmeti, ODbL 1.0, kaynak belirtme ve değiştirilmiş veri tabanı dağıtımında paylaşım koşulları var. Canlı maçta lig başına 30–60 saniyeden sık sorgu istenmiyor; diğer zamanlarda çok daha seyrek erişim, önbellek, değişiklik kontrolü ve iletişim içeren User-Agent bekleniyor. Teknik istatistik/oran alternatifi olarak sunulmadı. Ücretli API bağımlılığı değildir, fakat WEB SCRAPING FIRST kararını otomatik API fallback'e çevirmemek için etkinleştirilmedi veya veri çağrısı yapılmadı.

## Uygulama ve depolama sınırları

- Sportmonks token'ı artık sağlayıcı seçtirmez. Açık Sportmonks seçimi reddedilir; registry, eski probe ve legacy smoke girişleri ağ/yazma yapmaz. Eski adaptör sadece MockTransport ile çevrimdışı wire-regression testleri için korunur. Ücretli kaynağa otomatik fallback yoktur.
- Mevcut günlük SQL upsert akışı ve R2 partition/Parquet formatları korundu; bu teslimatta canlı SQL/R2 yazması yapılmadı. Veri alınabilen bir kaynak için günlük aktif kayıtlar PostgreSQL'e, izinli/doğrulanmış tarihsel kayıtlar mevcut R2 arşiv akışına uygundur. Canlı persistence/R2 erişimi burada doğrulanmadı.
- Mevcut oran saklama sözleşmesi bookmaker, market, selection, odds, source, captured_at, period, snapshot_type alanlarını korur. Gerçek oran yoksa boş kalır. Gerçek OPENING/CLOSING kayıtları olmadığında türetilmez; hareket mevcut gerçek snapshot'lar üzerinden mevcut projeksiyonda hesaplanır. `LATEST` bitmiş maçta otomatik CLOSING yapılmaz.
- Eşleştirme yalnız incelenmiş lig kimliği, tam UTC başlangıç zamanı ve güvenli Unicode/boşluk/büyük-küçük harf normalizasyonuyla eşit ev/deplasman isimleri üzerinden yapılır. Fuzzy isim, takım son eklerini silme veya yaklaşık saat eşleştirmesi yoktur. Bilinmeyen lig/isim alias'ı otomatik birleştirilmez.
- Günlük SQL upsert, eşleşen farklı kaynak kimliğini **atlayarak** mükerrer eklemeyi önler; skor/oranları başka kaynak kimliğine sessizce bağlamaz. PostgreSQL transaction advisory lock aynı uygulama yolundan geçen eşzamanlı yazıcıları sıralar. Bu koruma harici SQL yazıcılarına benzersiz şema kısıtı sağlamaz. Kalıcı çapraz kaynak alias tablosu eklenmedi; bunun için migration çalıştırılmadı.
- R2 sonuç/oran/teknik gözlem birleştirmeleri artık yalnız provider+match ID ile yapılır. Kaynağı farklı ama ID'si aynı satırlar birleştirilmez. Eski kaynaksız satırlar sadece kendi kaynaksız namespace'iyle eşleşebilir.
- Analiz sisteminde SQL'in FINAL olarak sakladığı doğrulanmış sonuçların form/iç saha/deplasman/gol analizine girmesini engelleyen FINISHED-only filtresi düzeltildi. R2'deki tarihli teknik gözlemler analiz özetine bağlandı: yalnız doğrulanmış geçmiş maçlar, analiz anından önce gözlenmiş sayısal değerler ve son gözlem kullanılır; eksikler sıfırla doldurulmaz. Teknik alanlar model ağırlıkları yeniden eğitilmiş gibi sunulmaz.
- Tek günlük probe geçmiş form örnekleri indirmez; geçmiş yoksa yetersiz kanıt gösterir. Tam teknik istatistikler yalnız kaynak ve maç durumu tutarlıysa kullanılabilir.

İlk rollout koruması: normal provider registry, Goaloo için birden fazla lig veya sezon seçen eski ortam yapılandırmasını `SCRAPING_SCOPE_LIMIT` ile ağ isteğinden önce durdurur. Sessizce ilk lige/sezona geçmez. Böylece bu commit'i alan eski geniş kapsamlı worker, test sınırını aşarak tarama başlatamaz. Geniş kapsamın açılması bu teslimatın parçası değildir.

`.env.example` tek lig ve güncel sezona daraltıldı; gerçek `.env`, Render ayarları ve Start Command değiştirilmedi. Üretimdeki eski ortam değişkenleri bu örnek dosyanın değişmesiyle otomatik güncellenmez. Mevcut arayüz, migration dosyaları ve gerçek R2 arşivleri değiştirilmedi.

## Testler

```powershell
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py
```

Otomatik testlerde geniş lig/sezon dolaşan eski canlı testler ve gerçek servis testleri hariç tutulur. Canlı doğrulama yalnız yukarıdaki sabit tek günlük probe ile yapıldı. Test fixture'ları canlı veri yerine kullanılmaz. Robots engeli, yönlendirme/403/429 sonrası durma, hız kuralları, ücretli sağlayıcı yasağı, dar kapsam, güvenli eşleştirme, skor/istatistik eksikleri ve zamanında erişilebilir teknik veri regresyonları test edilir.
