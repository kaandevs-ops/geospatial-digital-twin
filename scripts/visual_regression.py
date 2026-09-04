#!/usr/bin/env python3
"""Roadmap Faz 5.3 / Faz 1 kabul kriteri — "Görsel regresyon testi (render
çıktısının önceki versiyonla piksel/yapısal karşılaştırması) — geometri
kalitesi gerilemesin diye."

DÜRÜST SINIRLAMA: bu proje stdlib-only'dir ve gerçek GPU render pipeline'ı
yoktur (bkz. `render_engine/scene_bridge.py` başlığı) — ekrana çizim
`viewer/index.html` içindeki WebGL2 renderer'a ait, tarayıcı dışında
piksel üretilemez. Bu yüzden "piksel karşılaştırma" yerine, aynı amaca
hizmet eden **yapısal bir imza** (structural signature) karşılaştırıyoruz:
sabit bir seed ile üretilen demo bina setinin vertex/üçgen sayısı,
bounding box, watertight/manifold durumu ve mesh_engine/quality_metrics
raporu. Bu sayı grubu, geometriyi üreten koddaki bir regresyonun (örn.
kat hizalama hatası, kayıp üçgen, yeni non-manifold kenar) render'a hiç
bakmadan yakalanmasını sağlar — "daha kaliteli oldu/kötüleşti" tartışması
bu sayılarla nesnelleşir (bkz. yeni_roadmap.md Faz 0).

Kullanım::

    python3 scripts/visual_regression.py                  # baseline ile karşılaştır
    python3 scripts/visual_regression.py --update-baseline # baseline'ı yeniden üret
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo kökü (harita/'nın üstü)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harita.building_reconstruction import (
    BuildingType,
    Footprint,
    ProceduralBuildingGenerator,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine.quality_metrics import MeshQualityAnalyzer

BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "visual_regression_baseline.json"
)

# Sabit, deterministik bir demo bina seti (5 farklı footprint şekli / bina
# tipi — roadmap Faz 1 kabul kriterinin "dikdörtgen, L, U, düzensiz
# poligon" çeşitliliğinin küçük bir örneklemi).
_DEMO_CASES: list[tuple[str, Polygon, BuildingType, int, float]] = [
    (
        "dikdortgen_apartman",
        Polygon(
            [
                Point2D(0, 0),
                Point2D(20, 0),
                Point2D(20, 15),
                Point2D(0, 15),
            ]
        ),
        BuildingType.APARTMAN,
        5,
        15.0,
    ),
    (
        "l_sekli_ofis",
        Polygon(
            [
                Point2D(0, 0),
                Point2D(18, 0),
                Point2D(18, 8),
                Point2D(10, 8),
                Point2D(10, 16),
                Point2D(0, 16),
            ]
        ),
        BuildingType.OFIS,
        6,
        21.0,
    ),
    (
        "u_sekli_okul",
        Polygon(
            [
                Point2D(0, 0),
                Point2D(24, 0),
                Point2D(24, 10),
                Point2D(16, 10),
                Point2D(16, 4),
                Point2D(8, 4),
                Point2D(8, 10),
                Point2D(0, 10),
            ]
        ),
        BuildingType.OKUL,
        3,
        10.5,
    ),
    (
        "duzensiz_villa",
        Polygon(
            [
                Point2D(0, 0),
                Point2D(11, 2),
                Point2D(13, 9),
                Point2D(6, 12),
                Point2D(-1, 7),
            ]
        ),
        BuildingType.VILLA,
        2,
        6.0,
    ),
    (
        "kare_depo",
        Polygon(
            [
                Point2D(0, 0),
                Point2D(16, 0),
                Point2D(16, 16),
                Point2D(0, 16),
            ]
        ),
        BuildingType.DEPO,
        1,
        6.0,
    ),
]

SEED = 4242
# Geometri üretim kodu deterministik olsa da kayan nokta birikimi (farklı
# CPU/derleyici) küçük farklar yaratabilir — bu yüzden sayısal alanlar
# mutlak eşitlik yerine tolerans ile karşılaştırılır.
FLOAT_TOLERANCE = 1e-6


def _round_bbox(bbox):
    mn, mx = bbox
    return tuple(round(v, 4) for v in mn), tuple(round(v, 4) for v in mx)


def build_signature() -> dict:
    """Demo bina setinin güncel yapısal imzasını üretir."""
    signature: dict[str, dict] = {}
    for name, polygon, btype, floor_count, height_m in _DEMO_CASES:
        footprint = Footprint(
            polygon=polygon,
            building_type=btype.value,
            floor_count=floor_count,
            height_m=height_m,
        )
        building = ProceduralBuildingGenerator.generate(
            footprint,
            building_type=btype,
            seed=SEED,
        )
        mesh = building.full_mesh(include_interior=False)
        report = MeshQualityAnalyzer.analyze(mesh)
        signature[name] = {
            "vertex_count": report.vertex_count,
            "triangle_count": report.triangle_count,
            "non_manifold_edge_count": report.non_manifold_edge_count,
            "boundary_edge_count": report.boundary_edge_count,
            "degenerate_triangle_count": report.degenerate_triangle_count,
            "is_watertight": report.is_watertight,
            "is_manifold": report.is_manifold,
            "normal_consistency_ratio": round(report.normal_consistency_ratio, 4),
            "bounding_box": _round_bbox(mesh.bounding_box()),
            "floor_count": len(building.floors),
            "total_height_m": round(building.total_height_m, 4),
        }
    return signature


def _normalize(value):
    """JSON'dan okunan liste ile bellekteki tuple'ları karşılaştırılabilir
    hale getirir (JSON tuple bilmez, her zaman list'e döner)."""
    if isinstance(value, (list, tuple)):
        return tuple(_normalize(v) for v in value)
    return value


def _diff_case(name: str, baseline: dict, current: dict) -> list[str]:
    problems = []
    keys = set(baseline) | set(current)
    for key in sorted(keys):
        b = baseline.get(key, "<yok>")
        c = current.get(key, "<yok>")
        if (
            isinstance(b, (int, float))
            and isinstance(c, (int, float))
            and not isinstance(b, bool)
            and not isinstance(c, bool)
        ):
            if abs(b - c) > FLOAT_TOLERANCE:
                problems.append(f"  [{name}] {key}: baseline={b} güncel={c}")
        elif isinstance(b, (list, tuple)) and isinstance(c, (list, tuple)):
            if _normalize(b) != _normalize(c):
                problems.append(f"  [{name}] {key}: baseline={b} güncel={c}")
        elif b != c:
            problems.append(f"  [{name}] {key}: baseline={b} güncel={c}")
    return problems


def compare(current: dict, baseline: dict) -> list[str]:
    problems: list[str] = []
    missing = set(baseline) - set(current)
    added = set(current) - set(baseline)
    for name in sorted(missing):
        problems.append(
            f"  [{name}] baseline'da vardı ama güncel çıktıda YOK (silinmiş demo case olabilir)"
        )
    for name in sorted(added):
        problems.append(
            f"  [{name}] güncel çıktıda var ama baseline'da yok (yeni demo case — --update-baseline ile onaylayın)"
        )
    for name in sorted(set(baseline) & set(current)):
        problems.extend(_diff_case(name, baseline[name], current[name]))
    return problems


def main() -> int:
    update = "--update-baseline" in sys.argv
    current = build_signature()

    if update or not BASELINE_PATH.exists():
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps(current, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"baseline yazıldı: {BASELINE_PATH}")
        return 0

    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    problems = compare(current, baseline)
    if problems:
        print(f"GÖRSEL/YAPISAL REGRESYON TESPİT EDİLDİ ({len(problems)} fark):")
        for p in problems:
            print(p)
        print("\nBeklenen bir değişiklikse: python3 scripts/visual_regression.py --update-baseline")
        return 1

    print(f"OK — {len(current)} demo case baseline ile birebir uyuşuyor ({BASELINE_PATH.name}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
