"""
Footprint Parser
================

Roadmap Phase 3 - "Footprint Parser".

Haritadaki (Phase 1 GeoFeature) bina polygon'unu okuyup mühendislik
açısından anlamlı bir `Footprint` özetine çevirir:

    - alan (area)
    - çevre uzunluğu (perimeter)
    - ana yön / oryantasyon (dominant edge açısı)
    - eğim (aspect ratio üzerinden basit yaklaşık)
    - çatı şekli tahmini (basit ısı-haritası / kural tabanlı; gerçek ML
      tahmini Phase 4 - AIRoofPredictor'da)

Bağımlılık: yalnızca core_engine (geometry_engine, gis_core).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ...core_engine.geometry_engine import GeometryEngine, Point2D, Polygon
from ...core_engine.gis_core import GeoFeature


class RoofTypeGuess(str, Enum):
    FLAT = "flat"
    HIP = "hip"
    GABLE = "gable"
    PYRAMID = "pyramid"
    INDUSTRIAL = "industrial"


class FootprintShape(str, Enum):
    """Roadmap V2 - A3: footprint geometrisinden çıkarılan kaba taban şekli.

    Dışbükey-gövde (convex hull) alanına göre "eksik alan" (concavity) ve
    içbükey köşe sayısı üzerinden kural tabanlı bir sınıflandırma. Gerçek
    bir CV/ML şekil tanıma değildir; L/U/T binalarda çatı/cephe kural
    motorunu daha isabetli çalıştırmak için yeterli bir sinyaldir.
    """

    RECTANGLE = "rectangle"
    L_SHAPE = "l_shape"
    T_SHAPE = "t_shape"
    U_SHAPE = "u_shape"
    COMPLEX = "complex"


@dataclass(slots=True)
class Footprint:
    """Roadmap Phase 3 veri modeli: `Footprint`."""

    polygon: Polygon
    building_type: str | None = None
    floor_count: int | None = None
    height_m: float | None = None
    roof_type: str | None = None

    # Türetilmiş / hesaplanmış alanlar
    area_m2: float = field(init=False, default=0.0)
    perimeter_m: float = field(init=False, default=0.0)
    orientation_deg: float = field(init=False, default=0.0)
    aspect_ratio: float = field(init=False, default=1.0)
    centroid: Point2D = field(init=False, default_factory=lambda: Point2D(0.0, 0.0))
    shape: str = field(init=False, default=FootprintShape.RECTANGLE.value)
    concave_vertex_count: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.area_m2 = self.polygon.unsigned_area()
        self.perimeter_m = self.polygon.perimeter()
        self.orientation_deg = FootprintParser.dominant_orientation(self.polygon)
        self.aspect_ratio = FootprintParser.aspect_ratio(self.polygon)
        self.centroid = FootprintParser.centroid(self.polygon)
        self.concave_vertex_count = FootprintParser.concave_vertex_count(self.polygon)
        self.shape = FootprintParser.classify_shape(self.polygon).value
        if self.roof_type is None:
            self.roof_type = FootprintParser.guess_roof_type(self).value


class FootprintParser:
    """Phase 1 `GeoFeature` -> Phase 3 `Footprint` dönüşümü ve analizleri."""

    # ------------------------------------------------------------------ #
    # GeoFeature -> Footprint
    # ------------------------------------------------------------------ #
    @staticmethod
    def parse(feature: GeoFeature) -> Footprint:
        polygon = feature.to_polygon()
        props = feature.properties or {}

        building_type = (
            props.get("building") or props.get("building_type") or props.get("amenity") or None
        )
        floor_count = FootprintParser._as_int(
            props.get("building:levels") or props.get("floor_count") or props.get("levels")
        )
        height_m = FootprintParser._as_float(props.get("height") or props.get("building:height"))
        if height_m is None and floor_count:
            height_m = floor_count * 3.2  # ortalama kat yüksekliği varsayımı

        roof_type = props.get("roof:shape") or props.get("roof_type")

        return Footprint(
            polygon=polygon,
            building_type=building_type,
            floor_count=floor_count,
            height_m=height_m,
            roof_type=roof_type,
        )

    @staticmethod
    def _as_int(value) -> int | None:
        if value is None:
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_float(value) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    # ------------------------------------------------------------------ #
    # Geometrik analizler
    # ------------------------------------------------------------------ #
    @staticmethod
    def centroid(polygon: Polygon) -> Point2D:
        ring = polygon.closed_ring()
        area = polygon.area()  # imzalı
        if abs(area) < 1e-9:
            xs = [p.x for p in ring]
            ys = [p.y for p in ring]
            return Point2D(sum(xs) / len(xs), sum(ys) / len(ys))
        cx = cy = 0.0
        for p1, p2 in zip(ring, ring[1:]):
            cross = p1.x * p2.y - p2.x * p1.y
            cx += (p1.x + p2.x) * cross
            cy += (p1.y + p2.y) * cross
        factor = 1.0 / (6.0 * area)
        return Point2D(cx * factor, cy * factor)

    @staticmethod
    def dominant_orientation(polygon: Polygon) -> float:
        """En uzun kenarın X eksenine göre açısını derece cinsinden döner
        (0-180 arası, çünkü bir doğrunun yönü 180° periyodikdir)."""
        ring = polygon.closed_ring()
        best_len, best_angle = -1.0, 0.0
        for p1, p2 in zip(ring, ring[1:]):
            dx, dy = p2.x - p1.x, p2.y - p1.y
            length = math.hypot(dx, dy)
            if length > best_len:
                best_len = length
                angle = math.degrees(math.atan2(dy, dx)) % 180.0
                best_angle = angle
        return best_angle

    @staticmethod
    def aspect_ratio(polygon: Polygon) -> float:
        """Oryantasyona hizalanmış bounding-box üzerinden en-boy oranı
        (>= 1.0). Binanın 'uzun ve dar' mı yoksa 'kare' ye mi yakın
        olduğunu belirlemek için kullanılır (çatı şekli tahmininde önemli)."""
        angle = math.radians(FootprintParser.dominant_orientation(polygon))
        cos_a, sin_a = math.cos(-angle), math.sin(-angle)
        rotated = [(p.x * cos_a - p.y * sin_a, p.x * sin_a + p.y * cos_a) for p in polygon.points]
        xs = [p[0] for p in rotated]
        ys = [p[1] for p in rotated]
        w = max(xs) - min(xs) or 1e-6
        h = max(ys) - min(ys) or 1e-6
        long_side, short_side = max(w, h), min(w, h)
        return long_side / short_side

    @staticmethod
    def compactness(polygon: Polygon) -> float:
        """Polsby-Popper kompaktlık indeksi (1.0 = daire, düşük = düzensiz).
        Çatı/bina tipi tahmini için kaba bir 'düzenlilik' sinyali."""
        area = polygon.unsigned_area()
        perimeter = polygon.perimeter()
        if perimeter <= 0:
            return 0.0
        return (4.0 * math.pi * area) / (perimeter**2)

    @staticmethod
    def guess_roof_type(fp: Footprint) -> RoofTypeGuess:
        """Basit kural/ısı-haritası tabanlı çatı tahmini (gerçek ML modeli
        Phase 4 AIRoofPredictor'da devreye girer; bu, bağımlılıksız bir
        varsayılan/fallback'tir).

        Roadmap V2 - A3: taban şekli (`FootprintShape`) artık karara
        katılıyor — L/T/U/karmaşık tabanlarda tek mahyalı bir `gable` çatı
        geometrik olarak tanımsız/yanıltıcıdır; bu durumlarda güvenli
        varsayılan olarak `hip` seçilir, `gable`/`pyramid` yalnızca
        dikdörtgen-benzeri tabanlarda önerilir.
        """
        bt = (fp.building_type or "").lower()
        if bt in {"industrial", "warehouse", "hangar", "factory"}:
            return RoofTypeGuess.INDUSTRIAL
        if bt in {"apartments", "commercial", "office", "retail", "mall"}:
            return RoofTypeGuess.FLAT
        if fp.shape != FootprintShape.RECTANGLE.value:
            return RoofTypeGuess.HIP
        if fp.aspect_ratio >= 2.2:
            return RoofTypeGuess.GABLE
        if FootprintParser.compactness(fp.polygon) >= 0.78:
            return RoofTypeGuess.PYRAMID
        return RoofTypeGuess.HIP

    # ------------------------------------------------------------------ #
    # Roadmap V2 - A3: taban şekli (L/U/T) tespiti
    # ------------------------------------------------------------------ #
    @staticmethod
    def concave_vertex_count(polygon: Polygon) -> int:
        """Poligonun kaç köşesinin içbükey (reflex) olduğunu sayar.

        Poligonun baskın dönüş yönüne göre her köşedeki çapraz çarpımın
        işaretine bakılır; çoğunluk yönle ters işaretli köşeler içbükeydir.
        Dejenere (<4 nokta) poligonlarda 0 döner.
        """
        ring = polygon.closed_ring()
        pts = (
            ring[:-1]
            if len(ring) > 1 and ring[0].x == ring[-1].x and ring[0].y == ring[-1].y
            else ring
        )
        n = len(pts)
        if n < 4:
            return 0
        crosses: list[float] = []
        for i in range(n):
            a, b, c = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
            v1x, v1y = b.x - a.x, b.y - a.y
            v2x, v2y = c.x - b.x, c.y - b.y
            crosses.append(v1x * v2y - v1y * v2x)
        positive = sum(1 for cr in crosses if cr > 1e-9)
        negative = sum(1 for cr in crosses if cr < -1e-9)
        majority_positive = positive >= negative
        return sum(1 for cr in crosses if (cr < -1e-9 if majority_positive else cr > 1e-9))

    @staticmethod
    def classify_shape(polygon: Polygon) -> FootprintShape:
        """Convex-hull alan farkı + içbükey köşe sayısına göre kaba taban
        şekli sınıflandırması.

        - 0 içbükey köşe (veya ihmal edilebilir hull farkı) -> `RECTANGLE`.
        - 1 içbükey köşe -> `L_SHAPE`.
        - 2 içbükey köşe -> hull eksikliği büyükse (iki kollu oyuk) `U_SHAPE`,
          değilse (tek çıkıntı) `T_SHAPE`.
        - 3+ içbükey köşe -> `COMPLEX`.
        """
        area = polygon.unsigned_area()
        if area <= 1e-9:
            return FootprintShape.COMPLEX
        hull = GeometryEngine.convex_hull(polygon.points)
        hull_area = hull.unsigned_area()
        deficiency = (hull_area - area) / hull_area if hull_area > 1e-9 else 0.0
        concave_n = FootprintParser.concave_vertex_count(polygon)

        if concave_n == 0 or deficiency < 0.03:
            return FootprintShape.RECTANGLE
        if concave_n == 1:
            return FootprintShape.L_SHAPE
        if concave_n == 2:
            return FootprintShape.U_SHAPE if deficiency >= 0.30 else FootprintShape.T_SHAPE
        return FootprintShape.COMPLEX

    @staticmethod
    def simplify(footprint: Footprint, tolerance: float = 0.3) -> Footprint:
        simplified_poly = GeometryEngine.simplify_polygon(footprint.polygon, tolerance)
        return Footprint(
            polygon=simplified_poly,
            building_type=footprint.building_type,
            floor_count=footprint.floor_count,
            height_m=footprint.height_m,
            roof_type=footprint.roof_type,
        )
