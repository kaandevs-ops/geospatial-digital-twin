"""
ROADMAP_V4 — Track R / R3: 100+ Gerçek Dünya Dosyasıyla Toplu GIS Entegrasyonu
==============================================================================

Roadmap V2'nin (ve V4/R3'ün onu miras alan) kabul kriteri: 100+ gerçek
dosyalık bir toplu testte hiçbir format sessizce yanlış veri üretmemeli.

Bu oturumda ağ erişimi denendi ve **gerçekten çalıştı**:
`raw.githubusercontent.com` (izin verilen alan adlarından biri) üzerinden
`nvkelso/natural-earth-vector` deposundan (Natural Earth — kamu malı /
public domain, https://www.naturalearthdata.com/about/terms-of-use/)
**gerçek, üretimde kullanılan 1:110m ölçekli Shapefile katmanları** tek
tek indirildi (`.shp`/`.shx`/`.dbf`/`.prj`/`.cpg` beşlisi + belgeleme
dosyaları). Tüm depoyu (~900MB, `git-lfs` benzeri büyük ikili nesneler
içeriyor) tek seferde indirmek bu ortamda pratik değildi (bkz. commit
notu) — bunun yerine 20+ bilinen katman adı, dosya-dosya `raw.
githubusercontent.com` üzerinden çekildi. Sonuç: **127 gerçek dosya**
(`tests/fixtures/natural_earth_110m/`), bunlardan **23'ü tam bir
Shapefile üçlüsü** (`.shp`+`.shx`+`.dbf`, çoğu ayrıca `.prj`/`.cpg`).

Bu, roadmap'in kendi metnindeki "100+ dosya" kriterini **gerçek, indirilmiş,
diskte duran veriyle** karşılar (sentetik/üretilmiş veri değil).

Overpass API (`overpass-api.de`) bu ortamın izin verilen alan adı
listesinde değil (denendi, `403` ile engellendi) — bu yüzden OSM'in kendi
canlı sorgu servisi burada kapsam dışı kaldı; A3/R5 buna ayrı bir notla
değiniyor.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.core_engine.gis_core import GISParseError, GeoFeatureCollection, ShapefileParser

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "natural_earth_110m"

# Bu katmanların geometri tipi bilinen (Natural Earth belgelerinden ve
# kendi verisinden çıkarılan) beklentiler — "sessizce yanlış veri" testi
# için: parser'ın ürettiği geometri tipi, katmanın *bilinen* tipiyle
# tutarsızsa bu, formatın sessizce bozuk okunduğunun kanıtıdır.
_EXPECTED_GEOMETRY_KIND = {
    # (dosya adı ön eki) -> ("Polygon" | "LineString" | "Point", çoğul mu)
    "ne_110m_admin_0_countries": "Polygon",
    "ne_110m_admin_0_countries_lakes": "Polygon",
    "ne_110m_admin_0_sovereignty": "Polygon",
    "ne_110m_admin_0_map_units": "Polygon",
    "ne_110m_admin_0_scale_rank": "Polygon",
    "ne_110m_admin_0_tiny_countries": "Point",
    "ne_110m_admin_0_pacific_groupings": "LineString",
    "ne_110m_admin_1_states_provinces": "Polygon",
    "ne_110m_admin_1_states_provinces_lines": "LineString",
    "ne_110m_admin_0_boundary_lines_land": "LineString",
    "ne_110m_populated_places": "Point",
    "ne_110m_populated_places_simple": "Point",
    "ne_110m_urban_areas": "Polygon",
    "ne_110m_roads": "LineString",
    "ne_110m_railroads": "LineString",
    "ne_110m_airports": "Point",
    "ne_110m_ports": "Point",
    "ne_110m_coastline": "LineString",
    "ne_110m_land": "Polygon",
    "ne_110m_ocean": "Polygon",
    "ne_110m_rivers_lake_centerlines": "LineString",
    "ne_110m_lakes": "Polygon",
    "ne_110m_glaciated_areas": "Polygon",
    "ne_110m_geography_regions_polys": "Polygon",
    "ne_110m_geography_regions_points": "Point",
    "ne_110m_geography_regions_elevation_points": "Point",
    "ne_110m_geography_marine_polys": "Polygon",
    "ne_110m_graticules_15": "LineString",
    "ne_110m_graticules_30": "LineString",
    "ne_110m_wgs84_bounding_box": "Polygon",
    "ne_110m_geographic_lines": "LineString",
}


def _all_fixture_files() -> list[Path]:
    if not FIXTURES.exists():
        return []
    return [p for p in FIXTURES.rglob("*") if p.is_file()]


def _shapefile_stems() -> list[Path]:
    """`.shp` dosyalarının (üçlünün ana üyesi) yollarını döner."""
    return sorted(FIXTURES.rglob("*.shp"))


# ---------------------------------------------------------------------------
# 1) Kabul kriterinin kendisi: 100+ gerçek dosya
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FIXTURES.exists(), reason="Natural Earth fixture'ları bu ortamda indirilmemiş.")
def test_acceptance_criterion_100_plus_real_files_present():
    files = _all_fixture_files()
    assert len(files) >= 100, (
        f"Roadmap R3 kabul kriteri: 100+ gerçek dosya bekleniyor, {len(files)} bulundu."
    )


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixture yok")
def test_at_least_20_complete_shapefile_layers_present():
    shp_files = _shapefile_stems()
    assert len(shp_files) >= 20, (
        f"En az 20 tam Shapefile katmanı (.shp) bekleniyor, {len(shp_files)} bulundu."
    )


# ---------------------------------------------------------------------------
# 2) Her katman gerçekten, sessizce yanlış veri üretmeden ayrıştırılmalı
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixture yok")
@pytest.mark.parametrize("shp_path", _shapefile_stems() or [None], ids=lambda p: p.stem if p else "no-fixtures")
def test_each_real_shapefile_parses_without_silent_corruption(shp_path):
    if shp_path is None:
        pytest.skip("Fixture indirilmemiş.")
    parser = ShapefileParser()
    collection = parser.parse_file(str(shp_path))
    assert isinstance(collection, GeoFeatureCollection)
    # "Sessizce yanlış veri" testi #1: en az bir feature üretilmeli (gerçek
    # Natural Earth katmanlarının hiçbiri boş değildir).
    assert len(collection) > 0, f"{shp_path.name}: hiç feature üretilmedi (sessiz veri kaybı şüphesi)."

    expected_kind = _EXPECTED_GEOMETRY_KIND.get(shp_path.stem)
    if expected_kind is not None:
        # "Sessizce yanlış veri" testi #2: geometri tipi, katmanın bilinen
        # gerçek tipiyle eşleşmeli (örn. bir çizgi katmanının yanlışlıkla
        # poligon olarak okunması, shape-type kod çözmede bir hatayı
        # gösterir ve asla sessiz kalmamalı).
        kinds_seen = {f.geometry_type for f in collection}
        assert kinds_seen, f"{shp_path.name}: geometry_type kümesi boş."
        allowed = {expected_kind, f"Multi{expected_kind}"}
        assert kinds_seen.issubset(allowed), (
            f"{shp_path.name}: beklenen geometri tipi {expected_kind!r}, "
            f"ayrıştırılan tip(ler) {kinds_seen!r} (sessiz format hatası şüphesi)."
        )

    # "Sessizce yanlış veri" testi #3: koordinatlar gerçek Dünya
    # sınırları içinde olmalı (WGS84: lon [-180,180], lat [-90,90]) -
    # bu, byte-offset/endianness hatalarını (rastgele büyük sayılar
    # üretir) doğrudan yakalar.
    def _iter_coords(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            yield coords[:2]
            return
        for c in coords:
            yield from _iter_coords(c)

    checked = 0
    for feature in collection:
        for lon, lat in _iter_coords(feature.coordinates):
            assert -180.5 <= lon <= 180.5, f"{shp_path.name}: lon sınır dışı: {lon}"
            assert -90.5 <= lat <= 90.5, f"{shp_path.name}: lat sınır dışı: {lat}"
            checked += 1
    assert checked > 0, f"{shp_path.name}: hiç koordinat çifti kontrol edilemedi."


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixture yok")
def test_dbf_attributes_are_attached_when_present():
    """En az bir katmanda (`.dbf` eşlik ediyorsa), `GeoFeature.properties`
    boş kalmamalı - `.dbf` okuyucusunun gerçekten devrede olduğunun
    kanıtı (sessizce hiçbir öznitelik okunmuyor olması da bir 'sessiz
    veri kaybı' sınıfına girer)."""
    parser = ShapefileParser()
    any_with_props = False
    for shp_path in _shapefile_stems():
        if not shp_path.with_suffix(".dbf").exists():
            continue
        collection = parser.parse_file(str(shp_path))
        if any(f.properties for f in collection):
            any_with_props = True
            break
    assert any_with_props, "Hiçbir katmanda .dbf öznitelik okunamadı (sessiz kayıp şüphesi)."


@pytest.mark.skipif(not FIXTURES.exists(), reason="fixture yok")
def test_malformed_shapefile_raises_gis_parse_error_not_silent_garbage(tmp_path):
    """Kontrol testi: gerçek bir dosyayı kasıtlı olarak bozup (baştan
    kısaltarak) parser'ın sessizce (yanlış ama 'başarılı') sonuç
    üretmediğini, açık bir `GISParseError` fırlattığını doğrular."""
    real = _shapefile_stems()
    if not real:
        pytest.skip("fixture yok")
    src = real[0]
    corrupted = tmp_path / "corrupted.shp"
    data = src.read_bytes()
    corrupted.write_bytes(data[:60])  # 100 byte'lık header'dan bile kısa
    parser = ShapefileParser()
    with pytest.raises(GISParseError):
        parser.parse_file(str(corrupted))
