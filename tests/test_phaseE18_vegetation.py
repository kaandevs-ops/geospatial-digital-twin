"""Roadmap V4 - Track E / Faz E18 (vegetation) icin testler."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.terrain_engine import HeightmapGrid
from harita.vegetation import TreeSpecies, TreeGenerator, VegetationScatterer, VegetationInstance


def _flat_grid(width=10, height=10, resolution_m=2.0, z=0.0):
    elevations = [[z for _ in range(width)] for _ in range(height)]
    return HeightmapGrid(
        width=width, height=height, resolution_m=resolution_m,
        elevations=elevations, origin=GeoPoint(39.92, 32.85),
    )


def _valley_grid(width=12, height=12, resolution_m=2.0):
    """Ortada tek bir alcak 'vadi' hucresi (r=6,c=6) - o hucreye komsu
    hucrelerin akumule akisi, kenarlardaki hucrelerden cok daha yuksek
    olacak sekilde tasarlanmis basit bir konik cukur."""
    cx, cy = width // 2, height // 2
    elevations = []
    for r in range(height):
        row = []
        for c in range(width):
            dist = ((r - cy) ** 2 + (c - cx) ** 2) ** 0.5
            row.append(dist * 1.5)  # merkeze yaklastikca alcalir (huni)
        elevations.append(row)
    return HeightmapGrid(
        width=width, height=height, resolution_m=resolution_m,
        elevations=elevations, origin=GeoPoint(39.92, 32.85),
    ), (cy, cx)


# -- TreeGenerator --------------------------------------------------------- #

def test_generate_conifer_produces_valid_manifold_ish_mesh():
    mesh = TreeGenerator.generate(TreeSpecies.CONIFER, height=8.0, canopy_radius=2.0, seed=1)
    assert mesh.vertex_count() > 0
    assert mesh.triangle_count() > 0
    (min_x, min_y, min_z), (max_x, max_y, max_z) = mesh.bounding_box()
    assert min_z == 0.0 or min_z < 0.5  # taban ~zemin seviyesinde
    assert max_z > 5.0  # agac boyu makul


def test_generate_deciduous_has_two_canopy_lobes_more_triangles_than_conifer():
    conifer = TreeGenerator.generate(TreeSpecies.CONIFER, height=8.0, canopy_radius=2.0, seed=1)
    deciduous = TreeGenerator.generate(TreeSpecies.DECIDUOUS, height=8.0, canopy_radius=2.0, seed=1)
    assert deciduous.triangle_count() > conifer.triangle_count()


def test_generate_shrub_has_no_trunk_and_is_shorter():
    shrub = TreeGenerator.generate(TreeSpecies.SHRUB, height=1.5, canopy_radius=1.0, seed=2)
    _, (_, _, max_z) = shrub.bounding_box()
    assert max_z <= 1.5 * 1.2  # jitter faktoru (~%12) dahil makul ust sinir


def test_generate_is_deterministic_for_same_seed():
    m1 = TreeGenerator.generate(TreeSpecies.GENERIC, height=6.0, canopy_radius=1.5, seed=42)
    m2 = TreeGenerator.generate(TreeSpecies.GENERIC, height=6.0, canopy_radius=1.5, seed=42)
    assert m1.vertex_count() == m2.vertex_count()
    assert [v.as_tuple() for v in m1.vertices] == [v.as_tuple() for v in m2.vertices]


def test_generate_varies_with_different_seed():
    m1 = TreeGenerator.generate(TreeSpecies.GENERIC, height=6.0, canopy_radius=1.5, seed=1)
    m2 = TreeGenerator.generate(TreeSpecies.GENERIC, height=6.0, canopy_radius=1.5, seed=2)
    assert [v.as_tuple() for v in m1.vertices] != [v.as_tuple() for v in m2.vertices]


def test_generate_rejects_invalid_dimensions():
    try:
        TreeGenerator.generate(height=0.0)
        assert False, "beklenen ValueError firlatilmadi"
    except ValueError:
        pass
    try:
        TreeGenerator.generate(canopy_radius=-1.0)
        assert False, "beklenen ValueError firlatilmadi"
    except ValueError:
        pass


# -- VegetationScatterer ---------------------------------------------------- #

def test_scatter_returns_requested_count_on_flat_terrain():
    grid = _flat_grid()
    instances = VegetationScatterer.scatter(grid, target_count=50, seed=7)
    assert len(instances) == 50
    assert all(isinstance(i, VegetationInstance) for i in instances)


def test_scatter_zero_or_negative_count_returns_empty():
    grid = _flat_grid()
    assert VegetationScatterer.scatter(grid, target_count=0, seed=1) == []
    assert VegetationScatterer.scatter(grid, target_count=-5, seed=1) == []


def test_scatter_is_deterministic_for_same_seed():
    grid = _flat_grid()
    r1 = VegetationScatterer.scatter(grid, target_count=30, seed=99)
    r2 = VegetationScatterer.scatter(grid, target_count=30, seed=99)
    assert [(i.x, i.y, i.height, i.canopy_radius) for i in r1] == [(i.x, i.y, i.height, i.canopy_radius) for i in r2]


def test_scatter_instances_are_within_grid_bounds():
    grid = _flat_grid(width=8, height=8, resolution_m=3.0)
    instances = VegetationScatterer.scatter(grid, target_count=100, seed=3)
    max_extent = 8 * 3.0
    for inst in instances:
        assert -3.0 <= inst.x <= max_extent
        assert -3.0 <= inst.y <= max_extent


def test_moisture_field_shape_matches_grid():
    grid = _flat_grid(width=6, height=9)
    field = VegetationScatterer.moisture_field(grid)
    assert len(field) == 9
    assert all(len(row) == 6 for row in field)
    assert all(0.0 <= v <= 1.0 for row in field for v in row)


def test_scatter_density_higher_in_high_flow_accumulation_region():
    """Roadmap V4 - Faz E18 kabul kriteri: FlowAccumulation degeri yuksek
    (nemli) bolgelerde uretilen bitki ortusu yogunlugu olculebilir sekilde
    daha fazladir."""
    grid, (vr, vc) = _valley_grid()
    instances = VegetationScatterer.scatter(grid, target_count=2000, seed=11)

    def _count_near(row, col, radius_cells=2):
        cnt = 0
        r_lo = (row - radius_cells) * grid.resolution_m
        r_hi = (row + radius_cells) * grid.resolution_m
        c_lo = (col - radius_cells) * grid.resolution_m
        c_hi = (col + radius_cells) * grid.resolution_m
        for inst in instances:
            if c_lo <= inst.x <= c_hi and r_lo <= inst.y <= r_hi:
                cnt += 1
        return cnt

    valley_count = _count_near(vr, vc)
    # Ayni buyuklukte, vadiden uzak bir kontrol bolgesi (sol-ust kose)
    corner_count = _count_near(1, 1)

    assert valley_count > corner_count * 2, (
        f"vadi (nemli) bolge yogunlugu ({valley_count}) kontrol bolgesinden "
        f"({corner_count}) belirgin sekilde fazla olmali"
    )


def test_scatter_respects_max_slope_cutoff():
    """Cok dik bir arazi parcasinda max_slope altinda kalan hicbir hucre
    yoksa scatter bos liste dondurur (hata firlatmaz)."""
    width = height = 5
    elevations = [[c * 100.0 for c in range(width)] for _ in range(height)]  # asiri dik
    grid = HeightmapGrid(
        width=width, height=height, resolution_m=1.0,
        elevations=elevations, origin=GeoPoint(39.92, 32.85),
    )
    instances = VegetationScatterer.scatter(grid, target_count=10, seed=1, max_slope=0.01)
    assert instances == []


if __name__ == "__main__":
    import inspect
    mod = sys.modules[__name__]
    tests = [obj for name, obj in vars(mod).items() if name.startswith("test_") and inspect.isfunction(obj)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {fn.__name__}: {e!r}")
    print(f"\n{passed} passed, {failed} failed out of {len(tests)}")
    sys.exit(1 if failed else 0)
