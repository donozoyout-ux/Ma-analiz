# Stage 7A — kontrollü çok ligli gerçek kaynak pilotu

İncelenen güncel main: `f8f25d0`. İnceleme ve canlı gözlem tarihi **21 Kasım 2026**. Bu aşama bir production ingestion açılışı değildir: salt okunur kaynak kapsamı ve eşleştirme incelemesidir.

## Sonuç ve destek sınırı

- **Bundesliga (`de.bl1`)**: 20 Kasım 2026 günü için OpenLigaDB `bl1 / 4937 / 2026 / hafta 10` ve OpenFootball `de.1` erişilebilir. OpenLigaDB maç `83237`, takım `40` / `65`: FC Bayern München–1. FC Köln, `2026-11-20T19:30:00+00:00`, `matchIsFinished=true`, full-time 1–1. Bu yeni gözlemde **kaynağın final beyanı** doğrulandı. OpenFootball aynı isimli fikstürü veriyor; açık UTC, skor ve bitiş bayrağı yok. Dolayısıyla bu pilot yeni bir çok kaynaklı production FINAL üretmez. Önceki Goaloo `3018869` eşleştirmesi ve verified import kaydı korunur; önceki kanıt güncel Goaloo kontrolü gibi gösterilmez.
- **Premier League (`en.pl`)**: 21 Kasım 2026 için OpenFootball `en.1` ve OpenLigaDB `pl / 5996 / 2026 / hafta 11` fikstür adayları mevcut. OpenLigaDB `86635`: Manchester City–Fulham FC, `12:30 UTC`, takım `4244` / `549`; `86628`: AFC Bournemouth–Nottingham Forest, `15:00 UTC`, takım `2942` / `8793`. İkisi de açıkça bitmemiş; skor yok. Saatler **kaynağın UTC alanında bildirdiği değerlerdir**, bağımsız teyit edilmiş kesin program garantisi değildir. İkinci topluluk ligi `pl1 / 5993 / hafta 11` seçilen güne maç döndürmedi. `pl` ve `pl1` otomatik birleştirilmedi.
- **Süper Lig (`tr.sl`, yalnız pilot etiketi)**: OpenFootball `2026-27/tr.1.json` 404; incelenen OpenLigaDB 2026 kataloğunda güvenle atanabilecek Süper Lig kimliği kurulamadı. **NOT_AVAILABLE**. Başka bir ligin verisiyle doldurulmadı. Bu sonuç tüm internet kaynaklarının yokluğu anlamına gelmez; incelenen kaynaklarla kapsamın doğrulanamadığını gösterir. Üretim lig registry'sine destek eklenmedi.

[Canlı pilot JSON](reports/stage7a-live-pilot.json), [canlı lig kataloğu kanıtı](reports/stage7a-league-discovery.json) ve [kaynak uygunluğu / lisans matrisi](reports/stage7a-source-matrix.json) teslimata dahildir. Matris insan tarafından incelenmiş değerlendirmedir; canlı HTTP yanıtı değildir.

## Komut ve dar kapsam

Mevcut backend bağımlılıklarıyla, proje kökünden:

```powershell
cd backend
python -m app.jobs.multileague_probe
python -m app.jobs.multileague_probe --league en.pl --max-matches 2
```

İkinci komut alternatif tek lig çalışmasıdır; ilk komuttan hemen sonra tekrar çalıştırmayın. OpenLigaDB için çalıştırmalar arasında en az 60 saniye bırakın; bitmiş maçlarda birkaç günlük kontrol yeterlidir. Sabit pilot günleri Bundesliga için `2026-11-20`, İngiltere/Türkiye için `2026-11-21` olarak kodda açıktır. Tarih aralığı, başka sezon, tarama döngüsü veya `--persist` seçeneği **yoktur**. `--max-matches` yalnız 1 veya 2 olabilir.

Kaynaklar gün dosyası yayımlamadığı için her OpenFootball liginden tek sezon dosyası ve OpenLigaDB'den açıkça belirtilmiş tek hafta kaynağı indirilir. Yalnız seçili gün değerlendirilir; en fazla iki örnek/kaynak raporlanır. Tam sezon dosyası diske veya SQL/R2'ye arşivlenmez; diğer günlerin maçları rapora aktarılmaz. Katalog keşfi yalnız metadata isteğidir, geçmiş sezon maç taraması değildir. Hiçbir bulunamayan kaynak için başka tarih/lig taramasına geçilmez.

Çıkış 0 en az bir kaynakta gözlem alınarak pilotun tamamlandığını; 1 hiçbir kaynakta gözlem alınamadığını; 2 geçersiz CLI kapsamını belirtir. `READ_ONLY_PILOT_COMPLETE` tam lig kapsamı, import uygunluğu veya çok kaynaklı sonuç doğrulaması anlamına gelmez. Alan/kaynak hataları JSON içinde ayrıca bulunur.

## Gerçek gözlemler, eksikler ve uyuşmazlıklar

Teslim edilen komutun canlı çalışması **10:10:25–10:12:29 UTC** (13:10–13:12 Türkiye), sekiz GET: iki robots kontrolü ve altı veri kaynağı. İki hostun robots yanıtı 404; bu erişim gözlemidir, kullanım lisansı yerine geçmez. Kaynak URL'si, gözlem zamanı, HTTP kodu, bayt sayısı ve ham yanıt SHA-256 raporda saklıdır. Ön keşif istekleri bundan ayrıdır; katalog raporu kendi zaman/hash kanıtını taşır.

- Bundesliga: kaynak başına bir gözlem, toplam iki; tek maç. OpenFootball'daki eksik skor güncellik açığıdır, 1–1'e karşı farklı bir final skor değildir. Tek kaynağın final beyanı bağımsız mutabakat gibi sunulmaz.
- İngiltere: raporda iki OpenFootball ve iki OpenLigaDB örneği var. Günlük kaynak satırı sayıları **10 ve 7**; bu farklılık çözülmedi. Kaynak dosyalarının program güncelliği eşit kabul edilemez. Sadece iki örnek yayımlandığından tüm günün birebir mutabakatı yapıldığı iddia edilmez.
- İngiltere örnek adları `Nottingham Forest FC` / `Nottingham Forest` gibi farklılıklar içeriyor. `FC` silme, aksan temizleme veya fuzzy eşleştirme yok. OpenFootball'daki Manchester City FC–Fulham FC fikstürünün ön keşifteki `15:00` saat metni ile OpenLigaDB'nin `12:30 UTC` değeri otomatik eşitlenmedi: ilkinde saat dilimi yok. Muhtemel program farkı inceleme gerektirir; kesin UTC çatışması diye etiketlenmedi.
- `comparisons=[]` bağımsız uyuşma kanıtı değildir: karşılaştırılabilir tam isim çifti bulunamamış olabilir. Tam sıralı isimler eşleştiğinde bile `REVIEW_REQUIRED`, eksiksiz UTC/skor alanları çatıştığında `CONFLICT` üretilir; hiçbir aday otomatik kabul edilmez.
- Teknik istatistikler, korner, kart ve oran: seçilen yeniden kullanılabilir kaynaklarda pilotun kabul edebileceği doğrulanmış alanlar **NOT_AVAILABLE**. OpenLigaDB gol olayları veya seyirci alanları şut/kart istatistiğine çevrilmez. Açılış/kapanış oranı, performans yüzdesi veya form değeri üretilmedi.

Kaynak zamanının açık UTC olması yanlış veya eskimiş programı tek başına güvenilir yapmaz. OpenLigaDB'nin `lastUpdateDateTime` alanı ham biçimiyle korunur; saat dilimi varsayılıp taze veri etiketi verilmez. İngiltere örneklerinin son güncellemeleri Ağustos/Eylül'dür. OpenFootball için günlük JSON üretimi, veri bakımının günlük ve doğru olduğu garantisi değildir.

## Lisans ve erişim değerlendirmesi

- [OpenFootball CC0-1.0](https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md): veri için ticari kullanım ve yeniden dağıtım mümkün; CC0 üçüncü taraf marka hakları veya doğruluk garantisi sağlamaz. Kaynak URL'si ve atıf provenance amacıyla yine korunur. Logo indirilmez. Bu lisans eksik Süper Lig dosyasına fiilî kapsam kazandırmaz.
- [OpenLigaDB ODbL-1.0](https://www.openligadb.de/lizenz): ticari yeniden kullanım mümkün; görünür kaynak atfı gerekir. Kamuya açılan değiştirilmiş veritabanlarında ODbL yükümlülükleri ayrıca uygulanmalıdır; salt gösterim ile veritabanı dağıtımı ayrımı korunmalıdır. Logolar/harici görseller bu izne dahil değildir. Veriler topluluk tarafından girilir; editoryal doğrulama ve doğruluk garantisi yoktur.
- [OpenLigaDB erişim şartları](https://www.openligadb.de/nutzungsbedingungen): tanımlayıcı User-Agent, merkezi önbellek, seyrek sorgu, değişiklik kontrolü ve hata sonrası bekleme esastır. Mevcut `EvidenceHTTP` 60 saniyelik OpenLigaDB veri aralığını, robots kontrolünü, 12 GET bütçesini, HTTPS ve yönlendirme yasağını korur. Pilot erişim engeli/429/TLS hatası alan hosta alternatif endpoint deneyerek devam etmez. Otomatik retry yapmaz. `pl` ve `pl1` sadece katalogda görülen açık aday kimlikleridir; benzer isimle lig seçilmez.
- [football-data.co.uk şartları](https://football-data.co.uk/data.php) 21 Kasım yeniden incelendi: özel bireysel kullanım sınırı ve ticari/otomatik AI ürünlerine kısıt nedeniyle CSV indirilmedi. İstatistik ve oran kolonlarının mevcut olması ürünümüzde kullanım izni anlamına gelmez.
- Goaloo: önceki robots/yeniden dağıtım kısıtları devam eden engeldir; bu aşamada yeni Goaloo isteği yok. Önceki final teyidi tarihsel kanıt olarak korunur. Engelli AJAX oran adresleri sorgulanmadı. Sportmonks veya ücretli API kullanılmadı.

## Kimlik modeli ve mevcut altyapının korunması

Stage 1–5 raporları, Stage 6 işlevini sağlayan Actions retention belgeleri, provider registry, `identity.py`, SQL modelleri, verified import, arşiv/lifecycle ve scheduler/watchdog/keepalive workflow'ları incelendi. Bunların davranışı değiştirilmedi.

Yeni adapter modülü ingestion registry'sine kayıtlı değildir. Yerel `observation_key` sağlayıcı/kapsam/native ID üzerinden deterministiktir. OpenFootball native maç ID'si vermediğinden fingerprint açıkça **kaynak ID'si olmayan** gözlem anahtarıdır; saat/program değişirse yeni fingerprint çıkabilir. Bu nedenle production idempotency anahtarı olarak kullanılamaz. Aynı kaynak kimliğinde çelişen satırlar örnek limiti uygulanmadan önce kaynağı reddettirir; aynı satır tekrarı tekilleştirilir. Sağlayıcılar arası kanonik UUID atanmaz.

Mevcut `active_matches` provider/native ID uniqueness, tek `match_results`, zamanlı oran snapshot'ları ve teknik veri tabloları korunur. Bayern–Köln'ün dar kapsamlı incelenmiş alias'ı genişletilmedi. Yeni tablo/migration gerekmiyor. SQL modelleri ve veri depoları bu komut tarafından açılmıyor; mevcut canlı kayıtların bu teslimatta yeniden okunup denetlendiği iddia edilmez.

R2 sezon/lig/UTC gün partition'ları ve değişmez Parquet parçaları, SHA-256/satır/içerik doğrulaması, source identity metadata'sı, ortak advisory lock, audit ve tekrar çalıştırma mekanizması aynen kalır. Güncel plan/hash/snapshot ve bağımlılık kontrolleri atlanmaz. Retention varsayılanı 48 saat; başarısız arşiv zorla silinmez. Auto DELETE varsayılanı false korunur; bu komut ortam bayraklarını değiştirmez ve etkin olsa bile retention çağırmaz. Render Start Command, web servisi, Actions schedule ve keepalive değiştirilmedi.

**SQL_WRITE=NOT_RUN; R2_WRITE=NOT_RUN; LIVE_DELETE=NOT_RUN.** DROP/TRUNCATE, mevcut arşiv silme veya üzerine yazma yapılmadı.

## Otomatik testler

```powershell
cd backend
python -m pytest -q tests/test_multileague_probe.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Yeni testler: **28 geçti**. Güvenli backend paketi: **375 geçti, 14 mevcut deprecation uyarısı**. Yeni test verileri sentetiktir ve dosyada açıkça etiketlidir. MockTransport entegrasyonu GET/robots/403 sonrası host durdurmayı ve raporun gizli hata metni içermemesini sınar; SQL/R2 servisine bağlanmaz. Live integration dosyaları çalıştırılmadı. Canlı pilot ayrı JSON kanıttır; unit test başarısı gerçek veri başarısı gibi sunulmaz. Arayüz değişmediğinden frontend build kapsam dışıdır.

## Stage 7B önerisi

1. Önce Türkiye için ticari yeniden kullanımı açıkça izinli, güncel program ve açık UTC/bitiş alanları sağlayan kaynak bulunmalı. Bulunana kadar Süper Lig kapalı kapsam kalmalı. İngiltere'de `pl` ve `pl1` güncellik ve kimlik incelemesi tamamlanmadan birincil production sağlayıcısı seçilmemeli.
2. Takım ve maç eşleştirmelerini kaynak native ID'leri, sıralı takım kimlikleri, sezon, lig ve tam UTC üzerinden inceleme kaydıyla onayla. Belirsizleri karantinada tut. OpenFootball adlarını/fingerprint'lerini tek başına kalıcı kimlik yapma. Bağımsız güncel UTC/program ve final teyidi olmadan sonuç yayınlama.
3. Ayrı onaylanan günlük kapsamla, tek merkezi collector; lig başına rate limit, jitter, son değişiklik kontrolü, HTTP cache ve backoff kullan. Engellerde dur; ücretli sağlayıcıya otomatik fallback ekleme. Kaynak zamanını, erişim anını, hash'i, lisans ve eşleştirme inceleme sürümünü kaydet.
4. Sonraki aşamada kontrollü import, mevcut SQL idempotency ve lifecycle ortak advisory lock yoluna bağlanmalı. Metadata'ya lisans/provenance ve eşleştirme kanıtı eklenmeli. Teknik metrik ancak izin, tam maç kimliği, periyot, birim, gözlem zamanı ve final tutarlılığı doğrulanırsa alınmalı.
5. R2'ye yeni değişmez Parquet parçaları yaz; eskiyi değiştirme. Erken arşiv için mevcut 15 dakika, retention için saatlik Actions mekanizmasını kullan. SHA-256/satır/tam içerik/source identity/ilişkiler doğrulanmadan temizlik yok. Kaynak hatası veya 48 saat aşımı zorla silme gerekçesi değildir. DELETE açılması bu pilotun parçası değildir.
