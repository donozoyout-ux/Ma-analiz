# Connection-check CI saat bağımlılığı

İncelenen başarısız run: [44937507082](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44937507082), validate job **136947265651**. Log doğrulandı: `test_endpoint_rate_limit_and_no_cache`, beklenen 200/alınan 429; **1 failed, 588 passed, 14 warnings**. Retention job'u test hatası yüzünden skipped idi.

Mevcut `_last_check=0` başlangıcıyla `time.monotonic()=5.0` enjekte edilerek **orijinal test aynı 429 != 200 hatasıyla yeniden üretildi**. `time.monotonic()` referansı makineye bağlıdır; fixture gerçek saatin en az 30 olduğunu varsayıyordu. CI run'ında gerçek saat değeri loglanmadığından oradaki sayısal değer kesin bilinmez; bu koşulun aynı hatayı ürettiği deterministik olarak kanıtlandı.

`backend/app/api/connections.py` **değiştirilmedi**. Production'ın 30 saniye kuralı, `_last_check` başlangıcı, nonblocking lock, token doğrulaması ve no-store davranışı aynıdır. İlk monotonic değer 30'un altındaysa mevcut 429 başlangıç davranışı da korunur; test için güvenlik gevşetilmez.

Fixture yalnız endpoint modülünün `time` namespace'ini kontrollü saate çevirir; process genelindeki `time.monotonic`, asyncio ve TestClient saatleri değiştirilmez. Varsayılan test saati 1000'dir. Sekiz yeni parametrik vaka:

- Son kabul edilen istekten 0 / 29 / 29,999 saniye sonra 429; DB/R2 callback'leri yeniden çağrılmaz, `_last_check` ilerlemez, Retry-After=30.
- Tam 30 / 30,001 saniye sonra 200; no-store korunur; ardından aynı saatte yeniden istek 429 olur.
- Başlangıç saati 0 / 5 / 29,999 ve `_last_check=0` iken 429 ve sıfır callback; saat tam 30'a geldiğinde 200.

```powershell
cd backend
python -m pytest -q tests/test_connection_checks.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Tam güvenli yerel paket geçti. Harici/canlı entegrasyon ve görev dışı kullanıcı backup testleri çalıştırılmadı. Mock/stub/SQLite testleri production bağlantısı kurmaz.

SQL yazma yasağı nedeniyle bu push `[tests-only]` işaretlidir. Scheduler workflow'una yalnız push için bu işareti tanıyan koşul eklendi: **validate tam paketi çalıştırır, retention adımı SQL audit dahi yazmadan atlanır**. Schedule ve workflow_dispatch koşulları bundan etkilenmez; archive/auto girişleri, lock/idempotency ve silme bayrakları değişmedi. İşaret verilmezse mevcut push dry-run davranışı sürer. Başarısız eski archive run'ı rerun edilmedi; aksi halde testler geçince gerçek archive adımı başlayabilirdi.

Yeni GitHub Actions test kanıtı main push'undan sonra eklenecek. Archive modunu yeniden denemeye yönelik test engeli giderildi; bu görevde archive/retry/storage preflight başlatılmadı. **Production SQL_WRITE=NOT_RUN, R2_WRITE=NOT_RUN, LIVE_DELETE=NOT_RUN**; mevcut ayrı scheduled bakım işlerinin çalışması bu test görevinin veri yazma yetkisi değildir.
