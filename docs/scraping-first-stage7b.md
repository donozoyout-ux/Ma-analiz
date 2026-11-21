# Stage 7B â€” gÃ¼venli otomatik maÃ§ collector'Ä±

BaÅŸlangÄ±Ã§: `1ee9c5d` (Stage 7A). Stage 1â€“5 belgeleri, Actions retention/watchdog (Stage 6), kaynak registry/identity, verified import ve lifecycle/arÅŸiv yollarÄ± incelendi. Yeni collector bu yazma yollarÄ±na baÄŸlanmaz. Production SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN ve LIVE_DELETE=NOT_RUN. Collector ortamÄ±nda iki DELETE bayraÄŸÄ± da false; uygulama ayarlarÄ± okunmaz.

## Ã‡alÄ±ÅŸtÄ±rma

Mevcut backend ortamÄ±nda:

```powershell
cd backend
python -m app.jobs.match_collector
```

VarsayÄ±lan gÃ¼n aÃ§Ä±k UTC bugÃ¼ndÃ¼r. `--date YYYY-MM-DD` yalnÄ±z UTC bugÃ¼n veya dÃ¼n olabilir. Tek Ã§alÄ±ÅŸmada her lig iÃ§in aynÄ± tek gÃ¼n deÄŸerlendirilir. BaÅŸka tarih aralÄ±ÄŸÄ±, sezon taramasÄ±, `--persist`, SQL/R2 yazma veya import seÃ§eneÄŸi yoktur. BaÄŸÄ±msÄ±z kÃ¼Ã§Ã¼k kurulum iÃ§in Python 3.12, `httpx>=0.27,<1`, `pytest>=8.3,<10` ve Windows saat dilimleri iÃ§in `tzdata>=2024.1` yeterlidir. Mevcut backend requirements deÄŸiÅŸmedi.

Ã‡Ä±ktÄ±lar varsayÄ±lan olarak gitignore kapsamÄ±ndaki `backend/.collector/` iÃ§indedir: `report.json` gÃ¼venli denetim raporu, `state.json` geÃ§ici HTTP cache/deÄŸiÅŸiklik durumu. `--state-dir` yerel alternatif dizin iÃ§indir. stdout yalnÄ±z sabit durum kodlarÄ±, run ID ve sayÄ±mlar iÃ§erir. Ortam deÄŸiÅŸkenleri, ham exception, traceback ve credential iÃ§eren URL loglanmaz. Raporun `evidence_kind` alanÄ± canlÄ± kaynak ile enjekte test transport'unu ayÄ±rÄ±r.

## AÃ§Ä±k lig yapÄ±landÄ±rmasÄ± ve kapsam

`backend/app/jobs/match_collector.py` iÃ§indeki `LEAGUES` ayrÄ±, aÃ§Ä±k politika taÅŸÄ±r:

- **Bundesliga / de.bl1**: CONTROLLED_PILOT, OpenLigaDB `bl1`, beklenen lig kimliÄŸi `4937`, sezon 2026-2027. [BelgelenmiÅŸ getcurrentgroup](https://api.openligadb.de/swagger/v1/swagger.json) ile yalnÄ±z gÃ¼ncel haftayÄ± belirler; o haftanÄ±n tek kaynaÄŸÄ±nÄ± seÃ§ilen UTC gÃ¼ne sÃ¼zer. OpenFootball `de.1` yardÄ±mcÄ± gÃ¶zlemdir. Hafta 1â€“38 dÄ±ÅŸÄ±nda, yanlÄ±ÅŸ lig/sezon/hafta, eksik UTC veya bozuk kimlik reddedilir. GÃ¼ncel hafta, ertelenmiÅŸ eski hafta maÃ§larÄ±nÄ± iÃ§ermeyebilir; tam gÃ¼nlÃ¼k kapsam iddiasÄ± yoktur. BoÅŸ gÃ¼n baÅŸka haftaya/tarihe geÃ§ilmez.
- **Premier League / en.pl**: QUARANTINE_ONLY. `pl / 5996` ve `pl1 / 5993` ayrÄ± topluluk adaylarÄ± olarak sorgulanÄ±r. Her biri kendi gÃ¼ncel hafta metadata'sÄ±yla deÄŸerlendirilir; birincil production saÄŸlayÄ±cÄ±sÄ± seÃ§ilmez. OpenFootball `en.1` saat metni UTC diye yorumlanmaz; `FC` kaldÄ±rma, aksan silme veya fuzzy isim kabulÃ¼ yoktur. AynÄ± gÃ¼n ve benzer takÄ±m isimleri kesin kimlik teyidi sayÄ±lmaz. BaÄŸÄ±msÄ±z program teyidi bulunmadÄ±ÄŸÄ± sÃ¼rece production_ready=false.
- **TÃ¼rkiye SÃ¼per Lig / tr.sl**: NOT_AVAILABLE. Otomatik veri isteÄŸi yoktur. BaÅŸka ligle doldurulmaz. Yeni araÅŸtÄ±rma bulgularÄ± aÅŸaÄŸÄ±dadÄ±r.

Sezon 2026-2027 ile sÄ±nÄ±rlÄ±dÄ±r; 30 Haziran 2027 sonrasÄ±na otomatik sezon geÃ§iÅŸi yoktur. Kaynaklar gÃ¼n dosyasÄ± yayÄ±mlamadÄ±ÄŸÄ±ndan tek lig-sezon OpenFootball dosyasÄ± ve tek lig-hafta OpenLigaDB kaynaÄŸÄ± okunur; yalnÄ±z seÃ§ilen gÃ¼ne ait en fazla iki Ã¶rnek/kaynak raporlanÄ±r. Tam sezonu iÅŸlemeye veya geÃ§miÅŸ sezonlarÄ± dolaÅŸmaya geÃ§ilmez. Kaynak HTTP dosyalarÄ± kÄ±sa Ã¶mÃ¼rlÃ¼ yerel cache olabilir; bunlar kalÄ±cÄ± R2 sezon arÅŸivi deÄŸildir.

## DoÄŸrulama ve karantina

Her gÃ¶zlem kaynak/lisans/atÄ±f, kaynak URL'si, native maÃ§ ve takÄ±m kimlikleri (yoksa null ve NOT_AVAILABLE nedeni), lig/sezon, aÃ§Ä±k UTC kickoff (yoksa UNKNOWN), kaynak gÃ¼ncellemesinin ham deÄŸeri ve mÃ¼mkÃ¼nse UTC karÅŸÄ±lÄ±ÄŸÄ±, orijinal yanÄ±t gÃ¶zlem zamanÄ±, son kontrol zamanÄ±, kaynak durumu ve ham yanÄ±t SHA-256 iÃ§erir. Saat dilimi olmayan gÃ¼ncelleme zamanÄ± Ã§evrilmez: source_update_time_status=UNKNOWN.

Tek kaynak aÃ§Ä±k bitiÅŸ ve geÃ§erli full-time skor bildirse bile yalnÄ±z `source_final_confirmed` olarak saklanÄ±r; `verified_final_score=null`, `final_confirmed=false` ve `production_ready=false` kalÄ±r. SÃ¼renin geÃ§miÅŸ olmasÄ± veya skor alanÄ±nÄ±n dolu olmasÄ± FINAL Ã¼retmez. Ã–nceden doÄŸrulanan Bayernâ€“KÃ¶ln alias/import kaydÄ± deÄŸiÅŸtirilmez; collector SQL'e baÄŸlanmadÄ±ÄŸÄ±ndan bu kaydÄ±n canlÄ± yeniden denetlendiÄŸi iddia edilmez.

Tam sÄ±ralÄ± isimlerle eÅŸleÅŸen adaylarda iki aÃ§Ä±k UTC saati, skor veya bitiÅŸ beyanÄ± Ã§atÄ±ÅŸÄ±rsa CONFLICT Ã¼retilir. EÅŸleÅŸmeyen isimler otomatik yakÄ±nlaÅŸtÄ±rÄ±lmaz; REVIEW_REQUIRED kalÄ±r. AynÄ± native ID farklÄ± takÄ±mlarla tekrar gÃ¶rÃ¼nÃ¼rse Ã¶nceki kimlik baseline'Ä± deÄŸiÅŸtirilmeden SOURCE_IDENTITY_CHANGED Ã§atÄ±ÅŸmasÄ± korunur. AynÄ± yanÄ±ttaki Ã§eliÅŸkili duplicate ID kaynaÄŸÄ± reddettirir. OpenFootball fingerprint'i native ID veya kanonik maÃ§ UUID'si deÄŸildir; program deÄŸiÅŸikliklerinde yeni fingerprint oluÅŸabilir ve production idempotency iÃ§in kullanÄ±lamaz.

Korner, kart, ÅŸut, oran, tahmin veya baÅŸarÄ± yÃ¼zdesi Ã¼retilmez; bu alanlar NOT_AVAILABLE'dÄ±r. KayÄ±tlarÄ±n tamamÄ± bu aÅŸamada karantinadadÄ±r. JSON raporu bir import kuyruÄŸu veya yazma onayÄ± deÄŸildir.

## Trafik, cache ve tekrar Ã§alÄ±ÅŸtÄ±rma

- Merkezi collector; league matrix paralelliÄŸi yok. Her Ã§alÄ±ÅŸmada toplam en Ã§ok 20 GET, yanÄ±t baÅŸÄ±na 1 MB, veri baÅŸÄ±na en fazla bir retry. API hostunda tÃ¼m ligler arasÄ±nda en az 60 saniye; robots crawl-delay/request-rate daha yavaÅŸsa ona uyulur. Ä°steklere 0â€“2 saniye jitter eklenir.
- Robots her yeni Ã§alÄ±ÅŸmada yeniden kontrol edilir. HTTPS/TLS doÄŸrulamasÄ± aÃ§Ä±k, proxy/env credential kullanÄ±mÄ± kapalÄ±, yÃ¶nlendirme ve query/fragment/userinfo/port iÃ§eren kaynak URL'leri yasaktÄ±r. Ä°steklerde yalnÄ±z tanÄ±mlayÄ±cÄ± User-Agent ve koÅŸullu cache baÅŸlÄ±klarÄ± vardÄ±r.
- 401/403, yÃ¶nlendirme, robots ve TLS/baÄŸlantÄ± engeli hostu durdurur; baÅŸka endpoint ile dolaÅŸÄ±lmaz. 429 hemen retry edilmez: Retry-After ve en az 12 saat cooldown cache durumuna yazÄ±lÄ±r. GeÃ§ici 5xx veya timeout iÃ§in en fazla bir beklemeli retry; uzun Retry-After sonraki Ã§alÄ±ÅŸmaya ertelenir. 403/robots/TLS bloklarÄ± insan incelemesi olmadan otomatik temizlenmez. EriÅŸim koÅŸullarÄ± gerÃ§ekten dÃ¼zeldikten sonra ilgili blok kaydÄ± incelenebilir; bypass/force bayraÄŸÄ± yoktur.
- ETag/Last-Modified varsa koÅŸullu GET kullanÄ±lÄ±r. 304 iÃ§in orijinal gÃ¶vde cache SHA-256 ile doÄŸrulanÄ±r; eski received_at korunur, checked_at yenilenir ve CACHED_CONDITIONAL_REVALIDATION etiketi konur. 304 yeni indirilmiÅŸ gÃ¶vde gibi gÃ¶sterilmez. ETag sunmayan kaynakta tekrar indirme gerekebilir; SHA ve gÃ¶zlem sÃ¼rÃ¼mÃ¼ yine mÃ¼kerrer yeni olay Ã¼retmez.
- Ä°Ã§erik hash'i deÄŸiÅŸikliÄŸi NEW / UPDATED / UNCHANGED olarak raporlanÄ±r. YanÄ±ttaki baÅŸka maÃ§ deÄŸiÅŸti diye aynÄ± maÃ§Ä±n iÃ§erik sÃ¼rÃ¼mÃ¼ deÄŸiÅŸtirilmez. Yerel cache en Ã§ok 16 kaynak/3 MB ve 2000 gÃ¶zlem kimliÄŸiyle sÄ±nÄ±rlÄ±dÄ±r. Cache kaybÄ± tekrar NEW Ã¼retebilir: bu cache kalÄ±cÄ± import ledger'Ä± deÄŸildir.
- AynÄ± yerel state Ã¼zerinde exclusive dosya kilidi, atomic dosya deÄŸiÅŸimi ve Actions concurrency vardÄ±r. Ã‡Ã¶kme yerelde kilit bÄ±rakÄ±rsa Ã§alÄ±ÅŸan sÃ¼reÃ§ olmadÄ±ÄŸÄ± doÄŸrulanÄ±p yerel lock dosyasÄ± ele alÄ±nmalÄ±dÄ±r; zaman aÅŸÄ±mÄ±yla yaÅŸayan kilit Ã§alÄ±nmaz. Actions cache yalnÄ±z state.json iÃ§erir; lock dosyasÄ± restore edilmez.

## Lisans kontrolÃ¼

[OpenLigaDB lisansÄ±](https://www.openligadb.de/lizenz) ODbL 1.0 kapsamÄ±nda atÄ±f ve daÄŸÄ±tÄ±m yÃ¼kÃ¼mlÃ¼lÃ¼kleriyle, [eriÅŸim ÅŸartlarÄ±](https://www.openligadb.de/nutzungsbedingungen) ise merkezi/seyrek/Ã¶nbellekli eriÅŸimle deÄŸerlendirilmiÅŸtir. Topluluk verileri editoryal garanti saÄŸlamaz; logo haklarÄ± dahil deÄŸildir. [OpenFootball CC0](https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md) yeniden kullanÄ±m aÃ§Ä±sÄ±ndan uygundur; gÃ¼ncellik veya program doÄŸruluÄŸu garantisi saÄŸlamaz.

Ä°ncelenen belge parmak izleri `collector_policies.json` iÃ§indedir. Hukuki metin ve baÄŸlantÄ±lar veri isteklerinden Ã¶nce denetlenir. OpenLigaDB HTML'indeki deÄŸiÅŸken Blazor yorum/script alanlarÄ± hukuki metin deÄŸildir; fingerprint yalnÄ±z beklenen hukuki iÃ§erik bÃ¶lÃ¼mÃ¼nÃ¼n gÃ¶rÃ¼nÃ¼r metin ve linklerini kapsar. Ham yanÄ±t SHA ayrÄ±ca raporlanÄ±r. Metin/link deÄŸiÅŸikliÄŸi, bÃ¶lÃ¼mÃ¼n bulunamamasÄ± veya belge eriÅŸim engeli ilgili saÄŸlayÄ±cÄ±yÄ± kapatÄ±r. Hash otomatik yeniden onaylanmaz. Ä°lk inceleme 21 KasÄ±m; inceleme sÃ¼resi 21 AralÄ±k 2026'da dolar ve yeniden deÄŸerlendirme olmadan collector durur. Bu aylÄ±k inceleme, her Ã§alÄ±ÅŸmada kullanÄ±cÄ± onayÄ± istemek deÄŸildir.

## SÃ¼per Lig iÃ§in ek Ã¼cretsiz kaynak araÅŸtÄ±rmasÄ±

- [openfootball/europe](https://github.com/openfootball/europe) CC0 baÅŸlÄ±ÄŸÄ± altÄ±nda TÃ¼rkiye verisi listeler. Bu, Stage 7A'daki bulunamayan baÄŸÄ±msÄ±z turkey deposundan farklÄ±dÄ±r. GÃ¼ncel 2026-27 fikstÃ¼rÃ¼nÃ¼n native kimliÄŸi, aÃ§Ä±k UTC ve bitiÅŸ alanlarÄ± doÄŸrulanmadÄ±; TÃ¼rkiye alt dizini web incelemesinde eriÅŸim kÄ±sÄ±tÄ± verdi. Bu kÄ±sÄ±t baÅŸka adresle aÅŸÄ±lmadÄ±, collector'a adapter eklenmedi. Sonraki inceleme iÃ§in adaydÄ±r.
- [evrimulgen/turkey kaynak listesi](https://github.com/evrimulgen/turkey/blob/master/SOURCES.md) football-data.co.uk ve TFF kÃ¶kenlerini belirtir. Depodaki CC0 etiketi upstream kullanÄ±m haklarÄ±nÄ± tek baÅŸÄ±na Ã§Ã¶zmez; gÃ¼ncel sezon/program teyidi de yapÄ±lmadÄ±. Tarihsel CSV indirilmedi.
- [open-matchday kullanÄ±m ÅŸartlarÄ±](https://github.com/oladeeayo/open-matchday#license-and-attribution) ticari feed kullanÄ±mÄ±nÄ± kÄ±sÄ±tlÄ±yor ve upstream haklarÄ±nÄ± devralmadÄ±ÄŸÄ±nÄ± aÃ§Ä±klÄ±yor. GÃ¼ncel SÃ¼per Lig listelese de Ã¼rÃ¼nÃ¼mÃ¼z iÃ§in etkinleÅŸtirilmedi; veri CSV'si alÄ±nmadÄ±.
- football-data.co.uk Ã¼rÃ¼n/otomatik AI kÄ±sÄ±tÄ±, Goaloo'nun Ã¶nceki eriÅŸim/yeniden kullanÄ±m engelleri korunur. Sportmonks, Ã¼cretli servis veya otomatik fallback yoktur.

## GitHub Actions

Yeni `.github/workflows/match-collector.yml` ayrÄ± read-only workflow'dur. UTC **06:19 ve 18:19** (TÃ¼rkiye 09:19 / 21:19); manual dispatch ve collector kodu main'e push edildiÄŸinde de Ã§alÄ±ÅŸÄ±r. GÃ¼nde iki kontrol dÃ¼ÅŸÃ¼k maliyetli program/bitiÅŸ gÃ¶zlemidir; canlÄ± skor gÃ¼ncelleme hizmeti deÄŸildir. Kurulum/test sonrasÄ± collector baÅŸlar. Secrets aktarÄ±lmaz; DATABASE_URL/R2 deÄŸiÅŸkenleri yoktur. Checkout credentials kalÄ±cÄ±laÅŸtÄ±rÄ±lmaz; dÄ±ÅŸ action'lar doÄŸrulanmÄ±ÅŸ commit SHA'larÄ±na sabittir. Standard ubuntu-latest, tek job ve 15 dakika timeout kullanÄ±lÄ±r.

Kendi concurrency grubu retention grubunu kullanmaz; Ã§Ã¼nkÃ¼ SQL/R2'ye eriÅŸimi yoktur. Retention scheduler/watchdog, keepalive, Render web ve Start Command deÄŸiÅŸmedi. Bu, production advisory lock alÄ±ndÄ±ÄŸÄ± anlamÄ±na gelmez: Stage 7C importu mevcut ortak lifecycle advisory lock ve SQL transaction/audit yolundan geÃ§mek zorundadÄ±r.

Rapor run_id ile stdout ve yedi gÃ¼nlÃ¼k Actions artifact'Ä±nda izlenir. YalnÄ±z report.json yayÄ±mlanÄ±r; state/cache, HTML gÃ¶vdeleri, .env veya secret dosyalarÄ± artifact'a alÄ±nmaz. Kaynak hatasÄ± veya veri Ã§atÄ±ÅŸmasÄ± nonzero exit ve Actions hata bildirimi Ã¼retir; beklenen REVIEW_REQUIRED tek baÅŸÄ±na job hatasÄ± deÄŸildir. Setup/test aÅŸamasÄ± collector'dan Ã¶nce durursa sabit kodlu NOT_RUN raporu Ã¼retilir. Runner kaybÄ±/sert iptalde artifact garantisi yoktur.

[GitHub schedule aÃ§Ä±klamasÄ±](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): tetikler gecikebilir veya atlanabilir; public depoda 60 gÃ¼nlÃ¼k hareketsizlik schedule'Ä± kapatabilir. Son gÃ¶rÃ¼len tamamlanmÄ±ÅŸ Ã§alÄ±ÅŸma ile 18 saatten fazla fark varsa sonraki collector raporu uyarÄ±r. Cache eviction/reset bu geÃ§miÅŸi kaybettirebilir. Collector hiÃ§ Ã§alÄ±ÅŸmazsa kendisi alarm Ã¼retemez; Actions hata bildirimlerini aÃ§mak ve baÄŸÄ±msÄ±z bir schedule monitor kullanmak gerekir. Retention watchdog collector saÄŸlÄ±ÄŸÄ±nÄ± izlemiÅŸ gibi sunulmaz. KaÃ§Ä±rÄ±lan gÃ¼nler topluca geri taranmaz.

[GitHub Ã¼cretlendirmesi](https://docs.github.com/en/billing/concepts/product-billing/github-actions): public depoda standart runner hesaplamasÄ± Ã¼cretsizdir; cache/artifact ve private repo kurallarÄ± ayrÄ± deÄŸerlendirilmelidir. KÃ¼Ã§Ã¼k cache ve yedi gÃ¼nlÃ¼k artifact saklama bÃ¼yÃ¼meyi sÄ±nÄ±rlar; Ã¼cretli background worker gerekmez.

## Testler ve canlÄ± gÃ¶zlemler

Test komutlarÄ±:

```powershell
cd backend
python -m pytest -q tests/test_match_collector.py tests/test_multileague_probe.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Yeni collector testleri: **38 geçti**; Stage 7A ile odaklı paket **66 geçti**. Tüm güvenli backend paketi **413 geçti, 14 mevcut deprecation uyarısı**. Temiz küçük bağımlılık ortamı da denendi. Testler sentetik MockTransport kullanır; gerçek SQL/R2 veya production final teyidi değildir. Sınananlar: duplicate/kimlik değişimi, belirsiz UTC, skor/program çatışması, koşullu cache/checksum, rate limit/jitter, 403/429/TLS duruşu, Retry-After/backoff, lisans metni/link/yapı değişimi, kilit/atomic state, hassas hata metni koruması ve production modüllerinin import edilmemesi.

[Gerçek salt okunur collector raporu](reports/stage7b-live-collector.json): **READ_ONLY_COMPLETE**, 21 Kasım 2026 UTC günü. **14 GET**; Bu ilk gözlemde değişken jitter nedeniyle en kısa istek aralığı 58,36 saniye ölçüldü; collector artık rate-limit zaman damgasını jitter sonrasında kaydediyor. Farklı jitter değerli regresyon testi tam 60 saniyeyi zorunlu kılar. Düzeltilmiş sürümün Actions canlı kontrolü teslimat sırasında ayrıca denetlenecek. Bundesliga ve İngiltere için toplam sekiz kaynak gözlemi: **NEW=4, UPDATED=0, UNCHANGED=4**. UNCHANGED kayıtları önceki yerel cache gözlemleriyle aynıdır; bağımsız doğrulama sayılmaz. Kaynak hatası yok; tüm gözlemler REVIEW_REQUIRED, production_ready=false. Süper Lig NOT_AVAILABLE. OpenFootball saatleri belirsiz ve İngiltere pl/pl1 seçimi çözümsüz kalır. Bu başarı collector çalışmasının başarısıdır, production maç/sonuç doğrulaması değildir. SQL_WRITE=R2_WRITE=LIVE_DELETE=NOT_RUN. Actions üzerinde doğrulama durumu ayrıca teslimat mesajında belirtilir.

## Stage 7C'ye kalan engeller

1. Premier League `pl/pl1` sahipliÄŸi ve gÃ¼ncelliÄŸi, tam native takÄ±m eÅŸleÅŸtirmeleri ve baÄŸÄ±msÄ±z aÃ§Ä±k UTC teyidi; SÃ¼per Lig iÃ§in gÃ¼ncel ve kullanÄ±mÄ± izinli kaynak.
2. GÃ¼ncel, baÄŸÄ±msÄ±z sonuÃ§ teyidi ve incelenmiÅŸ source identity eÅŸleÅŸtirmesi. Tek kaynak finali veya eski Stage 2 kanÄ±tÄ± otomatik izin deÄŸildir.
3. Her aday iÃ§in lisans/provenance ve snapshot tazeliÄŸiyle kontrollÃ¼ import onayÄ±. Bu JSON'u doÄŸrudan generic SQL insert'e baÄŸlamayÄ±n.
4. Importu mevcut verified import/journal/source identity kurallarÄ±na, ortak advisory lock ve idempotency kontrollerine entegre etmek; mevcut Bayernâ€“KÃ¶ln UUID/alias'Ä±nÄ± korumak. Cache yerine kalÄ±cÄ± metadata ledger kullanmak.
5. Mevcut deÄŸiÅŸmez sezon/lig/UTC gÃ¼n Parquet parÃ§alarÄ±yla R2 yazma, SHA-256/satÄ±r/tam iÃ§erik doÄŸrulamasÄ± ve SQL/R2 kÄ±smi hata recovery testleri. Bu aÅŸamada hiÃ§biri canlÄ± Ã§aÄŸrÄ±lmadÄ±.
6. 48 saat temizlikte mevcut gÃ¼ncel plan/hash/snapshot, VERIFIED ledger, kaynak kimliÄŸi ve baÄŸÄ±mlÄ±lÄ±k kontrolleri aynen uygulanmalÄ±. Bu collector'a ayrÄ± silme yolu eklenmemeli; AUTO_RETENTION_DELETE_ENABLED=false kalÄ±r. Migration, DROP/TRUNCATE veya geÃ§miÅŸ veriyi deÄŸiÅŸtirme/silme yoktur.
