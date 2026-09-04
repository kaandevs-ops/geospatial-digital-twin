"""
Feature Survey (Saha Özellik Ölçümü)
=====================================

YENİ MOD — harita mühendisliği "feature survey" iş akışını (saha ölçümü →
dijitalleştirme → dijital ikiz) platforma ayrı bir mod olarak ekler.

Kapsam ve tasarım kararı
-------------------------
Bu modül **kendi bir SfM/fotogrametri motoru yeniden yazmaz**. Photogrammetry
(Structure-from-Motion) hesaplaması GPU-yoğun, onlarca yıllık optimize C++
kod tabanına dayanan bir problemdir (COLMAP, AliceVision/Meshroom, ODM hepsi
bunu yapar) — bunu stdlib Python'la yeniden üretmek hem gerçekçi değil hem de
var olan açık kaynak araçlardan daha kötü sonuç verir. Bunun yerine:

    1. **Saha veri modeli** (`codes.py`, `field_point.py`): mühendisin sahada
       total station / RTK-GNSS ile topladığı kodlanmış noktaları
       (bina köşesi, yol kenarı, direk, ağaç, rögar ...) temsil eder.
    2. **İçe aktarma** (`io_import.py`): yaygın saha veri formatlarını
       (PENZD CSV, basit GNSS NMEA/CSV çıktısı) okur.
    3. **Dış pipeline köprüsü** (`pipeline.py`): WebODM/ODM ve Meshroom gibi
       kurulu-ise-çalıştırılan harici açık kaynak araçları **orkestre eder**
       (REST API / CLI çağrısı) — bu araçlar kurulu değilse net bir hata
       verir, sessizce sahte sonuç üretmez (projenin genelindeki
       `UnsupportedFormatError` deseniyle tutarlı).
    4. **Dijitalleştirme köprüsü** (`bridge.py`): saha ölçüm oturumunu
       mevcut `core_engine.gis_core.GeoFeature` / `GeoFeatureCollection`'a
       ve nokta bulutu sonucunu mevcut `core_engine.gis_core.point_cloud.
       PointCloud`'a bağlar — böylece feature survey çıktısı, platformun
       zaten sahip olduğu mesh/terrain/BIM ardılına doğrudan akar.

Uçtan uca akış
--------------
    Sahada ölçüm (total station / RTK-GNSS / drone fotoğrafları)
      → FieldSurveySession (kodlanmış noktalar)              [bu modül]
      → (opsiyonel) WebODM/Meshroom ile fotoğraflardan nokta bulutu + mesh
      → bridge.session_to_geofeatures()  → GeoFeatureCollection (vektör)
      → bridge.photogrammetry_result_to_point_cloud() → PointCloud
      → PointCloud.to_heightmap_grid() → terrain_engine (mevcut)
      → mesh_engine / building_reconstruction (mevcut) → dijital ikiz

Bu modül, roadmap'teki "Videoda geçen Feature Survey" isteğine karşılık
gelen yeni bir faz olarak `yeni_roadmap.md`'ye de eklenmiştir (bkz. FAZ 6).
"""

from __future__ import annotations

# ROADMAP_V6 FAZ S1 (ham veri içe aktarma) ve FAZ S2 (jeodezik hesap motoru) —
# alt paketler olarak eklendi; geriye dönük uyumluluk için üst seviyede
# yeniden dışa aktarılmaz (isim çakışmasını önlemek için `feature_survey.raw_import`
# ve `feature_survey.geodetic_engine` olarak doğrudan import edilmelidir).
from . import (
    geodetic_engine,  # noqa: F401  (FAZ S2)
    orchestration,  # noqa: F401  (FAZ S6)
    pointcloud_engine,  # noqa: F401  (FAZ S3)
    qc,  # noqa: F401  (FAZ S5)
    raw_import,  # noqa: F401  (FAZ S1)
    vectorization,  # noqa: F401  (FAZ S4)
)
from .bridge import photogrammetry_result_to_point_cloud, session_to_geofeatures
from .codes import FeatureCategory, FeatureCode
from .field_point import FieldPoint, FieldSurveySession
from .io_import import PENZDImportError, import_penzd_csv
from .pipeline import (
    ExternalToolNotAvailableError,
    MeshroomPipeline,
    PhotogrammetryResult,
    WebODMPipeline,
)

__all__ = [
    "FeatureCategory",
    "FeatureCode",
    "FieldPoint",
    "FieldSurveySession",
    "PENZDImportError",
    "import_penzd_csv",
    "ExternalToolNotAvailableError",
    "MeshroomPipeline",
    "PhotogrammetryResult",
    "WebODMPipeline",
    "photogrammetry_result_to_point_cloud",
    "session_to_geofeatures",
]
