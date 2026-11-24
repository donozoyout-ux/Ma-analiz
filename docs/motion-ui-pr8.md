# Motion UI — PR #8

İncelenen dal `feature/motion-ui-stage7e`, başlangıç `05d1693`; PR başlangıçta draft idi ve test/build henüz çalıştırılmamıştı. Yalnız frontend görünüm katmanı geliştirildi; Stage 7E API, veri şekli, kaynak kimliği, SQL/R2 ve Render ayarları değişmedi.

## Düzenlemeler

- Kartlarda fine-pointer hover için 5px yükselme, yumuşak lime glow; aktif navigasyonda turkuaz kimlik korundu. Mobil/touch cihazlarda hover yükselmesi yok.
- Sidebar göstergesi tek bir aria-hidden rail olarak aktif linkin gerçek konumuna geçer. ResizeObserver, font/layout değişimi ve mobil menü için ölçüm yenilenir; route değişiminde CSS transform ile hareket eder.
- `:focus-visible` seçicisi link/butonun kendisine de uygulanır. Disabled butonlar hareket/glow almaz. Tarayıcı testinin ortaya çıkardığı hover/basma specificity çatışması giderildi.
- Reduced-motion tüm animasyon/transition/scroll hareketini durdurur; konum göstergesi doğru yerde kalır, focus görünürlüğü kaybolmaz. Kalıcı parent transform bırakmamak için giriş animasyonunda fill-mode kaldırıldı.
- Inter (gövde), Space Grotesk (başlık), Bebas Neue (skor/KPI) `next/font/local` ile self-host edilir; istemci Google Fonts'a istek atmaz. Yeni runtime/animasyon bağımlılığı yoktur. Orijinal font dosyaları (~1,03 MiB toplam), OFL 1.1 lisansları ve kaynak/SHA manifest'i `frontend/src/app/fonts` altında korunur. Bebas preload edilmez; font-display swap kullanılır.

## Testler

- `npm test`: **26 passed**.
- `npm run typecheck`: PASS.
- `npm run build`: PASS (Next 16.4.5 production + standalone).
- Chrome headless / yerel production build: **1440px masaüstü, 390px mobil**, sıfır yatay taşma; kart hover/glow, disabled hover, basılı buton scale, gerçek Tab focus, route'lar arasında rail konumu, Space ile mobil menü, Escape sonrası focus iadesi ve touch-hover kontrolü PASS.
- Emulated `prefers-reduced-motion: reduce`: çalışan animasyon sayısı 0, rail transition=0s, hover transform yok; focus korunuyor.
- Üç fontun gerçek yüklenmesi `document.fonts.load` ve Türkçe test metni ile beklenerek doğrulandı. Uyku/zaman varsayımıyla font yüklenmiş sayılmıyor.

```powershell
cd frontend
npm test
npm run typecheck
npm run build
node scripts/test-motion-browser.mjs http://127.0.0.1:3300 ../docs/reports/motion-ui-browser
```

Ekran testi Stage 7E'de gerçek SQL/R2'den doğrulanmış Bayern–Köln 1–1 snapshot'ının yerel read-only fixture'ını kullanır. Production veri okuması/yazması veya yeni canlı maç üretimi değildir. [Makine raporu](reports/motion-ui-browser/report.json), [masaüstü hover](reports/motion-ui-browser/desktop-hover.png), [mobil kart](reports/motion-ui-browser/mobile-card.png), [mobil menü](reports/motion-ui-browser/mobile-menu.png), [klavye focus](reports/motion-ui-browser/keyboard-focus.png), [reduced-motion](reports/motion-ui-browser/reduced-motion.png).

Önceki yerel dört dosyalık motion taslağı, çalışma kaybını önlemek için `preserve local motion draft before PR8 review` adlı stash'te korundu; bu PR'a otomatik taşınmadı. Görev dışı untracked dosyalar değiştirilmedi.

**Production SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN. AUTO_RETENTION_DELETE_ENABLED=false, OPERATIONAL_DELETE_ENABLED=false.** Yeni üretim importu, archive retry, retention komutu, migration veya Render yapılandırması çalıştırılmadı.
