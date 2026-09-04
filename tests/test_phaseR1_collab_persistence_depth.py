"""Roadmap V4 - Track R / R1: D18'in tam derinlikte tamamlanması —
Collaboration <-> Persistence gerçek entegrasyonu (izlenebilir test).

**Bulgu (bu oturumda doğrulandı):** V4'ün denetim özeti, `collab_session.py`/
`crdt.py` içinde doğrudan `persistence`/`db_backend` import'u bulunamadığını
belirtiyordu. Ancak kod incelemesinde `CollaborationHub.__init__`'in zaten
`db: Optional[ProjectDatabase]` parametresi aldığı, `_persist_room()` /
`_load_persisted_state()` metodlarıyla `ProjectDatabase.save_object()` /
`load_object()`'e gerçekten bağlandığı ve bunun `tests/test_phaseD18_collab_
persistence.py` ile (7 test, hepsi yeşil) zaten kanıtlandığı görüldü.

Yani R1'in **işlevsel kabul kriteri zaten karşılanmıştı** — denetim
raporundaki bulgu, bu oturumdaki kod tabanı için güncel değildi. Bu dosya,
V4'ün kendi R1 kabul kriterini ("tüm `LWWRegister` alanları + `ORSet`
üyeleri kayıpsız geri yüklenir") isimlendirilmiş, izlenebilir ve azami
derinlikte bir testle **ayrıca ve açıkça** kanıtlar — mevcut D18 testine
ek, onun yerine geçmez.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from harita.collaboration.auth import AuthService, Role
from harita.collaboration.collab_session import CollaborationHub
from harita.persistence.db_backend import ProjectDatabase
from harita.persistence.project_format import ProjectManifest


@pytest.fixture()
def db(tmp_path: Path) -> ProjectDatabase:
    path = tmp_path / "r1_collab.hproj"
    manifest = ProjectManifest(name="R1CollabTest", project_id="proj-r1")
    database = ProjectDatabase.create(path, manifest)
    yield database
    database.close()


def _make_hub(db: ProjectDatabase) -> tuple[CollaborationHub, AuthService, str]:
    auth = AuthService()
    user = auth.register("alice", "pw1")
    auth.grant_role("proj-r1", user.user_id, Role.EDITOR)
    token = auth.login("alice", "pw1").token
    return CollaborationHub(auth, db=db), auth, token


class TestR1FullDepthPersistenceAcrossRestart:
    """V4/R1 kabul kriteri: sunucu ortasında yeniden başlatılır, TÜM
    LWWRegister alanları + TÜM ORSet üyeleri (ekleme + çıkarma dahil)
    kayıpsız geri yüklenir."""

    def test_multiple_lww_fields_survive_restart(self, db: ProjectDatabase) -> None:
        hub1, auth, token = _make_hub(db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj-r1", building_key="bina-r1")

        # Birden fazla LWWRegister alanı - hepsi ayrı ayrı serileştirilip
        # geri yüklenmeli.
        fields = {
            "height_m": 55.5,
            "floor_count": 12,
            "roof_type": "hip",
            "facade_material": "brick",
            "is_landmark": True,
        }
        for i, (name, value) in enumerate(fields.items()):
            hub1.apply_field_edit(
                conn1,
                project_id="proj-r1",
                building_key="bina-r1",
                field_name=name,
                value=value,
                timestamp=float(i + 1),
            )

        # "Sunucu yeniden başlatılır" - yepyeni bir CollaborationHub, aynı DB.
        hub2 = CollaborationHub(auth, db=db)
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj-r1", building_key="bina-r1")

        for name, expected in fields.items():
            assert room2.state.get_field(name) == expected, (
                f"alan '{name}' yeniden başlatma sonrası kayıp/yanlış: "
                f"beklenen={expected!r} bulunan={room2.state.get_field(name)!r}"
            )

    def test_all_orset_members_including_removals_survive_restart(
        self, db: ProjectDatabase
    ) -> None:
        hub1, auth, token = _make_hub(db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj-r1", building_key="bina-r1")

        # 5 kat ekle, 2'sini çıkar - ORSet'in add+tombstone mekanizmasının
        # tamamı diskten doğru geri yüklenmeli.
        for floor_id in ["zemin", "kat-1", "kat-2", "kat-3", "cati"]:
            hub1.apply_add_floor(
                conn1,
                project_id="proj-r1",
                building_key="bina-r1",
                floor_id=floor_id,
            )
        hub1.apply_remove_floor(
            conn1,
            project_id="proj-r1",
            building_key="bina-r1",
            floor_id="kat-1",
        )
        hub1.apply_remove_floor(
            conn1,
            project_id="proj-r1",
            building_key="bina-r1",
            floor_id="kat-3",
        )

        hub2 = CollaborationHub(auth, db=db)
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj-r1", building_key="bina-r1")

        assert room2.state.floors == {"zemin", "kat-2", "cati"}
        # Çıkarılan katlar KESİNLİKLE geri gelmemeli (tombstone kalıcı).
        assert "kat-1" not in room2.state.floors
        assert "kat-3" not in room2.state.floors

    def test_re_add_after_remove_survives_restart(self, db: ProjectDatabase) -> None:
        """ORSet'in en incelikli davranışı: bir öğe çıkarılıp yeni bir
        tag ile tekrar eklenirse, restart sonrası hâlâ mevcut olmalı
        (eski tombstone yeni tag'i etkilememeli)."""
        hub1, auth, token = _make_hub(db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj-r1", building_key="bina-r1")

        hub1.apply_add_floor(conn1, project_id="proj-r1", building_key="bina-r1", floor_id="kat-x")
        hub1.apply_remove_floor(
            conn1, project_id="proj-r1", building_key="bina-r1", floor_id="kat-x"
        )
        hub1.apply_add_floor(conn1, project_id="proj-r1", building_key="bina-r1", floor_id="kat-x")

        hub2 = CollaborationHub(auth, db=db)
        conn2 = hub2.router.connect()
        room2 = hub2.join(conn2, token=token, project_id="proj-r1", building_key="bina-r1")

        assert "kat-x" in room2.state.floors

    def test_multiple_restarts_in_sequence_preserve_state(self, db: ProjectDatabase) -> None:
        """Birden fazla ardışık 'yeniden başlatma' - her seferinde durum
        korunmalı, kademeli veri kaybı olmamalı."""
        hub1, auth, token = _make_hub(db)
        conn1 = hub1.router.connect()
        hub1.join(conn1, token=token, project_id="proj-r1", building_key="bina-r1")
        hub1.apply_field_edit(
            conn1,
            project_id="proj-r1",
            building_key="bina-r1",
            field_name="v",
            value=1,
            timestamp=1.0,
        )

        for restart_value in (2, 3, 4):
            hub_n = CollaborationHub(auth, db=db)
            conn_n = hub_n.router.connect()
            room_n = hub_n.join(conn_n, token=token, project_id="proj-r1", building_key="bina-r1")
            assert room_n.state.get_field("v") == restart_value - 1
            hub_n.apply_field_edit(
                conn_n,
                project_id="proj-r1",
                building_key="bina-r1",
                field_name="v",
                value=restart_value,
                timestamp=float(restart_value),
            )

        hub_final = CollaborationHub(auth, db=db)
        conn_final = hub_final.router.connect()
        room_final = hub_final.join(
            conn_final, token=token, project_id="proj-r1", building_key="bina-r1"
        )
        assert room_final.state.get_field("v") == 4
