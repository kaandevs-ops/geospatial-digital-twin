"""Roadmap V4 - Track E / Faz E8: Collaboration - Gerçek WebSocket Sunucusu
ve Çok-Kullanıcılı Yük Testi.

Bu dosya roadmap'in kendi kabul kriterini doğrudan kanıtlar: **10+
eşzamanlı gerçek WebSocket istemcisi** aynı binayı düzenler ve CRDT
birleşmesi **gerçek bir TCP soketi + asyncio event loop üzerinden**
(önceki oturumlarda yalnızca in-process `router.dispatch()` ile test
edilenin aksine) kayıpsız yakınsar.

Önemli tasarım notu — mesaj çoklamas (demultiplexing): Bir istemcinin
soketine, kendi isteğinin yanıtı (`*.reply`) İLE başka istemcilerin
düzenlemelerinden gelen yayın (`crdt_patch`) mesajları **karışık sırayla**
gelebilir (örn. B düzenleme yaparken A henüz kendi düzenlemesini
göndermemişse, A'nın soketinde B'nin yayını, A'nın kendi yanıtından
*önce* sıraya girebilir). Bu yüzden `_recv_typed()` yardımcı fonksiyonu,
istenen tipte bir mesaj gelene kadar okumaya devam eder ve eşleşmeyen
mesajları istemci başına bir arabellekte (`client._pending`) saklar —
gerçek bir istemci uygulamasının yapacağı gibi (mesaj tipine göre
yönlendirme), sıraya bağımlı kırılgan bir varsayım yerine.

Üç senaryo:
    1. 12 gerçek istemci gerçek bir el sıkışmayla bağlanır, odaya katılır
       ve her biri **farklı** bir alanı düzenler - sunucudaki nihai
       `CRDTBuildingState`'in 12 alanın tamamını doğru değerlerle
       içerdiği ve her istemcinin diğer 11 düzenleme için gerçek soketten
       bir `crdt_patch` aldığı doğrulanır (temel yayın doğruluğu + yük).
    2. İki istemci **aynı alana eşzamanlı çakışan** yazımlar gönderir;
       sunucudaki nihai değer tek/tutarlıdır ve her iki istemcinin de
       (kendi yazdığı + karşı taraftan gerçek soketten aldığı patch'i
       merge ederek) bağımsız olarak **aynı** nihai değere ulaştığı
       doğrudan kanıtlanır (CRDT'nin sıra-bağımsız birleşme garantisi).
    3. Rol tabanlı yetkilendirme (Faz 19) gerçek soket üzerinden de
       bozulmadan çalışır (VIEWER `collab.edit` gönderemez).
"""

from __future__ import annotations

import asyncio

from harita.collaboration.auth import AuthService, Role
from harita.collaboration.collab_session import CollaborationHub
from harita.collaboration.crdt import CRDTBuildingState
from harita.collaboration.ws_server import CollaborationWebSocketServer, _MinimalWSClient
from harita.extensibility.websocket_api import WSMessage

PROJECT_ID = "proj-e8"
BUILDING_KEY = "bina-e8"
N_CLIENTS = 12  # roadmap kabul kriteri: "10+"


def _make_hub_with_users(n: int) -> tuple[CollaborationHub, list[str]]:
    auth = AuthService()
    tokens: list[str] = []
    for i in range(n):
        username = f"user{i}"
        auth.register(username, "pw")
        user_id = auth.login(username, "pw").user_id
        auth.grant_role(PROJECT_ID, user_id, Role.EDITOR)
        tokens.append(auth.login(username, "pw").token)
    hub = CollaborationHub(auth)
    return hub, tokens


async def _recv_typed(
    client: _MinimalWSClient, want_type: str, *, timeout: float = 5.0
) -> WSMessage:
    """İstenen `want_type`'ta bir mesaj gelene kadar okur; eşleşmeyen
    mesajları `client._pending`'e (istemci başına arabellek) saklar.
    Bkz. modül docstring'i - gerçek ağda mesaj karışık sırayla gelebilir."""
    pending: list[WSMessage] = getattr(client, "_pending", None)
    if pending is None:
        pending = []
        client._pending = pending  # type: ignore[attr-defined]

    for i, msg in enumerate(pending):
        if msg.type == want_type:
            return pending.pop(i)

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError(f"'{want_type}' tipinde mesaj {timeout}s icinde gelmedi")
        msg = await asyncio.wait_for(client.recv(), timeout=remaining)
        if msg.type == want_type:
            return msg
        pending.append(msg)


async def _join(client: _MinimalWSClient, token: str) -> WSMessage:
    await client.send(
        WSMessage(
            type="collab.join",
            payload={"token": token, "project_id": PROJECT_ID, "building_key": BUILDING_KEY},
        )
    )
    return await _recv_typed(client, "collab.join.reply")


async def _edit(client: _MinimalWSClient, field: str, value: object) -> WSMessage:
    await client.send(
        WSMessage(
            type="collab.edit",
            payload={
                "project_id": PROJECT_ID,
                "building_key": BUILDING_KEY,
                "field": field,
                "value": value,
            },
        )
    )
    return await _recv_typed(client, "collab.edit.reply")


async def _drain_pending_crdt_patches(
    client: _MinimalWSClient, *, quiet_period: float = 0.5
) -> list[dict]:
    """Kalan tüm `crdt_patch` mesajlarını (arabellek + soketten yeni
    gelenler, `quiet_period` sessizlik penceresi bitene kadar) toplar."""
    pending: list[WSMessage] = getattr(client, "_pending", None) or []
    patches = [m.payload["patch"] for m in pending if m.type == "crdt_patch"]
    client._pending = [m for m in pending if m.type != "crdt_patch"]  # type: ignore[attr-defined]
    try:
        while True:
            msg = await asyncio.wait_for(client.recv(), timeout=quiet_period)
            if msg.type == "crdt_patch":
                patches.append(msg.payload["patch"])
            else:
                client._pending.append(msg)  # type: ignore[attr-defined]
    except asyncio.TimeoutError:
        pass
    return patches


def test_twelve_real_clients_distinct_fields_converge() -> None:
    """E8 kabul kriteri (bacak 1+2): 12 gerçek WebSocket istemcisi bağlanır,
    her biri farklı bir alanı düzenler; sunucu tarafında nihai durum tüm
    alanları doğru içerir ve her istemci diğer 11 düzenleme için gerçek
    soketten broadcast mesajı alır."""

    async def _scenario() -> None:
        hub, tokens = _make_hub_with_users(N_CLIENTS)
        server = CollaborationWebSocketServer(hub, port=0)
        await server.start()
        clients: list[_MinimalWSClient] = []
        try:
            for _ in tokens:
                clients.append(await _MinimalWSClient.connect(server.host, server.actual_port))
            for client, token in zip(clients, tokens):
                join_reply = await _join(client, token)
                assert join_reply.type == "collab.join.reply"

            for i, client in enumerate(clients):
                await _edit(client, f"field_{i}", f"value_{i}")

            room = hub._room(PROJECT_ID, BUILDING_KEY)  # noqa: SLF001 - test icin dogrudan erisim
            assert len(room.state.fields) == N_CLIENTS
            for i in range(N_CLIENTS):
                assert room.state.get_field(f"field_{i}") == f"value_{i}"

            for client in clients:
                patches = await _drain_pending_crdt_patches(client)
                assert len(patches) == N_CLIENTS - 1, (
                    f"beklenen {N_CLIENTS - 1} gercek soket broadcast'i, alinan {len(patches)}"
                )
        finally:
            for client in clients:
                await client.close()
            await server.stop()

    asyncio.run(_scenario())


def test_concurrent_conflicting_edit_converges_deterministically_over_real_sockets() -> None:
    """E8 kabul kriteri (bacak 3): iki gerçek istemci aynı alana eşzamanlı
    çakışan yazımlar gönderir; sunucudaki nihai değer tek/tutarlıdır ve
    her istemcinin kendi yazdığı + karşı taraftan gerçek soketten aldığı
    patch'i merge etmesi, varış sırasından bağımsız olarak aynı nihai
    değere yakınsar."""

    async def _scenario() -> None:
        hub, tokens = _make_hub_with_users(2)
        server = CollaborationWebSocketServer(hub, port=0)
        await server.start()
        clients: list[_MinimalWSClient] = []
        try:
            for _ in tokens:
                clients.append(await _MinimalWSClient.connect(server.host, server.actual_port))
            client_a, client_b = clients
            for client, token in zip(clients, tokens):
                await _join(client, token)

            # Gercekten eszamanli (ayni event loop turunda baslatilan)
            # cakisan yazimlar - hangisinin sunucuya once ulastigi gercek
            # TCP/soket zamanlamasina bagli, onceden bilinmiyor.
            await asyncio.gather(
                _edit(client_a, "height_m", 10.0),
                _edit(client_b, "height_m", 99.0),
            )

            room = hub._room(PROJECT_ID, BUILDING_KEY)  # noqa: SLF001
            final_value = room.state.get_field("height_m")
            assert final_value in (10.0, 99.0), "bozulmus/karisik deger"

            patches_a = await _drain_pending_crdt_patches(client_a)
            patches_b = await _drain_pending_crdt_patches(client_b)
            assert len(patches_a) == 1 and len(patches_b) == 1, (
                "her istemci digerinin TEK patch'ini gercek soketten almali "
                f"(A={len(patches_a)}, B={len(patches_b)})"
            )
            patch_from_b_to_a = patches_a[0]  # B'nin yazdigi, A'ya yayinlanan gercek patch
            patch_from_a_to_b = patches_b[0]  # A'nin yazdigi, B'ye yayinlanan gercek patch

            # A: kendi yazdigi deger (B'ye yayinlanan hali uzerinden, ayni
            # patch'in gercek kimligi - timestamp/actor_id) + B'den gelen
            # gercek patch merge edilir.
            local_a = CRDTBuildingState(building_key=BUILDING_KEY)
            local_a.set_field(
                patch_from_a_to_b["field"],
                patch_from_a_to_b["value"],
                patch_from_a_to_b["timestamp"],
                patch_from_a_to_b["actor_id"],
            )
            local_a.set_field(
                patch_from_b_to_a["field"],
                patch_from_b_to_a["value"],
                patch_from_b_to_a["timestamp"],
                patch_from_b_to_a["actor_id"],
            )

            local_b = CRDTBuildingState(building_key=BUILDING_KEY)
            local_b.set_field(
                patch_from_b_to_a["field"],
                patch_from_b_to_a["value"],
                patch_from_b_to_a["timestamp"],
                patch_from_b_to_a["actor_id"],
            )
            local_b.set_field(
                patch_from_a_to_b["field"],
                patch_from_a_to_b["value"],
                patch_from_a_to_b["timestamp"],
                patch_from_a_to_b["actor_id"],
            )

            assert local_a.get_field("height_m") == local_b.get_field("height_m"), (
                "CRDT yakinsamadi: A ve B farkli nihai degerlere ulasti "
                f"(A={local_a.get_field('height_m')!r}, B={local_b.get_field('height_m')!r})"
            )
            assert local_a.get_field("height_m") == final_value, (
                "istemci-tarafi yakinsanan deger, sunucunun otoriter degeriyle tutarsiz"
            )
        finally:
            for client in clients:
                await client.close()
            await server.stop()

    asyncio.run(_scenario())


def test_server_rejects_non_editor_write_over_real_socket() -> None:
    """Gerçek soket üzerinden de rol tabanlı yetkilendirme (Faz 19/auth)
    bozulmadan çalışır: VIEWER rolündeki bir istemci `collab.edit`
    gönderirse gerçek ağdan bir `error` mesajı alır, sunucu çökmez."""

    async def _scenario() -> None:
        auth = AuthService()
        auth.register("viewer_user", "pw")
        viewer = auth.login("viewer_user", "pw")
        auth.grant_role(PROJECT_ID, viewer.user_id, Role.VIEWER)
        hub = CollaborationHub(auth)
        server = CollaborationWebSocketServer(hub, port=0)
        await server.start()
        client = await _MinimalWSClient.connect(server.host, server.actual_port)
        try:
            await _join(client, viewer.token)
            await client.send(
                WSMessage(
                    type="collab.edit",
                    payload={
                        "project_id": PROJECT_ID,
                        "building_key": BUILDING_KEY,
                        "field": "height_m",
                        "value": 5.0,
                    },
                )
            )
            reply = await _recv_typed(client, "error")
            assert "yetki" in str(reply.payload).lower(), (
                f"beklenmeyen hata mesaji: {reply.payload!r}"
            )
        finally:
            await client.close()
            await server.stop()

    asyncio.run(_scenario())


def test_acceptance_criterion_ten_plus_real_clients_documented() -> None:
    """Roadmap V4/E8 kabul kriterinin sayısal olarak >=10 gerçek eşzamanlı
    istemciyle test edildiğini açıkça belgeleyen izlenebilir test."""
    assert N_CLIENTS >= 10
