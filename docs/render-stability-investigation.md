# Render web kararlılığı incelemesi

İncelenen başlangıç: **6234585 / Stage 7C**. Tarih: **21 Kasım 2026**. Stage 7D geliştirmesine geçilmedi. Canlı adres: https://ma-analiz-2.onrender.com.

## Sonuç ve kanıt sınırı

**Bildirilen kapanmanın kesin sebebi belirlenemedi.** Render Events/Logs ve canlı instance planı okunamadı. Tarayıcıda servis Dashboard sekmesi saptandı ancak araç mevcut browser URL'sini güvenle doğrulayamadığı için UI incelemesini durdurdu; başka yöntemle bu engel aşılmadı. Render API erişim anahtarı/bağlantısı da mevcut değildi. OOM, process crash veya uyku olayı varmış gibi raporlanmaz.

Kodda doğrulanmış iki risk azaltıldı: sync istek havuzu doluyken liveness'ın da beklemesi ve web başlangıcında kullanılmayan native Arrow kütüphanelerinin yüklenmesi. Bunların kullanıcının yaşadığı olayın sebebi olduğu kanıtlanmış değildir. Mevcut supervisor'ın iki child exit'ini birbirine bağladığı gerçek yerel process testleriyle doğrulandı; bu davranış önceden de vardı.

## Canlı HTTP ve keepalive kanıtı

[HTTP gözlem raporu](reports/render-stability-http.json): 21 Kasım **16:40:43 UTC / 19:40 Türkiye** kontrolünde `/` HTTP 200, `/health/live` HTTP 200 ve `{"status":"ok"}` döndürdü. Süreler sırasıyla yaklaşık 0,063 ve 0,187 saniyeydi. Bu anlık erişim kanıtıdır; önceki/sonraki saatlerin uptime kanıtı değildir. `/health/ready` HTTP 404'tü; mevcut sürümde endpoint yoktu, Render konfigürasyonundaki kontrol yolu bu değildi.

[GitHub API/log kanıtı](reports/render-keepalive-evidence.json): workflow active; erişilebilir schedule geçmişi örneğinde **tek çalışma** vardı. [44924627168](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44924627168), event=schedule, conclusion=success. Oluşturulma zamanı `2026-11-21T16:26:54Z`; logda `2026-11-21T16:27:45.5589185Z Render liveness HTTP 200` doğrulandı. Tek örnekten düzenli 10 dakikalık HTTP aralıkları veya kaçırılmış çalışma sayısı hesaplanamaz. Manuel çalışmaları schedule başarısı diye saymadım.

`render-keepalive.yml` UTC `3,13,23,33,43,53 * * * *` cron'u, 120 saniye curl süresi ve üç dakika job timeout'u kullanır. Yalnız public GET yapar; DB/R2 Secret'ı yoktur. Bu düzeltmede zamanlama değişmedi; loga gerçek curl toplam/bağlantı saniyeleri eklendi. Önceki job'un toplam süresi tek başına cold start veya process crash kanıtı değildir.

[GitHub'ın schedule açıklamasına](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) göre çalışmalar gecikebilir/atlanabilir; public depoda 60 günlük hareketsizlik schedule'ı kapatabilir. Bu nedenle ping **kesin 7/24 uptime sağlamaz**. Başarılı tek ping, aradaki 15 dakikalık hareketsizlik boşluklarını dışlamaz.

## Uyku, crash ve health failure nasıl ayrılır?

- **Free idle uyku**: [Render Free belgesindeki](https://render.com/docs/free) 15 dakikalık inbound trafik yokluğu politikası geçerlidir; sonraki istek servisi uyandırabilir ve yaklaşık bir dakika sürebilir. Saat/traffic ve platform olayı birlikte incelenmeli. Uyku kısıtı uygulama koduyla kaldırılmadı.
- **SIGTERM / 143**: deploy, platform restart veya durdurma akışının parçası olabilir. Tek başına uygulama hatası değildir. Supervisor artık aldığı SIGTERM/SIGINT'i açıkça loglar; iki child'a iletir.
- **SIGKILL / 137**: zorla sonlandırmayı gösterir; OOM olasılıklardan biridir. Render'ın bellek limiti aşıldı olayı ve Memory Metrics olmadan “OOM” denemez.
- **Python/Node exit 1 veya traceback**: exception/başlatma hatası adaydır. İlk hata ve takip eden child/supervisor exit satırları birlikte gerekir; son cleanup satırı kök sebep olmayabilir.
- **HTTP 502/503**: o istekte bir proxy/upstream kullanılabilirlik sorunu gösterir; uyku, yeniden başlama, API erişilememe veya timeout arasında tek başına seçim yaptırmaz. Bu incelemede canlı örneklerde 502/503 alınmadı.
- **Health failure**: [Render HTTP health check](https://render.com/docs/health-checks) beş saniye içinde 2xx/3xx bekler; süregelen başarısızlıkta trafiği durdurup instance'ı yeniden başlatabilir. Dashboard'da gerçekten hangi yolun seçildiği doğrulanmalı; yalnız YAML bu ayarın canlıya uygulanmış olduğunu kanıtlamaz.

## Start Command, PORT ve iki process

Repo Start Command **değişmedi**:

```text
node frontend/scripts/run-single-service.mjs
```

`render.yaml`: web runtime python, healthCheckPath `/health/live`, backend port `8000`, plan **1c-2g**. Bu, canlı servisin Free olduğunun kanıtı değildir; Dashboard planı farklı olabilir. Blueprint planı, servis kimliği veya ücretli kaynak değiştirilmedi/oluşturulmadı.

Supervisor önce `python3 -m uvicorn app.main:app --host 127.0.0.1 --port BACKEND_PORT` başlatır. Backend liveness hazır olunca `.next/standalone/server.js`, Render'ın verdiği **PORT** üzerinde **0.0.0.0** ile açılır. Uvicorn için ayrı bir public port açılmaz. PORT/BACKEND_PORT integer ve farklı olmalıdır. [Render public port şartı](https://render.com/docs/web-services#port-binding) bu düzenle karşılanır.

Next `/api/v1/*` ve `/health/live` rewrites'ı build sırasında API target'a bağlanır. Varsayılan target `127.0.0.1:8000` mevcut backend ile uyumludur. BACKEND_PORT özelleştirilecekse build-time API_INTERNAL_URL ve runtime port birlikte doğrulanmalıdır; sadece runtime env'i değiştirmenin bütün rewrite'ları yenilediği varsayılmamalı. Bu görevde port/URL ayarları değiştirilmedi.

FastAPI veya Next.js beklenmedik şekilde kapanırsa supervisor diğerini de durdurur, sıfırdan farklı kodla çıkar. Normal bir child exit 0 bile beklenmedik kapanma kabul edilir. Kod içinde yeni restart döngüsü veya ücretli worker yoktur. Child'ın kapanması ile event-loop'un cevap vermeden asılı kalması farklı durumlardır; asılı kalma dış HTTP kontrolüyle izlenmelidir. Process-yalnız canlılık, veri bağımlılıklarının veya bütün sayfaların çalıştığını tek başına kanıtlamaz.

Normal [Render deploy/shutdown akışında](https://render.com/docs/deploys#graceful-shutdown) SIGTERM beklenir. Supervisor child'lara sinyali iletir, 25 saniye grace sonunda gerekirse SIGKILL uygular; bu mevcut davranış korunur. Render'ın varsayılan 30 saniyelik sonlandırma penceresiyle uyumludur. Linux gerçek graceful SIGTERM bu Windows ortamında doğrudan doğrulanmadı; Windows cleanup sonucu Linux sinyal testi gibi sunulmaz.

## Uygulanan küçük düzeltmeler

1. **FastAPI liveness async oldu.** DB/provider/R2 kontrolü yapmadan mevcut `{"status":"ok"}` sözleşmesini korur. Dolu sync thread havuzunu beklemez. Regresyon testi havuzun tek token'ını bloke edip liveness'ın hâlâ yanıtladığını denetler. Bu sentetik bir yük testi; production'da havuzun gerçekten dolduğu iddia edilmiyor.
2. **Arrow lazy import**: `HistoricalRepository` yalnız gerçek Parquet okurken `pyarrow.parquet` yükler. SHA-256, satır sayısı, içerik, historical cutoff ve R2 formatı değişmedi. Arşiv okunduğunda bellek maliyeti geri gelebilir; OOM sorunu tamamen çözüldü denemez.
3. **Soğuk başlangıç toleransı**: backend startup beklemesi 30'dan 90 saniyeye çıktı; mevcut BACKEND_STARTUP_TIMEOUT_MS override'ı korunur. Bekleme hâlâ sınırlıdır. Yavaş CPU başlangıcına pay bırakır; platformun uyumasını önlemez.
4. **Güvenli process logları**: child adı/PID ve parent sinyali yazılır; credential/env/DB adresi loglanmaz. Runtime bellek limitine kanıtsız heap cap veya restart döngüsü eklenmedi.
5. **Yardımcı `/health/ready`**: Next route'u gerçek colocated FastAPI `/health/live` isteğini iki saniyeyle sınırlar; transport başarısızsa 503, başarılıysa 200 verir. Cache kapalıdır. JSON `scope=web_api_transport`, `dependencies=not_checked` der: bu bir DB/R2/Groq hazır raporu değildir. Render'ın mevcut `/health/live` yolu değiştirilmedi.
6. **Keepalive gözlemi**: curl gerçek süreleri loglanır; cron sıklaştırılmadı ve süreklilik garantisi eklenmedi.

`/api/v1/health` detaylı DB/provider/R2/AI değerlendirmesidir, degraded dönebilir ve dış servis bekleyebilir. Bunu ucuz process probe yerine koymak provider/DB kesintilerinde gereksiz restart döngüsü yaratabilir; mevcut API sözleşmesi değiştirilmedi.

## Bellek ve test sonuçları

[Yerel ölçüm](reports/render-memory-evidence.json): aynı Windows ortamında iki ayrı fresh interpreter ile `import app.main` karşılaştırıldı. Working set **112,23 → 94,86 MB**; Windows private bytes **443,88 → 79,77 MB**; import süresi **2,297 → 2,031 saniye**. En sağlam gözlem `pyarrow_loaded=true → false` değişimidir. Bunlar tek yerel örneklerdir; Linux cgroup RSS/Render Memory Metrics değildir, kesin performans kazancı veya production OOM kanıtı sayılmaz.

- Güvenli backend paketi: **460 geçti, 14 mevcut deprecation uyarısı**. Liveness pool testi ve eager-native-import koruması dahil; tüm mevcut arşiv/retention testleri geçti.
- Frontend unit: **22 geçti**; gerçek asılı localhost backend iki saniyede readiness unavailable döndü.
- TypeScript: `npm run typecheck` geçti.
- Production build: `npm run build` geçti, standalone/static hazırlığı tamamlandı.
- Gerçek yerel single-service smoke: ana sayfa, static asset, liveness, bounded readiness, API rewrite, yedi legacy redirect ve on iki section view geçti.
- `--crash-child=FastAPI` ve `--crash-child=Next.js`: child zorla kapatılınca iki listener da kapandı ve supervisor nonzero döndü. Bu yerel child crash testidir; production crash sebebi değildir.
- Windows test cleanup'ı yalnız kendi loglanan child PID'lerini temizler; POSIX graceful shutdown iddiasında bulunmaz. Başka Python/Node süreçleri topluca öldürülmez.

Komutlar:

```powershell
cd backend
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
cd ../frontend
npm test
npm run typecheck
npm run build
npm run test:single-service
node scripts/test-single-service.mjs --crash-child=FastAPI
node scripts/test-single-service.mjs --crash-child=Next.js
```

DB/R2 canlı yazma, migration, retention DELETE veya production import çalıştırılmadı. Retention/collector/watchdog workflow'ları ve PostgreSQL/R2 mimarisi korunur. Yeni kodun Render'da deploy edildiği ayrıca doğrulanmalıdır; yerel standalone smoke canlı deploy kanıtı değildir.

## Kesin teşhis için gerekli Render alanları

Servisin **Events** sayfasından kapanma saatinin ±10 dakikası: UTC zamanlarıyla deploy/restart/spin-down/suspend/health failure/instance failure olayları, açıklama, deploy commit ve exit code/signal. **Logs** bölümünden aynı aralığın ilk exception'ı, “process exited unexpectedly”, supervisor signal, Uvicorn startup/shutdown ve Next ready/exit satırları gerekir.

**Metrics**: Memory limiti ile aynı eksendeki kullanım/peak ve CPU; varsa OOM bildirimi. **Settings**: gerçek instance type/plan, Start Command, Health Check Path, shutdown delay, auto-deploy ve son canlı commit. **Usage**: Free instance saatleri ve suspend/bandwidth durumu. Bağlantı parolaları, .env değerleri, token veya deploy-hook URL'si gerekmez; bu değerler loglarda varsa paylaşılmadan çıkarılmalıdır. Bu alanlar olmadan kesin crash/OOM teşhisi yapılamaz.

## İstenen üç ayrım

- **Kod kaynaklı kapanma düzeltildi mi?** Sağlık kontrolü bloklanması ve gereksiz başlangıç yükü düzeltildi/test edildi; kullanıcının bildirdiği kapanmanın kök sebebi ve bu değişikliklerle giderildiği **kanıtlanmadı**.
- **Render Free uyku modu hâlâ mevcut mu?** Servis gerçekten Free ise **evet**. Ping bunu garantili biçimde kaldırmaz. Canlı plan, YAML'daki 1c-2g ile karşılaştırılmalıdır.
- **Gerçek 7/24 erişim için ne gerekiyor?** Free idle kısıtından çıkmak için mevcut servisin uyumayan bir instance planında veya sürekli çalışan başka bir altyapıda olması gerekir. Workspace planını değiştirmek tek başına Free instance kısıtını kaldırmaz. Süreklilik ayrıca yeterli kaynak, doğru health check, izleme ve gerektiğinde yedeklilik gerektirir; ücretli tek instance da mutlak sıfır kesinti garantisi değildir. Bu görevde ücretli servis/plan oluşturulmadı veya satın alınmadı.

**LIVE_DELETE=NOT_RUN.**
