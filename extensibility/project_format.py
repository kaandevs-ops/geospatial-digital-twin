"""
Proje Dosya Formatı (.harita) & Sürüm Yönetimi
================================================

Roadmap Phase 14 - "Proje dosya formatı ve sürüm yönetimi".

`.harita` dosyası, bir sahnenin (bir veya daha çok `DigitalTwin` +
meta veri) JSON tabanlı, insan-okunabilir serileştirilmiş halidir.
`DigitalTwin.to_dict()/from_dict()` (Phase 5) doğrudan kullanılır - bu
modül onun üzerine proje-seviyesi bir zarf (envelope: isim, oluşturulma
zamanı, şema sürümü) ve semantic-versioning tabanlı migration altyapısı
ekler.

Kapsam:
    * `ProjectFile`        - proje verisi + JSON (de)serileştirme.
    * `SCHEMA_VERSION`     - güncel şema sürümü (semver: major.minor.patch).
    * `MIGRATIONS`         - ``{from_version: (to_version, fn)}`` kayıt tablosu.
    * `migrate_project_file` - eski bir dosyayı adım adım güncel şemaya taşır.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Tuple

from ..digital_twin import DigitalTwin

# Güncel şema sürümü. Yeni bir alan eklendiğinde/formatı değiştiğinde
# artırılır ve `MIGRATIONS` tablosuna bir geçiş fonksiyonu eklenir.
SCHEMA_VERSION = "1.1.0"

# ``{eski_sürüm: (yeni_sürüm, migrate_fn)}``
# migrate_fn: ham dict -> ham dict (bir sonraki sürümün şeklinde).
MIGRATIONS: Dict[str, Tuple[str, Callable[[dict], dict]]] = {}


def register_migration(from_version: str, to_version: str) -> Callable:
    """`MIGRATIONS` tablosuna bir geçiş fonksiyonu ekleyen dekoratör."""

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        MIGRATIONS[from_version] = (to_version, fn)
        return fn

    return decorator


@register_migration("1.0.0", "1.1.0")
def _migrate_1_0_0_to_1_1_0(data: dict) -> dict:
    """
    v1.0.0 -> v1.1.0: proje meta verisine `tags` alanı eklendi
    (yoksa boş liste ile doldurulur - geriye dönük uyumluluk).
    """
    data = dict(data)
    data.setdefault("tags", [])
    return data


class ProjectFileError(Exception):
    pass


class UnknownSchemaVersionError(ProjectFileError):
    """Dosyanın şema sürümü için bilinen bir migration yolu yoksa fırlatılır."""


@dataclass
class ProjectFile:
    """
    Tek bir `.harita` proje dosyasının bellek-içi temsili.

    `twins`, id -> `DigitalTwin` eşlemesidir (Phase 5 `DigitalTwinRegistry`
    ile aynı anahtar sözleşmesi - bir `ProjectFile`, bir registry'nin
    "disk üzerindeki" görünümü olarak düşünülebilir).
    """

    name: str
    schema_version: str = SCHEMA_VERSION
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    twins: Dict[str, DigitalTwin] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # -- oluşturma ---------------------------------------------------------
    @staticmethod
    def create(name: str) -> "ProjectFile":
        return ProjectFile(name=name)

    # -- serileştirme --------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "tags": list(self.tags),
            "twins": {tid: t.to_dict() for tid, t in self.twins.items()},
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: dict) -> "ProjectFile":
        version = data.get("schema_version", "1.0.0")
        if version != SCHEMA_VERSION:
            raise UnknownSchemaVersionError(
                f"beklenmeyen şema sürümü '{version}' (güncel: '{SCHEMA_VERSION}'); "
                "önce migrate_project_file() ile taşıyın"
            )
        twins = {tid: DigitalTwin.from_dict(t) for tid, t in data.get("twins", {}).items()}
        return ProjectFile(
            name=data["name"],
            schema_version=version,
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            tags=list(data.get("tags", [])),
            twins=twins,
            metadata=dict(data.get("metadata", {})),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    @staticmethod
    def from_json(raw: str) -> "ProjectFile":
        return ProjectFile.from_dict(json.loads(raw))

    # -- dosya G/Ç -----------------------------------------------------------
    def save(self, path: str) -> None:
        self.updated_at = time.time()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.to_json())

    @staticmethod
    def load(path: str) -> "ProjectFile":
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
        version = data.get("schema_version", "1.0.0")
        if version != SCHEMA_VERSION:
            data = _migrate_raw(data)
        return ProjectFile.from_dict(data)

    # -- kayıt yönetimi (DigitalTwinRegistry benzeri kolaylık) -----------------
    def add_twin(self, twin_id: str, twin: DigitalTwin) -> None:
        self.twins[twin_id] = twin

    def remove_twin(self, twin_id: str) -> None:
        self.twins.pop(twin_id, None)

    def get_twin(self, twin_id: str) -> DigitalTwin:
        return self.twins[twin_id]


def _migrate_raw(data: dict) -> dict:
    """Ham dict'i, kayıtlı `MIGRATIONS` zincirini izleyerek `SCHEMA_VERSION`'a taşır."""
    version = data.get("schema_version", "1.0.0")
    visited = set()
    while version != SCHEMA_VERSION:
        if version in visited:
            raise ProjectFileError(f"migration döngüsü tespit edildi: '{version}'")
        visited.add(version)
        step = MIGRATIONS.get(version)
        if step is None:
            raise UnknownSchemaVersionError(
                f"'{version}' sürümünden '{SCHEMA_VERSION}' sürümüne bilinen bir migration yolu yok"
            )
        to_version, fn = step
        data = fn(data)
        data["schema_version"] = to_version
        version = to_version
    return data


def migrate_project_file(project_file: ProjectFile) -> Tuple[ProjectFile, str, str]:
    """
    Bir `ProjectFile`'ı güncel şemaya taşır.

    Geriye ``(migrated_project_file, eski_sürüm, yeni_sürüm)`` döner. Proje
    zaten güncelse `eski_sürüm == yeni_sürüm` olur ve hiçbir şey değişmez.
    """
    from_version = project_file.schema_version
    if from_version == SCHEMA_VERSION:
        return project_file, from_version, SCHEMA_VERSION

    raw = project_file.to_dict()
    raw["schema_version"] = from_version
    migrated_raw = _migrate_raw(raw)
    migrated = ProjectFile.from_dict(migrated_raw)
    return migrated, from_version, SCHEMA_VERSION
