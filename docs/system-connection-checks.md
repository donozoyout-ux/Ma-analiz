# Normal servis üzerinden bağlantı kontrolü

Render web servisinde Environment → `SYSTEM_DIAGNOSTICS_TOKEN` alanına parola yöneticisiyle oluşturulmuş en az 32 karakterlik rastgele bir anahtar tanımlayın. Anahtarı Git'e, loglara veya NEXT_PUBLIC_ değişkenlerine koymayın. Bu anahtar yalnızca bağlantı kontrolüne erişim sağlar; mevcut diğer API yollarının yetkilendirme davranışını değiştirmez.

Normal Start Command aynı kalır: `node frontend/scripts/run-single-service.mjs`.
`/system` sayfasında anahtarı girip **Bağlantıları kontrol et** düğmesine basın. HTTPS kullanın. Anahtar yalnızca isteğin Authorization başlığında taşınır; URL'ye, localStorage'a veya cookie'ye yazılmaz ve giriş alanı gönderimde temizlenir. Anahtarı değiştirmek erişimi iptal eder.

`POST /api/v1/system/connections` kimlik doğrulaması olmadan kontrol çalıştırmaz. Anahtar tanımlı değilse veya 32 karakterden kısaysa kapalı kalır (503); yanlış anahtarda 401 döner. İşlem başına eşzamanlı bir kontrol ve 30 saniye aralık uygulanır (429). Sonuç önbelleğe alınmaz. Sayfa açıldığında bu yeni kontrol otomatik başlamaz.

PostgreSQL: salt okunur işlem, SELECT 1, modelde gerekli tüm tabloların ve sütunların varlık kontrolü. READY migration geçmişi, indeksler veya veri bütünlüğü garantisi değildir. R2: yalnızca HeadBucket; nesne oluşturmaz, listelemez, okumaz veya silmez. ACCESSIBLE yazma yetkisi veya arşiv checksum doğrulaması anlamına gelmez. Ağ hataları sabit hata kodlarıyla döner; bağlantı adresleri, bucket adı ve hata metinleri gösterilmez. Eksik/geçersiz environment alanlarının yalnızca isimleri gösterilir.

Hiçbir migration, arşivleme, toplu indirme veya PostgreSQL temizliği çağrılmaz. Başarısız kontroller otomatik onarım yapmaz. Diğer worker/cron görevlerinin takvimi bu özellikten bağımsızdır.

## Migration öncesi tanılama

Korumalı kontrol mevcut/beklenen Alembic revision'larını, varsayılan şemayı, diğer kullanıcı şemalarında beklenen tabloları ve Alembic tablosunun varlığını raporlar. Bilinmeyen revision içeriği gösterilmez. Revision head olduğu halde eksik tablo/sütun varsa REVISION_SCHEMA_MISMATCH döner. Başka şemalarda aynı tablolar bulunması inceleme gerektirir; otomatik taşıma veya stamp yapılmaz. READY yalnızca bu kontrollerin geçtiğini gösterir; sütun tipi, indeks, constraint ve veri bütünlüğünün tam denetimi değildir. Katalog okuma başarısızsa NOT_VERIFIED döner.

Genel sağlık yanıtı database ile database_schema alanlarını ayırır; ayrıntılı şema bilgileri yalnızca korumalı endpoint'te gösterilir. Bağlantı başarılı ama şema eksikse genel durum degraded kalır.

R2 hata kategorileri: AUTHENTICATION, AUTHORIZATION, AUTHENTICATION_OR_AUTHORIZATION, ENDPOINT_OR_BUCKET, ENDPOINT_CONFIGURATION, NETWORK, TIMEOUT, UNKNOWN. HeadBucket yalnızca 403 döndürürse kimlik doğrulama ile yetki sorunu kesin ayrıştırılamaz. DNS/erişim hatası NETWORK olarak gösterilir; endpoint'in yanlış olduğunu tek başına kanıtlamaz. Ham exception, endpoint, bucket veya kimlik bilgileri yanıt içine alınmaz.

Alembic env.py postgresql:// adreslerini psycopg sürücüsüne dönüştürür; bu değişiklik migration çalıştırmaz. Canlı uygulamadan önce yedek ve ayrı PostgreSQL ortamında migration doğrulaması hâlâ gereklidir.

## Son migration öncesi envanter

Korumalı kontrol, pg_catalog üzerinden sistem dışı şemaları ve kullanıcı tablolarını (r), bölümlenmiş tabloları (p), foreign tabloları (f), görünümleri (v), materialized view (m) ve sequence (S) nesnelerini listeler. Tablo içerikleri okunmaz. reltuples satır sayısı tahminidir; bilinmeyen tahmin null gösterilir. 500 nesne sınırı aşılırsa TRUNCATED döner. Sorgu/yetki hatasında NOT_VERIFIED kalır.

NO_USER_RELATIONS yalnızca bu nesne türlerinin katalogda olmadığını gösterir; fonksiyonlar, özel tipler, uzantılar ve large object içerikleri dahil tam veritabanı boşluğu garantisi değildir. Tablo tahmini sıfır olsa bile NOT_EMPTY döner. Sonuç kontrol anına aittir; migration öncesi yazarları durdurup yeniden kontrol edin.

alembic_version_exists ve alembic_version_state, tablo yokluğu ile boş revision tablosunu ayırır. Bilinen revision değerleri gösterilir; bilinmeyen değerlerin içeriği gizlenir. Diğer şemalardaki Alembic tabloları ayrıca işaretlenir.

Hedef parmak izi, yapılandırılan host/port ile sunucunun bildirdiği veritabanı adı/OID değerlerinin SYSTEM_DIAGNOSTICS_TOKEN anahtarıyla HMAC-SHA256 özetidir (24 hex karakter). Ham adres, veritabanı adı, kullanıcı veya parola gösterilmez. Parola değişimi parmak izini değiştirmez; tanılama anahtarı, adres veya veritabanı kimliği değişirse değişebilir. Bu değer tek başına doğru hedefin kanıtı değildir. Render hedefini bağımsız doğruladıktan sonra parmak izini web servisinde SYSTEM_EXPECTED_DATABASE_FINGERPRINT olarak sabitleyin; ilk görülen değeri doğrulamadan sabitlemeyin. Beklenen değer yoksa NOT_VERIFIED, uyuşmazsa MISMATCH, eşleşirse MATCH döner. Sürüm numarası, replika durumu ve yapılandırılan veritabanı adı eşleşmesi ayrıca gösterilir.

R2 tanılaması yalnızca sabit hata ailesi, izin listesindeki servis kodu ve sayısal HTTP durumunu döndürür. Ham mesaj/header/endpoint/bucket/anahtar gösterilmez. 400 REQUEST_CONFIGURATION, 429 RATE_LIMIT, 5xx SERVICE_ERROR, parametre hatası REQUEST_CONFIGURATION, import/tip hatası SDK_OR_DEPENDENCY olarak ayrılır. Tanınmayan hatalar UNKNOWN/UNCLASSIFIED kalabilir; başarılı bağlantı sayılmaz.

Migration, stamp, downgrade ve veri taşıma çalıştırılmaz. Start Command değişmez.
