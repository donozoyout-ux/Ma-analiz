# Stage 7I.5 — R2 geçmişinin liste-detay kimlik bağını doğrulama

22 Kasım 2026. [PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14) içinde, yalnız salt-okunur canlı GET gözlemlerini sınıflandıran frontend denetimi sıkılaştırıldı. Production SQL/R2 bağlantısı açılmadı, veri yazılmadı, import/migration/dispatch/deploy/DELETE yapılmadı.

## Risk ve çözüm

Önceki Stage 7I.3/7I.4 sınıflandırıcısı `R2` olduğunu ve geçerli UUID taşıdığını bildiren herhangi bir `archive` yanıtını kabul edebiliyor, fakat o yanıtı `source=history` listesindeki seçilmiş kayda bağlamıyordu. Bağımsız bir R2 yanıtı, geçmiş listeye bağlanmamışken olumlu `R2_HISTORY_NOT_VERIFIED` dışı değerlendirme yaratabilirdi. Yeni politika bunu **fail-closed** reddeder.

Yeni kanıt şartları:
1. `GET /api/v1/match-center?source=history&limit=1` yanıtı HTTP 200, `source=history`, `history_status=VERIFIED`, boş `warnings`, geçerli `as_of` ve bir veya daha fazla R2-etiketli geçerli UUID içermeli.
2. `GET /api/v1/match-center/{id}?source=history` yanıtı, listedeki ilk maçla aynı UUID/takım adları/`start_time` göstermeli; `storage=R2` ve geçerli `archive_verified_at` sağlamalı.
3. `archive_verified_at` geçerli history snapshot `as_of` zamanından sonraya kaydırılmışsa reddedilmeli.
4. History eksik, boş, `PARTIAL`, kimlik/isim çelişkili veya zaman olarak tutarsızsa `R2_HISTORY_NOT_VERIFIED` engeli üretilmeli.

Değişen dosyalar: `frontend/scripts/stage7i3-readonly-contract.mjs` (pure classifier), `frontend/scripts/stage7i4-live-get-probe.mjs` (history liste kanıtını snapshot'ta koruma), `frontend/scripts/test-stage7i3-readonly-contract.mjs` (altı negatif regresyon testi).

Bu kontroller public endpoint kanıtını **bilinçli olarak eksik** tutar: history liste-detay kimlik bağı, PostgreSQL/R2 bağımsız checksum veya production PITR/restore doğrulaması değildir. Gerçek `analysis_verified` hâlâ ayrı operatör değerlendirmesi gerektirir ve public probe bunu `false` bırakır; otomatik production GO/DELETE izni üretmez.

## Yeni gerçek gözlem sınırı

[Read-only collector #45033779477](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45033779477) yalnız kaynak gözlemi: `NEW=6`, `SQL_WRITE=NOT_RUN`, `R2_WRITE=NOT_RUN`, `LIVE_DELETE=NOT_RUN`. **Production Match Center'a altı kayıt persist edildiği anlamına gelmez.** Bu oturumda canlı Render health/Match Center GET erişimi web aracından başarısız: `LIVE_DATA=NOT_VERIFIED`.

**Karar:** `SAFE_TO_DELETE=NO`, `LIVE_DELETE=NOT_RUN`, production deploy / SQL write / R2 write / dispatch / PR merge = `NOT_RUN`. Kullanıcının bilinçli seçimiyle `npm run probe:read-only` canlı GET sonuçlarının yalnız allowlist özetini verir.
