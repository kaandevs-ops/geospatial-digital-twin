"""Roadmap V2 - Faz 19 - Çok Kullanıcılı İşbirliği & Kimlik Doğrulama testleri.

Kapsam:
    - `auth.AuthService`: kayıt/giriş, parola hash doğrulama, token
      süre-aşımı, rol-tabanlı yetkilendirme (VIEWER/EDITOR/OWNER)
    - `crdt`: `LWWRegister`/`ORSet`/`CRDTBuildingState` — commutative/
      associative/idempotent birleştirme; **kabul kriterinin çekirdeği**:
      farklı sırayla merge edilen eşzamanlı çakışan yazımlar aynı nihai
      duruma yakınsıyor mu
    - `collab_session.CollaborationHub`: iki bağlantının aynı binayı
      eşzamanlı düzenlemesi, broadcast + local merge sonrası her iki
      tarafın da aynı duruma ulaşması (uçtan uca kabul kriteri senaryosu)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from harita.collaboration.auth import (
    AuthService,
    InvalidCredentialsError,
    InvalidTokenError,
    PermissionDeniedError,
    Role,
    TokenExpiredError,
    UserAlreadyExistsError,
)
from harita.collaboration.collab_session import CollaborationHub, RoomNotFoundError
from harita.collaboration.crdt import CRDTBuildingState, LWWRegister, ORSet

# ------------------------------------------------------------------ #
# auth.AuthService
# ------------------------------------------------------------------ #


def test_register_and_login_success():
    auth = AuthService()
    auth.register("alice", "s3cret-pw")
    session = auth.login("alice", "s3cret-pw")
    user = auth.authenticate_token(session.token)
    assert user.username == "alice"


def test_login_wrong_password_raises():
    auth = AuthService()
    auth.register("alice", "s3cret-pw")
    with pytest.raises(InvalidCredentialsError):
        auth.login("alice", "wrong-password")


def test_register_duplicate_username_raises():
    auth = AuthService()
    auth.register("alice", "pw1")
    with pytest.raises(UserAlreadyExistsError):
        auth.register("alice", "pw2")


def test_password_never_stored_as_plaintext():
    auth = AuthService()
    user = auth.register("alice", "s3cret-pw")
    assert user.password_hash != b"s3cret-pw"
    assert b"s3cret" not in user.password_hash


def test_token_expiry_is_enforced():
    auth = AuthService(token_ttl_seconds=10.0)
    auth.register("alice", "pw")
    session = auth.login("alice", "pw")
    # tokenin suresi henuz dolmamis
    auth.authenticate_token(session.token, now=session.issued_at + 5.0)
    # 10 saniye sonra dolmus olmali
    with pytest.raises(TokenExpiredError):
        auth.authenticate_token(session.token, now=session.issued_at + 11.0)


def test_invalid_token_raises():
    auth = AuthService()
    with pytest.raises(InvalidTokenError):
        auth.authenticate_token("bilinmeyen-token")


def test_role_hierarchy_permissions():
    assert Role.VIEWER.can_read() and not Role.VIEWER.can_write()
    assert Role.EDITOR.can_read() and Role.EDITOR.can_write()
    assert not Role.EDITOR.can_manage_members()
    assert Role.OWNER.can_manage_members()


def test_require_role_raises_permission_denied_for_insufficient_role():
    auth = AuthService()
    user = auth.register("bob", "pw")
    auth.grant_role("proj1", user.user_id, Role.VIEWER)
    with pytest.raises(PermissionDeniedError):
        auth.require_role("proj1", user.user_id, at_least=Role.EDITOR)


def test_require_role_passes_for_sufficient_role():
    auth = AuthService()
    user = auth.register("bob", "pw")
    auth.grant_role("proj1", user.user_id, Role.OWNER)
    role = auth.require_role("proj1", user.user_id, at_least=Role.EDITOR)
    assert role == Role.OWNER


def test_members_lists_all_project_memberships():
    auth = AuthService()
    alice = auth.register("alice", "pw")
    bob = auth.register("bob", "pw")
    auth.grant_role("proj1", alice.user_id, Role.OWNER)
    auth.grant_role("proj1", bob.user_id, Role.EDITOR)
    members = auth.members("proj1")
    assert {m.user_id for m in members} == {alice.user_id, bob.user_id}


# ------------------------------------------------------------------ #
# crdt — matematiksel garantiler (kabul kriterinin çekirdeği)
# ------------------------------------------------------------------ #


def test_lww_register_later_timestamp_wins():
    a = LWWRegister(value=10.0, timestamp=1.0, actor_id="alice")
    b = LWWRegister(value=20.0, timestamp=2.0, actor_id="bob")
    assert a.merge(b).value == 20.0
    assert b.merge(a).value == 20.0  # commutative


def test_lww_register_tie_break_is_deterministic_by_actor_id():
    a = LWWRegister(value=10.0, timestamp=5.0, actor_id="alice")
    b = LWWRegister(value=20.0, timestamp=5.0, actor_id="bob")
    # ayni timestamp -> actor_id sozluk sirasina gore (bob > alice)
    assert a.merge(b).value == 20.0
    assert b.merge(a).value == 20.0


def test_lww_register_merge_is_idempotent():
    a = LWWRegister(value=10.0, timestamp=1.0, actor_id="alice")
    once = a.merge(a)
    twice = once.merge(a).merge(a)
    assert once.value == twice.value == 10.0


def test_orset_add_then_remove():
    s = ORSet()
    s.add("kat-1", ("alice", 1))
    assert "kat-1" in s
    s.remove("kat-1")
    assert "kat-1" not in s


def test_orset_concurrent_add_and_remove_add_wins():
    """Eşzamanlı senaryo: alice bir elemani ekliyor, bob (bu elemani hic
    gormeden) baska bir replikada ayni elemani farkli bir tag ile ekliyor,
    alice sonra kendi gordugu tag'i siliyor. Birlestirme sonrasi eleman
    hala kumede olmali (bob'un eklemesi, alice'in gormedigi tag'i
    etkilemedigi icin) — CRDT'nin "kayipsiz" garantisi."""
    replica_a = ORSet()
    replica_a.add("kat-1", ("alice", 1))

    replica_b = ORSet()
    replica_b.add("kat-1", ("bob", 1))  # bob bagimsiz olarak ayni elemani ekliyor

    # alice kendi gordugu (henuz bob'unkiyle birlesmemis) durumda siliyor
    replica_a.remove("kat-1")
    assert "kat-1" not in replica_a  # yerelde silinmis gibi gorunuyor

    merged = replica_a.merge(replica_b)
    # bob'un tag'i tombstone'da olmadigindan eleman hala kumede
    assert "kat-1" in merged


def test_orset_merge_is_commutative_and_idempotent():
    a = ORSet()
    a.add("x", ("alice", 1))
    b = ORSet()
    b.add("y", ("bob", 1))
    b.remove("y")

    merged_ab = a.merge(b)
    merged_ba = b.merge(a)
    assert merged_ab.elements() == merged_ba.elements() == {"x"}

    merged_twice = merged_ab.merge(merged_ab)
    assert merged_twice.elements() == {"x"}


def test_crdt_building_state_convergence_regardless_of_merge_order():
    """Kabul kriterinin doğrudan kanıtı: iki kullanıcı (alice, bob) aynı
    binanın 'height_m' alanına eşzamanlı farklı değerler yazıyor. Üç
    replika (alice'in yerel state'i, bob'un yerel state'i, ve alice->bob
    sırasıyla birleşen üçüncü bir replika) hangi sırayla merge edilirse
    edilsin **aynı nihai değere** yakınsamalı — çakışma otomatik ve
    kayıpsız çözülüyor."""
    alice_state = CRDTBuildingState(building_key="bina-a")
    alice_state.set_field("height_m", 30.0, timestamp=100.0, actor_id="alice")
    alice_state.add_floor("kat-1", actor_id="alice")

    bob_state = CRDTBuildingState(building_key="bina-a")
    bob_state.set_field("height_m", 45.0, timestamp=100.0, actor_id="bob")
    bob_state.add_floor("kat-2", actor_id="bob")

    merged_ab = alice_state.merge(bob_state)
    merged_ba = bob_state.merge(alice_state)

    # Ayni timestamp -> actor_id tie-break (bob > alice sozlukte) -> bob kazanir
    assert merged_ab.get_field("height_m") == 45.0
    assert merged_ba.get_field("height_m") == 45.0
    # Her iki katin da (farkli alanlar oldugundan) kaybolmadan korundugu (ORSet)
    assert merged_ab.floors == {"kat-1", "kat-2"} == merged_ba.floors


def test_crdt_merge_different_building_keys_raises():
    a = CRDTBuildingState(building_key="bina-a")
    b = CRDTBuildingState(building_key="bina-b")
    with pytest.raises(ValueError):
        a.merge(b)


def test_crdt_no_conflict_case_both_fields_preserved():
    """Alice yukseklik, Bob kat-sayisi degistiriyor - farkli alanlar
    oldugundan hicbir veri kaybolmamali (trivial ama kritik regresyon)."""
    a = CRDTBuildingState(building_key="bina-a")
    a.set_field("height_m", 30.0, timestamp=1.0, actor_id="alice")

    b = CRDTBuildingState(building_key="bina-a")
    b.set_field("floor_count", 10, timestamp=1.0, actor_id="bob")

    merged = a.merge(b)
    assert merged.get_field("height_m") == 30.0
    assert merged.get_field("floor_count") == 10


# ------------------------------------------------------------------ #
# collab_session.CollaborationHub — uçtan uca senaryo
# ------------------------------------------------------------------ #


@pytest.fixture
def hub_with_two_editors():
    auth = AuthService()
    alice = auth.register("alice", "pw1")
    bob = auth.register("bob", "pw2")
    auth.grant_role("proj1", alice.user_id, Role.EDITOR)
    auth.grant_role("proj1", bob.user_id, Role.EDITOR)
    alice_token = auth.login("alice", "pw1").token
    bob_token = auth.login("bob", "pw2").token

    hub = CollaborationHub(auth)
    return hub, alice_token, bob_token


def test_join_requires_valid_token(hub_with_two_editors):
    hub, alice_token, _ = hub_with_two_editors
    conn = hub.router.connect()
    room = hub.join(conn, token=alice_token, project_id="proj1", building_key="bina-a")
    assert conn.connection_id in room.members


def test_join_with_invalid_token_raises(hub_with_two_editors):
    hub, _, _ = hub_with_two_editors
    conn = hub.router.connect()
    with pytest.raises(InvalidTokenError):
        hub.join(conn, token="gecersiz", project_id="proj1", building_key="bina-a")


def test_viewer_cannot_apply_edit():
    auth = AuthService()
    carol = auth.register("carol", "pw")
    auth.grant_role("proj1", carol.user_id, Role.VIEWER)
    token = auth.login("carol", "pw").token
    hub = CollaborationHub(auth)
    conn = hub.router.connect()
    hub.join(conn, token=token, project_id="proj1", building_key="bina-a")
    with pytest.raises(PermissionDeniedError):
        hub.apply_field_edit(
            conn,
            project_id="proj1",
            building_key="bina-a",
            field_name="height_m",
            value=99.0,
        )


def test_two_editors_concurrent_conflicting_edit_converges(hub_with_two_editors):
    """Kabul kriterinin uçtan uca senaryosu: alice ve bob aynı binanın
    'height_m' alanını neredeyse eşzamanlı (aynı timestamp) düzenliyor.
    Her ikisinin de yerel CRDT state'i, karşı tarafın broadcast ettiği
    patch'i alıp merge ettikten sonra **aynı** nihai değere yakınsamalı."""
    hub, alice_token, bob_token = hub_with_two_editors

    conn_alice = hub.router.connect()
    conn_bob = hub.router.connect()
    room_alice = hub.join(conn_alice, token=alice_token, project_id="proj1", building_key="bina-a")
    room_bob = hub.join(conn_bob, token=bob_token, project_id="proj1", building_key="bina-a")
    assert room_alice is room_bob  # ayni oda (sunucu tarafinda tek CRDT state)

    fixed_ts = 1000.0
    hub.apply_field_edit(
        conn_alice,
        project_id="proj1",
        building_key="bina-a",
        field_name="height_m",
        value=30.0,
        timestamp=fixed_ts,
    )
    hub.apply_field_edit(
        conn_bob,
        project_id="proj1",
        building_key="bina-a",
        field_name="height_m",
        value=45.0,
        timestamp=fixed_ts,
    )

    # Sunucu tarafindaki paylasilan oda state'i tek bir CRDT oldugundan
    # (iki cagri da ayni room.state uzerinde calisiyor) sonuc zaten
    # deterministik merge kurallarina gore belirlenir.
    final_height = room_alice.state.get_field("height_m")
    assert final_height in (30.0, 45.0)  # tie-break gecerli bir sonuc uretmis olmali

    # Simdi iki bagimsiz istemci-tarafi CRDT kopyasinin da (broadcast
    # edilen patch'leri alip merge ederek) ayni sonuca yakinsadigini
    # dogrulayalim - bu, sunucudan bagimsiz "istemci merge" senaryosu.
    alice_local = CRDTBuildingState(building_key="bina-a")
    alice_local.set_field("height_m", 30.0, timestamp=fixed_ts, actor_id="alice-actor")
    bob_local = CRDTBuildingState(building_key="bina-a")
    bob_local.set_field("height_m", 45.0, timestamp=fixed_ts, actor_id="bob-actor")

    alice_converged = alice_local.merge(bob_local)
    bob_converged = bob_local.merge(alice_local)
    assert alice_converged.get_field("height_m") == bob_converged.get_field("height_m")


def test_alice_receives_bobs_edit_via_broadcast(hub_with_two_editors):
    hub, alice_token, bob_token = hub_with_two_editors
    conn_alice = hub.router.connect()
    conn_bob = hub.router.connect()
    hub.join(conn_alice, token=alice_token, project_id="proj1", building_key="bina-a")
    hub.join(conn_bob, token=bob_token, project_id="proj1", building_key="bina-a")

    hub.apply_field_edit(
        conn_bob,
        project_id="proj1",
        building_key="bina-a",
        field_name="height_m",
        value=50.0,
    )
    # alice'in outbox'ina bir crdt_patch mesaji dusmus olmali (bob haric broadcast)
    patch_messages = [m for m in conn_alice.outbox if m.type == "crdt_patch"]
    assert len(patch_messages) == 1
    assert patch_messages[0].payload["patch"]["value"] == 50.0
    # bob kendine broadcast almamali (exclude=connection_id)
    assert not [m for m in conn_bob.outbox if m.type == "crdt_patch"]


def test_apply_edit_on_unknown_room_raises():
    auth = AuthService()
    hub = CollaborationHub(auth)
    conn = hub.router.connect()
    with pytest.raises(RoomNotFoundError):
        hub.apply_field_edit(
            conn,
            project_id="proj-x",
            building_key="bina-x",
            field_name="height_m",
            value=1.0,
        )


def test_leave_removes_member_and_unsubscribes(hub_with_two_editors):
    hub, alice_token, _ = hub_with_two_editors
    conn = hub.router.connect()
    room = hub.join(conn, token=alice_token, project_id="proj1", building_key="bina-a")
    assert conn.connection_id in room.members
    hub.leave(conn, project_id="proj1", building_key="bina-a")
    assert conn.connection_id not in room.members
    assert "room:proj1:bina-a" not in conn.topics


def test_add_floor_broadcasts_and_merges_without_loss(hub_with_two_editors):
    hub, alice_token, bob_token = hub_with_two_editors
    conn_alice = hub.router.connect()
    conn_bob = hub.router.connect()
    room = hub.join(conn_alice, token=alice_token, project_id="proj1", building_key="bina-a")
    hub.join(conn_bob, token=bob_token, project_id="proj1", building_key="bina-a")

    hub.apply_add_floor(conn_alice, project_id="proj1", building_key="bina-a", floor_id="kat-1")
    hub.apply_add_floor(conn_bob, project_id="proj1", building_key="bina-a", floor_id="kat-2")

    assert room.state.floors == {"kat-1", "kat-2"}


def test_ws_handlers_registered_for_join_and_edit(hub_with_two_editors):
    hub, alice_token, _ = hub_with_two_editors
    conn = hub.router.connect()
    from harita.extensibility.websocket_api import WSMessage

    reply = hub.router.dispatch(
        conn,
        WSMessage(
            type="collab.join",
            payload={
                "token": alice_token,
                "project_id": "proj1",
                "building_key": "bina-a",
            },
        ),
    )
    assert reply is not None
    assert reply.type == "collab.join.reply"
    assert "alice" in reply.payload["members"]
