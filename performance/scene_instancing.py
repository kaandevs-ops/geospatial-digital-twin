"""
performance.scene_instancing - Faz C4 (LOD/instancing/performans), 1. dilim:
OSM nokta-prop köprülerini instancing motoruna bağlama
================================================================================

ROADMAP_V7.md Bölüm C, Faz C4: "1000+ karışık feature (bina+yol+ağaç+POI)
içeren bir sahne kabul edilebilir FPS'te render edilmeli" (B3 kabul kriteri).

Bu modül **yeni bir instancing motoru yazmaz**. Roadmap V5 M1.2'de zaten tam
teşekküllü olarak yazılmış olan `mesh_engine.batching.InstanceMeshBaker` /
`InstanceTransform` / `DrawCallEstimator` altyapısı hazır bekliyordu — Faz
C2/C3'ün altı OSM köprüsü (`vegetation`, `street_furniture`,
`religious_structures`, `sport_recreation`, `commerce_props`,
`power_infrastructure`) kendi docstring'lerinde bunu açıkça işaretlemişti:
"Faz C4 LOD/instancing için erken mesh üretimi dayatılmaz" / "bkz.
`performance.InstancingBatch`". Bu modül tam olarak o işaretlenen boşluğu
kapatan "son kilometre" köprüsüdür.

Tasarım kararı — **origin-merkezli şablon + world-space transform**:
Mevcut generator'ların hepsi (`StreetFurnitureGenerator.generate`,
`ReligiousStructureGenerator.generate`, `SportRecreationGenerator.playground`,
`CommercePropsGenerator.outdoor_seating_set`,
`PowerInfrastructureGenerator.communication_tower`, `mesh_for_tree_instance`)
konumu doğrudan mesh'in içine gömer (`center_x=position.x` gibi saf öteleme
çağrıları — rotasyon/ölçek uygulamazlar). Bu nedenle:

1. Her benzersiz "şablon anahtarı" (örn. `furniture:bench`, `tree:conifer:8`)
   için generator **bir kez**, konum `(0, 0)` / `ground_z=0.0` ile çağrılır ->
   origin-merkezli bir taban mesh (`base_mesh`) elde edilir. Bu, generator'ların
   saf öteleme davranışı nedeniyle matematiksel olarak `generator(gerçek_konum)`
   ile **birebir aynı geometriyi**, farklı bir yoldan üretir (bkz. bu modülün
   testlerindeki eşdeğerlik kontrolleri).
2. Her instance için `InstanceTransform(translation=gerçek_konum, ...)` üretilir.
3. `InstanceMeshBaker.bake_merged`/`bake_instances` (roadmap V5 M1.2,
   değiştirilmedi) bu şablon + transform listesini gerçek geometriye çevirir.

Mevcut generator/bridge fonksiyonlarının hiçbiri değiştirilmedi — bu modül
yalnızca onların çıktısını (Item listeleri) tüketir (roadmap'in "mevcut
mimari korunacak" ilkesi).

Bitki örtüsü (`VegetationInstance`) özel durum: yükseklik/gölgelik yarıçapı
sürekli bir dağılımdan gelir (her ağaç biraz farklı), bu yüzden "bir tür ==
bir şablon" saf instancing'i görsel çeşitliliği yok eder. Bunun yerine
**yükseklik kovalama (bucketing)** uygulanır (`height_bucket_m`, varsayılan
2m): aynı tür + aynı kovadaki ağaçlar aynı şablonu paylaşır, ama her
instance'ın `InstanceTransform.scale`'i kendi gerçek yüksekliğine göre
düzeltilir (tek-tip olmayan ama ucuz bir görsel çeşitlilik yaklaşımı - gerçek
oyun motorlarında da kullanılan "impostor bucketing" tekniği; B4'ün "mimari
kesinlik iddiası taşımaz" ilkesiyle tutarlı).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..commerce_props import CommercePropsGenerator, OutdoorSeatingItem
from ..core_engine.geometry_engine import Point2D
from ..mesh_engine import Mesh3D
from ..mesh_engine.batching import DrawCallEstimator, InstanceMeshBaker, InstanceTransform
from ..power_infrastructure import CommunicationTowerItem, PowerInfrastructureGenerator
from ..religious_structures import ReligiousStructureGenerator, ReligiousStructureItem
from ..sport_recreation import PlaygroundItem, SportRecreationGenerator
from ..street_furniture import StreetFurnitureGenerator, StreetFurnitureItem
from ..vegetation.osm_bridge import mesh_for_tree_instance
from ..vegetation.types import VegetationInstance

_ORIGIN = Point2D(0.0, 0.0)

#: Ağaç yükseklik kovası genişliği (metre) - bkz. modül dosyasının başındaki
#: "bitki örtüsü özel durumu" notu.
DEFAULT_TREE_HEIGHT_BUCKET_M = 2.0


@dataclass(slots=True)
class InstanceGroup:
    """Tek bir şablon anahtarı için: bir kez üretilmiş origin-merkezli taban
    mesh + bu mesh'i her instance için dünya-uzayına taşıyan transform
    listesi. `mesh_engine.batching.InstanceMeshBaker` (değiştirilmedi) ile
    tüketilir."""

    template_key: str
    base_mesh: Mesh3D
    transforms: list[InstanceTransform] = field(default_factory=list)

    def instance_count(self) -> int:
        return len(self.transforms)

    def base_triangle_count(self) -> int:
        return len(self.base_mesh.triangles)

    def naive_triangle_count(self) -> int:
        """Instancing UYGULANMAMIŞ hal: her instance kendi tam kopyasını
        taşısaydı toplam üçgen sayısı ne olurdu (karşılaştırma temeli,
        `mesh_engine.batching.DrawCallEstimator` ile aynı felsefe)."""
        return self.base_triangle_count() * self.instance_count()

    def bake_merged(self) -> Mesh3D:
        """Tüm instance'ları TEK mesh'e gömer (az sayıda tekrar için,
        bkz. `InstanceMeshBaker.bake_merged` docstring'i)."""
        return InstanceMeshBaker.bake_merged(
            self.base_mesh, self.transforms, name=f"{self.template_key}_merged"
        )

    def bake_instances(self) -> list[Mesh3D]:
        """Her instance için ayrı transformlanmış mesh (gerçek GPU instancing
        API'sine devredilecek backend'ler için, bkz. `InstanceMeshBaker.
        bake_instances` docstring'i)."""
        return InstanceMeshBaker.bake_instances(self.base_mesh, self.transforms)


@dataclass(slots=True)
class SceneInstancingResult:
    """Bir sahnedeki tüm nokta-prop kategorilerinin (ağaç, sokak mobilyası,
    dini yapı eki, oyun alanı, dış mekan oturma, iletişim kulesi)
    instancing gruplarının toplu özeti - B3 kabul kriterinin ("1000+
    feature") ölçülebilir kanıtı için."""

    groups: dict[str, InstanceGroup] = field(default_factory=dict)

    def total_instance_count(self) -> int:
        return sum(g.instance_count() for g in self.groups.values())

    def total_template_count(self) -> int:
        return len(self.groups)

    def total_base_triangle_count(self) -> int:
        """Sahnedeki BENZERSİZ (bir kez GPU'ya yüklenecek) taban mesh
        üçgenlerinin toplamı - instancing sonrası bellek/yükleme maliyeti."""
        return sum(g.base_triangle_count() for g in self.groups.values())

    def total_naive_triangle_count(self) -> int:
        """Instancing UYGULANMAMIŞ halde toplam üçgen sayısı (her instance
        kendi tam kopyasını taşısaydı)."""
        return sum(g.naive_triangle_count() for g in self.groups.values())

    def triangle_reduction_ratio(self) -> float:
        """Bellek/yükleme tarafında üçgen sayısı azalma oranı (0..1).
        Not: bu, `DrawCallEstimator`'ın ölçtüğü draw-call azalmasından
        FARKLI bir metriktir - `bake_merged` kullanılırsa draw call zaten
        1'e iner ama vertex/üçgen sayısı azalmaz; gerçek GPU instancing
        (`bake_instances` + backend instancing API'si) kullanılırsa HEM
        draw call HEM CPU->GPU'ya yüklenen benzersiz geometri azalır. Bu
        metrik ikinci senaryoyu (asıl hedeflenen) ölçer."""
        naive = self.total_naive_triangle_count()
        if naive == 0:
            return 0.0
        return 1.0 - (self.total_base_triangle_count() / naive)

    def draw_call_pairs(self) -> list[tuple[str, int]]:
        """`DrawCallEstimator.estimate*` ile uyumlu `(materyal_anahtarı,
        instance_sayısı)` listesi - materyal anahtarı burada şablon
        anahtarıyla eşleştirilir (her şablon = bir materyal grubu kabul
        edilir, `mesh_engine.batching` ile aynı 1-materyal-1-draw-call
        varsayımı)."""
        return [(key, g.instance_count()) for key, g in self.groups.items()]

    def estimated_draw_call_reduction_percent(self) -> float:
        return DrawCallEstimator.reduction_percent(self.draw_call_pairs())


def _group_by_key(
    items,
    key_fn,
    template_fn,
    transform_fn,
) -> dict[str, InstanceGroup]:
    groups: dict[str, InstanceGroup] = {}
    for item in items:
        key = key_fn(item)
        group = groups.get(key)
        if group is None:
            group = InstanceGroup(template_key=key, base_mesh=template_fn(item))
            groups[key] = group
        group.transforms.append(transform_fn(item))
    return groups


# ============================================================================ #
# Sokak mobilyası (street_furniture) - C3/3. dilim çıktısı
# ============================================================================ #

def instancing_groups_for_street_furniture(
    items: list[StreetFurnitureItem],
) -> dict[str, InstanceGroup]:
    """`StreetFurnitureItem` listesini `furniture_type`'a göre grupla - her
    tip için deterministik, konumdan bağımsız tek bir şablon mesh vardır
    (`StreetFurnitureGenerator.generate` saf öteleme kullanır)."""

    def key_fn(it: StreetFurnitureItem) -> str:
        return f"furniture:{it.furniture_type.value}"

    def template_fn(it: StreetFurnitureItem) -> Mesh3D:
        origin_item = StreetFurnitureItem(
            furniture_type=it.furniture_type, position=_ORIGIN, rotation_deg=0.0, ground_z=0.0
        )
        return StreetFurnitureGenerator.generate(origin_item)

    def transform_fn(it: StreetFurnitureItem) -> InstanceTransform:
        return InstanceTransform(
            translation=(it.position.x, it.position.y, it.ground_z),
            rotation_deg_z=it.rotation_deg,
        )

    return _group_by_key(items, key_fn, template_fn, transform_fn)


# ============================================================================ #
# Dini yapı ekleri (religious_structures) - C3/4. dilim çıktısı
# ============================================================================ #

def instancing_groups_for_religious_structures(
    items: list[ReligiousStructureItem],
) -> dict[str, InstanceGroup]:
    """`ReligiousStructureItem` listesini `(religion, base_height_m)`'e göre
    grupla - şablon yalnızca bu iki alana bağlı (`ReligiousStructureGenerator.
    generate` saf öteleme kullanır; `base_height_m` OSM `height` tag'inden
    geldiği ve genelde bina bazında değiştiği için tür kadar kaba
    olmayan ama yine de gerçekçi bir gruplama anahtarıdır)."""

    def key_fn(it: ReligiousStructureItem) -> str:
        return f"worship:{it.religion.value}:{round(it.base_height_m, 1)}"

    def template_fn(it: ReligiousStructureItem) -> Mesh3D:
        origin_item = ReligiousStructureItem(
            religion=it.religion, position=_ORIGIN, ground_z=0.0, base_height_m=it.base_height_m
        )
        return ReligiousStructureGenerator.generate(origin_item)

    def transform_fn(it: ReligiousStructureItem) -> InstanceTransform:
        return InstanceTransform(translation=(it.position.x, it.position.y, it.ground_z))

    return _group_by_key(items, key_fn, template_fn, transform_fn)


# ============================================================================ #
# Oyun alanı (sport_recreation.PlaygroundItem) - C3/6. dilim çıktısı
# ============================================================================ #

def instancing_groups_for_playgrounds(
    items: list[PlaygroundItem],
) -> dict[str, InstanceGroup]:
    """Tüm oyun alanı setleri tek tip (kaydırak+salıncak) olduğu için tek
    şablon anahtarı yeterli - `SportRecreationGenerator.playground` saf
    öteleme kullanır."""

    def key_fn(_it: PlaygroundItem) -> str:
        return "playground:default"

    def template_fn(_it: PlaygroundItem) -> Mesh3D:
        return SportRecreationGenerator.playground(_ORIGIN, ground_z=0.0)

    def transform_fn(it: PlaygroundItem) -> InstanceTransform:
        return InstanceTransform(
            translation=(it.position.x, it.position.y, it.ground_z),
            rotation_deg_z=it.rotation_deg,
        )

    return _group_by_key(items, key_fn, template_fn, transform_fn)


# ============================================================================ #
# Dış mekan oturma (commerce_props.OutdoorSeatingItem) - C3/5. dilim çıktısı
# ============================================================================ #

def instancing_groups_for_outdoor_seating(
    items: list[OutdoorSeatingItem],
) -> dict[str, InstanceGroup]:
    """`table_count`'a göre grupla - masa sayısı farklı setler farklı
    geometriye sahiptir (`CommercePropsGenerator.outdoor_seating_set` saf
    öteleme kullanır, `table_count` şablonun şeklini belirler)."""

    def key_fn(it: OutdoorSeatingItem) -> str:
        return f"seating:{max(1, it.table_count)}"

    def template_fn(it: OutdoorSeatingItem) -> Mesh3D:
        origin_item = OutdoorSeatingItem(
            position=_ORIGIN, table_count=it.table_count, rotation_deg=0.0, ground_z=0.0
        )
        return CommercePropsGenerator.outdoor_seating_set(origin_item)

    def transform_fn(it: OutdoorSeatingItem) -> InstanceTransform:
        return InstanceTransform(
            translation=(it.position.x, it.position.y, it.ground_z),
            rotation_deg_z=it.rotation_deg,
        )

    return _group_by_key(items, key_fn, template_fn, transform_fn)


# ============================================================================ #
# İletişim kulesi (power_infrastructure.CommunicationTowerItem) - C3/7. dilim
# ============================================================================ #

def instancing_groups_for_communication_towers(
    items: list[CommunicationTowerItem],
) -> dict[str, InstanceGroup]:
    """`height_m`'e göre kovalanmış gruplama - `PowerInfrastructureGenerator.
    communication_tower` saf öteleme kullanır, yükseklik OSM `height` tag'i
    varsa taşınır (B4 "gerçek veri önceliği"), bu yüzden tam eşitlik yerine
    1 metrelik kovaya yuvarlanır (aksi halde her farklı yükseklik ayrı
    şablon olur, instancing kazancı azalır)."""

    def _bucket(it: CommunicationTowerItem) -> float:
        return round(it.height_m)

    def key_fn(it: CommunicationTowerItem) -> str:
        return f"tower:{_bucket(it)}"

    def template_fn(it: CommunicationTowerItem) -> Mesh3D:
        origin_item = CommunicationTowerItem(position=_ORIGIN, height_m=_bucket(it), ground_z=0.0)
        return PowerInfrastructureGenerator.communication_tower(origin_item)

    def transform_fn(it: CommunicationTowerItem) -> InstanceTransform:
        return InstanceTransform(translation=(it.position.x, it.position.y, it.ground_z))

    return _group_by_key(items, key_fn, template_fn, transform_fn)


# ============================================================================ #
# Ağaç (vegetation.VegetationInstance) - C3/1. dilim çıktısı
# ============================================================================ #

def instancing_groups_for_vegetation(
    instances: list[VegetationInstance],
    height_bucket_m: float = DEFAULT_TREE_HEIGHT_BUCKET_M,
) -> dict[str, InstanceGroup]:
    """`(species, yükseklik_kovası)`'na göre grupla + her instance'ın gerçek
    yüksekliğine göre `InstanceTransform.scale` düzeltmesi - bkz. modül
    dosyasının başındaki "bitki örtüsü özel durumu" notu."""

    def _bucket_height(it: VegetationInstance) -> float:
        if height_bucket_m <= 0:
            return it.height
        bucket_index = max(1, round(it.height / height_bucket_m))
        return bucket_index * height_bucket_m

    def key_fn(it: VegetationInstance) -> str:
        return f"tree:{it.species.value}:{round(_bucket_height(it), 2)}"

    def template_fn(it: VegetationInstance) -> Mesh3D:
        template_height = _bucket_height(it)
        canopy_ratio = (it.canopy_radius / it.height) if it.height else 1.0
        template = VegetationInstance(
            species=it.species,
            x=0.0,
            y=0.0,
            z=0.0,
            height=template_height,
            canopy_radius=template_height * canopy_ratio,
            rotation_deg=0.0,
            seed=0,
        )
        return mesh_for_tree_instance(template, name=f"tree_template_{key_fn(it)}")

    def transform_fn(it: VegetationInstance) -> InstanceTransform:
        template_height = _bucket_height(it)
        scale = (it.height / template_height) if template_height else 1.0
        return InstanceTransform(
            translation=(it.x, it.y, it.z),
            rotation_deg_z=it.rotation_deg,
            scale=scale,
        )

    return _group_by_key(instances, key_fn, template_fn, transform_fn)


# ============================================================================ #
# Sahne toplayıcı - tüm kategorileri tek `SceneInstancingResult`'a indirger
# ============================================================================ #

def build_scene_instancing_result(
    *,
    vegetation: list[VegetationInstance] | None = None,
    street_furniture: list[StreetFurnitureItem] | None = None,
    religious_structures: list[ReligiousStructureItem] | None = None,
    playgrounds: list[PlaygroundItem] | None = None,
    outdoor_seating: list[OutdoorSeatingItem] | None = None,
    communication_towers: list[CommunicationTowerItem] | None = None,
    tree_height_bucket_m: float = DEFAULT_TREE_HEIGHT_BUCKET_M,
) -> SceneInstancingResult:
    """C2/C3'ün altı nokta-prop köprüsünün çıktısını (hepsi opsiyonel - bir
    sahnede hepsi bulunmayabilir) tek bir `SceneInstancingResult`'a indirger.
    B3 kabul kriteri ("1000+ karışık feature") için ölçüm arayüzü budur."""
    groups: dict[str, InstanceGroup] = {}
    if vegetation:
        groups.update(instancing_groups_for_vegetation(vegetation, tree_height_bucket_m))
    if street_furniture:
        groups.update(instancing_groups_for_street_furniture(street_furniture))
    if religious_structures:
        groups.update(instancing_groups_for_religious_structures(religious_structures))
    if playgrounds:
        groups.update(instancing_groups_for_playgrounds(playgrounds))
    if outdoor_seating:
        groups.update(instancing_groups_for_outdoor_seating(outdoor_seating))
    if communication_towers:
        groups.update(instancing_groups_for_communication_towers(communication_towers))
    return SceneInstancingResult(groups=groups)


__all__ = [
    "DEFAULT_TREE_HEIGHT_BUCKET_M",
    "InstanceGroup",
    "SceneInstancingResult",
    "instancing_groups_for_street_furniture",
    "instancing_groups_for_religious_structures",
    "instancing_groups_for_playgrounds",
    "instancing_groups_for_outdoor_seating",
    "instancing_groups_for_communication_towers",
    "instancing_groups_for_vegetation",
    "build_scene_instancing_result",
]
