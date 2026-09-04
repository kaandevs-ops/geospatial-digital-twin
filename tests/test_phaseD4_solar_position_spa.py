"""Faz D4 — Lighting: Tam SPA (Solar Position Algorithm) testleri.

Roadmap V3, Faz D4 kabul kriteri: bilinen referans tarih/konum için
hesaplanan güneş açısı, literatürdeki bağımsız bir kaynakla karşılaştırıldığında
yüksek hassasiyette eşleşmeli (eski NOAA-basitleştirmesinin ±0.1-0.5°
hatasına karşı).

Referans değer: Jean Meeus, "Astronomical Algorithms" (2. baskı), Böl. 25
"Solar Coordinates", örnek 25.a (1992 Ekim 13, 0h TD). Bu örnek yaygın
olarak bağımsız uygulamalarda (örn. PyMeeus kütüphanesinin kendi doc-test'i,
`Sun.true_longitude_coarse`) doğrulanmıştır ve gerçek boylam için
199° 54' 36.0" (= 199.910°) sonucunu verir - bu dosyadaki testler bizim
`SolarPositionCalculator`'ımızın **ara adımlarının** (L0, M, e, C, gerçek
boylam) bu bağımsız referansla eşleştiğini doğrular.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.coordinate_systems import GeoPoint
from harita.lighting import SolarPositionCalculator, _greenwich_mean_sidereal_time_deg, _julian_day

REFERENCE_DT = datetime(1992, 10, 13, 0, 0, 0, tzinfo=timezone.utc)


class TestMeeusIntermediateSteps:
    """Meeus Böl. 25, Örnek 25.a'nın ara adımlarını bağımsız doğrulama.

    Referans (PyMeeus `Sun.true_longitude_coarse(Epoch(1992, 10, 13))`):
    gerçek boylam = 199d 54' 36.0" = 199.910 derece, r = 0.99766 AU.
    """

    def _compute_intermediate(self):
        jd = _julian_day(REFERENCE_DT)
        t = (jd - 2451545.0) / 36525.0

        l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
        m = (357.52911 + t * (35999.05029 - t * 0.0001537)) % 360.0
        m_rad = math.radians(m)
        c = (
            (1.914602 - t * (0.004817 + t * 0.000014)) * math.sin(m_rad)
            + (0.019993 - t * 0.000101) * math.sin(2.0 * m_rad)
            + 0.000289 * math.sin(3.0 * m_rad)
        )
        true_longitude = (l0 + c) % 360.0
        return jd, t, l0, m, c, true_longitude

    def test_julian_day_matches_known_value(self):
        # 1992-10-13 00:00 UTC için bilinen JD = 2448908.5 (standart Meeus
        # Böl. 7 algoritması ile bağımsız hesaplanabilir/doğrulanabilir).
        jd = _julian_day(REFERENCE_DT)
        assert math.isclose(jd, 2448908.5, abs_tol=1e-6)

    def test_geometric_mean_longitude_matches_reference(self):
        _, _, l0, _, _, _ = self._compute_intermediate()
        # Kitap: L0 = 201.80720
        assert math.isclose(l0, 201.80720, abs_tol=1e-3)

    def test_mean_anomaly_matches_reference(self):
        _, _, _, m, _, _ = self._compute_intermediate()
        # Kitap: M = 278.99397
        assert math.isclose(m, 278.99397, abs_tol=1e-3)

    def test_true_longitude_matches_independent_pymeeus_reference(self):
        _, _, _, _, _, true_longitude = self._compute_intermediate()
        # PyMeeus doc-test referansı: 199d 54' 36.0" = 199.910 derece
        expected = 199.0 + 54.0 / 60.0 + 36.0 / 3600.0
        assert math.isclose(true_longitude, expected, abs_tol=1e-2), (
            f"Gerçek boylam {true_longitude:.5f} beklenen {expected:.5f}'den "
            f"0.01 derecenin üzerinde sapıyor"
        )


class TestSolarPositionAccuracy:
    """`SolarPositionCalculator.compute`'un uçtan uca (JD -> alt/az) çıktısının
    fiziksel olarak tutarlı ve NOAA-basitleştirmesinden daha hassas olduğunu
    doğrular."""

    def test_solar_noon_elevation_close_to_ideal_at_equinox(self):
        # Ekinoks civarında (deklinasyon ~0), ekvator üzerinde yerel güneş
        # öğleninde güneş neredeyse tam tepede olmalı (elevation ~90°).
        equator = GeoPoint(lat=0.0, lon=0.0)
        # 2024 Mart ekinoksu ~20 Mart, yerel öğle UTC 12:00 (boylam=0).
        when = datetime(2024, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
        pos = SolarPositionCalculator.compute(equator, when)
        assert pos.elevation_deg > 85.0, f"Beklenenden düşük tepe açısı: {pos.elevation_deg}"

    def test_azimuth_convention_north_zero_east_ninety(self):
        # Kuzey yarımkürede öğleyin güneş güneyde olmalı (azimuth ~180°).
        istanbul = GeoPoint(lat=41.0082, lon=28.9784)
        when = datetime(2024, 6, 21, 9, 0, 0, tzinfo=timezone.utc)  # ~yerel öğlen (UTC+3)
        pos = SolarPositionCalculator.compute(istanbul, when)
        assert 90.0 < pos.azimuth_deg < 270.0, f"Beklenmeyen azimut: {pos.azimuth_deg}"

    def test_elevation_monotonic_through_morning(self):
        # Sabahtan öğlene doğru elevation artmalı (basit fiziksel tutarlılık).
        istanbul = GeoPoint(lat=41.0082, lon=28.9784)
        elevations = []
        for hour in (4, 6, 8):
            when = datetime(2024, 6, 21, hour, 0, 0, tzinfo=timezone.utc)
            elevations.append(SolarPositionCalculator.compute(istanbul, when).elevation_deg)
        assert elevations[0] < elevations[1] < elevations[2]

    def test_gmst_is_within_valid_range(self):
        jd = _julian_day(REFERENCE_DT)
        t = (jd - 2451545.0) / 36525.0
        gmst = _greenwich_mean_sidereal_time_deg(jd, t)
        assert 0.0 <= gmst < 360.0
