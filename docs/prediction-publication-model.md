# Tahmin yayın kaydı: veri modeli değerlendirmesi

Bu çalışma frontend kapsamındadır; migration uygulanmadı ve veri değiştirilmedi.

## Mevcut durum
Prediction; created_at, analysis_timestamp, feature_snapshot, model_version, decimal_odds, status ve settled_at alanlarını içerir. created_at bir yayın kanıtı değildir. Modelde published_at yoktur; UPDATE/DELETE kısıtı veya kalıcı yayın defteri olmadan değiştirilemezlik iddia edilemez. Aktif kayıtlar expires_at ile sınırlı olduğundan mevcut predictions endpoint’i kalıcı performans defteri değildir.

## Önerilen eklemeli yayın modeli
Ayrı prediction_publications tablosu: publication_id, prediction_id, match_id, kickoff_at, published_at (sunucu UTC), market, selection, probability, confidence, model_version, feature_version, feature_snapshot_hash, odds_snapshot_id, odds_captured_at, decimal_odds, payload_hash ve payload_version. Veri paketi yayın anında dondurulur. Aynı tahmin sürümüne tek yayın için benzersiz kısıt; published_at < kickoff_at şartı. Oran kaydı yayın sonrasından alınamaz. Kanıtlar hash ile referanslanır; hash tek başına değişmezlik sağlamaz.

Uygulama rolüne yalnız SELECT/INSERT; UPDATE/DELETE engelleyen PostgreSQL trigger ve ayrı sahiplik. Yayın oluşturma tek transaction içinde yapılır. Yeni değerlendirme eskisini düzenlemez; yeni sürüm ve supersedes_publication_id ile eklenir. Kalıcı yayın tablosu aktif maçların süreli temizliğine CASCADE ile bağlanmamalıdır.

Sonuçlar ayrı prediction_settlements tablosuna eklenir: publication_id, result_id, result_source, verified_at, settled_at, outcome, paper_stake ve paper_return. Düzeltmeler yeni settlement sürümüdür; önceki sonuç saklanır. Performans yalnız maç öncesi yayınlanmış ve doğrulanmış sonuçla eşleşmiş kayıtlar üzerinden hesaplanır. Bekleyen, iptal ve sonuçsuz kayıtlar ayrı raporlanır; model olasılığı isabet oranı değildir.

R2 doğrulanmış yayın paketi ve hash yedeği için kullanılabilir; mevcut arşivleme yolları değiştirilmemelidir. Canlıya geçiş öncesinde yedek, migration provası, rol/trigger testleri, idempotency, eşzamanlı yayın ve maç başlangıç sınırı testleri gerekir. Bu öneri bu commit ile devreye alınmamıştır.

## Ürün referansları
- https://betist.ai/tr/picks.html : maç öncesi yayın, oran anlık görüntüsü ve sonuç takibi.
- https://golveri.com/ : lig/tarih bazlı maç listesi, oran araştırması ve geçmiş maç filtreleri.
Referans tasarımları, logoları ve içerikleri kopyalanmadı.

## Arayüz kapsamı
Arşiv, mevcut /matches?limit=500 endpoint’inde kalan tamamlanmış kayıtlarla sınırlıdır. R2 genel arama endpoint’i yoktur. Oran analizi seçilen maçın mevcut detay API’sini kullanır. Sayısal veri bulunmadığında boş durum gösterilir. Yayın zamanı olmayan tahminlerde çizgi ve yayın kaydı eksikliği açıklaması gösterilir.
