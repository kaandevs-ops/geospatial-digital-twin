"""
Gerçek WebSocket Sunucusu — ROADMAP_V4 Faz E8
================================================================

**Mevcut kısıt (E8 açılış notu):** `CollaborationHub`, Faz 14'ün
soket-agnostik `WebSocketRouter`'ı üzerine kurulu ama bugüne kadar
yalnızca `router.dispatch(connection, message)` doğrudan Python
çağrılarıyla (in-process, gerçek bir TCP soketi açılmadan) test
edilmişti — roadmap'in "10+ gerçek WebSocket istemcisi" kabul kriteri
hiç kanıtlanmamıştı.

**Bu modülün yaptığı:** stdlib `asyncio` üzerinden RFC 6455 uyumlu
(el sıkışma + metin/ping/pong/kapama çerçeveleme) **gerçek bir soket
dinleyen** sunucu. Üçüncü parti `websockets` paketine **bağımlı değil**
(`[ws]` extra tamamen opsiyonel kalır — bu implementasyon zaten
stdlib-only çalışıyor, extra yalnızca gelecekte performans/uyumluluk
için bir alternatif sağlar, roadmap ilkesiyle tutarlı).

Mimari — mevcut soyutlamalar korunuyor:
    * `CollaborationHub.router` (bir `WebSocketRouter`) hâlâ tek mesaj
      yönlendirme otoritesi — bu sunucu yalnızca gerçek bayt akışını
      `WSMessage`'a çevirip `router.dispatch()`'e besliyor, iş mantığına
      (CRDT/auth/persistence) hiç dokunmuyor.
    * Her gerçek TCP bağlantısı, `WSConnection.send_fn` enjeksiyonuyla
      (zaten var olan enjeksiyon noktası, bkz. `extensibility/
      websocket_api.py`) gerçek soket yazımına bağlanır — `WSConnection`
      sınıfının kendisi hiç değişmedi.

Kapsam dışı bırakılanlar (bilinçli, belgelenmiş):
    * WebSocket compression (permessage-deflate) — nadiren gereken bir
      optimizasyon, karmaşıklığı bu aşamada gerekçesiz.
    * Fragmented (multi-frame) mesajlar — CRDT patch'leri küçük JSON
      nesneleridir, tek çerçevede rahatça sığar; `_read_frame` bunu
      belgeler ve fragmanlı bir çerçeve gelirse açık bir hata fırlatır
      (sessiz veri bozulması yerine).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
from dataclasses import dataclass

from ..extensibility.websocket_api import WSMessage
from .collab_session import CollaborationHub

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

_OPCODE_CONTINUATION = 0x0
_OPCODE_TEXT = 0x1
_OPCODE_BINARY = 0x2
_OPCODE_CLOSE = 0x8
_OPCODE_PING = 0x9
_OPCODE_PONG = 0xA


class WebSocketProtocolError(Exception):
    """El sıkışma veya çerçeveleme (framing) RFC 6455'e uymuyor."""


def compute_accept_key(client_key: str) -> str:
    """RFC 6455 §1.3 — `Sec-WebSocket-Accept` başlığının hesaplanması."""
    digest = hashlib.sha1((client_key + _WS_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(payload: bytes, *, opcode: int = _OPCODE_TEXT, mask: bool = False) -> bytes:
    """Sunucudan istemciye (varsayılan: maskesiz) bir WebSocket çerçevesi
    kodlar. `mask=True`, test yardımcı istemcisinin (`_MinimalWSClient`)
    istemci->sunucu yönünde maskelemesi için kullanılır (RFC 6455 §5.1:
    istemciden sunucuya giden tüm çerçeveler maskelenmelidir)."""
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))  # FIN=1, opcode
    length = len(payload)
    mask_bit = 0x80 if mask else 0x00
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.append(mask_bit | 126)
        header += struct.pack("!H", length)
    else:
        header.append(mask_bit | 127)
        header += struct.pack("!Q", length)
    if mask:
        mask_key = bytes([0x12, 0x34, 0x56, 0x78])  # sabit, test-amaçlı maske
        header += mask_key
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + payload


@dataclass
class _RawFrame:
    opcode: int
    payload: bytes


async def read_frame(reader: asyncio.StreamReader) -> _RawFrame | None:
    """Bir istemci->sunucu (maskelenmiş) WebSocket çerçevesini okur.
    Bağlantı temiz şekilde kapandıysa (EOF) `None` döner."""
    try:
        first_two = await reader.readexactly(2)
    except asyncio.IncompleteReadError:
        return None
    b1, b2 = first_two
    fin = bool(b1 & 0x80)
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F

    if not fin:
        # Bilinçli kapsam dışı (bkz. modül docstring'i): parçalı mesaj
        # sessizce yanlış birleştirilmek yerine açıkça reddedilir.
        raise WebSocketProtocolError(
            "parçalı (fragmented) WebSocket mesajları desteklenmiyor "
            "(bkz. ws_server.py docstring'i, bilinçli kapsam dışı)"
        )
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]

    mask_key = await reader.readexactly(4) if masked else None
    raw_payload = await reader.readexactly(length) if length else b""
    if mask_key is not None:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(raw_payload))
    else:
        payload = raw_payload
    return _RawFrame(opcode=opcode, payload=payload)


async def _perform_handshake(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    """HTTP Upgrade isteğini okur, RFC 6455 el sıkışma yanıtını yazar."""
    request_line = await reader.readline()
    if not request_line:
        raise WebSocketProtocolError("boş istek — bağlantı erken kapandı")

    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"", b"\n"):
            break
        if b":" not in line:
            continue
        key, _, value = line.decode("latin-1").partition(":")
        headers[key.strip().lower()] = value.strip()

    client_key = headers.get("sec-websocket-key")
    if not client_key:
        raise WebSocketProtocolError(
            "Sec-WebSocket-Key başlığı yok — geçerli bir WebSocket el sıkışma isteği değil"
        )
    accept = compute_accept_key(client_key)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    writer.write(response.encode("ascii"))
    await writer.drain()


class CollaborationWebSocketServer:
    """`CollaborationHub`'ı gerçek bir TCP/WebSocket soketi üzerinden
    servis eden asyncio sunucusu.

    Her gerçek istemci bağlantısı bir `WSConnection` ile eşleştirilir
    (`hub.router.connect()`); gelen her metin çerçevesi `WSMessage`'a
    çözülüp `hub.router.dispatch(connection, message)`'e iletilir —
    bu, mevcut `collab.join`/`collab.edit` handler'larının (bkz.
    `collab_session.py::_register_handlers`) **hiç değişmeden** gerçek
    ağ üzerinden çalıştığı anlamına gelir.
    """

    def __init__(self, hub: CollaborationHub, host: str = "127.0.0.1", port: int = 0) -> None:
        self.hub = hub
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None

    @property
    def actual_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("sunucu henüz başlatılmadı")
        return self._server.sockets[0].getsockname()[1]

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_connection, self.host, self.port)
        # port=0 verildiyse işletim sistemi rastgele boş bir port seçer -
        # gerçek atanan portu geri okuyup `self.port`'u güncelliyoruz, böylece
        # aynı nesne yeniden başlatılırsa (`stop()` + `start()`) aynı porta
        # bağlanmaya çalışmaz.
        self.port = self.actual_port

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def serve_forever(self) -> None:
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            await _perform_handshake(reader, writer)
        except WebSocketProtocolError:
            writer.close()
            return

        connection = self.hub.router.connect()

        def _send_fn(message: WSMessage) -> None:
            data = message.to_json().encode("utf-8")
            writer.write(encode_frame(data, opcode=_OPCODE_TEXT, mask=False))

        connection.send_fn = _send_fn

        try:
            while True:
                frame = await read_frame(reader)
                if frame is None:
                    break
                if frame.opcode == _OPCODE_CLOSE:
                    writer.write(encode_frame(frame.payload, opcode=_OPCODE_CLOSE))
                    break
                if frame.opcode == _OPCODE_PING:
                    writer.write(encode_frame(frame.payload, opcode=_OPCODE_PONG))
                    await writer.drain()
                    continue
                if frame.opcode == _OPCODE_PONG:
                    continue
                if frame.opcode != _OPCODE_TEXT:
                    continue  # binary/continuation - bilinçli kapsam dışı

                try:
                    message = WSMessage.from_json(frame.payload.decode("utf-8"))
                except (ValueError, KeyError):
                    connection.send(WSMessage(type="error", payload="geçersiz JSON mesajı"))
                    continue

                try:
                    reply = self.hub.router.dispatch(connection, message)
                except Exception as exc:  # noqa: BLE001 - istemciye hata olarak raporla
                    reply = WSMessage(type="error", payload=str(exc), id=message.id)
                if reply is not None:
                    connection.send(reply)
                await writer.drain()
        except (ConnectionResetError, WebSocketProtocolError):
            pass
        finally:
            self.hub.router.disconnect(connection.connection_id)
            connection.close()
            writer.close()


class _MinimalWSClient:
    """**Yalnızca test/yük-testi amaçlı** minimal RFC 6455 istemcisi.

    Üretim istemcileri tarayıcının yerleşik `WebSocket` API'sini ya da
    (Python tarafı entegrasyon gerekiyorsa) opsiyonel `[ws]` extra'sının
    `websockets` paketini kullanmalı — bu sınıf yalnızca E8'in "10+
    gerçek eşzamanlı istemci" kabul kriterini stdlib-only kanıtlamak
    için var, genel-amaçlı bir istemci kütüphanesi değildir.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls, host: str, port: int) -> _MinimalWSClient:
        reader, writer = await asyncio.open_connection(host, port)
        key = base64.b64encode(b"harita-e8-test-key-0123").decode("ascii")
        request = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()

        status_line = await reader.readline()
        if b"101" not in status_line:
            raise WebSocketProtocolError(f"el sıkışma başarısız: {status_line!r}")
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"", b"\n"):
                break
        return cls(reader, writer)

    async def send(self, message: WSMessage) -> None:
        data = message.to_json().encode("utf-8")
        self._writer.write(encode_frame(data, opcode=_OPCODE_TEXT, mask=True))
        await self._writer.drain()

    async def recv(self) -> WSMessage:
        frame = await read_frame_serverbound_unmasked(self._reader)
        return WSMessage.from_json(frame.payload.decode("utf-8"))

    async def close(self) -> None:
        try:
            self._writer.write(encode_frame(b"", opcode=_OPCODE_CLOSE, mask=True))
            await self._writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        self._writer.close()


async def read_frame_serverbound_unmasked(reader: asyncio.StreamReader) -> _RawFrame:
    """Test istemcisinin sunucudan (maskesiz) çerçeve okuması için
    `read_frame`'in maskesiz varyantı — sunucu->istemci yönünde RFC 6455
    maskeleme zorunlu değildir."""
    first_two = await reader.readexactly(2)
    b1, b2 = first_two
    opcode = b1 & 0x0F
    length = b2 & 0x7F  # b2 & 0x80 (mask biti) sunucu çerçevelerinde 0 olmalı
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    payload = await reader.readexactly(length) if length else b""
    return _RawFrame(opcode=opcode, payload=payload)


__all__ = [
    "CollaborationWebSocketServer",
    "WebSocketProtocolError",
    "compute_accept_key",
    "encode_frame",
    "read_frame",
    "_MinimalWSClient",
]
