"""Roadmap V2 - Faz 17 - Coğrafi Doğruluk & WMTS/WMS testleri."""

from __future__ import annotations

import pytest

from harita.core_engine.coordinate_systems import CoordinateConverter, GeoPoint
from harita.core_engine.geo_reference import (
    REFERENCE_DISTANCES,
    REFERENCE_LOCATIONS,
    run_geodetic_accuracy_suite,
)
from harita.core_engine.tile_engine import TileCoordinate
from harita.core_engine.tile_sources import (
    TileFetchError,
    TileSourceConsumer,
    TileSourceError,
    WMSTileSource,
    WMTSTileSource,
    XYZTileSource,
)


# ------------------------------------------------------------------ #
# geo_reference
# ------------------------------------------------------------------ #

def test_reference_dataset_nonempty():
    assert len(REFERENCE_LOCATIONS) >= 5
    assert len(REFERENCE_DISTANCES) >= 1


def test_full_accuracy_suite_passes():
    report = run_geodetic_accuracy_suite()
    assert report.all_passed, report.summary()
    # Her lokasyon için 3 kontrol (zone + 2 round-trip) + mesafe kontrolleri
    expected_min = len(REFERENCE_LOCATIONS) * 3 + len(REFERENCE_DISTANCES)
    assert len(report.checks) == expected_min


def test_utm_zone_assignment_matches_known_locations():
    ankara = next(loc for loc in REFERENCE_LOCATIONS if "Ankara" in loc.name)
    assert CoordinateConverter.utm_zone_for(ankara.lon) == 36 == ankara.expected_utm_zone

    istanbul = next(loc for loc in REFERENCE_LOCATIONS if "İstanbul" in loc.name)
    assert CoordinateConverter.utm_zone_for(istanbul.lon) == 35 == istanbul.expected_utm_zone


def test_ankara_istanbul_distance_matches_published_value():
    ankara = next(loc for loc in REFERENCE_LOCATIONS if "Ankara" in loc.name).point()
    istanbul = next(loc for loc in REFERENCE_LOCATIONS if "İstanbul" in loc.name).point()
    km = CoordinateConverter.haversine_distance(ankara, istanbul) / 1000.0
    assert km == pytest.approx(349.0, abs=5.0)


def test_round_trip_error_is_submillimeter_for_all_references():
    for loc in REFERENCE_LOCATIONS:
        point = loc.point()
        utm = CoordinateConverter.wgs84_to_utm(point)
        back = CoordinateConverter.utm_to_wgs84(utm, northern_hemisphere=point.lat >= 0)
        err_m = CoordinateConverter.haversine_distance(point, back)
        assert err_m < 0.001, f"{loc.name}: {err_m} m"


def test_accuracy_report_summary_is_readable():
    report = run_geodetic_accuracy_suite()
    text = report.summary()
    assert "kontrol geçti" in text


def test_intentional_regression_is_detected():
    """Sıfır toleransla çağrıldığında (gerçekçi olmayan bir kabul kriteri),
    round-trip hatası > 0 olduğundan en az bir kontrol başarısız olmalı —
    bu, suite'in gerçekten hataları yakaladığını (yanlış-pozitif üretmediğini)
    kanıtlar."""
    report = run_geodetic_accuracy_suite(round_trip_tolerance_m=0.0)
    assert not report.all_passed
    assert len(report.failed) > 0


# ------------------------------------------------------------------ #
# tile_sources — XYZ
# ------------------------------------------------------------------ #

def test_xyz_build_url_basic():
    src = XYZTileSource(name="osm", url_template="https://tile.osm.org/{z}/{x}/{y}.png")
    coord = TileCoordinate(z=10, x=5, y=7)
    assert src.build_url(coord) == "https://tile.osm.org/10/5/7.png"


def test_xyz_subdomain_rotation():
    src = XYZTileSource(
        name="s", url_template="https://{s}.tiles.example.com/{z}/{x}/{y}.png",
        subdomains=("a", "b", "c"),
    )
    coord = TileCoordinate(z=1, x=0, y=0)
    urls = [src.build_url(coord, subdomain_index=i) for i in range(4)]
    assert urls == [
        "https://a.tiles.example.com/1/0/0.png",
        "https://b.tiles.example.com/1/0/0.png",
        "https://c.tiles.example.com/1/0/0.png",
        "https://a.tiles.example.com/1/0/0.png",
    ]


def test_xyz_missing_subdomain_config_raises():
    src = XYZTileSource(name="s", url_template="https://{s}.example.com/{z}/{x}/{y}.png")
    with pytest.raises(TileSourceError):
        src.build_url(TileCoordinate(0, 0, 0))


def test_xyz_zoom_over_max_raises():
    src = XYZTileSource(name="s", url_template="https://x/{z}/{x}/{y}.png", max_zoom=5)
    with pytest.raises(TileSourceError):
        src.build_url(TileCoordinate(z=6, x=0, y=0))


# ------------------------------------------------------------------ #
# tile_sources — WMTS
# ------------------------------------------------------------------ #

def test_wmts_kvp_build_url_contains_expected_params():
    src = WMTSTileSource(
        name="ortofoto", base_url="https://wmts.example.gov/service",
        layer="ortho2024", tile_matrix_set="GoogleMapsCompatible",
    )
    url = src.build_url(TileCoordinate(z=12, x=100, y=200))
    assert "REQUEST=GetTile" in url
    assert "LAYER=ortho2024" in url
    assert "TILEMATRIX=12" in url
    assert "TILECOL=100" in url
    assert "TILEROW=200" in url


def test_wmts_restful_build_url_uses_template():
    src = WMTSTileSource(
        name="restful",
        base_url="https://wmts.example.gov/{TileMatrix}/{TileCol}/{TileRow}.jpg",
        layer="ortho", tile_matrix_set="default028mm", kvp=False,
    )
    url = src.build_url(TileCoordinate(z=8, x=3, y=4))
    assert url == "https://wmts.example.gov/8/3/4.jpg"


def test_wmts_tile_matrix_prefix():
    src = WMTSTileSource(
        name="s", base_url="https://x", layer="l", tile_matrix_set="s",
        tile_matrix_prefix="EPSG:3857:",
    )
    url = src.build_url(TileCoordinate(z=5, x=1, y=1))
    assert "TILEMATRIX=EPSG%3A3857%3A5" in url


# ------------------------------------------------------------------ #
# tile_sources — WMS
# ------------------------------------------------------------------ #

def test_wms_build_url_bbox_axis_order_130():
    src = WMSTileSource(name="wms", base_url="https://wms.example.gov/ows", layers="ortho")
    url = src.build_url(TileCoordinate(z=1, x=1, y=0))
    assert "SERVICE=WMS" in url
    assert "REQUEST=GetMap" in url
    assert "CRS=EPSG%3A4326" in url
    assert "BBOX=" in url


def test_wms_111_uses_srs_and_lonlat_order():
    src = WMSTileSource(
        name="wms", base_url="https://wms.example.gov/ows", layers="ortho",
        version="1.1.1",
    )
    url = src.build_url(TileCoordinate(z=2, x=1, y=1))
    assert "SRS=EPSG%3A4326" in url
    assert "VERSION=1.1.1" in url


# ------------------------------------------------------------------ #
# TileSourceConsumer — pluggable fetcher (ağsız test)
# ------------------------------------------------------------------ #

class _FakeFetcher:
    def __init__(self, response: bytes = b"PNGDATA", raise_on: set[str] | None = None):
        self.response = response
        self.raise_on = raise_on or set()
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        if any(bad in url for bad in self.raise_on):
            raise ConnectionError("simulated network failure")
        return self.response


def test_consumer_fetch_tile_success():
    src = XYZTileSource(name="s", url_template="https://x/{z}/{x}/{y}.png")
    fetcher = _FakeFetcher(response=b"\x89PNG...")
    consumer = TileSourceConsumer(source=src, fetcher=fetcher)
    data = consumer.fetch_tile(TileCoordinate(z=3, x=1, y=1))
    assert data == b"\x89PNG..."
    assert consumer.stats["requests"] == 1
    assert consumer.stats["errors"] == 0
    assert fetcher.calls == ["https://x/3/1/1.png"]


def test_consumer_wraps_fetcher_errors_in_tile_fetch_error():
    src = XYZTileSource(name="s", url_template="https://x/{z}/{x}/{y}.png")
    fetcher = _FakeFetcher(raise_on={"3/1/1"})
    consumer = TileSourceConsumer(source=src, fetcher=fetcher)
    with pytest.raises(TileFetchError):
        consumer.fetch_tile(TileCoordinate(z=3, x=1, y=1))
    assert consumer.stats["errors"] == 1


def test_consumer_stats_accumulate_across_calls():
    src = XYZTileSource(name="s", url_template="https://x/{z}/{x}/{y}.png")
    fetcher = _FakeFetcher()
    consumer = TileSourceConsumer(source=src, fetcher=fetcher)
    for i in range(3):
        consumer.fetch_tile(TileCoordinate(z=1, x=i, y=0))
    assert consumer.stats["requests"] == 3
    assert consumer.stats["errors"] == 0
