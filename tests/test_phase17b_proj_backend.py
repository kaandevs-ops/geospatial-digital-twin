"""Roadmap V2 - Faz 17 (kalan madde) - Gerçek PROJ/pyproj datum dönüşümü.

Bu test dosyası `core_engine.geo_reference.proj_backend`'i kapsar. `pyproj`
bu ortamda kuruluysa gerçek dönüşümler doğrulanır; kurulu değilse modülün
"sessizce yanlış sonuç üretmek yerine açık hata fırlatma" davranışı
doğrulanır (`pytest.importorskip` YERİNE bilinçli olarak `skip` etmiyoruz,
çünkü ProjBackendUnavailable davranışının kendisi test edilmesi gereken bir
özellik).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.geo_reference import REFERENCE_LOCATIONS, proj_backend


def test_is_available_matches_import():
    try:
        import pyproj  # noqa: F401

        assert proj_backend.is_available() is True
    except ImportError:
        assert proj_backend.is_available() is False


def test_unavailable_backend_raises_clear_error(monkeypatch):
    monkeypatch.setattr(proj_backend, "_PYPROJ_AVAILABLE", False)
    with pytest.raises(proj_backend.ProjBackendUnavailable):
        proj_backend.transform_datum(GeoPoint(lat=39.9, lon=32.8), 4326, 4230)


@pytest.mark.skipif(not proj_backend.is_available(), reason="pyproj kurulu değil")
class TestRealPyprojTransforms:
    def test_identity_transform_wgs84_to_wgs84(self):
        p = GeoPoint(lat=39.92077, lon=32.85411)
        result = proj_backend.transform_datum(p, 4326, 4326)
        assert result.lat == pytest.approx(p.lat, abs=1e-9)
        assert result.lon == pytest.approx(p.lon, abs=1e-9)

    def test_wgs84_to_web_mercator_matches_stdlib(self):
        from core_engine.coordinate_systems import CoordinateConverter

        p = GeoPoint(lat=39.92077, lon=32.85411)
        stdlib_result = CoordinateConverter.wgs84_to_web_mercator(p)

        transformer_result = proj_backend.transform_datum(p, 4326, 3857)
        # pyproj Web Mercator sonucu x/y olarak lon/lat alanlarında döner
        # (aynı `transform_datum` API'si generic tutulduğu için).
        assert transformer_result.lon == pytest.approx(stdlib_result.x, abs=0.5)
        assert transformer_result.lat == pytest.approx(stdlib_result.y, abs=0.5)

    def test_ed50_to_wgs84_produces_known_offset(self):
        # ED50->WGS84 tipik olarak Türkiye bölgesinde birkaç yüz metrelik
        # (saniyelerce derece) bir kayma üretir - bu, stdlib'in HİÇ
        # yapamadığı gerçek bir datum dönüşümüdür.
        p_ed50 = GeoPoint(lat=39.92077, lon=32.85411)
        result = proj_backend.transform_datum(p_ed50, 4230, 4326)
        # ED50 ve WGS84 birbirine çok yakındır ama özdeş değildir;
        # fark tipik olarak birkaç metre mertebesindedir (~0.00001-0.0001 derece).
        assert result.lat != p_ed50.lat
        assert abs(result.lat - p_ed50.lat) < 0.01
        assert abs(result.lon - p_ed50.lon) < 0.01

    def test_round_trip_error_is_small_for_all_reference_locations(self):
        for loc in REFERENCE_LOCATIONS:
            point = loc.point()
            err_m = proj_backend.round_trip_error_m(point, via_epsg=3857)
            assert err_m < 0.01, f"{loc.name}: {err_m} m"

    def test_proj_version_reported(self):
        assert proj_backend.proj_version() is not None
