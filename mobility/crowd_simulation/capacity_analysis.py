"""
Bina Kapasite Simülasyonu - Batch Runner
=========================================

Roadmap V9 / Katman 7.3 ("Bina Kapasite Simülasyonu — eski 'madde 5',
yeni kod gerektirmiyor, referans korunuyor"):

    "Mevcut `EvacuationBenchmark`'ın parametrik koşulmasıyla elde edilir
    - zaten 'N agent, X exit_width' ile referans süre hesaplıyor.
    Eklenecek: Batch runner - aynı bina için 50/200/500 kişi
    senaryolarını otomatik art arda koşturup karşılaştırmalı rapor
    üreten ince orkestrasyon katmanı. Çıktı formatı: 'X kişi için
    tahliye süresi Y sn, darboğaz noktası Z' tablosu + kapasite eşiği
    uyarısı (TBDY/yönetmelikteki maksimum kabul edilebilir tahliye
    süresine göre 'güvenli/riskli' etiketi - eşik değeri `regulations`
    modülünden gelmeli, iskelet halindeyse önce o doldurulmalı)."

Bu modül **yeni bir simülasyon motoru yazmaz** - roadmap'in kendi
talimatına uygun olarak yalnızca mevcut `EvacuationBenchmark` /
`EvacuationSimulator`'ı tekrar tekrar çağıran ince bir sarmalayıcıdır
(kod tekrarı yaratılmadı).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ...building_reconstruction.regulations import RegulationProfile, default_profile
from . import (
    EvacuationBenchmark,
    EvacuationSimulator,
    SocialForceModel,
)

try:  # pragma: no cover - yalnızca opsiyonel entegrasyon, döngüsel import yok
    from ..simulation_recorder import SimulationRecorder
except Exception:  # pragma: no cover
    SimulationRecorder = None  # type: ignore[assignment]


@dataclass(slots=True)
class CapacityRunResult:
    """Tek bir "N kişilik senaryo" koşumunun sonucu - roadmap'in "X kişi
    için tahliye süresi Y sn, darboğaz noktası Z" tablo satırı."""

    agent_count: int
    evacuation_time_s: float
    evacuated_count: int
    total_agents: int
    timed_out: bool
    bottleneck_cell: tuple | None
    bottleneck_count: int | None
    threshold_s: Optional[float]
    within_threshold: Optional[bool]     # threshold_s yoksa None (sessizce "güvenli" varsayılmaz)


@dataclass(slots=True)
class CapacityAnalysisReport:
    """Batch koşumun tam raporu - `CapacityAnalyzer.run_batch()` çıktısı."""

    building_type: Optional[str]
    exit_width_m: float
    regulation_profile_name: str
    runs: list[CapacityRunResult] = field(default_factory=list)
    disclaimer: str = (
        "Bu rapor gösterge niteliğindedir; kesin bir mühendislik tahliye "
        "raporunun yerini tutmaz. Gerçek panik dinamiği, yönlendirme "
        "işaretleri ve bina-özgü engeller birebir modellenmemiştir."
    )

    def worst_case(self) -> Optional[CapacityRunResult]:
        """En uzun tahliye süresine sahip koşum - genelde en yüksek agent
        sayısına karşılık gelir, ama zaman aşımı/darboğaz farkları
        yüzünden garanti değildir; bu yüzden doğrudan max() ile bulunur."""
        if not self.runs:
            return None
        return max(self.runs, key=lambda r: r.evacuation_time_s)

    def any_over_threshold(self) -> bool:
        return any(r.within_threshold is False for r in self.runs)

    def to_dict(self) -> dict:
        return {
            "building_type": self.building_type,
            "exit_width_m": self.exit_width_m,
            "regulation_profile_name": self.regulation_profile_name,
            "disclaimer": self.disclaimer,
            "any_over_threshold": self.any_over_threshold(),
            "runs": [
                {
                    "agent_count": r.agent_count,
                    "evacuation_time_s": round(r.evacuation_time_s, 2),
                    "evacuated_count": r.evacuated_count,
                    "total_agents": r.total_agents,
                    "timed_out": r.timed_out,
                    "bottleneck_cell": r.bottleneck_cell,
                    "bottleneck_count": r.bottleneck_count,
                    "threshold_s": r.threshold_s,
                    "within_threshold": r.within_threshold,
                }
                for r in self.runs
            ],
        }


# Roadmap'in kendi örneği: "aynı bina için 50/200/500 kişi senaryolarını
# otomatik art arda koşturup".
DEFAULT_CAPACITY_AGENT_COUNTS: tuple[int, ...] = (50, 200, 500)


class CapacityAnalyzer:
    """`EvacuationBenchmark`/`EvacuationSimulator`'ı N farklı agent-sayısı
    için art arda koşturan ince orkestrasyon katmanı (roadmap 7.3)."""

    @staticmethod
    def run_batch(
        room_width_m: float,
        room_depth_m: float,
        exit_width_m: float,
        agent_counts: tuple[int, ...] = DEFAULT_CAPACITY_AGENT_COUNTS,
        building_type: Optional[str] = None,
        regulation_profile: Optional[RegulationProfile] = None,
        seed: int = 42,
        dt: float = 0.1,
        max_time_s: float = 900.0,
    ) -> CapacityAnalysisReport:
        """Aynı oda/çıkış geometrisini artan agent sayılarıyla koşturur.

        Her koşum `EvacuationBenchmark.build_single_exit_room` ile aynı
        geometriyi kurar (kod tekrarı yok), ardından tam `EvacuationSimulator`
        (A* + sosyal-kuvvet, `EvacuationBenchmark`'ın basitleştirilmiş
        "gate budget" modeli yerine) ile koşturulur - böylece
        `OccupancyHeatmap` tabanlı gerçek darboğaz tespiti de mümkün olur.
        """
        profile = regulation_profile or default_profile()
        threshold_s = profile.evacuation_time_threshold_s(building_type)

        report = CapacityAnalysisReport(
            building_type=building_type,
            exit_width_m=exit_width_m,
            regulation_profile_name=profile.name,
        )

        for count in agent_counts:
            agents, exit_point = EvacuationBenchmark.build_single_exit_room(
                count, room_width_m, room_depth_m, exit_width_m, seed=seed,
            )
            simulator = EvacuationSimulator(SocialForceModel())
            recorder = SimulationRecorder(keyframe_interval_s=0.5) if SimulationRecorder is not None else None
            result = simulator.run(agents, obstacles=None, dt=dt, max_time_s=max_time_s,
                                     recorder=recorder)

            within_threshold = (
                (result.evacuation_time_s <= threshold_s) if threshold_s is not None else None
            )
            report.runs.append(CapacityRunResult(
                agent_count=count,
                evacuation_time_s=result.evacuation_time_s,
                evacuated_count=result.evacuated_count,
                total_agents=result.total_agents,
                timed_out=result.timed_out,
                bottleneck_cell=result.bottleneck_location,
                bottleneck_count=result.bottleneck_peak_count,
                threshold_s=threshold_s,
                within_threshold=within_threshold,
            ))

        return report
