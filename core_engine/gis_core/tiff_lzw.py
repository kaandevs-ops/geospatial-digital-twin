"""Roadmap V7 - "bilinçli kapsam-dışı" madde 3'ün kapanışı: **LZW
sıkıştırmalı GeoTIFF desteği**.

Önceki durum: `core_engine/gis_core/__init__.py._parse_geotiff`, TIFF
Compression=5 (LZW) etiketini tanıyor ama stdlib'de codec'i olmadığı için
`UnsupportedFormatError` fırlatıyordu (DEFLATE/Adobe-Deflate zaten `zlib`
ile çözülüyordu çünkü stdlib'de var).

Bu modül, TIFF 6.0 spesifikasyonu Bölüm 13'teki LZW varyantının
(GIF LZW'den farklı: **"early change"** — kod genişliği tablo GIF'ten bir
kod erken büyür) saf-Python, harici bağımlılıksız bir çözücüsünü sağlar.
Kaynak: TIFF 6.0 Specification, Adobe Systems, 1992, §13 "LZW
Compression" — algoritma adım adım orada tarif edilir (metin buraya
kopyalanmadı, yalnızca algoritma uygulanmıştır).
"""
from __future__ import annotations

__all__ = ["LZWDecodeError", "lzw_decode"]


class LZWDecodeError(ValueError):
    """Akış bozuksa (beklenmeyen kod, EOI'den önce bit tükenmesi vb.)."""


_CLEAR_CODE = 256
_EOI_CODE = 257
_MIN_CODE_SIZE = 9
_MAX_CODE_SIZE = 12


class _BitReader:
    """MSB-first (en anlamlı bit önce) bit okuyucu — TIFF LZW akışı bu
    sırayla paketlenir (byte içinde soldan sağa, GIF'in LSB-first'ünün
    tersi)."""

    __slots__ = ("_data", "_bit_pos", "_total_bits")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._bit_pos = 0
        self._total_bits = len(data) * 8

    def read(self, n_bits: int) -> int | None:
        if self._bit_pos + n_bits > self._total_bits:
            return None
        value = 0
        pos = self._bit_pos
        data = self._data
        for _ in range(n_bits):
            byte = data[pos >> 3]
            bit = (byte >> (7 - (pos & 7))) & 1
            value = (value << 1) | bit
            pos += 1
        self._bit_pos = pos
        return value


def _initial_table() -> list[bytes]:
    # 0-255: tekil byte'lar, 256: CLEAR (placeholder), 257: EOI (placeholder).
    return [bytes((i,)) for i in range(256)] + [b"", b""]


def lzw_decode(data: bytes, *, expected_size_hint: int | None = None) -> bytes:
    """TIFF Compression=5 akışını ham byte dizisine çözer.

    Args:
        data: Bir TIFF strip'inin ham (henüz çözülmemiş) LZW baytları.
        expected_size_hint: Biliniyorsa çözülmüş boyut (yalnızca performans
            için `bytearray` ön-ayırma amaçlı; doğruluk için gerekmez).

    TIFF LZW'nin GIF LZW'den kritik farkı **"early change"**: kod tablosu
    2**code_size - 1 girdiye ulaştığında (GIF'te tam 2**code_size'da değil,
    bir erken) kod genişliği bir sonraki koddan itibaren artırılır. Bu
    davranış atlanırsa akış, ilk birkaç yüz koddan sonra bozulur — bu
    yüzden burada açıkça test edilmiş/yaygın implementasyonlarla (libtiff,
    Pillow TiffImagePlugin) aynı davranışı izleyen bir "erken artış"
    uygulanmıştır.
    """
    if not data:
        return b""

    reader = _BitReader(data)
    table = _initial_table()
    code_size = _MIN_CODE_SIZE
    out = bytearray()
    del expected_size_hint  # yalnızca API uyumluluğu için tutulur, kullanılmıyor

    old_code: int | None = None
    first_code_read = False

    while True:
        code = reader.read(code_size)
        if code is None:
            # EOI koymadan akış bitmiş olabilir (bazı yazıcılar EOI'yi
            # atlar) — bu, hata değil, akışın doğal sonu sayılır.
            break
        if code == _EOI_CODE:
            break
        if code == _CLEAR_CODE:
            table = _initial_table()
            code_size = _MIN_CODE_SIZE
            old_code = None
            first_code_read = False
            continue

        if not first_code_read:
            if code >= len(table) or not table[code]:
                if code > 255:
                    raise LZWDecodeError(f"LZW: geçersiz ilk kod {code}.")
            entry = table[code]
            out += entry
            old_code = code
            first_code_read = True
        else:
            if code < len(table) and (code < 256 or table[code]):
                entry = table[code]
            elif code == len(table):
                # KwKwK durumu: yeni kod henüz tabloya eklenmemiş ama
                # tanımı öngörülebilir (önceki girdi + önceki girdinin
                # ilk byte'ı).
                prev = table[old_code]
                entry = prev + prev[0:1]
            else:
                raise LZWDecodeError(
                    f"LZW: beklenmeyen kod {code} (tablo boyutu {len(table)})."
                )
            out += entry
            prev = table[old_code]
            table.append(prev + entry[0:1])
            old_code = code

        # "Early change": tablo (bir sonraki eklemeyle) mevcut kod
        # genişliğinin sınırına bir kala genişliğin artması gerekir.
        if len(table) >= (1 << code_size) - 1 and code_size < _MAX_CODE_SIZE:
            code_size += 1

    return bytes(out)
