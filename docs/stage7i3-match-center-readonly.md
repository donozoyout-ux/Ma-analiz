# Stage 7I.3 — Canlı Match Center ve analiz: salt okunur kanıt sözleşmesi

22 Kasım 2026. Bu aşamada **canlı Render verisine bağlandığımız iddia edilmez**. GitHub üzerinden doğrulanan API sözleşmesi ve gerçek üretim sırrı gerektirmeyen sentetik/offline frontend testleri tanımlanır. [Stage 7I.2](stage7i2-live-readonly-evidence.md) canlı GET erişimini NOT_VERIFIED olarak bırakmıştı.

## Yeni test-only kod

- `frontend/scripts/stage7i3-readonly-contract.mjs`: Ağ, SQL, R2 bağlantısı veya mutation yapmayan saf `assessLiveReadOnlyEvidence(snapshot)` fonksiyonu.
- `frontend/scripts/test-stage7i3-readonly-contract.mjs`: Sentetik taşıma + API health + SQL/R2 Match Center liste/detay sözleşmesini fail-closed sınar. `npm test` içinde CI'da çalışır.
- Tüm kontroller başarısız olsa veya başarılı gözükse de rapor **SAFE_TO_DELETE=NO, LIVE_DELETE=NOT_RUN** döner.

## Kanıt katmanları ve sınırlar

1. **İstek ulaşımı**: `GET /` ve `GET /health/live` 200, `status=ok`. Bunlar PostgreSQL veya futbol verisi kanıtı değildir.
2. **Web/API taşıma**: `GET /health/ready` 200, `scope=web_api_transport`, `dependencies=not_checked`. Bu aşamada frontend/backend proxy varlığını gösterir.
3. **Veritabanı/R2 özet sağlığı**: `GET /api/v1/health` 200, `status=ok`, `database=CONNECTED`, `database_schema=READY`, `r2=CONNECTED`. Bu, gerçek kurtarma/ledger checksum kanıtı değildir.
4. **Match Center liste**: `GET /api/v1/match-center?limit=1`. `items` listesi, `as_of`, `source`, `history_status` ve boş `warnings` kontrol edilir. Boş liste **MATCH_DATA_ABSENT**, kısmi arşiv **MATCH_LIST_NOT_VERIFIED**. Geçersiz UUID, skor veya takım alanları reddedilir.
5. **Detay çapraz kontrolü**: Yukarıdaki *güvenilir* listedeki UUID ile `GET /api/v1/match-center/{uuid}`; aynı ID, takımlar, storage ve geçerli tarih/kısıtlı skor modeli gerekir. 404/503 başarılı sonuç değildir.
6. **R2 geçmiş**: Bağımsız olarak seçilmiş geçmiş UUID için `GET /api/v1/match-center/{uuid}?source=history` sonucunda `storage=R2`, `archive_verified_at` ve valid result gereklidir. SQL-only listeden R2 readback başarısı çıkarılmaz.
7. **Analiz**: `analysis_verified=true` yalnız bağımsız ve geçerli bir analiz ekranı/kanıt kontrolünden gelmelidir. Mevcut kod bunu otomatik üretmez; AI key/configured alanı tek başına kanıt değildir.

**Önemli:** Bu pure classifier, dışarıdan sağlanan ve gerçekliği ayrıca doğrulanmış snapshot'ı sınıflandırır. Rastgele/uydurulmuş JSON ile PASS almak production readiness anlamına gelmez; `READ_ONLY_DOMAIN_EVIDENCE_VERIFIED` bile deployment, tam veri tazeliği, PITR veya DELETE onayı vermez.

## Canlı kontrollerin durumu

Bu aşamadaki `https://ma-analiz-2.onrender.com` GET denemeleri mevcut ağ aracından erişilemedi. Yeni gerçek GET/SQL/R2 doğrulaması **NOT_VERIFIED**. GitHub workflow başarıları veya sentetik testler bu eksikliği gidermez.

### Karar

```json
{
  "stage":"7I.3",
  "offline_evidence_contract":"TESTED_IN_CI_ONLY",
  "live_match_center":"NOT_VERIFIED",
  "live_r2_history":"NOT_VERIFIED",
  "live_analysis":"NOT_VERIFIED",
  "full_launch":"NO_GO_PENDING_LIVE_EVIDENCE",
  "SAFE_TO_DELETE":"NO",
  "LIVE_DELETE":"NOT_RUN",
  "SQL_WRITE":"NOT_RUN",
  "R2_WRITE":"NOT_RUN",
  "WORKFLOW_DISPATCH":"NOT_RUN",
  "PR_MERGE":"NOT_RUN"
}
```

Yayına geçiş, üretim verilerine yazma, migration, repo workflow dispatch ve silme etkinleştirmesi için ayrıca kapsamı belirlenmiş operatör onayı gerekir.
