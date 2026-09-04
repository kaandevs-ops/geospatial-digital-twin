"""FAZ S1.2 — RTK-GNSS ham veri içe aktarma: NMEA-0183 (GGA/GST/GSA).

Kaynak spesifikasyon: NMEA-0183 standardı, ilgili cümle formatları:
    $--GGA,time,lat,N/S,lon,E/W,fix_quality,num_sats,hdop,alt,M,geoid_sep,M,,*CS
    $--GST,time,rms,semi_major,semi_minor,orientation,std_lat,std_lon,std_alt*CS
    $--GSA,mode1,mode2,sat1..sat12,pdop,hdop,vdop*CS

Checksum (CS): '$' ve '*' arasındaki tüm karakterlerin XOR'u, 2 haneli hex.
Checksum tutmayan cümle **reddedilir** (`ChecksumError`) — sessizce kabul
edilmez, çünkü hatalı checksum veri bozulması/aktarım hatası anlamına gelir
ve konum hesaplamasında sessizce kullanılırsa yanlış "kesin" sonuç üretir.

Fix quality kodları (GGA alanı 6, NMEA standardı):
    0 = geçersiz, 1 = GPS (autonomous), 2 = DGPS, 4 = RTK Fixed, 5 = RTK Float
Fixed (4) dışındaki her epoch, roadmap ilkesi gereği ayrı işaretlenir ve
kullanıcı bilinçli override etmedikçe "kontrol/detay noktası" sayılmaz.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class ChecksumError(ValueError):
    """NMEA cümlesinin checksum'u tutmadığında fırlatılır."""


class MalformedRecordError(ValueError):
    """NMEA cümlesi alan sayısı/format açısından spesifikasyona uymadığında."""


class FixQuality(IntEnum):
    INVALID = 0
    GPS_AUTONOMOUS = 1
    DGPS = 2
    PPS = 3
    RTK_FIXED = 4
    RTK_FLOAT = 5
    ESTIMATED = 6
    MANUAL = 7
    SIMULATION = 8

    @property
    def is_survey_grade(self) -> bool:
        """Sadece RTK Fixed, doğrudan "kontrol/detay noktası" kalitesinde
        kabul edilir — roadmap S1.2 ilkesi (Float/Autonomous otomatik
        ayrı işaretlenir, override gerektirir)."""
        return self is FixQuality.RTK_FIXED


@dataclass(slots=True)
class GnssEpoch:
    """Tek bir GNSS epoch'unun (GGA + varsa eşleşen GST/GSA) birleşik
    kaydı. `is_survey_grade` False ise çağıran kod bu noktayı otomatik
    olarak "kontrol noktası" kabul ETMEMELİDİR."""

    utc_time: str
    latitude_deg: float
    longitude_deg: float
    ellipsoidal_height_m: float
    fix_quality: FixQuality
    num_satellites: int
    hdop: float
    geoid_separation_m: float | None = None
    vdop: float | None = None
    pdop: float | None = None
    std_lat_m: float | None = None
    std_lon_m: float | None = None
    std_alt_m: float | None = None

    @property
    def is_survey_grade(self) -> bool:
        return self.fix_quality.is_survey_grade


def _verify_checksum(sentence: str) -> str:
    """`$...*CS` biçimindeki bir NMEA cümlesini doğrular, checksum ve
    baştaki '$'/sondaki CRLF olmadan gövdeyi döndürür."""

    sentence = sentence.strip()
    if not sentence.startswith("$"):
        raise MalformedRecordError(f"NMEA cümlesi '$' ile başlamalı: {sentence!r}")
    if "*" not in sentence:
        raise MalformedRecordError(f"NMEA cümlesinde checksum ayracı '*' yok: {sentence!r}")
    body, _, checksum_hex = sentence[1:].partition("*")
    checksum_hex = checksum_hex.strip()
    if len(checksum_hex) < 2:
        raise MalformedRecordError(f"NMEA checksum alanı eksik: {sentence!r}")
    checksum_hex = checksum_hex[:2]
    computed = 0
    for ch in body:
        computed ^= ord(ch)
    expected = int(checksum_hex, 16)
    if computed != expected:
        raise ChecksumError(
            f"NMEA checksum uyuşmazlığı: hesaplanan={computed:02X} beklenen={expected} "
            f"cümle={sentence!r}"
        )
    return body


def _nmea_lat_lon(lat_raw: str, lat_hem: str, lon_raw: str, lon_hem: str) -> tuple[float, float]:
    """NMEA ddmm.mmmm / dddmm.mmmm biçimini ondalık dereceye çevirir."""

    if not lat_raw or not lon_raw:
        raise MalformedRecordError("NMEA konum alanı boş — enterpolasyon/varsayım yapılmaz.")
    lat_deg = int(lat_raw[:2])
    lat_min = float(lat_raw[2:])
    lat = lat_deg + lat_min / 60.0
    if lat_hem == "S":
        lat = -lat
    elif lat_hem != "N":
        raise MalformedRecordError(f"Geçersiz enlem yarımküre kodu: {lat_hem!r}")

    lon_deg = int(lon_raw[:3])
    lon_min = float(lon_raw[3:])
    lon = lon_deg + lon_min / 60.0
    if lon_hem == "W":
        lon = -lon
    elif lon_hem != "E":
        raise MalformedRecordError(f"Geçersiz boylam yarımküre kodu: {lon_hem!r}")

    return lat, lon


def parse_gga(sentence: str) -> GnssEpoch:
    body = _verify_checksum(sentence)
    fields_ = body.split(",")
    if len(fields_) < 14 or not fields_[0].endswith("GGA"):
        raise MalformedRecordError(f"Geçerli bir GGA cümlesi değil: {sentence!r}")

    _, utc_time, lat_raw, lat_hem, lon_raw, lon_hem, fix_q, num_sats, hdop, alt, alt_unit, geoid, geoid_unit, *_ = fields_

    if not fix_q.isdigit():
        raise MalformedRecordError(f"GGA fix quality sayısal değil: {fix_q!r}")
    quality = FixQuality(int(fix_q))
    if quality is FixQuality.INVALID:
        raise MalformedRecordError("GGA fix quality=0 (geçersiz çözüm) — nokta kabul edilemez.")

    lat, lon = _nmea_lat_lon(lat_raw, lat_hem, lon_raw, lon_hem)

    if not alt:
        raise MalformedRecordError("GGA yükseklik alanı boş.")

    return GnssEpoch(
        utc_time=utc_time,
        latitude_deg=lat,
        longitude_deg=lon,
        ellipsoidal_height_m=float(alt) + (float(geoid) if geoid else 0.0),
        fix_quality=quality,
        num_satellites=int(num_sats) if num_sats.isdigit() else 0,
        hdop=float(hdop) if hdop else float("nan"),
        geoid_separation_m=float(geoid) if geoid else None,
    )


def apply_gst(epoch: GnssEpoch, sentence: str) -> GnssEpoch:
    """Bir GST cümlesinden gerçek standart sapma değerlerini epoch'a ekler.
    GST verisi yoksa bu fonksiyon çağrılmaz — epoch std_* alanları None
    kalır (uydurma bir "tipik" sigma değeri asla atanmaz)."""

    body = _verify_checksum(sentence)
    fields_ = body.split(",")
    if len(fields_) < 8 or not fields_[0].endswith("GST"):
        raise MalformedRecordError(f"Geçerli bir GST cümlesi değil: {sentence!r}")
    _, _utc, _rms, _semi_major, _semi_minor, _orientation, std_lat, std_lon, *rest = fields_
    std_alt = rest[0] if rest else ""
    epoch.std_lat_m = float(std_lat) if std_lat else None
    epoch.std_lon_m = float(std_lon) if std_lon else None
    epoch.std_alt_m = float(std_alt) if std_alt else None
    return epoch


def apply_gsa(epoch: GnssEpoch, sentence: str) -> GnssEpoch:
    """Bir GSA cümlesinden PDOP/VDOP değerlerini epoch'a ekler."""

    body = _verify_checksum(sentence)
    fields_ = body.split(",")
    if len(fields_) < 17 or not fields_[0].endswith("GSA"):
        raise MalformedRecordError(f"Geçerli bir GSA cümlesi değil: {sentence!r}")
    pdop, hdop, vdop = fields_[-3], fields_[-2], fields_[-1]
    epoch.pdop = float(pdop) if pdop else None
    if hdop:
        epoch.hdop = float(hdop)
    epoch.vdop = float(vdop) if vdop else None
    return epoch


def parse_nmea_log(path: str | Path) -> list[GnssEpoch]:
    """Bir NMEA log dosyasını (karışık GGA/GST/GSA satırları) okuyup,
    zaman damgasına göre eşleşen cümleleri tek `GnssEpoch` kaydında
    birleştirir. Checksum hatalı satırlar reddedilir (satır numarasıyla
    birlikte hata verilir, denetlenebilirlik için)."""

    path = Path(path)
    epochs_by_time: dict[str, GnssEpoch] = {}
    order: list[str] = []
    with path.open("r", encoding="ascii", errors="strict") as fh:
        for line_no, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line or not line.startswith("$"):
                continue
            try:
                if "GGA" in line.split(",")[0]:
                    epoch = parse_gga(line)
                    if epoch.utc_time not in epochs_by_time:
                        order.append(epoch.utc_time)
                    epochs_by_time[epoch.utc_time] = epoch
                elif "GST" in line.split(",")[0]:
                    utc_time = line.split(",")[1] if "," in line else None
                    if utc_time and utc_time in epochs_by_time:
                        apply_gst(epochs_by_time[utc_time], line)
                elif "GSA" in line.split(",")[0]:
                    # GSA'da zaman damgası yok; son eklenen epoch'a uygulanır
                    # (aynı epoch grubu içinde geldiği varsayımıyla — tipik NMEA
                    # log sırası budur; belirsizse çağıran kod ham cümleyi
                    # ayrıca saklayabilir).
                    if order:
                        apply_gsa(epochs_by_time[order[-1]], line)
            except (MalformedRecordError, ChecksumError) as exc:
                raise type(exc)(f"{path}:{line_no}: {exc}") from exc

    return [epochs_by_time[t] for t in order]
