"""
Animasyon Video/GIF Export — ROADMAP_V9 Katman 9 madde 7
============================================================

Roadmap metni (Katman 9 madde 7): "`visualization/` ve `render_engine/
demo_export.py` zaten var — animasyon kaydını doğrudan paylaşılabilir
video/GIF olarak dışa aktarma, sunum/rapor senaryoları için."

**Dürüstlük notu (bu oturumda düzeltilen bir eksik):** `render_engine/
demo_export.py` aslında Faz 15'in statik `scene.json` demo betiğidir —
tahliye/simülasyon animasyonuyla hiçbir ilgisi yok. Roadmap'in orijinal
metni bu ayrımı net yapmamıştı; `analysis_engine/decision_support.py`'nin
Faz XI docstring'i de "render_engine/animation_export.py'de ayrı bir
modülde" diyerek bu modülün varlığını varsaymıştı ama modül o ana kadar
hiç yazılmamıştı. Bu modül o boşluğu kapatır.

Bu modül **yeni bir simülasyon/animasyon motoru yazmaz** (roadmap ilkesi
#2): girdi olarak doğrudan O.1'in `mobility.simulation_recorder.
SimulationRecorder` (veya ham `list[Keyframe]`) çıktısını alır — agent
pozisyonlarını/durumlarını üretmez, yalnızca **var olan** keyframe
verisini görselleştirilebilir bir dışa aktarıma çevirir. Renk kodlaması,
O.2/Katman 2.4'ün kendi notuyla birebir tutarlıdır: "yeşil=hareket
ediyor, kırmızı=beklemede/panik".

İki dışa aktarım yolu sağlanır:
1. **SVG kare dizisi** (`export_keyframes_svg_sequence`) — yalnızca
   stdlib, harici bağımlılık yok, her zaman çalışır. Sunum yazılımına
   (PowerPoint/Keynote) veya web'e tek tek karelerin gömülmesi için
   yeterlidir.
2. **Animasyonlu GIF** (`export_keyframes_gif`) — `export/vector_2d.py`
   ile aynı desende (bkz. o modülün `PIL` tembel/opsiyonel import'u),
   Pillow varsa kullanılır; yoksa açık, anlaşılır bir hata verir (sessizce
   bozuk bir dosya üretmez).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..mobility.simulation_recorder import AgentFrameState, Keyframe, SimulationRecorder

#: O.2 / Katman 2.4'ün kendi renk kodlaması ile birebir tutarlı
#: ("yeşil=hareket ediyor, kırmızı=beklemede/panik").
AGENT_STATE_COLORS: dict[AgentFrameState, str] = {
    AgentFrameState.MOVING: "#2ecc71",  # yeşil
    AgentFrameState.WAITING: "#f39c12",  # turuncu ("beklemede" — panikten ayırt edilebilir tonda)
    AgentFrameState.PANIC: "#e74c3c",  # kırmızı
    AgentFrameState.EVACUATED: "#95a5a6",  # gri (sahneden çıkmış)
}

DEFAULT_AGENT_RADIUS_PX = 4.0


class AnimationExportError(Exception):
    """Dışa aktarım hatası (boş keyframe listesi, eksik opsiyonel bağımlılık vb.)."""


@dataclass(slots=True)
class ExportBounds:
    """Keyframe'lerdeki tüm agent pozisyonlarını kapsayan dünya-koordinat
    kutusu — SVG/GIF piksel dönüşümü için kullanılır."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    @property
    def width(self) -> float:
        return max(self.max_x - self.min_x, 1e-6)

    @property
    def height(self) -> float:
        return max(self.max_y - self.min_y, 1e-6)


def _resolve_keyframes(source: SimulationRecorder | Sequence[Keyframe]) -> list[Keyframe]:
    keyframes = source.keyframes if isinstance(source, SimulationRecorder) else list(source)
    if not keyframes:
        raise AnimationExportError(
            "dışa aktarılacak keyframe yok (boş kayıt) — önce simülasyonu "
            "bir SimulationRecorder ile koşturun."
        )
    return keyframes


def compute_export_bounds(keyframes: Sequence[Keyframe], *, margin: float = 1.0) -> ExportBounds:
    """Tüm karelerdeki tüm agent'ları kapsayan sınır kutusunu (+ kenar
    payı) hesaplar — sabit bir dünya-koordinat varsayımı yapılmaz, her
    koşum kendi sahnesine göre ölçeklenir."""
    xs: list[float] = []
    ys: list[float] = []
    for kf in keyframes:
        for snap in kf.agents:
            xs.append(snap.x)
            ys.append(snap.y)
    if not xs:
        return ExportBounds(0.0, 0.0, 1.0, 1.0)
    return ExportBounds(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)


def _world_to_px(
    x: float, y: float, bounds: ExportBounds, width_px: int, height_px: int
) -> tuple[float, float]:
    px = (x - bounds.min_x) / bounds.width * width_px
    # Y ekseni SVG/görüntüde aşağı doğru arttığından çevrilir (dünya
    # koordinatında yukarı = +y varsayımıyla tutarlı, top-down kesit
    # görünümü notuyla uyumlu — Katman 2.4 madde 4).
    py = height_px - (y - bounds.min_y) / bounds.height * height_px
    return px, py


def render_keyframe_svg(
    keyframe: Keyframe,
    bounds: ExportBounds,
    *,
    width_px: int = 800,
    height_px: int = 600,
    agent_radius_px: float = DEFAULT_AGENT_RADIUS_PX,
    background: str = "#111318",
) -> str:
    """Tek bir keyframe'i top-down (üstten kesit) SVG kareye çevirir —
    yalnızca stdlib, `visualize` widget'ının SVG kuralına uygun (renkler
    CSS/hex sabiti, harici kaynak yok)."""
    circles = []
    for snap in keyframe.agents:
        cx, cy = _world_to_px(snap.x, snap.y, bounds, width_px, height_px)
        color = AGENT_STATE_COLORS.get(snap.state, "#ffffff")
        circles.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{agent_radius_px}" fill="{color}" />'
        )
    body = "\n  ".join(circles)
    return (
        f'<svg viewBox="0 0 {width_px} {height_px}" xmlns="http://www.w3.org/2000/svg">\n'
        f'  <rect width="{width_px}" height="{height_px}" fill="{background}" />\n'
        f"  {body}\n"
        f'  <text x="8" y="20" fill="#eeeeee" font-size="14" font-family="sans-serif">'
        f"t={keyframe.t:.1f}s</text>\n"
        f"</svg>"
    )


def export_keyframes_svg_sequence(
    source: SimulationRecorder | Sequence[Keyframe],
    output_dir: str | Path,
    *,
    width_px: int = 800,
    height_px: int = 600,
    agent_radius_px: float = DEFAULT_AGENT_RADIUS_PX,
    filename_prefix: str = "frame",
) -> list[Path]:
    """Tüm keyframe'leri sıra numaralı `.svg` dosyaları olarak
    `output_dir`'a yazar. Harici bağımlılık yok — her ortamda çalışır.
    Dönüş: yazılan dosya yollarının (zaman sırasıyla) listesi."""
    keyframes = _resolve_keyframes(source)
    bounds = compute_export_bounds(keyframes)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    digits = max(4, len(str(len(keyframes))))
    paths: list[Path] = []
    for i, kf in enumerate(keyframes):
        svg = render_keyframe_svg(
            kf,
            bounds,
            width_px=width_px,
            height_px=height_px,
            agent_radius_px=agent_radius_px,
        )
        path = out_dir / f"{filename_prefix}_{i:0{digits}d}.svg"
        path.write_text(svg, encoding="utf-8")
        paths.append(path)
    return paths


def export_keyframes_gif(
    source: SimulationRecorder | Sequence[Keyframe],
    output_path: str | Path,
    *,
    width_px: int = 800,
    height_px: int = 600,
    agent_radius_px: float = DEFAULT_AGENT_RADIUS_PX,
    frame_duration_ms: int = 150,
    background: tuple[int, int, int] = (17, 19, 24),
    loop: int = 0,
) -> Path:
    """Keyframe'leri tek bir animasyonlu GIF'e render eder — roadmap'in
    "sunum/rapor senaryoları için paylaşılabilir video/GIF" maddesinin
    doğrudan karşılığı.

    `export/vector_2d.py`'nin kendi deseni tekrar kullanıldı: `Pillow`
    yalnızca burada, çağrıldığında, tembel olarak import edilir — modülün
    kendisi Pillow kurulu olmasa bile import edilebilir (yalnızca bu
    fonksiyon çağrıldığında, kurulu değilse **açık bir hata** verir,
    sessizce bozuk dosya üretmez).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover - ortam bağımlı
        raise AnimationExportError(
            "GIF dışa aktarımı için Pillow gerekli ama kurulu değil "
            "(`pip install Pillow`). Bağımlılıksız alternatif için "
            "export_keyframes_svg_sequence() kullanılabilir."
        ) from exc

    keyframes = _resolve_keyframes(source)
    bounds = compute_export_bounds(keyframes)

    frames = []
    for kf in keyframes:
        img = Image.new("RGB", (width_px, height_px), color=background)
        draw = ImageDraw.Draw(img)
        for snap in kf.agents:
            cx, cy = _world_to_px(snap.x, snap.y, bounds, width_px, height_px)
            color = AGENT_STATE_COLORS.get(snap.state, "#ffffff")
            r = agent_radius_px
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        frames.append(img)

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        out_path,
        save_all=True,
        append_images=frames[1:],
        duration=frame_duration_ms,
        loop=loop,
        format="GIF",
    )
    return out_path


__all__ = [
    "AGENT_STATE_COLORS",
    "DEFAULT_AGENT_RADIUS_PX",
    "AnimationExportError",
    "ExportBounds",
    "compute_export_bounds",
    "render_keyframe_svg",
    "export_keyframes_svg_sequence",
    "export_keyframes_gif",
]
