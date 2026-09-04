"""`climate_data.OpenMeteoClient` <-> `analysis_engine.sun_simulation.RoofIrradiance`
gerçek entegrasyonu.

Önceki oturumda tespit edilen sorun: `RoofIrradiance` yalnızca CLEAR-SKY
(bulutsuz varsayım) modeliyle çalışıyordu; docstring'i "gerçek meteorolojik
girdiler climate_data'dan alınabilir" diyordu ama bunu gerçekten yapan kod
yoktu. Bu dosya, eklenen `compute_with_real_climate()` /
`daily_energy_kwh_per_m2_with_real_climate()`'in:
    1. Gerçek bulut örtüsü verisiyle clear-sky sonucunu doğru ölçeklendirdiğini
       (Kasten & Czeplak 1980 - yayınlanmış, uydurulmamış bir formül),
    2. Bulut verisi eksikse clear-sky'a SESSİZCE değil, açıkça (aynı
       değeri döndürerek, gizlemeden) düştüğünü,
    3. Ağ erişimi olmadığında (`OpenMeteoClient` gerçek `api.open-meteo.com`'a
       ulaşamazsa) `ClimateNetworkError`'ın sessiz sahte veri yerine
       doğrudan çağırana yükseldiğini
doğrular. Bu ortamın ağ erişimi `api.open-meteo.com`'u içermediği için (3)
canlı olarak test edilir - gerçekten fırlaması beklenir.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.analysis_engine.sun_simulation import RoofIrradiance, SolarPositionCalculator
from harita.climate_data import ClimateError, HourlyClimateSample, OpenMeteoClient
from harita.core_engine.coordinate_systems import GeoPoint

_ANKARA = GeoPoint(lat=39.92, lon=32.85)
_NOON_SUMMER = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)


def _sample(cloud_pct):
    return HourlyClimateSample(
        time_iso=_NOON_SUMMER.isoformat(),
        temperature_c=25.0,
        cloud_cover_pct=cloud_pct,
        shortwave_radiation_wm2=None,
        direct_radiation_wm2=None,
        diffuse_radiation_wm2=None,
    )


def test_zero_cloud_cover_matches_clear_sky():
    sun = SolarPositionCalculator.compute(_ANKARA, _NOON_SUMMER)
    clear = RoofIrradiance.compute(sun)
    real = RoofIrradiance.compute_with_real_climate(sun, _sample(0.0))
    assert abs(real.watts_per_m2 - clear.watts_per_m2) < 1e-6


def test_full_cloud_cover_reduces_to_kasten_czeplak_ratio():
    sun = SolarPositionCalculator.compute(_ANKARA, _NOON_SUMMER)
    clear = RoofIrradiance.compute(sun)
    real = RoofIrradiance.compute_with_real_climate(sun, _sample(100.0))
    # Kasten-Czeplak: k_c = 1 - 0.75*(8/8)^3.4 = 0.25
    expected = clear.watts_per_m2 * 0.25
    assert abs(real.watts_per_m2 - expected) < 1e-3


def test_missing_cloud_data_falls_back_to_clear_sky_explicitly():
    sun = SolarPositionCalculator.compute(_ANKARA, _NOON_SUMMER)
    clear = RoofIrradiance.compute(sun)
    real = RoofIrradiance.compute_with_real_climate(sun, _sample(None))
    assert real.watts_per_m2 == clear.watts_per_m2


def test_cloud_cover_is_monotonic():
    sun = SolarPositionCalculator.compute(_ANKARA, _NOON_SUMMER)
    vals = [
        RoofIrradiance.compute_with_real_climate(sun, _sample(p)).watts_per_m2
        for p in (0.0, 25.0, 50.0, 75.0, 100.0)
    ]
    assert vals == sorted(vals, reverse=True)


def test_daily_energy_with_real_climate_raises_cleanly_without_network():
    """Bu sandbox'ın ağ erişimi yalnızca paket-indirme domain'leriyle sınırlı
    (`api.open-meteo.com` YOK) - bu yüzden gerçek bir ağ hatası bekleniyor.
    Roadmap ilkesi: sessizce clear-sky'a düşülmez, hata açıkça yükselir."""
    try:
        RoofIrradiance.daily_energy_kwh_per_m2_with_real_climate(
            _ANKARA,
            _NOON_SUMMER,
            climate_client=OpenMeteoClient(timeout_s=3.0),
        )
        raise AssertionError(
            "Bu ortamda api.open-meteo.com'a erişim olmamalıydı; eğer bu satıra "
            "geldiyse ağ erişimi genişletilmiş olabilir - testi gözden geçirin."
        )
    except ClimateError:
        pass


_ALL_TESTS = [v for k, v in list(globals().items()) if k.startswith("test_")]


if __name__ == "__main__":
    failures = []
    for fn in _ALL_TESTS:
        try:
            fn()
            print(f"OK   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failures.append((fn.__name__, exc))
            print(f"FAIL {fn.__name__}: {exc}")
    print(f"\n{len(_ALL_TESTS) - len(failures)}/{len(_ALL_TESTS)} geçti.")
    if failures:
        sys.exit(1)
