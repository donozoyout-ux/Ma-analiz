# Stage 7I.6 — Match Center read-only uçtan uca doğrulama

22 Kasım 2026 (TSİ). Görev: [issue #13 / Stage 7I.6](https://github.com/donozoyout-ux/Ma-analiz/issues/13#issuecomment-7082772773). Başlangıç `bd0f24f`; mevcut `feature/stage7h-readiness-gate` / draft PR #14 üzerinde çalışıldı. Yeni PR veya merge yok.

## Sentetik test kanıtı

Bu bölümün tamamı fixture kanıtıdır. Gerçek maç verisi, production credential veya `.env` kullanılmadı. HTTP 200, `CONNECTED` health fixture'ı, `ANALYZED` metni veya CI başarısı canlı veri/provenance/isabet kanıtı değildir.

- Frontend: **134 passed**; 74 yeni senaryo. `npm run typecheck` ve `npm run build` PASS. Next.js uygulaması/API sözleşmesi değiştirilmedi; değişiklikler probe/classifier ve test katmanındadır.
- Backend: izole, boş `.env` ve temiz process environment ile **777 passed, 15 skipped**, 14 mevcut deprecation warning. 14 yeni backend senaryosu dahil. Yerelde atlananlar mevcut PostgreSQL 16 konteyner testleridir; başarı sayılmaz.
- GitHub frontend, backend ve ayrı PostgreSQL job'larının son SHA sonuçları PR açıklamasında linkleriyle doğrulanır. PR'da production `retention` job'u SKIPPED kalmalıdır.

### Gerçek sentetik HTTP/API zinciri

Frontend testi yalnız kendi loopback HTTP fixture sunucusuna ulaşan, açıkça enjekte edilmiş bir test transport'u kullanır. Collector'ın gördüğü URL'ler sabit HTTPS origin ve path'lerdir; fixture adapter bunları yalnız kendi loopback portuna eşler. Production endpoint'e bağlantı kurulmaz.

Backend fixture'ı SQL ve in-memory Parquet'i önceden hazırlar; ardından SQLite `PRAGMA query_only=ON`, commit yasağı ve SELECT-only statement kontrolü altında gerçek FastAPI router'ını okur:

1. `/api/v1/match-center?limit=1` → ilk UUID.
2. Aynı UUID'nin detail'i → aynı takım/tarih/skor, SQL kaydı.
3. `source=history&limit=1` → canonical history UUID.
4. Aynı history UUID'nin `?source=history` detail'i → ledger/index üzerinden R2 fake readback.

Backend'in gerçekten ürettiği dört HTTP yanıtı stdout'a basılmadan stdin ile test-only Node köprüsüne aktarılır. Köprüde global canlı fetch kapalıdır; collector yalnız bu yanıtları standart `Response` stream'leri olarak tekrar okur. Beklenen sonuç **NOT_VERIFIED**, yalnız `ANALYSIS_NOT_VERIFIED` blocker'ı ve `analysis_verified=false` olur. Böylece frontend ile gerçek backend DTO şekli arasında offline entegrasyon kontrolü yapılır; canlı kanıt uydurulmaz.

PR-only validate job'u Node 22'yi açıkça kurar ve köprü opt-in'ini etkinleştirir; Node eksikse test başarısız olur, sessiz skip yapılmaz. Bu adım PR dışındaki scheduler çalışmalarında çalışmaz. Mevcut production retention ve PostgreSQL contract job'ları, cron, delete gate ifadeleri ve concurrency değiştirilmedi.

### Veri ve analiz negatifleri

- Boş liste/veritabanı, bilinmeyen UUID, 401/404/503; PARTIAL history ve warnings.
- Yanlış UUID, takım, kickoff, lig, kaynak kimliği ve skor; Stage 7I.5 history-list/detail bağları korunur.
- Eksik kaynak policy/attribution, desteklenmeyen licence veya licence URL uyuşmazlığı. Bu metadata kontrolüdür; hukuki hakları veya gerçek kaynağın doğruluğunu bağımsız ispatlamaz.
- Gelecekteki arşiv okuma zamanı, final teyidinden önceki readback, naive/geçersiz tarih; eksik/negatif/ondalıklı/boolean skor, kickoff öncesi veya observation sonrası final.
- Bekleyen maçta skor `null` kalabilir; bitmiş maçta eksik skor doğrulanmış final kanıtı değildir. Gerçek fixture 0–0 değeri korunur; boşluk doldurmak için skor üretilmez.
- Eksik/bozuk Parquet ve tutarlı checksum ile yeniden yazılmış yanlış attribution/licence fixture'ı backend'de 503 verir. İki arşivden biri bozuksa liste açıkça PARTIAL/warnings döner.
- Analiz alanlarında yalnız tamamlanmış ve observation zamanına kadar olan summary seçilir. RUNNING/future summary öne geçmez; ham analysis output dışa aktarılmaz. Seçilen model adı ve zamanının eşleşmesi modelin isabeti/provenance'ı değildir.

Başlangıç classifier'ında üç sentetik regression tekrarlandı: detail tarih uyuşmazlığı, detail skor uyuşmazlığı ve eksik archive policy, `READ_ONLY_DOMAIN_EVIDENCE_VERIFIED` verebiliyordu. Yeni classifier hepsini `NOT_VERIFIED` olarak reddeder. Bu deneme yalnız geçici test modülleriyle yapıldı; production probe çalıştırılmadı.

## Probe güvenliği ve kapanan riskler

Önceki kod yanıtı `response.json()` ile bütünüyle okuduktan sonra JSON karakter sayısını ölçüyordu. Bu yaklaşım ağdan okuma/bellek sınırı değildir ve UTF-8 byte sayısını garanti etmez.

Yeni okuyucu sabit **262.144 byte** tampon kullanır. Content-Length yok veya yanlış olsa da stream parça parça ölçülür; sınır aşılırsa parse edilmeden reddedilir. Geniş header da erken reddedilir. UTF-8 decode ve JSON parse ancak byte kontrolünden sonra yapılır; unbounded `response.json()` fallback'i yoktur. Homepage gövdesi parse edilmeden iptal edilir.

Her isteğin header ve body okumasını kapsayan toplam 10 saniyelik deadline'ı vardır. Küçültülmüş timeout yalnız enjekte edilen sentetik testlerde kullanılır; üst sınır büyütülemez. Fetch veya body signal'i dikkate almasa bile deadline race'i sonucu sınırlar. Stream iptali ve timer temizliği yapılır; ham exception mesajı döndürülmez.

- GET-only; sabit `https://ma-analiz-2.onrender.com` origin. Kullanıcı URL, endpoint veya token CLI argümanı kabul edilmez.
- `redirect=error`, `credentials=omit`, no-store; geri dönen redirect/origin/userinfo uyuşmazlığı da reddedilir. Body içindeki linkler takip edilmez.
- Detail path'e yalnız gerçek string, tam 36 karakter ve izinli UUID biçimi girebilir. Sonunda newline/query olan veya coercion ile UUID görünen nesne reddedilir.
- Collector default/global canlı transport'u açık operator opt-in olmadan kullanamaz. CLI, CI/GITHUB_ACTIONS ortamında `--live` verilse bile engellenir. `npm test`, build veya CI workflow'u production probe çağırmaz.
- Testteki operator CLI subprocess'inde dahi global fetch fixture ile önceden değiştirilmiştir; gerçek production GET yapılmaz.
- Public rapor yalnız sabit durum kodları/HTTP sayıları/güvenlik alanlarını içerir; history HTTP durumu da artık özete dahildir. UUID, takım, raw JSON veya secret/token değerleri rapora/stdout/CI artifact'a yazılmaz. Bu durum adversarial sentinel verileriyle sınanır.

## Canlı doğrulanmış veri

**LIVE_MATCH_CENTER=NOT_VERIFIED.** Bu görevde gerçek Render/production endpoint GET'i dahi çalıştırılmadı. Production SQL/R2 bağlantısı ve bağımsız gerçek analiz doğrulaması NOT_RUN.

Collector ve public CLI raporu her zaman `analysis_verified=false`, `analysis=NOT_VERIFIED` üretir. Backend'in `analysis_summary` alanı, health=200 veya response içindeki `analysis_verified=true` bunu değiştiremez. Saf classifier'ın test oracle/operator assertion girdisi, canlı analiz isabetini kendi başına kontrol etmez; public CLI bu assertion'ı hiçbir zaman devralmaz.

Mevcut açık opt-in GET komutu operatör için ayrı kalır; bu görevde veya CI'da yürütülmedi. Bu probe score/policy metadata tutarlılığına bakar; model kalibrasyonu, tahmin isabeti, kaynak gerçekliği, bağımsız lisans teyidi, SQL/R2 disaster recovery veya retention silme onayı sağlamaz. Canlı freshness/RPO/RTO garantisi verilmez.

## Testleri yeniden çalıştırma

```bash
cd frontend
npm test
npm run typecheck
npm run build
```

Backend yalnız credential içermeyen checkout ve boş `.env` ile çalıştırılmalı. Node köprüsü için test ortamında `STAGE7I6_NODE_CONTRACT=true` ve Node 22 gerekir. Mevcut PostgreSQL 16 contract job'u disposable konteyner kullanır; yerel production bağlantısıyla taklit edilmez.

**SAFE_TO_DELETE=NO, LIVE_DELETE=NOT_RUN, AUTO_RETENTION_DELETE_ENABLED=false, OPERATIONAL_DELETE_ENABLED=false.** Production SQL/R2 yazma/DELETE, migration/import, provider ingest, deploy/Render değişikliği, production workflow dispatch veya PR merge yapılmadı. Görev dışı yerel dosyalar korunur.
