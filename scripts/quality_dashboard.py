#!/usr/bin/env python3
"""ROADMAP_V5 - Track Q / Q2: "Mesh kalite metrikleri (M1.3) dashboard'a
bağlanmalı - her yeni bina üretiminde otomatik skor (non-manifold kenar
sayısı, üçgen/vertex oranı, watertight durumu) loglanmalı, zaman içinde
trend görülebilmeli."

Bu script `mesh_engine.quality_metrics.BatchQualityAnalyzer`'ı
`scripts/visual_regression.py`'nin zaten tanımladığı deterministik demo
bina setine (`_DEMO_CASES`) çalıştırır ve iki çıktı üretir:

1. `tests/fixtures/quality_dashboard_history.jsonl` - append-only, her
   çalıştırmada bir satır (timestamp + toplu skor) eklenir; zaman içindeki
   trendi görmek için bu dosya okunur (JSON Lines, git diff'te okunabilir
   kalır, satır satır büyür).
2. `tests/fixtures/quality_dashboard.html` - en son çalıştırmanın anlık
   görüntüsü + geçmiş trend grafiği (bağımlılıksız, saf HTML/inline SVG -
   stdlib-only ilkesine uygun, tarayıcıda doğrudan açılabilir).

DÜRÜST SINIRLAMA: bu script'in kendisi bir "dashboard sunucusu" değildir
(bu ortamda sürekli çalışan bir web sunucusu/CI runner'ı yok) - her
çalıştırmada statik bir HTML anlık görüntüsü üretir. Gerçek bir CI'da bu
`.github/workflows/quality_ci.yml` tarafından her push'ta çalıştırılıp
artifact olarak yayınlanacak şekilde bağlanmıştır (bkz. o dosya).

Kullanım::

    python3 scripts/quality_dashboard.py               # çalıştır + logla
    python3 scripts/quality_dashboard.py --no-log       # sadece anlık görüntü, geçmişe ekleme
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # repo kökü (harita/'nın üstü)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from harita.building_reconstruction import (  # noqa: E402
    Footprint,
    ProceduralBuildingGenerator,
)
from harita.mesh_engine.quality_metrics import BatchQualityAnalyzer  # noqa: E402
from visual_regression import _DEMO_CASES, SEED  # noqa: E402

HISTORY_PATH = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "quality_dashboard_history.jsonl"
)
DASHBOARD_HTML_PATH = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "quality_dashboard.html"
)

MAX_HISTORY_POINTS_IN_CHART = 50


def _build_demo_meshes() -> dict[str, object]:
    """`visual_regression._DEMO_CASES`'i kullanarak deterministik mesh
    seti üretir - iki script aynı bina setini görür, farklı iki demo veri
    setinin yönetim yükünden kaçınılır."""
    meshes = {}
    for name, polygon, btype, floor_count, height_m in _DEMO_CASES:
        footprint = Footprint(
            polygon=polygon,
            building_type=btype.value,
            floor_count=floor_count,
            height_m=height_m,
        )
        building = ProceduralBuildingGenerator.generate(footprint, building_type=btype, seed=SEED)
        meshes[name] = building.full_mesh(include_interior=False)
    return meshes


def run_dashboard_snapshot() -> dict:
    """Demo bina seti üzerinde `BatchQualityAnalyzer`'ı çalıştırır ve
    JSON-serileştirilebilir bir özet sözlük döndürür (loglama ve HTML
    render'ının ortak girdisi)."""
    meshes = _build_demo_meshes()
    named_meshes = list(meshes.items())
    report = BatchQualityAnalyzer.analyze_all([m for _, m in named_meshes])
    for (name, _), r in zip(named_meshes, report.reports):
        r.mesh_name = name

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "case_count": len(report.reports),
        "total_vertices": report.total_vertices,
        "total_triangles": report.total_triangles,
        "watertight_ratio": round(report.watertight_ratio, 4),
        "total_non_manifold_edges": sum(r.non_manifold_edge_count for r in report.reports),
        "total_degenerate_triangles": sum(r.degenerate_triangle_count for r in report.reports),
        "min_normal_consistency": round(
            min((r.normal_consistency_ratio for r in report.reports), default=1.0), 4
        ),
        "cases": [
            {
                "name": r.mesh_name,
                "vertex_count": r.vertex_count,
                "triangle_count": r.triangle_count,
                "non_manifold_edge_count": r.non_manifold_edge_count,
                "degenerate_triangle_count": r.degenerate_triangle_count,
                "normal_consistency_ratio": round(r.normal_consistency_ratio, 4),
                "is_watertight": r.is_watertight,
                "is_manifold": r.is_manifold,
            }
            for r in report.reports
        ],
    }


def append_history(snapshot: dict) -> None:
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")


def load_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _sparkline_svg(
    values: list[float], width: int = 300, height: int = 60, color: str = "#2563eb"
) -> str:
    """Bağımlılıksız (saf SVG) küçük trend grafiği - harici bir çizim
    kütüphanesi (matplotlib vb.) gerektirmez, stdlib-only ilkesine uygun."""
    if not values:
        return "<svg></svg>"
    if len(values) == 1:
        values = values * 2
    vmin, vmax = min(values), max(values)
    span = (vmax - vmin) or 1.0
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = (i / (n - 1)) * (width - 10) + 5
        y = height - 5 - ((v - vmin) / span) * (height - 10)
        points.append(f"{x:.1f},{y:.1f}")
    polyline = " ".join(points)
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'xmlns="http://www.w3.org/2000/svg">'
        f'<polyline points="{polyline}" fill="none" stroke="{color}" stroke-width="2" />'
        f"</svg>"
    )


def render_html(snapshot: dict, history: list[dict]) -> str:
    recent = history[-MAX_HISTORY_POINTS_IN_CHART:]
    tri_trend = _sparkline_svg([h["total_triangles"] for h in recent], color="#2563eb")
    watertight_trend = _sparkline_svg(
        [h["watertight_ratio"] * 100 for h in recent], color="#16a34a"
    )
    nm_trend = _sparkline_svg([h["total_non_manifold_edges"] for h in recent], color="#dc2626")

    rows = "\n".join(
        f"<tr><td>{c['name']}</td><td>{c['vertex_count']}</td><td>{c['triangle_count']}</td>"
        f"<td>{c['non_manifold_edge_count']}</td><td>{c['degenerate_triangle_count']}</td>"
        f"<td>{c['normal_consistency_ratio']:.3f}</td>"
        f"<td>{'✅' if c['is_watertight'] else '❌'}</td>"
        f"<td>{'✅' if c['is_manifold'] else '❌'}</td></tr>"
        for c in snapshot["cases"]
    )

    return f"""<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8" />
<title>Harita Modelleme Platformu - Mesh Kalite Dashboard</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; background: #f8fafc; color: #0f172a; }}
  h1 {{ font-size: 1.4rem; }}
  .meta {{ color: #64748b; font-size: 0.85rem; margin-bottom: 1.5rem; }}
  .cards {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2rem; }}
  .card {{ background: white; border: 1px solid #e2e8f0; border-radius: 8px; padding: 1rem 1.25rem; min-width: 180px; }}
  .card .label {{ font-size: 0.75rem; color: #64748b; text-transform: uppercase; }}
  .card .value {{ font-size: 1.6rem; font-weight: 600; }}
  table {{ border-collapse: collapse; width: 100%; background: white; }}
  th, td {{ border: 1px solid #e2e8f0; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.85rem; }}
  th {{ background: #f1f5f9; }}
  h2 {{ font-size: 1rem; margin-top: 2rem; }}
</style>
</head>
<body>
<h1>Mesh Kalite Dashboard (ROADMAP_V5 Track Q / Q2)</h1>
<p class="meta">Son çalıştırma: {snapshot["timestamp"]} - {len(history)} kayıt geçmişte mevcut.</p>

<div class="cards">
  <div class="card"><div class="label">Toplam Üçgen</div><div class="value">{snapshot["total_triangles"]}</div></div>
  <div class="card"><div class="label">Watertight Oranı</div><div class="value">{snapshot["watertight_ratio"]:.1%}</div></div>
  <div class="card"><div class="label">Non-manifold Kenar</div><div class="value">{snapshot["total_non_manifold_edges"]}</div></div>
  <div class="card"><div class="label">Dejenere Üçgen</div><div class="value">{snapshot["total_degenerate_triangles"]}</div></div>
  <div class="card"><div class="label">Min. Normal Tutarlılığı</div><div class="value">{snapshot["min_normal_consistency"]:.3f}</div></div>
</div>

<h2>Trend - Toplam Üçgen Sayısı</h2>
{tri_trend}
<h2>Trend - Watertight Oranı (%)</h2>
{watertight_trend}
<h2>Trend - Non-manifold Kenar Sayısı</h2>
{nm_trend}

<h2>Demo Bina Seti - Anlık Görüntü</h2>
<table>
<tr><th>Bina</th><th>Vertex</th><th>Üçgen</th><th>Non-manifold</th><th>Dejenere</th><th>Normal Tutarlılık</th><th>Watertight</th><th>Manifold</th></tr>
{rows}
</table>
</body>
</html>
"""


def main() -> int:
    log_history = "--no-log" not in sys.argv
    snapshot = run_dashboard_snapshot()
    if log_history:
        append_history(snapshot)
    history = load_history()
    html = render_html(snapshot, history)
    DASHBOARD_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
    DASHBOARD_HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard yazıldı: {DASHBOARD_HTML_PATH}")
    print(f"Geçmiş kayıt sayısı: {len(history)}")
    print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
