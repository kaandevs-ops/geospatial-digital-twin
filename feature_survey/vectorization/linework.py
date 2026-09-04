"""FAZ S4 — Kod-Tabanlı Otomatik Vektörleştirme.

Roadmap ROADMAP_V6.md FAZ S4: `codes.py`'deki feature kodlarından ve
`field_point.FieldSurveySession`'daki ölçüm sırasından, otomatik
çizgi/poligon (linework) üretimi. İki gruplama stratejisi desteklenir:

1. **Açık gruplama (`string_id`)** — sahada cihazın/operatörün elle atadığı
   grup kimliği (örn. "BLD01"). Bu, geleneksel "bağlantı kodu" (link code)
   pratiğinin (`BLD_COR 1`, `BLD_COR 2`, ... aynı bina farklı gruplara
   ayrılabilir) doğrudan karşılığıdır — grup numarası `string_id` içine
   kodlanır (örn. "BLD_COR:1").
2. **Örtük gruplama** — `string_id` verilmemiş, ama ardışık ölçülen
   noktalar aynı koda sahipse (geleneksel "aynı kod = aynı çizgi" sahra
   pratiği) otomatik olarak tek bir çizgiye/poligona bağlanır; kod
   değiştiği anda grup kapanır.

Bu modül **hiçbir enterpolasyon/tahmin yapmaz** — sadece var olan ölçülmüş
noktaları, kodun `geometry_hint`'ine göre birbirine bağlar. Poligon olarak
işaretli (`geometry_hint == "Polygon"`) bir grup, ilk ve son nokta çakışık
değilse **otomatik kapatılır** (ilk nokta sona eklenir) — bu, mühendislik
pratiğinde "bina köşe zinciri kapanır" kuralının doğrudan uygulamasıdır ve
yeni bir koordinat *üretmez*, var olan ilk noktayı tekrarlar.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..codes import FeatureCode
from ..field_point import FieldPoint, FieldSurveySession


class VectorizationError(ValueError):
    """Vektörleştirme için yetersiz veri (örn. LineString için < 2 nokta,
    Polygon için < 3 nokta) durumunda fırlatılır — sessizce eksik/bozuk
    geometri üretilmez."""


@dataclass(slots=True)
class VectorFeature:
    """Vektörleştirme çıktısı: tek bir çizgi/poligon/nokta öğesi.

    `coordinates`, ham `FieldPoint.easting/northing/elevation` değerlerinden
    **doğrudan** kopyalanır — hiçbir enterpolasyon/yuvarlama yapılmaz.
    """

    kind: str  # "Point" | "LineString" | "Polygon"
    code: FeatureCode
    string_id: str | None
    coordinates: list[tuple[float, float, float]]
    source_point_ids: list[str] = field(default_factory=list)
    closed: bool = False  # Polygon için otomatik kapatma uygulandı mı

    def to_geojson_geometry(self) -> dict:
        """GeoJSON geometri sözlüğü (2B — E/N eksenleri; Z ayrıca
        `elevations` altında saklanır, GeoJSON'ın standart 2B eksenine
        sadık kalınır)."""
        coords_2d = [[c[0], c[1]] for c in self.coordinates]
        elevations = [c[2] for c in self.coordinates]
        if self.kind == "Point":
            geom = {"type": "Point", "coordinates": coords_2d[0]}
        elif self.kind == "LineString":
            geom = {"type": "LineString", "coordinates": coords_2d}
        elif self.kind == "Polygon":
            geom = {"type": "Polygon", "coordinates": [coords_2d]}
        else:
            raise VectorizationError(f"Bilinmeyen geometri türü: {self.kind}")
        return geom, elevations

    def to_geojson_feature(self) -> dict:
        geom, elevations = self.to_geojson_geometry()
        return {
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "code": self.code.value,
                "string_id": self.string_id,
                "closed": self.closed,
                "elevations_m": elevations,
                "source_point_ids": self.source_point_ids,
            },
        }


def _point_matches(a: FieldPoint, b: FieldPoint, tol: float = 1e-6) -> bool:
    return (
        abs(a.easting - b.easting) <= tol
        and abs(a.northing - b.northing) <= tol
        and abs(a.elevation - b.elevation) <= tol
    )


def _finalize_group(
    points: list[FieldPoint], code: FeatureCode, string_id: str | None
) -> VectorFeature:
    hint = code.geometry_hint
    coords = [(p.easting, p.northing, p.elevation) for p in points]
    ids = [p.point_id for p in points]

    if hint == "Point":
        if len(coords) != 1:
            raise VectorizationError(
                f"Kod {code.value} 'Point' geometrisine karşılık gelir ama "
                f"{len(coords)} nokta gruplanmış — grup mantığı hatalı."
            )
        return VectorFeature("Point", code, string_id, coords, ids)

    if hint == "LineString":
        if len(coords) < 2:
            raise VectorizationError(
                f"Kod {code.value} için LineString üretmek üzere en az 2 "
                f"nokta gerekir, {len(coords)} bulundu (grup: {string_id})."
            )
        return VectorFeature("LineString", code, string_id, coords, ids)

    if hint == "Polygon":
        if len(coords) < 3:
            raise VectorizationError(
                f"Kod {code.value} için Polygon üretmek üzere en az 3 "
                f"nokta gerekir, {len(coords)} bulundu (grup: {string_id})."
            )
        closed = False
        if not _point_matches(points[0], points[-1]):
            coords = coords + [coords[0]]
            ids = ids + [ids[0]]
            closed = True
        return VectorFeature("Polygon", code, string_id, coords, ids, closed=closed)

    raise VectorizationError(f"Desteklenmeyen geometry_hint: {hint}")


def build_linework(session: FieldSurveySession) -> list[VectorFeature]:
    """Bir `FieldSurveySession`'daki tüm noktalardan otomatik linework
    üretir.

    Algoritma:
    1. `string_id` verilmiş noktalar -> `string_id`'ye göre gruplanır
       (`session.strings()`, ekleme sırasını korur).
    2. `string_id` verilmemiş (`None`) noktalar -> ölçüm sırasında ardışık
       aynı-kodlu noktalar örtük olarak gruplanır (kod değişince grup
       kapanır; `Point` geometrili kodlar zaten tek nokta olduğundan
       gruplama gerektirmez, her biri ayrı `VectorFeature` olur).

    Bir grup içinde birden fazla `FeatureCode` varsa (örn. `string_id`
    hatalı atanmış) `VectorizationError` fırlatılır — sessizce ilk kodu
    kullanıp veri kaybına yol açılmaz.
    """

    features: list[VectorFeature] = []

    grouped_ids = session.strings()
    for string_id, points in grouped_ids.items():
        codes = {p.code for p in points}
        if len(codes) > 1:
            raise VectorizationError(
                f"string_id={string_id!r} grubunda birden fazla feature kodu "
                f"var ({[c.value for c in codes]}) — her grup tek koda ait olmalı."
            )
        features.append(_finalize_group(points, points[0].code, string_id))

    ungrouped = [p for p in session.points if p.string_id is None]
    i = 0
    while i < len(ungrouped):
        code = ungrouped[i].code
        hint = code.geometry_hint
        if hint == "Point":
            features.append(_finalize_group([ungrouped[i]], code, None))
            i += 1
            continue
        j = i
        while j < len(ungrouped) and ungrouped[j].code == code:
            j += 1
        run = ungrouped[i:j]
        if len(run) >= 2 or hint == "LineString" and len(run) >= 2:
            try:
                features.append(_finalize_group(run, code, None))
            except VectorizationError:
                # Tek nokta kaldıysa (örn. son çalışan grup 1 noktalık) —
                # geometri üretilemez, sessizce atlanmaz, açıkça raporlanır
                # üstteki exception zaten mesajı taşıyor; burada yut ve
                # noktayı 'yetersiz veri' olarak işaretleyip devam etme
                # KARARI: sessiz veri kaybı yasak -> yeniden fırlat.
                raise
        else:
            raise VectorizationError(
                f"Kod {code.value} için {hint} geometrisi gerekiyor ama "
                f"örtük grupta (string_id yok) yalnızca {len(run)} ardışık "
                f"nokta var (nokta id'leri: {[p.point_id for p in run]})."
            )
        i = j

    return features


def linework_to_geojson(features: list[VectorFeature]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [f.to_geojson_feature() for f in features],
    }
