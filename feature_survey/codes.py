"""Feature kod taksonomisi.

Mühendisler sahada her noktayı bir "kod" ile işaretler (total station / RTK
kayıt cihazlarında geleneksel olarak PENZD-kod formatı: Point, Easting,
Northing, Elevation, Description/Code). Bu modül o kod kümesini tip-güvenli
bir enum olarak tanımlar; serbest metin kod da (`FeatureCode.OTHER` +
`raw_code`) desteklenir çünkü sahada firmaya/ülkeye göre kod sözlükleri
değişir (bu, "sessizce reddetme" yerine esnek kabul ilkesiyle tutarlıdır).
"""

from __future__ import annotations

from enum import Enum


class FeatureCategory(str, Enum):
    """Üst düzey gruplama — arayüzde katman/renk ataması için kullanılır."""

    BUILDING = "building"
    ROAD = "road"
    UTILITY = "utility"
    VEGETATION = "vegetation"
    HYDROLOGY = "hydrology"
    CONTROL = "control"  # jeodezik kontrol noktası
    OTHER = "other"


class FeatureCode(str, Enum):
    """Sahada sık kullanılan feature kodları (TR harita mühendisliği
    pratiğiyle uyumlu, İngilizce kısa kod + Türkçe açıklama)."""

    BUILDING_CORNER = "BLD_COR"       # bina köşesi
    BUILDING_EAVE = "BLD_EAVE"        # bina saçak hattı
    ROAD_EDGE = "RD_EDGE"             # yol kenarı
    ROAD_CENTERLINE = "RD_CL"         # yol ekseni
    CURB = "CURB"                     # bordür
    POWER_POLE = "PWR_POLE"           # elektrik direği
    STREETLIGHT = "STR_LIGHT"         # aydınlatma direği
    MANHOLE = "MANHOLE"               # rögar/manhole kapağı
    TREE = "TREE"                     # ağaç
    HEDGE = "HEDGE"                   # çalı/çit sınırı
    WATER_EDGE = "WTR_EDGE"           # su kenarı (dere/göl/deniz)
    CONTROL_POINT = "CTRL_PT"         # jeodezik kontrol/nirengi noktası
    BENCHMARK = "BM"                  # kot noktası (RS/BM)
    FENCE = "FENCE"                   # çit hattı
    WALL = "WALL"                     # istinat/bahçe duvarı
    SPOT_ELEVATION = "SPOT_ELEV"      # ara nokta (arazi kotu)
    OTHER = "OTHER"

    @property
    def category(self) -> FeatureCategory:
        return _CODE_CATEGORY.get(self, FeatureCategory.OTHER)

    @property
    def geometry_hint(self) -> str:
        """Bu kodun tipik olarak hangi geometriye dönüştüğü ("Point" |
        "LineString" | "Polygon"). Ardışık aynı-kodlu noktalar
        `FieldSurveySession.linework()` ile çizgi/poligona bağlanabilir."""
        return _CODE_GEOMETRY.get(self, "Point")


_CODE_CATEGORY: dict[FeatureCode, FeatureCategory] = {
    FeatureCode.BUILDING_CORNER: FeatureCategory.BUILDING,
    FeatureCode.BUILDING_EAVE: FeatureCategory.BUILDING,
    FeatureCode.ROAD_EDGE: FeatureCategory.ROAD,
    FeatureCode.ROAD_CENTERLINE: FeatureCategory.ROAD,
    FeatureCode.CURB: FeatureCategory.ROAD,
    FeatureCode.POWER_POLE: FeatureCategory.UTILITY,
    FeatureCode.STREETLIGHT: FeatureCategory.UTILITY,
    FeatureCode.MANHOLE: FeatureCategory.UTILITY,
    FeatureCode.TREE: FeatureCategory.VEGETATION,
    FeatureCode.HEDGE: FeatureCategory.VEGETATION,
    FeatureCode.WATER_EDGE: FeatureCategory.HYDROLOGY,
    FeatureCode.CONTROL_POINT: FeatureCategory.CONTROL,
    FeatureCode.BENCHMARK: FeatureCategory.CONTROL,
    FeatureCode.FENCE: FeatureCategory.OTHER,
    FeatureCode.WALL: FeatureCategory.OTHER,
    FeatureCode.SPOT_ELEVATION: FeatureCategory.OTHER,
}

_CODE_GEOMETRY: dict[FeatureCode, str] = {
    FeatureCode.BUILDING_CORNER: "Polygon",   # ardışık köşeler bina cephesini kapatır
    FeatureCode.BUILDING_EAVE: "LineString",
    FeatureCode.ROAD_EDGE: "LineString",
    FeatureCode.ROAD_CENTERLINE: "LineString",
    FeatureCode.CURB: "LineString",
    FeatureCode.HEDGE: "LineString",
    FeatureCode.WATER_EDGE: "LineString",
    FeatureCode.FENCE: "LineString",
    FeatureCode.WALL: "LineString",
    FeatureCode.POWER_POLE: "Point",
    FeatureCode.STREETLIGHT: "Point",
    FeatureCode.MANHOLE: "Point",
    FeatureCode.TREE: "Point",
    FeatureCode.CONTROL_POINT: "Point",
    FeatureCode.BENCHMARK: "Point",
    FeatureCode.SPOT_ELEVATION: "Point",
}
