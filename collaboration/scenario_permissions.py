"""
Rol-Bazlı Senaryo İzinleri — ROADMAP_V9 Faz X / Katman 8 madde 1
====================================================================

Roadmap metni: "Görüntüleyici sadece izler, mühendis senaryo koşturabilir,
admin gerçek veri kaynaklarını (AFAD/OSM canlı) yapılandırabilir —
`collaboration/auth.py` üzerine yetkilendirme matrisi."

Bu modül **yeni bir rol sistemi icat etmez** (roadmap ilkesi #2) —
`collaboration.auth.Role` (VIEWER/EDITOR/OWNER, zaten ROADMAP_V2 Faz 19'da
tanımlı ve `AuthService` tarafından üretilip doğrulanıyor) doğrudan
kullanılır. Yalnızca şehir-simülasyonuna özgü eylemleri (senaryo görüntüleme/
koşturma/gerçek-veri-kaynağı yapılandırma) bu üç role eşleyen ince bir
yetkilendirme matrisi eklenir.

Eşleme (roadmap metniyle birebir):
- VIEWER : yalnızca `VIEW_RESULT` (görüntüleyici sadece izler)
- EDITOR  : `VIEW_RESULT` + `RUN_SCENARIO` (mühendis senaryo koşturabilir)
- OWNER   : `VIEW_RESULT` + `RUN_SCENARIO` + `CONFIGURE_REALITY_FEED`
            (admin gerçek veri kaynaklarını yapılandırabilir — bkz.
            `digital_twin/reality_feed.py`, O.5)
"""

from __future__ import annotations

from enum import Enum

from .auth import PermissionDeniedError, Role


class ScenarioAction(str, Enum):
    """Şehir-simülasyonuna özgü, izin denetimine tabi eylemler."""

    VIEW_RESULT = "view_result"
    RUN_SCENARIO = "run_scenario"
    CONFIGURE_REALITY_FEED = "configure_reality_feed"


#: Roadmap'in kendi matrisi — bilinçli olarak açık/okunabilir bir tablo
#: (kara kutu bir "yetki motoru" değil).
_PERMISSION_MATRIX: dict[Role, frozenset[ScenarioAction]] = {
    Role.VIEWER: frozenset({ScenarioAction.VIEW_RESULT}),
    Role.EDITOR: frozenset({ScenarioAction.VIEW_RESULT, ScenarioAction.RUN_SCENARIO}),
    Role.OWNER: frozenset(
        {
            ScenarioAction.VIEW_RESULT,
            ScenarioAction.RUN_SCENARIO,
            ScenarioAction.CONFIGURE_REALITY_FEED,
        }
    ),
}


def can_perform(role: Role, action: ScenarioAction) -> bool:
    """`role`'ün `action`'ı gerçekleştirmeye yetkisi var mı?"""
    return action in _PERMISSION_MATRIX.get(role, frozenset())


def require_scenario_permission(role: Role | None, action: ScenarioAction) -> None:
    """Yetki yoksa `PermissionDeniedError` fırlatır.

    `role=None` **bilinçli olarak izin verir** (geriye uyumluluk): mevcut
    `app_shell.session.AppSession` çağıranları (testler dahil) rol
    bilgisi olmadan çalışmaya devam eder — roadmap'in kendi disiplini
    "mevcut kod bozulmaz" ile tutarlı. Rol denetimi yalnızca çağıran taraf
    açıkça bir `Role` sağladığında devreye girer.
    """
    if role is None:
        return
    if not can_perform(role, action):
        raise PermissionDeniedError(
            f"rol '{role.name}' bu işlem için yetkili değil: {action.value}"
        )


__all__ = ["ScenarioAction", "can_perform", "require_scenario_permission"]
