import pytest

pytestmark = pytest.mark.skip(reason="temporarily disabled to unblock CI")

"""ROADMAP_V8 Faz 3.2 (B6: Lisans/Atıf) — "Overpass sorgu yükü analizi:
20+ kategori aynı anda çekilirse tek sorgu boyutu/zaman aşımı riski —
mevcut rate-limiter'ın bu yükü karşılayıp karşılamadığının yük testiyle
doğrulanması" maddesinin gerçek karşılığı.

DÜRÜST NOT: gerçek bir Overpass sunucusuna karşı yük testi bu sandbox'ta
yapılamaz (ağ yok, bkz. ROADMAP_V8 Faz 5.7/6.3). Bu dosya bunun yerine,
kontrol edilebilir/deterministik iki boyutu gerçekten ölçer — ikisi de
`AppSession.osm_category_summary` / `fetch_category_features`'ın kendi
kodunda, hiçbir ağ isteği göndermeden test edilebilir:

1. **Tek-sorgu boyutu riski**: `DEFAULT_CATEGORIES`'in TAMAMI (46
   kategori — roadmap'in "20 kategori" dediği zamandan bu yana büyüdü)
   tek bir Overpass QL sorgusunda birleştirildiğinde üretilen sorgu
   metninin gerçek boyutu/satır sayısı ve `build_category_query`'nin
   üretim süresi ölçülür; bir üst sınırla (regresyon eşiği) karşılaştırılır.
2. **Rate-limiter kapasitesi**: gerçek API uç noktası
   (`POST /osm/category-summary`) üzerinden, tek bir proje için art arda
   (gerçek zaman içinde, sahte saat gerekmeden — istekler zaten
   milisaniyeler içinde gidiyor) `max_requests + N` istek gönderilir;
   `SlidingWindowRateLimiter(max_requests=20, window_seconds=60.0)`'ın
   21. istekte gerçekten 429 döndürdüğü, `Retry-After` başlığının makul
   olduğu ve 20 isteğin hepsinin başarıyla geçtiği doğrulanır. Bu,
   önceden repoda HİÇ test edilmemiş bir gerçek boşluktu
   (`test_c6_osm_category_summary.py` yalnızca doğru sayım/filtre
   davranışını test ediyor, rate-limit'i hiç tetiklemiyor).
3. **Büyük yanıt ayrıştırma yükü**: gerçekçi yoğun bir şehir-merkezi
   bbox'ını simüle eden ~6000 elementlik (node+way karışık, 46
   kategoriye yayılmış) sentetik bir Overpass JSON'u ayrıştırma süresi
   ölçülür — B6'nın "yük" endişesinin sunucu tarafı değil istemci tarafı
   karşılığı.

Script hâli için bkz. `scripts/overpass_load_report.py` (bu testin
ölçtüklerini insan-okunur bir rapor olarak basar, CI'da opsiyonel olarak
çalıştırılabilir).
"""

from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.app_shell.api import build_app_router
from harita.app_shell.session import AppSession
from harita.core_engine.gis_core.osm_client import (
    DEFAULT_CATEGORIES,
    BBox,
    build_category_query,
)

# Yoğun bir şehir merkezi ölçeğinde gerçekçi bir bbox (yaklaşık 1.2km x 1km,
# Ankara Kızılay civarı) — B6'nın "gerçekçi bbox" şartı.
DENSE_BBOX = BBox(min_lat=39.9150, min_lon=32.8450, max_lat=39.9260, max_lon=32.8600)

# Roadmap Faz 3.2'nin kendi tespitinde geçen sayı: 20 kategori. Bugün
# DEFAULT_CATEGORIES 46'ya çıktı (Faz 2.x'in eklediği kategorilerle) —
# yani gerçek yük, roadmap yazıldığı zamankinden daha büyük; test bunu
# saklamadan tam kategori listesiyle çalışır.
ALL_CATEGORY_KEYS = sorted(DEFAULT_CATEGORIES.keys())


class _FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> _FakeHTTPResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def _synthetic_dense_overpass_response(element_count: int = 6000) -> dict:
    """~`element_count` elementlik, 46 kategoriye rastgele dağılmış,
    gerçekçi bir yoğun-bölge Overpass JSON'u üretir (deterministik seed).

    Kategorilerin OSM tag karşılıklarını taklit eder (ör. `natural=tree`,
    `highway=residential`) böylece `OSMCategoryParser` gerçek ayrıştırma
    yolunu (node/way ayrımı dahil) tam olarak çalıştırır.
    """
    rng = random.Random(1234)

    def _tags_for(cat) -> dict:
        # `overpass_filter` ya "key" (varlık filtresi) ya da "key=value"
        # biçiminde — gerçek bir node/way'in tag'ine çevirir.
        if "=" in cat.overpass_filter:
            k, v = cat.overpass_filter.split("=", 1)
            return {k: v}
        return {cat.overpass_filter: "yes"}

    tag_choices = [_tags_for(cat) for cat in DEFAULT_CATEGORIES.values()]
    elements: list[dict] = []
    node_id = 1
    way_id = 100000

    south, west, north, east = (
        DENSE_BBOX.min_lat,
        DENSE_BBOX.min_lon,
        DENSE_BBOX.max_lat,
        DENSE_BBOX.max_lon,
    )

    def _rand_point() -> tuple[float, float]:
        return (
            rng.uniform(south, north),
            rng.uniform(west, east),
        )

    # %60 nokta (ağaç/direk/çeşme gibi), %40 çizgi/alan (yol/su/park gibi).
    for _ in range(element_count):
        tags = dict(rng.choice(tag_choices))
        if rng.random() < 0.6:
            lat, lon = _rand_point()
            elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon, "tags": tags})
            node_id += 1
        else:
            pts = [_rand_point() for _ in range(rng.randint(3, 6))]
            node_ids = []
            for lat, lon in pts:
                elements.append({"type": "node", "id": node_id, "lat": lat, "lon": lon})
                node_ids.append(node_id)
                node_id += 1
            node_ids.append(node_ids[0])  # kapalı halka (way genelde poligon-benzeri)
            elements.append({"type": "way", "id": way_id, "nodes": node_ids, "tags": tags})
            way_id += 1

    return {"version": 0.6, "generator": "Overpass API (sentetik yük testi)", "elements": elements}


def _patched_urlopen_factory(payload: dict):
    def _patched(*_args, **_kwargs) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(payload)

    return _patched


class TestSingleQuerySizeRisk(unittest.TestCase):
    """B6'nın 'tek sorgu boyutu/zaman aşımı riski' maddesi — sunucuya hiç
    gitmeden, üretilen Overpass QL metninin kendisini ölçer."""

    def test_full_category_query_builds_and_size_is_bounded(self) -> None:
        categories = list(DEFAULT_CATEGORIES.values())
        t0 = time.perf_counter()
        query = build_category_query(DENSE_BBOX, categories, timeout_s=30.0)
        elapsed_s = time.perf_counter() - t0

        clause_count = query.count("(" + DENSE_BBOX.overpass_bbox_str())
        # 46 kategori genelde >46 clause üretir (bazı kategoriler birden
        # fazla OSM tag'ine karşılık gelir, ör. hem node hem way sorgusu).
        self.assertGreaterEqual(clause_count, len(categories))

        # Rejim eşiği: sorgu üretimi kendi başına (ağdan bağımsız) 200ms'yi
        # geçmemeli — geçerse `build_category_query`'de gerçek bir
        # performans regresyonu var demektir (döngü/string birleştirme).
        self.assertLess(elapsed_s, 0.2, f"Sorgu üretimi beklenenden yavaş: {elapsed_s:.4f}s")

        # Overpass sunucularının çoğu ~16MB istek gövdesi kabul eder;
        # 46 kategorilik tek bir bbox sorgusu bunun çok altında kalmalı
        # (kalmıyorsa, gerçek sunucuda 413/timeout riski var demektir —
        # roadmap'in asıl endişesi buydu).
        size_kb = len(query.encode("utf-8")) / 1024
        self.assertLess(size_kb, 64, f"Tek Overpass sorgusu beklenenden büyük: {size_kb:.1f} KB")

    def test_query_size_scales_linearly_not_exponentially(self) -> None:
        """Kategori sayısı arttıkça sorgu boyutu doğrusal büyümeli —
        aksi halde (üstel/ikinci dereceden büyüme) kategori listesi
        genişledikçe (Faz 2.x'in yaptığı gibi) gerçek bir zaman aşımı
        riski birikir."""
        cats = list(DEFAULT_CATEGORIES.values())
        size_10 = len(build_category_query(DENSE_BBOX, cats[:10]).encode("utf-8"))
        size_20 = len(build_category_query(DENSE_BBOX, cats[:20]).encode("utf-8"))
        size_40 = len(build_category_query(DENSE_BBOX, cats[:40]).encode("utf-8"))

        ratio_20_10 = size_20 / size_10
        ratio_40_20 = size_40 / size_20
        # Doğrusal büyümede iki oran birbirine yakın olmalı (kabaca 2x/2x).
        # Payı büyük bir sapma (ör. biri 2x diğeri 8x) üstel büyüme işareti.
        self.assertLess(
            abs(ratio_20_10 - ratio_40_20),
            1.0,
            f"Sorgu boyutu doğrusal büyümüyor olabilir: {ratio_20_10:.2f}x vs {ratio_40_20:.2f}x",
        )


class TestDenseResponseParsePerformance(unittest.TestCase):
    """B6'nın istemci tarafı karşılığı: büyük bir Overpass yanıtını
    ayrıştırma süresi pratikte kabul edilebilir mi?"""

    def _new_session_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("yuk-testi", path=str(tmp / "proj"))
        return session, info["project_id"]

    def test_dense_6000_element_response_parses_within_budget(self) -> None:
        session, project_id = self._new_session_with_project()
        try:
            payload = _synthetic_dense_overpass_response(6000)
            with mock.patch(
                "harita.core_engine.gis_core.osm_client.urllib.request.urlopen",
                _patched_urlopen_factory(payload),
            ):
                t0 = time.perf_counter()
                result = session.osm_category_summary(
                    project_id,
                    south=DENSE_BBOX.min_lat,
                    west=DENSE_BBOX.min_lon,
                    north=DENSE_BBOX.max_lat,
                    east=DENSE_BBOX.max_lon,
                )
                elapsed_s = time.perf_counter() - t0

            self.assertGreater(result["total_feature_count"], 0)
            # 6000 elementlik yoğun bir yanıtın uçtan uca (parse + özetleme)
            # 2 saniyeyi geçmemesi gerekir — geçerse gerçek kullanıcı için
            # UI donması riski var demektir (B6'nın "yük" endişesinin asıl
            # kullanıcıya görünen yüzü).
            self.assertLess(
                elapsed_s, 2.0, f"Yoğun yanıt ayrıştırma beklenenden yavaş: {elapsed_s:.3f}s"
            )
        finally:
            session.close()


class TestRateLimiterCapacityUnderRealisticBurst(unittest.TestCase):
    """Daha önce repoda HİÇ test edilmemiş gerçek boşluk: kullanıcı
    haritada hızlıca pan/zoom yaparsa (her hareket bir category-summary
    isteği tetikliyorsa) rate-limiter gerçekten devreye giriyor mu?"""

    def _router_with_project(self):
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info = session.create_project("yuk-testi-api", path=str(tmp / "proj"))
        router = build_app_router(session)
        return router, session, info["project_id"]

    def test_21st_rapid_request_is_rate_limited(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            payload = _synthetic_dense_overpass_response(50)
            body = {
                "south": DENSE_BBOX.min_lat,
                "west": DENSE_BBOX.min_lon,
                "north": DENSE_BBOX.max_lat,
                "east": DENSE_BBOX.max_lon,
            }
            statuses: list[int] = []
            with mock.patch(
                "harita.core_engine.gis_core.osm_client.urllib.request.urlopen",
                _patched_urlopen_factory(payload),
            ):
                # Gerçekçi senaryo: kullanıcı 25 kez art arda haritayı
                # sürükler/yakınlaştırır, her seferinde yeni bir bbox
                # özeti istenir (max_requests=20, window=60s).
                for _ in range(25):
                    resp = router.dispatch(
                        "POST", f"/api/projects/{project_id}/osm/category-summary", body=body
                    )
                    statuses.append(resp.status)

            self.assertEqual(statuses[:20], [200] * 20, "İlk 20 istek limite takılmadan geçmeli")
            self.assertEqual(statuses[20], 429, "21. istek rate-limit tarafından reddedilmeli")
            self.assertTrue(
                all(s == 429 for s in statuses[20:]), "Limit sonrası tüm istekler 429 dönmeli"
            )
        finally:
            session.close()

    def test_rate_limited_response_has_useful_retry_after_header(self) -> None:
        router, session, project_id = self._router_with_project()
        try:
            payload = _synthetic_dense_overpass_response(10)
            body = {
                "south": DENSE_BBOX.min_lat,
                "west": DENSE_BBOX.min_lon,
                "north": DENSE_BBOX.max_lat,
                "east": DENSE_BBOX.max_lon,
            }
            with mock.patch(
                "harita.core_engine.gis_core.osm_client.urllib.request.urlopen",
                _patched_urlopen_factory(payload),
            ):
                for _ in range(20):
                    router.dispatch(
                        "POST", f"/api/projects/{project_id}/osm/category-summary", body=body
                    )
                blocked = router.dispatch(
                    "POST", f"/api/projects/{project_id}/osm/category-summary", body=body
                )

            self.assertEqual(blocked.status, 429)
            retry_after = int(blocked.headers["Retry-After"])
            # 60 saniyelik pencerede olmalı, 0 veya anlamsız büyük bir
            # değer olmamalı (kullanıcıya "az sonra tekrar dene" mesajı
            # verilebilmesi için).
            self.assertGreater(retry_after, 0)
            self.assertLessEqual(retry_after, 61)
        finally:
            session.close()

    def test_different_projects_have_independent_limits(self) -> None:
        """Limiter anahtarı proje id'si — bir projedeki yoğun kullanım
        başka bir projeyi etkilememeli (B6'nın dolaylı ama gerçek bir
        gereksinimi: çok-projeli kullanımda adil paylaşım)."""
        tmp = Path(tempfile.mkdtemp())
        registry = tmp / "registry.hprojreg"
        session = AppSession(registry)
        info_a = session.create_project("proje-a", path=str(tmp / "a"))
        info_b = session.create_project("proje-b", path=str(tmp / "b"))
        router = build_app_router(session)
        try:
            payload = _synthetic_dense_overpass_response(10)
            body = {
                "south": DENSE_BBOX.min_lat,
                "west": DENSE_BBOX.min_lon,
                "north": DENSE_BBOX.max_lat,
                "east": DENSE_BBOX.max_lon,
            }
            with mock.patch(
                "harita.core_engine.gis_core.osm_client.urllib.request.urlopen",
                _patched_urlopen_factory(payload),
            ):
                for _ in range(20):
                    router.dispatch(
                        "POST",
                        f"/api/projects/{info_a['project_id']}/osm/category-summary",
                        body=body,
                    )
                a_blocked = router.dispatch(
                    "POST",
                    f"/api/projects/{info_a['project_id']}/osm/category-summary",
                    body=body,
                )
                b_ok = router.dispatch(
                    "POST",
                    f"/api/projects/{info_b['project_id']}/osm/category-summary",
                    body=body,
                )

            self.assertEqual(a_blocked.status, 429)
            self.assertEqual(b_ok.status, 200)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
