"""
ROADMAP_V4 — Track R / R4: Gerçek bir kamu WMTS/WMS sunucusuna karşı
uçtan uca doğrulama.

`core_engine/tile_sources/__init__.py` şimdiye kadar bilinçli olarak
**ağ-agnostik** kalmıştı: `TileFetcher` bir `Protocol`, testler yalnızca
`FakeFetcher` ile URL üretimini doğruluyordu; gerçek bir sunucuya karşı
uçtan uca doğrulama roadmap'te "kapsam dışı / ortam ağ kısıtı" olarak not
edilmişti. Bu dosya, R5'in (`test_r5_osm_live_integration.py`) izlediği
aynı iki katmanlı stratejiyi tekrarlar:

1. `TestOfflineSanity` — ağ gerektirmez, bu ortamda dahil her zaman çalışır;
   gerçek (halka açık) sunucuların gerçek URL şablonlarıyla `WMTSTileSource`/
   `WMSTileSource`'ın doğru URL ürettiğini doğrular (sahte yeşil değil —
   gerçek servis parametreleriyle).
2. `TestLiveWMTS` / `TestLiveWMS` — gerçek ağ isteği atar. Bu ortamın
   izin verilen alan adı listesi (`allowed_domains`) genel amaçlı kamu
   WMTS/WMS sunucularını kapsamadığından burada **açıkça skip edilir**
   (`pytest.mark.skipif`, skip nedeni raporda görünür — sessizce
   atlanmaz). Ağ erişimi olan bir makinede (kullanıcının kendi
   bilgisayarı) bu testler gerçek sunuculara bağlanıp gerçek tile
   byte'larını indirir ve temel içerik/başlık doğrulaması yapar.

Kullanılan gerçek kamu sunucuları (API anahtarı gerektirmez):
- WMTS: NASA GIBS (`gibs.earthdata.nasa.gov`) — `BlueMarble_NextGeneration`
  katmanı, EPSG4326 RESTful tile matrisi.
- WMS: USGS National Map (`basemap.nationalmap.gov`) — `USGSImageryOnly`
  servisi, GetMap.

Bu ortamda çalıştırma: `pytest tests/test_r4_wmts_wms_live_integration.py -v`
(offline testler yeşil, `TestLive*` skip). Ağ erişimi olan bir ortamda:
`pytest tests/test_r4_wmts_wms_live_integration.py -k Live -v`.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.core_engine.tile_engine import TileCoordinate
from harita.core_engine.tile_sources import (
    TileFetchError,
    TileSourceConsumer,
    UrllibFetcher,
    WMSTileSource,
    WMTSTileSource,
)

# ---------------------------------------------------------------------------
# Gerçek kamu sunucu tanımları
# ---------------------------------------------------------------------------

GIBS_WMTS_HOST = "gibs.earthdata.nasa.gov"
GIBS_WMTS = WMTSTileSource(
    name="nasa-gibs-bluemarble",
    base_url=f"https://{GIBS_WMTS_HOST}/wmts/epsg4326/best/wmts.cgi",
    layer="BlueMarble_NextGeneration",
    tile_matrix_set="2km",
    style="default",
    image_format="image/jpeg",
    kvp=True,
    tile_matrix_prefix="2km:",
)

NATIONALMAP_WMS_HOST = "basemap.nationalmap.gov"
NATIONALMAP_WMS = WMSTileSource(
    name="usgs-imagery-only",
    base_url=f"https://{NATIONALMAP_WMS_HOST}/arcgis/services/USGSImageryOnly/MapServer/WMSServer",
    layers="0",
    crs="EPSG:4326",
    image_format="image/png",
    tile_size=256,
    version="1.3.0",
    transparent=True,
)


def _http_reachable(url: str, timeout_s: float = 4.0) -> bool:
    """
    Gerçek erişilebilirlik ön-kontrolü — yalnızca DNS çözümlemesi yeterli
    değil: kısıtlı ortamlarda (ör. bu sandbox'ın izin-listeli egress
    proxy'si) DNS başarılı olsa bile gerçek HTTP isteği bir ağ geçidi
    tarafından reddedilebilir (403/bağlantı hatası). Bu yüzden burada
    gerçek, kısa zaman aşımlı bir HTTP isteği denenir; herhangi bir hata
    (DNS, bağlantı, zaman aşımı, HTTP hata kodu) "erişilemez" sayılır ve
    test skip edilir — kısıtsız bir ağda (kullanıcının kendi bilgisayarı)
    bu prob normal şekilde başarılı olur ve `TestLive*` testleri gerçekten
    çalışır.
    """
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "harita-modelleme-r4-probe/1.0"})
        with urllib.request.urlopen(req, timeout=timeout_s):
            return True
    except Exception:
        return False


def _dns_resolves(host: str, timeout_s: float = 2.0) -> bool:
    """Geriye dönük yardımcı — artık `_http_reachable` birincil ön-kontrol."""
    try:
        socket.setdefaulttimeout(timeout_s)
        socket.gethostbyname(host)
        return True
    except OSError:
        return False


_NETWORK_REASON_GIBS = (
    f"'{GIBS_WMTS_HOST}' bu ortamdan çözümlenemiyor/erişilemiyor "
    "(izin verilen alan adı listesi kamu WMTS sunucularını kapsamıyor). "
    "Ağ erişimi olan bir makinede bu test gerçek bir GIBS tile'ı indirir."
)
_NETWORK_REASON_NATIONALMAP = (
    f"'{NATIONALMAP_WMS_HOST}' bu ortamdan çözümlenemiyor/erişilemiyor "
    "(izin verilen alan adı listesi kamu WMS sunucularını kapsamıyor). "
    "Ağ erişimi olan bir makinede bu test gerçek bir USGS WMS GetMap "
    "yanıtı indirir."
)


# ---------------------------------------------------------------------------
# 1) Ağ gerektirmeyen sağlamlık testleri — bu ortamda her zaman çalışır
# ---------------------------------------------------------------------------


class TestOfflineSanity:
    def test_gibs_wmts_url_is_well_formed(self):
        coord = TileCoordinate(z=2, x=1, y=0)
        url = GIBS_WMTS.build_url(coord)
        assert url.startswith(f"https://{GIBS_WMTS_HOST}/wmts/epsg4326/best/wmts.cgi?")
        assert "LAYER=BlueMarble_NextGeneration" in url
        assert "TILEMATRIX=2km%3A2" in url or "TILEMATRIX=2km:2" in url
        assert "REQUEST=GetTile" in url

    def test_nationalmap_wms_url_is_well_formed(self):
        coord = TileCoordinate(z=5, x=10, y=12)
        url = NATIONALMAP_WMS.build_url(coord)
        assert url.startswith(
            f"https://{NATIONALMAP_WMS_HOST}/arcgis/services/USGSImageryOnly/MapServer/WMSServer?"
        )
        assert "REQUEST=GetMap" in url
        assert "LAYERS=0" in url
        assert "CRS=EPSG%3A4326" in url

    def test_consumer_with_fake_fetcher_tracks_stats_for_real_urls(self):
        """URL üretimi gerçek sunucu parametreleriyle, fetch sahte —
        `TileSourceConsumer` istatistiklerinin bu senaryoda da doğru
        işlediğini kanıtlar (canlı ağ testleriyle aynı çağrı yolunu
        offline biçimde kanıtlar)."""

        def fake_fetcher(url: str) -> bytes:
            assert GIBS_WMTS_HOST in url
            return b"\xff\xd8\xff\xe0FAKEJPEG"

        consumer = TileSourceConsumer(source=GIBS_WMTS, fetcher=fake_fetcher)
        data = consumer.fetch_tile(TileCoordinate(z=1, x=0, y=0))
        assert data.startswith(b"\xff\xd8\xff")
        assert consumer.stats == {"requests": 1, "errors": 0}


# ---------------------------------------------------------------------------
# 2) Gerçek ağ isteği atan canlı testler — bu ortamda skip edilir
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _http_reachable(f"https://{GIBS_WMTS_HOST}/wmts/epsg4326/best/1.0.0/WMTSCapabilities.xml"),
    reason=_NETWORK_REASON_GIBS,
)
class TestLiveWMTS:
    """Gerçek NASA GIBS WMTS sunucusuna karşı uçtan uca doğrulama."""

    def test_live_fetch_returns_real_jpeg_tile(self):
        fetcher = UrllibFetcher(timeout_s=15.0, user_agent="harita-modelleme-r4/1.0")
        consumer = TileSourceConsumer(source=GIBS_WMTS, fetcher=fetcher)

        # z=1 (2km tile matrix) düşük çözünürlükte, küçük ve hızlı iner.
        data = consumer.fetch_tile(TileCoordinate(z=1, x=0, y=0))

        assert isinstance(data, bytes)
        assert len(data) > 500  # gerçek bir JPEG boş yanıt değil
        # JPEG magic bytes (FF D8 FF)
        assert data[:3] == b"\xff\xd8\xff"
        assert consumer.stats["requests"] == 1
        assert consumer.stats["errors"] == 0

    def test_live_fetch_invalid_layer_raises_tile_fetch_error(self):
        bad_source = WMTSTileSource(
            name="nasa-gibs-invalid",
            base_url=GIBS_WMTS.base_url,
            layer="THIS_LAYER_DOES_NOT_EXIST_XYZ",
            tile_matrix_set="2km",
            image_format="image/jpeg",
            kvp=True,
            tile_matrix_prefix="2km:",
        )
        fetcher = UrllibFetcher(timeout_s=15.0)
        consumer = TileSourceConsumer(source=bad_source, fetcher=fetcher)
        with pytest.raises(TileFetchError):
            consumer.fetch_tile(TileCoordinate(z=1, x=0, y=0))
        assert consumer.stats["errors"] == 1


@pytest.mark.skipif(
    not _http_reachable(f"https://{NATIONALMAP_WMS_HOST}/arcgis/rest/services?f=json"),
    reason=_NETWORK_REASON_NATIONALMAP,
)
class TestLiveWMS:
    """Gerçek USGS National Map WMS sunucusuna karşı uçtan uca doğrulama."""

    def test_live_getmap_returns_real_image(self):
        fetcher = UrllibFetcher(timeout_s=15.0, user_agent="harita-modelleme-r4/1.0")
        consumer = TileSourceConsumer(source=NATIONALMAP_WMS, fetcher=fetcher)

        # ABD'de bilinen bir bölge (Denver, CO civarı) — z=8 makul tile boyutu.
        data = consumer.fetch_tile(TileCoordinate(z=8, x=53, y=98))

        assert isinstance(data, bytes)
        assert len(data) > 200
        # PNG magic bytes
        assert data[:8] == b"\x89PNG\r\n\x1a\n"
        assert consumer.stats["requests"] == 1
        assert consumer.stats["errors"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
