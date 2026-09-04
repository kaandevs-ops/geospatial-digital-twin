"""
Persistence & Proje Yönetimi
============================

Roadmap V2 - Faz 16 - "Persistence & Proje Yönetimi".

Alt modüller:
    project_format  — Sürümlenmiş proje dosyası formatı (`.hproj`, SQLite
                       tabanlı konteyner), şema migrasyonu
    db_backend      — `ProjectDatabase`: kalıcı disk backend (SQLite,
                       stdlib `sqlite3`), nesne/sahne/geçmiş depolama
    project_manager — `ProjectManager`: proje aç/kaydet/oto-kaydet, çoklu
                       proje kaydı (registry), son kullanılanlar listesi

Tasarım ilkesi: `data_engine` (Faz 10) bellek-içi `ObjectCache`/`SceneCache`
ile birebir aynı kalır — bu paket onun *üstüne* kalıcılık ekler, üzerine
yazmaz. `ObjectCache` RAM'de sıcak veriyi tutar; `ProjectDatabase` diskte
soğuk/kalıcı veriyi tutar. Bağımlılık: yalnızca stdlib (`sqlite3`, `json`).
"""

from .project_format import (
    FORMAT_VERSION,
    ProjectManifest,
    ProjectFormatError,
    MigrationError,
    migrate_schema,
)
from .db_backend import (
    ProjectDatabase,
    ObjectRecord,
)
from .project_manager import (
    ProjectManager,
    ProjectHandle,
    ProjectNotFoundError,
    ProjectAlreadyExistsError,
    BranchNotFoundError,
)
# Faz 5.2: PostgreSQL+PostGIS backend'i - opsiyonel (`psycopg` yoksa
# import başarılı olur, yalnızca create()/open() çağrıldığında
# `PostgresUnavailable` fırlatılır; import zamanında patlamaz).
from .postgres_backend import (
    PostgresProjectDatabase,
    PostgresUnavailable,
    PostGISExtensionMissing,
    footprint_to_wkt,
)

__all__ = [
    "FORMAT_VERSION",
    "ProjectManifest",
    "ProjectFormatError",
    "MigrationError",
    "migrate_schema",
    "ProjectDatabase",
    "ObjectRecord",
    "ProjectManager",
    "ProjectHandle",
    "ProjectNotFoundError",
    "ProjectAlreadyExistsError",
    "BranchNotFoundError",
    "PostgresProjectDatabase",
    "PostgresUnavailable",
    "PostGISExtensionMissing",
    "footprint_to_wkt",
]
