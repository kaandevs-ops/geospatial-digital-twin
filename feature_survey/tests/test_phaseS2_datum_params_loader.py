"""ROADMAP_V6 FAZ S2.4 — resmi datum parametre yükleyici testleri.

Kabul kriteri: platform, kaynağı belgelenmemiş/şablon (placeholder)
parametrelerle asla "gerçek" bir dönüşüm yapmış gibi davranmamalı --
yalnızca gerçek, kaynağı dolu bir JSON dosyasını kabul etmeli.
"""

from __future__ import annotations

import json

import pytest

from harita.feature_survey.geodetic_engine.datum_transform import InsufficientDataError
from harita.feature_survey.geodetic_engine.datum_params_loader import (
    load_datum_transform_params,
)


def test_missing_file_raises():
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params("/nonexistent/path/params.json")


def test_shipped_example_template_is_rejected(tmp_path):
    """Depoda gelen şablon dosyası (REPLACE_ME işaretçili) fiilen
    kullanılmaya çalışılırsa reddedilmeli."""
    from pathlib import Path

    example = (
        Path(__file__).resolve().parents[1]
        / "geodetic_engine"
        / "datum_params"
        / "itrf_to_ed50_turkey.example.json"
    )
    assert example.is_file()
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params(example)


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params(p)


def test_missing_numeric_field_raises(tmp_path):
    p = tmp_path / "missing_field.json"
    data = {
        "tx_m": 1.0, "ty_m": 2.0, "tz_m": 3.0,
        "rx_arcsec": 0.1, "ry_arcsec": 0.2,
        # rz_arcsec eksik
        "scale_ppm": 1.0,
        "source": "Test kaynağı 2026",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params(p)


def test_non_numeric_field_raises(tmp_path):
    p = tmp_path / "bad_type.json"
    data = {
        "tx_m": "not-a-number", "ty_m": 2.0, "tz_m": 3.0,
        "rx_arcsec": 0.1, "ry_arcsec": 0.2, "rz_arcsec": 0.3,
        "scale_ppm": 1.0,
        "source": "Test kaynağı 2026",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params(p)


def test_empty_source_raises(tmp_path):
    p = tmp_path / "no_source.json"
    data = {
        "tx_m": 1.0, "ty_m": 2.0, "tz_m": 3.0,
        "rx_arcsec": 0.1, "ry_arcsec": 0.2, "rz_arcsec": 0.3,
        "scale_ppm": 1.0,
        "source": "   ",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(InsufficientDataError):
        load_datum_transform_params(p)


def test_valid_params_load_successfully(tmp_path):
    """Gerçek (test amaçlı ama tam ve belgelenmiş) bir parametre seti
    başarıyla yüklenmeli ve doğrudan `apply_helmert_transform`'a
    verilebilecek bir `DatumTransformParameters` üretmeli."""
    p = tmp_path / "valid.json"
    data = {
        "tx_m": 1.234, "ty_m": -2.345, "tz_m": 3.456,
        "rx_arcsec": 0.01, "ry_arcsec": -0.02, "rz_arcsec": 0.03,
        "scale_ppm": 0.999,
        "source": "Birim testi için üretilmiş örnek parametre seti (gerçek resmi değer değildir, sadece yükleyici mantığını doğrular) - 2026",
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    params = load_datum_transform_params(p)
    assert params.tx_m == pytest.approx(1.234)
    assert params.scale_ppm == pytest.approx(0.999)
    assert "üretilmiş" in params.source

    # Gerçekten kullanılabilir mi -- apply_helmert_transform ile doğrula.
    from harita.feature_survey.geodetic_engine.datum_transform import (
        GeocentricCoordinate,
        apply_helmert_transform,
    )

    result = apply_helmert_transform(GeocentricCoordinate(100.0, 200.0, 300.0), params)
    assert isinstance(result.x_m, float)
