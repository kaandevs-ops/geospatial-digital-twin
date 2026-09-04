"""FAZ S1 — Ham Ölçüm Verisi İçe Aktarma.

Roadmap V6, FAZ S1: cihazdan çıkan ham, işlenmemiş veriyi kayıpsız okur.
Burada hiçbir enterpolasyon/tahmin yapılmaz — sadece dosya formatı
ayrıştırma. Trigonometrik indirgeme ve dengeleme `feature_survey.geodetic_engine`
(FAZ S2) sorumluluğundadır.

Alt modüller:
    - `total_station`: Leica GSI (GSI8/GSI16) ham gözlem ayrıştırma (S1.1).
    - `gnss`: NMEA-0183 (GGA/GST/GSA) ham epoch ayrıştırma, checksum
      doğrulamalı (S1.2).
    - `lidar`: LAS 1.2-1.4 header + VLR (CRS dahil) okuma, LAZ için
      `ExternalToolNotAvailableError` (S1.3).
    - `drone_gcp`: WebODM `gcp_list.txt` GCP dosyası okuma/yazma (S1.4).
"""

from __future__ import annotations

from .drone_gcp import (
    GcpList,
    GroundControlPoint,
    parse_gcp_list,
    write_gcp_list,
)
from .drone_gcp import (
    MalformedRecordError as GcpMalformedRecordError,
)
from .gnss import (
    ChecksumError,
    FixQuality,
    GnssEpoch,
    apply_gsa,
    apply_gst,
    parse_gga,
    parse_nmea_log,
)
from .gnss import (
    MalformedRecordError as GnssMalformedRecordError,
)
from .lidar import (
    ExternalToolNotAvailableError,
    LasHeader,
    MissingCRSError,
    VariableLengthRecord,
    extract_crs_wkt,
    read_las_header,
)
from .total_station import (
    MalformedRecordError as TotalStationMalformedRecordError,
)
from .total_station import (
    TotalStationObservation,
    parse_gsi_file,
    parse_gsi_line,
)

__all__ = [
    "TotalStationObservation",
    "TotalStationMalformedRecordError",
    "parse_gsi_file",
    "parse_gsi_line",
    "ChecksumError",
    "FixQuality",
    "GnssEpoch",
    "GnssMalformedRecordError",
    "apply_gsa",
    "apply_gst",
    "parse_gga",
    "parse_nmea_log",
    "ExternalToolNotAvailableError",
    "LasHeader",
    "MissingCRSError",
    "VariableLengthRecord",
    "extract_crs_wkt",
    "read_las_header",
    "GcpList",
    "GroundControlPoint",
    "GcpMalformedRecordError",
    "parse_gcp_list",
    "write_gcp_list",
]
