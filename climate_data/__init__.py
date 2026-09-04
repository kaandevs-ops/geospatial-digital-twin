"""İklim/meteorolojik gerçek veri istemcileri (Open-Meteo, API key gerektirmez).

`hazard_data` paketiyle aynı mimari desen: stdlib-only `urllib.request`,
çoklu uç nokta fallback'i, sessiz sahte veri yerine açık network hatası.
`analysis_engine.sun_simulation` modülünü sabit/varsayılan değerler yerine
gerçek bulutluluk ve güneşlenme (irradiance) verisiyle beslemek için kullanılır.
"""

from .open_meteo_client import (
    ClimateError,
    ClimateNetworkError,
    ClimateParseError,
    HourlyClimateSample,
    OpenMeteoClient,
    parse_open_meteo_hourly,
)

__all__ = [
    "ClimateError",
    "ClimateNetworkError",
    "ClimateParseError",
    "HourlyClimateSample",
    "OpenMeteoClient",
    "parse_open_meteo_hourly",
]
