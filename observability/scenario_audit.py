"""
Şehir-Ölçeği Senaryo Denetim İzi (Audit Trail) — ROADMAP_V9 Faz X / Katman 8 madde 3
========================================================================================

Roadmap metni: "Kim, ne zaman, hangi senaryoyu koşturdu, hangi sonucu
aldı — `observability/logging.py` şehir-ölçeği senaryo geçmişine
özelleştirilmeli."

Yeni bir loglama motoru yazılmadı (roadmap ilkesi #2) — mevcut
`observability.logging.StructuredLogger` (zaten var, bellek-içi halka
tampon + JSON-lines çıktı) doğrudan kullanılır. Bu modül yalnızca
"senaryo çalıştırma" olayı için sabit bir alan şeması (`actor`,
`project_id`, `scenario_id`, `result_id`, `action`) ve bu şemayla
loglanmış kayıtları sorgulayan ince yardımcılar sağlar.

Ayrıca `persistence.db_backend.ProjectDatabase.iter_history()` (zaten var,
her `save_object` çağrısında otomatik biriken (ts, op, key, kind) günlüğü)
ile birleşik bir "kim + ne" görünümü kurulabilir: `StructuredLogger` kimin
(actor/role) tetiklediğini, `iter_history()` projedeki hangi nesnenin
(scenario/simulation_result/capacity_analysis_result) değiştiğini tutar —
ikisi ayrı, tekrarsız kaynaklardır.
"""

from __future__ import annotations

from typing import Any

from .logging import StructuredLogger

#: Denetim izinde kullanılan sabit olay adı — filtre/sorgu için tutarlı.
SCENARIO_AUDIT_EVENT = "scenario_audit"


def record_scenario_event(
    logger: StructuredLogger,
    *,
    actor: str,
    project_id: str,
    action: str,
    scenario_id: str | None = None,
    result_id: str | None = None,
    role: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Bir senaryo eylemini (kaydet/koştur/görüntüle) denetim izine yazar.

    `action` serbest metin değil, çağıranların tutarlı kullanması beklenen
    kısa bir fiil (`"scenario_saved"`, `"evacuation_run"`,
    `"capacity_analysis_run"`, `"result_viewed"` gibi) — roadmap'in "kim,
    ne zaman, hangi senaryoyu koşturdu, hangi sonucu aldı" sorusuna cevap
    verecek asgari alan kümesi burada zorunlu tutulur.
    """
    return logger.info(
        SCENARIO_AUDIT_EVENT,
        event=SCENARIO_AUDIT_EVENT,
        actor=actor,
        project_id=project_id,
        action=action,
        scenario_id=scenario_id,
        result_id=result_id,
        role=role,
        **extra,
    )


def query_scenario_audit_trail(
    logger: StructuredLogger,
    *,
    project_id: str | None = None,
    actor: str | None = None,
    scenario_id: str | None = None,
) -> list[dict[str, Any]]:
    """Bellek-içi kayıtları (`StructuredLogger.records()`) denetim izi
    şemasına göre filtreler — yeni bir depolama icat edilmedi, mevcut
    halka tampon taranır. Zaman sırasına göre (en eski önce) döner."""
    results = []
    for record in logger.records():
        if record.get("event") != SCENARIO_AUDIT_EVENT:
            continue
        if project_id is not None and record.get("project_id") != project_id:
            continue
        if actor is not None and record.get("actor") != actor:
            continue
        if scenario_id is not None and record.get("scenario_id") != scenario_id:
            continue
        results.append(record)
    return results


__all__ = [
    "SCENARIO_AUDIT_EVENT",
    "record_scenario_event",
    "query_scenario_audit_trail",
]
