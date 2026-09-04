#!/usr/bin/env python3
"""Roadmap V7 — gerçek **piksel-tabanlı** görsel regresyon testi.

`scripts/visual_regression.py` ("yapısal imza") ile aynı amaca hizmet
eder ama farklı, tamamlayıcı bir katmanda: `render_engine.software_rasterizer`
ile aynı deterministik demo bina setini gerçekten CPU'da rasterize edip
(gerçek RGB piksel matrisi üretir), sonucu diskteki bir PPM baseline
görüntüsüyle **piksel piksel** karşılaştırır.

Yapısal imza testi geometri verisindeki (vertex/üçgen sayısı, bbox vb.)
bir regresyonu yakalar; bu test ONA EK OLARAK saf görsel bir regresyonu
(örn. yüzey normali ters dönmüş, ışık yönü/gölgelendirme bozulmuş,
üçgenler örtüşüyor/delinmiş ama sayılar aynı) de yakalar — çünkü gerçekten
piksel üretiyor ve karşılaştırıyor.

Kullanım::

    python3 scripts/pixel_visual_regression.py                  # baseline ile karşılaştır
    python3 scripts/pixel_visual_regression.py --update-baseline # baseline PPM'leri yeniden üret

Dürüst sınırlama: bu, tarayıcıdaki gerçek WebGL2 renderer'ın pikseli
değildir — headless, stdlib-only bir yazılım rasterizer'ının pikselidir
(bkz. `render_engine/software_rasterizer.py` docstring'i).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo kökü (harita/'nın üstü)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import (
    BuildingType, Footprint, ProceduralBuildingGenerator,
)
from harita.render_engine.software_rasterizer import (
    Camera, pixel_diff, rasterize_mesh, read_ppm, write_ppm,
)

BASELINE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "visual_regression_pixels"

# `scripts/visual_regression.py` ile birebir aynı demo set — iki test
# aynı geometriyi farklı katmanlarda (yapısal vs. piksel) doğrular.
_DEMO_CASES: list[tuple[str, Polygon, BuildingType, int, float]] = [
    ("dikdortgen_apartman", Polygon([
        Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15),
    ]), BuildingType.APARTMAN, 5, 15.0),
    ("l_sekli_ofis", Polygon([
        Point2D(0, 0), Point2D(18, 0), Point2D(18, 8), Point2D(10, 8),
        Point2D(10, 16), Point2D(0, 16),
    ]), BuildingType.OFIS, 6, 21.0),
    ("u_sekli_okul", Polygon([
        Point2D(0, 0), Point2D(24, 0), Point2D(24, 10), Point2D(16, 10),
        Point2D(16, 4), Point2D(8, 4), Point2D(8, 10), Point2D(0, 10),
    ]), BuildingType.OKUL, 3, 10.5),
    ("duzensiz_villa", Polygon([
        Point2D(0, 0), Point2D(11, 2), Point2D(13, 9), Point2D(6, 12),
        Point2D(-1, 7),
    ]), BuildingType.VILLA, 2, 6.0),
    ("kare_depo", Polygon([
        Point2D(0, 0), Point2D(16, 0), Point2D(16, 16), Point2D(0, 16),
    ]), BuildingType.DEPO, 1, 6.0),
]

SEED = 4242
WIDTH, HEIGHT = 160, 120

# Sabit izometrik-benzer kamera açısı: her demo case kendi bounding
# box'ına göre otomatik konumlanır (bkz. rasterize_mesh varsayılan
# kamera mantığı) ki farklı boyuttaki binalar hep kadraja sığsın.
MAX_CHANNEL_DIFF_TOLERANCE = 12
MAX_CHANGED_RATIO_TOLERANCE = 0.02


def render_all() -> dict[str, "Image"]:  # noqa: F821 - Image sadece tip ipucu
    from harita.render_engine.software_rasterizer import Image  # local import: tip netliği

    images: dict[str, Image] = {}
    for name, polygon, btype, floor_count, height_m in _DEMO_CASES:
        footprint = Footprint(
            polygon=polygon, building_type=btype.value,
            floor_count=floor_count, height_m=height_m,
        )
        building = ProceduralBuildingGenerator.generate(footprint, building_type=btype, seed=SEED)
        mesh = building.full_mesh(include_interior=False)
        images[name] = rasterize_mesh(mesh, width=WIDTH, height=HEIGHT)
    return images


def main() -> int:
    update = "--update-baseline" in sys.argv
    images = render_all()

    if update or not BASELINE_DIR.exists() or not any(BASELINE_DIR.glob("*.ppm")):
        BASELINE_DIR.mkdir(parents=True, exist_ok=True)
        for name, img in images.items():
            write_ppm(img, str(BASELINE_DIR / f"{name}.ppm"))
        print(f"piksel baseline yazıldı: {BASELINE_DIR} ({len(images)} görüntü, {WIDTH}x{HEIGHT})")
        return 0

    problems: list[str] = []
    for name, img in images.items():
        baseline_path = BASELINE_DIR / f"{name}.ppm"
        if not baseline_path.exists():
            problems.append(f"  [{name}] baseline PPM yok — yeni demo case, --update-baseline ile onaylayın")
            continue
        baseline_img = read_ppm(str(baseline_path))
        try:
            diff = pixel_diff(baseline_img, img)
        except ValueError as exc:
            problems.append(f"  [{name}] {exc}")
            continue
        if not diff.within_tolerance(max_diff=MAX_CHANNEL_DIFF_TOLERANCE,
                                      max_ratio=MAX_CHANGED_RATIO_TOLERANCE):
            problems.append(
                f"  [{name}] piksel regresyonu: max_channel_diff={diff.max_channel_diff}, "
                f"changed_ratio={diff.changed_pixel_ratio:.4f} "
                f"(eşik: max_diff<= {MAX_CHANNEL_DIFF_TOLERANCE}, ratio<= {MAX_CHANGED_RATIO_TOLERANCE})"
            )

    if problems:
        print(f"PİKSEL GÖRSEL REGRESYON TESPİT EDİLDİ ({len(problems)} fark):")
        for p in problems:
            print(p)
        print("\nBeklenen bir değişiklikse: python3 scripts/pixel_visual_regression.py --update-baseline")
        return 1

    print(f"OK — {len(images)} demo case piksel baseline ile eşik dahilinde uyuşuyor ({BASELINE_DIR}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
