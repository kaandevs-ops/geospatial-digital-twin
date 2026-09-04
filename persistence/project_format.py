"""
Proje Dosyası Formatı
=====================

`.hproj` dosyası, tek bir SQLite veritabanıdır (bkz. `db_backend.py`).
Bu modül yalnızca formatın **sürüm sözleşmesini** tanımlar:

- `FORMAT_VERSION`   : şu anki en güncel şema sürümü (int, monoton artar).
- `ProjectManifest`  : proje meta verisi (isim, oluşturulma/güncellenme
  zamanı, format sürümü, açıklama, etiketler) — `meta` tablosunda anahtar/
  değer çiftleri olarak saklanır.
- `migrate_schema()` : eski bir `.hproj` dosyasını adım adım (v(n) -> v(n+1))
  güncel şemaya taşır. Her adım kendi fonksiyonuyla temsil edilir, böylece
  1 -> 5 gibi çok adımlı bir geçiş de tek tek, izlenebilir şekilde uygulanır.

Geriye uyumluluk ilkesi: bir sonraki format sürümü her zaman bir önceki
sürümden migrate edilebilir olmalı; migrasyon fonksiyonu olmayan bir sürüm
sıçraması `MigrationError` fırlatır (sessizce veri kaybetmek yerine).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field

#: Güncel şema sürümü. Yeni alan/tablo eklenince artırılır ve
#: `_MIGRATIONS` sözlüğüne (eski_surum -> yeni_surum) fonksiyonu eklenir.
FORMAT_VERSION = 1


class ProjectFormatError(Exception):
    """Proje dosyası formatı bozuk, tanınmıyor veya desteklenmiyor."""


class MigrationError(ProjectFormatError):
    """Bilinen bir migrasyon yolu bulunamadı (versiyon atlaması vs.)."""


@dataclass
class ProjectManifest:
    """Bir `.hproj` dosyasının meta verisi.

    `meta` tablosunda `key TEXT PRIMARY KEY, value TEXT` çiftleri olarak
    saklanır; `to_dict`/`from_dict` bu tablo satırlarıyla karşılıklı
    dönüşüm sağlar.
    """

    name: str
    project_id: str
    format_version: int = FORMAT_VERSION
    description: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    crs: str = "EPSG:4326"

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "project_id": self.project_id,
            "format_version": str(self.format_version),
            "description": self.description,
            "tags": "\u001f".join(self.tags),  # unit-separator ile ayrık
            "created_at": repr(self.created_at),
            "updated_at": repr(self.updated_at),
            "crs": self.crs,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> ProjectManifest:
        try:
            return cls(
                name=data["name"],
                project_id=data["project_id"],
                format_version=int(data.get("format_version", "1")),
                description=data.get("description", ""),
                tags=tuple(t for t in data.get("tags", "").split("\u001f") if t),
                created_at=float(data.get("created_at", time.time())),
                updated_at=float(data.get("updated_at", time.time())),
                crs=data.get("crs", "EPSG:4326"),
            )
        except (KeyError, ValueError) as exc:
            raise ProjectFormatError(f"Bozuk proje manifesti: {exc}") from exc


def _migrate_0_to_1(conn: sqlite3.Connection) -> None:
    """v0 (şema öncesi / eski ham tablo) -> v1: standart şemayı kurar.

    v0 diye bir şey pratikte hiç yayınlanmadı; bu fonksiyon yalnızca
    migrasyon zincirinin nasıl genişletileceğini göstermek ve gelecekteki
    "meta tablosu yok" durumunu (bozuk/elle oluşturulmuş dosya) tolere
    etmek için var.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")


#: (from_version -> to_version) : migrasyon fonksiyonu.
_MIGRATIONS: dict[tuple[int, int], Callable[[sqlite3.Connection], None]] = {
    (0, 1): _migrate_0_to_1,
}


def migrate_schema(
    conn: sqlite3.Connection, from_version: int, to_version: int = FORMAT_VERSION
) -> int:
    """`conn` üzerindeki şemayı `from_version`'dan `to_version`'a taşır.

    Ardışık (n -> n+1) adımlarla ilerler; iki sürüm arasında tanımlı bir
    adım yoksa `MigrationError` fırlatır. Dönüş: ulaşılan son sürüm.
    """
    if from_version == to_version:
        return to_version
    if from_version > to_version:
        raise MigrationError(f"Geriye migrasyon desteklenmiyor: v{from_version} -> v{to_version}")
    current = from_version
    while current < to_version:
        step = (current, current + 1)
        migrator = _MIGRATIONS.get(step)
        if migrator is None:
            raise MigrationError(f"v{current} -> v{current + 1} için migrasyon adımı tanımlı değil")
        migrator(conn)
        current += 1
    return current
