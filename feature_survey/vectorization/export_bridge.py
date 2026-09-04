"""FAZ S4 — vektörleştirme çıktısını mevcut export zincirine bağlar.

Roadmap: "Çıktı doğrudan `export/vector_2d.py` ve mevcut CAD/GIS export
zincirine ... bağlanır — yeni bir export formatı icat edilmiyor." Bu
modül, `VectorFeature` listesini (a) 2D SVG kat planına (`SVGCanvas`,
`export/vector_2d.py`) ve (b) GeoJSON `FeatureCollection`'ı JSON dosyasına
(`export/reports.JSONReportExporter`) yazar — ikisi de var olan, test
edilmiş exporter'ları çağırır, yeni bir dosya yazıcısı icat edilmez.
"""

from __future__ import annotations

from ...export.reports import JSONReportExporter
from ...export.vector_2d import SVGCanvas, SVGStyle
from .linework import VectorFeature, linework_to_geojson

# Kategoriye göre basit stil ataması — arayüzdeki katman renklerine
# karşılık gelir (bkz. `codes.FeatureCategory`), sadece görsel QA amaçlı.
_CATEGORY_COLOR = {
    "building": "#c0392b",
    "road": "#34495e",
    "utility": "#f39c12",
    "vegetation": "#27ae60",
    "hydrology": "#2980b9",
    "control": "#8e44ad",
    "other": "#7f8c8d",
}


def export_linework_geojson(features: list[VectorFeature], path: str):
    """GeoJSON `FeatureCollection` olarak diske yazar (var olan
    `JSONReportExporter` üzerinden — yeni bir JSON yazıcı icat edilmez)."""
    data = linework_to_geojson(features)
    return JSONReportExporter.export(data, path)


def export_linework_svg(features: list[VectorFeature], path: str, margin: float = 20.0):
    """Vektörleştirilmiş linework'ü 2D SVG kat planı olarak yazar
    (var olan `export/vector_2d.SVGCanvas` üzerinden)."""
    all_coords: list[tuple[float, float]] = []
    for f in features:
        all_coords.extend((c[0], c[1]) for c in f.coordinates)
    if not all_coords:
        all_coords = [(0.0, 0.0), (1.0, 1.0)]

    xs = [c[0] for c in all_coords]
    ys = [c[1] for c in all_coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x) + margin * 2
    height = (max_y - min_y) + margin * 2
    canvas = SVGCanvas(width=max(width, 1.0), height=max(height, 1.0))

    def _to_svg(pt: tuple[float, float]) -> tuple[float, float]:
        x, y = pt
        # SVG Y ekseni aşağı büyür — çağıran taraf sorumluluğu
        # (`vector_2d.SVGCanvas` docstring'i ile tutarlı) burada üstlenilir.
        return (x - min_x + margin, (max_y - y) + margin)

    for f in features:
        color = _CATEGORY_COLOR.get(f.code.category.value, "#7f8c8d")
        style = SVGStyle(
            stroke=color, stroke_width=1.5, fill="none" if f.kind != "Polygon" else color + "22"
        )
        points_2d = [_to_svg((c[0], c[1])) for c in f.coordinates]
        if f.kind == "Point":
            canvas.circle(points_2d[0][0], points_2d[0][1], 2.0, style)
        elif f.kind == "LineString":
            canvas.polyline(points_2d, style)
        elif f.kind == "Polygon":
            canvas.polygon(points_2d, style)

    return canvas.save(path)
