# Güvenli R2 bağlantı doğrulaması

Komut: `python -m app.jobs.r2_smoke_test`. Mevcut `Settings`, `.env` yükleme düzeni ve `R2ObjectStore` istemcisi kullanılır. PostgreSQL bağlantısı, migration, backfill veya arşiv temizleme başlatılmaz. Render Start Command değişmez.

## Yerel bilgisayarda çalıştırma (PowerShell)

Proje kökünden backend klasörüne geçin. Python 3.11 veya üzeri gerekir. Sanal ortamınız ve bağımlılıklarınız hazırsa kurulum adımlarını atlayın:

```powershell
cd 'C:\Users\PC\Desktop\Maçanaliz\backend'
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Proje kökündeki `.env` dosyasını editörünüzde yapılandırın veya aynı terminal oturumunda ortam değişkenlerini tanımlayın. Mevcut yükleme düzeninde terminal ortamı `.env` değerlerinden önceliklidir. Değerleri çıktıya veya Git'e koymayın. Gerekli değişkenler:

- `R2_ENDPOINT`: R2 hesabınızın HTTPS S3 API adresi. Public bucket/custom domain adresi kullanılmaz.
- `R2_ACCESS_KEY_ID`: hedef bucket için erişim anahtarı kimliği.
- `R2_SECRET_ACCESS_KEY`: ilgili gizli anahtar.
- `R2_BUCKET`: mevcut bucket adı.
- `R2_REGION`: isteğe bağlı; mevcut varsayılan `auto` kullanılır.

Anahtar hedef bucket üzerinde nesne yazma, okuma/metadata okuma ve test nesnesini silme yetkilerine sahip olmalıdır. Araç bucket veya yetki oluşturmaz.

Önce hiçbir ağ isteği göndermeyen yapılandırma kontrolünü yapın:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.r2_smoke_test --check-config
```

Bu kontrol yalnızca değişken adlarını ve tanımlı/geçersiz olma durumlarını gösterir. `CONFIGURED_NOT_TESTED`, bağlantının çalıştığı anlamına gelmez. Gerçek test yapılmadığı için çıkış kodu 2'dir.

Gerçek yazma/okuma/bütünlük/temizleme testi:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.r2_smoke_test
$LASTEXITCODE
```

İsteğe bağlı yapılandırılmış rapor veya mevcut Parquet doğrulaması:

```powershell
.\.venv\Scripts\python.exe -m app.jobs.r2_smoke_test --json
.\.venv\Scripts\python.exe -m app.jobs.r2_smoke_test --parquet
```

Her test komutu ayrı bir benzersiz test nesnesi oluşturur. `--parquet`, aynı sahiplik ve temizleme korumalarıyla küçük bir Zstandard Parquet dosyasını da çözümler. Bir veritabanı migration komutu çalıştırmanız gerekmez.

## Güvenlik sınırları

- Anahtar program içinde `_smoke_tests/<uuid>/health.txt` olarak üretilir; dışarıdan key/prefix/bucket override alınmaz. Parquet modunda uzantı `.parquet` olur.
- Bucket listelenmez. Arşiv yollarına yazma veya toplu silme çağrısı yoktur.
- HEAD ile boş anahtar seçilir; ardından `IfNoneMatch="*"` koşullu PUT kullanılır. HEAD ile PUT arasında başka nesne oluşsa da üzerine yazılmaz. Koşullu yazma desteklenmiyorsa koşulsuz yazmaya dönülmez.
- Nesnede UUID ve bağımsız rastgele sahiplik belirteci tutulur. Yazma yanıtı kaybolsa bile yalnız bu iki belirteç doğrulanırsa temizleme denenir. Belirteç doğrulanamıyorsa nesne silinmez ve temizleme başarılı sayılmaz.
- Geri okunan baytların SHA-256 özeti, gönderilen içeriğin özetiyle karşılaştırılır; boyut ve metadata özeti ayrıca kontrol edilir.
- Temizleme `finally` içinde yalnız o çalışmanın anahtarını siler; ardından HEAD ile yokluğu doğrulanır. İstemci başarıda da hatada da kapatılır.
- Sahiplik kontrolü ile silme arasındaki anlık değişime karşı nesne kilidi sağlanmaz. Test UUID alanını başka uygulamaların yazma alanı olarak kullanmayın.
- SDK hata mesajları, endpoint, bucket değeri ve kimlik bilgileri rapora aktarılmaz. Yalnız sabit izinli hata kodları gösterilir. `OBJECT_KEY` yalnız bu çalışmanın ürettiği test anahtarıdır.

## Çıktıyı yorumlama

Metin çıktısında `WRITE`, `READ`, `CHECKSUM`, `CLEANUP` adımları `PASS`/`FAIL` olarak gösterilir. Çalıştırılmayan adımlarda ayrıca `<ADIM>_REASON=NOT_RUN` yazılır: `FAIL` burada doğrulamanın geçmediğini belirtir; uzaktan bir işlem denendiğini kanıtlamaz. JSON raporunda bu ayrım doğrudan `NOT_RUN` olarak korunur.

Yalnız bütün adımlar gerçekten başarılıysa `STATUS=CONNECTED`, `LIVE_TEST=PASS` ve çıkış kodu 0 üretilir. Bu rapor o andaki küçük nesne testine aittir; arşivlerin bütünlüğünü veya PostgreSQL durumunu doğrulamaz. `--check-config` bağlantı başarısı değildir. JSON'daki `delete` alanı eski Python çağıranlar için `cleanup` ile aynı sonucu taşır.

Çıkış kodları:

- `0`: bütün yazma, okuma, boyut, SHA-256 ve temizleme kontrolleri geçti.
- `1`: gerçek test denemesi başarısız oldu.
- `2`: yapılandırma eksik/geçersiz/yüklenemedi veya yalnız yapılandırma kontrolü yapıldı; gerçek test çalışmadı.

`ERROR_STAGE` ilk başarısız aşamayı, `ERROR_CODE` güvenli nedenini gösterir. `CLEANUP_ERROR_CODE` ayrı tutulur; ilk hata temizleme hatası tarafından gizlenmez. Başlıca kodlar:

- `ACCESS_DENIED`: gerekli nesne işlemi için yetki reddedildi.
- `INVALID_ACCESS_KEY` / `SIGNATURE_MISMATCH`: anahtar veya imza doğrulanamadı.
- `BUCKET_NOT_FOUND`: hedef bucket bulunamadı.
- `ENDPOINT_UNREACHABLE`, `CONNECT_TIMEOUT`, `READ_TIMEOUT`, `TLS_ERROR`: ağ, zaman aşımı veya TLS sorunu.
- `KEY_ALREADY_EXISTS` / `CONDITIONAL_WRITE_CONFLICT`: koşullu yazma çakışması; mevcut nesnenin üzerine yazılmaz.
- `SMOKE_OBJECT_VERIFICATION_FAILED`: boyut veya SHA-256/metadata uyuşmazlığı.
- `SMOKE_OBJECT_OWNERSHIP_UNCONFIRMED`: güvenli sahiplik doğrulanamadı; silme yapılmaz.
- `SMOKE_OBJECT_STILL_PRESENT`: silme sonrasında nesne hâlâ mevcut.
- `R2_REQUEST_FAILED` / `R2_OPERATION_FAILED`: güvenli genel hata; ham SDK mesajı paylaşılmaz.

`CLEANUP=FAIL` olduğunda tek test nesnesi kalmış olabilir. `OBJECT_KEY` kaydını saklayın; yetki/ağ sorununu giderdikten sonra yalnız bu anahtarı ve sahipliğini inceleyin. Komutu yeniden çalıştırmak yeni UUID üretir; eski nesneyi otomatik temizlemez. Bucket boşaltma veya prefix altında toplu silme kullanmayın.

## Otomatik testler ve gerçek doğrulama ayrımı

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_r2_smoke_test.py -q
.\.venv\Scripts\python.exe -m pytest -q
```

Birim testleri InMemoryObjectStore ve kontrollü hata taklitleri kullanır; bunların geçmesi gerçek R2 bağlantısı doğrulaması değildir.

Bu geliştirme sırasında yerel yapılandırma kontrolünde `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` eksik bulundu. Gerçek R2 yazma/okuma testi çalıştırılmadı ve bağlantı başarısı iddia edilmedi. Canlı sistemin CONNECTED durumu yerel kimlik bilgilerinin mevcut olduğu anlamına gelmez.
