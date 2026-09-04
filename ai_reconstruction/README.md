# ai_reconstruction (Phase 4) — ✅ Uygulandı

Yapay zekâ destekli bina analizi ve rekonstrüksiyon katmanı. İki katmanlı
tasarım: (a) bağımlılıksız kural/istatistik tabanlı `*HeuristicPredictor`
sınıfları (varsayılan, hemen çalışır), (b) `Predictor` Protocol'ü ile
enjekte edilebilen harici ML modeli entegre noktaları.

Teknik spesifikasyon: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md#phase-4--ai-reconstruction)

## Alt modüller

| Modül | Sorumluluk |
|---|---|
| `predictor.py` | Ortak `Predictor` Protocol'ü (`predict(features: dict) -> dict`) |
| `building_analyzer.py` | `AIBuildingAnalyzer` — yükseklik/kat sayısı/kullanım/mimari stil/yaş/cephe tahmini |
| `roof_predictor.py` | `AIRoofPredictor` — bina tipi + iklim bölgesi -> çatı tipi olasılık dağılımı (11 çatı tipi) |
| `interior_layout.py` | `AIInteriorLayout` — `RoomGenerator` üzerine seed varyasyonu + çeşitlilik skoru ile alternatif plan üretimi |
| `material_predictor.py` | `AIMaterialPredictor` — Duvar/Cam/Metal/Çatı/Yüzey malzeme sınıflandırması |
| `environment_generator.py` | `AIEnvironmentGenerator` — Poisson-disk (Bridson algoritması) ile 11 tip çevre objesi yerleşimi |
| `height_model.py` *(Roadmap V2 - A4)* | `HeightRegressionModel` — stdlib-only, gerçekten `fit()` edilen çok değişkenli doğrusal regresyon; `MLAssistedHeightPredictor` — eğitilmiş model varsa kullanır, yoksa `HeuristicPredictor`'a düşer |

## Hızlı kullanım

```python
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction.footprint_parser import Footprint
from harita.ai_reconstruction import (
    AIBuildingAnalyzer,
    AIRoofPredictor,
    AIEnvironmentGenerator,
    ClimateZone,
    EnvironmentObjectType,
)

poly = Polygon([Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15)])
fp = Footprint(polygon=poly, building_type="apartments")

analysis = AIBuildingAnalyzer().analyze(fp)
print(analysis.floor_count, analysis.architectural_style, analysis.confidence)

roof = AIRoofPredictor().predict("apartments", ClimateZone.ILIMAN)
print(roof.most_likely, roof.top_n(3))

env = AIEnvironmentGenerator(seed=1).generate(
    poly,
    margin_m=20.0,
    object_types=[EnvironmentObjectType.AGAC],
)
print(len(env), "ağaç yerleştirildi")
```

### Harici ML modeli takma (Predictor Protocol)

```python
class MyTrainedModel:
    def predict(self, features: dict) -> dict:
        ...  # kendi ONNX/sklearn modelinizle tahmin döner
        return {"height_m": 24.5, "floor_count": 8, ...}

analyzer = AIBuildingAnalyzer(predictor=MyTrainedModel())
```

## Testler

`../tests/test_phase4_ai_reconstruction.py` — 19 test (Predictor protokol
uyumu, custom predictor enjeksiyonu, çatı olasılık dağılımı toplamı,
iklim etkisi, iç mekan çeşitliliği/alternatifleri, malzeme sınıflandırma,
Poisson-disk minimum mesafe garantisi, seed determinizmi).

```
python -m pytest harita/tests/test_phase4_ai_reconstruction.py -q
```

## Roadmap V2 — A4 derinleştirme: gerçekten eğitilen yükseklik modeli

`predictor=` enjeksiyon noktası artık dışarıdan bir ONNX/sklearn sarmalayıcı
beklemek yerine, kendi içinde **gerçekten `fit()` edilen** (katsayıları
veriden öğrenen) hafif bir doğrusal regresyon modeli sunuyor —
`height_model.py`, stdlib-only (numpy/sklearn gerektirmez).

```python
from harita.ai_reconstruction import (
    train_default_height_model,
    MLAssistedHeightPredictor,
    AIBuildingAnalyzer,
)

model = train_default_height_model()  # sentetik veriyle eğit
predictor = MLAssistedHeightPredictor(model=model)  # egitilmisse kullanir, yoksa heuristic
analysis = AIBuildingAnalyzer(predictor=predictor).analyze(footprint)
print(analysis.height_m, analysis.confidence, analysis.uncertainty_m)
```

- **`HeightRegressionModel.fit(samples, targets)`** — normal denklemler +
  Gauss-Jordan eleme ile çok değişkenli en-küçük-kareler (alan/çevre/aspect
  ratio/kompaktlık özellikleri); `residual_std` ile belirsizlik tahmini.
  Eğitilmeden `predict()` çağrılırsa `ModelNotTrainedError`.
- **`MLAssistedHeightPredictor`** — `model=None` veya eğitilmemiş model
  verildiğinde davranışı saf `HeuristicPredictor` ile **birebir aynıdır**
  (roadmap'in "opsiyonel, yoksa heuristic'e düşer" gereksinimi; regresyon
  testiyle doğrulanır). Eğitilmiş modelde güven skoru, göreli belirsizliğe
  göre kalibre edilir ve heuristic'in tabanının altına asla düşmez.
- **Dürüst sınırlama:** bu ortamda erişilebilir gerçek uydu/ortofoto
  görüntüsünden bina yüksekliği veri seti bulunmuyor. `generate_synthetic_
  training_set()` fiziksel olarak motive edilmiş (alan → kat sayısı →
  yükseklik + gürültü) **sentetik** bir proxy'dir — gerçek veri değildir,
  bu açıkça belgelenmiştir. Model API'si (`fit(samples, targets)`) gerçek
  bir etiketli veri seti sağlandığında değişmeden kullanılabilir.
- **Kabul kriteri raporu** (`benchmark_height_predictors()`, sentetik
  held-out test seti, seed=7, n_train=300/n_test=120):

  | Model | MAE (m) |
  |---|---|
  | `HeuristicPredictor` (mevcut, kural tabanlı) | ~8.5 |
  | `MLAssistedHeightPredictor` (eğitilmiş, bu oturum) | ~7.5 |

  (~%10-15 MAE azalması; tam sayılar `seed`'e göre küçük farklılık
  gösterebilir, bkz. `tests/test_a4_ai_reconstruction_deepening.py::
  test_benchmark_report_shows_measurable_improvement`.)

Testler: `../tests/test_a4_ai_reconstruction_deepening.py` — 11 test
(katsayıların veriye duyarlılığı, eğitilmemiş model hatası, fallback
garantisi, uçtan uca `AIBuildingAnalyzer` entegrasyonu, güven kalibrasyonu,
determinizm, MAE kabul kriteri).

## Roadmap V3 — Faz D12: Yarı-Gerçek Veri Seti + k-fold Çapraz Doğrulama — ✅ Tamamlandı

**Veri seti kaynağı ve lisans notu (roadmap D12 kabul kriteri gereği açıkça
belirtiliyor):** Bu ortamda ağ erişimi kapalıdır (egress proxy
`host_not_allowed` ile reddediyor) — halka açık bir OSM-türevi bina
yüksekliği CSV'si (örn. bir GitHub reposu) indirilemedi. Bu nedenle
roadmap'in kendi öngördüğü dürüst geri düşüş uygulandı: **hiçbir gerçek
üçüncü taraf veri seti kullanılmadı**; yalnızca bu depodaki
`generate_synthetic_training_set()`'in daha zorlu bir varyantı olan
`generate_synthetic_training_set_nonlinear()` kullanıldı (üretici kod,
bu proje kapsamında yazıldı — harici lisans gerektirmez).

`generate_synthetic_training_set_nonlinear()`, doğrusal `Height
RegressionModel`'i gerçekten zorlayacak şekilde üç bileşen ekler:
1. **Log-doyma:** yükseklik `sqrt(alan)` yerine `log(1+alan)` ile ölçekleniyor.
2. **Kategorik etkileşim:** `aspect_ratio`'nun etkisi bina tipine göre
   işaret değiştiriyor (ofis: ince/uzun → daha yüksek; konut: tam tersi).
3. **Heteroskedastik gürültü:** gürültü std'si alanla birlikte büyüyor
   (üst sınırlı).

**`k_fold_cross_validate(samples, targets, k=5)`** — stdlib-only k-fold
çapraz doğrulama (`random.Random(seed).shuffle` ile tek seferlik karıştırma,
`i::k` dilimleme ile fold'lara ayırma; her fold için ayrı `fit()` +
held-out MAE). Kabul kriteri sonucu:

  | Veri seti | Ortalama heuristic MAE | Ortalama eğitilmiş MAE | Eğitilmiş model kaç fold'da daha iyi |
  |---|---|---|---|
  | Doğrusal (`generate_synthetic_training_set`) | ~9.2m | ~6.9m | 5/5 |
  | Doğrusal-olmayan (`generate_synthetic_training_set_nonlinear`) | ~43m | ~35m | 5/5 |

  (tam sayılar `seed`'e göre küçük farklılık gösterebilir). Her iki veri
  setinde de k-fold sonucu, tekil train/test bölünmesiyle **aynı yönde**
  (eğitilmiş model her zaman daha iyi) — roadmap D12'nin kabul kriteri bu.
  Doğrusal-olmayan veri setinde eğitilmiş modelin mutlak MAE'si doğrusal
  veri setine göre belirgin şekilde yüksek (~35m'ye karşı ~6.9m) — model,
  yapısal olarak temsil edemediği log-doyma/kategorik-etkileşim bileşenleri
  yüzünden gerçekten zorlanıyor, bu da veri setinin "daha zor" olma amacını
  doğruluyor.

Testler: `../tests/test_phaseD12_kfold_cv.py` — 8 test (doğrusal/doğrusal-
olmayan veri setlerinde yön tutarlılığı, zorluk karşılaştırması, k-fold
raporunun iç tutarlılığı, hata durumları — k<2, yetersiz örnek —,
determinizm).

## Roadmap V2 — A4'ün son kalan maddesi: opsiyonel sklearn sarmalayıcısı

`sklearn_wrapper.py`, A4'ün "opsiyonel ONNX/sklearn sarmalayıcı örneği"
maddesini kapatır. `predictor=` enjeksiyon noktasına artık **gerçek bir
scikit-learn modeli** (`GradientBoostingRegressor`) de takılabilir —
opsiyonel `pip install harita-modelleme[ml]` bağımlılığı kuruluysa:

```python
from harita.ai_reconstruction import sklearn_wrapper

model = sklearn_wrapper.train_default_sklearn_model()  # sentetik veriyle egit
predictor = sklearn_wrapper.as_ml_assisted_predictor(model)  # MLAssistedHeightPredictor'a koprule
analysis = AIBuildingAnalyzer(predictor=predictor).analyze(footprint)
```

- `scikit-learn` kurulu değilse **sessizce yanlış sonuç üretmek yerine**
  açık `SklearnBackendUnavailable` fırlatılır; ana pakette bu modüle bağımlı
  hiçbir test/kod yoktur (regresyon riski yok).
- `SklearnHeightModel`, `HeightRegressionModel` ile aynı `predict_raw()`
  arayüzüne uyar, dolayısıyla `MLAssistedHeightPredictor`'a doğrudan
  enjekte edilebilir; belirsizlik, ensemble'ın `staged_predict` çıktısındaki
  son-aşama varyansından türetilir.
- **Üç yönlü kabul kriteri raporu** (`benchmark_sklearn_vs_heuristic()`,
  aynı sentetik held-out test seti, seed=7, n_train=300/n_test=120, bu
  oturumda `scikit-learn` gerçekten kurulup çalıştırıldı):

  | Model | MAE (m) |
  |---|---|
  | `HeuristicPredictor` (kural tabanlı) | 8.29 |
  | `HeightRegressionModel` (stdlib OLS) | 7.23 |
  | `SklearnHeightModel` (gradient boosting) | 7.36 |

  Dürüst sonuç: sklearn modeli heuristic'e göre **%11.2** daha iyi, ama bu
  *özellikle doğrusal* sentetik veri setinde stdlib OLS'den (~%2) hafifçe
  daha kötü — beklenen bir durum, çünkü `generate_synthetic_training_set()`
  hedefi zaten yaklaşık doğrusal bir kuralla üretiyor, dolayısıyla
  doğrusal-olmayan bir modelin (gradient boosting) buradan ek kazanç elde
  etmesi için sebep yok. Gerçek uydu/ortofoto verisiyle (doğrusal olmayan
  ilişkiler içerecek şekilde) sklearn'in görece avantajı ortaya çıkması
  beklenir — sonuçlar abartılmadan, olduğu gibi raporlanmıştır.

Testler: `../tests/test_a4b_sklearn_wrapper.py` — 10 test (`scikit-learn`
kurulu değilse çoğu otomatik `skip`, "unavailable" davranışı her zaman
test edilir).

## ROADMAP_V4 — Faz E6: Gerçek ONNX Runtime Entegrasyonu ve Görüntü-Tabanlı Çıkarım ✅ Tamamlandı

`sklearn_wrapper.py`/`height_model.py` yalnızca **sayısal öznitelik**
(footprint alanı, kenar sayısı, komşu yükseklikleri vb.) girdisiyle
çalışır — gerçek bir **görüntü** (uydu/ortofoto kırpması) alıp CNN-tarzı
bir modelle çıkarım yapan bir yol yoktu. `pyproject.toml`'daki
`onnxruntime` bağımlılığı (`[ml]` extra) tanımlıydı ama hiç kullanılmıyordu.

`onnx_predictor.py` bunu kapatır:

- **`ImageBasedPredictor(model_path=None, roof_types=..., fallback=None,
  providers=None)`** — `model_path` verilirse `onnxruntime.InferenceSession`
  ile gerçek bir `.onnx` modeli yükler; girdi/çıktı tensor adları ve
  şekli modelin kendi metadata'sından okunur (sabit mimari varsayılmaz).
- **`predict_image(image) -> OnnxInferenceResult`** — gerçek graph
  execution çalıştırır: `height_m` (çıktı[0], skaler) ve varsa
  `roof_type`/`roof_confidence` (çıktı[1], softmax uygulanmış sınıf
  logit'leri, `roof_types` listesine indekslenir). Şekil uyuşmazlığında
  `OnnxModelShapeError`.
- **`predict(features) -> dict`** — `Predictor` Protocol uyumluluğu için;
  öznitelik-tabanlı girdi görüntü DEĞİLDİR, bu yüzden her zaman
  `fallback`'e (varsayılan `MLAssistedHeightPredictor()`) devreder —
  mevcut enjeksiyon noktalarına (`AIBuildingAnalyzer(predictor=...)` vb.)
  hiçbir değişiklik yapmadan takılabilir.
- `onnxruntime` kurulu değilse: import zamanında hiçbir hata yok, yalnızca
  `model_path` verilip gerçek çıkarım istendiğinde açık
  `OnnxBackendUnavailable` fırlatılır — `model_path=None` ile
  öznitelik-tabanlı fallback her zaman çalışır (roadmap'in "sessizce
  düşer" ilkesi).
- `is_available()` / `onnxruntime_version()` — sklearn_wrapper'daki
  aynı desenin ONNX karşılığı.

Bu ortamda `onnxruntime` gerçekten kurulu (1.24.4) — testler gerçek bir
sentetik `.onnx` modeli (test-zamanı `onnx` paketiyle elle inşa edilmiş,
eğitim gerektirmeyen sabit bir hesaplama grafiği: `height = mean(image)*30+3`,
`roof_logits = GlobalAveragePool(image) @ sabit_ağırlık`) yükleyip gerçek
ONNX Runtime çıkarımı çalıştırarak doğrular. `onnx` (yalnızca test-fixture
üretimi için, üretim kodunun bağımlılığı değil) kurulu değilse o testler
`pytest.skip` ile atlanır; `onnxruntime` kurulu değilmiş gibi davranan
senaryolar `monkeypatch` ile ayrıca test edilir.

Testler: `../tests/test_phaseE6_onnx_predictor.py` — 14 test (kullanılabilirlik,
model-yok fallback, gerçek çıkarım + girdiye göre çıktı değişimi, şekil
doğrulama, özel çatı-etiketi eşlemesi, `OnnxBackendUnavailable` yolları).

`from harita import ImageBasedPredictor, OnnxBackendUnavailable,
OnnxInferenceResult, OnnxModelShapeError, ONNX_DEFAULT_ROOF_TYPES` ile
tek giriş noktasından erişilebilir (C3 ilkesiyle tutarlı).
