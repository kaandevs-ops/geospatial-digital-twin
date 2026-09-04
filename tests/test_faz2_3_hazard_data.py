"""Roadmap Faz 2.3 — Deprem ve afet verisi.

Offline testler (AFAD/USGS JSON parse, PGA lookup, risk skorlama,
tahliye önceliklendirme) her ortamda çalışır. Canlı ağ testleri gerçek
erişim yoksa açıkça `skip` edilir.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.hazard_data.afad_client import (
    AFADClient, DEFAULT_AFAD_ENDPOINTS, DEFAULT_USER_AGENT as AFAD_UA,
    HazardNetworkError, HazardParseError, parse_afad_response,
)
from harita.hazard_data.usgs_client import (
    USGSClient, parse_usgs_geojson,
)
from harita.hazard_data.pga_estimate import RegionalPGAEstimate, PGAZone
from harita.hazard_data.risk_scoring import (
    RiskLevel, SoilType, score_building_risk,
)
from harita.hazard_data.evacuation import prioritize_evacuation


# ---------------------------------------------------------------------------
# AFAD parsing (offline, fixture veri)
# ---------------------------------------------------------------------------

class TestAFADParsing:
    def test_parse_list_response(self):
        raw = [
            {
                "eventID": "20260101000000",
                "date": "2026-01-01 03:15:00",
                "latitude": 39.93, "longitude": 32.86,
                "depth": 7.2, "magnitude": 4.1, "magnitudeType": "ML",
                "location": "Ankara",
            }
        ]
        events = parse_afad_response(raw)
        assert len(events) == 1
        ev = events[0]
        assert ev.event_id == "20260101000000"
        assert ev.magnitude == 4.1
        assert ev.location_name == "Ankara"
        assert ev.time_utc.tzinfo is not None

    def test_parse_dict_wrapped_response(self):
        raw = {"result": [
            {"id": "x1", "eventDate": "2026-02-02 10:00:00", "latitude": 38.42,
             "longitude": 27.14, "depth": 10.0, "mag": 3.5, "magType": "Mw",
             "place": "İzmir"},
        ]}
        events = parse_afad_response(raw)
        assert len(events) == 1
        assert events[0].location_name == "İzmir"

    def test_unexpected_schema_raises_parse_error_not_silent_empty(self):
        with pytest.raises(HazardParseError):
            parse_afad_response({"unexpected_key": []})

    def test_malformed_event_raises_parse_error(self):
        with pytest.raises(HazardParseError):
            parse_afad_response([{"latitude": "not-a-number", "longitude": 1.0}])

    def test_all_endpoints_fail_raises_network_error_not_empty_list(self):
        client = AFADClient(endpoints=("https://127.0.0.1:1/definitely-down",), timeout_s=1.0)
        with pytest.raises(HazardNetworkError):
            client.fetch_raw(min_lat=39.0, max_lat=40.0, min_lon=32.0, max_lon=33.0)


# ---------------------------------------------------------------------------
# USGS parsing (offline, fixture GeoJSON)
# ---------------------------------------------------------------------------

class TestUSGSParsing:
    def test_parse_geojson_features(self):
        raw = {
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "us7000abcd",
                    "properties": {"mag": 5.2, "magType": "mww", "place": "12km E of Van, Turkey",
                                    "time": 1735689600000},
                    "geometry": {"type": "Point", "coordinates": [43.38, 38.49, 10.0]},
                }
            ],
        }
        events = parse_usgs_geojson(raw)
        assert len(events) == 1
        ev = events[0]
        assert ev.magnitude == 5.2
        assert ev.latitude == 38.49
        assert ev.longitude == 43.38
        assert ev.depth_km == 10.0

    def test_missing_features_key_raises_parse_error(self):
        with pytest.raises(HazardParseError):
            parse_usgs_geojson({"type": "FeatureCollection"})


# ---------------------------------------------------------------------------
# PGA tahmini (offline lookup)
# ---------------------------------------------------------------------------

class TestPGAEstimate:
    def test_nearest_zone_used_for_known_city(self):
        estimator = RegionalPGAEstimate()
        result = estimator.estimate(41.00, 28.97)  # İstanbul yakını
        assert result.zone_name is not None
        assert "İstanbul" in result.zone_name
        # Artık tek-en-yakın-nokta değil, IDW (ters-mesafe-ağırlıklı)
        # enterpolasyon kullanılıyor — en yakın nokta İstanbul olduğu için
        # değer İstanbul'un 0.40'ına çok yakın olmalı, ama komşu referans
        # noktalarının küçük etkisiyle birebir aynı olmayabilir.
        assert result.pga_g == pytest.approx(0.40, rel=0.01)
        assert not result.is_official_source

    def test_far_point_falls_back_to_default(self):
        estimator = RegionalPGAEstimate(max_zone_distance_km=50.0)
        result = estimator.estimate(0.0, 0.0)  # Gine Körfezi - hiçbir bölgeye yakın değil
        assert result.pga_g == estimator.fallback_pga_g
        assert result.zone_name is None

    def test_official_lookup_fn_takes_priority(self):
        estimator = RegionalPGAEstimate(lookup_fn=lambda lat, lon: 0.77)
        result = estimator.estimate(41.00, 28.97)
        assert result.pga_g == 0.77
        assert result.is_official_source

    def test_lookup_fn_returning_none_falls_back_to_zones(self):
        estimator = RegionalPGAEstimate(lookup_fn=lambda lat, lon: None)
        result = estimator.estimate(41.00, 28.97)
        assert not result.is_official_source
        assert result.zone_name is not None


# ---------------------------------------------------------------------------
# Risk skorlama
# ---------------------------------------------------------------------------

class TestRiskScoring:
    def test_old_building_high_pga_is_high_risk(self):
        report = score_building_risk(
            pga_g=0.55, construction_year=1985, floor_count=6,
            slenderness_ratio=5.0, soil_type=SoilType.SOFT_SOIL,
        )
        assert report.risk_level in (RiskLevel.HIGH, RiskLevel.VERY_HIGH)
        # Disclaimer dili "gösterge" yerine daha doğru bir ifadeye
        # ("ön değerlendirme" + yöntem adı) güncellendi.
        assert "ÖN DEĞERLENDİRME" in report.disclaimer

    def test_new_building_low_pga_is_low_risk(self):
        report = score_building_risk(
            pga_g=0.15, construction_year=2021, floor_count=2,
            slenderness_ratio=1.5, soil_type=SoilType.ROCK,
        )
        assert report.risk_level == RiskLevel.LOW

    def test_missing_optional_fields_excluded_not_assumed(self):
        report = score_building_risk(pga_g=0.30)
        for factor in report.factors:
            if factor.name != "Bölgesel PGA":
                assert factor.subscore_0_100 is None
        # PGA tek başına yine de bir indeks üretmeli (weight_total > 0 yolu).
        assert 0.0 <= report.risk_index_0_100 <= 100.0

    def test_higher_pga_never_decreases_risk_all_else_equal(self):
        low = score_building_risk(pga_g=0.10, construction_year=2020, floor_count=3)
        high = score_building_risk(pga_g=0.55, construction_year=2020, floor_count=3)
        assert high.risk_index_0_100 > low.risk_index_0_100

    def test_summary_lines_mentions_disclaimer(self):
        report = score_building_risk(pga_g=0.4)
        lines = report.summary_lines()
        assert any("ÖN DEĞERLENDİRME" in line for line in lines)


# ---------------------------------------------------------------------------
# Tahliye önceliklendirme
# ---------------------------------------------------------------------------

class TestEvacuationPriority:
    def test_higher_risk_building_ranked_first(self):
        low = score_building_risk(pga_g=0.15, construction_year=2020, floor_count=2)
        high = score_building_risk(pga_g=0.55, construction_year=1980, floor_count=10)
        ranked = prioritize_evacuation([("bina-A-lowrisk", low), ("bina-B-highrisk", high)])
        assert ranked[0].building_id == "bina-B-highrisk"
        assert ranked[0].rank == 1
        assert ranked[1].building_id == "bina-A-lowrisk"

    def test_equal_risk_tiebreak_by_occupant_count(self):
        r1 = score_building_risk(pga_g=0.3)
        r2 = score_building_risk(pga_g=0.3)
        ranked = prioritize_evacuation(
            [("az-kisi", r1), ("cok-kisi", r2)],
            occupant_estimates={"az-kisi": 5, "cok-kisi": 500},
        )
        assert ranked[0].building_id == "cok-kisi"

    def test_ranks_are_contiguous_starting_at_one(self):
        r = score_building_risk(pga_g=0.2)
        ranked = prioritize_evacuation([("a", r), ("b", r), ("c", r)])
        assert [p.rank for p in ranked] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Canlı ağ testleri — yalnızca gerçek erişim varsa çalışır
# ---------------------------------------------------------------------------

def _afad_reachable() -> bool:
    import urllib.error
    import urllib.request
    try:
        request = urllib.request.Request(
            DEFAULT_AFAD_ENDPOINTS[0], headers={"User-Agent": AFAD_UA},
        )
        with urllib.request.urlopen(request, timeout=4.0) as response:
            return response.status < 400
    except Exception:
        return False


def _usgs_reachable() -> bool:
    import urllib.error
    import urllib.request
    try:
        request = urllib.request.Request(
            "https://earthquake.usgs.gov/fdsnws/event/1/query?format=geojson&limit=1",
        )
        with urllib.request.urlopen(request, timeout=4.0) as response:
            return response.status < 400
    except Exception:
        return False


@pytest.mark.skipif(not _afad_reachable(), reason="Bu ortamda AFAD'a ağ erişimi yok.")
class TestLiveAFAD:
    def test_live_fetch(self):
        client = AFADClient()
        events = client.fetch_earthquakes(
            min_lat=35.0, max_lat=43.0, min_lon=25.0, max_lon=45.0, min_magnitude=4.0,
        )
        assert isinstance(events, list)


@pytest.mark.skipif(not _usgs_reachable(), reason="Bu ortamda USGS'e ağ erişimi yok.")
class TestLiveUSGS:
    def test_live_fetch(self):
        client = USGSClient()
        events = client.fetch_earthquakes(
            min_lat=35.0, max_lat=43.0, min_lon=25.0, max_lon=45.0, min_magnitude=4.0, limit=10,
        )
        assert isinstance(events, list)
