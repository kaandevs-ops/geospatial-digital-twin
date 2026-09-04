"""
Yapısal Doğrulama (Structural Validation)
==========================================

Roadmap Faz 1.5 — "Yapısal mantık": tam bir yapısal analiz motoru DEĞİL;
"bu bina fiziksel olarak saçma mı?" sorusuna hızlı, sayısal bir cevap veren
bir sağlık-kontrolü (sanity check) katmanı. `Building` (bkz.
`procedural_generator`) nesnesini girdi alır, bulguları `StructuralIssue`
listesi olarak döner.

Kapsam (roadmap'in kendi metniyle):
    - Konsol (cantilever) mesafesi limiti — balkon/çıkma projeksiyonu.
    - Kat başına maksimum çıkma (şimdilik: aynı balkon kontrolü, kat
      footprint'i şu an tüm katlarda sabit olduğu için ayrı bir "üst kat
      alt kattan taşıyor mu" kontrolü henüz anlamlı değil — bkz. NOT).
    - Temel/taşıyıcı sistem tutarlılığı — narinlik oranı (slenderness
      ratio: toplam yükseklik / en dar footprint boyutu) çok yüksekse
      (öngörülen mühendislik olmadan) bayrak.
    - Kat yüksekliği sağlığı — anormal derecede alçak/yüksek katlar.

NOT (dürüstçe belgelenmiş sınırlama): `ProceduralBuildingGenerator` şu an
her katta AYNI footprint'i kullanıyor (kat bazlı setback/çekme kat henüz
uygulanmadı — roadmap Faz 1.2'nin ayrı bir maddesi). Bu yüzden "üst kat alt
kattan ne kadar taşıyor" kontrolü şu an her zaman 0 verir; bu modül bunu
gizlemek yerine açıkça `cantilever_m=0.0` olarak raporlar ve
`applicable=False` bayrağı ekler.

Bu, gerçek bir statik/deprem analizi DEĞİLDİR — yalnızca bariz fiziksel
tutarsızlıkları (aşırı ince/uzun bina, aşırı konsol, saçma kat yüksekliği)
erken yakalamak içindir. Sonuçlar "gösterge niteliğindedir", kesin mühendislik
raporu yerine geçmez (bkz. Faz 2.3'teki aynı ilke: deprem risk skoru için de
kullanılan dil).

EK KONTROLLER (TBDY 2018 düzensizlik tanımlarına GEOMETRİK YAKLAŞIM):
    TBDY 2018 Bölüm 3, deprem hesabında dikkate alınması gereken düzensizlik
    türlerini tanımlar: A1 (burulma düzensizliği — kat ötelenme oranlarının
    birbirine oranı), B1 (zayıf kat — kat dayanımlarının oranı), B2 (yumuşak
    kat — kat rijitliklerinin oranı). Bunların TAM/RESMİ hesabı, kat bazlı
    rijitlik/dayanım/kütle matrisleri gerektirir — bu proje modelinde
    (`Building`/`Floor`) böyle bir yapısal analiz verisi YOK (yalnızca
    geometri var). Bu yüzden aşağıdaki kontroller, TBDY'nin resmi
    hesaplarının YERİNE GEÇMEYEN, yalnızca GEOMETRİK PROXY'lerdir:

        - Plan eksantrikliği (A1'in proxy'si): footprint'in alan-ağırlıklı
          merkezi (centroid) ile bounding-box merkezi arasındaki kayma.
          Bu, gerçek "rijitlik merkezi - kütle merkezi" eksantrikliği
          DEĞİLDİR (rijitlik dağılımı bilinmiyor); yalnızca çarpık/L-U-T
          biçimli planların dışarıdan görülebilir bir işaretidir — tıpkı
          FEMA P-154'ün sokaktan yapılan görsel taramada "plan
          irregularity"yi bina dış hatlarından okumasına benzer mantık.
        - Kat yüksekliği sıçraması (B2 - yumuşak kat proxy'si): bir katın
          yüksekliğinin komşu katlara oranla anormal büyük olması (örn.
          zemin katta yüksek tavanlı dükkan/lobi). Gerçek rijitlik oranı
          hesaplanmadan bu yalnızca bir GÖRSEL/GEOMETRİK kırmızı bayraktır
          — ki FEMA P-154'ün "soft/weak story" kriteri de zaten sahada tam
          olarak buna, dıştan gözlemlenebilen kat yüksekliği/açıklık
          farkına dayanır (iç rijitlik ölçülemediği için).

    Kısacası: bu kontroller artık TBDY'nin isimlendirdiği düzensizlik
    kategorileriyle KAVRAMSAL OLARAK hizalı, ama TBDY'nin kendisinin
    talep ettiği sayısal (rijitlik/dayanım) hesabı değil.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ..core_engine.geometry_engine import Point2D, Polygon


class IssueSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(slots=True)
class StructuralIssue:
    code: str
    severity: IssueSeverity
    message: str
    value: float
    limit: float


@dataclass(slots=True)
class StructuralValidationReport:
    is_plausible: bool
    slenderness_ratio: float
    max_cantilever_m: float
    issues: list[StructuralIssue] = field(default_factory=list)

    def summary_line(self) -> str:
        status = "OK" if self.is_plausible else "SORUNLU"
        return (
            f"structural_validation: {status} "
            f"slenderness={self.slenderness_ratio:.2f} "
            f"max_cantilever_m={self.max_cantilever_m:.2f} "
            f"issue_count={len(self.issues)}"
        )


# Varsayılan limitler (tipik konut/ofis ölçeği; gerçek yönetmelik/mühendislik
# hesabı yerine geçmez — bkz. modül docstring'i).
DEFAULT_MAX_CANTILEVER_M = 1.8       # tipik balkon/çıkma projeksiyon limiti
#: Narinlik oranı eşiği (toplam yükseklik / en dar footprint boyutu, H/B).
#: BU OTURUMDA DOĞRULANDI (resmigazete.gov.tr + afad.gov.tr TBDY 2018
#: tam metni araştırıldı): TBDY 2018 Madde 3.3, Bina Yükseklik Sınıfını
#: (BYS) MUTLAK bina yüksekliği H'ye (metre) ve Deprem Tasarım Sınıfına
#: (DTS) göre tanımlar — H/B (yükseklik/taban) ORANINA dayanan resmi bir
#: "narinlik sınırı" TBDY'de YOKTUR (burulma düzensizliği ayrı bir
#: kavramdır: ηbi<1.4 ötelenme-oranı katsayısı, gerçek kat deplasmanı
#: analizi gerektirir, geometriden hesaplanamaz — bkz. modül docstring'i,
#: A1 proxy notu). Bu yüzden 8.0 değeri hâlâ TEMSİLİDİR ve TBDY'ye
#: bağlanamaz (böyle resmi bir sayı yok) — genel yapı mühendisliği
#: pratiğinde (rüzgar/devrilme stabilitesi ön-tasarım kontrolleri gibi
#: TBDY-dışı bağlamlarda) sık atıfta bulunulan kaba bir eşiktir.
DEFAULT_MAX_SLENDERNESS_RATIO = 8.0
DEFAULT_MIN_FLOOR_HEIGHT_M = 2.2     # TSE/yönetmelik asgari net kat yüksekliği yaklaşıklaması
DEFAULT_MAX_FLOOR_HEIGHT_M = 8.0     # bu değerin üzeri (asma kat/atrium hariç) olağandışı

#: A1 (burulma düzensizliği) GEOMETRİK PROXY eşiği: footprint centroid'inin
#: bounding-box merkezinden kayma oranı (kayma_mesafesi / karakteristik_boyut).
#: TBDY'nin resmi eta_bi (ötelenme oranı) eşiği DEĞİLDİR — yalnızca "bu plan
#: çarpık/asimetrik mi" sorusuna kaba bir geometrik cevaptır.
DEFAULT_MAX_PLAN_ECCENTRICITY_RATIO = 0.10

#: B2 (yumuşak kat) GEOMETRİK PROXY eşiği: bir katın yüksekliğinin, komşu
#: katların ortalamasına oranı. TBDY'nin resmi rijitlik-oranı (%70/%80)
#: eşiklerinin KAVRAMSAL YANSIMASIDIR (aynı sayısal anlamda değil — rijitlik
#: yerine yükseklik kullanılıyor, bkz. modül docstring'i).
DEFAULT_SOFT_STORY_HEIGHT_RATIO = 1.5


def _footprint_min_dimension(polygon: Polygon) -> float:
    """Footprint'in en dar (bounding-box) boyutunu döner — narinlik oranı
    paydası için basit bir yaklaşıklama (gerçek moment-of-inertia analizi
    değil)."""
    xs = [p.x for p in polygon.points]
    ys = [p.y for p in polygon.points]
    width = max(xs) - min(xs)
    depth = max(ys) - min(ys)
    return min(width, depth) if width > 0 and depth > 0 else max(width, depth, 1e-6)


def validate_building(
    building,
    *,
    balconies: list | None = None,
    max_cantilever_m: float = DEFAULT_MAX_CANTILEVER_M,
    max_slenderness_ratio: float = DEFAULT_MAX_SLENDERNESS_RATIO,
    min_floor_height_m: float = DEFAULT_MIN_FLOOR_HEIGHT_M,
    max_floor_height_m: float = DEFAULT_MAX_FLOOR_HEIGHT_M,
    max_plan_eccentricity_ratio: float = DEFAULT_MAX_PLAN_ECCENTRICITY_RATIO,
    soft_story_height_ratio: float = DEFAULT_SOFT_STORY_HEIGHT_RATIO,
) -> StructuralValidationReport:
    """`Building` üzerinde temel fiziksel tutarlılık kontrolü.

    `balconies`: opsiyonel — `BalconyGenerator` çıktısı (`Balcony` listesi)
    verilirse konsol mesafesi bunlardan da kontrol edilir. Verilmezse
    konsol kontrolü yalnızca kat-footprint taşması üzerinden yapılır
    (bkz. modül docstring'indeki NOT).
    """
    issues: list[StructuralIssue] = []

    # -- 1) Narinlik oranı (slenderness) ---------------------------------- #
    min_dim = _footprint_min_dimension(building.footprint.polygon)
    total_height = building.total_height_m
    slenderness = total_height / min_dim if min_dim > 0 else float("inf")
    if slenderness > max_slenderness_ratio:
        issues.append(StructuralIssue(
            code="excessive_slenderness",
            severity=IssueSeverity.CRITICAL if slenderness > max_slenderness_ratio * 1.5 else IssueSeverity.WARNING,
            message=(
                f"Bina çok ince/uzun görünüyor (yükseklik/en-dar-kenar oranı "
                f"{slenderness:.1f}, limit {max_slenderness_ratio:.1f}). Gerçek "
                "yapısal analiz olmadan bu geometri fiziksel olarak riskli kabul edilmeli."
            ),
            value=slenderness, limit=max_slenderness_ratio,
        ))

    # -- 2) Kat yüksekliği sağlığı ---------------------------------------- #
    for floor in building.floors:
        if floor.height_m < min_floor_height_m:
            issues.append(StructuralIssue(
                code="floor_height_too_low",
                severity=IssueSeverity.WARNING,
                message=(
                    f"Kat {floor.level}: yükseklik {floor.height_m:.2f} m, "
                    f"asgari makul değer {min_floor_height_m:.2f} m'nin altında."
                ),
                value=floor.height_m, limit=min_floor_height_m,
            ))
        elif floor.height_m > max_floor_height_m:
            issues.append(StructuralIssue(
                code="floor_height_unusually_high",
                severity=IssueSeverity.INFO,
                message=(
                    f"Kat {floor.level}: yükseklik {floor.height_m:.2f} m, "
                    f"olağan üst sınır {max_floor_height_m:.2f} m'nin üzerinde "
                    "(atrium/asma kat değilse gözden geçirin)."
                ),
                value=floor.height_m, limit=max_floor_height_m,
            ))

    # -- 3) Konsol (cantilever) — balkon projeksiyonları ------------------- #
    max_cantilever_found = 0.0
    for balcony in (balconies or []):
        depth = getattr(balcony, "depth", 0.0)
        max_cantilever_found = max(max_cantilever_found, depth)
        if depth > max_cantilever_m:
            issues.append(StructuralIssue(
                code="excessive_cantilever",
                severity=IssueSeverity.CRITICAL if depth > max_cantilever_m * 1.5 else IssueSeverity.WARNING,
                message=(
                    f"Balkon projeksiyonu {depth:.2f} m, tipik konsol limiti "
                    f"{max_cantilever_m:.2f} m'yi aşıyor — gerçek statik hesap gerekir."
                ),
                value=depth, limit=max_cantilever_m,
            ))

    # -- 4) Kat-footprint taşması (şu an her zaman 0 — bkz. modül NOT'u) -- #
    # `ProceduralBuildingGenerator` tüm katlarda aynı footprint'i kullandığı
    # için üst kat hiçbir zaman alt kattan taşmaz. Bu bilinçli olarak
    # raporlanır (gizlenmez) — kat-bazlı setback eklenince bu bölüm
    # gerçek bir taşma hesabına dönüştürülmeli.

    # -- 5) Plan eksantrikliği — TBDY A1 (burulma düzensizliği) GEOMETRİK
    #       PROXY'si. Footprint alan-merkezi ile bounding-box merkezi
    #       arasındaki kayma; rijitlik/kütle merkezi eksantrikliği DEĞİLDİR
    #       (bkz. modül docstring'i) ama çarpık/asimetrik planları yakalar.
    polygon = building.footprint.polygon
    centroid = polygon.centroid()
    min_x, min_y, max_x, max_y = polygon.bounding_box()
    bbox_center_x = (min_x + max_x) / 2.0
    bbox_center_y = (min_y + max_y) / 2.0
    characteristic_size = max(max_x - min_x, max_y - min_y, 1e-6)
    eccentricity = math.hypot(centroid.x - bbox_center_x, centroid.y - bbox_center_y)
    eccentricity_ratio = eccentricity / characteristic_size
    if eccentricity_ratio > max_plan_eccentricity_ratio:
        issues.append(StructuralIssue(
            code="plan_eccentricity_proxy",
            severity=IssueSeverity.WARNING,
            message=(
                f"Footprint alan-merkezi, bounding-box merkezinden "
                f"{eccentricity_ratio * 100:.1f}% oranında kaymış (limit "
                f"{max_plan_eccentricity_ratio * 100:.1f}%) — plan asimetrisi/"
                "çarpıklığı olası, TBDY A1 (burulma düzensizliği) GEOMETRİK "
                "GÖSTERGESİdir, rijitlik merkezi hesabı yerine geçmez."
            ),
            value=eccentricity_ratio, limit=max_plan_eccentricity_ratio,
        ))

    # -- 6) Kat yüksekliği sıçraması — TBDY B2 (yumuşak kat) GEOMETRİK
    #       PROXY'si. Bir katın komşularına oranla anormal yüksek olması
    #       (örn. yüksek tavanlı zemin kat/lobi) dıştan gözlemlenebilen bir
    #       yumuşak-kat kırmızı bayrağıdır (bkz. modül docstring'i).
    floors_sorted = sorted(building.floors, key=lambda f: f.level)
    for idx, floor in enumerate(floors_sorted):
        neighbor_heights = [
            floors_sorted[j].height_m
            for j in (idx - 1, idx + 1)
            if 0 <= j < len(floors_sorted)
        ]
        if not neighbor_heights:
            continue
        avg_neighbor = sum(neighbor_heights) / len(neighbor_heights)
        if avg_neighbor <= 0:
            continue
        ratio = floor.height_m / avg_neighbor
        if ratio > soft_story_height_ratio:
            issues.append(StructuralIssue(
                code="soft_story_height_proxy",
                severity=IssueSeverity.WARNING,
                message=(
                    f"Kat {floor.level}: yükseklik ({floor.height_m:.2f} m) komşu "
                    f"katların ortalamasının ({avg_neighbor:.2f} m) {ratio:.2f} "
                    f"katı (limit {soft_story_height_ratio:.2f}x) — olası yumuşak "
                    "kat (TBDY B2) GEOMETRİK GÖSTERGESİ, gerçek rijitlik oranı "
                    "hesabı değildir; sahada doğrulanmalıdır."
                ),
                value=ratio, limit=soft_story_height_ratio,
            ))

    is_plausible = not any(i.severity == IssueSeverity.CRITICAL for i in issues)
    return StructuralValidationReport(
        is_plausible=is_plausible,
        slenderness_ratio=slenderness,
        max_cantilever_m=max_cantilever_found,
        issues=issues,
    )
