"""
Collaborative Session — Gerçek Zamanlı Ortak Düzenleme Köprüsü
===================================================================

ROADMAP_V2 Faz 19 — `auth.AuthService` (kimlik doğrulama + rol-tabanlı
yetkilendirme) ile `crdt.CRDTBuildingState` (çakışmasız birleştirme) ve
Faz 14'ün `extensibility.websocket_api.WebSocketRouter`'ını (soket-agnostik
mesaj yönlendirme) tek bir "oda" (`CollaborationRoom`) soyutlamasında
birleştirir.

Akış
-----
1. İstemci `join(token, project_id, building_key)` ile bir odaya katılır —
   token doğrulanır, en az `EDITOR` rolü olmayan istemciler yalnızca
   `apply_edit` haricindeki her şeyi yapabilir (read-only).
2. Bir istemci `apply_edit(...)` çağırdığında, yerel CRDT state'i güncellenir
   ve **tüm diğer odadaki bağlantılara** bir "patch" broadcast edilir.
3. Her istemci kendi CRDT state'ini gelen patch'lerle `merge()` eder —
   sıra/gecikme/tekrar fark etmeksizin tüm istemciler aynı nihai duruma
   yakınsar (bkz. `crdt.py` docstring'i, CRDT'nin matematiksel garantisi).

Bu, ROADMAP_V2 Faz 19 kabul kriterinin ("İki kullanıcı aynı binayı
eşzamanlı düzenler, çakışma otomatik ve kayıpsız çözülür") doğrudan
uygulamasıdır — testler, iki bağlantının **çakışan** eşzamanlı yazımlar
(aynı alana farklı değer) göndermesi ve her iki tarafın da CRDT
birleştirmesinden sonra **aynı, deterministik** nihai duruma ulaştığını
kanıtlar.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..extensibility.websocket_api import WebSocketRouter, WSConnection, WSMessage
from .auth import AuthService, PermissionDeniedError, Role
from .crdt import CRDTBuildingState

if TYPE_CHECKING:  # pragma: no cover - yalnizca tip kontrolu icin
    from ..persistence.db_backend import ProjectDatabase

_CRDT_OBJECT_KIND = "crdt_building_state"


class CollaborationError(Exception):
    """Oda/katılım hatalarının ortak temel sınıfı."""


class RoomNotFoundError(CollaborationError):
    pass


@dataclass(slots=True)
class RoomMember:
    connection_id: str
    user_id: str
    username: str
    role: Role


@dataclass(slots=True)
class CollaborationRoom:
    """Tek bir binaya (`building_key`) karşılık gelen ortak düzenleme odası.

    Oda, kendi `CRDTBuildingState`'ini tutar — bu, "sunucudaki gerçek
    durum" değil, **bu oda üzerinden geçen tüm patch'lerin birleşimidir**;
    her istemcinin kendi yerel kopyası da bağımsız olarak aynı sonuca
    yakınsar (CRDT garantisi), sunucu yalnızca patch'leri iletir/saklar.
    """

    building_key: str
    state: CRDTBuildingState
    members: dict[str, RoomMember] = field(default_factory=dict)  # connection_id -> member


class CollaborationHub:
    """
    Birden çok `CollaborationRoom`'u yönetir, `AuthService` ile yetkilendirir
    ve `WebSocketRouter` üzerinden broadcast eder.
    """

    def __init__(
        self,
        auth: AuthService,
        router: WebSocketRouter | None = None,
        db: ProjectDatabase | None = None,
    ) -> None:
        self._auth = auth
        self._router = router or WebSocketRouter()
        self._rooms: dict[str, CollaborationRoom] = {}
        self._db = db
        self._register_handlers()

    # ------------------------------------------------------------------ #
    # Kalicilik (Faz D18): CRDT durumunun db_backend'e serilestirilmesi
    # ------------------------------------------------------------------ #
    def _persist_room(self, key: str, room: CollaborationRoom) -> None:
        """Oda durumunu (varsa) kalici katmana yazar. `db` verilmemisse
        sessizce hicbir sey yapmaz (bellek-ici referans davranisi
        tamamen korunur, geriye uyumlu)."""
        if self._db is None:
            return
        self._db.save_object(f"collab_room:{key}", _CRDT_OBJECT_KIND, room.state.to_dict())

    def _load_persisted_state(self, key: str, building_key: str) -> CRDTBuildingState:
        """Kalici katmanda bu oda icin daha once kaydedilmis bir durum
        varsa geri yukler; yoksa bos bir `CRDTBuildingState` doner. `db`
        verilmemisse her zaman bos durumdan baslar (mevcut davranis)."""
        if self._db is not None:
            record = self._db.load_object(f"collab_room:{key}")
            if record is not None:
                return CRDTBuildingState.from_dict(record.data)
        return CRDTBuildingState(building_key=building_key)

    @property
    def router(self) -> WebSocketRouter:
        return self._router

    def _room_key(self, project_id: str, building_key: str) -> str:
        return f"{project_id}:{building_key}"

    def _room(self, project_id: str, building_key: str) -> CollaborationRoom:
        key = self._room_key(project_id, building_key)
        room = self._rooms.get(key)
        if room is None:
            raise RoomNotFoundError(f"oda bulunamadi: {key}")
        return room

    # ------------------------------------------------------------------ #
    # Katılım / ayrılma
    # ------------------------------------------------------------------ #
    def join(
        self,
        connection: WSConnection,
        *,
        token: str,
        project_id: str,
        building_key: str,
    ) -> CollaborationRoom:
        user = self._auth.authenticate_token(token)
        role = self._auth.require_role(project_id, user.user_id, at_least=Role.VIEWER)

        key = self._room_key(project_id, building_key)
        room = self._rooms.get(key)
        if room is None:
            # Faz D18: bellek-ici oda yoksa, once kalici katmandan geri
            # yuklemeyi dene (sunucu yeniden baslatilmis olabilir); orada
            # da yoksa bos bir durumdan basla (eski davranis, tamamen
            # geriye uyumlu).
            state = self._load_persisted_state(key, building_key)
            room = CollaborationRoom(building_key=building_key, state=state)
            self._rooms[key] = room

        room.members[connection.connection_id] = RoomMember(
            connection_id=connection.connection_id,
            user_id=user.user_id,
            username=user.username,
            role=role,
        )
        connection.subscribe(f"room:{key}")
        return room

    def leave(self, connection: WSConnection, *, project_id: str, building_key: str) -> None:
        key = self._room_key(project_id, building_key)
        room = self._rooms.get(key)
        if room is not None:
            room.members.pop(connection.connection_id, None)
        connection.unsubscribe(f"room:{key}")

    # ------------------------------------------------------------------ #
    # Düzenleme uygulama (edit apply) — CRDT + broadcast
    # ------------------------------------------------------------------ #
    def apply_field_edit(
        self,
        connection: WSConnection,
        *,
        project_id: str,
        building_key: str,
        field_name: str,
        value: Any,
        timestamp: float | None = None,
    ) -> CRDTBuildingState:
        room = self._room(project_id, building_key)
        member = room.members.get(connection.connection_id)
        if member is None or not member.role.can_write():
            raise PermissionDeniedError(
                f"user={member.user_id if member else '?'} bu odada yazma yetkisine sahip degil"
            )
        ts = timestamp if timestamp is not None else time.time()
        room.state.set_field(field_name, value, ts, member.user_id)
        self._persist_room(self._room_key(project_id, building_key), room)

        patch = {
            "op": "set_field",
            "field": field_name,
            "value": value,
            "timestamp": ts,
            "actor_id": member.user_id,
        }
        self._broadcast_patch(project_id, building_key, patch, exclude=connection.connection_id)
        return room.state

    def apply_add_floor(
        self,
        connection: WSConnection,
        *,
        project_id: str,
        building_key: str,
        floor_id: str,
    ) -> CRDTBuildingState:
        room = self._room(project_id, building_key)
        member = room.members.get(connection.connection_id)
        if member is None or not member.role.can_write():
            raise PermissionDeniedError("yazma yetkisi yok")
        room.state.add_floor(floor_id, member.user_id)
        self._persist_room(self._room_key(project_id, building_key), room)
        patch = {"op": "add_floor", "floor_id": floor_id, "actor_id": member.user_id}
        self._broadcast_patch(project_id, building_key, patch, exclude=connection.connection_id)
        return room.state

    def apply_remove_floor(
        self,
        connection: WSConnection,
        *,
        project_id: str,
        building_key: str,
        floor_id: str,
    ) -> CRDTBuildingState:
        room = self._room(project_id, building_key)
        member = room.members.get(connection.connection_id)
        if member is None or not member.role.can_write():
            raise PermissionDeniedError("yazma yetkisi yok")
        room.state.remove_floor(floor_id)
        self._persist_room(self._room_key(project_id, building_key), room)
        patch = {"op": "remove_floor", "floor_id": floor_id, "actor_id": member.user_id}
        self._broadcast_patch(project_id, building_key, patch, exclude=connection.connection_id)
        return room.state

    def receive_remote_patch(self, room: CollaborationRoom, patch: dict[str, Any]) -> None:
        """Uzaktan (başka bir sunucu replikası/istemci) gelen bir patch'i
        yerel CRDT state'ine uygular — `merge()` çakışmasız olduğundan
        patch'lerin varış sırası sonucu etkilemez."""
        if patch["op"] == "set_field":
            room.state.set_field(
                patch["field"], patch["value"], patch["timestamp"], patch["actor_id"]
            )
        elif patch["op"] == "add_floor":
            room.state.add_floor(patch["floor_id"], patch["actor_id"])
        elif patch["op"] == "remove_floor":
            room.state.remove_floor(patch["floor_id"])
        key = self._room_key_for_room(room)
        if key is not None:
            self._persist_room(key, room)

    def _room_key_for_room(self, room: CollaborationRoom) -> str | None:
        for key, candidate in self._rooms.items():
            if candidate is room:
                return key
        return None

    def _broadcast_patch(
        self,
        project_id: str,
        building_key: str,
        patch: dict[str, Any],
        *,
        exclude: str,
    ) -> int:
        key = self._room_key(project_id, building_key)
        message = WSMessage(type="crdt_patch", payload={"room": key, "patch": patch})
        count = 0
        for conn in list(self._router._connections.values()):  # noqa: SLF001 - dahili erisim, ayni paket
            if conn.connection_id == exclude or conn.closed:
                continue
            if f"room:{key}" in conn.topics:
                conn.send(message)
                count += 1
        return count

    # ------------------------------------------------------------------ #
    # WebSocketRouter handler kaydı
    # ------------------------------------------------------------------ #
    def _register_handlers(self) -> None:
        def _on_join(connection: WSConnection, payload: dict) -> dict:
            room = self.join(
                connection,
                token=payload["token"],
                project_id=payload["project_id"],
                building_key=payload["building_key"],
            )
            return {
                "members": [m.username for m in room.members.values()],
                "fields": {name: reg.value for name, reg in room.state.fields.items()},
            }

        def _on_edit(connection: WSConnection, payload: dict) -> dict:
            state = self.apply_field_edit(
                connection,
                project_id=payload["project_id"],
                building_key=payload["building_key"],
                field_name=payload["field"],
                value=payload["value"],
            )
            return {"ok": True, "fields": {n: r.value for n, r in state.fields.items()}}

        self._router.register("collab.join", _on_join)
        self._router.register("collab.edit", _on_edit)


__all__ = [
    "CollaborationError",
    "RoomNotFoundError",
    "RoomMember",
    "CollaborationRoom",
    "CollaborationHub",
]
