# Stage 7I.4 — Kontrollü canlı public GET kanıt toplama

Gün: 22 Kasım 2026. Kaynak: [Draft PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14). Burada production maintenance dispatch, SQL/R2 write, deploy, migration veya merge yapılmadı.

## Yeni araç (operatör açık onayı gerektirir)

`frontend/scripts/stage7i4-live-get-probe.mjs` fixed public HTTPS origin `https://ma-analiz-2.onrender.com` için GET-only, 10 saniye timeout, `redirect=error`, `credentials=omit`, `cache=no-store` ile sınırlandırılmış çağrılar tanımlar. Değişken URL almaz; user-supplied match ID kabul etmez, sadece GET listesinden gelen UUID biçimini doğrular. Yanıtları taşımak yalnız yerel işlemdir, ham yanıtlar loglanmaz.

Operatörün *kendi* güvenilir ağı üzerinde çalıştırabileceği komut:

```bash
cd frontend
npm run probe:read-only
```

Aynı komut doğrudan `node scripts/run-stage7i4-live-probe.mjs --live` ile de çalışır. `--live` verilmeden CLI hiçbir ağ isteği göndermez. Bu süreç GitHub Actions secrets, Render Dashboard veya auth token kullanmaz. Yalnız herkese açık GET endpoint'leri kullanır:

1. `/` — sadece HTTP status
2. `/health/live`
3. `/health/ready`
4. `/api/v1/health`
5. `/api/v1/match-center?limit=1`
6. `/api/v1/match-center?source=history&limit=1`
7. Liste geçerli UUID döndürürse `/api/v1/match-center/{id}`
8. Geçmiş liste geçerli UUID döndürürse `/api/v1/match-center/{history_id}?source=history`

Çıktı **yalnız allowlist**: yedi HTTP status, durum kodları ve fail-closed blocker listesi; maç ID'si, takım isimleri, provider verisi, response body, secret veya SQL bağlantısı çıktı olarak verilmez. Her koşulda `SAFE_TO_DELETE=NO` ve `LIVE_DELETE=NOT_RUN`.

`analysis_verified=false` kasıtlıdır: public API'den model analizinin gerçek doğruluğu bağımsız ölçülmediği için bir site sağlık isteği analiz kanıtı sayılamaz. Operatör raporu olası `ANALYSIS_NOT_VERIFIED` engelini korur. Bu komutun 0 dışında çıkış yapması veya NOT_VERIFIED olması silme/deploy kararı değildir.

## Bu oturumda elde edilen yeni kanıt

- [Doğal read-only collector #45033779477](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45033779477), 2026-11-22 14:36:55 UTC'de oluşturuldu, SUCCESS. Job logu `READ_ONLY_COMPLETE`, `counts.NEW=6`, `UPDATED=0`, `UNCHANGED=0`, `SQL_WRITE=NOT_RUN`, `R2_WRITE=NOT_RUN`, `LIVE_DELETE=NOT_RUN`, `warnings=[]`. Bu altı yeni **gözlem** demektir, PostgreSQL/R2'ye altı maç yazıldığı demek değildir. Artifact `match-collector-45033779477-1` üretildi.
- Önceki canlı Render GET denemeleri bu yürütme ortamında erişilemedi. Bu oturumdaki public API istekleri de araç katmanında erişim hatası verdi; HTTP status veya dependency state çıkarılamadı. Bu nedenle **live Match Center, health ve analysis NOT_VERIFIED**.

## CI ve sınır

`frontend/scripts/test-stage7i4-live-get-probe.mjs` ağsız sentetik testlerde fixed origin, GET, no credentials/redirect, hata sınıflandırma, geçersiz ID'nin URL'ye konmaması, 503 ve boş listelerde fail-closed politikası denetlenir. `frontend/package.json` npm test'i bu yeni testleri içerir. CI iş bağlantısı ayrıca kontrol edilmeli. Bunlar canlı veriye erişimi veya gerçek tahmin doğruluğunu kanıtlamaz.

## Karar

- Operatör public GET probe: **Hazır / explicit opt-in**, bu çalışma sırasında otomatik çalıştırılmadı.
- Gerçek servis/API SQL/R2/Match Center/analiz: **NOT_VERIFIED**, operasyonel GO yok.
- `SAFE_TO_DELETE=NO`, `LIVE_DELETE=NOT_RUN`, production SQL/R2 write/deploy/dispatch/merge **NOT_RUN**.
