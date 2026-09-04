"""FAZ S1.1 — Total Station ham veri içe aktarma (Leica GSI, ham PENZD CSV).

Bu modül **hiçbir açı/mesafe verisini önceden E/N/Z'ye indirgemez** — bu
FAZ S2 (`geodetic_engine.reduction`) sorumluluğundadır. Burada sadece
cihazdan çıkan ham gözlem kaydı, format spesifikasyonuna göre birebir
ayrıştırılır. Spesifikasyona uymayan bir alan bulunursa sessizce
varsayılan değerle doldurulmaz — `MalformedRecordError` fırlatılır (bkz.
`feature_survey/pipeline.py` içindeki `ExternalToolNotAvailableError`
deseniyle aynı "açıkça hata ver" ilkesi).

Leica GSI format özeti (kaynak: Leica GSI Online/Offline format
spesifikasyonu — GSI8: her alan sabit 16 karakter, "WWFF+/-DD...D" biçimi;
GSI16: sabit 24 karakter, aynı kodlama, ekstra hane genişliği):

    110001+0000000A 84....+angle 21....+distance ...

Her satır kelime (word) bloklarından oluşur, her blok:
    WW  : 2 haneli word-index (veri tipi: 11=nokta no, 21=eğik mesafe,
          22=yatay açı, 25=düşey/zenit açısı, 87=alet yüksekliği,
          88=hedef/prizma yüksekliği, 41=kod/description ...)
    F   : 1 haneli "information"/birim/işaret alanı grubunun ilk hanesi
          (GSI'de aslında WW'den hemen sonra 2 haneli "information" +
          1 haneli işaret gelir; burada spesifikasyona sadık kalınarak
          ayrıştırılır, bkz. `_split_gsi_word`).
    +/-DD...D : işaretli sayısal değer (GSI8: 8 hane, GSI16: 16 hane).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class MalformedRecordError(ValueError):
    """Ham ölçüm kaydı, ilan edilen format spesifikasyonuna uymadığında
    fırlatılır. Asla varsayılan/uydurma bir değerle devam edilmez."""


class AngleUnit(str, Enum):
    """GSI word 22/25 information alanındaki birim koduna karşılık gelir
    (Leica GSI format spesifikasyonu, information hanesi 5-6)."""

    DEGREES_DMS = "0"  # DDDMMSS (derece-dakika-saniye, sıkıştırılmış)
    GON = "1"
    DEGREES_DECIMAL = "2"
    MIL = "3"


# GSI word-index (WW) -> anlam. Kaynak: Leica GSI8/16 format tablosu.
_GSI_WORD_MEANINGS: dict[str, str] = {
    "11": "point_id",
    "21": "slope_distance_mm",  # eğik mesafe (mm, GSI birim varsayılan)
    "22": "horizontal_angle",  # yatay açı
    "25": "zenith_angle",  # düşey (zenit) açısı
    "31": "coordinate_easting_mm",
    "32": "coordinate_northing_mm",
    "33": "coordinate_elevation_mm",
    "41": "point_code",
    "87": "target_height_mm",  # prizma/hedef yüksekliği
    "88": "instrument_height_mm",  # alet yüksekliği (bazı cihazlarda 88=HI)
}


@dataclass(slots=True)
class TotalStationObservation:
    """Tek bir ham total station gözlemi — **indirgenmemiş** açı/mesafe.

    Trigonometrik indirgeme (E/N/Z hesaplama) bilerek burada yapılmaz;
    bkz. `feature_survey.geodetic_engine.reduction.reduce_observation`.
    """

    point_id: str
    slope_distance_m: float | None = None  # eğik mesafe (metre)
    horizontal_angle_gon: float | None = None  # yatay açı (gon, 0-400)
    zenith_angle_gon: float | None = None  # zenit açısı (gon, 0-400)
    instrument_height_m: float | None = None
    target_height_m: float | None = None
    point_code: str | None = None
    raw_words: dict[str, str] = field(default_factory=dict)  # denetim izi


def _split_gsi_word(word: str, word_length: int) -> tuple[str, str, str]:
    """Bir GSI kelimesini (WW + information + işaretli değer) parçalara
    ayırır. `word_length` GSI8 için 16, GSI16 için 24 olmalıdır (WW hariç,
    toplam kelime uzunluğu = word_length + 2)."""

    total_len = word_length + 2
    if len(word) != total_len:
        raise MalformedRecordError(
            f"GSI kelime uzunluğu beklenen {total_len} değil, {len(word)}: {word!r}"
        )
    ww = word[0:2]
    information = word[2:8]  # 6 haneli information bloğu (Leica spesifikasyonu)
    signed_value = word[8:]
    sign = signed_value[0]
    if sign not in ("+", "-"):
        raise MalformedRecordError(
            f"GSI değer alanı işaret ile başlamalı (+/-), bulundu: {signed_value[0]!r} "
            f"(kelime: {word!r})"
        )
    digits = signed_value[1:]
    if not digits.isdigit():
        raise MalformedRecordError(f"GSI değer alanı sayısal değil: {digits!r} (kelime: {word!r})")
    return ww, information, ("-" if sign == "-" else "") + digits


def _gon_from_raw(raw_digits: str, information: str) -> float:
    """GSI açı değerini, information alanındaki birim koduna göre gon'a
    çevirir. Information hanesi 5 (0-indeksli), birim kodunu taşır."""

    unit_code = information[4] if len(information) >= 5 else "2"
    value = int(raw_digits)
    if unit_code == AngleUnit.GON.value:
        # 5 ondalık hane sıkıştırılmış gon (Leica konvansiyonu: son 5 hane ondalık)
        return value / 100000.0
    if unit_code == AngleUnit.DEGREES_DECIMAL.value:
        degrees = value / 100000.0
        return degrees * (400.0 / 360.0)
    if unit_code == AngleUnit.DEGREES_DMS.value:
        # DDDMMSSs (derece-dakika-saniye, ondalık saniye sıkıştırılmış)
        s = str(value).zfill(9)
        deg = int(s[0:3])
        minute = int(s[3:5])
        sec = int(s[5:7]) + int(s[7:9]) / 100.0
        decimal_degrees = deg + minute / 60.0 + sec / 3600.0
        return decimal_degrees * (400.0 / 360.0)
    raise MalformedRecordError(f"Bilinmeyen GSI açı birim kodu: {unit_code!r}")


def parse_gsi_line(line: str, word_length: int = 16) -> TotalStationObservation:
    """Tek bir GSI satırını ayrıştırır. `word_length`: GSI8=16, GSI16=24."""

    line = line.rstrip("\n\r")
    if not line.strip():
        raise MalformedRecordError("Boş GSI satırı ayrıştırılamaz.")

    full_word_len = word_length + 2
    if len(line) % full_word_len != 0:
        raise MalformedRecordError(
            f"GSI satır uzunluğu ({len(line)}) kelime uzunluğunun ({full_word_len}) "
            "katı değil — format bozuk veya word_length parametresi yanlış."
        )

    words: dict[str, str] = {}
    infos: dict[str, str] = {}
    for i in range(0, len(line), full_word_len):
        chunk = line[i : i + full_word_len]
        ww, information, signed_digits = _split_gsi_word(chunk, word_length)
        meaning = _GSI_WORD_MEANINGS.get(ww)
        if meaning is None:
            # Bilinmeyen word-index: veri kaybetmemek için raw_words'e kaydedilir,
            # ama gözlem alanlarına işlenmez (sessizce yok sayılmaz).
            words[f"unknown_{ww}"] = signed_digits
            continue
        words[meaning] = signed_digits
        infos[meaning] = information

    if "point_id" not in words:
        raise MalformedRecordError(f"GSI satırında nokta no (word 11) bulunamadı: {line!r}")

    obs = TotalStationObservation(
        point_id=words["point_id"].lstrip("0") or "0", raw_words=dict(words)
    )

    if "slope_distance_mm" in words:
        obs.slope_distance_m = int(words["slope_distance_mm"]) / 1000.0
    if "horizontal_angle" in words:
        obs.horizontal_angle_gon = _gon_from_raw(
            words["horizontal_angle"], infos["horizontal_angle"]
        )
    if "zenith_angle" in words:
        obs.zenith_angle_gon = _gon_from_raw(words["zenith_angle"], infos["zenith_angle"])
    if "instrument_height_mm" in words:
        obs.instrument_height_m = int(words["instrument_height_mm"]) / 1000.0
    if "target_height_mm" in words:
        obs.target_height_m = int(words["target_height_mm"]) / 1000.0
    if "point_code" in words:
        obs.point_code = words["point_code"]

    return obs


def parse_gsi_file(path: str | Path, word_length: int = 16) -> list[TotalStationObservation]:
    """Bir GSI dosyasının tamamını satır satır ayrıştırır. İlk hatalı
    satırda `MalformedRecordError` fırlatılır (kısmi/sessiz kabul yok);
    hangi satırın bozuk olduğu hata mesajına eklenir (denetlenebilirlik)."""

    path = Path(path)
    observations: list[TotalStationObservation] = []
    with path.open("r", encoding="ascii", errors="strict") as fh:
        for line_no, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                observations.append(parse_gsi_line(line, word_length=word_length))
            except MalformedRecordError as exc:
                raise MalformedRecordError(f"{path}:{line_no}: {exc}") from exc
    return observations


def parse_raw_penzd_csv(path: str | Path) -> list[TotalStationObservation]:
    """Ham (indirgenmemiş açı/mesafe değil, zaten E/N/Z'ye indirgenmiş)
    total station CSV çıktısı için PENZD ayrıştırma. Bu, S1.1'in "ikincil"
    yoludur — çoğu ucuz/eski total station yazılımı ham açı/mesafeyi değil
    doğrudan E/N/Z verir; bu durumda S2 trigonometrik indirgeme atlanır ve
    veri doğrudan `field_point.FieldPoint`'e (bkz. `io_import.py`) akar.
    Bu fonksiyon burada **sadece format tutarlılığı için** yer alır ve S1
    kapsamında ham açı/mesafe akışından ayrı tutulur; gerçek E/N/Z akışı
    zaten mevcut `io_import.import_penzd_csv` tarafından karşılanır.
    """

    raise NotImplementedError(
        "Zaten indirgenmiş E/N/Z PENZD CSV için feature_survey.io_import.import_penzd_csv "
        "kullanılmalıdır. Bu fonksiyon, ham açı/mesafe barındıran total station "
        "biçimleri için ayrı bir kaynak formatı gerektiğinde uygulanacaktır ve "
        "kasıtlı olarak varsayılan bir dönüşüm YAPMAZ (InsufficientDataError ilkesi)."
    )
