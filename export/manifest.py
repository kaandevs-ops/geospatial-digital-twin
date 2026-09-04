"""Scene Manifest
================

Kullanıcı talebi (madde 3): "somut manifest scripti ekle projeye".

Bir proje export edildiğinde (`app_shell.session.AppSession.export_scene`),
diskte tek tek dosyalar (.obj/.stl/.ply/.glb/.dxf/.ifc/3D Tiles dizini)
oluşuyor ama bunları **tek bir yerden, makine-okunur şekilde** kataloglayan
hiçbir şey yoktu — bir CI/CD adımının veya harici bir motorun (Unity,
Unreal, bir web viewer) "bu export'ta hangi dosyalar var, her biri hangi
binaya karşılık geliyor, bütünlükleri (checksum) doğru mu" sorusuna cevap
verecek somut bir manifest dosyası yoktu.

`SceneManifest` / `ManifestBuilder` bunu üretir: `manifest.json` — şema
sürümü, üretim zamanı, proje kimliği, her export dosyası için
biçim/yol/boyut/SHA-256 checksum/(varsa) vertex-triangle sayısı, ve her
bina için kimlik/tip/kat sayısı/yükseklik/bbox.

Somut CLI script'i: `scripts/generate_manifest.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "ManifestBuildingEntry",
    "ManifestFileEntry",
    "SceneManifest",
    "ManifestBuilder",
    "ManifestValidationError",
    "load_manifest",
    "verify_manifest_checksums",
]

MANIFEST_SCHEMA_VERSION = "1.0"


class ManifestValidationError(Exception):
    """Bir manifest dosyası beklenen şemaya uymuyor veya bir dosya
    checksum'ı diskteki gerçek içerikle eşleşmiyor."""


@dataclass(slots=True)
class ManifestFileEntry:
    """Tek bir export çıktısını (dosya veya 3D Tiles dizini) tanımlar."""

    format: str
    path: str
    bytes_written: int
    sha256: Optional[str] = None
    vertex_count: Optional[int] = None
    triangle_count: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(slots=True)
class ManifestBuildingEntry:
    """Sahnedeki tek bir binayı tanımlar (export dosyalarının hangi
    binalardan oluştuğunu izlenebilir kılmak için)."""

    building_id: str
    building_type: str
    floor_count: int
    height_m: float
    bbox_min: tuple[float, float]
    bbox_max: tuple[float, float]

    def to_dict(self) -> dict[str, Any]:
        return {
            "building_id": self.building_id,
            "building_type": self.building_type,
            "floor_count": self.floor_count,
            "height_m": round(self.height_m, 3),
            "bbox_min": list(self.bbox_min),
            "bbox_max": list(self.bbox_max),
        }


@dataclass(slots=True)
class SceneManifest:
    """`manifest.json`'un tam içeriği."""

    schema_version: str
    project_id: str
    generated_at: str
    generator: str
    building_count: int
    buildings: list[ManifestBuildingEntry] = field(default_factory=list)
    files: list[ManifestFileEntry] = field(default_factory=list)
    crs: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "generated_at": self.generated_at,
            "generator": self.generator,
            "building_count": self.building_count,
            "buildings": [b.to_dict() for b in self.buildings],
            "files": [f.to_dict() for f in self.files],
        }
        if self.crs:
            d["crs"] = self.crs
        if self.extra:
            d["extra"] = self.extra
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def write(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")
        return p


def _sha256_of(path: Path) -> Optional[str]:
    """Bir dosyanın (veya 3D Tiles gibi bir dizinin) SHA-256 özetini
    hesaplar. Dizinse, içindeki tüm dosyaların göreli-yol-sıralı
    birleşik özetini alır (deterministik). Yol hiç mevcut değilse `None`
    döner (örn. bazı export formatlarında yalnızca bytes_written raporlanır,
    gerçek path her zaman garanti değildir)."""
    if not path.exists():
        return None
    hasher = hashlib.sha256()
    if path.is_file():
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    # Dizin (örn. 3D Tiles çıktısı): tüm dosyaları göreli yola göre
    # sıralayıp sırayla hash'e ekle - girdi sırasından bağımsız, deterministik.
    for sub in sorted(path.rglob("*")):
        if sub.is_file():
            hasher.update(str(sub.relative_to(path)).encode("utf-8"))
            with open(sub, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    hasher.update(chunk)
    return hasher.hexdigest()


class ManifestBuilder:
    """`AppSession.export_scene()` çağrılarının sonuçlarından ve proje
    bina listesinden bir `SceneManifest` inşa eder."""

    @staticmethod
    def build(
        project_id: str,
        building_entries: list[Any],
        export_results: list[dict[str, Any]],
        *,
        crs: Optional[str] = None,
        compute_checksums: bool = True,
        generator: str = "harita-modelleme",
        extra: Optional[dict[str, Any]] = None,
    ) -> SceneManifest:
        """`building_entries`: `app_shell.session`'daki `_BuildingEntry`
        benzeri, `.key` ve `.building` (bir `Building` nesnesi) alanlarına
        sahip nesnelerin listesi. `export_results`: `export_scene()`'in
        dönüş sözlüklerinin listesi (`{"format", "path", "bytes_written", ...}`).
        """
        buildings: list[ManifestBuildingEntry] = []
        for entry in building_entries:
            building = getattr(entry, "building", entry)
            footprint = building.footprint
            xs = [p.x for p in footprint.polygon.points]
            ys = [p.y for p in footprint.polygon.points]
            buildings.append(ManifestBuildingEntry(
                building_id=getattr(entry, "key", getattr(footprint, "building_type", "bina")),
                building_type=str(building.building_type.value if hasattr(building.building_type, "value") else building.building_type),
                floor_count=len(building.floors),
                height_m=building.total_height_m,
                bbox_min=(min(xs), min(ys)) if xs else (0.0, 0.0),
                bbox_max=(max(xs), max(ys)) if xs else (0.0, 0.0),
            ))

        files: list[ManifestFileEntry] = []
        for res in export_results:
            checksum = None
            if compute_checksums and res.get("path"):
                checksum = _sha256_of(Path(res["path"]))
            files.append(ManifestFileEntry(
                format=res["format"],
                path=res["path"],
                bytes_written=res.get("bytes_written", 0),
                sha256=checksum,
                vertex_count=res.get("vertex_count"),
                triangle_count=res.get("triangle_count"),
            ))

        return SceneManifest(
            schema_version=MANIFEST_SCHEMA_VERSION,
            project_id=project_id,
            generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            generator=f"{generator} (python {platform.python_version()})",
            building_count=len(buildings),
            buildings=buildings,
            files=files,
            crs=crs,
            extra=extra or {},
        )


def load_manifest(path: str | Path) -> dict[str, Any]:
    """Bir `manifest.json`'u okur ve temel şema alanlarını doğrular."""
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    required = {"schema_version", "project_id", "generated_at", "building_count", "files"}
    missing = required - set(data)
    if missing:
        raise ManifestValidationError(f"manifest eksik alan(lar): {sorted(missing)}")
    return data


def verify_manifest_checksums(path: str | Path) -> list[str]:
    """Bir manifest'teki her dosya kaydının SHA-256'sını diskteki gerçek
    içerikle karşılaştırır. Uyuşmayan (veya artık bulunamayan) dosyaların
    yollarını döner - boş liste = hepsi tutarlı."""
    data = load_manifest(path)
    manifest_dir = Path(path).parent
    mismatches: list[str] = []
    for entry in data.get("files", []):
        recorded = entry.get("sha256")
        if not recorded:
            continue
        file_path = Path(entry["path"])
        if not file_path.is_absolute():
            file_path = manifest_dir / file_path
        actual = _sha256_of(file_path)
        if actual != recorded:
            mismatches.append(entry["path"])
    return mismatches
