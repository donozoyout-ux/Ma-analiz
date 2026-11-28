# Bundesliga istatistikleri — offline pilot ve kaynak incelemesi

## Durum

`BLOCKED_SOURCE_OR_LICENCE`. 2024–25 öncelikli pilot için, bu projenin kalıcı R2 saklaması ve sitede yeniden yayımlaması bakımından kapsamı ve yetkisi doğrulanmış gerçek istatistik dosyası bulunamadı. Gerçek istatistik eşleşmesi, korner, sarı/kırmızı kart ve şut kapsamı ile gerçek aktarım boyutu **ÖLÇÜLEMEDİ**. Sentetik fixture sonuçları gerçek veri kapsamı değildir.

28 Kasım 2026'da mevcut OpenFootball Parquet dosyaları R2'den salt okunur olarak alındı. Hash ve satır sayıları 2024–25 306, 2025–26 306 ve 2026–27 96 olarak doğrulandı. Bu sayıların istatistik kapsamına dair bir anlamı yoktur.

## Kaynak ve hak incelemesi

- **11footy** ücretsiz veri setini [CC BY-NC 4.0](https://github.com/fm-devlab/11footy-dataset/blob/main/LICENSE) ile yayımlıyor. Bundesliga'yı ve ilk iki sezona uzanan tarihsel takım istatistiklerini kapsadığını bildiriyor. Katmanlar arası tanımlar farklı ve bazı sıfır feed satırları bilinmeyen olarak ele alınmalı. Kalıcı saklama ve yeniden dağıtım lisans koşulları altında ticari olmayan kullanıma bağlıdır. Bu projenin kullanım biçimi ve sağlayıcının alttaki hakları henüz doğrulanmadı. 2026–27 saha bazlı kapsamı ölçülmedi. [Şema ve koşullar](https://github.com/fm-devlab/11footy-dataset)
- **Football-Data** üç sezon için Bundesliga CSV listeliyor. Kendi kullanım politikası kişisel/akademik kullanım ve hafif indirme için izin veriyor, ticari veya AI veri eğitimini kapsamıyor. Bu proje için saklama ve yeniden yayımlama izni doğrulanmadı. CSV'ler otomatik indirilmedi. [Politika](https://football-data.co.uk/contact.php) · [Sezon listesi](https://football-data.co.uk/germanym.php)
- **openplay** Bundesliga 2024–25 ve 2025–26 olay ve takım verisini kapsadığını, 2026–27'yi kapsamadığını belirtiyor. Yayıncı her amaçla atıflı kullanıma izin veriyor ancak verinin public maç sayfalarından toplandığını söylüyor. Üçüncü taraf kaynak ve hak sahibinin kalıcı arşiv ve yeniden yayına yetkisi doğrulanmadı. Ücretsiz erişim, alttaki hakların doğrulanması olarak değerlendirilmedi. [Veri ve kapsam](https://github.com/nlbair/openplay) · [Yayıncı izni](https://github.com/nlbair/openplay/blob/main/LICENSE)
- **open-matchday** 2026–27 için ücretsiz dosyalar sunuyor. Derleme ticari olmayan kullanımla sınırlı, alttaki sağlayıcı hakları saklı ve yeniden dağıtım için ayrıca kontrol gerektiriyor. Geçmiş iki sezon kapsamı sağlanmıyor. [Lisans ve kaynaklar](https://github.com/oladeeayo/open-matchday)
- **Goaloo / SofaScore** maç sayfası istatistikleri için saklama ve yayınlama izni doğrulanmadı. İstek gönderilmedi. Goaloo ayrıştırıcısı yalnız sentetik, offline fixture üzerinde denendi.

## Bet-app213 incelemesi

Kaynak commit `35a2c1bba013d09177694aea67bf156eb572e07a`. `src/scrapers/goaloo/statistics.py` içindeki maç/lig kimlik denetimi, HTML blokları, sayısal değer doğrulama, ilk yarı korneri ve NULL mantığı offline ayrıştırıcıya uyarlandı. Dış JavaScript çalıştırılmaz ve ağ işlevi yoktur. `src/analytics/match_statistics.py` örneklem bazlı, eksik değerleri dışlayan özet üretir. Pilot bu ilkeyi basit çift-taraf ortalamalarında kullanır, tahmin veya öneri üretmez. `src/jobs/stats_backfill.py` gerçek işi CLI/SQL worker'a yönlendirir, bu PostgreSQL tabanlı kontrol akışı taşınmadı. Üç istenen denetim belgesi incelendi; kırmızı kartın yokluğunu sıfır saymama ve eksik kapsamı açık gösterme korundu. Kaynak deponun canlı servisi veya veritabanına bağlanılmadı.

## Offline sözleşme

`app.statistics.goaloo_offline.parse_statistics` yalnız verilmiş HTML metnini ayrıştırır. `app.statistics.offline_pilot.build_pilot` `season`, `league=de.bl1`, tarih, ev/deplasman eşleşmesi, kaynak maç kimliği ve kaynakta final durumu ister. Unicode NFKC ve büyük/küçük harf normalizasyonu dışında isim tahmini yoktur. Aynı tarihte eşleşme belirsizliği veya yinelenen kayıt reddedilir. Eksik değer NULL, açık sıfır sıfırdır. Ölçümler ve gözlem zamanı ayrı ayrı saklanır.

Offline komut yerel JSON dosyalarını okur, Parquet'i yalnız bellekte üretip geri okur ve hash/manifest planı raporlar. Yerel dosya veya R2 yazmaz. Uygunluk raporu kaynak adı, lisans, atıf ve açık saklama, yeniden yayınlama, ticari izin kanıtı gerektirir; bu girdiler hukuki inceleme yerine geçmez. İzin bulunmadığında BLOCKED durumuna düşer. Yalnızca üretim dışı sentetik fixture için test edilmiştir.

`python -m app.jobs.bundesliga_statistics_pilot --fixtures FIXTURES.json --observations OBSERVATIONS.json --permission PERMISSION.json --season 2024-2025`

İleride onaylı veri sağlandığında ayrı `statistics/bundesliga/season=YYYY-YYYY/` önekinde içerik hash'li Parquet ve manifest planı; kaynak kimliği, lisans, izin referansı, gözlem zamanı, alan kapsamı ve SHA-256 tutulur. Production yazıcısı veya otomatik toplama bu pilotta bulunmaz. Dosya boyutu gerçek uygun kaynakla ölçülmeden tahmin edilemez. Canlı R2 arşivi için izin ve ayrı yazma onayı ile koşullu yazma, yeniden okuma ve idempotent staging provası gerekir.
