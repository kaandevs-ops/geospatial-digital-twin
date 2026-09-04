"""
ONNX Runtime Entegrasyonu — Görüntü-Tabanlı Çıkarım
======================================================

ROADMAP_V4 Faz E6: "AI Reconstruction: Gerçek ONNX Runtime Entegrasyonu ve
Görüntü-Tabanlı Çıkarım".

`sklearn_wrapper.py` sayısal öznitelik-tabanlı (footprint alanı, kenar
sayısı vb.) tahmin yapar; bu modül ise gerçek bir **görüntü** (uydu/ortofoto
kırpması) girdisi alıp önceden eğitilmiş bir ONNX modeliyle (CNN veya
başka bir mimari) bina yüksekliği + çatı tipi tahmini yapan bir yol açar.

Tasarım kararları
------------------
- `onnxruntime` opsiyoneldir (`pyproject.toml` içindeki `[ml]` extra'sında
  zaten tanımlı). Kurulu değilse hiçbir import-zamanı hatası fırlatılmaz —
  yalnızca gerçek bir görüntü tahmini *istendiğinde* açık
  `OnnxBackendUnavailable` fırlatılır.
- `ImageBasedPredictor`, `predictor.Predictor` Protocol'üne uyar
  (`predict(features: dict) -> dict`) — bu sayede mevcut
  `MLAssistedHeightPredictor`/`AIBuildingAnalyzer` enjeksiyon noktalarına
  değişiklik yapılmadan takılabilir. Ancak asıl yeni yeteneği
  `predict_image()` metodudur (görüntü-tensor girdisi).
- Model kurulu değilse veya `model_path=None` ise, `predict()`/
  `predict_image()` çağrıları **sessizce** enjekte edilmiş `fallback`
  (varsayılan: `MLAssistedHeightPredictor`) tahmincisine düşer — mevcut
  davranış hiçbir zaman bozulmaz (roadmap'in "kurulu değilse sessizce
  düşer" ilkesi).
- Giriş/çıkış tensor şekilleri modelin kendi `get_inputs()/get_outputs()`
  metadata'sından okunur ve doğrulanır — sabit kodlanmış bir mimari
  varsayılmaz, herhangi bir uyumlu ONNX modeli (giriş: NCHW görüntü
  tensor'u; çıkış[0]: skaler yükseklik, çıkış[1] (opsiyonel): çatı-tipi
  logit'leri) kullanılabilir.
- Gerçek bir eğitilmiş model dosyası bu pakete gömülü değildir (ağırlık
  dosyası boyutu ve lisans belirsizliği nedeniyle) — testler, `onnx`
  paketi ortamda mevcutsa kendi ürettiği minik sentetik bir modeli
  kullanır; `onnx` paketi de yoksa o testler `pytest.skip` ile atlanır
  (yalnızca *model üretimi* için gereken `onnx`, *çalışma zamanı
  çıkarımı* için gereken `onnxruntime`'dan ayrı bir bağımlılıktır ve
  projenin `[ml]` extra'sına dahil değildir — yalnızca test-zamanı
  kolaylığıdır, üretim kodu asla `onnx` paketini import etmez).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .height_model import MLAssistedHeightPredictor
from .predictor import Predictor

try:
    import numpy as np  # type: ignore[import-untyped]

    _NUMPY_AVAILABLE = True
except ImportError:  # pragma: no cover - ortam bağımlı
    np = None  # type: ignore[assignment]
    _NUMPY_AVAILABLE = False

try:
    import onnxruntime as ort  # type: ignore[import-untyped]

    _ONNXRUNTIME_AVAILABLE = True
    _ONNXRUNTIME_VERSION = ort.__version__
except ImportError:  # pragma: no cover - ortam bağımlı
    ort = None  # type: ignore[assignment]
    _ONNXRUNTIME_AVAILABLE = False
    _ONNXRUNTIME_VERSION = None


DEFAULT_ROOF_TYPES: tuple[str, ...] = (
    "flat",
    "gable",
    "hip",
    "mansard",
    "shed",
    "gambrel",
    "dome",
)


class OnnxBackendUnavailable(RuntimeError):
    """`onnxruntime` kurulu değilken veya model yüklenmemişken gerçek
    görüntü-tabanlı ONNX çıkarımı istendiğinde fırlatılır."""


class OnnxModelShapeError(ValueError):
    """Verilen görüntü tensor'u, modelin beklediği giriş şekliyle uyuşmuyor."""


def is_available() -> bool:
    """`onnxruntime` bu ortamda kurulu mu?"""
    return _ONNXRUNTIME_AVAILABLE


def onnxruntime_version() -> str | None:
    """Kurulu onnxruntime sürümü, yoksa None."""
    return _ONNXRUNTIME_VERSION


def _require_onnxruntime() -> None:
    if not _ONNXRUNTIME_AVAILABLE:
        raise OnnxBackendUnavailable(
            "onnxruntime kurulu değil. Görüntü-tabanlı ONNX çıkarımı için "
            "`pip install harita-modelleme[ml]` (veya doğrudan "
            "`pip install onnxruntime>=1.16`) gerekir. Alternatif: "
            "`ImageBasedPredictor(model_path=None)` ile öznitelik-tabanlı "
            "fallback tahminciyi (MLAssistedHeightPredictor / heuristic) "
            "kullanabilirsiniz."
        )
    if not _NUMPY_AVAILABLE:  # pragma: no cover - onnxruntime zaten numpy'a bağımlı
        raise OnnxBackendUnavailable("numpy kurulu değil (onnxruntime çalışma zamanı bağımlılığı).")


@dataclass(frozen=True)
class OnnxInferenceResult:
    """`ImageBasedPredictor.predict_image()` çıktısı."""

    height_m: float
    roof_type: str | None
    roof_confidence: float | None
    source: str
    raw_outputs: tuple[Any, ...] = field(default_factory=tuple, repr=False)


class ImageBasedPredictor:
    """`onnxruntime` ile önceden eğitilmiş bir modeli yükleyip görüntü
    tensor'undan bina yüksekliği/çatı tipi tahmini yapan Predictor.

    Parametreler
    ------------
    model_path:
        `.onnx` model dosyasının yolu. `None` ise yalnızca `fallback`
        üzerinden öznitelik-tabanlı tahmin yapılabilir; `predict_image()`
        çağrısı `OnnxBackendUnavailable` fırlatır.
    roof_types:
        Modelin ikinci çıktısının (varsa) sınıf indekslerine karşılık
        gelen çatı-tipi etiketleri. Varsayılan: `DEFAULT_ROOF_TYPES`.
    fallback:
        `model_path=None` olduğunda veya `predict()` (öznitelik-tabanlı,
        Protocol uyumluluğu için) çağrıldığında kullanılacak tahminci.
        Varsayılan: yeni bir `MLAssistedHeightPredictor()` (kendi içinde
        sklearn varsa onu, yoksa stdlib OLS/heuristic'i kullanır).
    providers:
        onnxruntime execution provider listesi. Varsayılan: yalnızca CPU
        (bu ortamda GPU donanımı garanti değil — bkz. Faz E10).
    """

    def __init__(
        self,
        model_path: str | None = None,
        *,
        roof_types: Sequence[str] = DEFAULT_ROOF_TYPES,
        fallback: Predictor | None = None,
        providers: Sequence[str] | None = None,
    ) -> None:
        self._model_path = model_path
        self._roof_types = tuple(roof_types)
        self._fallback: Predictor = fallback or MLAssistedHeightPredictor()
        self._session: Any = None
        self._input_name: str | None = None
        self._input_shape: tuple[int | str | None, ...] | None = None
        self._output_names: list[str] = []

        if model_path is not None:
            _require_onnxruntime()
            self._session = ort.InferenceSession(
                model_path,
                providers=list(providers) if providers else ["CPUExecutionProvider"],
            )
            model_input = self._session.get_inputs()[0]
            self._input_name = model_input.name
            self._input_shape = tuple(model_input.shape)
            self._output_names = [o.name for o in self._session.get_outputs()]

    @property
    def loaded(self) -> bool:
        """Gerçek bir ONNX oturumu yüklü mü (yoksa yalnızca fallback modda mı)."""
        return self._session is not None

    @property
    def input_shape(self) -> tuple[int | str | None, ...] | None:
        return self._input_shape

    def _validate_image(self, image: Any) -> Any:
        arr = np.asarray(image, dtype=np.float32)
        if self._input_shape is not None:
            expected_rank = len(self._input_shape)
            if arr.ndim != expected_rank:
                raise OnnxModelShapeError(
                    f"Beklenen tensor rank'i {expected_rank} (şekil="
                    f"{self._input_shape}), verilen görüntü rank'i {arr.ndim} "
                    f"(şekil={arr.shape})."
                )
            for expected_dim, actual_dim in zip(self._input_shape, arr.shape):
                if isinstance(expected_dim, int) and expected_dim != actual_dim:
                    raise OnnxModelShapeError(
                        f"Beklenen tensor şekli {self._input_shape}, verilen "
                        f"görüntü şekli {arr.shape} ile uyuşmuyor "
                        f"(boyut {expected_dim} != {actual_dim})."
                    )
        return arr

    def predict_image(self, image: Any) -> OnnxInferenceResult:
        """Bir görüntü-tensor'undan (NCHW veya modelin beklediği şekilde)
        gerçek ONNX çıkarımıyla yükseklik/çatı tipi tahmini üretir.

        `model_path` verilmediyse (`loaded is False`) `OnnxBackendUnavailable`
        fırlatır — bu metod yalnızca gerçek görüntü-tabanlı çıkarım için
        vardır, öznitelik-tabanlı fallback için `predict()` kullanılmalıdır.
        """
        if not self.loaded:
            raise OnnxBackendUnavailable(
                "Bu ImageBasedPredictor bir model_path ile oluşturulmadı, "
                "gerçek görüntü-tabanlı ONNX çıkarımı yapılamaz. "
                "Öznitelik-tabanlı fallback için predict() kullanın."
            )
        arr = self._validate_image(image)
        outputs = self._session.run(self._output_names or None, {self._input_name: arr})

        height = float(np.asarray(outputs[0]).reshape(-1)[0])
        roof_type: str | None = None
        roof_confidence: float | None = None
        if len(outputs) > 1:
            logits = np.asarray(outputs[1]).reshape(-1)
            if logits.size > 0:
                shifted = logits - np.max(logits)
                probs = np.exp(shifted) / np.sum(np.exp(shifted))
                best_idx = int(np.argmax(probs))
                roof_confidence = float(probs[best_idx])
                if 0 <= best_idx < len(self._roof_types):
                    roof_type = self._roof_types[best_idx]

        return OnnxInferenceResult(
            height_m=height,
            roof_type=roof_type,
            roof_confidence=roof_confidence,
            source="onnx",
            raw_outputs=tuple(outputs),
        )

    def predict(self, features: dict) -> dict:
        """`Predictor` Protocol uyumluluğu — öznitelik-tabanlı (görüntü
        DEĞİL) tahmin ister; her zaman `fallback`'e devreder. Görüntü
        girdisi olduğunda `predict_image()` kullanılmalıdır."""
        result = dict(self._fallback.predict(features))
        result.setdefault("source", "fallback_heuristic")
        return result
