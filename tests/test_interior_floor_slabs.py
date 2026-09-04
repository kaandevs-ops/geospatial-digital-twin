"""Faz 1 düzeltmesi: `generate_interior=True` iç mekan yolu daha önce hiç
test edilmiyordu. Kullanıcı geri bildirimi: iç mekanı incelerken (x-ray /
kesit / patlatma) katları yalnızca pencere sırasından sayabiliyordu çünkü
`interior_mesh` kendi zemin döşemesini taşımıyordu (döşeme yalnızca
Facade.mesh içindeydi, facade devre dışı bırakılan görünümlerde kayboluyordu).

Bu testler her katın `interior_mesh`'inin facade'dan bağımsız olarak kendi
zeminini içerdiğini doğrular.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshMerger
from harita.building_reconstruction import (
    Footprint, ProceduralBuildingGenerator,
)


def _rect_polygon(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


def _build(n_floors: int = 4, seed: int | None = 42):
    poly = _rect_polygon(10, 8)
    fp = Footprint(polygon=poly, height_m=None, floor_count=n_floors, building_type="apartman")
    return ProceduralBuildingGenerator.generate(
        fp, floor_count=n_floors, seed=seed, generate_interior=True,
    )


def test_every_floor_has_nonempty_interior_mesh():
    building = _build()
    assert len(building.floors) == 4
    for floor in building.floors:
        assert floor.interior_mesh is not None
        assert floor.interior_mesh.triangle_count() > 0


def test_every_floor_interior_mesh_contains_its_own_slab():
    """Zemin döşemesi artık `interior_mesh` içinde - facade mesh'inden
    bağımsız olarak var olmalı (isim önekinden doğrulanır)."""
    building = _build()
    for floor in building.floors:
        assert floor.interior_mesh.name is not None
        # Katman kendi ismiyle merge edilmiş olsa da alt parçaların isimleri
        # kaybolur; bunun yerine geometrik olarak doğrula: kat tabanına
        # (floor_base_z) yakın, aşağı doğru ince bir slab olmalı.
        floor_base_z = floor.level * floor.height_m
        zs = [v.z for v in floor.interior_mesh.vertices]
        assert min(zs) <= floor_base_z + 1e-6, (
            f"kat {floor.level}: interior_mesh içinde tabana (z={floor_base_z}) "
            f"inen bir zemin döşemesi bulunamadı, min z={min(zs)}"
        )


def test_interior_only_view_without_facade_still_shows_floor_separation():
    """x-ray/kesit gibi yalnızca iç mekanın gösterildiği bir görünümü
    simüle eder (facade.mesh dahil edilmez) - katlar arasında görünür bir
    zemin ayrımı olmalı, yalnızca pencerelerden sayılabilir olmamalı."""
    building = _build(n_floors=3)
    interior_only = MeshMerger.merge(
        [f.interior_mesh for f in building.floors if f.interior_mesh],
        name="interior_only_view",
    )
    assert interior_only.triangle_count() > 0

    # Her kat sınırında (floor_height katları) zemine ait vertex kümesi
    # bulunmalı - bu, katların dikey olarak ayırt edilebilir olduğunu
    # gösterir (yalnızca pencere aralıklarından değil).
    floor_height = building.floors[0].height_m
    zs = sorted({round(v.z, 3) for v in interior_only.vertices})
    for level in range(len(building.floors)):
        expected_base = level * floor_height
        assert any(abs(z - expected_base) < 0.2 for z in zs), (
            f"kat {level} tabanına (z~{expected_base}) yakın vertex bulunamadı"
        )


def test_ground_floor_elevator_shaft_does_not_hide_missing_slabs_on_upper_floors():
    """Asansör şaftı yalnızca zemin katta (level 0) üretilip tüm bina
    boyunca uzanır - bu durum üst katların KENDİ zemin döşemesinin var
    olup olmadığını maskelememeli."""
    building = _build(n_floors=4)
    for floor in building.floors[1:]:
        floor_base_z = floor.level * floor.height_m
        zs = [v.z for v in floor.interior_mesh.vertices]
        assert min(zs) <= floor_base_z + 1e-6
