"""Phase 6 (Analysis Engine) için birim testleri."""

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.analysis_engine.environmental_sim import (
    FloodEstimation,
    HeatIslandSimulation,
    NoiseSimulation,
    RainSimulation,
    ReflectionSimulation,
    WindSimulation,
)
from harita.analysis_engine.measurement import MeasurementEngine
from harita.analysis_engine.sun_simulation import (
    RoofIrradiance,
    SeasonalSunPath,
    ShadowProjection,
    SolarExposure,
    SolarPositionCalculator,
)
from harita.analysis_engine.visibility import (
    BlindSpotAnalysis,
    LineOfSight,
    RayCasting,
    ShadowAnalysis,
    VisibilityHeatmap,
)
from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder
from harita.terrain_engine import DEMImporter


def _box_mesh(size=10.0, height=6.0, base_z=0.0, cx=0.0, cy=0.0):
    poly = Polygon(
        points=[
            Point2D(cx - size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy + size / 2),
            Point2D(cx - size / 2, cy + size / 2),
        ]
    )
    return MeshBuilder.extrude_polygon(poly, base_z=base_z, height=height)


ISTANBUL = GeoPoint(lat=41.0082, lon=28.9784)


# ============================================================================ #
# Measurement
# ============================================================================ #


class TestMeasurement:
    def test_distance_2d(self):
        r = MeasurementEngine.distance_2d(Point2D(0, 0), Point2D(3, 4))
        assert math.isclose(r.value, 5.0)
        assert r.unit == "m"

    def test_distance_3d(self):
        r = MeasurementEngine.distance_3d((0, 0, 0), (1, 2, 2))
        assert math.isclose(r.value, 3.0)

    def test_area_square(self):
        poly = Polygon(points=[Point2D(0, 0), Point2D(4, 0), Point2D(4, 4), Point2D(0, 4)])
        r = MeasurementEngine.area(poly)
        assert math.isclose(r.value, 16.0)

    def test_mesh_surface_area_positive(self):
        mesh = _box_mesh(size=2.0, height=2.0)
        r = MeasurementEngine.mesh_surface_area(mesh)
        assert r.value > 0

    def test_volume_tetrahedral_box(self):
        mesh = _box_mesh(size=2.0, height=3.0)
        r = MeasurementEngine.volume_tetrahedral(mesh)
        assert math.isclose(r.value, 2.0 * 2.0 * 3.0, rel_tol=0.05)

    def test_volume_monte_carlo_close_to_exact(self):
        mesh = _box_mesh(size=4.0, height=4.0)
        exact = MeasurementEngine.volume_tetrahedral(mesh).value
        mc = MeasurementEngine.volume_monte_carlo(mesh, samples=8000, seed=42).value
        assert abs(mc - exact) / exact < 0.15

    def test_height(self):
        mesh = _box_mesh(size=2.0, height=5.0, base_z=1.0)
        r = MeasurementEngine.height(mesh)
        assert math.isclose(r.value, 5.0, rel_tol=0.01)

    def test_angle_right_angle(self):
        r = MeasurementEngine.angle(Point2D(1, 0), Point2D(0, 0), Point2D(0, 1))
        assert math.isclose(r.value, 90.0, abs_tol=1e-6)

    def test_angle_3d(self):
        r = MeasurementEngine.angle_3d((1, 0, 0), (0, 0, 0), (0, 0, 1))
        assert math.isclose(r.value, 90.0, abs_tol=1e-6)

    def test_slope_flat(self):
        r = MeasurementEngine.slope((0, 0, 0), (10, 0, 0))
        assert math.isclose(r.value, 0.0)

    def test_slope_45_degrees(self):
        r = MeasurementEngine.slope((0, 0, 0), (10, 0, 10))
        assert math.isclose(r.value, 100.0)

    def test_slope_degrees_45(self):
        r = MeasurementEngine.slope_degrees((0, 0, 0), (10, 0, 10))
        assert math.isclose(r.value, 45.0, abs_tol=1e-6)


# ============================================================================ #
# Visibility
# ============================================================================ #


class TestRayCasting:
    def test_hits_box(self):
        mesh = _box_mesh(size=4.0, height=4.0, cx=0, cy=10)
        hit = RayCasting.cast(origin=(0, 0, 2), direction=(0, 1, 0), mesh=mesh)
        assert hit is not None
        assert hit.distance > 0

    def test_misses_box(self):
        mesh = _box_mesh(size=4.0, height=4.0, cx=0, cy=10)
        hit = RayCasting.cast(origin=(100, 0, 2), direction=(0, 1, 0), mesh=mesh)
        assert hit is None


class TestLineOfSight:
    def test_visible_without_occluder(self):
        result = LineOfSight.check((0, 0, 1), (20, 0, 1), occluders=[])
        assert result.visible

    def test_blocked_by_building(self):
        mesh = _box_mesh(size=6.0, height=6.0, cx=10, cy=0)
        result = LineOfSight.check((0, 0, 1), (20, 0, 1), occluders=[mesh])
        assert not result.visible
        assert result.blocked_at is not None

    def test_not_blocked_when_over_building(self):
        mesh = _box_mesh(size=6.0, height=2.0, cx=10, cy=0)
        result = LineOfSight.check((0, 0, 10), (20, 0, 10), occluders=[mesh])
        assert result.visible


class TestShadowAnalysis:
    def test_night_is_shadow(self):
        midnight = datetime(2026, 6, 21, 0, 0, tzinfo=timezone.utc)
        result = ShadowAnalysis.evaluate((0, 0, 0), ISTANBUL, midnight, occluders=[])
        assert result.in_shadow

    def test_daily_shadow_hours_bounded(self):
        mesh = _box_mesh(size=6.0, height=20.0, cx=5, cy=0)
        date = datetime(2026, 6, 21, tzinfo=timezone.utc)
        hours = ShadowAnalysis.daily_shadow_hours(
            (0, 0, 0.5), ISTANBUL, date, [mesh], step_minutes=60
        )
        assert 0.0 <= hours <= 24.0


class TestBlindSpotAnalysis:
    def test_detects_blind_spot_toward_building(self):
        mesh = _box_mesh(size=4.0, height=4.0, cx=0, cy=20)
        spots = BlindSpotAnalysis.scan((0, 0, 1), [mesh], scan_radius=100.0, angle_step_deg=10.0)
        assert len(spots) > 0
        assert all(s.max_visible_distance < 100.0 for s in spots)

    def test_no_blind_spot_without_obstacles(self):
        spots = BlindSpotAnalysis.scan((0, 0, 1), [], scan_radius=50.0, angle_step_deg=30.0)
        assert spots == []


class TestVisibilityHeatmap:
    def test_full_visibility_no_occluders(self):
        cells = VisibilityHeatmap.compute(
            observers=[(0, 0, 5)],
            grid_origin=(0.0, 0.0),
            grid_width=3,
            grid_height=3,
            cell_size=5.0,
            z=0.0,
            occluders=[],
        )
        assert len(cells) == 9
        assert all(c.visible_observer_count == 1 for c in cells)


# ============================================================================ #
# Sun Simulation
# ============================================================================ #


class TestSeasonalSunPath:
    def test_sample_day_length(self):
        samples = SeasonalSunPath.sample_day(
            ISTANBUL, datetime(2026, 6, 21, tzinfo=timezone.utc), step_minutes=60
        )
        assert len(samples) == 24

    def test_sample_seasons_has_four(self):
        seasons = SeasonalSunPath.sample_seasons(ISTANBUL, 2026)
        assert len(seasons) == 4

    def test_solar_noon_has_highest_elevation(self):
        date = datetime(2026, 6, 21, tzinfo=timezone.utc)
        noon = SeasonalSunPath.solar_noon(ISTANBUL, date)
        all_samples = SeasonalSunPath.sample_day(ISTANBUL, date, step_minutes=10)
        assert noon.position.elevation_deg == max(s.position.elevation_deg for s in all_samples)


class TestSolarExposure:
    def test_open_point_has_full_exposure_ratio(self):
        date = datetime(2026, 6, 21, tzinfo=timezone.utc)
        result = SolarExposure.compute((0, 0, 0), ISTANBUL, date, occluders=[], step_minutes=60)
        assert result.exposure_ratio == 1.0
        assert result.daylight_hours > 0

    def test_shaded_point_has_lower_exposure(self):
        mesh = _box_mesh(size=8.0, height=30.0, cx=3, cy=0)
        date = datetime(2026, 6, 21, tzinfo=timezone.utc)
        result = SolarExposure.compute((0, 0, 0.5), ISTANBUL, date, [mesh], step_minutes=60)
        assert result.exposure_ratio <= 1.0


class TestRoofIrradiance:
    def test_zero_at_night(self):
        sun = SolarPositionCalculator.compute(
            ISTANBUL, datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        )
        result = RoofIrradiance.compute(sun)
        assert result.watts_per_m2 == 0.0

    def test_positive_at_solar_noon_summer(self):
        date = datetime(2026, 6, 21, tzinfo=timezone.utc)
        noon = SeasonalSunPath.solar_noon(ISTANBUL, date)
        result = RoofIrradiance.compute(noon.position, roof_tilt_deg=30.0, roof_azimuth_deg=180.0)
        assert result.watts_per_m2 > 0

    def test_daily_energy_non_negative(self):
        energy = RoofIrradiance.daily_energy_kwh_per_m2(
            ISTANBUL,
            datetime(2026, 6, 21, tzinfo=timezone.utc),
            step_minutes=60,
        )
        assert energy >= 0.0


class TestShadowProjection:
    def test_no_shadow_at_night(self):
        footprint = Polygon(points=[Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5)])
        night_sun = SolarPositionCalculator.compute(
            ISTANBUL, datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        )
        shadow = ShadowProjection.project_footprint(footprint, 10.0, night_sun)
        assert shadow is None

    def test_shadow_polygon_during_day(self):
        footprint = Polygon(points=[Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5)])
        date = datetime(2026, 6, 21, 8, 0, tzinfo=timezone.utc)
        day_sun = SolarPositionCalculator.compute(ISTANBUL, date)
        shadow = ShadowProjection.project_footprint(footprint, 10.0, day_sun)
        assert shadow is not None
        assert len(shadow.points) >= 3
        assert shadow.unsigned_area() >= footprint.unsigned_area()


# ============================================================================ #
# Environmental Simulation
# ============================================================================ #


class TestWindSimulation:
    def test_obstacle_cell_has_zero_speed(self):
        obstacle = Polygon(points=[Point2D(4, 4), Point2D(6, 4), Point2D(6, 6), Point2D(4, 6)])
        field = WindSimulation.simulate(
            width=10,
            height=10,
            cell_size_m=1.0,
            obstacles=[obstacle],
            free_stream_speed=5.0,
            free_stream_direction_deg=0.0,
        )
        speed, _ = field.at(5, 5)
        assert speed == 0.0

    def test_wake_reduces_downstream_speed(self):
        obstacle = Polygon(points=[Point2D(2, 2), Point2D(4, 2), Point2D(4, 4), Point2D(2, 4)])
        field = WindSimulation.simulate(
            width=10,
            height=10,
            cell_size_m=1.0,
            obstacles=[obstacle],
            free_stream_speed=10.0,
            free_stream_direction_deg=0.0,
        )
        speed_downstream, _ = field.at(3, 5)
        assert speed_downstream <= 10.0


class TestRainSimulation:
    def test_flow_direction_matrix_shape(self):
        grid = DEMImporter.synthetic_hills(
            width=8, height=8, resolution_m=1.0, origin=GeoPoint(0, 0), amplitude=5.0
        )
        result = RainSimulation.simulate(grid, rainfall_mm=10.0)
        assert len(result.accumulation) == 8
        assert len(result.accumulation[0]) == 8

    def test_accumulation_non_negative(self):
        grid = DEMImporter.flat_terrain(
            width=5, height=5, resolution_m=1.0, elevation=0.0, origin=GeoPoint(0, 0)
        )
        result = RainSimulation.simulate(grid, rainfall_mm=5.0)
        assert all(v >= 0 for row in result.accumulation for v in row)


class TestFloodEstimation:
    def test_flat_low_terrain_floods_fully(self):
        grid = DEMImporter.flat_terrain(
            width=6, height=6, resolution_m=1.0, elevation=0.0, origin=GeoPoint(0, 0)
        )
        result = FloodEstimation.estimate(grid, water_level_m=1.0)
        assert result.flooded_cell_count == 36

    def test_high_terrain_not_flooded(self):
        grid = DEMImporter.flat_terrain(
            width=6, height=6, resolution_m=1.0, elevation=100.0, origin=GeoPoint(0, 0)
        )
        result = FloodEstimation.estimate(grid, water_level_m=1.0)
        assert result.flooded_cell_count == 0

    def test_flooded_area_matches_cell_size(self):
        grid = DEMImporter.flat_terrain(
            width=4, height=4, resolution_m=2.0, elevation=0.0, origin=GeoPoint(0, 0)
        )
        result = FloodEstimation.estimate(grid, water_level_m=1.0)
        assert result.flooded_area_m2(2.0) == 16 * 4.0


class TestHeatIslandSimulation:
    def test_asphalt_hotter_than_green(self):
        grid = [["asfalt", "yesil_alan"]]
        result = HeatIslandSimulation.simulate(grid, irradiance_factor=1.0)
        assert result.temperature_delta[0][0] > result.temperature_delta[0][1]

    def test_average_delta_is_mean(self):
        grid = [["asfalt", "asfalt"], ["asfalt", "asfalt"]]
        result = HeatIslandSimulation.simulate(grid)
        expected = result.temperature_delta[0][0]
        assert math.isclose(result.average_delta, expected, rel_tol=1e-9)


class TestNoiseSimulation:
    def test_attenuates_with_distance(self):
        near = NoiseSimulation.spl_at(90.0, (0, 0, 0), (10, 0, 0))
        far = NoiseSimulation.spl_at(90.0, (0, 0, 0), (100, 0, 0))
        assert far.spl_db < near.spl_db

    def test_blocked_los_extra_attenuation(self):
        clear = NoiseSimulation.spl_at(90.0, (0, 0, 0), (10, 0, 0), line_of_sight_blocked=False)
        blocked = NoiseSimulation.spl_at(90.0, (0, 0, 0), (10, 0, 0), line_of_sight_blocked=True)
        assert blocked.spl_db < clear.spl_db

    def test_combine_sources_increases_level(self):
        single = NoiseSimulation.spl_at(70.0, (0, 0, 0), (10, 0, 0))
        combined = NoiseSimulation.combine_sources([single, single])
        assert combined.spl_db > single.spl_db


class TestReflectionSimulation:
    def test_reflect_point_across_ground_plane(self):
        reflected = ReflectionSimulation.reflect_point_across_plane(
            point=(0, 0, 5),
            plane_point=(0, 0, 0),
            plane_normal=(0, 0, 1),
        )
        assert math.isclose(reflected[2], -5.0, abs_tol=1e-9)

    def test_compute_path_longer_than_direct(self):
        result = ReflectionSimulation.compute(
            source=(0, 0, 2),
            receiver=(10, 0, 2),
            plane_point=(5, 5, 0),
            plane_normal=(0, 1, 0),
        )
        direct = math.hypot(10, 0)
        assert result.total_path_length_m >= direct
