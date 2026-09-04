"""Yeni roadmap (yeni_roadmap.md) - Faz 1.3: Pencere/Kapı Sistemi.

Kapsam:
- WWR (zaten Faz E19/D13'te vardı) -> değişmedi, regresyon kontrolü burada
  yapılmıyor (ayrı test dosyalarında zaten kapsanıyor).
- Pencere tipolojisi (sabit/açılır/sürgülü/balkon kapısı) - YENİ.
- Kapı tipolojisi (giriş/yangın/garaj/iç kapı) - YENİ.
- Cephe ritmi algoritması (`place_facade_rhythm`) - YENİ.
- Balkon/çıkma/giriş sundurması gerçek 3D mesh üretimi - YENİ.
"""

from __future__ import annotations

import math

from harita.building_reconstruction.building_elements import (
    DOOR_TYPE_DEFAULTS,
    WINDOW_TYPE_DEFAULTS,
    BayWindowGenerator,
    DoorGenerator,
    DoorType,
    EntranceCanopyGenerator,
    WindowGenerator,
    WindowType,
)
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import FacadeElementMeshBuilder


def _rect(w: float = 12.0, d: float = 20.0) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


class TestWindowTypology:
    def test_default_window_type_is_casement(self):
        windows = WindowGenerator.place_on_footprint(_rect())
        assert windows
        assert all(w.window_type == WindowType.CASEMENT for w in windows)

    def test_explicit_window_type_propagates(self):
        windows = WindowGenerator.place_on_footprint(_rect(), window_type=WindowType.SLIDING)
        assert windows
        assert all(w.window_type == WindowType.SLIDING for w in windows)

    def test_all_window_types_have_defaults(self):
        for wt in WindowType:
            defaults = WINDOW_TYPE_DEFAULTS[wt]
            assert defaults["width"] > 0
            assert defaults["height"] > 0
            assert 0.0 <= defaults["sill_height_ratio"] <= 1.0

    def test_balcony_door_has_zero_sill(self):
        assert WINDOW_TYPE_DEFAULTS[WindowType.BALCONY_DOOR]["sill_height_ratio"] == 0.0


class TestDoorTypology:
    def test_default_entrance_door(self):
        door = DoorGenerator.exterior_entrance(_rect())
        assert door.door_type == DoorType.ENTRANCE
        assert door.height == DOOR_TYPE_DEFAULTS[DoorType.ENTRANCE]["height"]

    def test_fire_door_typology(self):
        door = DoorGenerator.exterior_entrance(_rect(), door_type=DoorType.FIRE)
        assert door.door_type == DoorType.FIRE
        assert door.height == DOOR_TYPE_DEFAULTS[DoorType.FIRE]["height"]

    def test_secondary_exit_excludes_main_entrance_edge(self):
        poly = _rect()
        main = DoorGenerator.exterior_entrance(poly)
        second = DoorGenerator.secondary_exit(poly, exclude_edge_index=main.wall_edge_index)
        assert second.wall_edge_index != main.wall_edge_index
        assert second.door_type == DoorType.FIRE

    def test_garage_door_is_wide(self):
        door = DoorGenerator.exterior_entrance(_rect(), door_type=DoorType.GARAGE)
        assert door.width == DOOR_TYPE_DEFAULTS[DoorType.GARAGE]["width"]
        assert (
            DOOR_TYPE_DEFAULTS[DoorType.GARAGE]["width"]
            > DOOR_TYPE_DEFAULTS[DoorType.ENTRANCE]["width"]
        )


class TestFacadeRhythm:
    def test_ground_floor_uses_sliding_larger_windows(self):
        poly = _rect()
        ground = WindowGenerator.place_facade_rhythm(poly, floor_index=0, floor_height=3.5, seed=1)
        upper = WindowGenerator.place_facade_rhythm(poly, floor_index=1, floor_height=3.0, seed=1)
        assert ground and upper
        assert all(w.window_type == WindowType.SLIDING for w in ground)
        assert all(w.window_type == WindowType.CASEMENT for w in upper)
        assert ground[0].width > upper[0].width

    def test_balcony_door_inserted_on_request(self):
        poly = _rect()
        windows = WindowGenerator.place_facade_rhythm(
            poly,
            floor_index=2,
            floor_height=3.0,
            seed=5,
            has_balcony_door=True,
        )
        balcony_doors = [w for w in windows if w.window_type == WindowType.BALCONY_DOOR]
        assert len(balcony_doors) == 1
        assert balcony_doors[0].sill_height == 0.0

    def test_no_balcony_door_when_not_requested(self):
        poly = _rect()
        windows = WindowGenerator.place_facade_rhythm(poly, floor_index=1, floor_height=3.0, seed=5)
        assert not any(w.window_type == WindowType.BALCONY_DOOR for w in windows)


class TestBayWindowGenerator:
    def test_places_bay_windows_at_interval(self):
        windows = WindowGenerator.place_on_footprint(_rect(), spacing=2.0)
        bays = BayWindowGenerator.place_on_windows(windows, every_nth=2)
        assert bays
        assert len(bays) <= len(windows)

    def test_balcony_doors_excluded_from_bay_windows(self):
        poly = _rect()
        windows = WindowGenerator.place_facade_rhythm(
            poly,
            floor_index=2,
            floor_height=3.0,
            seed=3,
            has_balcony_door=True,
        )
        bays = BayWindowGenerator.place_on_windows(windows, every_nth=1)
        assert all(b.window.window_type != WindowType.BALCONY_DOOR for b in bays)


class TestEntranceCanopyGenerator:
    def test_canopy_wider_than_door(self):
        door = DoorGenerator.exterior_entrance(_rect())
        canopy = EntranceCanopyGenerator.for_entrance(door)
        assert canopy.width > door.width
        assert canopy.height_above_door > door.height


class TestFacadeGeneratorIntegration:
    """Roadmap 1.3'ün son açık maddesi: balkon/çıkma/sundurma artık
    `FacadeGenerator.generate()` ana akışına opsiyonel parametrelerle
    (`add_balconies`, `add_bay_windows`, `add_entrance_canopy`) bağlı -
    önceden bunlar yalnızca bağımsız yardımcı sınıflardı, ana üretim
    akışına hiç bağlanmamıştı."""

    @staticmethod
    def _poly():
        return Polygon([Point2D(0, 0), Point2D(12, 0), Point2D(12, 20), Point2D(0, 20)])

    def test_default_generate_has_no_extra_elements(self):
        from harita.building_reconstruction import FacadeGenerator

        f = FacadeGenerator.generate(self._poly(), "apartman", 0.0, 3.0, floor_count=3)
        assert f.balconies == []
        assert f.bay_windows == []

    def test_add_balconies_produces_geometry_above_ground_floor(self):
        from harita.building_reconstruction import FacadeGenerator

        f = FacadeGenerator.generate(
            self._poly(),
            "apartman",
            0.0,
            3.0,
            floor_count=4,
            add_balconies=True,
        )
        assert f.balconies
        assert f.mesh.triangle_count() > 0

    def test_add_bay_windows_produces_geometry(self):
        from harita.building_reconstruction import FacadeGenerator

        f = FacadeGenerator.generate(
            self._poly(),
            "apartman",
            0.0,
            3.0,
            floor_count=2,
            add_bay_windows=True,
            bay_window_every_nth=1,
        )
        assert f.bay_windows

    def test_add_entrance_canopy_increases_mesh_triangle_count(self):
        from harita.building_reconstruction import FacadeGenerator

        base = FacadeGenerator.generate(self._poly(), "apartman", 0.0, 3.0, floor_count=2)
        with_canopy = FacadeGenerator.generate(
            self._poly(),
            "apartman",
            0.0,
            3.0,
            floor_count=2,
            add_entrance_canopy=True,
        )
        assert with_canopy.mesh.triangle_count() > base.mesh.triangle_count()

    def test_all_elements_together_regression_safe(self):
        from harita.building_reconstruction import FacadeGenerator

        f = FacadeGenerator.generate(
            self._poly(),
            "apartman",
            0.0,
            3.0,
            floor_count=5,
            add_balconies=True,
            add_bay_windows=True,
            add_entrance_canopy=True,
        )
        assert f.mesh.triangle_count() > 0
        assert len(f.doors) == 1

    def test_balcony_mesh_is_nonempty_and_protrudes(self):
        a, b = Point2D(0, 0), Point2D(12, 0)
        pos = Point2D(6, 0)
        mesh = FacadeElementMeshBuilder.build_balcony(
            a,
            b,
            pos,
            width=1.6,
            depth=1.2,
            base_z=3.0,
            railing_height=1.0,
        )
        assert mesh.triangle_count() > 0
        ys = [v.y for v in mesh.vertices]
        # duvar y=0'da; balkon dışa (normal -y yönünde, CCW dikdörtgende
        # (0,0)->(12,0) kenarının dış normali (0,-1)) taşmalı.
        assert min(ys) < -0.5

    def test_bay_window_mesh_protrudes_outward(self):
        a, b = Point2D(0, 0), Point2D(12, 0)
        pos = Point2D(6, 0)
        mesh = FacadeElementMeshBuilder.build_bay_window(
            a,
            b,
            pos,
            side_width=1.6,
            protrusion=0.6,
            base_z=3.0,
            height=1.4,
        )
        assert mesh.triangle_count() > 0
        ys = [v.y for v in mesh.vertices]
        assert min(ys) < -0.3

    def test_canopy_mesh_has_slab_and_brackets(self):
        a, b = Point2D(0, 0), Point2D(12, 0)
        pos = Point2D(6, 0)
        mesh = FacadeElementMeshBuilder.build_entrance_canopy(
            a,
            b,
            pos,
            width=1.8,
            depth=1.2,
            base_z=2.3,
            bracket_count=2,
        )
        assert mesh.triangle_count() > 0
        zs = [v.z for v in mesh.vertices]
        # konsollar plakanın altına sarkar, plakanın kendisi base_z civarında
        assert min(zs) < 2.3
        assert max(zs) >= 2.3

    def test_meshes_have_valid_normals(self):
        a, b = Point2D(0, 0), Point2D(12, 0)
        pos = Point2D(6, 0)
        mesh = FacadeElementMeshBuilder.build_balcony(a, b, pos, 1.6, 1.2, 3.0)
        for v in mesh.vertices:
            n = math.sqrt(v.normal[0] ** 2 + v.normal[1] ** 2 + v.normal[2] ** 2)
            assert n > 0.9  # normalize edilmiş (yaklaşık 1.0)
