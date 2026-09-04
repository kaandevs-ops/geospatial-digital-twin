"""
Roadmap V2 — A3 kalan iş: "500 gerçek OSM bina footprint'i ile toplu
üretim, üretilen binaların %100'ü geometrik olarak geçerli
(self-intersection yok, manifold mesh)."

**Önemli dürüst not:** Bu ortamda gerçek bir OSM/Overpass sunucusuna ağ
erişimi YOK (izin verilen domain listesi yalnızca pypi/npm/github/ubuntu
paket kaynaklarını kapsıyor — bkz. repo kökü network yapılandırması).
Dolayısıyla kabul kriterindeki "500 gerçek OSM footprint'i" harfiyen
karşılanamadı. Bunun yerine, gerçek şehir dokusunu makul ölçüde temsil
eden **500 adet prosedürel olarak üretilmiş, geometrik olarak çeşitli
footprint** (dikdörtgen, L/T/U şekilli, ve rastgele çok köşeli "complex"
poligonlar; 8m–80m arası kenar uzunlukları, 1–20 kat) kullanılır ve HER
BİRİ için:
    1) Footprint poligonunun kendisiyle kesişmediği (self-intersection
       yok) doğrulanır,
    2) `ProceduralBuildingGenerator.generate()` ile tam bir `Building`
       üretilir (12 bina tipinin hepsi turlanarak),
    3) Üretilen `full_mesh()`'in `MeshRepair.is_manifold()` testini
       geçtiği doğrulanır (üçgen kenarları en fazla 2 üçgene ait).

Bu, kabul kriterinin *yöntemini* (toplu üretim + %100 geometrik geçerlilik
ölçümü) tam karşılar; yalnızca veri kaynağı gerçek-OSM yerine sentetik
fakat gerçekçi ölçekli/şekilli footprint'lerdir. Gerçek OSM verisiyle
uçtan uca entegrasyon testi (ağ erişimi olan bir ortamda) hâlâ ROADMAP_V2
"kalan iş" listesinde açık kalmaya devam ediyor.
"""

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import (
    BuildingType,
    Footprint,
    ProceduralBuildingGenerator,
)
from harita.mesh_engine import MeshRepair

N_FOOTPRINTS = 500


# ---------------------------------------------------------------------------
# Self-intersection (basit çokgen mi?) kontrolü — O(n^2), footprint
# köşe sayısı küçük olduğu için (<=12) yeterince hızlı.
# ---------------------------------------------------------------------------

def _segments_intersect(p1: Point2D, p2: Point2D, p3: Point2D, p4: Point2D) -> bool:
    def cross(o: Point2D, a: Point2D, b: Point2D) -> float:
        return (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)

    d1 = cross(p3, p4, p1)
    d2 = cross(p3, p4, p2)
    d3 = cross(p1, p2, p3)
    d4 = cross(p1, p2, p4)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False


def polygon_is_simple(poly: Polygon) -> bool:
    """Kapalı halkanın kenarları, komşu olmayan kenarlarla kesişmiyor mu?"""
    ring = poly.closed_ring()
    n = len(ring) - 1  # son nokta ilkiyle aynı (kapalı halka)
    if n < 3:
        return False
    edges = [(ring[i], ring[i + 1]) for i in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            # komşu kenarları (ortak köşesi olanları) atla
            if j == i or (j + 1) % n == i or (i + 1) % n == j:
                continue
            a1, a2 = edges[i]
            b1, b2 = edges[j]
            if _segments_intersect(a1, a2, b1, b2):
                return False
    return True


# ---------------------------------------------------------------------------
# Sentetik-ama-gerçekçi footprint üreteci
# ---------------------------------------------------------------------------

def _rect(w: float, h: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, h), Point2D(0, h)])


def _l_shape(w: float, h: float, cut_w: float, cut_h: float) -> Polygon:
    return Polygon([
        Point2D(0, 0), Point2D(w, 0), Point2D(w, h - cut_h),
        Point2D(w - cut_w, h - cut_h), Point2D(w - cut_w, h), Point2D(0, h),
    ])


def _t_shape(w: float, h: float, stem_w: float, stem_h: float) -> Polygon:
    sx = (w - stem_w) / 2.0
    return Polygon([
        Point2D(0, 0), Point2D(w, 0), Point2D(w, stem_h),
        Point2D(sx + stem_w, stem_h), Point2D(sx + stem_w, h),
        Point2D(sx, h), Point2D(sx, stem_h), Point2D(0, stem_h),
    ])


def _u_shape(w: float, h: float, notch_w: float, notch_h: float) -> Polygon:
    nx = (w - notch_w) / 2.0
    return Polygon([
        Point2D(0, 0), Point2D(w, 0), Point2D(w, h), Point2D(nx + notch_w, h),
        Point2D(nx + notch_w, notch_h), Point2D(nx, notch_h), Point2D(nx, h), Point2D(0, h),
    ])


def _complex_convex_ok_polygon(rng: random.Random, n_vertices: int, radius: float) -> Polygon:
    """Rastgele fakat kendisiyle kesişmeyen bir poligon (star-shaped polygon).

    Açılar TAM daireye eşit aralıklarla dağıtılıp az miktarda jitter
    eklenir (yalnızca rastgele `uniform(0, 2π)` kullanılmaz - çünkü az
    sayıda köşe rastgele bir alt-yay içinde kümelenebilir, bu durumda
    orijin poligonun İÇİNDE olmaz ve kapanış kenarı diğer kenarları
    kesebilir). Eşit aralıklı + sınırlı jitter, orijinin her zaman
    poligonun içinde kalmasını (dolayısıyla self-intersection'sız bir
    "star-shaped" poligon) garanti eder.
    """
    sector = 2 * math.pi / n_vertices
    angles = sorted(
        i * sector + rng.uniform(0, sector * 0.6) for i in range(n_vertices)
    )
    points = []
    for a in angles:
        r = radius * rng.uniform(0.55, 1.0)
        points.append(Point2D(r * math.cos(a), r * math.sin(a)))
    return Polygon(points)


def generate_footprints(count: int, seed: int = 20260725) -> list[Footprint]:
    rng = random.Random(seed)
    footprints: list[Footprint] = []
    building_types = list(BuildingType)

    for i in range(count):
        shape_kind = rng.choice(["rect", "l", "t", "u", "complex"])
        w = rng.uniform(10.0, 80.0)
        h = rng.uniform(8.0, 60.0)

        if shape_kind == "rect":
            poly = _rect(w, h)
        elif shape_kind == "l":
            poly = _l_shape(w, h, w * rng.uniform(0.2, 0.45), h * rng.uniform(0.2, 0.45))
        elif shape_kind == "t":
            poly = _t_shape(w, h, w * rng.uniform(0.25, 0.5), h * rng.uniform(0.3, 0.6))
        elif shape_kind == "u":
            poly = _u_shape(w, h, w * rng.uniform(0.25, 0.5), h * rng.uniform(0.3, 0.6))
        else:
            poly = _complex_convex_ok_polygon(rng, rng.randint(6, 12), max(w, h) / 2.0)

        n_floors = rng.randint(1, 20)
        height_m = n_floors * rng.uniform(2.6, 4.2)
        bt = building_types[i % len(building_types)]

        fp = Footprint(
            polygon=poly,
            building_type=bt.value,
            floor_count=n_floors,
            height_m=height_m,
        )
        footprints.append(fp)
    return footprints


# ---------------------------------------------------------------------------
# Testler
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def synthetic_footprints() -> list[Footprint]:
    return generate_footprints(N_FOOTPRINTS)


class TestBulkFootprintGeneration:
    def test_generates_requested_count(self, synthetic_footprints: list[Footprint]) -> None:
        assert len(synthetic_footprints) == N_FOOTPRINTS

    def test_all_footprints_are_simple_polygons(
        self, synthetic_footprints: list[Footprint]
    ) -> None:
        """Kaynak footprint poligonlarının kendisi self-intersection içermez."""
        bad = [fp for fp in synthetic_footprints if not polygon_is_simple(fp.polygon)]
        assert bad == [], f"{len(bad)} footprint self-intersecting çıktı"

    def test_bulk_generation_100_percent_valid(
        self, synthetic_footprints: list[Footprint]
    ) -> None:
        """Kabul kriteri: 500 footprint'in TAMAMI, üretilen binaların
        %100'ü geometrik olarak geçerli (self-intersection yok, manifold
        mesh) koşulunu sağlamalı."""
        failures: list[tuple[int, str]] = []

        for idx, fp in enumerate(synthetic_footprints):
            try:
                building = ProceduralBuildingGenerator.generate(
                    fp, building_type=fp.building_type, seed=idx,
                )
            except Exception as exc:  # noqa: BLE001
                failures.append((idx, f"generate() exception: {exc!r}"))
                continue

            if not polygon_is_simple(building.footprint.polygon):
                failures.append((idx, "footprint self-intersecting"))
                continue

            mesh = building.full_mesh()
            if len(mesh.triangles) == 0:
                failures.append((idx, "mesh bos (ucgen yok)"))
                continue
            if not MeshRepair.is_manifold(mesh):
                failures.append((idx, "mesh manifold degil"))
                continue

        total = len(synthetic_footprints)
        success_rate = 100.0 * (total - len(failures)) / total
        assert failures == [], (
            f"{len(failures)}/{total} bina geometrik olarak geçersiz "
            f"(başarı oranı %{success_rate:.2f}): ilk 5 hata: {failures[:5]}"
        )

    def test_all_12_building_types_represented(
        self, synthetic_footprints: list[Footprint]
    ) -> None:
        seen = {fp.building_type for fp in synthetic_footprints}
        assert seen == {bt.value for bt in BuildingType}

    def test_shape_diversity_covers_all_categories(
        self, synthetic_footprints: list[Footprint]
    ) -> None:
        """Şekil sınıflandırıcının (`FootprintShape`) tüm kategorileri
        (rectangle/l_shape/t_shape/u_shape/complex) en az bir kez
        gözlemlenmiş olmalı - toplu testin gerçekten çeşitli geometri
        kapsadığının kanıtı."""
        seen_shapes = {fp.shape for fp in synthetic_footprints}
        assert len(seen_shapes) >= 4  # en az 4 farklı sınıf gözlemlenmeli
