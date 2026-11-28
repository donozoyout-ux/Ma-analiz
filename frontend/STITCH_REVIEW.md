# MA-ANALİZ Stitch — 12 HTML referans / izole önizleme karşılaştırması

Kaynak: kullanıcının yüklediği `stitch_ma_analizi_aray_z_tasar_m(1).zip`.
Stitch canvas projesi: `projects/5112290510275601483`.
ZIP'in 12 `code.html` dosyası tek tek açılıp HTML başlıkları, kart bölümleri,
navigasyon ve tema renkleri kontrol edildi. Kaynak, gerçek üretim API'si değil,
**statik tasarım** içeriyor. İçindeki maç, oran, kazanma ve tahmin örnekleri
uygulamaya veri olarak aktarılmadı.

## Tasarım renkleri

| İşlev | Kaynak renk |
| --- | --- |
| Ana arka plan | `#0A0E13` |
| Panel | `#181C21` |
| Mint vurgu | `#4EDEA3` |
| Mint üzeri yazı | `#003824` |
| Genel yazı | `#E0E2EA` |

**Yalnızca `/stitch-preview`** üzerinde kullanılır. Mevcut beyaz/kırmızı
üretim tasarımı değiştirilmedi.

## 12 kaynak ekranı → 6 gerçek API önizlemesi

| Kaynak masaüstü HTML klasörü | Kaynak mobil HTML klasörü | Önizleme görünümü / veri bileşeni |
| --- | --- | --- |
| `genel_bak_ma_anali_z` | `genel_bak_mobil_ma_anali_z` | Genel Bakış → `FootballHome` |
| `ma_merkezi_ma_anali_z` | `ma_merkezi_mobil_ma_anali_z` | Maç Merkezi → `MatchCenter` / `SourceBulletin` |
| `ai_tahminleri_ma_anali_z` | `ai_tahminleri_mobil_ma_anali_z` | AI Tahminleri → `StitchPredictionLedger` kartları |
| `i_statistik_analiz_ma_anali_z` | `i_statistik_analiz_mobil_ma_anali_z` | İstatistik & Analiz → `Performance` ve geçici kayıt eğrisi |
| `veri_ar_ivi_ma_anali_z` | `veri_ar_ivi_mobil_ma_anali_z` | Veri Arşivi → `MatchCenter(initialSource='history',lockHistory)` |
| `ma_analiz_detay_ma_anali_z` | `ma_analiz_detay_mobil_ma_anali_z` | Maç Analiz Detayı → yalnız seçilmiş gerçek maç kimliğiyle `MatchCenterDetail` |

## 28 Kasım 2026 — Bu tur uygulanan iyileştirmeler

- AI Tahminleri ekranında eski jenerik tablo yerine **kaynak tasarıma
  yakın kart-öncelikli sunum**: lig/maç, model seçimi ve pazar, kayıt zamanı,
  model sürümü ve görünür `Doğrulanmamış` uyarısı.
- Kartlar mevcut `/api/v1/predictions` ve `/api/v1/matches`
  yanıtlarıyla doldurulur. **Uydurma oran, model güveni veya yayın isabeti
  yoktur**. Tanıtım amaçlı kaynak HTML'deki sayılar kullanılmaz.
- İlk 24 kayıt kartlarda sunulur; tamamı filtrelenebilir teknik tabloda
  erişilebilir bir `details/summary` açılır bölümüyle korunur.
- Mobilde tek sütun, geniş ekranda iki sütun; kaynak mint/koyu tonlar,
  klavye focus halkası ve `prefers-reduced-motion` korunur.
- AI ekranında `Tümü`, `Bekleyen` hızlı filtreleri ve erişilebilir
  `Diğer filtreler` açılır kontrolü önce gelir; kartlar boş yayın
  sayaçlarından önce görünür. Analiz ekranındaki ayrıntılı filtreler ve
  güvenilir olmayan başarı KPI'larının açıklaması korunur.
- Salt lokal 127.0.0.1 üzerinde çalışan ayrı CI eklendi:
  `Stitch preview QA (isolated)`. Kaynak ZIP GitHub'a yüklenmez.
- CI `46178441353` sonucu: frontend unit, typecheck, build ve
  ilk CI'da **81 tarayıcı kontrolü** başarılıydı; son hızlı-filtre kontrolleriyle güncel CI sonucu ayrıca doğrulanmalıdır. Önceki koşuda 390 ve 1440 pikselde altışar
  sayfa, toplam **12 gerçek render ekran görüntüsü** artifacts olarak saklanır.
  360/1280 için ayrıca taşma ve navigasyon kontrolü çalışır.
  Tüm tarayıcı API cevapları yalnız **sentetik** fixture'dır; production
  DB, R2, Render veya gerçek provider çağrısı yapılmaz.

## Açık tasarım farkları / üretim öncesi iş

- Kaynak HTML tamamen bağımsız Stitch statik sayfalarıdır; mevcut UI
  gerçek API bileşenlerini tekrar kullanır. Bu nedenle **pixel-perfect
  referans eşleşmesi henüz kanıtlanmadı**.
- Genel Bakış kaynak kart sırası ve yoğunluğu, Maç Merkezi filtre satırları,
  Analiz grafik yerleşimi ve Arşiv mobil kaydı hâlâ kaynaktan farklıdır.
- Mobilde kaynak genellikle dört alt navigasyon butonu kullanır;
  mevcut önizlemede alt dört sekme yanında Arşiv/Detay için
  görünür bağlamsal geçiş düğmeleri vardır.
- Maç detayı ancak API'den geçerli maç ID'si seçilince açılır;
  kaynak HTML'deki Mainz–Union örneği gerçek maçmış gibi gösterilmez.
- Gerçek sunucu ve gerçek veriyle QA, yardımcı teknolojilerle ekran okuyucu
  testi, tam referans-önizleme görüntü kıyası ve tema kabulü henüz yapılmadı.
- Repo genelindeki `Real Source Bulletin Tests` backend aşamasında
  `test_source_bulletin.py` sabit tarih/çağrı beklentisi sebebiyle
  başarısız olabilir. Bu durum ayrı kaynak bülteni testidir;
  arayüz testlerinin başarılı olduğunu gizlemez veya backend'i başarılı
  göstermemizi sağlamaz.

## Üretim sınırı

PR #68 ve PR #69 Draft kalır. `main` merge, production deploy, SQL/R2
write, gerçek veri scraping, canlı model yayınlama ve örnek skorları
gerçek gibi gösterme **bu işin kapsamında değildir**.
