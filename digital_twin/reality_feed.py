"""
Reality Feed — Gerçek Zamanlı Veri Füzyon Katmanı
====================================================

ROADMAP_V9.md — Faz I / OMURGA / O.5.

Roadmap metni birebir: "`hazard_data/afad_client.py`, `hazard_data/
usgs_client.py`, `climate_data/open_meteo_client.py` ayrı ayrı canlı veri
çekiyor. Bunları tek 'gerçeklik beslemesi' altında toplayan orkestratör:
`digital_twin/reality_feed.py` (yeni) — periyodik olarak AFAD/USGS/
Open-Meteo'yu sorgular, sonucu Event Bus'a basar."

Tasarım ilkeleri (roadmap "Kritik Tasarım İlkeleri" bölümüyle tutarlı):

- **Tekrar yazma yok:** `AFADClient`/`USGSClient`/`OpenMeteoClient` burada
  yeniden uygulanmaz, yalnızca çağrılır ve sonuçları normalize edilir.
- **Sessiz sahte-başarı yok:** Bir kaynak başarısız olursa (`HazardError`/
  `ClimateError` alt sınıfları) `RealityFeed` çökmez — hatayı
  `CityEventType.REALITY_FEED_SOURCE_ERROR` olarak Event Bus'a basar ve
  diğer kaynaklara devam eder (tek bir ağ kesintisi tüm besleyiciyi
  durdurmamalı), ama hatayı asla yutup "veri yok" ile "veri boş geldi"yi
  birbirine karıştırmaz.
- **Olay-güdümlü mimari:** `RealityFeed` hiçbir katmanı doğrudan
  çağırmaz/import etmez (`mobility`, `power_infrastructure` vb. bu modülü
  bilmez) — yalnızca `extensibility.event_system.EventSystem` üzerinden
  yayın yapar; dinleyiciler kendi katmanlarında abone olur.
- **Deduplikasyon:** Aynı depremi her poll turunda tekrar tekrar
  yayınlamamak için görülen `event_id`'ler bir pencere (`_seen_quake_ids`,
  `max_seen_ids` ile sınırlı - bellek şişmesin) içinde tutulur.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, List, Optional, Sequence

from ..climate_data.open_meteo_client import ClimateError, HourlyClimateSample, OpenMeteoClient
from ..extensibility.city_events import CityEventType, emit_city_event
from ..extensibility.event_system import EventSystem, default_bus
from ..hazard_data.afad_client import AFADClient, AFADEarthquake, HazardError
from ..hazard_data.usgs_client import USGSClient, USGSEarthquake


@dataclass(frozen=True)
class RealityFeedRegion:
    """Beslemenin izlediği coğrafi kutu (bbox) — bir proje/twin bölgesi."""

    name: str
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float
    min_magnitude: float = 3.0


@dataclass
class RealityFeedConfig:
    regions: Sequence[RealityFeedRegion]
    poll_interval_s: float = 300.0          # 5 dk — deprem kataloğu için makul varsayılan
    quake_lookback: timedelta = field(default_factory=lambda: timedelta(hours=1))
    max_seen_ids: int = 2000
    fetch_weather: bool = True
    afad_client: AFADClient = field(default_factory=AFADClient)
    usgs_client: USGSClient = field(default_factory=USGSClient)
    open_meteo_client: OpenMeteoClient = field(default_factory=OpenMeteoClient)


class RealityFeed:
    """AFAD + USGS + Open-Meteo'yu tek noktadan sorgulayıp Event Bus'a basan
    orkestratör (O.5). `digital_twin/iot_bridge.py`'deki `TopicBus`'ın
    tersine, bu **dışarıdan** (internet) içeriye veri çeken tek-yönlü bir
    besleme — `TopicBus` ise içerideki sensör mesajlarını taşır. İkisi
    tamamlayıcıdır, birbirinin yerine geçmez.
    """

    def __init__(
        self,
        config: RealityFeedConfig,
        bus: Optional[EventSystem] = None,
    ) -> None:
        self.config = config
        self.bus = bus if bus is not None else default_bus
        self._seen_quake_ids: List[str] = []
        self._seen_quake_id_set: set[str] = set()
        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

    # ------------------------------------------------------------------ #
    # Tek seferlik sorgu (test edilebilir çekirdek — döngü bunu sarar)
    # ------------------------------------------------------------------ #

    def poll_once(self) -> None:
        """Tüm bölgeler için AFAD → USGS (yedek) deprem verisini ve
        (isteğe bağlı) hava durumunu bir kez çeker, Event Bus'a basar.
        """
        now = datetime.now(timezone.utc)
        start = now - self.config.quake_lookback
        for region in self.config.regions:
            self._poll_region_earthquakes(region, start=start, end=now)
            if self.config.fetch_weather:
                self._poll_region_weather(region)

    def _poll_region_earthquakes(
        self, region: RealityFeedRegion, *, start: datetime, end: datetime
    ) -> None:
        quakes: List[AFADEarthquake] | List[USGSEarthquake]
        source_name = "afad"
        try:
            quakes = self.config.afad_client.fetch_earthquakes(
                min_lat=region.min_lat, max_lat=region.max_lat,
                min_lon=region.min_lon, max_lon=region.max_lon,
                start=start, end=end, min_magnitude=region.min_magnitude,
            )
        except HazardError as afad_exc:
            self._emit_source_error(region, "afad", afad_exc)
            # AFAD Türkiye'ye özel; başarısız olursa küresel kapsamlı USGS'e
            # yedek olarak düş (roadmap: "AFAD/USGS canlı deprem verisi").
            source_name = "usgs"
            try:
                quakes = self.config.usgs_client.fetch_earthquakes(
                    min_lat=region.min_lat, max_lat=region.max_lat,
                    min_lon=region.min_lon, max_lon=region.max_lon,
                    start=start, end=end, min_magnitude=region.min_magnitude,
                )
            except HazardError as usgs_exc:
                self._emit_source_error(region, "usgs", usgs_exc)
                return

        for quake in quakes:
            if quake.event_id in self._seen_quake_id_set:
                continue
            self._remember_quake_id(quake.event_id)
            emit_city_event(
                self.bus,
                CityEventType.REALITY_FEED_EARTHQUAKE,
                source=f"reality_feed:{source_name}",
                region=region.name,
                event_id=quake.event_id,
                time_utc=quake.time_utc.isoformat(),
                latitude=quake.latitude,
                longitude=quake.longitude,
                depth_km=quake.depth_km,
                magnitude=quake.magnitude,
                magnitude_type=quake.magnitude_type,
            )
            # Yönetmelik/roadmap notu: gerçek bir M-eşiği üstü deprem,
            # Katman 7'nin cascade zincirini tetiklemesi için ayrıca
            # genel HAZARD_STARTED olarak da yayınlanır (Cascade Engine
            # `hazard.*` desenini dinler, kaynağın AFAD/USGS/senaryo
            # olduğunu ayırt etmesi gerekmez).
            emit_city_event(
                self.bus,
                CityEventType.HAZARD_STARTED,
                source=f"reality_feed:{source_name}",
                hazard_type="earthquake",
                region=region.name,
                magnitude=quake.magnitude,
                epicenter=(quake.latitude, quake.longitude),
                depth_km=quake.depth_km,
                event_id=quake.event_id,
            )

    def _poll_region_weather(self, region: RealityFeedRegion) -> None:
        center_lat = (region.min_lat + region.max_lat) / 2.0
        center_lon = (region.min_lon + region.max_lon) / 2.0
        today = datetime.now(timezone.utc).date()
        try:
            samples: List[HourlyClimateSample] = self.config.open_meteo_client.fetch_hourly(
                latitude=center_lat, longitude=center_lon,
                start_date=today, end_date=today,
            )
        except ClimateError as exc:
            self._emit_source_error(region, "open_meteo", exc)
            return

        if not samples:
            return
        latest = samples[-1]
        emit_city_event(
            self.bus,
            CityEventType.REALITY_FEED_WEATHER,
            source="reality_feed:open_meteo",
            region=region.name,
            time_iso=latest.time_iso,
            temperature_c=latest.temperature_c,
            cloud_cover_pct=latest.cloud_cover_pct,
            shortwave_radiation_wm2=latest.shortwave_radiation_wm2,
        )

    def _emit_source_error(self, region: RealityFeedRegion, source: str, exc: Exception) -> None:
        emit_city_event(
            self.bus,
            CityEventType.REALITY_FEED_SOURCE_ERROR,
            source=f"reality_feed:{source}",
            region=region.name,
            error=repr(exc),
        )

    def _remember_quake_id(self, event_id: str) -> None:
        self._seen_quake_id_set.add(event_id)
        self._seen_quake_ids.append(event_id)
        if len(self._seen_quake_ids) > self.config.max_seen_ids:
            oldest = self._seen_quake_ids.pop(0)
            self._seen_quake_id_set.discard(oldest)

    # ------------------------------------------------------------------ #
    # Sürekli çalıştırma (opsiyonel arka plan iş parçacığı)
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Arka planda `poll_interval_s` aralıklarla `poll_once()` çağırır.

        Ağ hataları `poll_once()` içinde zaten olay olarak yayınlandığı
        (yutulmadığı) için döngü çökmez, bir sonraki periyotta devam eder.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_flag.clear()

        def _loop() -> None:
            while not self._stop_flag.is_set():
                try:
                    self.poll_once()
                except Exception as exc:  # pragma: no cover - beklenmeyen hata güvenlik ağı
                    self._emit_source_error(
                        RealityFeedRegion("__unknown__", 0, 0, 0, 0), "reality_feed", exc
                    )
                self._stop_flag.wait(self.config.poll_interval_s)

        self._thread = threading.Thread(target=_loop, name="reality-feed", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_flag.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
