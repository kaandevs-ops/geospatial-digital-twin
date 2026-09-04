"""FAZ S1.4 — Drone fotogrametri: Ground Control Point (GCP) dosyası desteği.

Format: WebODM'in beklediği `gcp_list.txt` (kaynak: WebODM/ODM dokümantasyonu,
`docs.opendronemap.org` — GCP dosya formatı):

    Satır 1: projeksiyon tanımı (örn. "EPSG:32636" veya proj4 string)
    Sonraki satırlar: geo_x geo_y geo_z im_x im_y image_name [gcp_name] [extra1] [extra2]

`feature_survey/pipeline.py` içindeki `WebODMPipeline` bu dosyayı doğrudan
görev girdisine ekleyebilir — burada sadece okuma/yazma/doğrulama vardır,
WebODM'e gönderim pipeline.py'nin sorumluluğundadır (tek sorumluluk ilkesi).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class MalformedRecordError(ValueError):
    """GCP dosyası WebODM `gcp_list.txt` spesifikasyonuna uymadığında."""


@dataclass(slots=True)
class GroundControlPoint:
    geo_x: float
    geo_y: float
    geo_z: float
    image_x: float
    image_y: float
    image_name: str
    gcp_name: str | None = None


@dataclass(slots=True)
class GcpList:
    projection: str  # örn. "EPSG:32636" veya proj4 string — VARSAYILAN ATANMAZ
    points: list[GroundControlPoint]


def parse_gcp_list(path: str | Path) -> GcpList:
    """WebODM `gcp_list.txt` dosyasını ayrıştırır. İlk satır projeksiyon
    olmak zorundadır; boşsa/eksikse hata verilir (WebODM'in kendisi de
    projeksiyonsuz GCP dosyasını reddeder — bu davranış burada da taklit
    edilir, sessiz varsayım yok)."""

    path = Path(path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        raise MalformedRecordError(f"{path}: GCP dosyası boş.")

    projection = lines[0].strip()
    if not projection:
        raise MalformedRecordError(f"{path}: ilk satırda projeksiyon tanımı olmalı, boş bulundu.")

    points: list[GroundControlPoint] = []
    for line_no, line in enumerate(lines[1:], start=2):
        parts = line.split()
        if len(parts) < 6:
            raise MalformedRecordError(
                f"{path}:{line_no}: GCP satırı en az 6 alan gerektirir "
                f"(geo_x geo_y geo_z im_x im_y image_name), bulunan: {len(parts)}"
            )
        try:
            geo_x, geo_y, geo_z, im_x, im_y = (float(p) for p in parts[:5])
        except ValueError as exc:
            raise MalformedRecordError(
                f"{path}:{line_no}: sayısal alan ayrıştırılamadı: {line!r}"
            ) from exc
        image_name = parts[5]
        gcp_name = parts[6] if len(parts) > 6 else None
        points.append(
            GroundControlPoint(
                geo_x=geo_x,
                geo_y=geo_y,
                geo_z=geo_z,
                image_x=im_x,
                image_y=im_y,
                image_name=image_name,
                gcp_name=gcp_name,
            )
        )

    if not points:
        raise MalformedRecordError(f"{path}: projeksiyon satırından sonra hiç GCP noktası yok.")

    return GcpList(projection=projection, points=points)


def write_gcp_list(gcp_list: GcpList, path: str | Path) -> None:
    """`GcpList`'i WebODM `gcp_list.txt` formatında yazar (round-trip
    testi için — S1 kabul kriteri: oku→yaz→karşılaştır veri kaybı yok)."""

    path = Path(path)
    lines = [gcp_list.projection]
    for p in gcp_list.points:
        fields = [
            f"{p.geo_x}",
            f"{p.geo_y}",
            f"{p.geo_z}",
            f"{p.image_x}",
            f"{p.image_y}",
            p.image_name,
        ]
        if p.gcp_name is not None:
            fields.append(p.gcp_name)
        lines.append(" ".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
