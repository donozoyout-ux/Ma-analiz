# Stage 7F — Maç detayını paylaşma (read-only UI)

Bu aşama yalnız maç detay sayfasının kullanıcı etkileşimlerini geliştirir.

- Doğrulanmış bir maç detayı yüklendiğinde **Bağlantıyı kopyala** ve **Paylaş** butonları görünür.
- Panoya her zaman tarayıcının **mevcut sayfa URL'si** yazılır; `?source=history` korunur.
- Paylaş destekleyen tarayıcılarda işletim sistemi paylaşım penceresi kullanılır.
- Native paylaşım desteklemeyen tarayıcılarda bağlantı panoya kopyalanmaya çalışılır.
- Kullanıcının paylaşımı iptal etmesi hata diye gösterilmez; panoya erişim reddedilirse adres çubuğundan kopyalama yönlendirmesi sunulur.
- Paylaşım sonucunu bildiren metin `role=status` ve `aria-live=polite` ile erişilebilirdir.
- Mobil düğme yerleşimi 620px altında iki sütundur. Odak ve reduced-motion özellikleri mevcut katmandan korunur.
- Skor/analiz/istatistik verisi değiştirilmez. Yeni API veya runtime bağımlılığı yoktur.

## Doğrulama
```bash
cd frontend
npm test
npm run typecheck
npm run build
```

## PR #9 tamamlama doğrulaması — 22 Kasım 2026 (TSİ)

- `npm test`: **34 passed** (8 yeni paylaşım testi dahil).
- `npm run typecheck`: PASS; `npm run build`: PASS.
- Chrome headless, yerel standalone production build: **1440px ve 390px**, hem aktif hem `?source=history` maç detayında PASS.
- Gerçek tarayıcı clipboard yazma/okumasında mevcut URL birebir korundu. Tarihsel API isteğinde `source=history` doğrulandı.
- Web Share API yokluğu, `canShare=false`, clipboard izin reddi, native share başarısı/izin reddi/iptali kontrollü browser API stub'larıyla test edildi. İşletim sistemi native paylaşım penceresi otomatik test edilmedi; bunlar gerçek cihaz paylaşım kanıtı değildir.
- Paylaşım iptali önceki başarı mesajını temizler; iptal veya izin reddinde sessiz clipboard kopyası yapılmaz. Bekleyen işlemde iki buton disabled; tamamlanınca tekrar kullanılabilir. Tab focus ve yatay taşma kontrolleri PASS.
- Test verisi, Stage 7E'de daha önce doğrulanmış gerçek SQL/R2 readback snapshot'ıdır. Browser testinde yalnız `/api/v1/*` GET yanıtları yerel fixture olarak karşılanır; canlı SQL/R2 bağlantısı kurulmaz.

Tarayıcı testi yeniden çalıştırma (önce `npm run build`, ardından yerel standalone server, örneğin PORT=3300):

```bash
node scripts/test-match-share-browser.mjs http://127.0.0.1:3300 ../docs/reports/match-share-browser
```

Test scripti yalnız loopback URL kabul eder. Chrome yolu `CHROME_PATH` ile verilebilir. [JSON sonuçları](reports/match-share-browser/report.json), [masaüstü tarihsel paylaşım](reports/match-share-browser/1440-history-copy.png), [mobil tarihsel paylaşım](reports/match-share-browser/390-history-copy.png).

PR #10'un frontend CI workflow'u bu dala alındı; GitHub'da bu dalın son commit'i için test/typecheck/build sonucu ayrıca kontrol edilir. Render ayarı değiştirilmez ve deploy komutu verilmez.

**Not:** Bu değişiklik doğrulanmış maç verisinin yeniden doğrulanması veya canlı Render deploy kanıtı değildir. Veri servisleri salt okunur kullanılır. SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN. Otomatik DELETE bayrakları değiştirilmedi.
