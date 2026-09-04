"""
Collaboration
==============

ROADMAP_V2 Faz 19 — "Çok Kullanıcılı İşbirliği & Kimlik Doğrulama".

Alt modüller:
    * `auth`            - `AuthService` (kullanıcı kaydı/girişi, PBKDF2 parola
                           hashleme, oturum tokenları, proje-bazlı rol
                           yönetimi: VIEWER/EDITOR/OWNER)
    * `crdt`             - `LWWRegister`, `ORSet`, `CRDTBuildingState`
                           (matematiksel olarak çakışmasız birleştirme —
                           bkz. modülün docstring'i, OT yerine CRDT tercih
                           gerekçesi)
    * `collab_session`   - `CollaborationHub`/`CollaborationRoom` — Faz 14
                           `WebSocketRouter`'ı `auth` + `crdt` ile birleştiren
                           gerçek zamanlı ortak düzenleme köprüsü

Kabul kriteri (ROADMAP_V2): "İki kullanıcı aynı binayı eşzamanlı düzenler,
çakışma otomatik ve kayıpsız çözülür." — bkz. `tests/test_faz19_collaboration.py`.
"""

from .auth import (
    AuthError,
    AuthService,
    InvalidCredentialsError,
    InvalidTokenError,
    PermissionDeniedError,
    ProjectMembership,
    Role,
    SessionToken,
    TokenExpiredError,
    UserAccount,
    UserAlreadyExistsError,
)
from .collab_session import (
    CollaborationError,
    CollaborationHub,
    CollaborationRoom,
    RoomMember,
    RoomNotFoundError,
)
from .crdt import CRDTBuildingState, LWWRegister, ORSet
from .scenario_permissions import ScenarioAction, can_perform, require_scenario_permission
from .scenario_watch import PlaybackState, ScenarioWatchHub, scenario_topic
from .ws_server import CollaborationWebSocketServer, WebSocketProtocolError

__all__ = [
    "AuthError",
    "AuthService",
    "InvalidCredentialsError",
    "InvalidTokenError",
    "PermissionDeniedError",
    "ProjectMembership",
    "Role",
    "SessionToken",
    "TokenExpiredError",
    "UserAccount",
    "UserAlreadyExistsError",
    "CollaborationError",
    "CollaborationHub",
    "CollaborationRoom",
    "RoomMember",
    "RoomNotFoundError",
    "LWWRegister",
    "ORSet",
    "CRDTBuildingState",
    "CollaborationWebSocketServer",
    "WebSocketProtocolError",
    "ScenarioAction",
    "can_perform",
    "require_scenario_permission",
    "PlaybackState",
    "ScenarioWatchHub",
    "scenario_topic",
]
