# PostgreSQL + R2 retention son doğrulaması — Stage 7D.1

Başlangıç **3b16c54**, tarih **21 Kasım 2026**. Stage 5 lifecycle/retention, Stage 6 Actions scheduler/watchdog ve Stage 7D storage preflight incelendi. **Production LIVE_DELETE=NOT_RUN; AUTO_RETENTION_DELETE_ENABLED=false; OPERATIONAL_DELETE_ENABLED=false.** Production DELETE, R2 PUT/overwrite/delete, migration, DROP/TRUNCATE ve VACUUM/VACUUM FULL çalıştırılmadı. Production kayıtlarının zamanları değiştirilmedi.

## Canlı salt okunur doğrulama

Bu raporun kod/test teslimatı sırasında yeni canlı rapor henüz **NOT_RUN**; Actions salt okunur kontrolü tamamlandığında bu bölüm gerçek sonuçla güncellenecek. Önceki Stage 7D preflight'ı yeni gözlem gibi sunulmaz.

Yeni komut:

```powershell
cd backend
python -m app.jobs.retention_validation --output retention-validation.json
```

Yerel PG/R2 değişkenleri eksikse bağlantı kurulduğu varsayılmaz; Stage 7D preflight'ın güvenli eksik-değişken raporu korunur. Gerçek kontrol, mevcut beş Secret'ı kullanan **Controlled import read-only preflight** workflow'unda `check=retention-validation` ile manuel yapılır. Bu seçenek SQL audit bile yazmaz. Production yazma/silme seçeneği veya yeni schedule eklenmedi.

`retention_validation` önce Stage 7D bağlantı/şema/constraint/tip/eski Bayern zinciri kontrolünü kullanır. Ardından sunucunun doğruladığı **READ ONLY + REPEATABLE READ** SQL transaction'ında:

- Her operasyonel tablo için gerçek COUNT, 48 saati dolan satır sayısı (`>=48h`) ve yaş referansı eksik satırlar sayılır. Maçta kickoff, sonuçta verified_at; oran/teknik veri/tahmin/analiz/kuponlarda mevcut lifecycle zaman kuralları aynen kullanılır.
- Lifecycle planı 25 maçlık sayfalarla, en fazla 20 sayfa ve maç başına mevcut 500 child sınırıyla incelenir. Paylaşılan kayıtlar PK ile tek sayılır. Eksik tarama/child limiti/orphan kupon varsa **INCOMPLETE_SCAN** ve `not_scanned` görünür; bütün veritabanı doğrulanmış gibi raporlanmaz.
- Her taranan kayıt için R2 nesne varlığı, SHA-256, byte boyutu, satır sayısı, metadata, tam içerik, SQL snapshot/ledger, source identity ve bağımlılıklar mevcut Stage 5 verifier ile kontrol edilir. İlk doğrulama hatasından sonra denenmeyen alanlar PASS sayılmaz; `NOT_VERIFIED` ve sabit neden kodları korunur.
- Bilinmeyen bir foreign key, rapordaki silinebilir sayısını da sıfırlar; yalnız execute aşamasında ortaya çıkacak engel dry-run'da gizlenmez.
- Kullanıcı ve metadata değerleri okunup rapora taşınmaz. Korunan tablo/prefix sayıları, aynı snapshot'ta değişmediği, sunucu read-only durumu ve cleanup audit sayıları gösterilir. Bu kanıt **bu kontrolün** veri değiştirmediğidir; eşzamanlı başka bakım işlerinin hiç metadata yazmadığı iddia edilmez.
- PostgreSQL toplam boyutu, bilinen uygulama tablolarının table/index/total byte büyüklükleri, tahmini live/dead tuple sayıları ve salt okunur autovacuum değerlendirmesi raporlanır. `pg_table_size` TOAST/FSM/VM dahil tablo alanıdır; tuple sayıları istatistik tahminidir.

Rapor `NO_ELIGIBLE_RECORDS` dönerse bunun anlamı tamamlanmış salt okunur planda silinebilir kayıt olmadığıdır. Bu ne hata ne de canlı DELETE yolunun başarıyla denendiğinin kanıtıdır. Gözlem anından sonra yaş/SQL/R2 değişebileceği için rapor otomatik silme yetkisi değildir; execute güncel kilitli planı tekrar üretir.

## İzole ortamda gerçek DELETE ve veri koruma

Yeni testler yalnız **SQLite (FK açık) + InMemoryObjectStore** kullanır. Sabit sentetik test saati **25 Kasım 2026 12:00 UTC**; maç 50, sonuç 49 saat yaşındadır. Bu production saatini veya gerçek kayıt tarihlerini değiştirmez.

Pozitif uçtan uca senaryoda:

- Başlangıç: 1 sentetik FINISHED maç + 1 doğrulanmış FINAL sonuç; 2 doğrulanmış immutable lifecycle Parquet nesnesi.
- Aynı Stage 5 `automatic(enabled=True)` yolu gerçekten SQLite DELETE çalıştırır; SQL event gözlemcisi yalnız **match_results ve active_matches** tablolarında DELETE gördüğünü doğrular.
- **TEST_DELETE=EXECUTED, silinen=2**, kalan operasyonel maç/sonuç=0. Bu bayrak yalnız izole test içindir; **production LIVE_DELETE=NOT_RUN**.
- User, sistem ayarı, review-source/evidence/audit, operator metadata ve eski kritik JobRun korunur. Source identity aynı maç UUID'sini tutar; yalnız doğrulanmış cleanup zamanı/plan hash'i eklenir. Cleanup audit'i aynı transaction'da oluşur.
- R2 byte'ları ve nesne sayısı aynen kalır; HistoricalRepository SQL temizliğinden sonra aynı 1–1 geçmiş sonucunu okur.
- Aynı görev tekrar çalışınca silinen=0, yeni cleanup audit'i=0; duplicate ve yeni UUID oluşmaz.

Negatif senaryolar: eksik/bozuk nesne, başarısız onarım/kısmi arşiv, metadata/ledger/içerik değişikliği, identity uyuşmazlığı, SCHEDULED maç, bekleyen tahmin, RUNNING analiz, dış ilişki/child limiti, native duplicate constraint'i, silme öncesi yeniden doğrulama ve commit/crash rollback. Başarısız/eksik arşiv hiçbir zaman zorla silinmez. Eksik nesne normal otomasyonda önce append-only onarılabilir; onarım başarısızsa silme engellenir. Bozuk mevcut nesne ezilmez.

Crash senaryosu, DELETE flush'ından önce hata enjekte ederek SQL satırlarının ve eski audit'in kaldığını, başarı audit'i oluşmadığını, R2'nin değişmediğini; tekrar denemede aynı arşivlerin kullanıldığını doğrular. Eşzamanlı iki thread, **mocked PostgreSQL advisory transport** üzerinde üretim `storage_session` lock yolunu çalıştırır; ikinci bağlantı `LIFECYCLE_ALREADY_RUNNING` alır. Bu gerçek production'da iki DELETE worker çalıştırıldığı iddiası değildir. Mevcut ledger idempotency/tekrar slot, busy lock ve değişen plan hash'i testleri de korunur.

## GitHub Actions — schedule ve manuel ayrımı

[API gözlem JSON'u](reports/stage7d1-actions-schedules.json) ve [izinli alanlarla filtrelenmiş job logları](reports/stage7d1-schedule-log-evidence.json), **17:59 UTC / 20:59 Türkiye** gözlemini kaydeder. Scheduler active; erişilebilir geçmişte **14 run: 2 schedule, 4 workflow_dispatch, 8 push**. Watchdog active; **3 run: 1 schedule, 2 workflow_dispatch**. Manuel/push başarıları schedule başarısı sayılmadı.

- [Archive 44927449780](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927449780): **event=schedule**, oluşturulma `16:58:19Z`, workflow success. Runtime **SKIPPED**, arşiv hatası=0, silinen=0, SQL audit RECORDED. Bu örnekte işlenecek sayfa bulunmadığı/cursor boş olduğu için yeni arşiv yazıldığı iddia edilmez.
- [Auto 44928703385](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44928703385): **event=schedule**, `17:11:45Z`, workflow success. Runtime **SKIPPED / DRY_RUN**, policy_hours=48, auto_delete_enabled=false, scanned=2, eligible=0, retained=2, **WITHIN_RETENTION_WINDOW=2**, LIVE_DELETE=NOT_RUN. SQL audit RECORDED.
- [Watchdog 44927261704](https://github.com/donozoyout-ux/Ma-analiz/actions/runs/44927261704): **event=schedule**, `16:56:09Z`, **failure**. Log: `NO_RECENT_SUCCESSFUL_ARCHIVE_SCHEDULE` ve `NO_RECENT_SUCCESSFUL_AUTO_SCHEDULE`; her iki last-success null. Bu iş ilk iki başarılı schedule'dan önce çalışmış; hata SQL/R2 veya DELETE hatası değil, gerçek schedule-freshness uyarısıdır.
- Manuel eski dry-run **44891581219 failure** ile sonraki **44891800021 success** ayrıdır; manuel archive/auto başarıları **44892128200 / 44892302205**. Eski hata yeni canlı veri hatası diye sunulmaz.

Cron değişmedi: archive UTC `7,22,37,52`, auto UTC `2`, watchdog UTC `41`; `concurrency=ma-analiz-retention`, cancel-in-progress=false. Connection-scope lifecycle advisory lock arşiv commit'leri boyunca tutulur. Aynı zaman slotu SQL ledger idempotency anahtarıyla tekrar işlenmez. Auto ayrıca güncel planı kendi üretir; snapshot/hash ve bağımlılıklar yeniden kontrol edilir; manuel approve-plan gerekmez, bağımsız auto bayrağı gerekir.

İki başarılı schedule, sürekli 15 dakika/saat hizmet garantisi değildir. Gözlemde son archive yaklaşık 61 dakika önceydi; watchdog'un 45 dakikalık archive eşiğini aşar. Kaçırılmış nominal cron sayısı bu sınırlı API örneklerinden kesin hesaplanamaz. GitHub gecikme/atlama ve public repo 60 günlük hareketsizlik riski sürer; watchdog'un kendisi de çalışmayabilir. Harici bağımsız izleme/insan takibi gerekir. 48 saat bu nedenle veri güvenliği pahasına zorunlu silme garantisi yapılamaz.

## Kapasite, VACUUM ve bildirimler

Mevcut eşikler decimal 1 GB üzerinden **%70 WARNING, %80 HIGH, %90 CRITICAL**. Kapasite hesaplaması zaten mevcuttu; küçük düzeltme Actions `emit` içinde bu üç durumu ayrıca sabit kodlu **::warning::POSTGRES_CAPACITY_...** annotation'ına dönüştürür. Threshold sınırları, raw secret redaksiyonu ve **::error::** hata bildirimi test edildi. Bunlar GitHub UI/log uyarılarıdır; e-posta/SMS/push teslimatı kurulmuş veya garanti edilmiş değildir. Kişilere mesaj gönderilmedi.

DELETE fiziksel dosya boyutunu hemen küçültmeyebilir. Dead tuple/autovacuum/track_counts ve son vacuum/analyze istatistikleri yalnız SELECT ile değerlendirilir; normal autovacuum yeniden kullanıma alan açabilir. Dead tuple >=1000 ve oran >=%20 veya autovacuum kapalıysa inceleme önerilir. Normal VACUUM bile bu görevde çalıştırılmadı; VACUUM FULL/DDL/rewrite yapılmadı. Korunan metadata için mevcut 100.000 satır büyüme uyarısı ve tablo/index boyutları izlenir; audit veya review verisi topluca budanmaz.

## Testler, değişiklikler ve çalıştırma

**544 güvenli backend testi geçti, 14 mevcut deprecation warning; 24 yeni vaka.** Harici/canlı credential isteyen `test_goaloo_live.py`, `test_live_integrations.py` ve görev dışı, kullanıcıya ait untracked backup testi çalıştırılmadı. Gerçek salt okunur kontrol ayrı Actions adımıdır.

```powershell
python -m pytest -q tests/test_retention_validation.py tests/test_auto_retention.py tests/test_lifecycle.py tests/test_actions_retention.py
python -m pytest -q --ignore=tests/test_goaloo_live.py --ignore=tests/test_live_integrations.py --ignore=tests/test_backup_before_migration.py
```

Eklenenler: salt okunur validation CLI, mevcut manuel preflight workflow'unda salt okunur seçim, offline güvenlik testleri ve kapasite annotation'ı. DELETE/retention kararı değişmedi. Stage 6 cron/watchdog, collector, verified import, frontend, kullanıcı sistemi, Render Start Command ve health endpoint'leri değiştirilmedi. Yeni migration, Secret veya ücretli servis yoktur.

## Otomatik DELETE açılmadan önce

- [ ] Ayrı, tek anlamlı kullanıcı onayı alınmalı; **bu görev onay değildir**.
- [ ] Güncel, tam taranmış readonly plan; R2/ledger/SHA/satır/içerik/identity/dependency PASS olmalı. INCOMPLETE_SCAN veya kaynağı belirsiz satırlar incelenmeli.
- [ ] Gerçekten 48 saati dolan uygun kayıt yoksa canlı silme doğrulanmış sayılamaz. Synthetic pozitif test ile canlı gözlem ayrımı korunmalı.
- [ ] Scheduler'ın daha uzun bir dönemdeki archive/auto schedule sürekliliği ve watchdog uyarılarının ele alınması doğrulanmalı; mevcut iki örnek yeterli uptime kanıtı değildir.
- [ ] PostgreSQL DELETE/audit izinleri ve connection-scope lock davranışı canlı etkinleştirme öncesi değerlendirilmelidir; bu görevde yazma izinleri denenmedi.
- [ ] Her silmeden önce güncel kilitli hash/snapshot/R2 recheck devam etmeli; archive/ilişki engeli olanlar silinmemeli. SQL + audit atomic olmalı.
- [ ] Kapasite/dead tuple/metadata büyümesi için bağımsız bildirim takibi ve başarısız arşiv için retry/runbook olmalı; R2 hiçbir zaman silinmemeli.
- [ ] AUTO_RETENTION_DELETE_ENABLED yalnız sonraki açık onayla değiştirilmeli. OPERATIONAL_DELETE_ENABLED manuel yol için ayrı kalır; toplu reset/prune açılmamalı.

**Sonuç: izole gerçek silme yolu testlerle doğrulandı. Production silme açılmadı; sonraki aşamada açık kullanıcı onayı beklenecek.**
