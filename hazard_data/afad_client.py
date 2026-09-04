"""AFAD (Türkiye Afet ve Acil Durum Yönetimi Başkanlığı) deprem kataloğu istemcisi.

`core_engine.gis_core.osm_client.OverpassClient` ile aynı desen: stdlib-only
(`urllib.request`), birden fazla uç nokta arasında sırayla fallback, ağ
erişimi yoksa (bu sandbox gibi) sessizce sahte veri üretmek yerine açık
`HazardNetworkError` fırlatır.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Sequence


class HazardError(Exception):
    """Deprem/afet verisiyle ilgili genel hata."""


class HazardNetworkError(HazardError):
    """Tüm uç noktalar denendi ve hiçbirine ulaşılamadı."""


class HazardParseError(HazardError):
    """Yanıt geldi ama beklenen şemaya uymuyor."""


#: AFAD'ın kamuya açık deprem servisi (resmi doküman: afad.gov.tr / deprem.afad.gov.tr).
#: Birincil + yedek (aynı servisin farklı yol varyantları) sırayla denenir.
DEFAULT_AFAD_ENDPOINTS: tuple[str, ...] = (
    "https://deprem.afad.gov.tr/apiv2/event/filter",
    "https://deprem.afad.gov.tr/EventData/GetEventsByFilter",
)

DEFAULT_USER_AGENT = "harita-modelleme-platformu/faz2.3 (afad-client)"


@dataclass(slots=True, frozen=True)
class AFADEarthquake:
    """Tek bir deprem kaydı (AFAD şemasından normalize edilmiş)."""

    event_id: str
    time_utc: datetime
    latitude: float
    longitude: float
    depth_km: float
    magnitude: float
    magnitude_type: str
    location_name: str

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "time_utc": self.time_utc.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "depth_km": self.depth_km,
            "magnitude": self.magnitude,
            "magnitude_type": self.magnitude_type,
            "location_name": self.location_name,
        }


@dataclass
class AFADClient:
    """AFAD deprem kataloğu için stdlib-only istemci."""

    endpoints: Sequence[str] = DEFAULT_AFAD_ENDPOINTS
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
    ) -> dict | list:
        """Bir bbox + zaman aralığı için ham AFAD JSON yanıtını çeker.

        Uç noktalar sırayla denenir; hepsi başarısız olursa
        `HazardNetworkError` fırlatılır (sessizce boş liste dönmez).
        """
        params = {
            "minlat": min_lat, "maxlat": max_lat,
            "minlon": min_lon, "maxlon": max_lon,
            "minmag": min_magnitude,
        }
        if start is not None:
            params["start"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end"] = end.strftime("%Y-%m-%d %H:%M:%S")

        query = urllib.parse.urlencode(params)
        last_error: Optional[Exception] = None

        for endpoint in self.endpoints:
            url = f"{endpoint}?{query}"
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                    raw_bytes = response.read()
                return json.loads(raw_bytes.decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                    OSError, ValueError) as exc:
                last_error = exc
                continue

        raise HazardNetworkError(
            f"Hiçbir AFAD uç noktasına ulaşılamadı (denenen {len(self.endpoints)} "
            f"uç nokta): {last_error!r}"
        )

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
    ) -> List[AFADEarthquake]:
        raw = self.fetch_raw(
            min_lat=min_lat, max_lat=max_lat, min_lon=min_lon, max_lon=max_lon,
            start=start, end=end, min_magnitude=min_magnitude,
        )
        return parse_afad_response(raw)


def parse_afad_response(raw: dict | list) -> List[AFADEarthquake]:
    """AFAD'ın döndürdüğü JSON'u (liste ya da `{"result": [...]}` sarmalı
    olabilir — servis sürümüne göre değişir) `AFADEarthquake` listesine çevirir."""
    if isinstance(raw, dict):
        items = raw.get("result") or raw.get("events") or raw.get("data")
        if items is None:
            raise HazardParseError(
                f"Beklenmeyen AFAD yanıt şeması (dict ama result/events/data yok): "
                f"anahtarlar={list(raw.keys())}"
            )
    elif isinstance(raw, list):
        items = raw
    else:
        raise HazardParseError(f"Beklenmeyen AFAD yanıt tipi: {type(raw).__name__}")

    results: List[AFADEarthquake] = []
    for item in items:
        try:
            results.append(_parse_one_event(item))
        except (KeyError, ValueError, TypeError) as exc:
            raise HazardParseError(f"AFAD olay kaydı ayrıştırılamadı: {item!r} ({exc})") from exc
    return results


def _parse_one_event(item: dict) -> AFADEarthquake:
    time_raw = item.get("date") or item.get("eventDate") or item.get("time_utc")
    time_utc = _parse_afad_time(time_raw)
    return AFADEarthquake(
        event_id=str(item.get("eventID") or item.get("id") or item.get("event_id") or ""),
        time_utc=time_utc,
        latitude=float(item["latitude"]),
        longitude=float(item["longitude"]),
        depth_km=float(item.get("depth", 0.0)),
        magnitude=float(item.get("magnitude") or item.get("mag") or 0.0),
        magnitude_type=str(item.get("magnitudeType") or item.get("magType") or "Mw"),
        location_name=str(item.get("location") or item.get("place") or ""),
    )


def _parse_afad_time(value) -> datetime:
    if value is None:
        raise ValueError("zaman alanı eksik")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        # AFAD bazı uçlarda "YYYY-MM-DD HH:MM:SS" (naive, yerel+3) döner.
        dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
