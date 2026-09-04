"""FAZ S5 — Poligon/QC birleşik rapor şablonu.

Roadmap S5'in "Poligon/QC birleşik rapor şablonu" maddesi: `closure_report`
(S2.2 poligon kapatma) ve `checkpoint_report` (S2.3 kontrol noktası
karşılaştırması) çıktısını, ikisi de mevcutsa (bir saha projesinde ikisi de
olmayabilir — örn. sadece GNSS ile çalışılan bir projede poligon kapatması
yoktur) tek bir üst-seviye rapora birleştirir. Bu modül **kendi başına**
hiçbir yeni hesaplama yapmaz — sadece iki alt raporun `all_passed` /
başarısızlık bilgisini toplu bir görünümde sunar (export/reports.py'nin
teknik rapor şablonuna doğrudan beslenebilir bir yapı).
"""

from __future__ import annotations

from dataclasses import dataclass

from .checkpoint_report import CheckpointAccuracyReport
from .closure_report import ClosureQcReport


@dataclass(slots=True)
class SurveyQualityReport:
    checkpoint: CheckpointAccuracyReport | None
    closure: ClosureQcReport | None

    @property
    def overall_passed(self) -> bool:
        """Mevcut olan her iki alt-raporun (varsa) `all_passed` sonucunun
        mantıksal VE'si. Hiçbiri sağlanmamışsa (ikisi de None) rapor
        anlamsız olur — bu durum `build_survey_quality_report` içinde
        `InsufficientDataError` ile engellenir, burada oluşmaz."""
        results = []
        if self.checkpoint is not None:
            results.append(self.checkpoint.all_passed)
        if self.closure is not None:
            results.append(self.closure.all_passed)
        return all(results)

    @property
    def failure_summary(self) -> list[str]:
        """İnsan-okunur, denetlenebilir başarısızlık özeti (boş liste = her
        şey toleransta). Sessizce "her şey yolunda" demek yerine, hangi
        alt-raporun/noktanın neden kaldığını açıkça listeler."""
        summary: list[str] = []
        if self.checkpoint is not None and not self.checkpoint.all_passed:
            for p in self.checkpoint.per_point:
                if not p.passed:
                    summary.append(
                        f"Kontrol noktası #{p.index}: yatay hata={p.horizontal_error_m:.4f}m "
                        f"(tolerans={self.checkpoint.tolerance_horizontal_m:.4f}m, "
                        f"geçti={p.horizontal_pass}); düşey hata={p.vertical_error_m:.4f}m "
                        f"(tolerans={self.checkpoint.tolerance_vertical_m:.4f}m, geçti={p.vertical_pass})"
                    )
        if self.closure is not None and not self.closure.all_passed:
            if not self.closure.angular_pass:
                summary.append(
                    f"Açısal kapatma hatası {self.closure.angular_closure.closure_error_gon:.4f} gon, "
                    f"tolerans {self.closure.angular_tolerance_gon:.4f} gon aşıldı."
                )
            if not self.closure.linear_pass:
                summary.append(
                    f"Bağıl hassasiyet {self.closure.linear_closure.relative_precision:.6f}, "
                    f"üst sınır {self.closure.max_relative_precision:.6f} aşıldı."
                )
        return summary


class InsufficientDataError(ValueError):
    """Birleşik rapor için hem `checkpoint` hem `closure` None olduğunda
    (raporlanacak hiçbir şey yok) fırlatılır."""


def build_survey_quality_report(
    checkpoint: CheckpointAccuracyReport | None,
    closure: ClosureQcReport | None,
) -> SurveyQualityReport:
    if checkpoint is None and closure is None:
        raise InsufficientDataError(
            "Birleşik rapor için en az bir alt-rapor (checkpoint veya closure) gerekir."
        )
    return SurveyQualityReport(checkpoint=checkpoint, closure=closure)
