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

Ek olarak Chrome/Firefox ve mobilde maç detayını açıp panoya kopyalama, geçerli URL, tarihsel `?source=history`, native paylaşım desteği/eksikliği, izin reddi, klavye odağı ve dar ekranda taşma test edilmeli.

**Not:** Bu değişiklik doğrulanmış maç verisinin yeniden doğrulanması veya canlı Render deploy kanıtı değildir. Veri servisleri salt okunur kullanılır. SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN. Otomatik DELETE bayrakları değiştirilmedi.
