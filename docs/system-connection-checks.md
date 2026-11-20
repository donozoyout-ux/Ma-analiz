# Normal servis üzerinden bağlantı kontrolü

Render web servisinde Environment → `SYSTEM_DIAGNOSTICS_TOKEN` alanına parola yöneticisiyle oluşturulmuş en az 32 karakterlik rastgele bir anahtar tanımlayın. Anahtarı Git'e, loglara veya NEXT_PUBLIC_ değişkenlerine koymayın. Bu anahtar yalnızca bağlantı kontrolüne erişim sağlar; mevcut diğer API yollarının yetkilendirme davranışını değiştirmez.

Normal Start Command aynı kalır: `node frontend/scripts/run-single-service.mjs`.
`/system` sayfasında anahtarı girip **Bağlantıları kontrol et** düğmesine basın. HTTPS kullanın. Anahtar yalnızca isteğin Authorization başlığında taşınır; URL'ye, localStorage'a veya cookie'ye yazılmaz ve giriş alanı gönderimde temizlenir. Anahtarı değiştirmek erişimi iptal eder.

`POST /api/v1/system/connections` kimlik doğrulaması olmadan kontrol çalıştırmaz. Anahtar tanımlı değilse veya 32 karakterden kısaysa kapalı kalır (503); yanlış anahtarda 401 döner. İşlem başına eşzamanlı bir kontrol ve 30 saniye aralık uygulanır (429). Sonuç önbelleğe alınmaz. Sayfa açıldığında bu yeni kontrol otomatik başlamaz.

PostgreSQL: salt okunur işlem, SELECT 1, modelde gerekli tüm tabloların ve sütunların varlık kontrolü. READY migration geçmişi, indeksler veya veri bütünlüğü garantisi değildir. R2: yalnızca HeadBucket; nesne oluşturmaz, listelemez, okumaz veya silmez. ACCESSIBLE yazma yetkisi veya arşiv checksum doğrulaması anlamına gelmez. Ağ hataları sabit hata kodlarıyla döner; bağlantı adresleri, bucket adı ve hata metinleri gösterilmez. Eksik/geçersiz environment alanlarının yalnızca isimleri gösterilir.

Hiçbir migration, arşivleme, toplu indirme veya PostgreSQL temizliği çağrılmaz. Başarısız kontroller otomatik onarım yapmaz. Diğer worker/cron görevlerinin takvimi bu özellikten bağımsızdır.
