"""
Manifest Tabanlı Veri Seti Yükleyici
======================================

ROADMAP_V5 - Faz T1.

Gerçek eğitim verisi, `DATASET_GUIDE.md`'de tanımlanan basit bir
**manifest** dosyasıyla (CSV veya JSONL) tanımlanır:

    image_path,height_m,roof_type,building_type
    tiles/000123.jpg,14.5,gable,house
    tiles/000124.jpg,32.0,flat,office
    ...

Bu modül, framework'ten (torch/tensorflow) bağımsız olarak:
  1. Manifesti okur ve doğrular (`load_manifest`),
  2. train/val/test'e böler (`split_manifest`) — `TrainingConfig.seed` ile
     tekrarlanabilir,
  3. Her satırı `(image_array, targets_dict)` çiftine çeviren stdlib+Pillow
     tabanlı bir `ManifestDataset` sağlar.

`Pillow` (PIL) tek "opsiyonel" bağımlılıktır (`pip install
harita-modelleme[train]` ile gelir) — resim decode/resize için. Kurulu
değilse, `ManifestDataset` oluşturma anında açık `PillowNotAvailable`
fırlatır (roadmap'in "yoksa sessizce ama açıkça düşer" ilkesiyle uyumlu;
burada gerçek bir resim yüklemeden eğitim yapmanın anlamı olmadığı için
"düşecek" bir stdlib alternatif yok, sadece net bir hata var).

Torch/TF'e özgü `Dataset`/`tf.data.Dataset` sarmalayıcıları
`backends/torch_backend.py` ve `backends/tf_backend.py` içinde, bu
modüldeki `ManifestDataset`'i sararak tanımlanır — böylece decode/resize/
augment mantığı **tek yerde** yaşar ve iki framework arasında tutarlıdır.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    from PIL import Image

    _PIL_AVAILABLE = True
except ImportError:  # pragma: no cover - ortam bağımlı
    Image = None  # type: ignore[assignment]
    _PIL_AVAILABLE = False


class PillowNotAvailable(RuntimeError):
    """`Pillow` kurulu değilken görüntü decode edilmeye çalışılırsa."""

    def __init__(self) -> None:
        super().__init__(
            "Görüntü tabanlı eğitim için Pillow gerekli: "
            "`pip install harita-modelleme[train]` (veya `pip install Pillow`)."
        )


class ManifestError(ValueError):
    """Manifest dosyası eksik/bozuk sütun içerdiğinde."""


REQUIRED_COLUMNS = ("image_path",)
OPTIONAL_NUMERIC_COLUMNS = ("height_m",)
OPTIONAL_CATEGORICAL_COLUMNS = ("roof_type", "building_type")


@dataclass(slots=True)
class ManifestRow:
    image_path: str
    height_m: float | None = None
    roof_type: str | None = None
    building_type: str | None = None
    extra: dict[str, Any] | None = None


def load_manifest(manifest_path: str | Path) -> list[ManifestRow]:
    """CSV veya JSONL manifest dosyasını okur.

    Uzantı `.jsonl`/`.ndjson` ise her satır bağımsız bir JSON nesnesi
    olarak, aksi halde CSV (başlık satırı zorunlu) olarak okunur.
    """
    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest bulunamadı: {path}")

    rows: list[ManifestRow] = []
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        with path.open(encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ManifestError(f"Satır {line_no}: geçersiz JSON ({exc})") from exc
                rows.append(_row_from_dict(obj, line_no))
    else:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "image_path" not in reader.fieldnames:
                raise ManifestError("CSV manifest başlık satırında 'image_path' sütunu gerekli")
            for line_no, obj in enumerate(reader, start=2):
                rows.append(_row_from_dict(obj, line_no))

    if not rows:
        raise ManifestError(f"Manifest boş: {path}")
    return rows


def _row_from_dict(obj: dict[str, Any], line_no: int) -> ManifestRow:
    if not obj.get("image_path"):
        raise ManifestError(f"Satır {line_no}: 'image_path' zorunlu")
    height_raw = obj.get("height_m")
    height = float(height_raw) if height_raw not in (None, "") else None
    known = {"image_path", "height_m", "roof_type", "building_type"}
    extra = {k: v for k, v in obj.items() if k not in known} or None
    return ManifestRow(
        image_path=str(obj["image_path"]),
        height_m=height,
        roof_type=(obj.get("roof_type") or None),
        building_type=(obj.get("building_type") or None),
        extra=extra,
    )


def split_manifest(
    rows: list[ManifestRow], val_split: float, test_split: float, seed: int = 42,
) -> tuple[list[ManifestRow], list[ManifestRow], list[ManifestRow]]:
    """Satırları train/val/test'e rastgele (ama tekrarlanabilir) böler."""
    if not 0.0 <= val_split < 1.0 or not 0.0 <= test_split < 1.0:
        raise ValueError("val_split ve test_split [0, 1) aralığında olmalı")
    if val_split + test_split >= 1.0:
        raise ValueError("val_split + test_split < 1.0 olmalı (train için pay kalmalı)")

    shuffled = rows[:]
    random.Random(seed).shuffle(shuffled)
    n = len(shuffled)
    n_val = round(n * val_split)
    n_test = round(n * test_split)
    val = shuffled[:n_val]
    test = shuffled[n_val:n_val + n_test]
    train = shuffled[n_val + n_test:]
    if not train:
        raise ManifestError(
            f"train seti boş kaldı (n={n}, val={n_val}, test={n_test}) — "
            "daha fazla veri toplayın veya split oranlarını düşürün"
        )
    return train, val, test


class ManifestDataset:
    """Framework'ten bağımsız, indekslenebilir veri seti.

    `__getitem__` her zaman `(image: PIL.Image.Image, targets: dict)` döner
    — resize/normalize/tensor-dönüşümü **yapmaz**; bunlar backend'e özgü
    sarmalayıcılarda (`backends/torch_backend.py::TorchManifestDataset` vb.)
    yapılır, çünkü her framework'ün beklediği tensor düzeni/tipi farklıdır.
    """

    def __init__(self, rows: list[ManifestRow], images_root: str | Path = ".") -> None:
        if not _PIL_AVAILABLE:
            raise PillowNotAvailable()
        self.rows = rows
        self.images_root = Path(images_root)

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, idx: int) -> tuple["Image.Image", dict[str, Any]]:
        row = self.rows[idx]
        img_path = self.images_root / row.image_path
        if not img_path.exists():
            raise FileNotFoundError(f"Görüntü bulunamadı: {img_path}")
        image = Image.open(img_path).convert("RGB")
        targets = {
            "height_m": row.height_m,
            "roof_type": row.roof_type,
            "building_type": row.building_type,
        }
        return image, targets

    def __iter__(self) -> Iterator[tuple["Image.Image", dict[str, Any]]]:
        for i in range(len(self)):
            yield self[i]


def dataset_summary(rows: list[ManifestRow]) -> dict[str, Any]:
    """Hızlı sağlık kontrolü: kaç satır, kaçında etiket eksik, sınıf dağılımı."""
    n = len(rows)
    n_with_height = sum(1 for r in rows if r.height_m is not None)
    roof_counts: dict[str, int] = {}
    for r in rows:
        if r.roof_type:
            roof_counts[r.roof_type] = roof_counts.get(r.roof_type, 0) + 1
    heights = [r.height_m for r in rows if r.height_m is not None]
    return {
        "n_rows": n,
        "n_with_height_label": n_with_height,
        "height_label_coverage": round(n_with_height / n, 3) if n else 0.0,
        "roof_type_distribution": roof_counts,
        "height_min": min(heights) if heights else None,
        "height_max": max(heights) if heights else None,
        "height_mean": round(sum(heights) / len(heights), 2) if heights else None,
    }
