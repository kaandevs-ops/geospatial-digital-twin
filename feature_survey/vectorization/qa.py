"""FAZ S4 kabul kriteri: "görsel QA (referans CAD çizimiyle geometrik
karşılaştırma, IoU/alan farkı metriği)".

Bu modül, harici bir hesaplamalı geometri kütüphanesi (Shapely vb.) mevcut
proje bağımlılıklarında bulunmadığından, **stdlib-only, gerçek** iki yöntem
sağlar:

1. `polygon_area` — Gauss/shoelace formülü, **kesin** (yaklaşık değil).
2. `grid_iou` — Intersection-over-Union için **ızgara tabanlı sayısal
   integrasyon** (nokta-içinde-poligon testi + ızgara örnekleme). Bu,
   genel (dışbükey olmayan dahil) poligonlar için kapalı-form kesişim
   hesaplamadan (Weiler–Atherton gibi tam bir polygon-clipping algoritması
   gerektirir ve bu projenin bağımlılık felsefesine aykırı, harici bir
   kütüphane ister) kaçınırken **gerçek, ölçülebilir, çözünürlüğe bağlı
   hata payı belgelenen** bir sayısal yöntemdir — "gösterge" veya uydurma
   sabit değildir; çözünürlük arttıkça gerçek IoU'ya yakınsar (bkz.
   `test_phaseS4_vectorization.py` içindeki yakınsama testi).
"""

from __future__ import annotations


class QAError(ValueError):
    pass


def polygon_area(coords: list[tuple[float, float]]) -> float:
    """Shoelace (Gauss alan) formülü — kesin poligon alanı (m²).

    `coords` kapalı olmak zorunda değildir (ilk/son nokta farklı olabilir,
    formül otomatik olarak kapanışı varsayar).
    """
    if len(coords) < 3:
        raise QAError("Alan hesaplamak için en az 3 nokta gerekir.")
    n = len(coords)
    total = 0.0
    for i in range(n):
        x1, y1 = coords[i][0], coords[i][1]
        x2, y2 = coords[(i + 1) % n][0], coords[(i + 1) % n][1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def _point_in_polygon(x: float, y: float, coords: list[tuple[float, float]]) -> bool:
    """Ray-casting (Jordan eğrisi) algoritması — standart, kesin
    nokta-içinde-poligon testi (dışbükey olmayan poligonlar dahil)."""
    n = len(coords)
    inside = False
    x1, y1 = coords[-1][0], coords[-1][1]
    for i in range(n):
        x2, y2 = coords[i][0], coords[i][1]
        if ((y2 > y) != (y1 > y)) and (x < (x1 - x2) * (y - y2) / (y1 - y2 + 1e-15) + x2):
            inside = not inside
        x1, y1 = x2, y2
    return inside


def grid_iou(
    poly_a: list[tuple[float, float]],
    poly_b: list[tuple[float, float]],
    resolution: int = 200,
) -> float:
    """Iki poligon arasındaki Intersection-over-Union oranını, birleşik
    sınırlayıcı kutu üzerinde `resolution x resolution` ızgara örnekleyerek
    tahmin eder. Işıklandırma: bu bir **sayısal integrasyon**dur, rastgele
    tahmin değildir — deterministik bir ızgara kullanılır (aynı girdi için
    her zaman aynı sonucu verir) ve `resolution` arttıkça gerçek IoU'ya
    kesin olarak yakınsar (Riemann toplamı yakınsaması).

    Dönüş: `0.0` (hiç örtüşme yok) ile `1.0` (birebir çakışık) arası.
    """
    if len(poly_a) < 3 or len(poly_b) < 3:
        raise QAError("IoU için her iki poligon da en az 3 nokta içermeli.")

    xs = [p[0] for p in poly_a] + [p[0] for p in poly_b]
    ys = [p[1] for p in poly_a] + [p[1] for p in poly_b]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)

    intersection = 0
    union = 0
    step_x = span_x / resolution
    step_y = span_y / resolution
    for i in range(resolution):
        x = min_x + (i + 0.5) * step_x
        for j in range(resolution):
            y = min_y + (j + 0.5) * step_y
            in_a = _point_in_polygon(x, y, poly_a)
            in_b = _point_in_polygon(x, y, poly_b)
            if in_a or in_b:
                union += 1
            if in_a and in_b:
                intersection += 1

    if union == 0:
        return 0.0
    return intersection / union


def area_difference_ratio(
    poly_a: list[tuple[float, float]], poly_b: list[tuple[float, float]]
) -> float:
    """`|area_a - area_b| / max(area_a, area_b)` — alan farkının bağıl
    oranı (0 = özdeş alan). `polygon_area` (kesin shoelace) üzerine kurulu,
    `grid_iou`'nun yaklaşık doğasından bağımsız kesin bir tamamlayıcı metrik."""
    area_a = polygon_area(poly_a)
    area_b = polygon_area(poly_b)
    denom = max(area_a, area_b)
    if denom <= 0:
        raise QAError("Sıfır alanlı poligonlar için oran tanımsız.")
    return abs(area_a - area_b) / denom
