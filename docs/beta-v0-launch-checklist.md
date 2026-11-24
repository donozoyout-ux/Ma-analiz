# Beta v0 — kontrollü ilk yayın kapısı (22 Kasım 2026)

## Kullanıcı kararı ve kapsam

Kullanıcı, tam üretim veri akışı doğrulanmadan **beta sürümüyle mevcut özellikleri test etmeyi** seçti. Bu seçim otomatik SQL silme, production migration, yeni veri importer'ı, provider aktivasyonu veya onaysız PR merge izni değildir.

Beta test kapsamı: ana sayfa; maç listesi ve filtreler; maç detayları; analiz/tahmin/performance ekranlarının **veri varsa gösterim, yoksa açık boş/hata durumu**; doğrulanmış geçmiş maçların R2 liste-detay okuması. Uydurma/placeholder skor, otomatik provider ingest, backfill veya sahte analiz gösterimi yok.

## Bu dalda yapılan değişiklikler

- `frontend/src/components/football-home.tsx`: beta durumu kullanıcılara açık; veri yoksa sonuç/tahmin üretilmediği bildirilir.
- `render.yaml`: `AUTO_RETENTION_DELETE_ENABLED=false` ve `OPERATIONAL_DELETE_ENABLED=false` envVar bildirimleri eklendi. **Gerçek Render Dashboard/env'nin de bunlara eşit olduğunu operatör doğrulamalıdır.** Blueprint declaration, mevcut Dashboard env veya çalışan instance üzerinde otomatik değişiklik yapıldığını kanıtlamaz.
- Mevcut backend SQL/R2, retention concurrency, workflow schedule, migration ve provider ayarlarına dokunulmaz.

## Yayından önce ZORUNLU no-go kontrolü

1. **PR/CI:** PR #14'e son commit için frontend `npm test`, TypeScript, build; backend testleri ve ayrı izole PostgreSQL contract job'u SUCCESS. Draft PR için production retention job SKIPPED. Beklenmeyen migration/cleanup/dangerous diff yok.
2. **Render:** Yetkili operatör Dashboard'da doğru servis `mac-analiz-single`, mevcut ortamda iki delete flag'in gerçekten `false` olduğunu, `Pre-Deploy Command` ile migration/cleanup çalıştırılmadığını, Build/Start Command'ın repo `render.yaml` ile uyumunu doğrular. Secret değerlerini rapora koymaz.
3. **Geri dönüş:** Son başarılı deploy SHA'sı ve Render rollback yöntemi operatörce belirlenir; beta açılmadan önce rollback/kritik günlük yedek operasyonu kontrol edilir.
4. **Read-only health:** `GET /`, `GET /health/live`, `GET /health/ready`, `GET /api/v1/health` sonuçları ve anahtar status alanları incelenir. `/health/live` tek başına DB/R2/analiz GO değildir.
5. **Match Center:** `GET /api/v1/match-center?limit=1`, `?source=history&limit=1` ve dönen gerçek UUID detayları yalnız GET ile kontrol edilir. Boş liste beta için kabul edilebilir **ancak** 'veri var' veya 'analiz çalışıyor' demek yasaktır; UI açık empty/error göstermeli. SQL ya da R2 503, temel veri erişimi sağlamıyorsa yayın notunda açıkça belirtilmeli, beklentinin niteliğine göre beta kapsamı daraltılmalı.
6. **Kullanıcı kabul testi:** desktop+mobil navigasyon; ana sayfa, maç filtreleri, analiz/performance boş durumları; arşiv, hata durumu ve yenile butonları. Beta tester'a 'veri eksik olabilir' uyarısı görünür.
7. **Ayrı yayın onayı:** Bu belgede deploy **YAPILMADI**. Operator kontrolü sonrasında PR merge/deploy işlemi ayrıca kararlaştırılır.

## İlk beta kullanımında

- Test süresince otomatik DELETE **kesinlikle kapalı**: `AUTO_RETENTION_DELETE_ENABLED=false`, `OPERATIONAL_DELETE_ENABLED=false`, `SAFE_TO_DELETE=NO`.
- Test edilmemiş özelliklere başarı etiketi verme; analizin doğru çıkması ve gerçek veri içeriği ayrı kanıt ister.
- Beta geri bildirimini URL/ekran/expected vs actual biçiminde topla; credential/DB_URL/özel kullanıcı verisini paylaşma.
- **Rollback tetikleri:** login/credential sızıntısı, beklenmeyen write/delete, yanlış takım/skor kaynağı gösterimi, kimlik çakışması, sürekli 500/503, istemsiz migration veya kritik UI erişim kaybı.

## Karar

```json
{
  "release_kind": "LIMITED_BETA_PREPARATION",
  "current_live_deploy": "NOT_PERFORMED",
  "beta_ui_notice": "IMPLEMENTED_IN_DRAFT_PR",
  "repo_render_delete_flags": "EXPLICIT_FALSE_IN_DRAFT_PR",
  "actual_render_delete_flags": "NOT_VERIFIED",
  "live_match_center": "NOT_VERIFIED",
  "production_sql_write": "NOT_RUN",
  "production_r2_write": "NOT_RUN",
  "production_migration": "NOT_RUN",
  "production_delete": "NOT_RUN",
  "SAFE_TO_DELETE": "NO",
  "PR_MERGE": "NOT_RUN"
}
```
