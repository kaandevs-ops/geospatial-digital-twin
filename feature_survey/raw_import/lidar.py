"""FAZ S1.3 — LiDAR / nokta bulutu ham verisi: LAS header + VLR okuma.

Bu modül **stdlib-only** çekirdek olarak LAS 1.2–1.4 public header block'unu
ve VLR (Variable Length Record) tablosunu gerçek LAS spesifikasyonuna göre
(ASPRS LAS Specification) `struct` ile ayrıştırır. Nokta verisinin tamamının
(point records) yüksek performanslı okunması/yazılması ve LAZ (sıkıştırılmış)
desteği, roadmap'te belirtildiği gibi opsiyonel `laspy`/`lazrs` bağımlılığına
bırakılır (`[survey]` extra) — burada kurulu değilse `ExternalToolNotAvailableError`
ile açıkça bildirilir, sessizce atlanmaz.

CRS bilgisi header'da yoksa (ne GeoTIFF VLR ne de WKT VLR varsa)
`MissingCRSError` fırlatılır — varsayılan bir CRS **asla** uydurulmaz
(roadmap S1.3 ilkesi).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class MissingCRSError(ValueError):
    """LAS dosyasının header'ında hiçbir CRS tanımı (WKT/GeoTIFF VLR)
    bulunamadığında fırlatılır."""


class MalformedRecordError(ValueError):
    """LAS header, ilan edilen dosya imzasına/spesifikasyona uymadığında."""


class ExternalToolNotAvailableError(RuntimeError):
    """Tam nokta verisi okuma/yazma (`laspy`) veya LAZ açma (`lazrs`/`laszip`)
    gerektiğinde, bu opsiyonel bağımlılıklar kurulu değilse fırlatılır."""


# LAS public header block'unun ilk kısmı — sürümden bağımsız sabit alanlar.
# Kaynak: ASPRS LAS Specification, "Public Header Block" tablosu.
_HEADER_CORE_FMT = "<4sHH8sHHH8sBBHHHLLLLdddddddddd"
_HEADER_CORE_SIZE = struct.calcsize(_HEADER_CORE_FMT)


@dataclass(slots=True)
class LasHeader:
    """LAS public header block'undan gerçek okunan alanlar (uydurma/
    varsayılan yok — hepsi dosyadan)."""

    file_signature: str
    version_major: int
    version_minor: int
    point_data_format: int
    point_data_record_length: int
    number_of_point_records: int
    scale_x: float
    scale_y: float
    scale_z: float
    offset_x: float
    offset_y: float
    offset_z: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float
    header_size: int
    offset_to_point_data: int
    num_vlr: int
    is_compressed: bool  # LAZ: point_data_format'ın en üst biti set edilir


@dataclass(slots=True)
class VariableLengthRecord:
    """Ham VLR — CRS (GeoTIFF/WKT) ve diğer meta veri burada taşınır."""

    user_id: str
    record_id: int
    record_length: int
    description: str
    data: bytes


def read_las_header(path: str | Path) -> tuple[LasHeader, list[VariableLengthRecord]]:
    """LAS dosyasının header + VLR tablosunu okur. Nokta kayıtlarının
    (point records) kendisi burada okunmaz — bu, roadmap'in `laspy`
    bağımlılığına bıraktığı yüksek hacimli/performans-kritik iştir."""

    path = Path(path)
    with path.open("rb") as fh:
        raw = fh.read(227)  # LAS 1.2 header minimum boyutu
        if len(raw) < 227:
            raise MalformedRecordError(f"{path}: dosya LAS header için çok kısa ({len(raw)} bayt).")

        signature = raw[0:4]
        if signature != b"LASF":
            raise MalformedRecordError(
                f"{path}: LAS dosya imzası 'LASF' değil, bulundu: {signature!r} — bu bir LAS dosyası değil."
            )

        version_major, version_minor = struct.unpack_from("<BB", raw, 24)
        header_size = struct.unpack_from("<H", raw, 94)[0]
        offset_to_point_data = struct.unpack_from("<L", raw, 96)[0]
        num_vlr = struct.unpack_from("<L", raw, 100)[0]
        point_data_format_raw = struct.unpack_from("<B", raw, 104)[0]
        point_data_record_length = struct.unpack_from("<H", raw, 105)[0]
        number_of_point_records = struct.unpack_from("<L", raw, 107)[0]

        scale_x, scale_y, scale_z = struct.unpack_from("<ddd", raw, 131)
        offset_x, offset_y, offset_z = struct.unpack_from("<ddd", raw, 155)
        max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<dddddd", raw, 179)

        is_compressed = bool(point_data_format_raw & 0x80)
        point_data_format = point_data_format_raw & 0x3F

        header = LasHeader(
            file_signature=signature.decode("ascii"),
            version_major=version_major,
            version_minor=version_minor,
            point_data_format=point_data_format,
            point_data_record_length=point_data_record_length,
            number_of_point_records=number_of_point_records,
            scale_x=scale_x,
            scale_y=scale_y,
            scale_z=scale_z,
            offset_x=offset_x,
            offset_y=offset_y,
            offset_z=offset_z,
            min_x=min_x,
            max_x=max_x,
            min_y=min_y,
            max_y=max_y,
            min_z=min_z,
            max_z=max_z,
            header_size=header_size,
            offset_to_point_data=offset_to_point_data,
            num_vlr=num_vlr,
            is_compressed=is_compressed,
        )

        fh.seek(header_size)
        vlrs: list[VariableLengthRecord] = []
        for _ in range(num_vlr):
            vlr_header = fh.read(54)
            if len(vlr_header) < 54:
                raise MalformedRecordError(f"{path}: VLR header eksik/bozuk (dosya sonu erken geldi).")
            _reserved, user_id_raw, record_id, record_length, description_raw = struct.unpack(
                "<H16sHH32s", vlr_header
            )
            data = fh.read(record_length)
            if len(data) < record_length:
                raise MalformedRecordError(f"{path}: VLR veri bloğu ilan edilen uzunluktan kısa.")
            vlrs.append(
                VariableLengthRecord(
                    user_id=user_id_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
                    record_id=record_id,
                    record_length=record_length,
                    description=description_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace"),
                    data=data,
                )
            )

        if header.is_compressed:
            raise ExternalToolNotAvailableError(
                f"{path}: dosya LAZ (sıkıştırılmış). Header okundu, ancak nokta verisi için "
                "'lazrs'/'laszip' opsiyonel bağımlılığı gerekir (pyproject [survey] extra). "
                "Kurulu değilse bu adım burada durur, sessizce sahte veri üretilmez."
            )

        return header, vlrs


# Bilinen CRS taşıyan VLR record_id'leri (kaynak: ASPRS LAS Spec + GeoTIFF
# VLR konvansiyonu, user_id="LASF_Projection").
_GEOTIFF_VLR_IDS = {34735, 34736, 34737}  # GeoKeyDirectoryTag, GeoDoubleParams, GeoAsciiParams
_WKT_VLR_ID = 2112  # OGC WKT (user_id="LASF_Projection", record_id 2112, LAS 1.4)


def extract_crs_wkt(vlrs: list[VariableLengthRecord]) -> str:
    """VLR listesinden gerçek CRS tanımını (WKT metni) çıkarır. Ne WKT ne
    de GeoTIFF anahtarı bulunursa varsayılan bir CRS uydurulmaz —
    `MissingCRSError` fırlatılır (roadmap S1.3 ilkesi)."""

    for vlr in vlrs:
        if vlr.user_id == "LASF_Projection" and vlr.record_id == _WKT_VLR_ID:
            return vlr.data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

    geotiff_vlrs = [v for v in vlrs if v.user_id == "LASF_Projection" and v.record_id in _GEOTIFF_VLR_IDS]
    if geotiff_vlrs:
        raise MissingCRSError(
            "Dosyada GeoTIFF anahtar VLR'leri var ama WKT metni yok — GeoTIFF anahtar "
            "kodlarının (EPSG) tam çözümlenmesi 'pyproj'/dış CRS veritabanı gerektirir; "
            "bu fonksiyon ham anahtarları uydurma bir WKT'ye çevirmez. Ham VLR verisi "
            "`vlr.data` üzerinden erişilebilir, çözümleme `core_engine.geo_reference` "
            "modülüne bırakılmıştır."
        )

    raise MissingCRSError(
        "LAS dosyasının header/VLR'lerinde hiçbir CRS tanımı (WKT veya GeoTIFF anahtarı) "
        "bulunamadı. Varsayılan bir CRS ASLA uydurulmaz — kullanıcı CRS'i açıkça belirtmelidir."
    )
