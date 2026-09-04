#!/usr/bin/env python3
"""ROADMAP_V8 - Faz 3.2 (B6): "20+ kategori aynı anda çekilirse tek sorgu
boyutu/zaman aşımı riski — mevcut rate-limiter'ın bu yükü karşılayıp
karşılamadığının yük testiyle doğrulanması" maddesinin insan-okunur
rapor karşılığı.

`tests/test_roadmap_v8_faz3_2_overpass_load.py`'nin ölçtüğü üç boyutu
(tek-sorgu boyutu, yoğun-yanıt ayrıştırma süresi, rate-limiter kapasitesi)
buradan tek komutla, okunabilir bir özet olarak basar. Testler CI'da
regresyonu yakalar; bu script insan gözünün "ne kadar" sorusuna cevap
vermesi için var (roadmap'in "yük/zaman ölçümü raporu var" kabul
kriterinin somut karşılığı).

DÜRÜST SINIRLAMA: gerçek bir Overpass sunucusuna karşı ölçüm değildir
(ağ yok, bkz. ROADMAP_V8 Faz 5.7/6.3) — tamamen yerel/sentetik veriyle,
istemci tarafı maliyeti ölçer.

Kullanım::

    python3 scripts/overpass_load_report.py
"""
from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from harita.app_shell.api import build_app_router  # noqa: E402
from harita.app_shell.session import AppSession  # noqa: E402
from harita.core_engine.gis_core.osm_client import (  # noqa: E402
    DEFAULT_CATEGORIES,
    BBox,
    build_category_query,
)

DENSE_BBOX = BBox(min_lat=39.9150, min_lon=32.8450, max_lat=39.9260, max_lon=32.8600)


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _tags_for(cat) -> dict:
    if "=" in cat.overpass_filter:
        k, v = cat.overpass_filter.split("=", 1)
        return {k: v}
    return {cat.overpass_filter: "yes"}


def _synthetic_dense_response(element_count: int) -> dict:
    rng = random.Random(1234)
    tag_choices = [_tags_for(cat) for cat in DEFAULT_CATEGORIES.values()]
    elements: list[dict] = []
    node_id = 1
    way_id = 100000
    south, west, north, east = DENSE_BBOX.min_lat, DENSE_BBOX.min_lon, DENSE_BBOX.max_lat, DENSE_BBOX.max_lon

    def _pt() -> tuple[float, float]:
        return rng.uniform(south, north), rng.uniform(west, east)

    for _ in range(element_count):
        tags = dict(rng.choice(tag_choices))
        if rng.random() < 0.6:
            lat, lon = _pt()
            elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon, "tags": tags})
            node_id += 1
        else:
            pts = [_pt() for _ in range(rng.randint(3, 6))]
            ids = []
            for lat, lon in pts:
                elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon})
                ids.append(node_id)
                node_id += 1
            ids.append(ids[0])
            elements.append({"type": "way", "id": way_id, "nodes": ids, "tags": tags})
            way_id += 1
    return {"version": 0.6, "generator": "sentetik yük raporu", "elements": elements}


def main() -> int:
    print("=" * 72)
    print("ROADMAP_V8 Faz 3.2 — Overpass Sorgu Yükü / Zaman Ölçümü Raporu")
    print("=" * 72)
    print(f"Bbox: {DENSE_BBOX.min_lat},{DENSE_BBOX.min_lon} .. {DENSE_BBOX.max_lat},{DENSE_BBOX.max_lon}")
    print(f"Toplam kategori sayısı (DEFAULT_CATEGORIES): {len(DEFAULT_CATEGORIES)}")
    print()

    # 1) Tek-sorgu boyutu
    categories = list(DEFAULT_CATEGORIES.values())
    t0 = time.perf_counter()
    query = build_category_query(DENSE_BBOX, categories, timeout_s=30.0)
    build_elapsed = time.perf_counter() - t0
    size_kb = len(query.encode("utf-8")) / 1024
    print("-- 1) Tek-sorgu boyutu (tüm kategoriler birleşik) --")
    print(f"   Sorgu üretim süresi : {build_elapsed * 1000:.2f} ms")
    print(f"   Sorgu boyutu        : {size_kb:.1f} KB  (Overpass sunucu sınırı genelde ~16 MB)")
    print(f"   Satır sayısı        : {query.count(chr(10))}")
    print()

    # 2) Yoğun yanıt ayrıştırma
    print("-- 2) Yoğun bbox yanıtı ayrıştırma süresi (6000 element, 46 kategori) --")
    tmp = Path(tempfile.mkdtemp())
    session = AppSession(tmp / "registry.hprojreg")
    info = session.create_project("rapor-projesi", path=str(tmp / "proj"))
    payload = _synthetic_dense_response(6000)

    def _urlopen(*_a, **_k):
        return _FakeHTTPResponse(payload)

    with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _urlopen):
        t0 = time.perf_counter()
        result = session.osm_category_summary(
            info["project_id"],
            south=DENSE_BBOX.min_lat, west=DENSE_BBOX.min_lon,
            north=DENSE_BBOX.max_lat, east=DENSE_BBOX.max_lon,
        )
        parse_elapsed = time.perf_counter() - t0
    print(f"   Ayrıştırma + özetleme süresi : {parse_elapsed * 1000:.1f} ms")
    print(f"   Toplam feature sayısı        : {result['total_feature_count']}")
    print()

    # 3) Rate-limiter kapasitesi (API seviyesi, gerçek 429 davranışı)
    print("-- 3) Rate-limiter kapasitesi (max_requests=20, window=60s) --")
    router = build_app_router(session)
    body = {
        "south": DENSE_BBOX.min_lat, "west": DENSE_BBOX.min_lon,
        "north": DENSE_BBOX.max_lat, "east": DENSE_BBOX.max_lon,
    }
    small_payload = _synthetic_dense_response(50)

    def _urlopen_small(*_a, **_k):
        return _FakeHTTPResponse(small_payload)

    statuses = []
    with mock.patch("harita.core_engine.gis_core.osm_client.urllib.request.urlopen", _urlopen_small):
        t0 = time.perf_counter()
        for _ in range(25):
            resp = router.dispatch(
                "POST", f"/api/projects/{info['project_id']}/osm/category-summary", body=body,
            )
            statuses.append(resp.status)
        burst_elapsed = time.perf_counter() - t0

    ok_count = statuses.count(200)
    blocked_count = statuses.count(429)
    print(f"   25 art arda istek toplam süre : {burst_elapsed * 1000:.1f} ms")
    print(f"   Başarılı (200)                : {ok_count}")
    print(f"   Sınırlanan (429)              : {blocked_count}")
    print(f"   Beklenen davranış             : ilk 20 -> 200, sonraki 5 -> 429")
    ok = ok_count == 20 and blocked_count == 5 and statuses[:20] == [200] * 20
    print(f"   Sonuç                         : {'GEÇTİ' if ok else 'BEKLENMEDİK DAVRANIŞ'}")
    print()

    session.close()
    print("=" * 72)
    if not ok:
        print("UYARI: rate-limiter beklenen şekilde davranmadı, koda bakılmalı.")
        return 1
    print("Özet: tek-sorgu boyutu makul, yoğun yanıt ayrıştırma hızlı, ")
    print("rate-limiter 20 istek/60sn kapasitesini gerçekten uyguluyor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
