"""
vegetation.osm_bridge - OSM Kategori Feature'larından Bitki Örtüsü Üretimi
============================================================================

ROADMAP_V7.md Faz C3 ("Prosedürel üretim genişletme") — Bölüm B2/B4'ün
"nokta feature" ve "yoğunluk bazlı dağılım" stratejilerini, Faz C2'de
eklenen `core_engine.gis_core.osm_client.OSMCategoryParser` çıktısına
(kategori damgalı `GeoFeature` listesi) bağlar.

Bu modül **iki senaryoyu** kapsar:

1. Tekil ağaç (`__category__ == "trees"`, OSM `natural=tree`, geometry
   "Point") -> tek bir `VegetationInstance` + (istenirse) doğrudan
   `TreeGenerator.generate()` ile `Mesh3D`. Tür/yükseklik OSM tag'lerinden
   (varsa) çıkarılır, yoksa B4'ün istediği "tür bazlı ortalama tablo"
   heuristiğine düşülür (`DEFAULT_SPECIES_HEIGHT_M`).
2. Orman/koru alanı (`__category__ in {"forest", "wood"}`, geometry
   "Polygon") -> `VegetationScatterer.scatter_polygon_poisson_disc` ile
   alan içine yoğunluk bazlı, çakışmasız ağaç dağılımı.

Girdi olarak `osm_client.project_to_local_meters` ile zaten metreye
projekte edilmiş bir `GeoFeatureCollection` beklenir (WGS84 derece
üzerinde metre cinsinden `min_distance_m` gibi parametrelerin bir anlamı
olmaz — roadmap'in "mevcut mimari korunacak" ilkesi gereği aynı
projeksiyon adımı, bina boru hattında olduğu gibi burada da zorunlu).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D, Polygon
from ..core_engine.gis_core import GeoFeature, GeoFeatureCollection
from .scatter import VegetationScatterer
from .tree_generator import TreeGenerator
from .types import TreeSpecies, VegetationInstance

#: B4 — "ağaç yüksekliği için tür bazlı ortalama tablo" (OSM'de `height`
#: tag'i genelde eksiktir). Değerler kaba, tipik yetişkin ağaç yükseklik
#: aralıklarının orta noktasına yakın metre cinsinden sabitlerdir —
#: botanik hassasiyet iddiası taşımaz, yalnızca görsel makuliyet içindir.
DEFAULT_SPECIES_HEIGHT_M: dict[TreeSpecies, float] = {
    TreeSpecies.CONIFER: 12.0,
    TreeSpecies.DECIDUOUS: 9.0,
    TreeSpecies.SHRUB: 1.5,
    TreeSpecies.GENERIC: 7.0,
    # Roadmap V8 Faz 5.2 - yeni alt tipler için tipik yetişkin yükseklik
    # (botanik hassasiyet iddiası taşımaz, görsel makuliyet içindir).
    TreeSpecies.PALM: 10.0,
    TreeSpecies.CYPRESS: 14.0,
    TreeSpecies.OLIVE: 5.0,
    TreeSpecies.PINE: 15.0,
    TreeSpecies.OAK: 13.0,
    TreeSpecies.PLANE: 18.0,
}

#: OSM `leaf_type` tag'i (broadleaved/needleleaved/mixed) -> `TreeSpecies`.
#: OSM Wiki'nin belgelediği (`natural=tree`/`leaf_type`) standart
#: değerlerdir. `leaf_type` yoksa `_classify_species` GENERIC'e düşer.
_LEAF_TYPE_TO_SPECIES: dict[str, TreeSpecies] = {
    "needleleaved": TreeSpecies.CONIFER,
    "broadleaved": TreeSpecies.DECIDUOUS,
    "mixed": TreeSpecies.GENERIC,
}

#: Roadmap V8 Faz 5.2 - OSM `genus`/`species`/`type`/`taxon` tag
#: değerlerinden (Türkçe/Latince/İngilizce karışık girilebilir) 6 yeni
#: alt türe eşleme. En spesifik/dar eşleşmeler (ör. palm/palmiye) önce
#: kontrol edilir ki genel "conifer" ipucuyla çakışmasın (sırayla
#: `_SPECIES_HINTS` listesinde kontrol edilir - ilk eşleşen kazanır).
_SPECIES_HINTS: list[tuple[TreeSpecies, tuple[str, ...]]] = [
    (TreeSpecies.PALM, ("palm", "palmiye", "phoenix", "washingtonia", "hurma")),
    (TreeSpecies.CYPRESS, ("cypress", "servi", "cupressus")),
    (TreeSpecies.OLIVE, ("olive", "zeytin", "olea")),
    (TreeSpecies.PLANE, ("plane", "çınar", "cinar", "platanus", "sycamore")),
    (TreeSpecies.OAK, ("oak", "meşe", "mese", "quercus")),
    (TreeSpecies.PINE, ("pine", "çam", "cam", "pinus")),
    (TreeSpecies.CONIFER, ("spruce", "fir", "ladin", "koknar", "picea", "abies", "conifer")),
]


def classify_species(tags: dict) -> TreeSpecies:
    """OSM tag'lerinden kaba bir `TreeSpecies` sınıflandırması (B4,
    Roadmap V8 Faz 5.2 ile genişletildi).

    Öncelik sırası: `genus`/`species`/`type`/`taxon` içindeki spesifik
    tür ipuçları (palmiye/servi/zeytin/çınar/meşe/çam gibi, en dar
    eşleşmeden en genişe) -> `leaf_type` (standart OSM tag'i, genel
    iğne/geniş yapraklı ayrımı) -> varsayılan GENERIC. Bilinçli olarak
    basit tutuldu: tam bir botanik tür kütüphanesi roadmap kapsamı
    dışı (B1'in kendi ifadesiyle "gerçek botanik tür katalogu değil,
    görsel çeşitlilik için kaba bir sınıflandırma")."""
    hint_fields = " ".join(
        str(tags.get(k, "")) for k in ("genus", "species", "type", "taxon")
    ).lower()
    for species, hints in _SPECIES_HINTS:
        if any(hint in hint_fields for hint in hints):
            return species

    leaf_type = tags.get("leaf_type")
    if leaf_type in _LEAF_TYPE_TO_SPECIES:
        return _LEAF_TYPE_TO_SPECIES[leaf_type]

    if hint_fields.strip():
        return TreeSpecies.DECIDUOUS
    return TreeSpecies.GENERIC


def estimate_height_m(tags: dict, species: TreeSpecies) -> float:
    """B4 — `height`/`est_height` tag'i varsa (OSM'de metre cinsinden,
    örn. `"height": "12"` ya da `"12 m"`) onu kullanır; yoksa tür bazlı
    ortalama tabloya düşer."""
    for key in ("height", "est_height"):
        raw = tags.get(key)
        if raw is None:
            continue
        try:
            value = float(str(raw).strip().rstrip("m").strip())
            if value > 0:
                return value
        except ValueError:
            continue
    return DEFAULT_SPECIES_HEIGHT_M[species]


def tree_instance_from_point(feature: GeoFeature, seed: int = 0) -> VegetationInstance:
    """Tekil bir OSM `natural=tree` (`__category__ == "trees"`, `Point`)
    feature'ını `VegetationInstance`'a çevirir (B2 "nokta feature"
    stratejisi + B4 yükseklik tahmini). `feature.coordinates` metre
    cinsinden yerel projeksiyonda olmalı (bkz. modül docstring'i)."""
    if feature.geometry_type != "Point":
        raise ValueError(
            f"tree_instance_from_point yalnızca Point geometrisi kabul eder, "
            f"gelen: {feature.geometry_type!r}"
        )
    x, y = feature.coordinates
    tags = feature.properties
    species = classify_species(tags)
    height = estimate_height_m(tags, species)
    # Taç yarıçapı için kaba, yüksekliğe orantılı bir varsayım (gerçek tag
    # nadiren bulunur) — `TreeGenerator`'ın varsayılan oranlarıyla tutarlı.
    canopy_radius = max(0.6, height * 0.28)
    osm_id = tags.get("osm_id", 0)
    inst_seed = seed * 1_000_003 + int(osm_id) if isinstance(osm_id, int) else seed
    return VegetationInstance(
        species=species,
        x=x,
        y=y,
        z=0.0,
        height=height,
        canopy_radius=canopy_radius,
        rotation_deg=(inst_seed % 360),
        seed=inst_seed,
    )


def mesh_for_tree_instance(instance: VegetationInstance, name: str = "osm_tree"):
    """`VegetationInstance` -> `Mesh3D` (yerel köke göre, konumlandırma
    `RenderScene`'e bırakılır - `TreeGenerator` zaten orijin-merkezli
    mesh üretir, roadmap'in mevcut `VegetationInstance` "mesh saklamadan
    yeniden üretilebilir" tasarım ilkesiyle tutarlı)."""
    return TreeGenerator.generate(
        species=instance.species,
        height=instance.height,
        canopy_radius=instance.canopy_radius,
        seed=instance.seed,
        name=name,
    )


@dataclass(slots=True)
class ForestScatterResult:
    """`scatter_forest_polygon`'un dönüş değeri - hangi OSM feature'dan
    (osm_id) kaç ağaç üretildiği izlenebilir kalsın diye (denetim/QA)."""

    osm_id: object
    instances: list[VegetationInstance]


def scatter_forest_polygon(
    feature: GeoFeature,
    density_per_100m2: float = 1.2,
    min_distance_m: float = 3.0,
    seed: int = 0,
) -> ForestScatterResult:
    """B2 "yoğunluk bazlı dağılım" — bir orman/koru poligonu
    (`__category__ in {"forest", "wood"}`) içine Poisson-disc örneklemeli
    ağaç dağılımı üretir.

    `density_per_100m2`: 100 metrekarede hedeflenen yaklaşık ağaç sayısı
    (varsayılan 1.2 — orta yoğunluklu şehir içi koru için makul bir
    başlangıç değeri; gerçek yoğunluk zaten `min_distance_m` tarafından
    da sınırlanır, bu yalnızca *hedef* sayıyı belirler, `max_points` üst
    sınırı olarak kullanılır)."""
    if feature.geometry_type != "Polygon":
        raise ValueError(
            f"scatter_forest_polygon yalnızca Polygon geometrisi kabul eder, "
            f"gelen: {feature.geometry_type!r}"
        )
    ring = feature.coordinates[0]
    polygon = Polygon([Point2D(x, y) for x, y in ring])
    area_m2 = polygon.unsigned_area()
    target_count = max(1, int(area_m2 / 100.0 * density_per_100m2))

    species = classify_species(feature.properties)
    instances = VegetationScatterer.scatter_polygon_poisson_disc(
        polygon,
        min_distance_m=min_distance_m,
        seed=seed,
        species=species,
        max_points=target_count,
    )
    return ForestScatterResult(osm_id=feature.properties.get("osm_id"), instances=instances)


def generate_vegetation_for_collection(
    collection: GeoFeatureCollection,
    seed: int = 0,
    density_per_100m2: float = 1.2,
    min_distance_m: float = 3.0,
) -> list[VegetationInstance]:
    """Faz C3 uçtan uca giriş noktası: `fetch_category_features` +
    `project_to_local_meters` çıktısındaki (yerel metre projeksiyonlu)
    bir `GeoFeatureCollection`'daki tüm `trees`/`forest`/`wood`
    feature'larını tek bir `VegetationInstance` listesine indirger.
    Diğer kategoriler (roads, water_area, waterway) bu fonksiyon
    tarafından yok sayılır (onların üretim yolu ayrı - yol/su mesh'i,
    bkz. Faz C3'ün geri kalanı)."""
    instances: list[VegetationInstance] = []
    for idx, feature in enumerate(collection.features):
        category = feature.properties.get("__category__")
        if category == "trees" and feature.geometry_type == "Point":
            instances.append(tree_instance_from_point(feature, seed=seed + idx))
        elif category in ("forest", "wood") and feature.geometry_type == "Polygon":
            result = scatter_forest_polygon(
                feature,
                density_per_100m2=density_per_100m2,
                min_distance_m=min_distance_m,
                seed=seed + idx,
            )
            instances.extend(result.instances)
    return instances


__all__ = [
    "DEFAULT_SPECIES_HEIGHT_M",
    "classify_species",
    "estimate_height_m",
    "tree_instance_from_point",
    "mesh_for_tree_instance",
    "ForestScatterResult",
    "scatter_forest_polygon",
    "generate_vegetation_for_collection",
]
