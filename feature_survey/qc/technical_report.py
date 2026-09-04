"""FAZ S5 — Ölçüm teknik raporu şablonu (PDF/JSON çıktısı).

Roadmap ROADMAP_V6.md S5'in son eksik maddesi: `SurveyQualityReport`
(`survey_quality_report.py`) içindeki **gerçekten hesaplanmış** RMSE/kapatma
değerlerini, `export/reports.py` (JSON) ve `export/vector_2d.PDFExporter`
(PDF) üzerine inşa ederek somut bir dosyaya (Türkiye harita mühendisliği
pratiğindeki ölçü teknik raporu formatına yakın: proje başlığı, kontrol
noktası tablosu, poligon kapatma özeti, genel sonuç) yazar.

Tasarım ilkesi (README'deki ile birebir tutarlı): bu modül **hiçbir yeni
sayı üretmez** — sadece `SurveyQualityReport`'ta zaten var olan gerçek
değerleri biçimlendirir. `overall_passed` sabit `True` değildir; rapor
metninde açıkça "BAŞARISIZ" ibaresi ve `failure_summary` satırları yer
alır (negatif senaryo burada da sessizce gizlenmez).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..field_point import FieldSurveySession
from .survey_quality_report import SurveyQualityReport


class InsufficientDataError(ValueError):
    """Rapor üretmek için proje adı boşsa veya `report` sağlanmamışsa."""


@dataclass(slots=True)
class TechnicalReportMeta:
    """Rapor üst bilgisi — hiçbiri varsayılan/uydurma değildir, çağıran
    taraf projeye ait gerçek bilgiyi geçirmek zorundadır."""

    project_name: str
    surveyor: str
    crs: str
    standard_reference: str
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )


def _fmt(value: float, digits: int = 4) -> str:
    return f"{value:.{digits}f}"


def build_report_lines(
    meta: TechnicalReportMeta,
    report: SurveyQualityReport,
    session: FieldSurveySession | None = None,
) -> list[str]:
    """`SurveyQualityReport` + (opsiyonel) `FieldSurveySession` özetinden
    insan-okunur rapor satırları üretir. PDF/print/markdown çıktısı bu
    satırlardan türetilir — tek bir doğruluk kaynağı (single source of
    truth), format başına ayrı mantık tekrarlanmaz."""

    if not meta.project_name.strip():
        raise InsufficientDataError("Rapor için proje adı (project_name) zorunludur.")

    lines: list[str] = []
    lines.append(f"Proje: {meta.project_name}")
    lines.append(f"Ölçümü Yapan: {meta.surveyor}")
    lines.append(f"Koordinat Sistemi: {meta.crs}")
    lines.append(f"Referans Standart: {meta.standard_reference}")
    lines.append(f"Üretim Zamanı (UTC): {meta.generated_at}")
    lines.append("")
    lines.append("=== GENEL SONUÇ ===")
    lines.append("SONUÇ: BAŞARILI" if report.overall_passed else "SONUÇ: BAŞARISIZ")
    lines.append("")

    if report.checkpoint is not None:
        cp = report.checkpoint
        lines.append("=== KONTROL NOKTASI DOĞRULUK RAPORU ===")
        lines.append(f"Standart: {cp.standard_reference}")
        lines.append(
            f"Tolerans (yatay/düşey): {_fmt(cp.tolerance_horizontal_m)} m / "
            f"{_fmt(cp.tolerance_vertical_m)} m"
        )
        lines.append(
            f"RMSE (E/N/Z): {_fmt(cp.rmse.rmse_easting_m)} / "
            f"{_fmt(cp.rmse.rmse_northing_m)} / {_fmt(cp.rmse.rmse_elevation_m)} m "
            f"(2D={_fmt(cp.rmse.rmse_2d_m)}, %95 güven yarıçapı="
            f"{_fmt(cp.rmse.confidence_95_radius_m)} m)"
        )
        lines.append(f"Toplam nokta: {len(cp.per_point)}, Başarısız: {cp.n_failed}")
        for p in cp.per_point:
            status = "GEÇTİ" if p.passed else "KALDI"
            lines.append(
                f"  #{p.index}: dE={_fmt(p.delta_easting_m)} dN={_fmt(p.delta_northing_m)} "
                f"dZ={_fmt(p.delta_elevation_m)} | yatay={_fmt(p.horizontal_error_m)}m "
                f"düşey={_fmt(p.vertical_error_m)}m -> {status}"
            )
        lines.append("")

    if report.closure is not None:
        cl = report.closure
        lines.append("=== POLİGON KAPATMA RAPORU ===")
        lines.append(f"Standart: {cl.standard_reference}")
        lines.append(
            f"Açısal kapatma hatası: {_fmt(cl.angular_closure.closure_error_gon)} gon "
            f"(tolerans={_fmt(cl.angular_tolerance_gon)} gon) -> "
            f"{'GEÇTİ' if cl.angular_pass else 'KALDI'}"
        )
        lines.append(
            f"Bağıl hassasiyet: {cl.linear_closure.relative_precision:.6f} "
            f"(üst sınır={cl.max_relative_precision:.6f}) -> "
            f"{'GEÇTİ' if cl.linear_pass else 'KALDI'}"
        )
        lines.append("")

    if report.failure_summary:
        lines.append("=== BAŞARISIZLIK ÖZETİ ===")
        lines.extend(f"- {s}" for s in report.failure_summary)
        lines.append("")

    if session is not None:
        lines.append("=== SAHA ÖLÇÜM ÖZETİ ===")
        lines.append(f"Oturum: {session.name} (CRS={session.crs})")
        lines.append(f"Toplam nokta: {len(session.points)}")
        for code_value, count in session.summary().items():
            lines.append(f"  {code_value}: {count} nokta")
        lines.append("")

    return lines


def export_technical_report_pdf(
    meta: TechnicalReportMeta,
    report: SurveyQualityReport,
    path: str,
    session: FieldSurveySession | None = None,
):
    """PDF çıktısı — `export/vector_2d.PDFExporter` üzerinden (reportlab
    varsa zengin biçimli, yoksa stdlib-only minimal PDF; ikisi de gerçek
    bir dosya üretir, format seçimi çağırana görünmez)."""
    from ...export.vector_2d import PDFExporter

    lines = build_report_lines(meta, report, session)
    title = f"Saha Ölçüm Teknik Raporu — {meta.project_name}"
    return PDFExporter.export_text_report(title, lines, path)


def export_technical_report_json(
    meta: TechnicalReportMeta,
    report: SurveyQualityReport,
    path: str,
    session: FieldSurveySession | None = None,
):
    """JSON çıktısı — makine tarafından okunabilir eşdeğer (audit trail /
    S6 orkestrasyonunun ileride tüketebileceği yapılandırılmış biçim)."""
    from ...export.reports import JSONReportExporter

    data: dict = {
        "project_name": meta.project_name,
        "surveyor": meta.surveyor,
        "crs": meta.crs,
        "standard_reference": meta.standard_reference,
        "generated_at": meta.generated_at,
        "overall_passed": report.overall_passed,
        "failure_summary": report.failure_summary,
    }
    if report.checkpoint is not None:
        cp = report.checkpoint
        data["checkpoint"] = {
            "standard_reference": cp.standard_reference,
            "tolerance_horizontal_m": cp.tolerance_horizontal_m,
            "tolerance_vertical_m": cp.tolerance_vertical_m,
            "rmse_easting_m": cp.rmse.rmse_easting_m,
            "rmse_northing_m": cp.rmse.rmse_northing_m,
            "rmse_elevation_m": cp.rmse.rmse_elevation_m,
            "rmse_2d_m": cp.rmse.rmse_2d_m,
            "rmse_3d_m": cp.rmse.rmse_3d_m,
            "confidence_95_radius_m": cp.rmse.confidence_95_radius_m,
            "n_failed": cp.n_failed,
            "all_passed": cp.all_passed,
            "points": [
                {
                    "index": p.index,
                    "delta_easting_m": p.delta_easting_m,
                    "delta_northing_m": p.delta_northing_m,
                    "delta_elevation_m": p.delta_elevation_m,
                    "horizontal_error_m": p.horizontal_error_m,
                    "vertical_error_m": p.vertical_error_m,
                    "passed": p.passed,
                }
                for p in cp.per_point
            ],
        }
    if report.closure is not None:
        cl = report.closure
        data["closure"] = {
            "standard_reference": cl.standard_reference,
            "angular_closure_error_gon": cl.angular_closure.closure_error_gon,
            "angular_tolerance_gon": cl.angular_tolerance_gon,
            "angular_pass": cl.angular_pass,
            "relative_precision": cl.linear_closure.relative_precision,
            "max_relative_precision": cl.max_relative_precision,
            "linear_pass": cl.linear_pass,
            "all_passed": cl.all_passed,
        }
    if session is not None:
        data["session"] = {
            "name": session.name,
            "crs": session.crs,
            "point_count": len(session.points),
            "summary": session.summary(),
        }

    return JSONReportExporter.export(data, path)
