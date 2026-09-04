"""USGS deprem kataloğu istemcisi — AFAD'ın uluslararası kapsam dışı kaldığı
durumlar için yedek/alternatif kaynak (roadmap Faz 2.3'ün kendi maddesi).

USGS'in `earthquake.usgs.gov` GeoJSON servisi resmi olarak belgelidir ve
şema AFAD'a göre çok daha stabildir (tek uç nokta, tek şema).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .afad_client import HazardNetworkError, HazardParseError

DEFAULT_USGS_ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/query"
DEFAULT_USER_AGENT = "harita-modelleme-platformu/faz2.3 (usgs-client)"


@dataclass(slots=True, frozen=True)
class USGSEarthquake:
    event_id: str
    time_utc: datetime
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    magnitude_type: str
    place: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "time_utc": self.time_utc.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depth_km": self.depth_km,
            "magnitude": self.magnitude,
            "magnitude_type": self.magnitude_type,
            "place": self.place,
        }


@dataclass
class USGSClient:
    endpoint: str = DEFAULT_USGS_ENDPOINT
    timeout_s: float = 20.0
    user_agent: str = DEFAULT_USER_AGENT

    def fetch_raw(
        self,
        *,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_magnitude: float = 0.0,
        limit: int = 200,
    ) -> dict:
        params = {
            "format": "geojson",
            "minlatitude": min_lat, "maxlatitude": max_lat,
            "minlongitude": min_lon, "maxlongitude": max_lon,
            "minmagnitude": min_magnitude,
            "limit": limit,
            "orderby": "time",
        }
        if start is not None:
            params["starttime"] = start.strftime("%Y-%m-%d")
        if end is not None:
            params["endtime"] = end.strftime("%Y-%m-%d")

        url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                raw_bytes = response.read()
            return json.loads(raw_bytes.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                OSError, ValueError) as exc:
            raise HazardNetworkError(f"USGS uç noktasına ulaşılamadı: {exc!r}") from exc

    def fetch_earthquakes(
        self,
        *,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        min_magnitude: float = 0.0,
        limit: int = 200,
    ) -> List[USGSEarthquake]:
        raw = self.fetch_raw(
            min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
            start=start, end=end, min_magnitude=min_magnitude, limit=limit,
        )
        return parse_usgs_geojson(raw)


def parse_usgs_geojson(raw: dict) -> List[USGSEarthquake]:
    if not isinstance(raw, dict) or "features" not in raw:
        raise HazardParseError(
            f"Beklenmeyen USGS GeoJSON şeması: {type(raw).__name__} "
            f"(anahtarlar={list(raw.keys()) if isinstance(raw, dict) else 'yok'})"
        )
    results: List[USGSEarthquake] = []
    for feature in raw["features"]:
        try:
            props = feature["properties"]
            lon, lat, depth = feature["geometry"]["coordinates"]
            time_ms = props["time"]
            results.append(USGSEarthquake(
                event_id=str(feature.get("id", "")),
                time_utc=datetime.fromtimestamp(time_ms / 1000.0, tz=timezone.utc),
                latitude=float(lat),
                longitude=float(lon),
                depth_km=float(depth),
                magnitude=float(props.get("mag") or 0.0),
                magnitude_type=str(props.get("magType") or "Mw"),
                place=str(props.get("place") or ""),
            ))
        except (KeyError, ValueError, TypeError) as exc:
            raise HazardParseError(f"USGS feature ayrıştırılamadı: {feature!r} ({exc})") from exc
    return results
