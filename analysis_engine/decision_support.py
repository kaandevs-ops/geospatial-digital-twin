"""
Karar Destek Motoru — ROADMAP_V9 Faz XI / Katman 9 (nihai teslimat)
========================================================================

Roadmap metni (Katman 9): "Bu katman, alttaki 8 katmanın neden var
olduğunun cevabıdır — sadece 'güzel bir simülasyon' değil, 'karar almaya
yardımcı olan bir araç'."

Bu modül **yeni bir simülasyon motoru yazmaz** (roadmap ilkesi #2) — var
olan `EvacuationSimulator`/`CapacityAnalyzer`/`FireEvacuationComparator`/
`indoor_navigation.BuildingNavGraph`/`behavior_rules.
close_exit_and_seek_alternative`'i tekrar tekrar çağırıp sonuçları
karşılaştıran, önceliklendiren ve doğal dile çeviren ince bir üst katman
sağlar. Kapsanan maddeler:

1. Senaryo karşılaştırma paneli (before/after dahil)               -> `compare_evacuation_results`
2. Otomatik öneri motoru (kural-tabanlı, temkinli)                  -> `RecommendationEngine`
3. Duyarlılık analizi (sensitivity analysis)                        -> `SensitivityAnalyzer`
5. Gerçek zamanlı "ne olur" (what-if) modu                          -> `what_if_close_exit_and_rerun`
6. Erişilebilirlik uyarı motoru                                     -> `AccessibilityWarningEngine`

(Madde 4 — rapor anlatıcı entegrasyonu — `analysis_engine/
result_narrator.py`'ye eklendi; madde 7 — video/GIF export —
`render_engine/animation_export.py`'de ayrı bir modülde.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..building_reconstruction.regulations import RegulationProfile
from ..mobility.crowd_simulation import Agent, EvacuationResult, EvacuationSimulator, SocialForceModel
from ..mobility.crowd_simulation.behavior_rules import close_exit_and_seek_alternative
from ..mobility.crowd_simulation.capacity_analysis import CapacityAnalysisReport
from ..mobility.indoor_navigation import BuildingNavGraph
from ..mobility.pathfinding import NavGraph

#: Gösterge disiplini (roadmap ilke #1) — tüm karar-destek çıktılarında
#: birebir korunması gereken uyarı metni.
INDICATIVE_DISCLAIMER = (
    "Bu rapor gösterge niteliğindedir; kesin bir mühendislik değerlendirmesinin "
    "yerini tutmaz. Öneriler kural-tabanlıdır, kesin doğru cevap iddiası taşımaz."
)


# ========================================================================== #
# 1) Senaryo karşılaştırma paneli (before/after)
# ========================================================================== #

@dataclass(slots=True)
class ScenarioComparison:
    """"Mevcut durum" vs "Önerilen değişiklik" — roadmap'in "her katmandan
    tek bir karşılaştırmalı rapor" notunun tahliye sonucu için karşılığı.
    Diğer katmanlar (enerji talebi, ısı adası indeksi vb.) aynı desende
    (`metric_name`, `before_value`, `after_value`) genişletilebilir —
    burada tahliye/kapasite metriklerine odaklanılmıştır (en olgun motor)."""

    label: str
    metric_name: str
    before_value: float
    after_value: float

    @property
    def delta(self) -> float:
        return self.after_value - self.before_value

    @property
    def pct_change(self) -> Optional[float]:
        if self.before_value == 0:
            return None
        return (self.delta / self.before_value) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "metric_name": self.metric_name,
            "before_value": round(self.before_value, 3),
            "after_value": round(self.after_value, 3),
            "delta": round(self.delta, 3),
            "pct_change": round(self.pct_change, 2) if self.pct_change is not None else None,
        }


def compare_evacuation_results(
    before: EvacuationResult, after: EvacuationResult, *, label: str = "tahliye senaryosu",
) -> list[ScenarioComparison]:
    """İki `EvacuationResult`'ı (örn. bir dalın önce/sonra koşumları,
    `persistence.project_manager.create_branch` ile üretilmiş) karşılaştırır.

    Roadmap'in "before/after karşılaştırma ... mimarlar için asıl 'wow'
    özelliği" notu — yeni bir sonuç tipi icat edilmedi, mevcut
    `EvacuationResult` alanları karşılaştırılır.
    """
    comparisons = [
        ScenarioComparison(
            label=label, metric_name="evacuation_time_s",
            before_value=before.evacuation_time_s, after_value=after.evacuation_time_s,
        ),
        ScenarioComparison(
            label=label, metric_name="evacuated_count",
            before_value=float(before.evacuated_count), after_value=float(after.evacuated_count),
        ),
    ]
    if before.bottleneck_peak_count is not None and after.bottleneck_peak_count is not None:
        comparisons.append(
            ScenarioComparison(
                label=label, metric_name="bottleneck_peak_count",
                before_value=float(before.bottleneck_peak_count),
                after_value=float(after.bottleneck_peak_count),
            )
        )
    return comparisons


def compare_capacity_reports(
    before: CapacityAnalysisReport, after: CapacityAnalysisReport, *, label: str = "kapasite analizi",
) -> list[ScenarioComparison]:
    """Aynı `agent_count` değerlerine sahip iki kapasite raporunu (batch
    koşum, Faz III) satır satır karşılaştırır — yalnızca her iki raporda
    da ortak olan `agent_count` değerleri eşleştirilir."""
    before_by_count = {r.agent_count: r for r in before.runs}
    after_by_count = {r.agent_count: r for r in after.runs}
    comparisons = []
    for count in sorted(set(before_by_count) & set(after_by_count)):
        b, a = before_by_count[count], after_by_count[count]
        comparisons.append(
            ScenarioComparison(
                label=f"{label} ({count} kişi)", metric_name="evacuation_time_s",
                before_value=b.evacuation_time_s, after_value=a.evacuation_time_s,
            )
        )
    return comparisons


# ========================================================================== #
# 2) Otomatik öneri motoru
# ========================================================================== #

@dataclass(slots=True)
class Recommendation:
    issue: str
    suggestions: list[str]
    severity: str  # "info" | "warning" | "critical"


class RecommendationEngine:
    """Roadmap: "Bu binanın tahliye süresi yönetmelik eşiğinin üstünde,
    olası çözümler: [ek çıkış / merdiven genişletme / doluluk azaltma]" —
    kural-tabanlı, kesin-doğru-cevap iddiası taşımayan bir yardımcı.

    Yeni bir öneri "motoru"/ML modeli yazılmadı — açık, okunabilir bir
    kural listesi (roadmap'in kendi disiplini: kara kutu değil).
    """

    @staticmethod
    def from_capacity_report(report: CapacityAnalysisReport) -> list[Recommendation]:
        recommendations: list[Recommendation] = []
        for run in report.runs:
            if run.within_threshold is False:
                recommendations.append(
                    Recommendation(
                        issue=(
                            f"{run.agent_count} kişilik senaryoda tahliye süresi "
                            f"({run.evacuation_time_s:.1f}s) eşik değerini "
                            f"({run.threshold_s}s) aşıyor."
                        ),
                        suggestions=[
                            "Ek çıkış eklenmesi değerlendirilebilir.",
                            "Mevcut çıkış genişliği artırılabilir.",
                            "Bu doluluk seviyesinde bina kullanım yoğunluğu azaltılabilir.",
                        ],
                        severity="critical",
                    )
                )
            elif run.timed_out:
                recommendations.append(
                    Recommendation(
                        issue=(
                            f"{run.agent_count} kişilik senaryo azami süre içinde "
                            f"tamamlanamadı ({run.evacuated_count}/{run.total_agents} tahliye oldu)."
                        ),
                        suggestions=[
                            "Rota/çıkış kapasitesi gözden geçirilmeli.",
                            "Darboğaz noktası (varsa) genişletilmeli.",
                        ],
                        severity="warning",
                    )
                )
        if not recommendations:
            recommendations.append(
                Recommendation(
                    issue="Test edilen tüm senaryolar eşik dahilinde tamamlandı.",
                    suggestions=["Ek bir aksiyon gerekmiyor (mevcut veriye göre)."],
                    severity="info",
                )
            )
        return recommendations

    @staticmethod
    def from_accessibility_impact(unreachable_room_count: int, room_graph_available: bool) -> list[Recommendation]:
        if not room_graph_available:
            return [
                Recommendation(
                    issue="Bina oda-graf verisi mevcut değil, erişilebilirlik değerlendirmesi yapılamadı.",
                    suggestions=["Bina için oda/kat verisi (RoomGenerator çıktısı) sağlanmalı."],
                    severity="warning",
                )
            ]
        if unreachable_room_count > 0:
            return [
                Recommendation(
                    issue=(
                        f"Asansörler devre dışıyken {unreachable_room_count} odaya "
                        f"merdivenle ulaşılamıyor."
                    ),
                    suggestions=[
                        "Engelli tahliye asansörü (afet-dayanıklı) eklenmesi değerlendirilmeli.",
                        "Refuge alanı (geçici bekleme/sığınma alanı) planlanmalı.",
                        "Bu binanın tahliye planı, erişilebilirlik yönetmeliği açısından eksik olabilir.",
                    ],
                    severity="critical",
                )
            ]
        return [
            Recommendation(
                issue="Asansörler devre dışıyken tüm odalara merdivenle ulaşılabiliyor.",
                suggestions=["Ek bir aksiyon gerekmiyor (mevcut veriye göre)."],
                severity="info",
            )
        ]


# ========================================================================== #
# 3) Duyarlılık analizi (sensitivity analysis)
# ========================================================================== #

@dataclass(slots=True)
class SensitivityResult:
    parameter_name: str
    values_tested: list[float]
    outcomes: list[float]

    def outcome_range(self) -> float:
        return max(self.outcomes) - min(self.outcomes) if self.outcomes else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "parameter_name": self.parameter_name,
            "values_tested": self.values_tested,
            "outcomes": [round(o, 3) for o in self.outcomes],
            "outcome_range": round(self.outcome_range(), 3),
        }


class SensitivityAnalyzer:
    """"Hangi parametrenin (agent sayısı, çıkış genişliği, yol kapasitesi)
    sonucu en çok etkilediğini otomatik tarayan araç" (roadmap).

    Yeni bir tarama algoritması icat edilmedi — çağıranın verdiği
    `run_fn(value) -> float` (örn. `CapacityAnalyzer`/`EvacuationSimulator`
    çağıran bir kapanış) her parametre değeri için tekrar çağrılır (basit,
    şeffaf bir "tek parametre değiştir, sonucu ölç" taraması — tam
    faktöriyel/Sobol duyarlılık analizi değildir, roadmap'in kendi ölçek
    disipliniyle tutarlı bilinçli bir basitleştirme).
    """

    @staticmethod
    def scan_parameter(
        parameter_name: str,
        values: list[float],
        run_fn: Callable[[float], float],
    ) -> SensitivityResult:
        outcomes = [run_fn(v) for v in values]
        return SensitivityResult(parameter_name=parameter_name, values_tested=list(values), outcomes=outcomes)

    @staticmethod
    def rank_parameters(results: list[SensitivityResult]) -> list[SensitivityResult]:
        """Sonucu en çok değiştiren parametreden en aza doğru sıralar."""
        return sorted(results, key=lambda r: r.outcome_range(), reverse=True)


# ========================================================================== #
# 5) Gerçek zamanlı "ne olur" (what-if) modu
# ========================================================================== #

def what_if_close_exit_and_rerun(
    graph: NavGraph,
    exits: list,
    exit_to_close,
    agents: list[Agent],
    node_of_agent: Callable[[Agent], Any],
    *,
    model: Optional[SocialForceModel] = None,
    max_time_s: float = 600.0,
) -> EvacuationResult:
    """Roadmap: "Kullanıcı arayüzde bir kapıyı canlı olarak kapatıp
    simülasyonu yeniden koşturabilsin."

    Yeni bir motor yazılmadı — Faz IV'ün `behavior_rules.
    close_exit_and_seek_alternative` fonksiyonu (kenar `set_blocked` ile
    kapatılır, silinmez — geri alınabilir) + mevcut `EvacuationSimulator`
    doğrudan yeniden kullanılır.

    `exit_to_close`'a bağlı kenarlar `graph.edges()` üzerinden türetilir
    (Faz IV'ün `close_exit_and_seek_alternative` imzası tek bir "kapı
    düğümü" değil, açık `closed_exit_edges` listesi bekliyor — burada
    yeni bir kural yazılmadı, yalnızca var olan imzaya doğru şekilde
    uyarlanıyor).
    """
    closed_exit_edges = [
        (a, b) for (a, b, _cost) in graph.edges()
        if a == exit_to_close or b == exit_to_close
    ]
    remaining_exits = [e for e in exits if e != exit_to_close]
    close_exit_and_seek_alternative(
        agents=agents, graph=graph, closed_exit_edges=closed_exit_edges,
        remaining_exits=remaining_exits, node_of_agent=node_of_agent,
    )
    simulator = EvacuationSimulator(model=model or SocialForceModel())
    return simulator.run(agents, max_time_s=max_time_s)


# ========================================================================== #
# 6) Erişilebilirlik uyarı motoru
# ========================================================================== #

class AccessibilityWarningEngine:
    """Katman 2.1'deki erişilebilirlik profili analiziyle birlikte "bu
    binada engelli tahliye planı yok/yetersiz" gibi otomatik uyarılar
    üreten kural seti — `BuildingNavGraph.unreachable_rooms_without_
    elevator()` (Faz II'de eklendi) doğrudan tüketilir, yeni bir analiz
    motoru yazılmadı."""

    @staticmethod
    def evaluate(building: BuildingNavGraph, *, ground_floor_index: int = 0) -> dict[str, Any]:
        unreachable = building.unreachable_rooms_without_elevator(ground_floor_index)
        recommendations = RecommendationEngine.from_accessibility_impact(
            unreachable_room_count=len(unreachable), room_graph_available=True,
        )
        return {
            "unreachable_room_count": len(unreachable),
            "unreachable_rooms": unreachable,
            "accessibility_warning": len(unreachable) > 0,
            "recommendations": [r.__dict__ for r in recommendations],
            "disclaimer": INDICATIVE_DISCLAIMER,
        }


__all__ = [
    "INDICATIVE_DISCLAIMER",
    "ScenarioComparison",
    "compare_evacuation_results",
    "compare_capacity_reports",
    "Recommendation",
    "RecommendationEngine",
    "SensitivityResult",
    "SensitivityAnalyzer",
    "what_if_close_exit_and_rerun",
    "AccessibilityWarningEngine",
]
