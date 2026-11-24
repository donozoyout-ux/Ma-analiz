# 5. aşama — otomatik, doğrulanmış 48 saat temizliği

Başlangıçta incelenen `main`: `a86b3ff`; çalışma sırasında gelen `fd329f9` keepalive commit’i de incelenip korunarak güncel main üzerine alındı. 4. aşamanın arşiv/hash/snapshot/ilişki kontrolleri korunur. Otomatik worker artık her çalışmada manuel `--approve-plan` istemeden kendi güncel planını üretip aynı doğrulama yolunu çalıştırabilir.

## Tek seferlik etkinleştirme

Bağımsız **`mac-analiz-worker`** servisinde bir kez:

```text
AUTO_RETENTION_DELETE_ENABLED=true
```

Varsayılan **false**. `OPERATIONAL_DELETE_ENABLED` eski manuel operatör yolunun bayrağıdır; otomatik worker'ın çalışması için onu açmak veya her seferinde hash vermek gerekmez. Auto bayrağı kapalıysa saatlik iş salt okunur plan üretir; arşiv tamamlama, operational DELETE veya otomatik cursor güncellemesi yapmaz.

`render.yaml` worker için auto bayrağını `sync:false` olarak tanımlar; Dashboard'daki açık tercih Blueprint tarafından her deploy'da false'a sıfırlanmaz. İlk Blueprint oluşturulurken bu alan için false/true değeri istenir; mevcut serviste yeni sync:false alanı otomatik eklenmez, Dashboard’dan bir kez eklenir. Değişken hiç tanımlanmazsa uygulama default'u false'tur. [Resmi Blueprint davranışı](https://render.com/docs/blueprint-spec). Bu teslimatta canlı bayrak değiştirilmedi. Ortam değişikliği worker yeniden başladıktan sonra geçerli olur. Web servisi veya ortak env group yerine worker üzerinde etkinleştirilmesi önerilir.

## Çalışma akışı

1. Aynı PostgreSQL bağlantısında ortak lifecycle advisory lock alınır. Arşiv commit'leri kilidi bırakmaz. Kilit başka worker/importta ise işlem ilerlemez.
2. Auto için ayrı kalıcı cursor ile sınırlı bir SQL batch seçilir. Erken arşivleme cursor'uyla karışmaz; engelli ilk kayıt tüm eski veriyi sonsuza kadar bekletmez.
3. Eksik arşiv, ledger veya kaynak kimlik indeksi varsa mevcut append-only arşivleyiciyle tamamlanması denenir. R2 nesnesi üzerine yazılmaz. Bozuk/çelişen nesne onarılmış gibi kabul edilmez.
4. Tracker commit'lerinden sonra ORM önbelleği atılır. Güncel SQL satırları ve ilişkiler kilitlenerek ilk plan/hash oluşturulur.
5. ORM tekrar yenilenir; ortak `execute` yolu planı yeniden hesaplayıp üretilen hash ile karşılaştırır. R2, SQL snapshot'ı veya uygun kayıt kümesi değişmişse işlem durur.
6. Tam 48 saat, terminal maç, FINAL/verification, kaynak kimliği, izin, bağımlılıklar ve VERIFIED ledger kontrolleri korunur. SHA-256, metadata, boyut, satır sayısı ve tüm kayıt içeriği tekrar doğrulanır.
7. Yalnız uygun bundle'ların çocuk kayıtları önce silinir. PK bazında gerçek satır yokluğu kontrol edilir. Silme, kaynak kimliği üzerindeki kaldırma makbuzu ve `retention_cleanup:<hash>` audit kaydı **aynı SQL transaction'ında** commit edilir.
8. Başarısız arşivli veya ilişkileri tamamlanmamış kayıt tutulur; diğer güvenli bundle'lar ilerleyebilir. `PARTIAL/FAILED` job sonucu kalıcı ledger'da retry edilebilir. Cursor döngüsü engelli kaydı sonraki turda yeniden değerlendirir.

SQL commit sonucu belirsizse başarı/0 silindi denmez: `deleted=null / UNKNOWN_RECONCILE_REQUIRED` korunur. Cursor güncellemesi commit sonrasında başarısız olsa bile kaynak kimlikleri/audit durur; yeniden çalıştırma silinen SQL satırını ikinci kez silmez. Audit bulunmadan SQL yokluğu başarılı temizlik sayılmaz.

Tamamlanmamış maç, sonuçlanmamış tahmin, devam eden analiz, eksik FINAL sonuç, kapsam dışı kupon ilişkisi, izin veya arşiv engeli hâlâ korunur. Kullanıcılar, ayarlar, kaynak kimlikleri, kritik job/audit kayıtları ve arşiv metadata'sı topluca sıfırlanmaz. Migration, DROP/TRUNCATE, R2 silme veya Sportmonks/ücretli API işlemi yoktur.

## Scheduler sahipliği ve süreler

Mevcut Start Command korunur:

```text
cd backend && python -m app.worker
```

Worker içinde retention ayrı döngü/thread olarak çalışır; koleksiyon ve AI I/O döngüsü retention'ın zaman kontrolünü durdurmaz. ORM session'ları thread'ler arasında paylaşılmaz. Web/FastAPI/Next.js başlangıcına scheduler eklenmedi. Mevcut Render background worker bağımsız servis olarak kalır; Retention HTTP keep-alive’a bağlanmaz ve ikinci web scheduler’ı eklenmez. Mevcut GitHub keepalive workflow’u değiştirilmedi; sağlık ping’i retention/audit çalıştığı kanıtı değildir. Render da background worker'ları sürekli çalışan ayrı servis olarak tanımlar. [Resmi Render açıklaması](https://render.com/docs/background-workers).

- Erken R2 arşivi: 900 saniyelik UTC slotlar.
- Otomatik temizlik: 3600 saniyelik UTC slotlar.
- Zamanlayıcı kontrolü: en fazla 60 saniyelik polling aralığı; aralık ayarları 15 dakika/1 saat üstünü geçemez.
- İş ledger'ı tamamlanmış slotu tekrar çalıştırmaz; yarım/hatalı slot retry edilebilir. Başka worker aynı slotu claim etse veya eski lease yeniden alınsa bile lifecycle advisory lock eşzamanlı veri işlemeyi engeller.

Bu bir zamanlayıcıdır, gerçek zaman garantisi değildir. 48 saati dolan kayıt ilk başarılı uygun kontrolde temizlenir; saatlik kontrol gecikmesi ve açıkça raporlanan arşiv/ilişki engelleri olabilir. Arşiv başarısızlığında süre doldu diye zorla silinmez.

## Çalıştığını doğrulama

Worker `retention_cycle` / `retention_cycle_failed` olaylarını loglar. Kalıcı, üzerine güncellenen tek heartbeat kaydı:

```text
scheduler:retention-heartbeat
```

Bu kayıt son döngü zamanı, scheduler sahibi, worker'ın auto bayrağı ve job sonuçlarını taşır. Web'in cevap vermesi worker'ın çalıştığı kanıtı sayılmaz. 15 dakikadan eski veya saat uyumsuz heartbeat açıkça stale olarak gösterilir.

Diğer küçük kalıcı durum kayıtları:

- `lifecycle:auto-retention-cursor`: sonraki temizlik batch'i.
- `lifecycle:last-auto-run`: son auto plan hash'i, silinen/korunan sayılar ve arşiv hataları.
- `lifecycle:last-auto-delete`: başarılı silmenin özeti; DELETE/audit ile atomik yazılır.

Salt okunur doğrulama:

```powershell
cd backend
python -m app.jobs.retention --dry-run --target
python -m app.jobs.retention --dry-run
```

Çıktıda bu sürecin bayrağı ayrı `auto_delete_enabled_in_this_process` olarak gösterilir. Web shell'i ile worker env'i farklı olabilir; gerçek worker durumu heartbeat üzerinden incelenir. Günlük çalışma için bu komutları çalıştırmak gerekmez. `--auto` aynı worker yolunu çağırır ve bayrak true ise gerçek DELETE yapabilir; rutin kontrol için dry-run kullanılmalıdır.

## 1 GB ve VACUUM

%70 WARNING, %80 HIGH, %90 CRITICAL sınırları korunur. Silme sonrası `pg_database_size` hemen küçülmeyebilir. PostgreSQL'in normal vacuum/autovacuum işlemleri ölü satır alanını yeniden kullanıma hazırlar; dosyayı işletim sistemine hemen geri vermesi garanti değildir. `VACUUM FULL` tabloyu yeniden yazar, ek disk alanı ve güçlü kilit gerektirir. [PostgreSQL resmi bakım açıklaması](https://www.postgresql.org/docs/current/routine-vacuuming.html).

Yeni teşhis yalnız SELECT/SAVEPOINT kullanır: autovacuum/track_counts durumu, operasyonel tabloların tahmini canlı/ölü satırları ve son vacuum/analyze zamanları. En az 1.000 ölü satır ve %20 tahmini ölü satır oranında veya autovacuum kapalıysa inceleme önerir. İstatistikler kesin satır sayısı değildir. Yetki/istatistik hatası, doğrulanmamış veriyi silme iznine dönüşmez.

Otomatik `VACUUM FULL`, CLUSTER, tablo rewrite veya zorunlu alan boşaltma yoktur. Bu aşamada manuel/otomatik VACUUM komutu çalıştırılmadı; autovacuum ayarları değiştirilmedi.

## Test ve canlı durumun ayrımı

[Otomatik test raporu](reports/auto-retention-tests.json): **305 test geçti**. Testler; default-off ve bağımsız bayrak, onaysız otomatik plan üretimi, arşiv tamamlama, hash/snapshot değişikliği, bozuk nesne, 48 saat sınırı, aynı işlemin tekrarı, SQL commit rollback'i, audit, mutex busy, iki ayrı scheduler döngüsü/heartbeat ve salt okunur VACUUM teşhisini kapsar. Gerçek DELETE testleri yalnız SQLite bellek veritabanındadır.

Kullanıcının doğruladığı canlı durum (PostgreSQL/R2 başarılı, Bayern–Köln zinciri doğru, yaklaşık 8,86 MB kullanım) başlangıç bilgisi olarak kabul edildi. Bu bilgisayardaki [.env tabanlı salt okunur komut](reports/auto-retention-live-readonly.json) ise `DATABASE_URL`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` eksikliği nedeniyle bağlantı kuramadı. Bu, uzaktaki doğrulanmış durumun başarısız olduğu anlamına gelmez; bu oturumda yeniden ölçülemediği anlamına gelir.

Repo workflow'undaki gerçek web adresine tek bir [salt okunur liveness isteği](reports/auto-retention-web-health.json) gönderildi: HTTP 200. Bu yalnız o andaki web erişilebilirliğini doğrular; worker'ın yeni kodla deploy edildiği veya auto flag'inin açık olduğu kanıtı değildir.

**Bu teslimatta LIVE_DELETE=NOT_RUN, LIVE_VACUUM=NOT_RUN, LIVE_MIGRATION=NOT_RUN.** Canlı scheduler aktivasyonu yapılmadı; varsayılan false korundu. Deploy ve worker üzerinde tek seferlik true ayarından sonra her temizlikte manuel komut/hash onayı gerekmez. Üretimdeki ilk gerçek çalışma, heartbeat ve atomik audit ile ayrıca doğrulanmalıdır.
