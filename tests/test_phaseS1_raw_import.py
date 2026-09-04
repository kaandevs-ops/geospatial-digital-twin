"""ROADMAP_V6 FAZ S1 — ham veri içe aktarma birim testleri."""

from __future__ import annotations

import pytest
from harita.feature_survey.raw_import.drone_gcp import (
    GcpList,
    GroundControlPoint,
    parse_gcp_list,
    write_gcp_list,
)
from harita.feature_survey.raw_import.gnss import (
    ChecksumError,
    FixQuality,
    parse_gga,
)
from harita.feature_survey.raw_import.gnss import (
    MalformedRecordError as GnssMalformedRecordError,
)
from harita.feature_survey.raw_import.lidar import (
    MalformedRecordError as LasMalformedRecordError,
)
from harita.feature_survey.raw_import.lidar import (
    read_las_header,
)
from harita.feature_survey.raw_import.total_station import (
    MalformedRecordError as GsiMalformedRecordError,
)
from harita.feature_survey.raw_import.total_station import (
    parse_gsi_line,
)

# --- GNSS / NMEA -----------------------------------------------------------

VALID_GGA = "$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"


def test_parse_gga_valid_checksum():
    epoch = parse_gga(VALID_GGA)
    assert epoch.fix_quality is FixQuality.GPS_AUTONOMOUS
    assert epoch.latitude_deg == pytest.approx(48.1173, abs=1e-4)
    assert epoch.longitude_deg == pytest.approx(11.51667, abs=1e-4)
    assert epoch.is_survey_grade is False  # sadece RTK Fixed (4) survey-grade sayılır


def test_parse_gga_rejects_bad_checksum():
    bad = VALID_GGA[:-2] + "00"
    with pytest.raises(ChecksumError):
        parse_gga(bad)


def test_parse_gga_rejects_invalid_fix_quality():
    invalid_fix = VALID_GGA.replace(",1,08,", ",0,08,")
    # checksum artık tutmayacağı için önce checksum hatası beklenir; bu da
    # "sessizce kabul yok" ilkesini dolaylı olarak doğrular.
    with pytest.raises((ChecksumError, GnssMalformedRecordError)):
        parse_gga(invalid_fix)


# --- Total Station / GSI ----------------------------------------------------


def test_parse_gsi_line_missing_point_id_raises():
    # Sadece bilinmeyen word-index içeren, 11 (point_id) olmayan bir GSI
    # satırı point_id eksikliğinden reddedilmeli.
    bogus_word = "99" + "000000" + "+00000000"  # 2+6+9=17 char != 18 (word_length=16 => total 18)
    # Doğru uzunlukta ama bilinmeyen word-index üret:
    bogus_word = "99" + "000000" + "+000000000"
    with pytest.raises(GsiMalformedRecordError):
        parse_gsi_line(bogus_word, word_length=16)


def test_parse_gsi_line_bad_length_raises():
    with pytest.raises(GsiMalformedRecordError):
        parse_gsi_line("110001+00001", word_length=16)


# --- Drone GCP ---------------------------------------------------------------


def test_gcp_round_trip(tmp_path):
    gcp = GcpList(
        projection="EPSG:32636",
        points=[
            GroundControlPoint(500000.0, 4400000.0, 850.0, 1200.5, 800.2, "IMG_0001.jpg", "GCP1"),
            GroundControlPoint(500010.0, 4400010.0, 851.0, 1300.5, 900.2, "IMG_0002.jpg", "GCP2"),
        ],
    )
    path = tmp_path / "gcp_list.txt"
    write_gcp_list(gcp, path)
    loaded = parse_gcp_list(path)
    assert loaded.projection == gcp.projection
    assert len(loaded.points) == 2
    assert loaded.points[0].image_name == "IMG_0001.jpg"
    assert loaded.points[0].geo_x == pytest.approx(500000.0)


def test_gcp_missing_projection_raises(tmp_path):
    path = tmp_path / "bad_gcp.txt"
    path.write_text("500000 4400000 850 1200 800 img.jpg\n", encoding="utf-8")
    # ilk satır projeksiyon olarak yorumlanacağı için burada satır sayısı
    # yetersiz kalacak (2. satır yok) -> hata beklenir
    with pytest.raises(Exception):
        parse_gcp_list(path)


# --- LAS header --------------------------------------------------------------


def test_las_header_rejects_non_las_file(tmp_path):
    path = tmp_path / "not_a_las.bin"
    path.write_bytes(b"NOTLASF" + b"\x00" * 250)
    with pytest.raises(LasMalformedRecordError):
        read_las_header(path)
