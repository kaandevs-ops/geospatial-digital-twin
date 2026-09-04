"""yeni_roadmap.md Faz 2.4 - Gerçek yükseklik/arazi verisi (Open-Elevation).

Sandbox ağ erişimi api.open-elevation.com'a izin vermediği için testler
`urllib.request.urlopen`'i mock'lar; şema-parse + HeightmapGrid dönüşümü +
offline fallback mantığını doğrular.
"""

from __future__ import annotations

import io
import json
from unittest.mock import patch

import pytest
from harita.core_engine.gis_core.elevation_client import (
    ElevationClient,
    ElevationError,
    ElevationNetworkError,
    fetch_heightmap_grid,
    fetch_terrain_or_flat,
)
from harita.terrain_engine import HeightmapGrid


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_lookup_response(points: list[tuple[float, float]]) -> bytes:
    results = [
        {"latitude": lat, "longitude": lon, "elevation": 850.0 + i}
        for i, (lat, lon) in enumerate(points)
    ]
    return json.dumps({"results": results}).encode()


def test_lookup_parses_schema():
    client = ElevationClient()
    pts = [(39.92, 32.85), (39.93, 32.86)]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(_fake_lookup_response(pts))):
        samples = client.lookup(pts)
    assert len(samples) == 2
    assert samples[0].lat == pytest.approx(39.92)
    assert samples[1].elevation_m == pytest.approx(851.0)


def test_lookup_empty_points_returns_empty_without_network():
    client = ElevationClient()
    with patch("urllib.request.urlopen", side_effect=AssertionError("ağ çağrılmamalıydı")):
        assert client.lookup([]) == []


def test_lookup_network_error_raises():
    import urllib.error

    client = ElevationClient()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        with pytest.raises(ElevationNetworkError):
            client.lookup([(0.0, 0.0)])


def test_lookup_malformed_result_raises_elevation_error():
    client = ElevationClient()
    bad = json.dumps({"results": [{"latitude": 1.0}]}).encode()
    with patch("urllib.request.urlopen", return_value=_FakeResponse(bad)):
        with pytest.raises(ElevationError):
            client.lookup([(1.0, 1.0)])


def test_fetch_heightmap_grid_builds_correct_shape():
    client = ElevationClient()

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001
        body = json.loads(req.data)
        pts = [(loc["latitude"], loc["longitude"]) for loc in body["locations"]]
        return _FakeResponse(_fake_lookup_response(pts))

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        grid = fetch_heightmap_grid(
            client, south=39.90, west=32.80, north=39.95, east=32.90, grid_size=4
        )

    assert isinstance(grid, HeightmapGrid)
    assert grid.width == 4 and grid.height == 4
    assert len(grid.elevations) == 4
    assert all(len(row) == 4 for row in grid.elevations)
    assert grid.resolution_m > 0
    assert grid.origin.lat == pytest.approx(39.90)
    assert grid.origin.lon == pytest.approx(32.80)


def test_fetch_heightmap_grid_rejects_grid_size_below_2():
    client = ElevationClient()
    with pytest.raises(ValueError):
        fetch_heightmap_grid(client, south=0, west=0, north=1, east=1, grid_size=1)


def test_fetch_terrain_or_flat_falls_back_when_network_unavailable():
    import urllib.error

    client = ElevationClient()
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
        grid, source = fetch_terrain_or_flat(
            client, south=39.9, west=32.8, north=39.95, east=32.9, grid_size=4
        )

    assert source == "flat-fallback"
    assert isinstance(grid, HeightmapGrid)
    assert all(v == 0.0 for row in grid.elevations for v in row)


def test_fetch_terrain_or_flat_uses_network_result_when_available():
    client = ElevationClient()

    def _fake_urlopen(req, timeout=None):  # noqa: ANN001
        body = json.loads(req.data)
        pts = [(loc["latitude"], loc["longitude"]) for loc in body["locations"]]
        return _FakeResponse(_fake_lookup_response(pts))

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        grid, source = fetch_terrain_or_flat(
            client, south=39.9, west=32.8, north=39.95, east=32.9, grid_size=4
        )

    assert source == "open-elevation"
    assert grid.elevations[0][0] == pytest.approx(850.0)
