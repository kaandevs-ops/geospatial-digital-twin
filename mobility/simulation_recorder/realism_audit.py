"""
Gerçekçilik Denetim Raporu (Realism Audit)
===========================================

Roadmap V10 / Faz 1.6 — "Ölçüm/kalite katmanı (kendi kendini doğrulayan
simülasyon)".

Roadmap metni: "Her koşu sonunda otomatik 'gerçekçilik denetim raporu'
üretilmeli: ör. 'tahliye süresi dağılımı literatürdeki (Helbing/SFPE)
beklenen aralıkta mı', 'asansör kısıtlı senaryoda merdiven kullanım oranı
mantıklı mı', 'hiçbir ajan duvar/mesh içinden geçmedi mi (collision sanity
check)'."

Bu modül, bir `EvacuationResult` + (varsa) `SimulationRecorder` çıktısını
girdi alarak üç bağımsız denetim çalıştırır ve tek bir `RealismAuditReport`
üretir. Projenin "dürüstlük ilkesi" gereği (ROADMAP_V10.md §0), literatür
aralıkları burada **gösterge niteliğinde varsayılan değerler** olarak
tanımlanır (SFPE Handbook / Helbing & Molnár 1995 tarzı genel eğilimler) —
belirli bir binanın resmi tahliye analizi yerine geçmez; çağıran taraf
`EvacuationAuditThresholds` ile kendi literatür aralığını enjekte edebilir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

from ...core_engine.geometry_engine import Point2D
from ..crowd_simulation import EvacuationResult
from . import AgentFrameState, SimulationRecorder

__all__ = [
    "AuditSeverity",
    "AuditFinding",
    "EvacuationAuditThresholds",
    "RealismAuditReport",
    "run_realism_audit",
]


class AuditSeverity(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(slots=True)
class AuditFinding:
    """Tek bir denetim maddesinin sonucu — CI'nın (Faz 1.7) kırmızı/yeşil
    kararı verebilmesi için makine-okunabilir (`severity`) + insan-okunabilir
    (`message`) bir çift taşır."""

    check_id: str
    severity: AuditSeverity
    message: str
    details: dict = field(default_factory=dict)


@dataclass(slots=True)
class EvacuationAuditThresholds:
    """Roadmap'in "literatürdeki beklenen aralık" ifadesinin somutlaştığı
    yer. Varsayılanlar SFPE Handbook / Helbing & Molnár (1995) tarzı genel
    tahliye-süresi ve darboğaz yoğunluğu eğilimlerine dayanan **kaba, gösterge
    niteliğinde** sınırlardır — `risk_scoring.py`'deki disiplinle tutarlı
    olarak, belirli bir bina için resmi bir mühendislik onayı iddia etmez.
    """

    # Kişi başı tahliye süresi (s) — çok kısa (ör. <1s) fizik ihlali
    # işaretidir (agent'lar ışınlanıyor demektir); çok uzun (ör. >1800s / 30dk
    # bir bina için) genellikle rota/kapasite hatasına işaret eder.
    min_plausible_evacuation_time_s: float = 1.0
    max_plausible_evacuation_time_s: float = 1800.0
    # Darboğazda (bkz. `bottleneck_over_time`) literatürde kabul edilen
    # yaklaşık üst yoğunluk (kişi/m^2) — SFPE'de kritik sıkışma ~4-6 kişi/m^2
    # civarında kabul edilir; bunun belirgin şekilde üzerindeki bir hücre
    # sayısı, sosyal-kuvvet modelinin "birbirinin içinden geçme" moduna
    # girdiğinin bir işaretidir (fiziksel olarak imkansız yoğunluk).
    max_plausible_density_per_m2: float = 8.0
    # Duvar/engel penetrasyon toleransı (m) — sayısal integrasyon nedeniyle
    # küçük, geçici bir örtüşme normaldir; bunun üzerindeki kalıcı
    # penetrasyon collision-sanity ihlalidir.
    obstacle_penetration_tolerance_m: float = 0.15


@dataclass(slots=True)
class RealismAuditReport:
    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def overall_severity(self) -> AuditSeverity:
        if any(f.severity == AuditSeverity.FAIL for f in self.findings):
            return AuditSeverity.FAIL
        if any(f.severity == AuditSeverity.WARN for f in self.findings):
            return AuditSeverity.WARN
        return AuditSeverity.PASS

    @property
    def passed(self) -> bool:
        """Faz 1.7 CI kapısının doğrudan kullandığı boolean — yalnızca FAIL
        varsa kırmızı olur, WARN CI'yı kırmaz ama rapora yansır."""
        return self.overall_severity != AuditSeverity.FAIL

    def to_markdown(self) -> str:
        lines = [f"# Gerçekçilik Denetim Raporu — genel durum: {self.overall_severity.value.upper()}", ""]
        for f in self.findings:
            icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[f.severity.value]
            lines.append(f"- {icon} `{f.check_id}` — {f.message}")
        return "\n".join(lines)


def _check_evacuation_time_distribution(
    result: EvacuationResult, thresholds: EvacuationAuditThresholds
) -> AuditFinding:
    times = list(getattr(result, "per_agent_time_s", {}).values())
    if not times:
        return AuditFinding(
            "evacuation_time_distribution", AuditSeverity.WARN,
            "Kişi-başı tahliye süresi verisi bulunamadı — dağılım denetlenemedi "
            "(EvacuationResult bu alanı doldurmuyor olabilir).",
        )
    out_of_range = [
        t for t in times
        if t < thresholds.min_plausible_evacuation_time_s
        or t > thresholds.max_plausible_evacuation_time_s
    ]
    ratio = len(out_of_range) / len(times)
    if ratio > 0.10:
        return AuditFinding(
            "evacuation_time_distribution", AuditSeverity.FAIL,
            f"Ajanların %{ratio * 100:.1f}'i literatür-dışı tahliye süresine "
            f"sahip (izin verilen aralık: "
            f"[{thresholds.min_plausible_evacuation_time_s}, "
            f"{thresholds.max_plausible_evacuation_time_s}] sn) — %10 eşiği aşıldı.",
            details={"out_of_range_ratio": ratio, "sample_size": len(times)},
        )
    if out_of_range:
        return AuditFinding(
            "evacuation_time_distribution", AuditSeverity.WARN,
            f"Ajanların %{ratio * 100:.1f}'i aralık dışında ama %10 eşiğinin altında.",
            details={"out_of_range_ratio": ratio, "sample_size": len(times)},
        )
    return AuditFinding(
        "evacuation_time_distribution", AuditSeverity.PASS,
        f"Tüm {len(times)} ajanın tahliye süresi beklenen literatür aralığında.",
    )


def _check_collision_sanity(
    recorder: SimulationRecorder | None,
    obstacles: list[Point2D] | None,
    agent_radius_m: float,
    thresholds: EvacuationAuditThresholds,
) -> AuditFinding:
    if recorder is None or not recorder.keyframes:
        return AuditFinding(
            "collision_sanity", AuditSeverity.WARN,
            "Recorder verilmedi/boş — duvar-penetrasyon denetimi yapılamadı.",
        )
    obstacles = obstacles or []
    if not obstacles:
        return AuditFinding(
            "collision_sanity", AuditSeverity.PASS,
            "Engel listesi boş — denetlenecek duvar yok (açık alan senaryosu).",
        )
    worst_penetration = 0.0
    worst_at: tuple[float, int] | None = None
    for kf in recorder.keyframes:
        for snap in kf.agents:
            if snap.state == AgentFrameState.EVACUATED:
                continue
            for obstacle in obstacles:
                dist = math.hypot(snap.x - obstacle.x, snap.y - obstacle.y)
                penetration = agent_radius_m - dist
                if penetration > worst_penetration:
                    worst_penetration = penetration
                    worst_at = (kf.t, snap.agent_id)
    if worst_penetration > thresholds.obstacle_penetration_tolerance_m:
        t, agent_id = worst_at
        return AuditFinding(
            "collision_sanity", AuditSeverity.FAIL,
            f"Agent {agent_id}, t={t:.1f}s anında bir engelin {worst_penetration:.2f}m "
            f"içine geçti (tolerans: {thresholds.obstacle_penetration_tolerance_m}m) — "
            f"bu, ajanın duvar/mesh içinden geçtiği anlamına gelir.",
            details={"worst_penetration_m": worst_penetration, "at": worst_at},
        )
    return AuditFinding(
        "collision_sanity", AuditSeverity.PASS,
        f"Hiçbir ajan hiçbir keyframe'de duvar/engel içine "
        f"{thresholds.obstacle_penetration_tolerance_m}m toleransından fazla geçmedi "
        f"(en kötü değer: {worst_penetration:.3f}m).",
    )


def _check_bottleneck_density(
    recorder: SimulationRecorder | None, cell_size_m: float, thresholds: EvacuationAuditThresholds
) -> AuditFinding:
    if recorder is None or not recorder.keyframes:
        return AuditFinding(
            "bottleneck_density", AuditSeverity.WARN,
            "Recorder verilmedi/boş — darboğaz yoğunluğu denetlenemedi.",
        )
    series = recorder.bottleneck_over_time(cell_size=cell_size_m)
    if not series:
        return AuditFinding(
            "bottleneck_density", AuditSeverity.PASS,
            "Kayıtlı koşuda hiçbir dolu hücre gözlemlenmedi (ör. çok az ajan/çok kısa koşu).",
        )
    cell_area_m2 = cell_size_m * cell_size_m
    max_density = max(count / cell_area_m2 for _, _, count in series)
    if max_density > thresholds.max_plausible_density_per_m2:
        return AuditFinding(
            "bottleneck_density", AuditSeverity.FAIL,
            f"En yoğun hücrede {max_density:.1f} kişi/m² gözlendi — literatürde "
            f"fiziksel olarak makul kabul edilen üst sınırın "
            f"({thresholds.max_plausible_density_per_m2} kişi/m²) üzerinde. Bu, "
            f"sosyal-kuvvet modelinin ajanları birbirinin içine sıkıştırdığına işaret eder.",
            details={"max_density_per_m2": max_density},
        )
    return AuditFinding(
        "bottleneck_density", AuditSeverity.PASS,
        f"En yoğun hücre {max_density:.1f} kişi/m² — literatür üst sınırının "
        f"({thresholds.max_plausible_density_per_m2} kişi/m²) altında.",
    )


def run_realism_audit(
    result: EvacuationResult,
    recorder: SimulationRecorder | None = None,
    obstacles: list[Point2D] | None = None,
    agent_radius_m: float = 0.25,
    cell_size_m: float = 1.0,
    thresholds: EvacuationAuditThresholds | None = None,
) -> RealismAuditReport:
    """Roadmap V10 / Faz 1.6 — bir koşunun "gerçekçilik denetim raporu"nu
    üretir. Üç bağımsız kontrolü çalıştırır ve hepsini tek bir raporda
    birleştirir; herhangi biri FAIL verirse `report.passed` False olur
    (Faz 1.7 CI kapısı bunu doğrudan kullanır)."""
    thresholds = thresholds or EvacuationAuditThresholds()
    findings = [
        _check_evacuation_time_distribution(result, thresholds),
        _check_collision_sanity(recorder, obstacles, agent_radius_m, thresholds),
        _check_bottleneck_density(recorder, cell_size_m, thresholds),
    ]
    if recorder is not None and recorder.run_metadata.seed is None:
        findings.append(AuditFinding(
            "deterministic_replay_seed", AuditSeverity.WARN,
            "Bu koşu için `recorder.run_metadata.seed` boş — koşu tekrar "
            "oynatılabilirliği (Faz 1.5) doğrulanamaz. `EvacuationSimulator.run(..., "
            "seed=...)` ile koşmayı düşünün.",
        ))
    else:
        findings.append(AuditFinding(
            "deterministic_replay_seed", AuditSeverity.PASS,
            f"Koşu seed={recorder.run_metadata.seed} ile etiketlendi — tekrar oynatılabilir.",
        ))
    return RealismAuditReport(findings=findings)
