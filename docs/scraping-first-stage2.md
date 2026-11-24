# WEB SCRAPING FIRST: ikinci aşama, ilk final sonuç zinciri

## Gerçek sonuç

**20 Kasım 2026, Bundesliga: FC Bayern München – 1. FC Köln, 1–1 FINAL.** Başlangıç 19:30 UTC / 22:30 Türkiye saati. Aynı maç için Goaloo `3018869` ve OpenLigaDB `83237` açık bitiş durumu, UTC başlangıcı ve final skorda uyuştu. Bu, kaynakların gözlem anındaki mutabakatıdır; resmi federasyon onayı veya değişmez tarihsel kayıt garantisi değildir.

[Makine tarafından üretilen kanıt raporu](reports/result-chain-2026-11-20-bundesliga.json) `RESULT_VERIFIED` ve çıkış kodu 0 üretti. Son kontrol 20 Kasım 21:35:33–21:35:46 UTC, Türkiye saatinde **21 Kasım 00:35** aralığında yapıldı. İlk aşamada seçilen 20 Kasım günü korunmuştur; ikinci bir gün veya lig taranmamıştır.

- Goaloo: `FINISHED`, final skor 1–1, maç ID `3018869`.
- OpenLigaDB: `matchIsFinished=true`, full-time `resultTypeID=2`, skor 1–1; maç ID `83237`, ev sahibi takım ID `40`, deplasman ID `65`. Birincil yeniden kullanılabilir sonuç kaynağı OpenLigaDB'dir.
- OpenFootball: aynı gün ve takım çifti için fikstür, yerel saat metni `20:30`; skor henüz yok. Zaman dilimi tanımlanmadığı için UTC saat tahmin edilmedi. Skor/bitiş yokluğu, başka kaynaktaki açık final sonucuyla çelişen bir final skor gibi değerlendirilmedi; eksik/güncellenmemiş teyit olarak uyarıda tutuldu.
- Başlangıç/bitmişlik/final skor bakımından eşleşen iki kaynak arasında çelişki bulunmadı. Skor veya UTC saati farklı olsaydı `CONFLICT` ve `final_result=null` üretilecekti.
- Altı salt-okunur GET: her veri hostunun robots belgesi, tek Goaloo sezon dosyası, tek OpenFootball lig dosyası ve tek açık OpenLigaDB maç haftası kaynağı. Robots'u olmayan hostların 404 yanıtları raporlandı; yönlendirme, sertifika atlama, kimlik doğrulama veya engelli URL erişimi yapılmadı.

## Çalıştırma

Python ve mevcut backend bağımlılıklarıyla:

```powershell
cd 'C:\Users\PC\Desktop\Maçanaliz\backend'
python -m app.jobs.result_probe --date 2026-11-20 --matchday 10 --goaloo-match-id 3018869
```

JSON raporu yerel dosyaya almak için:

```powershell
python -m app.jobs.result_probe --date 2026-11-20 --matchday 10 --goaloo-match-id 3018869 |
    Set-Content -Encoding utf8 ../docs/reports/result-chain-2026-11-20-bundesliga.json
$LASTEXITCODE
```

Komut Bundesliga ile sınırlıdır; başka lig, tarih aralığı, çoklu maç haftası, otomatik gün/maç arama veya `--persist` kabul etmez. `--matchday` açık bir tek kaynak haftasıdır. Kaynaklar günlük dosya yayımlamadığından iki lig-sezon dosyası ile bir lig-hafta dosyası okunur; yalnız seçilen gün değerlendirilir ve rapora alınır. Diğer günler/sezonlar/ligler dolaşılmaz; ham sezon dosyaları yerel arşiv olarak kaydedilmez. Kontrol başarısızsa başka gün veya kaynağa sessiz geçiş yapılmaz.

Çıkış 0: sonuç zinciri doğrulandı. Çıkış 1: sonuç doğrulanamadı veya çelişki var. 0, PostgreSQL/R2 importunun çalıştığı veya oran/teknik istatistiklerin tamamlandığı anlamına gelmez.

## Kimlik ve final kabul kuralları

[Eşleştirme inceleme kaydı](reports/result-alias-review-2026-11-20.json), Goaloo'nun `Bayern Munchen` / `FC Koln` adlarını bu maç için OpenLigaDB'nin `FC Bayern München` / `1. FC Köln` adlarıyla ilişkilendirir. Ham yanıt SHA-256 değerleri ve kaynak ID'leri kayıtta tutulur. Bu eşleştirme sadece Goaloo ID `3018869`, lig `de.bl1`, UTC başlangıç `2026-11-20T19:30:00Z` için uygulanır. OpenLigaDB tarafında maç ID'si ve iki takım ID'si de eşleşmelidir. Genel alias kayıtları veya mevcut günlük ingestion kimlik kuralları değiştirilmedi.

Aksanları kaldırma, `FC` veya takım son eklerini silme, yaklaşık saat veya fuzzy isim eşleştirmesi yapılmaz. Farklı ID, yanlış ev/deplasman sırası veya farklı kickoff otomatik kabul edilmez. OpenFootball kaynak maç ID'si vermediği için içerik parmak izi ayrı etiketlenir; kaynak ID'siymiş gibi sunulmaz.

Final kabulü için en az iki kaynağın tam fixture kimliği ve UTC saati uyuşmalı; yeniden kullanım lisansı doğrulanmış bir kaynak açıkça bitmiş demeli ve geçerli full-time skor vermelidir. Başlangıç gözlem anından önce olmalıdır. Sürenin geçmiş olması, yarı zaman skoru, dolu bir skor alanı, string `"true"` veya sayısal `1` bitiş kanıtı sayılmaz. Açıkça bitmemiş kaynak varsa çelişki/eksik doğrulama raporlanır; kazanan kaynak seçilip üstü örtülmez.

OpenFootball'da `score.ft` görülse bile açık bitiş işareti yerine kullanılmaz. Bilinmeyen saat dilimi Berlin varsayımıyla doldurulmaz. Bu çalışmada güçlü saat/bitiş/skor teyidi OpenLigaDB ve Goaloo'dan geldi.

## Lisans, güncellik ve yeniden kullanım

### OpenFootball

[CC0 1.0 lisansı](https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md) ve [yayım/güncelleme açıklaması](https://github.com/openfootball/football.json) 21 Kasım 2026 itibarıyla yeniden incelendi. Veriler kamu malı/CC0 olarak yayımlanıyor; ticari yeniden kullanıma da izin veriyor. Günlük JSON üretimi, topluluk TXT kaynaklarının her gün güncel olduğu anlamına gelmiyor. Canlı gözlemde skorun hâlâ eksik olması bu sınırlamayı somutlaştırdı. Oran veya ayrıntılı teknik istatistik kaynağı değildir.

### OpenLigaDB

[Lisans](https://www.openligadb.de/lizenz), [erişim şartları](https://www.openligadb.de/nutzungsbedingungen) ve [resmi API dokümantasyonu](https://github.com/OpenLigaDB/OpenLigaDB-Samples) yeniden incelendi. Veri ODbL 1.0 altında; ticari kullanım mümkün, kaynak belirtme ve değiştirilmiş veri tabanı dağıtımında paylaşım koşulları var. Kanıt raporunda kaynak/lisans bilgisi tutulur. OpenLigaDB'den alınan veri bölümleri bu lisans altında değerlendirilmelidir; bu not uygulama koduna yeni bir lisans atamaz. Görseller/arma hakları kapsanmıyor; hiçbir görsel indirilmedi.

Topluluk verisi için editoryal doğruluk veya değişmez geçmiş sürüm garantisi yok. Erişim halen ücretsiz ve anahtarsız; kalıcı erişim garantisi yok. Kaynak uygulama/iletişim içeren User-Agent, merkezi önbellek ve seyrek erişim istiyor. Komut aynı çalışmada cache kullanır; OpenLigaDB veri istekleri arasında en az 60 saniye uygular. Canlı testte yalnız bir veri isteği gönderildi. 429/403/yönlendirme sonrası yeniden deneme yok. Bu ücretsiz servis açıkça seçilen çapraz doğrulama kaynağıdır; production provider registry'sine veya fallback zincirine eklenmedi.

### Goaloo

İlk rapordaki robots ve yeniden dağıtım sınırlamaları geçerlidir. Bu aşamada izinli fikstür dosyası tekrar salt okunur incelendi. Açık ticari ham veri yeniden dağıtımı varsayılmadı; birincil import adayı OpenLigaDB verisidir. `/ajax/` oran endpoint'ine bu aşamada da erişilmedi.

## Teknik istatistiklerin güvenilirlik koşulları

Yeni teknik istatistik toplaması yapılmadı. İlk rapordaki 29 gözlem, kendi gerçek HTTP gözlem zamanlarıyla tekrar değerlendirildi; güncel/live veri yerine kullanılmadı. **Kullanılabilir satır: 0.** Bunlar doğrulanmış final zincirinden önce toplanmıştı. Maçın sonradan bitmesi eski gözlemleri final istatistiği yapmaz. Goaloo ham teknik verisinin yeniden kullanım izni de doğrulanmış değil.

Otomatik kabul testleri şunları gerektirir:

- Doğrulanmış final zinciri ve o zincirdeki aynı provider maç ID'si.
- Kaynak URL'si, kaynak adı ve açık yeniden kullanım izni.
- Final teyidinden sonra ve değerlendirme anından önce gözlenmiş bir zaman damgası.
- Full-time dönem, incelenmiş metrik adı ve bilinen birim; `source_unspecified` birimi kabul edilmez.
- İki tarafta gerçek sonlu ve negatif olmayan sayılar; eksikler sıfırla doldurulmaz. Count tam sayı, yüzde 0–100 aralığında olmalıdır.

Örneğin geçerli birim/izin/tarih olmadan `ftstat` etiketi ve sayıların varlığı yeterli değildir. Bu kontroller yalnız gözlem uygunluğunu test eder; gerçek model başarısı veya bağımsız istatistik doğruluğu iddiası değildir.

## Günlük oran erişimi ve kullanım değerlendirmesi

İncelenen adaylarda bu kapsam için anahtarsız ve açık ürün yeniden kullanım izni doğrulanmış bir oran kaynağı bulunamadı; `quotes=[]` bırakıldı. Bu, tüm internet için yokluk iddiası değildir.

- Goaloo: önceki kanıtta `/ajax/` robots yasağı var; hiçbir oran endpoint'i çağrılmadı.
- [football-data.co.uk kullanım metni](https://football-data.co.uk/data.php): özel bireysel kullanım sınırlaması ve otomatik ticari/AI ürün yasağı sürüyor. CSV veya oran indirilmedi.
- OpenFootball/OpenLigaDB: sonuç/fikstür kaynakları; oran sağlamıyorlar.
- [The Odds API erişimi](https://the-odds-api.com/#get-access) ve [kullanım şartları](https://the-odds-api.com/terms-and-conditions.html): ücretsiz başlangıç planı bulunsa da hesap/API anahtarı ve kota/abonelik hizmeti gerektiriyor. Ham veri servisi olarak yeniden dağıtıma kısıtlar var. Açık web scraping kaynağı veya ücretsiz kalıcı bağımlılık kabul edilmedi; kaydolunmadı, anahtar istenmedi, veri endpoint'i çağrılmadı.

Hiçbir oran, açılış/kapanış değeri veya oran hareketi üretilmedi. Sportmonks/ücretli API kullanılmadı.

## PostgreSQL/R2 hazırlığı ve test sonucu

Rapor `pipeline_preparation` içinde birincil sağlayıcı `openligadb`, kaynak maç ID `83237`, canonical lig `de.bl1`, gerçek skor ve kaynak kimlikleriyle aday kayıt sunar. Bu bir yazma işlemi değildir. Provider alias'ları, kaynak atfı/lisansı ve mevcut aktif kayıtla kontrollü eşleştirme korunmadan import etkinleştirilmez. `persistence_ready=false`, `writes_allowed=false`, SQL/R2 yazmaları false kalır. Veri doğrulaması ile storage rollout'u ayrı sonuçlardır.

Migration dosyaları, PostgreSQL, R2 arşivleri, Render ayarları ve arayüz değiştirilmedi. Hiçbir storage bağlantısı açılmadı.

```powershell
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py
```

**238 yerel otomatik test geçti.** Geniş kapsamlı eski canlı testler ve gerçek servis yazma testleri çalıştırılmadı. Yeni testler bitiş bayrağı, yanlış/çelişkili skor, UTC/scope kontrolleri, sınırlı alias'lar, tamamlanmamış kaynaklar, teknik istatistik karantinası, robots, cache ve hız sınırlarını kapsar. Test fixture'ları canlı rapora konmadı.
