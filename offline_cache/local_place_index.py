"""
offline_cache.local_place_index - Faz C5 (offline mod, A4): Nominatim'in
çevrimdışı alternatifi
================================================================================

ROADMAP_V7.md Bölüm A4: "Adres arama (Nominatim) offline alternatifi:
internet yokken arama çalışmaz — bunun yerine önceden indirilmiş bölge
için basit bir yerel isim→koordinat indeksi (ör. daha önce import edilmiş
OSM verisinden çıkarılan yer adları) opsiyonel olarak sunulabilir."

Bu modül tam bir geocoder DEĞİLDİR (fuzzy matching, adres parçalama,
enlem/boylam ağırlıklandırma gibi Nominatim'in yaptığı işleri yapmaz) -
A4'ün kendi ifadesiyle "basit bir yerel isim→koordinat indeksi": daha önce
`core_engine.gis_core.GeoFeatureCollection` olarak import edilmiş OSM
feature'larının `name` tag'inden (varsa) bir sözlük çıkarır, büyük/küçük
harf ve Türkçe karakter duyarsız alt-dize (substring) araması yapar.

Tasarım kararı — bu, C2'nin (`fetch_category_features`) çıktısını tüketir,
yeni bir OSM alan modeli icat etmez (roadmap'in "mevcut mimari korunacak"
ilkesi).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..core_engine.gis_core import GeoFeatureCollection


def _normalize(text: str) -> str:
    """Türkçe karakter/büyük-küçük harf duyarsız arama için basit
    normalize (stdlib-only, `str.casefold` + elle Türkçe harf eşlemesi -
    `locale` modülüne bağımlı olmadan platformlar arası tutarlı davranış
    için)."""
    table = str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")
    return text.translate(table).casefold().strip()


@dataclass(slots=True)
class PlaceEntry:
    name: str
    lat: float
    lon: float
    category: str = ""
    feature_type: str = ""


@dataclass(slots=True)
class LocalPlaceIndex:
    """Bellek-içi isim -> koordinat indeksi + basit alt-dize araması."""

    entries: list[PlaceEntry] = field(default_factory=list)

    def add(self, entry: PlaceEntry) -> None:
        self.entries.append(entry)

    def __len__(self) -> int:
        return len(self.entries)

    def search(self, query: str, limit: int = 10) -> list[PlaceEntry]:
        """`query`'yi (alt-dize, Türkçe/büyük-küçük harf duyarsız) isim
        alanında arar. Nominatim'in tersine coğrafi/popülerlik
        ağırlıklandırması yapmaz - A4'ün "basit" isteğiyle tutarlı; sonuçlar
        `entries` listesindeki sırayla (ekleme sırası) döner."""
        needle = _normalize(query)
        if not needle:
            return []
        matches = [e for e in self.entries if needle in _normalize(e.name)]
        return matches[:limit]

    # -- kalıcılık (önceden indirilmiş bölge = "offline paket") --------- #

    def save(self, path: str | Path) -> None:
        data = [
            {
                "name": e.name,
                "lat": e.lat,
                "lon": e.lon,
                "category": e.category,
                "feature_type": e.feature_type,
            }
            for e in self.entries
        ]
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> LocalPlaceIndex:
        p = Path(path)
        if not p.is_file():
            return cls()
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return cls()
        return cls([PlaceEntry(**item) for item in raw])


def build_index_from_collection(
    collection: GeoFeatureCollection,
    *,
    category: str = "",
    name_keys: tuple[str, ...] = ("name", "name:tr", "addr:housename"),
) -> LocalPlaceIndex:
    """C2'nin `GeoFeatureCollection` çıktısından (herhangi bir kategori -
    yol, POI, bina...) `name` tag'i olan feature'ları çıkarıp bir
    `LocalPlaceIndex` kurar. `name` yoksa feature sessizce atlanır (B4'ün
    "eksik veri sahneyi bozmasın" ilkesiyle tutarlı - burada "sahne" yerine
    "arama indeksi" ama aynı felsefe)."""
    index = LocalPlaceIndex()
    for feature in collection:
        name = None
        for key in name_keys:
            value = feature.properties.get(key)
            if value:
                name = str(value)
                break
        if not name:
            continue
        try:
            if feature.geometry_type == "Point":
                point = feature.to_point()
                lat, lon = point.y, point.x
            elif feature.geometry_type == "Polygon":
                ring = feature.coordinates[0]
                lon = sum(c[0] for c in ring) / len(ring)
                lat = sum(c[1] for c in ring) / len(ring)
            elif feature.geometry_type == "LineString":
                mid = feature.coordinates[len(feature.coordinates) // 2]
                lon, lat = mid[0], mid[1]
            else:
                continue
        except (IndexError, ZeroDivisionError, ValueError):
            continue
        index.add(
            PlaceEntry(
                name=name,
                lat=lat,
                lon=lon,
                category=category or str(feature.properties.get("__category__", "")),
                feature_type=feature.geometry_type,
            )
        )
    return index


def merge_indices(indices: list[LocalPlaceIndex]) -> LocalPlaceIndex:
    """Birden çok kategori/import turundan gelen indeksleri tek bir
    `LocalPlaceIndex`'e birleştirir (B5'in "katman bazlı içe aktarma"
    akışıyla tutarlı - her import turu kendi indeksini üretir, sonda
    birleştirilir)."""
    merged = LocalPlaceIndex()
    for idx in indices:
        merged.entries.extend(idx.entries)
    return merged


__all__ = [
    "PlaceEntry",
    "LocalPlaceIndex",
    "build_index_from_collection",
    "merge_indices",
]
