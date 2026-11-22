# Stage 7H.5 — Production go/no-go güvenlik değerlendirmesi (read-only)

Gözlem: **22 Kasım 2026**. PR [#14](https://github.com/donozoyout-ux/Ma-analiz/pull/14) commit `da3f1e3` temel alınmıştır. Bu belgenin oluşturulması production SQL/R2 bağlantısı, workflow dispatch, DELETE, archive upload veya Render değişikliği gerektirmez.

## Karar

- **Uygulamanın otomatik SQL DELETE özelliği: NO-GO / SAFE_TO_DELETE=NO.**
- **PR #14: draft, merge edilmez.** PR CI'ın yeşil olması canlı kaynakların güvenilirliği veya operasyonel onay değildir.
- Uygulamanın *silme kapalı* modda çalıştırılması, diğer deploy/read-only readiness kriterlerine göre ayrı değerlendirilebilir; bu belge bir deploy onayı değildir.

## Kaynaklar ve doğrudan kanıt

| Kanıt | Gerçek gözlem | Bu kanıtın ispatlamadığı |
|---|---|---|
| [Stage 7H.4 PR CI #45015445671](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45015445671) | 757 backend passed, 15 PostgreSQL contract passed, production retention job SKIPPED | Production restore/PITR veya doğal scheduled run sürekliliği |
| [Doğal auto #45014551688](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45014551688) | event=schedule; 2026-11-22 10:52:44 UTC, SHA `dae3d305`, success; log: mode=auto, AUTO_RETENTION_DELETE_ENABLED=false, SQL audit RECORDED, storage_status=DRY_RUN, scanned=2, retained=2 (WITHIN_RETENTION_WINDOW), deleted=0, LIVE_DELETE=NOT_RUN, capacity_status=HEALTHY | SQL audit **v2 run_id + attempt** bağımsız eşleşmesi, veri silme, R2 PUT veya saatlik güvenilirlik |
| [Doğal archive #45009618881](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45009618881) | event=schedule; 2026-11-22 09:52:09 UTC, SHA `dae3d305`, success, archive status SKIPPED, v2 audit linked on earlier watchdog | Yeni R2 verisi üretimi ve sonraki döngüler |
| [Doğal watchdog #45011345910](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45011345910) | event=schedule, status failure: AUTO_STALE, AUTO_SQL_AUDIT_STALE, NO_RECENT_SUCCESSFUL_AUTO_SCHEDULE; aynı snapshot R2 readback VERIFIED, capacity HEALTHY, incomplete ledger=0 | Sonraki auto'nun v2 linkage PASS olduğunu veya sorunun kökten çözüldüğünü |
| [Stage 7H.2 test recovery #45011628522](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/45011628522) | 17 tablo, 20 sentetik SQL satırı, 9 sentetik Parquet objesi için pg_dump/pg_restore, FK ve Match Center testleri | Production WAL/PITR ve gerçek R2 felaket kurtarma |
| [Stage 7H.3 PR #14](https://github.com/donozoyout-ux/Ma-analiz/pull/14) | Ayrı in-memory object-store kopyası ve fail-closed kurtarma kontrolleri | Gerçek bağımsız R2 bucket/offsite backup |

**GitHub cron notu:** Workflow `auto` için `:02`, archive için `:07/:22/:37/:52` tanımlı; GitHub run `created_at` nominal cron tick'i değildir. GitHub schedule gecikmesi/kaybının kesin nedeni bu kayıtlardan saptanmadı. `workflow_dispatch`, `push` veya PR run'ları doğal schedule kanıtı olamaz.

## Silmeyi kapalı tutan açık kapılar

1. **Doğal scheduler continuity:** Birkaç ardışık gerçek archive + auto + watchdog döngüsü; event=schedule; taze başarı; `run_id/run_attempt` SQL v2 audit eşleşmesi ve v2 zaman penceresi. GitHub workflow success tek başına onay değildir. Geçmiş `AUTO_STALE` alarmı kayıtlara korunur; tek yeni auto uzun dönem PASS sayılmaz.
2. **Taze, bağımsız read-only storage validation:** `scan_complete=true`, `unknown_dependency_tables=0`, `read_only=on`, source identity ve immutable archive ledger/readback/checksum; protected metadata stable; incomplete ledgers=0; bilinmeyen FK=0; capacity/growth. En son eski validation snapshot'ı güncel kanıt değildir.
3. **Gerçek kurtarma:** Production PostgreSQL için yetkili yedekleme/PITR, şifreli offsite backup, RPO/RTO ve ayrı ortamda test restore; R2 object inventory + bağımsız kopya + restore/readback kanıtı. Source ZIP, SQL veya R2 yedeği değildir. Bu üretim işlemlerine bu belgeyle izin verilmez.
4. **Operator kararı:** Tam onaylanmış plan digest, değişmeden kilit altında pre-delete recheck, gözden geçirilmiş rollback/duraklatma adımları ve ayrı açık kullanıcı onayı. PR merge ayrı karardır; DELETE izni değildir.

## Önerilen güvenli sıra

- Önce **code-only review**: Stage 7H.2/H.3/H.4 değişikliklerini güvenlik kapsamı, veri formatı, migrate/deploy bağımlılıkları ve testlerin sırları kullanmaması yönünden denetle.
- Sonra bağımsız monitor'un schedule sessizliğini yakaladığını ve gerçek doğal run'lardaki v2 audit'i salt okunur doğrula. **Run olmayan yerde run uydurma veya otomatik dispatch ile sahte başarı üretme.**
- Backup/PITR ve R2 bağımsız kurtarma için ayrıntılı değişiklik/izin planı hazırla. Production export veya test restore için ayrı onay al.
- Aşağıdaki karar değişene kadar production DELETE varsayılan olarak kapalı tutulur.

```json
{
  "stage": "7H.5",
  "assessment": "READ_ONLY_GO_NO_GO",
  "sql_cleanup_production": "NO_GO",
  "SAFE_TO_DELETE": "NO",
  "LIVE_DELETE": "NOT_RUN",
  "AUTO_RETENTION_DELETE_ENABLED": false,
  "OPERATIONAL_DELETE_ENABLED": false,
  "PR_MERGE": "NOT_RUN",
  "PRODUCTION_SQL_WRITE": "NOT_RUN",
  "PRODUCTION_R2_WRITE": "NOT_RUN",
  "WORKFLOW_DISPATCH": "NOT_RUN"
}
```
