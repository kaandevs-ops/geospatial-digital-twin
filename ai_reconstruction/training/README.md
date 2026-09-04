# ai_reconstruction/training — Sıfırdan Eğitim + Fine-Tuning

ROADMAP_V5 - Faz T1. `onnx_predictor.py`/`sklearn_wrapper.py` yalnızca
**hazır** bir modeli çalıştırıyordu (inference); bu paket eksik olan
parçayı — modeli gerçekten **eğitme** kodunu — ekler.

## Neden iki backend (torch + tensorflow)?

Hangi framework kuruluysa o kullanılır (`backend="auto"`), ya da açıkça
`"torch"` / `"tensorflow"` seçilebilir. İkisi de:
- aynı `TrainingConfig` şemasını okur (`config.py`),
- aynı manifest formatını kullanır (`dataset.py`, bkz. `DATASET_GUIDE.md`),
- aynı çıktı sözleşmesiyle ONNX export eder — üretilen `.onnx` dosyası
  doğrudan mevcut `ai_reconstruction/onnx_predictor.py::ImageBasedPredictor`
  ile çalışır, hiçbir değişiklik gerekmez.

Böylece hangi backend'i seçersen seç, geri kalan proje (Predictor
Protocol, `AIBuildingAnalyzer`, export/rapor kodu) **hiç değişmeden**
sonucu kullanabilir.

## Kurulum

```bash
# Yalnızca biri yeterli:
pip install harita-modelleme[train-torch]        # torch + torchvision
pip install harita-modelleme[train-tensorflow]   # tensorflow + tf2onnx

# Ayrıca Pillow (görüntü decode) — ikisi için de gerekli:
pip install harita-modelleme[train]              # sadece Pillow
```

> `pyproject.toml`'daki extra adları için proje köküne bakın; henüz
> eklenmediyse `pip install torch torchvision Pillow` /
> `pip install tensorflow tf2onnx Pillow` ile manuel kurulum da olur.

## Sıfırdan eğitim (scratch)

```bash
python -m harita.ai_reconstruction.training.train init-config cfg.json --manifest data/manifest.csv
# cfg.json'u aç, images_root / task / epochs vs. düzenle
python -m harita.ai_reconstruction.training.train fit cfg.json
```

## Fine-tuning (ön-eğitimli backbone)

```bash
python -m harita.ai_reconstruction.training.train init-config cfg.json --manifest data/manifest.csv --finetune
```

`cfg.json` içinde önemli alanlar:

```json
{
  "mode": "finetune",
  "pretrained_backbone": "mobilenet_v3_small",
  "freeze_backbone": true,
  "unfreeze_last_n_blocks": 2,
  "learning_rate": 0.001,
  "finetune_learning_rate": 0.0001
}
```

- `freeze_backbone=true` (önerilen başlangıç, özellikle az veri varken):
  yalnızca yeni eklenen yükseklik/çatı başlığı eğitilir.
- `freeze_backbone=false`: backbone'un son `unfreeze_last_n_blocks`
  bloğu da düşük `finetune_learning_rate` ile eğitime dahil olur —
  daha fazla veri (birkaç bin+) olduğunda daha iyi sonuç verebilir.

Desteklenen `pretrained_backbone` değerleri: `mobilenet_v3_small`,
`mobilenet_v3_large`, `resnet18`/`resnet34` (yalnızca torch),
`resnet50` (yalnızca tf), `efficientnet_b0`.

## Eğitilen modeli projeye bağlama

```python
from harita.ai_reconstruction.onnx_predictor import ImageBasedPredictor

predictor = ImageBasedPredictor(model_path="runs/default/model.onnx")
result = predictor.predict_image(image_tensor)  # -> OnnxInferenceResult
```

Bunu `AIBuildingAnalyzer`'a `predictor=` parametresiyle enjekte ederek
tüm mevcut rapor/export akışının (CityJSON, IFC vb.) yeni modeli
kullanmasını sağlayabilirsin — kod değişikliği gerekmez, sadece
enjeksiyon noktası kullanılır (bkz. `predictor.py`).

## Dosyalar

| Dosya | İçerik |
|---|---|
| `config.py` | `TrainingConfig` — tüm eğitim ayarları (framework'ten bağımsız) |
| `dataset.py` | Manifest okuma/bölme, framework'ten bağımsız `ManifestDataset` |
| `backends/base.py` | `TrainerBackend` protokolü + `resolve_backend()` |
| `backends/torch_backend.py` | PyTorch: scratch CNN + torchvision fine-tune + ONNX export |
| `backends/tf_backend.py` | TensorFlow/Keras karşılığı (tf2onnx ile export) |
| `train.py` | CLI (`init-config`, `fit`) |
| `DATASET_GUIDE.md` | Veri seti nasıl bulunur/oluşturulur — **veri yoksa buradan başla** |

## Sınırlamalar (dürüstçe)

- Bu paket **eğitim altyapısını** sağlar; gerçek bir veri seti veya
  gerçek bir eğitilmiş ağırlık dosyası **içermez** (repo'ya dahil
  değil — lisans/boyut belirsizliği, bkz. `onnx_predictor.py`'daki aynı
  gerekçe).
  Bu ortamda ağ erişimi kapalı olduğundan, `torch`/`tensorflow`
  kurulumu ve uçtan-uca bir eğitim koşusu bu oturumda **doğrulanamadı** —
  kod, dokümante edilen API'lere göre yazıldı ama kendi ortamınızda
  (`pip install ...` sonrası) ilk çalıştırmada küçük framework-sürümüne
  özgü düzeltmeler gerekebilir.
- `tf_backend.py`'deki iki-aşamalı fine-tune (önce head, sonra backbone),
  torch tarafındaki tek-optimizer çoklu-LR-grubu kadar ince ayarlı
  değildir — Keras'ta gerçek katman-bazlı farklı LR için özel bir
  `train_step` yazmak gerekir; bu basitleştirilmiş iki-aşama yaklaşımı
  pratikte yeterli ama not edilmeli.
