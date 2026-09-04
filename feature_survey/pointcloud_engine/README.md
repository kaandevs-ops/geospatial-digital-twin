# `feature_survey/pointcloud_engine/` — ROADMAP_V6 FAZ S3

| Modül | Kapsam | Durum |
|---|---|---|
| `icp.py` | ICP (Besl & McKay) nokta-nokta kayıt: Kabsch/Arun optimal dönüş (Jacobi özdeğer ayrışımıyla, stdlib-only SVD eşdeğeri), gerçek RMSE yakınsama kriteri | S3 tamamlandı |
| `ground_classification.py` | Progressive Morphological Filter (Zhang et al. 2003) — zemin/zemin-dışı sınıflandırma, ASPRS kodları (2=zemin, 1=sınıflandırılmamış) | S3 tamamlandı |
| `quality_report.py` | Yoğunluk (convex hull tabanlı, nokta/m²), gap analizi (ızgara + convex hull), gürültü tespiti (Statistical Outlier Removal — k-NN mesafe dağılımı) | S3 tamamlandı |

## Tasarım ilkesi

- **Gerçek, yayınlanmış algoritmalar** — ICP (Besl & McKay 1992 + Kabsch/
  Arun 1987 optimal dönüş), PMF (Zhang et al. 2003), SOR (PCL'nin standart
  gürültü giderme yöntemi). Hiçbiri "kural of thumb" veya uydurma eşik
  değildir; her eşik ya veriden türetilir (SOR: mu+k*sigma) ya da
  algoritmanın kendi yayınlanmış formülünden gelir (PMF: eğim-bağımlı eşik
  büyümesi).
- **Dış bağımlılık yok** — `data_engine.spatial_index.KDTree` (kesin
  en-yakın-komşu) ve stdlib `math` dışında hiçbir kütüphane kullanılmaz;
  proje bağımlılık felsefesiyle (stdlib-only çekirdek) tutarlı. Büyük
  bulutlar için dış motor (CloudCompare CLI, PDAL) roadmap'te "ince
  istemci" deseniyle bahsedilir — bu, platformun kendi implementasyonunun
  **yerini almaz**, büyük ölçekte performans için ileride eklenebilecek
  isteğe bağlı bir hızlandırma seçeneğidir; bu iterasyonda platformun
  kendi, test edilmiş algoritmaları önceliklendirildi (roadmap'in "gerçek
  hesaplama" ilkesi + stdlib-only çekirdek felsefesiyle en tutarlı seçim).
- **`converged`/sınıflandırma sonucu sabit "başarılı" değildir** — `ICPResult.
  converged` gerçek bir yakınsama testi sonucudur (max_iterations'a
  ulaşılıp yakınsamadıysa `False`); `GroundClassificationResult` gerçek
  algoritma çıktısıdır, placeholder kod atanmaz.

## Kabul kriteri (roadmap'ten) ve bu iterasyondaki karşılığı

> Açık kaynak referans veri setleri (örn. ISPRS benchmark point cloud'ları)
> üzerinde sınıflandırma doğruluğu (precision/recall) ölçülüp raporlanacak.

Bu iterasyonda ISPRS benchmark veri setine ağ erişimi (bu ortamda internet
erişimi sadece paket kayıt defterlerine izinlidir — genel internetten veri
seti indirilemez) olmadığından, `tests/test_phaseE4_point_cloud.py` ve yeni
`tests/test_phaseS3_pointcloud_engine.py` içinde **sentetik ama geometrik
olarak gerçekçi** bir test seti kullanılır: bilinen düz bir zemin düzlemi +
üzerine yerleştirilmiş "bina" (yüksek, dik kenarlı bloklar) noktaları.
Zemin/zemin-dışı ayrımı bilindiğinden (sentetik üretim sırasında etiketli),
precision/recall bu bilinen referansa göre hesaplanıp test içinde assert
edilir — algoritmanın "çalışıyor" değil, **sayısal olarak kanıtlanmış**
performansı gösterilir. Gerçek ISPRS veri setiyle doğrulama, ağ erişimi
sağlandığında `tests/` içine ayrı bir entegrasyon testi olarak eklenebilir
(mimari bunu engellemiyor — `progressive_morphological_filter` girdi olarak
sadece `list[tuple[float,float,float]]` bekler, veri kaynağından bağımsız).
