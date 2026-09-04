"""
ROADMAP_V3 — Faz D18: Collaboration kalicilik (persistence entegrasyonu)
testleri.

Kabul kriteri: bir collaboration oturumu ortasinda "sunucu" yeniden
baslatilir (test icinde yeni bir `CollaborationHub` nesnesi + ayni DB
dosyasi ile), kaldigi CRDT durumu kayipsiz geri yuklenir.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.collaboration.auth import AuthService, Role
from harita.collaboration.collab_session import CollaborationHub
from harita.collaboration.crdt import CRDTBuildingState, LWWRegister, ORSet
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest


@pytest.fixture()
def db(tmp_path: Path) -> ProjectDatabase:
    path = tmp_path / "collab_test.hproj"
    manifest = ProjectManifest(name="CollabTest", project_id="p1")
    database = ProjectDatabase.create(path, manifest)
    yield database
    database.close()


class TestCRDTSerialization:
    def test_lww_register_roundtrip(self) -> None:
        reg: LWWRegister[float] = LWWRegister(value=12.5, timestamp=1.0, actor_id="alice")
        restored = LWWRegister.from_dict(reg.to_dict())
        assert restored.value == 12.5
        assert restored.timestamp == 1.0
        assert restored.actor_id == "alice"

    def test_orset_roundtrip(self) -> None:
        orset: ORSet[str] = ORSet()
        orset.add("kat-1", ("alice", 1))
        orset.add("kat-2", ("bob", 1))
        orset.remove("kat-1")
        restored = ORSet.from_dict(orset.to_dict())
        assert restored.elements() == {"kat-2"}

    def test_building_state_roundtrip(self) -> None:
        state = CRDTBuildingState(building_key="bina-a")
        state.set_field("height_m", 30.0, timestamp=1.0, actor_id="alice")
        state.add_floor("kat-1", "alice")
        state.add_floor("kat-2", "bob")

        restored = CRDTBuildingState.from_dict(state.to_dict())
        assert restored.get_field("height_m") == 30.0
        assert restored.floors == {"kat-1", "kat-2"}
        assert restored.building_key == "bina-a"


class TestHubPersistenceAcrossRestart:
    def test_state_survives_hub_recreation(self, db: ProjectDatabase) -> None:
        auth = AuthService()
        alice = auth.register("alice", "pw1")
        auth.grant_role("proj1", alice.user_id, Role.EDITOR)
        token = auth.login("alice", "pw1").token

        hub1 = CollaborationHub(auth, db=db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj1", building_key="bina-a")
        hub1.apply_field_edit(
            conn1,
            project_id="proj1",
            building_key="bina-a",
            field_name="height_m",
            value=42.0,
            timestamp=1.0,
        )
        hub1.apply_add_floor(conn1, project_id="proj1", building_key="bina-a", floor_id="kat-1")

        # "sunucu yeniden baslatildi" - yeni bir CollaborationHub, ayni db
        hub2 = CollaborationHub(auth, db=db)
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj1", building_key="bina-a")

        assert room2.state.get_field("height_m") == 42.0
        assert "kat-1" in room2.state.floors

    def test_state_without_db_does_not_persist(self) -> None:
        """`db` verilmezse eski davranis (bellek-ici, kalicisiz)
        tamamen korunur - geriye uyumluluk."""
        auth = AuthService()
        alice = auth.register("alice", "pw1")
        auth.grant_role("proj1", alice.user_id, Role.EDITOR)
        token = auth.login("alice", "pw1").token

        hub1 = CollaborationHub(auth)  # db=None
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj1", building_key="bina-a")
        hub1.apply_field_edit(
            conn1,
            project_id="proj1",
            building_key="bina-a",
            field_name="height_m",
            value=42.0,
            timestamp=1.0,
        )

        hub2 = CollaborationHub(auth)  # yeni hub, hicbir db paylasilmiyor
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj1", building_key="bina-a")
        assert room2.state.get_field("height_m") is None

    def test_remove_floor_persists(self, db: ProjectDatabase) -> None:
        auth = AuthService()
        alice = auth.register("alice", "pw1")
        auth.grant_role("proj1", alice.user_id, Role.EDITOR)
        token = auth.login("alice", "pw1").token

        hub1 = CollaborationHub(auth, db=db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj1", building_key="bina-a")
        hub1.apply_add_floor(conn1, project_id="proj1", building_key="bina-a", floor_id="kat-1")
        hub1.apply_add_floor(conn1, project_id="proj1", building_key="bina-a", floor_id="kat-2")
        hub1.apply_remove_floor(conn1, project_id="proj1", building_key="bina-a", floor_id="kat-1")

        hub2 = CollaborationHub(auth, db=db)
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj1", building_key="bina-a")
        assert room2.state.floors == {"kat-2"}

    def test_two_rooms_persist_independently(self, db: ProjectDatabase) -> None:
        auth = AuthService()
        alice = auth.register("alice", "pw1")
        auth.grant_role("proj1", alice.user_id, Role.EDITOR)
        token = auth.login("alice", "pw1").token

        hub1 = CollaborationHub(auth, db=db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj1", building_key="bina-a")
        hub1.apply_field_edit(
            conn1,
            project_id="proj1",
            building_key="bina-a",
            field_name="height_m",
            value=10.0,
            timestamp=1.0,
        )
        conn2 = hub1.router.connect()
        hub1.join(conn2, token=token, project_id="proj1", building_key="bina-b")
        hub1.apply_field_edit(
            conn2,
            project_id="proj1",
            building_key="bina-b",
            field_name="height_m",
            value=20.0,
            timestamp=1.0,
        )

        hub2 = CollaborationHub(auth, db=db)
        connA = hub2.router.connect()
        connB = hub2.router.connect()
        room_a = hub2.join(connA, token=token, project_id="proj1", building_key="bina-a")
        room_b = hub2.join(connB, token=token, project_id="proj1", building_key="bina-b")

        assert room_a.state.get_field("height_m") == 10.0
        assert room_b.state.get_field("height_m") == 20.0
