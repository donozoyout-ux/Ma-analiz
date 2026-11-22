# Stage 7I.2 — Canlı servisin salt okunur erişim ve veri sağlığı kanıtı

**Gözlem günü:** 22 Kasım 2026. **Kapsam:** eski, tamamlanmış GitHub Actions logları + bu değerlendirme ortamından izinsiz/veri değiştirmeyen HTTPS GET denemesi + GitHub main kod sözleşmeleri. Gerçek production SQL/R2 bağlantısı açılmadı, Render yapılandırması değiştirilmedi. Bu doküman tek başına deployment onayı değildir.

## Doğrudan kanıtlar

- [GitHub keepalive #45004714396](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45004714396): 2026-11-22 08:51:02 UTC logu `Render liveness HTTP 200 total_seconds=0.476966 connect_seconds=0.051233` gösteriyor. Bu **o andaki süreç/HTTP liveness** kanıtıdır; bugünün sürekli erişimi veya PostgreSQL/R2/provider/Match Center hazır olma kanıtı değildir.
- [Read-only match collector #44961084735](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44961084735): workflow SUCCESS, ancak rapor artifact'ının içerik sonucu burada doğrulanmadı; `SQL_WRITE=NOT_RUN` politikasıyla üretim ingest olarak sayılamaz.
- [PR #14 CI #45017512299](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45017512299): sentetik/offline 763 backend testi ve 15 izole PostgreSQL testi başarılıydı; canlı bağlantı testi veya production migration kanıtı değil.
- Bu inceleme ortamından `https://ma-analiz-2.onrender.com/`, `/health/live`, `/health/ready`, `/api/v1/health` için doğrudan HTTPS erişim denenmiştir. Web araçları isteklere erişemedi; container tarafında host DNS çözülemedi. **LIVE_ENDPOINTS=NOT_VERIFIED**. Bu hata servis kesin kapalı demek değildir; teşhis için güvenilir external egress/DNS gerekir. Bu denemelerde SQL/R2 write veya API POST yoktur.

## Sözleşmeler ve sınırlamalar

| Yüzey | Beklenen davranış | Neyi kanıtlamaz? |
|---|---|---|
| `GET /health/live` | FastAPI `{"status":"ok"}` | PostgreSQL, R2, provider, analiz |
| `GET /health/ready` | Frontend+colocated backend transport, `scope=web_api_transport`, `dependencies=not_checked` | DB/R2/provider |
| `GET /api/v1/health` | `database`, `database_schema`, `provider`, `r2`, `services` alanları | Gerçek arşiv readback, source identity, yedek restore ve tam match akışı |
| `GET /api/v1/match-center?limit=1` | Read-only SQL/R2 Match Center liste; `Cache-Control: no-store` | Boş liste, fonksiyonun tüm sezonlarda veriyle çalıştığını ispatlamaz |
| `GET /api/v1/match-center/{match_id}` | Salt okunur belirli, önceden doğrulanmış UUID detayı | Birleştirilmiş canlı sonuç ve analizin doğrulanması için güvenilir referans gerek |
| `POST /api/v1/system/connections` | Korumalı ayrıntılı diagnostics | Bu aşamada token kullanılmadı; HTTP POST / credential ile deneme **YAPILMADI** |

## Açılış engelleri

1. **Doğrudan canlı erişim:** Güvenilir ve izinli operatör ağından GET ile root, liveness, transport readiness, public dependency health kontrolü. Salt bir keepalive geçmişi NO-GO'yu kaldırmaz.
2. **Database schema/identity:** Yetkili salt okunur audit'te doğru DB fingerprint + Alembic HEAD + `database_schema=READY` ve mümkünse `transaction_read_only=on`. Otomatik migration yok.
3. **Read-only Match Center:** En az bir güvenilir ve tanımlı maç UUID'sinin liste + detay okunması, R2 history cross-check ve gerçek kaynak/analiz niteliği. Boş liste veya success job = production ingest değildir.
4. **Kaynak/analiz:** Read-only collector başarı job'ından bağımsız rapor artifact'ı ve provider erişim/lisans/pilot kanıtı; şüpheli kaynakların quarantine olması. AI modelinin configured olması başarılı tahmin kanıtı değildir.
5. **Storage ve scheduler:** Doğal auto/archive/Watchdog birkaç döngü, run ID/attempt + v2 audit tam eşleşme, taze R2 readback; kapasite. Güvenlik bayraklarının Render ve GitHub ortamında gerçekten kapalı kaldığını doğrula.
6. **Kurtarma:** Production PostgreSQL PITR ve gerçek bağımsız R2 felaket kurtarma hâlâ sentetik testlerle ikame edilemez.

## Karar

```json
{
  "stage": "7I.2",
  "method": "PUBLIC_GET_AND_HISTORICAL_GITHUB_LOGS",
  "historical_liveness": "HTTP_200_AT_2026-11-22T08:51:02Z",
  "current_live_transport": "NOT_VERIFIED",
  "current_live_database_schema": "NOT_VERIFIED",
  "current_live_r2_readback": "NOT_VERIFIED",
  "current_live_match_center": "NOT_VERIFIED",
  "read_only_full_launch": "NO_GO_PENDING_LIVE_EVIDENCE",
  "SAFE_TO_DELETE": "NO",
  "LIVE_DELETE": "NOT_RUN",
  "PRODUCTION_SQL_WRITE": "NOT_RUN",
  "PRODUCTION_R2_WRITE": "NOT_RUN",
  "WORKFLOW_DISPATCH": "NOT_RUN",
  "PR_MERGE": "NOT_RUN"
}
```

**Güvenli manuel operatör kontrolü:** Uygun ağda ve mevcut deploy URL'sinde yalnız `GET` yöntemiyle `/health/live`, `/health/ready`, `/api/v1/health`, `/api/v1/match-center?limit=1` durum kodlarını ve sadece allowlisted durum alanlarını paylaş. Authorization, cookie, tam JSON içindeki özel veri, DATABASE_URL, R2 credentials veya token kopyalama. Production migration/import/DELETE/dispatch/deploy için ayrı açık onay gerekir.
