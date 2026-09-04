"""FAZ S1.2 — RTK-GNSS ham veri içe aktarma: RINEX 3.x gözlem dosyası.

Bu modül, `gnss.py`'deki NMEA-0183 ayrıştırmasını tamamlayan ikinci parça:
RINEX (Receiver Independent Exchange Format) sürüm 3.x **gözlem** dosyaları
(observation files, genellikle `.rnx`/`.obs`/`.YYo` uzantılı, ASCII metin).

Kaynak spesifikasyon: RINEX 3.0x/3.04/3.05 resmi format tanımı (IGS/RINEX
Working Group). Bu modül **stdlib-only** kalır — `georinex` (opsiyonel
`[survey]` extra, `pyproject.toml`) daha büyük/karmaşık RINEX varyantları
(navigasyon mesajları, çoklu-frekans birleşik analiz) için tercih edilebilir,
ancak temel gözlem dosyası ayrıştırması (epoch + per-uydu gözlem değerleri)
için platformun kendi, test edilmiş, bağımlılıksız ayrıştırıcısı burada
uygulanır — roadmap S1 ilkesiyle tutarlı ("dış araç varsa ince istemci,
matematik/ayrıştırma varsa platformun kendi kodu").

Format özeti (RINEX 3.x OBS):
    Başlık (header): her satır 61. kolondan itibaren bir "label" içerir
        (örn. "RINEX VERSION / TYPE", "SYS / # / OBS TYPES",
        "TIME OF FIRST OBS", "END OF HEADER").
    Epoch kaydı: '>' ile başlayan satır:
        > YYYY MM DD HH MM SS.SSSSSSS  epoch_flag  num_sats  [clk_offset]
    Her uydu için bir gözlem satırı:
        SatID (örn. G01, R05, E12) + gözlem alanları, her biri 16 karakter
        genişliğinde: 14.3f değer + 1 karakter LLI (loss-of-lock) + 1
        karakter SSI (signal strength indicator). Eksik gözlem = boşluk
        (14 boşluk) — bu **gerçek eksik veri** anlamına gelir, 0.0 ile
        DOLDURULMAZ (roadmap ilkesi: sessiz varsayım yok).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


class MalformedRecordError(ValueError):
    """RINEX dosyası (başlık veya veri kaydı) spesifikasyona uymadığında."""


class UnsupportedRinexVersionError(ValueError):
    """Dosya RINEX 2.x veya desteklenmeyen bir sürüm/dosya tipinde olduğunda.
    Sessizce "muhtemelen uyumludur" varsayımıyla ayrıştırılmaya ÇALIŞILMAZ —
    RINEX 2.x alan genişlikleri ve başlık etiketleri 3.x'ten farklıdır."""


_HEADER_LABEL_COLUMN = 60  # 0-indeksli; label 61. kolondan (index 60) başlar
_OBS_FIELD_WIDTH = 16
_OBS_VALUE_WIDTH = 14


@dataclass(slots=True)
class RinexHeader:
    version: float
    file_type: str  # 'O' = Observation
    satellite_system: str  # 'G','R','E','C','M' (mixed) vb.
    marker_name: str | None
    approx_position_xyz_m: tuple[float, float, float] | None
    obs_types_by_system: dict[str, list[str]] = field(default_factory=dict)
    time_of_first_obs: str | None = None
    receiver_type: str | None = None
    antenna_type: str | None = None


@dataclass(slots=True)
class SatelliteObservation:
    """Tek bir uydu için, header'da tanımlı sıradaki gözlem tipi ->
    (değer, lli, ssi) eşlemesi. Değeri olmayan (boşluk) gözlem tipleri bu
    sözlükte hiç yer almaz — 0.0 ile doldurulmaz."""

    satellite_id: str  # örn. "G01", "R05"
    values: dict[str, float]
    lli: dict[str, int | None]
    ssi: dict[str, int | None]


@dataclass(slots=True)
class RinexEpoch:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: float
    epoch_flag: int
    receiver_clock_offset_s: float | None
    satellites: list[SatelliteObservation]

    @property
    def is_ok(self) -> bool:
        """Epoch flag 0 (normal) veya 1 (power failure sonrası devam) dışı
        (2-6: özel olay/anten hareketi/başlık kaydı vb.) veri epoch'u
        DEĞİLDİR — çağıran kod bunları konum/gözlem olarak kullanmamalıdır."""
        return self.epoch_flag in (0, 1)


@dataclass(slots=True)
class RinexObservationFile:
    header: RinexHeader
    epochs: list[RinexEpoch]


def _label(line: str) -> str:
    return line[_HEADER_LABEL_COLUMN:].strip() if len(line) > _HEADER_LABEL_COLUMN else line.strip()


def parse_rinex_header(lines: list[str]) -> tuple[RinexHeader, int]:
    """Başlığı ayrıştırır, `(header, end_of_header_line_index)` döner.
    `END OF HEADER` etiketi bulunamazsa `MalformedRecordError`."""

    if not lines:
        raise MalformedRecordError("Boş RINEX dosyası.")

    first = lines[0]
    if _label(first) != "RINEX VERSION / TYPE":
        raise MalformedRecordError(f"İlk satır 'RINEX VERSION / TYPE' etiketi taşımıyor: {first!r}")
    try:
        version = float(first[0:9].strip())
    except ValueError as exc:
        raise MalformedRecordError(f"RINEX sürüm numarası ayrıştırılamadı: {first[0:9]!r}") from exc
    if version < 3.0 or version >= 4.0:
        raise UnsupportedRinexVersionError(
            f"Sadece RINEX 3.x gözlem dosyaları desteklenir, bulunan sürüm: {version}. "
            "RINEX 2.x farklı alan genişliği/etiket şemasına sahiptir; RINEX 4.x "
            "için ayrı bir doğrulama gerekir — sessizce 3.x gibi ayrıştırılmaz."
        )
    file_type = first[20:21].strip()
    if file_type != "O":
        raise UnsupportedRinexVersionError(
            f"Sadece gözlem (Observation, 'O') dosyaları desteklenir, bulunan tip: {file_type!r}"
        )
    satellite_system = first[40:41].strip() or "G"

    marker_name: str | None = None
    approx_xyz: tuple[float, float, float] | None = None
    obs_types_by_system: dict[str, list[str]] = {}
    time_of_first_obs: str | None = None
    receiver_type: str | None = None
    antenna_type: str | None = None

    idx = 1
    pending_sys: str | None = None
    pending_count: int = 0
    pending_types: list[str] = []

    while idx < len(lines):
        line = lines[idx]
        label = _label(line)

        if label == "END OF HEADER":
            return (
                RinexHeader(
                    version=version,
                    file_type=file_type,
                    satellite_system=satellite_system,
                    marker_name=marker_name,
                    approx_position_xyz_m=approx_xyz,
                    obs_types_by_system=obs_types_by_system,
                    time_of_first_obs=time_of_first_obs,
                    receiver_type=receiver_type,
                    antenna_type=antenna_type,
                ),
                idx,
            )

        if label == "MARKER NAME":
            marker_name = line[0:60].strip() or None
        elif label == "APPROX POSITION XYZ":
            try:
                x = float(line[0:14])
                y = float(line[14:28])
                z = float(line[28:42])
            except ValueError as exc:
                raise MalformedRecordError(
                    f"APPROX POSITION XYZ alanı ayrıştırılamadı: {line[:42]!r}"
                ) from exc
            approx_xyz = (x, y, z)
        elif label == "TIME OF FIRST OBS":
            time_of_first_obs = line[0:60].strip()
        elif label == "REC # / TYPE / VERS":
            receiver_type = line[20:40].strip() or None
        elif label == "ANT # / TYPE":
            antenna_type = line[20:40].strip() or None
        elif label == "SYS / # / OBS TYPES":
            if pending_sys is None:
                sys_code = line[0:1].strip()
                if not sys_code:
                    raise MalformedRecordError(
                        f"'SYS / # / OBS TYPES' satırında uydu sistem kodu eksik: {line!r}"
                    )
                count_str = line[3:6].strip()
                if not count_str.isdigit():
                    raise MalformedRecordError(
                        f"'SYS / # / OBS TYPES' gözlem sayısı sayısal değil: {line!r}"
                    )
                pending_sys = sys_code
                pending_count = int(count_str)
                pending_types = []
                rest = line[7:60]
            else:
                rest = line[7:60]

            tokens = rest.split()
            pending_types.extend(tokens)

            if len(pending_types) >= pending_count:
                obs_types_by_system[pending_sys] = pending_types[:pending_count]
                pending_sys, pending_count, pending_types = None, 0, []

        idx += 1

    raise MalformedRecordError("'END OF HEADER' etiketi bulunamadı — dosya eksik/bozuk.")


def _parse_epoch_line(line: str) -> tuple[int, int, int, int, int, float, int, int, float | None]:
    """'>' epoch satırını ayrıştırır: (yıl, ay, gün, saat, dk, sn, flag, num_sats, clk_offset)."""

    if not line.startswith(">"):
        raise MalformedRecordError(f"Epoch kaydı '>' ile başlamalı: {line!r}")
    body = line[1:]
    tokens = body.split()
    if len(tokens) < 7:
        raise MalformedRecordError(f"Epoch satırı eksik alan içeriyor: {line!r}")
    try:
        year = int(tokens[0])
        month = int(tokens[1])
        day = int(tokens[2])
        hour = int(tokens[3])
        minute = int(tokens[4])
        second = float(tokens[5])
        flag = int(tokens[6])
    except ValueError as exc:
        raise MalformedRecordError(f"Epoch satırı alanları sayısal değil: {line!r}") from exc

    if len(tokens) < 8:
        raise MalformedRecordError(f"Epoch satırında uydu sayısı alanı yok: {line!r}")
    try:
        num_sats = int(tokens[7])
    except ValueError as exc:
        raise MalformedRecordError(f"Epoch uydu sayısı sayısal değil: {tokens[7]!r}") from exc

    clk_offset = float(tokens[8]) if len(tokens) > 8 else None
    return year, month, day, hour, minute, second, flag, num_sats, clk_offset


def _parse_observation_line(line: str, obs_types: list[str]) -> SatelliteObservation:
    if len(line) < 3:
        raise MalformedRecordError(f"Uydu gözlem satırı çok kısa: {line!r}")
    sat_id = line[0:3].strip()
    if len(sat_id) != 3 or not sat_id[0].isalpha():
        raise MalformedRecordError(f"Geçersiz uydu kimliği: {sat_id!r} (satır: {line!r})")

    values: dict[str, float] = {}
    lli: dict[str, int | None] = {}
    ssi: dict[str, int | None] = {}

    for i, obs_type in enumerate(obs_types):
        start = 3 + i * _OBS_FIELD_WIDTH
        field_str = line[start : start + _OBS_FIELD_WIDTH]
        if not field_str.strip():
            continue  # gerçek eksik gözlem — 0.0 ile doldurulmaz
        value_str = field_str[0:_OBS_VALUE_WIDTH]
        lli_str = field_str[_OBS_VALUE_WIDTH : _OBS_VALUE_WIDTH + 1]
        ssi_str = field_str[_OBS_VALUE_WIDTH + 1 : _OBS_VALUE_WIDTH + 2]
        if not value_str.strip():
            continue
        try:
            values[obs_type] = float(value_str)
        except ValueError as exc:
            raise MalformedRecordError(
                f"Uydu {sat_id} gözlem değeri ayrıştırılamadı ({obs_type}): {value_str!r}"
            ) from exc
        lli[obs_type] = int(lli_str) if lli_str.strip().isdigit() else None
        ssi[obs_type] = int(ssi_str) if ssi_str.strip().isdigit() else None

    return SatelliteObservation(satellite_id=sat_id, values=values, lli=lli, ssi=ssi)


def parse_rinex_observation_file(path: str | Path) -> RinexObservationFile:
    """Bir RINEX 3.x gözlem dosyasını tam olarak ayrıştırır.

    Kabul kriteri (roadmap S1.1/S1.2 ile tutarlı): her epoch ve her uydu
    gözlemi, dosyadaki bayt/karakter konumlarına göre birebir okunur; eksik
    alan asla varsayılan değerle doldurulmaz, format ihlali `MalformedRecordError`
    ile reddedilir.
    """

    path = Path(path)
    with path.open("r", encoding="ascii", errors="strict") as fh:
        raw_lines = fh.readlines()
    lines = [ln.rstrip("\n").rstrip("\r") for ln in raw_lines]

    try:
        header, end_idx = parse_rinex_header(lines)
    except (MalformedRecordError, UnsupportedRinexVersionError) as exc:
        raise type(exc)(f"{path}: {exc}") from exc

    if not header.obs_types_by_system:
        raise MalformedRecordError(
            f"{path}: başlıkta 'SYS / # / OBS TYPES' tanımı yok — gözlem alanları "
            "bilinmeden veri satırları güvenle ayrıştırılamaz."
        )

    epochs: list[RinexEpoch] = []
    idx = end_idx + 1
    n_lines = len(lines)

    while idx < n_lines:
        line = lines[idx]
        if not line.strip():
            idx += 1
            continue
        try:
            year, month, day, hour, minute, second, flag, num_sats, clk = _parse_epoch_line(line)
        except MalformedRecordError as exc:
            raise MalformedRecordError(f"{path}:{idx + 1}: {exc}") from exc

        idx += 1
        satellites: list[SatelliteObservation] = []
        for _ in range(num_sats):
            if idx >= n_lines:
                raise MalformedRecordError(
                    f"{path}:{idx + 1}: epoch {num_sats} uydu bekliyor ama dosya erken bitti."
                )
            sat_line = lines[idx]
            sys_code = sat_line[0:1].strip()
            obs_types = header.obs_types_by_system.get(sys_code) or header.obs_types_by_system.get(
                header.satellite_system
            )
            if obs_types is None:
                raise MalformedRecordError(
                    f"{path}:{idx + 1}: uydu sistemi {sys_code!r} için 'SYS / # / OBS TYPES' "
                    "başlıkta tanımlı değil."
                )
            try:
                satellites.append(_parse_observation_line(sat_line, obs_types))
            except MalformedRecordError as exc:
                raise MalformedRecordError(f"{path}:{idx + 1}: {exc}") from exc
            idx += 1

        epochs.append(
            RinexEpoch(
                year=year,
                month=month,
                day=day,
                hour=hour,
                minute=minute,
                second=second,
                epoch_flag=flag,
                receiver_clock_offset_s=clk,
                satellites=satellites,
            )
        )

    return RinexObservationFile(header=header, epochs=epochs)
