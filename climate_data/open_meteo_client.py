"""Open-Meteo (open-meteo.com) stdlib-only iklim/güneş verisi istemcisi.

`hazard_data.afad_client.AFADClient` ile aynı desen: `urllib.request`,
API key gerektirmez, ağa ulaşılamazsa sessizce sahte veri üretmek yerine
açık `ClimateNetworkError` fırlatır. Amaç: `analysis_engine.sun_simulation`
içindeki güneş/gölge hesaplarını varsayılan/sabit değerler yerine gerçek
bulutluluk, sıcaklık ve güneşlenme (irradiance) verisiyle beslemek.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_type


class ClimateError(Exception):
    """İklim verisiyle ilgili genel hata."""


class ClimateNetworkError(ClimateError):
    """Tüm uç noktalar denendi ve hiçbirine ulaşılamadı."""


class ClimateParseError(ClimateError):
    """Yanıt geldi ama beklenen şemaya uymuyor."""


#: Open-Meteo ücretsiz, API key gerektirmeyen tahmin + arşiv servisleri.
#: forecast: son 16 gün ileri; archive: geçmiş tarihli (ERA5 reanaliz) veri.
DEFAULT_FORECAST_ENDPOINTS: tuple[str, ...] = ("https://api.open-meteo.com/v1/forecast",)
DEFAULT_ARCHIVE_ENDPOINTS: tuple[str, ...] = ("https://archive-api.open-meteo.com/v1/archive",)

DEFAULT_USER_AGENT = "harita-modelleme-platformu/faz2.4 (open-meteo-client)"

#: RoofIrradiance / SolarExposure hesaplarını beslemek için istenen saatlik alanlar.
DEFAULT_HOURLY_VARS: tuple[str, ...] = (
    "temperature_2m",
    "cloud_cover",
    "shortwave_radiation",
    "direct_radiation",
    "diffuse_radiation",
)

#: ROADMAP_V9 Faz VI / Katman 3.3 madde 4 ("Hava durumu etkisi ...
#: yağış/görüş mesafesi verisi IDM parametrelerini hafifçe değiştirir") -
#: `DEFAULT_HOURLY_VARS`'a EKLENMEDİ (geriye uyumluluk: mevcut
#: `analysis_engine.sun_simulation` çağrıları hâlâ eskisiyle aynı 5 alanı
#: alır), yalnızca `mobility.weather_traffic_effect`'in isteyerek talep
#: edeceği ek bir küme olarak tanımlandı.
TRAFFIC_WEATHER_HOURLY_VARS: tuple[str, ...] = DEFAULT_HOURLY_VARS + (
    "precipitation",
    "visibility",
)


@dataclass(slots=True, frozen=True)
class HourlyClimateSample:
    """Tek bir saatlik iklim/güneşlenme örneği."""

    time_iso: str
    temperature_c: float | None
    cloud_cover_pct: float | None
    shortwave_radiation_wm2: float | None
    direct_radiation_wm2: float | None
    diffuse_radiation_wm2: float | None
    # ROADMAP_V9 Faz VI / Katman 3.3 madde 4 — yalnızca `hourly_vars`
    # `TRAFFIC_WEATHER_HOURLY_VARS` ile istenirse dolar; aksi halde `None`
    # kalır (mevcut çağıranlar için sessiz/zararsız varsayılan).
    precipitation_mm: float | None = None
    visibility_m: float | None = None

    def to_dict(self) -> dict:
        return {
            "time_iso": self.time_iso,
            "temperature_c": self.temperature_c,
            "cloud_cover_pct": self.cloud_cover_pct,
            "shortwave_radiation_wm2": self.shortwave_radiation_wm2,
            "direct_radiation_wm2": self.direct_radiation_wm2,
            "diffuse_radiation_wm2": self.diffuse_radiation_wm2,
            "precipitation_mm": self.precipitation_mm,
            "visibility_m": self.visibility_m,
        }


@dataclass
class OpenMeteoClient:
    """Open-Meteo için stdlib-only istemci (tahmin + arşiv)."""

    forecast_endpoints: Sequence[str] = DEFAULT_FORECAST_ENDPOINTS
    archive_endpoints: Sequence[str] = DEFAULT_ARCHIVE_ENDPOINTS
    timeout_s: float = 20.0
    user_agent: str = DEFAULT_USER_AGENT

    def _fetch_json(self, endpoints: Sequence[str], params: dict) -> dict:
        query = urllib.parse.urlencode(params)
        last_error: Exception | None = None

        for endpoint in endpoints:
            url = f"{endpoint}?{query}"
            request = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw_bytes = response.read()
                return json.loads(raw_bytes.decode("utf-8"))
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                OSError,
                ValueError,
            ) as exc:
                last_error = exc
                continue

        raise ClimateNetworkError(
            f"Hiçbir Open-Meteo uç noktasına ulaşılamadı (denenen {len(endpoints)} "
            f"uç nokta): {last_error!r}"
        )

    def fetch_raw(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: date_type,
        end_date: date_type,
        hourly_vars: Sequence[str] = DEFAULT_HOURLY_VARS,
        use_archive: bool = False,
    ) -> dict:
        """Bir nokta + tarih aralığı için ham Open-Meteo JSON yanıtını çeker.

        `use_archive=True` ise geçmiş tarihli (ERA5 reanaliz) arşiv servisi,
        aksi halde son 16 günlük tahmin servisi kullanılır. Open-Meteo
        forecast servisi geçmişe dönük sorguları da (~92 gün) destekler;
        daha eskisi için `use_archive=True` gerekir.
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "hourly": ",".join(hourly_vars),
            "timezone": "UTC",
        }
        endpoints = self.archive_endpoints if use_archive else self.forecast_endpoints
        return self._fetch_json(endpoints, params)

    def fetch_hourly(
        self,
        *,
        latitude: float,
        longitude: float,
        start_date: date_type,
        end_date: date_type,
        hourly_vars: Sequence[str] = DEFAULT_HOURLY_VARS,
        use_archive: bool = False,
    ) -> list[HourlyClimateSample]:
        raw = self.fetch_raw(
            latitude=latitude,
            longitude=longitude,
            start_date=start_date,
            end_date=end_date,
            hourly_vars=hourly_vars,
            use_archive=use_archive,
        )
        return parse_open_meteo_hourly(raw)


def parse_open_meteo_hourly(raw: dict) -> list[HourlyClimateSample]:
    """Open-Meteo'nun `hourly: {time: [...], var: [...]}` sütunsal şemasını
    `HourlyClimateSample` listesine çevirir."""
    if not isinstance(raw, dict) or "hourly" not in raw:
        raise ClimateParseError(
            f"Beklenmeyen Open-Meteo yanıt şeması: {list(raw.keys()) if isinstance(raw, dict) else type(raw)}"
        )

    hourly = raw["hourly"]
    times = hourly.get("time")
    if not isinstance(times, list):
        raise ClimateParseError("Open-Meteo yanıtında 'hourly.time' alanı yok/liste değil")

    def col(name: str) -> list:
        values = hourly.get(name)
        return values if isinstance(values, list) else [None] * len(times)

    temps = col("temperature_2m")
    clouds = col("cloud_cover")
    shortwave = col("shortwave_radiation")
    direct = col("direct_radiation")
    diffuse = col("diffuse_radiation")
    precipitation = col("precipitation")
    visibility = col("visibility")

    samples: list[HourlyClimateSample] = []
    for i, t in enumerate(times):
        samples.append(
            HourlyClimateSample(
                time_iso=t,
                temperature_c=temps[i] if i < len(temps) else None,
                cloud_cover_pct=clouds[i] if i < len(clouds) else None,
                shortwave_radiation_wm2=shortwave[i] if i < len(shortwave) else None,
                direct_radiation_wm2=direct[i] if i < len(direct) else None,
                diffuse_radiation_wm2=diffuse[i] if i < len(diffuse) else None,
                precipitation_mm=precipitation[i] if i < len(precipitation) else None,
                visibility_m=visibility[i] if i < len(visibility) else None,
            )
        )
    return samples
