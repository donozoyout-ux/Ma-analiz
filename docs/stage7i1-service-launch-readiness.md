# Stage 7I.1 — Silme kapalı servis açılışı için read-only deploy değerlendirmesi

Gözlem: 22 Kasım 2026. İnceleme GitHub **main** çalışma/deploy tanımları ve [draft PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14) Stage 7H CI kanıtları ile sınırlı. Bu rapor production ortamına bağlanma, deployment veya migration/SQL/R2 yazısı içermiyor.

## Kararlar

- **Tek web servisinin source-level başlatma yapısı**: MEVCUT — ancak canlı deploy/servis sağlığı **NOT_VERIFIED**.
- **Salt-okunur futbol verisi toplama**: source-level iş mevcut; kanıtlı çalışma, kurallara uygun erişim ve veri kapsamı ayrı değerlendirilir. Collector **production SQL/R2 writer değildir**.
- **Maç Analiz'in tam veri toplama/analiz pipeline'ını açma**: **NO-GO (kanıt eksik)**. Mevcut kodda işlevin bulunması, canlı veri akışı ve analiz çalıştığı anlamına gelmez.
- **Otomatik SQL DELETE**: **NO-GO, SAFE_TO_DELETE=NO**. Stage 7H sentetik test başarıları / merge asla DELETE onayı değildir.

## Koddaki somut durum

| Katman | Kaynak | Gözlenen sözleşme | Gereken ayrı kanıt |
|---|---|---|---|
| Render tek servis | `render.yaml`, `frontend/scripts/run-single-service.mjs` | FastAPI, sonra Next.js; aynı servis; `BACKEND_PORT=8000`; `healthCheckPath=/health/live` | Canlı /, statik asset, API proxy ve supervised süreç sağlığı |
| Süreç liveness | `backend/app/main.py` | `/health/live` sadece `{"status":"ok"}`; SQL/R2/AI/provider KONTROL ETMEZ | Tek başına deploy GO kriteri olarak kullanılamaz |
| Web/API taşıma readiness | `frontend/src/app/health/ready/route.ts`, `frontend/src/lib/service-readiness.ts` | `/health/ready` sadece colocated web/API transport; `dependencies=not_checked` | PostgreSQL/R2/provider için ayrı /api/v1/health ve korumalı diagnostics |
| API dependency diagnostics | `backend/app/api/routes.py`, `backend/app/api/connections.py` | `/api/v1/health` durum özetleri verir; `database_schema=READY` ayrımı vardır. Ayrıntılı kontrol Bearer token ile korunur | Gerçek Render ortamında kimliği doğrulanmış salt okunur schema, bağlantı ve fingerprint değerlendirmesi; hiçbir secret kaydedilmez |
| Migration | `render.yaml`, `backend/app/jobs/production_readiness.py` | Deploy öncesi migration bilinçli kaldırılmış; Alembic beklenen `0010_active_statistics` | Gerçek DB current head = code head; eski DB'ye yanlışlıkla migration uygulanmaz |
| Read-only collector | `.github/workflows/match-collector.yml`, `backend/app/jobs/match_collector.py` | Günde iki kez gözlem; `SQL_WRITE=NOT_RUN`, `R2_WRITE=NOT_RUN`; quarantine ve kaynak lisans kontrolü | Son gerçek run raporunda `source_errors`, `production_ready` ve pilot kapsam; başarı yeni SQL maçı anlamına gelmez |
| Frontend offline CI | `.github/workflows/frontend-validation.yml`, `frontend/package.json` | `npm test`, typecheck, build; frontend dosya değişiklikleriyle tetiklenir | Mevcut frontend main commit'i için geçerli CI ve canlı sayfa smoke |
| Production API smoke | `frontend/scripts/smoke-production-api.mjs` | `SERVICE_ORIGIN` üzerinden /, /health/live, /api/v1/health JSON sözleşmesi; sadece transport/readability | Bağımlılıkların gerçekten CONNECTED olması, maçı okuyabilme veya gerçek analizin çalışması değil |
| Scheduler | `.github/workflows/retention-scheduler.yml` ve Stage 7H.4 | Doğal archive/auto için ayrı schedule; auto eski saat boşluklarından sonra tekrar görüldü | Ardışık doğal run + v2 audit eşleşmesi + taze R2 readback |
| SQL/R2 recovery | Stage 7H.2 / 7H.3 | PostgreSQL 16 ve iki bağımsız in-memory object-store sentetik restore başarılı | Production PITR ve gerçek R2 offsite kopya doğrulaması |

**Render güvenlik gözlemi:** `render.yaml` içinde `AUTO_RETENTION_DELETE_ENABLED` ve `OPERATIONAL_DELETE_ENABLED` tanımları yok; bu yokluk canlı Dashboard/env durumunun false olduğunu kanıtlamaz. Repo `.env.example` değerleri false olsa da gerçek ortamın iki bayrağı ayrıca **salt okunur** ve sır sızdırmadan doğrulanmalı. Herhangi bir Render env değişikliği burada YAPILMADI.

**Sağlık sınırı:** `render-keepalive.yml` sadece `https://ma-analiz-2.onrender.com/health/live` endpoint'ini ping ediyor. GitHub job success uygulama verilerinin güncel olduğuna, SQL şemasına, arşiv kurtarma kapasitesine veya analiz çıktısına kanıt değildir.

**Kolektör kapsamı:** `match_collector.py` açıkça "no production writer" sözleşmesiyle çalışır. `production_readiness.py` ise Goaloo 36/2025-2026 tek gün legacy backfill için dependency gate; 2026-2027 openligadb/read-only collector yolunun üretim onayı yerine geçmez. Farklı sistemler birbirinin başarısı sayılmamalı.

## Salt okunur açılış kabul kriterleri

1. **Uçtan uca servis erişimi:** Canlı URL için HTTPS GET /, /health/live, /health/ready, /api/v1/health ve read-only Match Center liste/detay çağrılarını sabit zaman aşımıyla kontrol et. Sağlık yüzeylerini ayır: process / transport / dependency / domain. Secret, user row veya connection string yazdırma.
2. **DB kimliği ve Alembic:** Yetkili, salt okunur diagnostics ile beklenen fingerprint ve şema `READY` doğrulansın. `MISSING_TABLES`, unknown revision veya mismatch olursa açılış STOP; otomatik migration uygulama.
3. **Gerçek veri akışı:** Güncel ve izinli kaynak pilotu, provider attribution/licence ve Match Center en az bir doğrulanabilir aktif/tarihsel sonuçla kontrol edilsin. `DRY_RUN`, `SKIPPED` veya collector observation tek başına SQL/R2 ingest kanıtı değildir.
4. **Analiz ve UI:** Canlı dashboard'da kaynaklı skor/analiz, boş/veri yok state'leri, tahmin geçmişi, hata/503 fail-closed, responsive doğrulama. Yeni kullanıcı verisi veya toplu import olmadan.
5. **Safety:** Canlı Render + Actions ortamı için her iki delete flag false; `LIVE_DELETE=NOT_RUN`; production SQL write/R2 PUT/DELETE, migration, dispatch, env/redeploy çalıştırılmaz. Gerçek pipeline açılacaksa değişiklik kapsamı ve ayrı izin belirlenir.
6. **Operasyonel yedek:** PostgreSQL PITR/backup restore ve gerçek R2 bağımsız kurtarma ispatı tamamlanmadan production DELETE enable edilmez; PR merge bir izin değildir.

## Bu incelemede doğrulanamayanlar

- Render Dashboard'da gerçek environment flag/konfigürasyon durumu, güncel deploy SHA ve instance health: **NOT_VERIFIED**.
- Canlı Render endpoint'lerine bu inceleme ortamından doğrudan istekler erişilebilir değildi: **NOT_VERIFIED** (uygulamanın kesin kapalı olduğu sonucunu vermez).
- Canlı PostgreSQL schema `READY`, R2 readback, provider lisans/erişim, analiz ve gerçek tarihsel UI: **NOT_VERIFIED**.
- Yeni archive/auto/watchdog döngülerinde uzun vadeli v2 schedule evidence: **NOT_VERIFIED**.

### Son karar

```json
{
  "stage": "7I.1",
  "review_kind": "SOURCE_AND_PUBLIC_EVIDENCE_ONLY",
  "service_transport": "NOT_VERIFIED_LIVE",
  "database_schema": "NOT_VERIFIED_LIVE",
  "provider_ingestion": "NOT_VERIFIED_LIVE",
  "match_center_readback": "NOT_VERIFIED_LIVE",
  "full_application_launch": "NO_GO_UNTIL_LIVE_READ_ONLY_EVIDENCE",
  "production_sql_cleanup": "NO_GO",
  "SAFE_TO_DELETE": "NO",
  "LIVE_DELETE": "NOT_RUN",
  "PRODUCTION_SQL_WRITE": "NOT_RUN",
  "PRODUCTION_R2_WRITE": "NOT_RUN",
  "PRODUCTION_DEPLOY": "NOT_RUN",
  "PRODUCTION_MIGRATION": "NOT_RUN",
  "PR_MERGE": "NOT_RUN"
}
```

Bu dosya yalnız kaynak kod analizi ve geçmiş GitHub iş kanıtlarını açıklar; runtime'ı veya ortamdaki credential'ları değiştirmez.
