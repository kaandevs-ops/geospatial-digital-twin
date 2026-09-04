"""ROADMAP_V6 FAZ S1.2 — RINEX 3.x gözlem dosyası ayrıştırma birim testleri."""

from __future__ import annotations

import pytest
from harita.feature_survey.raw_import.rinex import (
    MalformedRecordError,
    UnsupportedRinexVersionError,
    parse_rinex_observation_file,
)


def _line(content: str, label: str) -> str:
    """60 karaktere kadar doldurup, 61. kolondan itibaren etiketi ekler
    (RINEX başlık satır biçimi)."""
    return content.ljust(60)[:60] + label


def _obs_field(value: float, lli: str = " ", ssi: str = " ") -> str:
    return f"{value:14.3f}{lli}{ssi}"


def _build_minimal_rinex304(tmp_path, num_epochs: int = 1):
    ver_line = f"{3.04:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20)
    ver_line = _line(ver_line, "RINEX VERSION / TYPE")

    marker_line = _line("TEST_MARKER".ljust(60), "MARKER NAME")

    approx_xyz = f"{4000000.0:14.4f}{3000000.0:14.4f}{3500000.0:14.4f}".ljust(60)
    approx_line = _line(approx_xyz, "APPROX POSITION XYZ")

    # G sistemi için 3 gözlem tipi: C1C (pseudorange), L1C (carrier phase), S1C (SNR)
    sys_obs_line = _line("G    3 C1C  L1C  S1C".ljust(60), "SYS / # / OBS TYPES")

    time_first_line = _line(
        "  2024    1    1    0    0    0.0000000".ljust(60), "TIME OF FIRST OBS"
    )

    end_header_line = _line("", "END OF HEADER")

    lines = [ver_line, marker_line, approx_line, sys_obs_line, time_first_line, end_header_line]

    for e in range(num_epochs):
        epoch_line = f"> 2024  1  1  0  0 {float(e):10.7f}  0  2"
        sat1 = "G01" + _obs_field(20123456.789) + _obs_field(105781234.567) + _obs_field(45.500)
        sat2 = "G05" + _obs_field(21123456.123) + _obs_field(110781234.111) + _obs_field(40.100)
        lines.extend([epoch_line, sat1, sat2])

    path = tmp_path / "test.24o"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def test_parse_minimal_rinex_header_and_epoch(tmp_path):
    path = _build_minimal_rinex304(tmp_path, num_epochs=1)
    result = parse_rinex_observation_file(path)

    assert result.header.version == pytest.approx(3.04)
    assert result.header.file_type == "O"
    assert result.header.marker_name == "TEST_MARKER"
    assert result.header.approx_position_xyz_m == pytest.approx((4000000.0, 3000000.0, 3500000.0))
    assert result.header.obs_types_by_system["G"] == ["C1C", "L1C", "S1C"]

    assert len(result.epochs) == 1
    epoch = result.epochs[0]
    assert epoch.year == 2024 and epoch.month == 1 and epoch.day == 1
    assert epoch.epoch_flag == 0
    assert epoch.is_ok is True
    assert len(epoch.satellites) == 2
    g01 = next(s for s in epoch.satellites if s.satellite_id == "G01")
    assert g01.values["C1C"] == pytest.approx(20123456.789, abs=1e-3)
    assert g01.values["L1C"] == pytest.approx(105781234.567, abs=1e-3)
    assert g01.values["S1C"] == pytest.approx(45.500, abs=1e-3)


def test_multiple_epochs_parsed_in_order(tmp_path):
    path = _build_minimal_rinex304(tmp_path, num_epochs=3)
    result = parse_rinex_observation_file(path)
    assert len(result.epochs) == 3
    assert [round(e.second, 3) for e in result.epochs] == [0.0, 1.0, 2.0]


def test_missing_observation_field_is_not_filled_with_default(tmp_path):
    """Bir gözlem alanı boşsa (14 boşluk), sonuçta o anahtar hiç bulunmamalı
    — 0.0 ile doldurulmamalı (roadmap ilkesi: sessiz varsayım yok)."""

    ver_line = _line(
        f"{3.04:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20), "RINEX VERSION / TYPE"
    )
    sys_obs_line = _line("G    3 C1C  L1C  S1C".ljust(60), "SYS / # / OBS TYPES")
    end_header_line = _line("", "END OF HEADER")
    lines = [ver_line, sys_obs_line, end_header_line]

    epoch_line = "> 2024  1  1  0  0  0.0000000  0  1"
    # L1C alanı tamamen boş bırakıldı (14 boşluk + 2 boşluk = eksik gözlem)
    sat1 = "G01" + _obs_field(20123456.789) + (" " * 16) + _obs_field(45.5)
    lines.extend([epoch_line, sat1])

    path = tmp_path / "missing.24o"
    path.write_text("\n".join(lines) + "\n", encoding="ascii")

    result = parse_rinex_observation_file(path)
    sat = result.epochs[0].satellites[0]
    assert "C1C" in sat.values
    assert "L1C" not in sat.values
    assert "S1C" in sat.values


def test_unsupported_rinex2_version_rejected(tmp_path):
    ver_line = _line(
        f"{2.11:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20), "RINEX VERSION / TYPE"
    )
    end_header_line = _line("", "END OF HEADER")
    path = tmp_path / "old.rnx"
    path.write_text("\n".join([ver_line, end_header_line]) + "\n", encoding="ascii")
    with pytest.raises(UnsupportedRinexVersionError):
        parse_rinex_observation_file(path)


def test_missing_end_of_header_raises(tmp_path):
    ver_line = _line(
        f"{3.04:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20), "RINEX VERSION / TYPE"
    )
    path = tmp_path / "no_end.rnx"
    path.write_text(ver_line + "\n", encoding="ascii")
    with pytest.raises(MalformedRecordError):
        parse_rinex_observation_file(path)


def test_missing_obs_types_header_raises(tmp_path):
    ver_line = _line(
        f"{3.04:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20), "RINEX VERSION / TYPE"
    )
    end_header_line = _line("", "END OF HEADER")
    path = tmp_path / "no_obs_types.rnx"
    path.write_text("\n".join([ver_line, end_header_line]) + "\n", encoding="ascii")
    with pytest.raises(MalformedRecordError):
        parse_rinex_observation_file(path)


def test_epoch_flag_event_marks_not_ok(tmp_path):
    ver_line = _line(
        f"{3.04:9.2f}".ljust(20) + "O".ljust(20) + "G".ljust(20), "RINEX VERSION / TYPE"
    )
    sys_obs_line = _line("G    1 C1C".ljust(60), "SYS / # / OBS TYPES")
    end_header_line = _line("", "END OF HEADER")
    # epoch flag = 4 (uydu/anten olayı), num_sats burada "kayıt sayısı" anlamına
    # gelir (spec); basitleştirmek için burada 0 kayıt verelim.
    epoch_line = "> 2024  1  1  0  0  0.0000000  4  0"
    path = tmp_path / "event.rnx"
    path.write_text(
        "\n".join([ver_line, sys_obs_line, end_header_line, epoch_line]) + "\n", encoding="ascii"
    )
    result = parse_rinex_observation_file(path)
    assert result.epochs[0].epoch_flag == 4
    assert result.epochs[0].is_ok is False
