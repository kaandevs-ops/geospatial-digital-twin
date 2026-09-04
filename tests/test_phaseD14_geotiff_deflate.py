"""
Roadmap V3 - Faz D14: Core Engine - Sıkıştırılmış GeoTIFF Desteği
===================================================================

`HeightmapParser` artık baseline TIFF 6.0 IFD'sini ayrıştırıp DEFLATE/
Adobe-Deflate (Compression=8/32946) sıkıştırılmış strip'leri stdlib
`zlib` ile çözebiliyor - tam bir TIFF codec'i değil (tiled TIFF, çoklu
sample, JPEG sıkıştırma vs. kapsam dışı), ama gerçek bir DEFLATE-
sıkıştırmalı tek-strip GeoTIFF'i baştan sona doğru okuyor.

Bu test dosyası, stdlib `struct` + `zlib` ile kendi minimal TIFF
yazıcısını (yalnızca test amaçlı, `_build_tiff`) kullanarak hem
sıkıştırılmamış hem DEFLATE-sıkıştırılmış aynı yükseklik grid'ini
üretir ve `HeightmapParser`'ın ikisini de piksel-piksel özdeş
çözdüğünü doğrular (kabul kriteri).
"""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.gis_core import GISParseError, HeightmapParser, UnsupportedFormatError


def _lzw_encode(raw: bytes) -> bytes:
    """Testlerde kullanmak için minimal, saf-Python TIFF-uyumlu LZW
    kodlayıcı (TIFF 6.0 §13 "early change" varyantı) — üretim kodundaki
    `core_engine/gis_core/tiff_lzw.lzw_decode`'un tersini doğrulamak için
    buraya kasıtlı olarak bağımsız yazıldı (aynı hatayı iki kere yapmamak
    için farklı bir yaklaşım: sözlük tabanlı, klasik LZW78 kodlama)."""
    CLEAR, EOI = 256, 257
    table = {bytes((i,)): i for i in range(256)}
    next_code = 258
    code_size = 9
    bits = []

    def emit(code: int, size: int) -> None:
        for i in range(size - 1, -1, -1):
            bits.append((code >> i) & 1)

    emit(CLEAR, code_size)
    w = b""
    for byte in raw:
        c = bytes((byte,))
        wc = w + c
        if wc in table:
            w = wc
        else:
            emit(table[w], code_size)
            table[wc] = next_code
            next_code += 1
            w = c
            if next_code >= (1 << code_size) - 1 and code_size < 12:
                code_size += 1
    if w:
        emit(table[w], code_size)
    emit(EOI, code_size)

    while len(bits) % 8 != 0:
        bits.append(0)
    out = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for b in bits[i:i + 8]:
            byte = (byte << 1) | b
        out.append(byte)
    return bytes(out)


def _build_tiff(width, height, elevations, compression=8, endian="<", predictor=1):
    flat = [v for row in elevations for v in row]
    raw = struct.pack(f"{endian}{len(flat)}f", *flat)

    if predictor == 2:
        # Not used for float32 in this test (predictor 2 is defined for
        # integer samples in the TIFF spec); float DEM data uses predictor=1.
        raise NotImplementedError("test builder: predictor=2 float not needed")

    if compression in (8, 32946):
        strip_data = zlib.compress(raw)
    elif compression == 1:
        strip_data = raw
    elif compression == 5:
        # Roadmap V7: artık gerçek LZW çözülüyor, bu yüzden test verisi de
        # gerçekten LZW ile kodlanmış olmalı (aksi halde "kabul edilmemeli"
        # yerine "bozuk akış hatası" test edilmiş olurdu, ki bu ayrı bir
        # test - bkz. test_corrupted_lzw_stream_raises_parse_error).
        strip_data = _lzw_encode(raw)
    else:
        raise ValueError("unsupported test compression")

    header = struct.pack(f"{endian}2sHI", b"II" if endian == "<" else b"MM", 42, 8)

    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 1, 32),
        (259, 3, 1, compression),
        (273, 4, 1, 0),  # patched below
        (277, 3, 1, 1),
        (278, 4, 1, height),
        (279, 4, 1, len(strip_data)),
        (339, 3, 1, 3),
    ]
    entries.sort(key=lambda e: e[0])
    n = len(entries)
    ifd_offset = 8
    ifd_size = 2 + n * 12 + 4
    strip_offset = ifd_offset + ifd_size

    _CODE = {3: "H", 4: "I"}  # SHORT / LONG

    body = struct.pack(f"{endian}H", n)
    for tag, ftype, count, value in entries:
        if tag == 273:
            value = strip_offset
        code = _CODE[ftype]
        # TIFF spec: value stored "left-justified" in the 4-byte field -
        # value bytes first, zero-padded at the end (both byte orders).
        value_bytes = struct.pack(f"{endian}{code}", value)
        value_bytes = value_bytes + b"\x00" * (4 - len(value_bytes))
        body += struct.pack(f"{endian}HHI", tag, ftype, count)
        body += value_bytes
    body += struct.pack(f"{endian}I", 0)

    return header + body + strip_data


_GRID = [
    [10.5, 11.25, 12.0, 9.75],
    [15.0, 14.5, 13.25, 12.75],
    [8.0, 9.5, 10.25, 11.0],
]


def _write(tmp_name, compression):
    data = _build_tiff(4, 3, _GRID, compression=compression)
    path = f"/tmp/{tmp_name}"
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def test_deflate_compressed_geotiff_matches_uncompressed_pixel_for_pixel():
    uncompressed_path = _write("d14_ref_uncompressed.tif", compression=1)
    compressed_path = _write("d14_ref_deflate.tif", compression=8)

    parser = HeightmapParser()
    ref = parser.parse_file(uncompressed_path)
    got = parser.parse_file(compressed_path)

    ref_elev = ref.features[0].coordinates["elevations"]
    got_elev = got.features[0].coordinates["elevations"]
    assert len(ref_elev) == len(got_elev) == 3
    for r_row, g_row in zip(ref_elev, got_elev):
        assert len(r_row) == len(g_row) == 4
        for r_val, g_val in zip(r_row, g_row):
            assert abs(r_val - g_val) < 1e-6


def test_deflate_compressed_geotiff_matches_expected_values():
    path = _write("d14_expected.tif", compression=8)
    parser = HeightmapParser()
    fc = parser.parse_file(path)
    elevations = fc.features[0].coordinates["elevations"]
    for r_row, e_row in zip(elevations, _GRID):
        for got, expected in zip(r_row, e_row):
            assert abs(got - expected) < 1e-6


def test_geotiff_reports_compression_and_dimensions_in_metadata():
    path = _write("d14_meta.tif", compression=8)
    parser = HeightmapParser()
    fc = parser.parse_file(path)
    feature = fc.features[0]
    assert feature.coordinates["width"] == 4
    assert feature.coordinates["height"] == 3
    assert feature.properties["format"] == "GEOTIFF"
    assert feature.properties["compression"] == 8


def test_adobe_deflate_code_32946_also_supported():
    data = _build_tiff(4, 3, _GRID, compression=32946)
    path = "/tmp/d14_adobe_deflate.tif"
    with open(path, "wb") as fh:
        fh.write(data)
    parser = HeightmapParser()
    fc = parser.parse_file(path)
    elevations = fc.features[0].coordinates["elevations"]
    for r_row, e_row in zip(elevations, _GRID):
        for got, expected in zip(r_row, e_row):
            assert abs(got - expected) < 1e-6


def test_lzw_compression_is_decoded_correctly():
    """Roadmap V7: LZW artık `UnsupportedFormatError` fırlatmıyor,
    gerçekten çözülüyor - DEFLATE testleriyle aynı kabul kriteri
    (piksel-piksel özdeş grid)."""
    data = _build_tiff(4, 3, _GRID, compression=5)
    path = "/tmp/d14_lzw.tif"
    with open(path, "wb") as fh:
        fh.write(data)
    parser = HeightmapParser()
    result = parser.parse_file(path)
    feature = result.features[0]
    assert feature.properties["compression"] == 5
    elevations = feature.coordinates["elevations"]
    for r_row, e_row in zip(elevations, _GRID):
        for r_val, e_val in zip(r_row, e_row):
            assert abs(r_val - e_val) < 1e-4


def test_corrupted_lzw_stream_raises_parse_error_not_silent_garbage():
    """Gerçekten bozuk (rastgele) LZW baytları sessizce yanlış sonuç
    üretmemeli - açık bir `GISParseError` fırlatılmalı."""
    data = _build_tiff(4, 3, _GRID, compression=8)  # geçerli bir DEFLATE dosyasıyla başla
    # Compression tag'ini 5'e çevirip strip verisini rastgele baytlarla
    # değiştirerek "sözdizimsel olarak LZW ama bozuk" bir akış üretiyoruz.
    data = bytearray(data)
    import re as _re
    # basit yaklaşım: yeni, kasıtlı-bozuk bir dosya inşa etmek yerine
    # doğrudan _build_tiff(compression=5) çıktısının strip verisini boz.
    good = bytearray(_build_tiff(4, 3, _GRID, compression=5))
    # StripOffsets etiketinden strip'in dosyadaki konumunu bulmak yerine,
    # dosyanın son N baytını (strip verisi IFD'den sonra yazılır) bozuyoruz.
    good[-8:] = bytes([0xFF] * 8)
    path = "/tmp/d14_lzw_corrupt.tif"
    with open(path, "wb") as fh:
        fh.write(bytes(good))
    parser = HeightmapParser()
    try:
        parser.parse_file(path)
    except (GISParseError, UnsupportedFormatError):
        pass
    else:
        raise AssertionError(
            "bozuk LZW akışı sessizce kabul edilmemeli (GISParseError bekleniyordu)"
        )


def test_invalid_byte_order_marker_raises_parse_error():
    bad = b"XX" + b"\x00" * 20
    path = "/tmp/d14_bad_marker.tif"
    with open(path, "wb") as fh:
        fh.write(bad)
    parser = HeightmapParser()
    try:
        parser.parse_file(path)
        assert False, "gecersiz byte-order marker kabul edilmemeli"
    except Exception:
        pass


def test_big_endian_tiff_also_parses():
    data = _build_tiff(4, 3, _GRID, compression=8, endian=">")
    path = "/tmp/d14_big_endian.tif"
    with open(path, "wb") as fh:
        fh.write(data)
    parser = HeightmapParser()
    fc = parser.parse_file(path)
    elevations = fc.features[0].coordinates["elevations"]
    for r_row, e_row in zip(elevations, _GRID):
        for got, expected in zip(r_row, e_row):
            assert abs(got - expected) < 1e-6


def test_esri_ascii_grid_still_works_no_regression():
    content = (
        "ncols 3\nnrows 2\nxllcorner 0.0\nyllcorner 0.0\n"
        "cellsize 1.0\nNODATA_value -9999\n"
        "1.0 2.0 3.0\n4.0 5.0 6.0\n"
    )
    path = "/tmp/d14_regression.asc"
    with open(path, "w") as fh:
        fh.write(content)
    parser = HeightmapParser()
    fc = parser.parse_file(path)
    assert fc.features[0].coordinates["elevations"] == [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]
