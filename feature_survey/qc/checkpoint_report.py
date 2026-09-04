"""FAZ S5 — Kontrol noktası doğruluk raporu.

`geodetic_engine.gnss_adjustment` içindeki gerçek `ControlPointComparison`/
`RmseReport` hesaplarının üzerine, roadmap S5'in "kabul kriteri" ilkesini
(negatif test senaryosu zorunlu — modül her zaman "başarılı" dememeli)
doğrudan uygulayan katman: her kontrol noktası, verilen tolerans değeriyle
karşılaştırılır ve **gerçekten** geçer/kalır (pass/fail), sabit "OK" yanıtı
üretilmez.

Tolerans değeri **bu modül tarafından uydurulmaz** — çağıran kod, projeye
uygulanan resmi standardı (örn. ASPRS Positional Accuracy Standards, FGDC-
STD-001-1998/NSSDA, veya ulusal harita mühendisliği yönetmeliği) `standard_reference`
alanıyla birlikte açıkça belirtmek zorundadır (`InsufficientDataError`:
tolerans veya kaynak belirtilmezse rapor üretilmez).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..geodetic_engine.gnss_adjustment import (
    ControlPointComparison,
    InsufficientDataError,
    RmseReport,
    rmse_from_differences,
)


@dataclass(slots=True)
class PointAccuracyResult:
    index: int
    delta_easting_m: float
    delta_northing_m: float
    delta_elevation_m: float
    horizontal_error_m: float  # hypot(dE, dN) — gerçek 2B hata, tolerans ile karşılaştırma tabanı
    vertical_error_m: float  # |dZ|
    horizontal_pass: bool
    vertical_pass: bool

    @property
    def passed(self) -> bool:
        return self.horizontal_pass and self.vertical_pass


@dataclass(slots=True)
class CheckpointAccuracyReport:
    rmse: RmseReport
    tolerance_horizontal_m: float
    tolerance_vertical_m: float
    standard_reference: str
    per_point: list[PointAccuracyResult] = field(default_factory=list)

    @property
    def n_failed(self) -> int:
        return sum(1 for p in self.per_point if not p.passed)

    @property
    def all_passed(self) -> bool:
        """Tüm kontrol noktaları tolerans içinde mi? Bu, sabit `True` değildir
        — kasıtlı olarak toleransı aşan bir test veri setinde `False`
        dönmesi roadmap S5 kabul kriteridir (bkz. `tests/test_phaseS5_qc.py`
        içindeki negatif test senaryosu)."""
        return self.n_failed == 0


def generate_checkpoint_report(
    comparisons: list[ControlPointComparison],
    tolerance_horizontal_m: float,
    tolerance_vertical_m: float,
    standard_reference: str,
) -> CheckpointAccuracyReport:
    """Kontrol noktası karşılaştırmalarından gerçek RMSE + nokta-nokta
    pass/fail raporu üretir.

    `tolerance_horizontal_m`/`tolerance_vertical_m`: pozitif olmalı ve
    çağıran kod tarafından uygulanan gerçek standarttan gelmelidir (bu
    fonksiyon kendi eşiğini uydurmaz). `standard_reference`: bu toleransın
    hangi standart/yönetmelikten geldiğini belgeleyen boş olmayan bir metin
    (örn. "ASPRS 2014, Class II, 5cm RMSEx/RMSEy eşdeğeri" veya
    "TUJJB Büyük Ölçekli Harita Yapım Yönetmeliği, detay noktası toleransı").
    """

    if tolerance_horizontal_m <= 0 or tolerance_vertical_m <= 0:
        raise InsufficientDataError(
            "Yatay/düşey tolerans değerleri pozitif olmalı — bu modül kendi "
            "eşiğini uydurmaz, gerçek uygulanan standarttan gelen bir değer bekler."
        )
    if not standard_reference or not standard_reference.strip():
        raise InsufficientDataError(
            "`standard_reference` boş olamaz — hangi standarda göre tolerans "
            "uygulandığı denetlenebilirlik için belgelenmelidir."
        )
    if not comparisons:
        raise InsufficientDataError("Rapor için en az bir kontrol noktası karşılaştırması gerekir.")

    rmse = rmse_from_differences(comparisons)

    per_point: list[PointAccuracyResult] = []
    for i, c in enumerate(comparisons):
        horizontal_error = c.rmse_2d_m  # tek nokta için bu zaten |fark| (hypot(dE,dN))
        vertical_error = abs(c.delta_elevation_m)
        per_point.append(
            PointAccuracyResult(
                index=i,
                delta_easting_m=c.delta_easting_m,
                delta_northing_m=c.delta_northing_m,
                delta_elevation_m=c.delta_elevation_m,
                horizontal_error_m=horizontal_error,
                vertical_error_m=vertical_error,
                horizontal_pass=horizontal_error <= tolerance_horizontal_m,
                vertical_pass=vertical_error <= tolerance_vertical_m,
            )
        )

    return CheckpointAccuracyReport(
        rmse=rmse,
        tolerance_horizontal_m=tolerance_horizontal_m,
        tolerance_vertical_m=tolerance_vertical_m,
        standard_reference=standard_reference,
        per_point=per_point,
    )
