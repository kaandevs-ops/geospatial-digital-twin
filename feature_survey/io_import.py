"""Saha veri içe aktarma: PENZD CSV (total station / RTK-GNSS export'ları).

PENZD, harita/jeodezi cihazlarının (Trimble, Leica, Topcon dahil çoğu total
station ve RTK-GNSS kayıt yazılımı) fiili standart çıktı sırasıdır:

    Point, Easting, Northing, Elevation, Description

`Description` alanı genelde `KOD` veya `KOD_STRINGID` biçimindedir (örn.
``BLD_COR_BLD01`` → kod=BLD_COR, string_id=BLD01) — bu modül her iki yaygın
ayracı (`_` ve `-`) da tanır; ayrılamıyorsa tüm description ham kod olarak
`FeatureCode.OTHER` + `raw_code`'a düşer (sessizce veri kaybetmemek için).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .codes import FeatureCode
from .field_point import FieldPoint, FieldSurveySession


class PENZDImportError(ValueError):
    """Bozuk/beklenmeyen sütun sayısına sahip bir PENZD satırı için."""


_CODE_LOOKUP = {c.value: c for c in FeatureCode}


def _split_description(desc: str) -> tuple[FeatureCode, str | None, str | None]:
    """``BLD_COR_BLD01`` -> (BUILDING_CORNER kodu, ham metin, "BLD01")."""
    desc = desc.strip()
    for sep in ("_", "-"):
        if sep in desc:
            head, _, tail = desc.rpartition(sep)
            if head in _CODE_LOOKUP:
                return _CODE_LOOKUP[head], None, tail or None
    if desc in _CODE_LOOKUP:
        return _CODE_LOOKUP[desc], None, None
    return FeatureCode.OTHER, desc, None


def _rows_to_session(
    rows: list[list[str]],
    *,
    session_name: str,
    has_header: bool,
    instrument: str,
    source_label: str,
) -> FieldSurveySession:
    session = FieldSurveySession(name=session_name)

    if has_header and rows:
        rows = rows[1:]

    for line_no, row in enumerate(rows, start=(2 if has_header else 1)):
        if not row or all(not cell.strip() for cell in row):
            continue  # boş satır atla
        if len(row) < 5:
            raise PENZDImportError(
                f"{source_label}: satır {line_no} beklenen 5 sütundan az içeriyor "
                f"(point,easting,northing,elevation,description): {row!r}"
            )
        point_id, e_str, n_str, z_str, desc = row[0], row[1], row[2], row[3], row[4]
        try:
            easting, northing, elevation = float(e_str), float(n_str), float(z_str)
        except ValueError as exc:
            raise PENZDImportError(
                f"{source_label}: satır {line_no} sayısal olmayan koordinat: {row!r}"
            ) from exc

        code, raw_code, string_id = _split_description(desc)
        session.add(
            FieldPoint(
                point_id=point_id.strip(),
                easting=easting,
                northing=northing,
                elevation=elevation,
                code=code,
                raw_code=raw_code,
                description=desc,
                string_id=string_id,
                instrument=instrument,
            )
        )

    return session


def import_penzd_csv(
    path: str | Path,
    *,
    has_header: bool = True,
    instrument: str = "total_station",
) -> FieldSurveySession:
    """Bir PENZD CSV dosyasını `FieldSurveySession`'a okur.

    Beklenen sütun sırası: point_id, easting, northing, elevation, description.
    Fazladan sütunlar yok sayılır (bazı cihazlar zaman damgası/instrument id
    ekler); eksik sütun `PENZDImportError` fırlatır.
    """
    path = Path(path)
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    return _rows_to_session(
        rows,
        session_name=path.stem,
        has_header=has_header,
        instrument=instrument,
        source_label=str(path),
    )


def import_penzd_csv_text(
    text: str,
    *,
    session_name: str = "field_session",
    has_header: bool = True,
    instrument: str = "total_station",
) -> FieldSurveySession:
    """`import_penzd_csv` ile aynı format, ancak dosya yerine yapıştırılmış
    CSV metnini kabul eder (web arayüzündeki 'PENZD CSV yapıştır' alanı
    için — sahadaki cihaz çıktısı genelde küçük olduğundan dosya yüklemeye
    gerek kalmadan doğrudan metin girişi pratik bir yol)."""
    rows = list(csv.reader(text.splitlines()))
    return _rows_to_session(
        rows,
        session_name=session_name,
        has_header=has_header,
        instrument=instrument,
        source_label="<pasted CSV>",
    )
