"""
İmar Ön-Kontrol / "Ruhsat Alır mı?" Simülasyonu
=================================================

Roadmap iş fikri #5: "pencere oranı, çekme mesafesi, kaçış yolu, kat sayısı
sınırı kontrolü, madde referanslı rapor."

Bu modül YENİ bir hesap motoru DEĞİLDİR — projede zaten var olan üç ayrı
denetim yüzeyini TEK bir "ruhsat ön-kontrolü" raporunda birleştirir:

    1. Pencere/cephe oranı + kaçış yolu zorunluluğu
       -> `facade_generator.FacadeGenerator.check_compliance`
    2. Yapısal makuliyet (narinlik, kat yüksekliği, plan eksantrikliği,
       yumuşak kat)
       -> `structural_validation.validate_building`
    3. Çekme mesafesi (setback) — parsel sınırı ile bina dış hattı arası
       asgari mesafe — YENİ (bu modülde eklendi, `regulations` profiline
       bağlı).
    4. Kat sayısı / yükseklik sınırı (imar hakkı — plana göre azami kat
       sayısı) — YENİ (kullanıcı/plan verisiyle karşılaştırma).

Açıkça belirtilmelidir: bu bir **resmi ruhsat değerlendirmesi değildir**.
Belediye imar müdürlüğünün yaptığı incelemenin yerini tutmaz; yalnızca
projede zaten hesaplanan sayısal eşiklerin tek bir "büyük olasılıkla
sorun çıkar / çıkarmaz" görünümünde özetlenmesidir. Her madde, kaynağını
(`regulations.RegulationProfile.source_label` + ilgili madde künyesi)
açıkça taşır — böylece kullanıcı hangi eşiğin nereden geldiğini görebilir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ..core_engine.geometry_engine import Point2D, Polygon
from .facade_generator import THRESHOLD_SOURCES, FacadeGenerator
from .regulations import RegulationProfile, default_profile
from .structural_validation import IssueSeverity, validate_building


class PermitVerdict(str, Enum):
    LIKELY_PASS = "likely_pass"
    NEEDS_REVISION = "needs_revision"
    LIKELY_FAIL = "likely_fail"


@dataclass(slots=True)
class PermitCheckItem:
    """Tek bir denetim maddesi (örn. 'pencere oranı') — geçti/geçmedi +
    madde referansı ile."""

    code: str
    title: str
    passed: bool
    value: float | None
    limit: float | None
    unit: str
    source: str
    message: str
    severity: IssueSeverity = IssueSeverity.WARNING


@dataclass(slots=True)
class PermitPrecheckReport:
    verdict: PermitVerdict
    profile_name: str
    profile_source: str
    items: list[PermitCheckItem] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for i in self.items if i.passed)

    @property
    def fail_count(self) -> int:
        return sum(1 for i in self.items if not i.passed)

    def summary_line(self) -> str:
        return (
            f"permit_precheck: {self.verdict.value} "
            f"({self.pass_count}/{len(self.items)} madde geçti)"
        )


def _min_distance_polygon_to_polygon(inner: Polygon, outer: Polygon) -> float:
    """Bina footprint'inin (inner), parsel sınırının (outer) her kenarına
    olan en kısa mesafesini döner — basit nokta-kenar mesafesi taraması
    (gerçek bir CAD-seviyesi Minkowski hesabı değil, ama pratik amaçlı
    yeterli: köşe/kenar örneklemesiyle küçük mesafe hatası ihmal
    edilebilir düzeydedir)."""

    def _point_segment_distance(p: Point2D, a: Point2D, b: Point2D) -> float:
        ax, ay, bx, by, px, py = a.x, a.y, b.x, b.y, p.x, p.y
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        cx, cy = ax + t * dx, ay + t * dy
        return math.hypot(px - cx, py - cy)

    outer_edges = list(zip(outer.points, outer.points[1:] + outer.points[:1]))
    min_dist = float("inf")
    for p in inner.points:
        for a, b in outer_edges:
            min_dist = min(min_dist, _point_segment_distance(p, a, b))
    return min_dist if min_dist != float("inf") else 0.0


def precheck_building(
    building,
    *,
    plot_polygon: Polygon | None = None,
    min_setback_m: float = 3.0,
    max_floor_count: int | None = None,
    max_height_m: float | None = None,
    profile: RegulationProfile | None = None,
) -> PermitPrecheckReport:
    """Bir `Building` için ruhsat ön-kontrol raporu üretir.

    `plot_polygon`: parsel sınırı verilirse çekme mesafesi kontrolü
    yapılır (verilmezse bu madde 'uygulanamaz' olarak info seviyesinde
    işaretlenir — inşa footprint'i tek başına parsel sınırını bilemez).
    `max_floor_count` / `max_height_m`: imar planının izin verdiği azami
    değerler (kullanıcıdan/plan notundan gelir — proje bunu üretmez).
    """
    profile = profile or default_profile()
    items: list[PermitCheckItem] = []

    building_type = (
        building.building_type.value
        if hasattr(building.building_type, "value")
        else str(building.building_type)
    )
    floor_count = len(building.floors)
    floor_height = building.floors[0].height_m if building.floors else 3.0

    # -- 1) Pencere/cephe oranı + kaçış yolu ------------------------------ #
    facade = FacadeGenerator.generate(
        polygon=building.footprint.polygon,
        building_type=building_type,
        base_z=0.0,
        floor_height=floor_height,
        floor_count=floor_count,
        build_mesh=False,
    )
    facade_report = FacadeGenerator.check_compliance(
        facade,
        building.footprint.polygon,
        floor_height,
        floor_count,
        building_type,
    )
    items.append(
        PermitCheckItem(
            code="window_wall_ratio",
            title="Pencere/duvar oranı (WWR)",
            passed=facade_report.meets_window_ratio,
            value=round(facade_report.window_wall_ratio, 4),
            limit=round(facade_report.min_required_ratio, 4),
            unit="oran",
            source=THRESHOLD_SOURCES.get("PAİY-8", "PAİY Madde 8"),
            message=(
                f"Cephedeki pencere oranı %{facade_report.window_wall_ratio * 100:.1f}, "
                f"asgari %{facade_report.min_required_ratio * 100:.1f} gerekli."
                if not facade_report.meets_window_ratio
                else f"Pencere oranı (%{facade_report.window_wall_ratio * 100:.1f}) asgari şartı karşılıyor."
            ),
            severity=IssueSeverity.CRITICAL
            if not facade_report.meets_window_ratio
            else IssueSeverity.INFO,
        )
    )

    escape_ok = not (
        facade_report.requires_fire_escape
        and "ikinci kaçış yolu" in " ".join(facade_report.issues).lower()
    )
    items.append(
        PermitCheckItem(
            code="fire_escape_route",
            title="Kaçış yolu (yangın merdiveni) yeterliliği",
            passed=escape_ok,
            value=float(floor_count),
            limit=4.0,
            unit="kat",
            source=THRESHOLD_SOURCES.get("BYKHY", "BYKHY"),
            message=(
                "4 kat ve üzeri yapılarda ikinci kaçış yolu zorunludur — bu bina için "
                "ayrıca doğrulanmalı."
                if facade_report.requires_fire_escape
                else "4 kat altı — ikinci kaçış yolu zorunluluğu bu eşiğe göre devreye girmiyor."
            ),
            severity=IssueSeverity.WARNING
            if facade_report.requires_fire_escape
            else IssueSeverity.INFO,
        )
    )

    # -- 2) Çekme mesafesi (setback) --------------------------------------- #
    if plot_polygon is not None:
        distance = _min_distance_polygon_to_polygon(building.footprint.polygon, plot_polygon)
        setback_ok = distance >= min_setback_m
        items.append(
            PermitCheckItem(
                code="setback_distance",
                title="Parsel sınırına çekme mesafesi",
                passed=setback_ok,
                value=round(distance, 2),
                limit=round(min_setback_m, 2),
                unit="m",
                source="Planlı Alanlar İmar Yönetmeliği, çekme mesafesi hükümleri (plana göre değişir)",
                message=(
                    f"Bina dış hattı, parsel sınırına en yakın noktada {distance:.2f} m "
                    f"mesafede; asgari {min_setback_m:.2f} m gerekli."
                    if not setback_ok
                    else f"Çekme mesafesi ({distance:.2f} m) asgari şartı ({min_setback_m:.2f} m) karşılıyor."
                ),
                severity=IssueSeverity.CRITICAL if not setback_ok else IssueSeverity.INFO,
            )
        )
    else:
        items.append(
            PermitCheckItem(
                code="setback_distance",
                title="Parsel sınırına çekme mesafesi",
                passed=True,
                value=None,
                limit=min_setback_m,
                unit="m",
                source="Planlı Alanlar İmar Yönetmeliği, çekme mesafesi hükümleri (plana göre değişir)",
                message="Parsel sınırı verilmedi — çekme mesafesi kontrolü atlandı (uygulanamaz).",
                severity=IssueSeverity.INFO,
            )
        )

    # -- 3) Kat sayısı / yükseklik sınırı (imar hakkı) --------------------- #
    if max_floor_count is not None:
        floors_ok = floor_count <= max_floor_count
        items.append(
            PermitCheckItem(
                code="max_floor_count",
                title="Azami kat sayısı (imar hakkı)",
                passed=floors_ok,
                value=float(floor_count),
                limit=float(max_floor_count),
                unit="kat",
                source="İmar planı plan notu (kullanıcı/plan verisi)",
                message=(
                    f"Bina {floor_count} kat, planın izin verdiği azami {max_floor_count} katı aşıyor."
                    if not floors_ok
                    else f"Bina {floor_count} kat — azami {max_floor_count} kat sınırı içinde."
                ),
                severity=IssueSeverity.CRITICAL if not floors_ok else IssueSeverity.INFO,
            )
        )
    if max_height_m is not None:
        height_ok = building.total_height_m <= max_height_m
        items.append(
            PermitCheckItem(
                code="max_height",
                title="Azami bina yüksekliği (imar hakkı)",
                passed=height_ok,
                value=round(building.total_height_m, 2),
                limit=round(max_height_m, 2),
                unit="m",
                source="İmar planı plan notu (kullanıcı/plan verisi)",
                message=(
                    f"Bina yüksekliği {building.total_height_m:.2f} m, azami {max_height_m:.2f} m'yi aşıyor."
                    if not height_ok
                    else f"Bina yüksekliği ({building.total_height_m:.2f} m) azami sınır içinde."
                ),
                severity=IssueSeverity.CRITICAL if not height_ok else IssueSeverity.INFO,
            )
        )

    # -- 4) Yapısal makuliyet (structural_validation'ın özeti) ------------- #
    structural_report = validate_building(building)
    critical_structural = [
        i for i in structural_report.issues if i.severity == IssueSeverity.CRITICAL
    ]
    items.append(
        PermitCheckItem(
            code="structural_plausibility",
            title="Yapısal makuliyet (ön-kontrol)",
            passed=structural_report.is_plausible,
            value=round(structural_report.slenderness_ratio, 2),
            limit=8.0,
            unit="narinlik oranı",
            source="Geometrik sağlık-kontrolü (resmi statik/deprem hesabı yerine geçmez)",
            message=(
                f"{len(critical_structural)} kritik yapısal uyarı bulundu: "
                + "; ".join(i.message for i in critical_structural[:2])
                if not structural_report.is_plausible
                else "Yapısal geometri ön-kontrolde makul görünüyor (kesin mühendislik raporu yerine geçmez)."
            ),
            severity=IssueSeverity.CRITICAL
            if not structural_report.is_plausible
            else IssueSeverity.INFO,
        )
    )

    # -- Genel karar -------------------------------------------------------- #
    critical_fails = [i for i in items if not i.passed and i.severity == IssueSeverity.CRITICAL]
    warning_fails = [i for i in items if not i.passed and i.severity == IssueSeverity.WARNING]
    if critical_fails:
        verdict = PermitVerdict.LIKELY_FAIL
    elif warning_fails:
        verdict = PermitVerdict.NEEDS_REVISION
    else:
        verdict = PermitVerdict.LIKELY_PASS

    return PermitPrecheckReport(
        verdict=verdict,
        profile_name=profile.name,
        profile_source=profile.source_label,
        items=items,
    )


__all__ = [
    "PermitVerdict",
    "PermitCheckItem",
    "PermitPrecheckReport",
    "precheck_building",
]
