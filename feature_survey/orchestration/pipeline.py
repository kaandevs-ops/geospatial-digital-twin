"""FAZ S6 — Saha → Dijital İkiz Uçtan Uca Entegrasyon (`feature_survey/orchestration/`).

Roadmap ROADMAP_V6.md S6: "S1-S5'i tek bir 'Saha Ölçüm Projesi' akışında
birleştiren üst seviye orkestrasyon ... Web arayüzü: saha projesi yükleme,
adım adım işlem geçmişi (audit trail — hangi ham veriden hangi sonucun
türetildiği izlenebilir olmalı, mühendislik sorumluluğu gereği)."

Bu modül, FAZ S1 (ham veri — çağıran taraf tarafından zaten `FieldSurveySession`'a
işlenmiş halde verilir), S2 (jeodezik hesap — `ControlPointComparison`/
`AngularClosure`/`LinearClosure` olarak, yine çağıran taraftan), S3 (nokta
bulutu, opsiyonel), S4 (vektörleştirme) ve S5 (QC raporu)'i **tek bir
fonksiyon çağrısında** birleştirir ve her adımı **denetlenebilir bir audit
trail**'e (hangi roadmap fazının, hangi girdiden, hangi çıktıyı ürettiği)
kaydeder.

Tasarım kararı (mimariyi koruma ilkesiyle tutarlı): bu modül S1-S5'in
kendi hesaplama mantığını **tekrar etmez** — sadece zaten var olan, test
edilmiş fonksiyonları (`build_linework`, `build_survey_quality_report`,
`progressive_morphological_filter`, `compare_pointcloud_to_mesh`, ...)
doğru sırayla çağırıp sonuçları tek bir `SurveyOrchestrationResult`'ta
toplar. `persistence/project_manager.py` ile tam entegrasyon (proje
formatına kayıt) — roadmap'in "sonraki iterasyon" notuyla tutarlı olarak
— bu modülün `to_project_manifest_payload()` yardımcı metoduyla
hazırlanmış, JSON-serileştirilebilir bir sözlük olarak sağlanır; bu
sözlüğün `persistence.project_format.ProjectManifest`'e yazılması, mevcut
proje formatının şemasına dokunmadan ayrı bir entegrasyon adımıdır (bkz.
`README.md` "Sonraki adım" notu).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..codes import FeatureCode
from ..field_point import FieldSurveySession
from ..geodetic_engine.gnss_adjustment import ControlPointComparison
from ..geodetic_engine.traverse import AngularClosure, LinearClosure
from ..pointcloud_engine.ground_classification import (
    GroundClassificationResult,
    progressive_morphological_filter,
)
from ..pointcloud_engine.quality_report import PointCloudQualityReport, build_quality_report
from ..qc.checkpoint_report import CheckpointAccuracyReport, generate_checkpoint_report
from ..qc.closure_report import ClosureQcReport, generate_closure_report
from ..qc.pointcloud_mesh_comparison import SurfaceComparisonReport, compare_pointcloud_to_mesh
from ..qc.survey_quality_report import SurveyQualityReport, build_survey_quality_report
from ..qc.technical_report import TechnicalReportMeta, build_report_lines
from ..vectorization.linework import VectorFeature, build_linework


class OrchestrationError(ValueError):
    """Orkestrasyon için yetersiz/tutarsız girdi (örn. hiçbir fazın
    çalıştırılamayacağı boş bir istek) durumunda fırlatılır."""


@dataclass(slots=True)
class AuditEntry:
    """Tek bir orkestrasyon adımının denetlenebilir kaydı — "hangi ham
    veriden hangi sonucun türetildiği" sorusunun doğrudan cevabı."""

    phase: str  # örn. "S4", "S5-checkpoint"
    description: str
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )
    input_summary: str = ""
    output_summary: str = ""


@dataclass(slots=True)
class SurveyOrchestrationResult:
    project_name: str
    audit_trail: list[AuditEntry] = field(default_factory=list)
    linework: list[VectorFeature] | None = None
    quality_report: SurveyQualityReport | None = None
    ground_classification: GroundClassificationResult | None = None
    pointcloud_quality: PointCloudQualityReport | None = None
    surface_comparison: SurfaceComparisonReport | None = None

    def _log(self, phase: str, description: str, input_summary: str, output_summary: str) -> None:
        self.audit_trail.append(
            AuditEntry(
                phase=phase,
                description=description,
                input_summary=input_summary,
                output_summary=output_summary,
            )
        )

    def to_project_manifest_payload(self) -> dict:
        """`persistence.project_format.ProjectManifest`'e (veya benzeri bir
        proje kaydına) beslenebilecek, JSON-serileştirilebilir özet.
        Mevcut proje formatı şemasına dokunulmaz — bu, çağıran koda
        bırakılan ayrı bir entegrasyon adımıdır (bkz. modül docstring'i).
        """
        payload: dict = {
            "project_name": self.project_name,
            "audit_trail": [
                {
                    "phase": e.phase,
                    "description": e.description,
                    "timestamp": e.timestamp,
                    "input_summary": e.input_summary,
                    "output_summary": e.output_summary,
                }
                for e in self.audit_trail
            ],
        }
        if self.linework is not None:
            payload["linework_feature_count"] = len(self.linework)
        if self.quality_report is not None:
            payload["qc_overall_passed"] = self.quality_report.overall_passed
            payload["qc_failure_summary"] = self.quality_report.failure_summary
        if self.ground_classification is not None:
            payload["ground_classification"] = {
                "n_ground": self.ground_classification.n_ground,
                "n_nonground": self.ground_classification.n_nonground,
                "ground_ratio": self.ground_classification.ground_ratio,
            }
        if self.pointcloud_quality is not None:
            pq = self.pointcloud_quality
            payload["pointcloud_quality"] = {
                "density_points_per_m2": pq.density.density_points_per_m2,
                "gap_ratio": pq.gaps.gap_ratio,
                "n_outliers": pq.noise.n_outliers,
            }
        if self.surface_comparison is not None:
            payload["surface_comparison"] = {
                "rms_distance_m": self.surface_comparison.rms_distance_m,
                "hausdorff_distance_m": self.surface_comparison.hausdorff_distance_m,
            }
        return payload


def run_field_survey_pipeline(
    project_name: str,
    session: FieldSurveySession | None = None,
    checkpoint_comparisons: list[ControlPointComparison] | None = None,
    checkpoint_tolerance_horizontal_m: float | None = None,
    checkpoint_tolerance_vertical_m: float | None = None,
    checkpoint_standard_reference: str | None = None,
    angular_closure: AngularClosure | None = None,
    linear_closure: LinearClosure | None = None,
    angular_tolerance_gon: float | None = None,
    max_relative_precision: float | None = None,
    closure_standard_reference: str | None = None,
    pointcloud_points: list[tuple[float, float, float]] | None = None,
    reference_mesh=None,
) -> SurveyOrchestrationResult:
    """S1 (girdi olarak zaten işlenmiş `session`) -> S2 (girdi olarak zaten
    hesaplanmış `checkpoint_comparisons`/`angular_closure`/`linear_closure`)
    -> S3 (varsa `pointcloud_points`) -> S4 (vektörleştirme) -> S5 (QC
    raporu + varsa yüzey karşılaştırması) sırasıyla çalıştırır.

    Her faz **opsiyoneldir** (bir saha projesinde her zaman hepsi
    olmayabilir — örn. sadece GNSS ile çalışılan bir projede poligon
    kapatması yoktur, roadmap'in kendi `survey_quality_report.py`
    tasarım ilkesiyle tutarlı). En az bir faz için girdi verilmelidir;
    aksi halde `OrchestrationError` fırlatılır (sessizce boş bir sonuç
    döndürülmez).
    """
    if not project_name or not project_name.strip():
        raise OrchestrationError("`project_name` boş olamaz.")

    has_any_input = any(
        [
            session is not None,
            checkpoint_comparisons is not None,
            angular_closure is not None,
            pointcloud_points is not None,
        ]
    )
    if not has_any_input:
        raise OrchestrationError(
            "Orkestrasyon için en az bir faza ait girdi (session, checkpoint_comparisons, "
            "angular_closure/linear_closure veya pointcloud_points) gerekir."
        )

    result = SurveyOrchestrationResult(project_name=project_name)

    # --- FAZ S4: vektörleştirme (S1'in çıktısı olan session'dan) --------
    if session is not None:
        features = build_linework(session)
        result.linework = features
        result._log(
            phase="S4",
            description="Kod-tabanlı otomatik vektörleştirme (linework)",
            input_summary=f"FieldSurveySession '{session.name}' ({len(session.points)} nokta)",
            output_summary=f"{len(features)} vektör öğesi (Point/LineString/Polygon)",
        )

    # --- FAZ S5: kontrol noktası + poligon kapatma raporu ---------------
    checkpoint_report: CheckpointAccuracyReport | None = None
    if checkpoint_comparisons is not None:
        if checkpoint_tolerance_horizontal_m is None or checkpoint_tolerance_vertical_m is None or not checkpoint_standard_reference:
            raise OrchestrationError(
                "`checkpoint_comparisons` verildiğinde tolerans değerleri ve "
                "`checkpoint_standard_reference` de verilmelidir (S5 uydurma tolerans kabul etmez)."
            )
        checkpoint_report = generate_checkpoint_report(
            checkpoint_comparisons,
            checkpoint_tolerance_horizontal_m,
            checkpoint_tolerance_vertical_m,
            checkpoint_standard_reference,
        )
        result._log(
            phase="S5-checkpoint",
            description="Kontrol noktası doğruluk raporu (S2 GNSS/traverse çıktısından)",
            input_summary=f"{len(checkpoint_comparisons)} kontrol noktası karşılaştırması",
            output_summary=(
                f"all_passed={checkpoint_report.all_passed}, "
                f"n_failed={checkpoint_report.n_failed}"
            ),
        )

    closure_report: ClosureQcReport | None = None
    if angular_closure is not None or linear_closure is not None:
        if angular_closure is None or linear_closure is None:
            raise OrchestrationError(
                "Poligon kapatma raporu için hem `angular_closure` hem `linear_closure` gerekir."
            )
        if angular_tolerance_gon is None or max_relative_precision is None or not closure_standard_reference:
            raise OrchestrationError(
                "`angular_closure`/`linear_closure` verildiğinde tolerans değerleri ve "
                "`closure_standard_reference` de verilmelidir."
            )
        closure_report = generate_closure_report(
            angular_closure,
            linear_closure,
            angular_tolerance_gon,
            max_relative_precision,
            closure_standard_reference,
        )
        result._log(
            phase="S5-closure",
            description="Poligon kapatma raporu (S2 traverse çıktısından)",
            input_summary="AngularClosure + LinearClosure (S2.2)",
            output_summary=f"all_passed={closure_report.all_passed}",
        )

    if checkpoint_report is not None or closure_report is not None:
        quality_report = build_survey_quality_report(
            checkpoint=checkpoint_report, closure=closure_report
        )
        result.quality_report = quality_report
        result._log(
            phase="S5-combined",
            description="Birleşik saha kalite raporu (checkpoint + closure)",
            input_summary="S5-checkpoint ve/veya S5-closure çıktısı",
            output_summary=f"overall_passed={quality_report.overall_passed}",
        )

    # --- FAZ S3: nokta bulutu (zemin sınıflandırma + kalite raporu) -----
    if pointcloud_points is not None:
        ground = progressive_morphological_filter(pointcloud_points)
        result.ground_classification = ground
        result._log(
            phase="S3-ground",
            description="Zemin/zemin-dışı sınıflandırma (Progressive Morphological Filter)",
            input_summary=f"{len(pointcloud_points)} ham LiDAR/nokta bulutu noktası",
            output_summary=(
                f"n_ground={ground.n_ground}, n_nonground={ground.n_nonground}, "
                f"ground_ratio={ground.ground_ratio:.3f}"
            ),
        )

        pq = build_quality_report(pointcloud_points)
        result.pointcloud_quality = pq
        result._log(
            phase="S3-quality",
            description="Nokta bulutu yoğunluk/gap/gürültü raporu",
            input_summary=f"{len(pointcloud_points)} nokta",
            output_summary=(
                f"density={pq.density.density_points_per_m2:.3f} nokta/m², "
                f"gap_ratio={pq.gaps.gap_ratio:.3f}, n_outliers={pq.noise.n_outliers}"
            ),
        )

        # --- FAZ S5 (S3'e bağımlı parça): nokta bulutu-mesh karşılaştırması
        if reference_mesh is not None:
            comparison = compare_pointcloud_to_mesh(pointcloud_points, reference_mesh)
            result.surface_comparison = comparison
            result._log(
                phase="S5-surface-comparison",
                description="Nokta bulutu-mesh karşılaştırması (RMS/Hausdorff)",
                input_summary=f"{len(pointcloud_points)} LiDAR noktası vs. mesh '{reference_mesh.name}'",
                output_summary=(
                    f"rms={comparison.rms_distance_m:.4f}m, "
                    f"hausdorff={comparison.hausdorff_distance_m:.4f}m"
                ),
            )

    return result
