"""Saha ölçüm oturumu veri modeli: `FieldPoint` + `FieldSurveySession`."""

from __future__ import annotations

from dataclasses import dataclass, field

from .codes import FeatureCode


@dataclass(slots=True)
class FieldPoint:
    """Sahada ölçülen tek bir kodlanmış nokta (total station / RTK-GNSS).

    `easting`/`northing`/`elevation` yerel/projeksiyonlu (metre) koordinat
    kabul eder — çoğu total station çıktısı zaten böyledir. WGS84 lat/lon
    gerekiyorsa `coordinate_systems` modülündeki dönüşümlerle ayrıca
    projelendirilebilir (bu sınıf projeksiyon bilgisini taşımaz, sorumluluk
    çağıran koda aittir — mevcut `ProjectedPoint.system` deseniyle tutarlı).
    """

    point_id: str
    easting: float
    northing: float
    elevation: float
    code: FeatureCode
    raw_code: str | None = None  # kod OTHER ise ham metin burada
    description: str = ""
    string_id: str | None = None  # aynı çizgiye/poligona ait noktaları gruplar (örn. "BLD01")
    instrument: str = "unknown"  # "total_station" | "rtk_gnss" | "manual"
    timestamp: str | None = None


@dataclass(slots=True)
class FieldSurveySession:
    """Bir saha ölçüm oturumundaki tüm noktaların koleksiyonu."""

    name: str
    points: list[FieldPoint] = field(default_factory=list)
    crs: str = "local"  # örn. "EPSG:32636" (UTM 36N) — proje bazında atanır

    def add(self, point: FieldPoint) -> None:
        self.points.append(point)

    def by_code(self, code: FeatureCode) -> list[FieldPoint]:
        return [p for p in self.points if p.code == code]

    def by_category(self) -> dict[str, list[FieldPoint]]:
        result: dict[str, list[FieldPoint]] = {}
        for p in self.points:
            result.setdefault(p.code.category.value, []).append(p)
        return result

    def strings(self) -> dict[str, list[FieldPoint]]:
        """`string_id`'ye göre gruplanmış noktalar (çizgi/poligon üretimi
        için) — grup içindeki sıra, oturuma eklenme sırasıdır (sahada
        ölçüm sırası genelde geometrik sırayla örtüşür)."""
        grouped: dict[str, list[FieldPoint]] = {}
        for p in self.points:
            if p.string_id is None:
                continue
            grouped.setdefault(p.string_id, []).append(p)
        return grouped

    def summary(self) -> dict[str, int]:
        """Kod başına nokta sayısı — saha raporu / QA için hızlı özet."""
        counts: dict[str, int] = {}
        for p in self.points:
            key = p.code.value
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))
