# `feature_survey/geodetic_engine/` — ROADMAP_V6 FAZ S2

Platformun **kendi** jeodezi/topografya matematiği — dış araca bağımlı
değil, stdlib (`math`, `statistics`) ile uygulanır.

| Alt modül | Kapsam | Durum |
|---|---|---|
| `reduction.py` | Trigonometrik indirgeme (Total Station → E/N/Z), yöney biriktirme | S2.1 tamamlandı |
| `traverse.py` | Açısal/doğrusal kapatma, Bowditch + Transit dengeleme | S2.2 (birinci alt-faz) tamamlandı |
| `least_squares.py` | Parametrik en küçük kareler ağ dengelemesi: mesafe+azimut gözlem denklemleri, ağırlıklı normal denklemler, varyans-kovaryans matrisi, hata elipsleri | S2.2 (ikinci alt-faz) tamamlandı |
| `gnss_adjustment.py` | Ağırlıklı epoch ortalaması, kontrol noktası karşılaştırması, RMSE/güven aralığı, gerçek Student t-dağılımı kritik değeri (küçük örneklem) | S2.3 tamamlandı |
| `datum_transform.py` | 7-parametreli Helmert, jeodezik↔jeosentrik dönüşüm, jeoit ondülasyonu | S2.4 tamamlandı (matematik); resmi parametre kaynağı (HGK/TUSAGA) entegrasyonu eklenmedi |

## Tasarım ilkesi

Veri veya parametre eksikse platform **açıkça hata fırlatır**
(`InsufficientDataError`), sessizce varsayılan/uydurma değer üretmez —
`ROADMAP_V6.md`'nin "pazarlık konusu değil" ilkesi burada birebir uygulanır:
örn. `orthometric_height()` jeoit ondülasyonu (N) verilmeden asla
N=0 varsaymaz; `weighted_mean_position()` GST kaynaklı gerçek sigma
olmadan ağırlıklı ortalama hesaplamaz.

## Tamamlanan (bu iterasyonda eklendi)

- **En küçük kareler (least squares) ağ dengelemesi** (S2.2 ikinci alt-faz,
  `least_squares.py`): parametrik (Gauss-Markov) model, mesafe ve azimut
  gözlem denklemleri, gerçek ağırlıklı normal denklemler (P = diag(1/sigma^2)),
  Newton-Raphson tipi iterasyonla doğrusal olmayan gözlemlerin çözümü,
  a-posteriori referans varyans (sigma0^2), tam varyans-kovaryans matrisi ve
  2x2 alt-matristen kapalı-formül hata elipsi (yarı eksenler + yönelim).
  Tekil (rank eksik/datum eksik) ağlarda `SingularNormalEquationsError`,
  yetersiz fazla-ölçüde `InsufficientDataError` fırlatılır - sessiz
  pseudo-inverse veya varsayılan sigma yok. Saf stdlib (Gauss-Jordan
  matris tersleme) - `geodetic_engine`'in "dış araca bağımlı değil"
  ilkesiyle tutarlı. Birim testler: `tests/test_phaseS2_least_squares.py`
  (hatasız gözlemlerle bilinen konumun tam geri kazanılması, yetersiz
  redundancy/sigma reddi, tekil ağ reddi, kovaryans/elips pozitifliği).
- **t-dağılımı kritik değerleri** (küçük örneklem güven aralığı,
  `gnss_adjustment.t_critical_95`): nu=1..30 için standart istatistik
  tablosundan (Ghilani & Wolf Ek C / NIST e-Handbook) tam değer; nu>30 için
  normal yaklaşım - hangisinin kullanıldığı `exact_from_table` ile açıkça
  raporlanır. `RmseReport.confidence_95_radius_m` artık n<20 durumunda
  gerçek t-kritik değeriyle ölçeklenir (önceden sabit normal yaklaşımı
  kullanılıyordu); `used_normal_approximation` artık "gerçekten normale
  düşüldü mü" anlamına geliyor (n=3 gibi küçük örneklemler artık tablodan
  tam t-değeri aldığı için bu alan `False` döner).

## Eksik kalanlar (roadmap S2 kapsamında, sonraki iterasyon)

- **Datum dönüşüm parametrelerinin resmi kaynaktan (HGK/TUSAGA-Aktif)
  yüklenmesi**: `DatumTransformParameters.source` alanı belge zorunluluğunu
  taşır, ancak resmi parametre setinin dosya/README içine gömülmesi ayrı bir
  veri-temin adımı gerektirir (roadmap: "gerçek sayılar kod içine
  gömülmeyecek, kaynak dosyadan yüklenecek").
- **Elle hesaplanmış 10 referans problem testi** (roadmap S2 kabul
  kriteri): `tests/test_phaseS2_geodetic_engine.py` +
  `tests/test_phaseS2_least_squares.py` temel/analitik doğrulanabilir
  senaryoları (kapalı-form çözümle karşılaştırılabilir resection örneği
  dahil) içerir; ders kitabı/yönetmelik referans problemleriyle tam eşleşme
  testi ayrı bir iterasyonda genişletilmelidir.
