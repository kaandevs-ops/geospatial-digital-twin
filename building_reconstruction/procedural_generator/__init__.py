"""
Procedural Building Generator
==============================

Roadmap Phase 3 - "Procedural Building Generator".

Bina tipine göre kat şablonu seçimi (Apartman/Villa/Ofis/Fabrika/AVM/Depo/
Hastane/Okul/Sanayi Tesisi/Terminal/Hangar/Stadyum) - her tip için
parametrik kural seti (`BuildingTypeRules` registry).

Bu modül, Phase 3'ün tüm alt sistemlerini (footprint_parser, roof_generator,
facade_generator, room_generator, building_elements) tek bir `Building`
nesnesinde birleştiren üst seviye orkestratördür.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ...core_engine.geometry_engine import Point2D, Polygon
from ...mesh_engine import FloorPlateBuilder, Mesh3D, MeshBuilder, MeshMerger
from ...mesh_engine.uv_atlas import MeshUVAtlasBaker, WorldScaleUVMapper
from ...lighting import AmbientOcclusionBaker, SceneAOBaker

from ..footprint_parser import Footprint, FootprintParser
from ..roof_generator import RoofGenerator, RoofType
from ..facade_generator import Facade, FacadeGenerator, FacadeMaterial
from ..room_generator import Room, RoomGenerator, RoomType
from ..building_elements import (
    Balcony, BalconyGenerator, CorridorGenerator, Door, DoorGenerator,
    DoubleSkinFacade, DoubleSkinFacadeGenerator, ElevatorCore, ElevatorCoreGenerator,
    SetbackFloorGenerator, Stair, StairGenerator, WindowGenerator, WindowPlacement,
)
from ..interior_generator import (
    ElevatorShaftMeshBuilder, FurnitureGenerator, FurnitureItem,
    InteriorWallBuilder, StairMeshBuilder,
)
from ..terrain_integration import TerrainFoundationGenerator, RetainingWallGenerator
from ..curved_facade import VerticalProfileGenerator
from ...terrain_engine import HeightmapGrid


class BuildingType(str, Enum):
    APARTMAN = "apartman"
    VILLA = "villa"
    OFIS = "ofis"
    FABRIKA = "fabrika"
    AVM = "avm"
    DEPO = "depo"
    HASTANE = "hastane"
    OKUL = "okul"
    SANAYI_TESISI = "sanayi_tesisi"
    TERMINAL = "terminal"
    HANGAR = "hangar"
    STADYUM = "stadyum"


@dataclass(slots=True)
class BuildingTypeRule:
    """Bina tipine özgü parametrik üretim kuralları."""

    default_floor_height: float
    default_floor_count: int
    default_roof_type: RoofType
    roof_pitch_deg: float
    has_elevator: bool
    has_balconies: bool
    window_spacing: float
    facade_material: FacadeMaterial
    room_generation_min_size: float


class BuildingTypeRules:
    """Roadmap: 'BuildingTypeRules registry'."""

    _RULES: dict[BuildingType, BuildingTypeRule] = {
        BuildingType.APARTMAN: BuildingTypeRule(3.0, 5, RoofType.FLAT, 15, True, True, 2.4, FacadeMaterial.BETON, 3.0),
        BuildingType.VILLA: BuildingTypeRule(3.2, 2, RoofType.HIP, 30, False, True, 2.0, FacadeMaterial.TAS, 3.5),
        BuildingType.OFIS: BuildingTypeRule(3.5, 8, RoofType.FLAT, 5, True, False, 1.8, FacadeMaterial.CAM, 4.0),
        BuildingType.FABRIKA: BuildingTypeRule(6.0, 1, RoofType.INDUSTRIAL, 8, False, False, 4.0, FacadeMaterial.ENDUSTRIYEL, 6.0),
        BuildingType.AVM: BuildingTypeRule(5.0, 2, RoofType.FLAT, 5, True, False, 3.5, FacadeMaterial.CAM, 8.0),
        BuildingType.DEPO: BuildingTypeRule(7.0, 1, RoofType.FLAT, 3, False, False, 5.0, FacadeMaterial.METAL, 10.0),
        BuildingType.HASTANE: BuildingTypeRule(3.6, 6, RoofType.FLAT, 5, True, False, 2.2, FacadeMaterial.KOMPOZIT, 3.5),
        BuildingType.OKUL: BuildingTypeRule(3.4, 3, RoofType.HIP, 20, False, False, 2.5, FacadeMaterial.TUGLA, 4.0),
        BuildingType.SANAYI_TESISI: BuildingTypeRule(8.0, 1, RoofType.SAWTOOTH, 35, False, False, 4.5, FacadeMaterial.ENDUSTRIYEL, 6.0),
        BuildingType.TERMINAL: BuildingTypeRule(9.0, 1, RoofType.MODERN, 8, True, False, 3.0, FacadeMaterial.CAM, 10.0),
        BuildingType.HANGAR: BuildingTypeRule(12.0, 1, RoofType.INDUSTRIAL, 10, False, False, 6.0, FacadeMaterial.METAL, 15.0),
        BuildingType.STADYUM: BuildingTypeRule(6.0, 1, RoofType.MODERN, 12, False, False, 4.0, FacadeMaterial.KOMPOZIT, 8.0),
    }

    @classmethod
    def get(cls, building_type: BuildingType | str) -> BuildingTypeRule:
        if isinstance(building_type, str):
            try:
                building_type = BuildingType(building_type.lower())
            except ValueError:
                building_type = BuildingType.APARTMAN
        return cls._RULES[building_type]

    @classmethod
    def register(cls, building_type: BuildingType, rule: BuildingTypeRule) -> None:
        """Genişletilebilirlik: dışarıdan yeni/özelleştirilmiş kural eklenebilir."""
        cls._RULES[building_type] = rule


@dataclass(slots=True)
class Floor:
    """Roadmap Phase 3 veri modeli: `Floor`.

    Faz 2: `stairs`/`elevators`/`doors` eskiden yalnızca konum (`Point2D`)
    tutuyordu; artık gerçek veri nesneleri (`Stair`/`ElevatorCore`/`Door`)
    + bunlardan üretilmiş gerçek `interior_mesh` (bölme duvarları + merdiven
    basamakları + asansör kuyusu + sabit donatı) de saklanıyor. Eski
    `Point2D` listeleri (`stair_positions` vb.) geriye dönük uyumluluk için
    ayrıca tutulur.
    """

    level: int
    height_m: float
    rooms: list[Room] = field(default_factory=list)
    corridors: list[Polygon] = field(default_factory=list)
    stairs: list[Point2D] = field(default_factory=list)
    elevators: list[Point2D] = field(default_factory=list)
    doors: list[Point2D] = field(default_factory=list)
    windows: list[Point2D] = field(default_factory=list)
    # Faz 2 - gerçek veri nesneleri
    stair_objects: list[Stair] = field(default_factory=list)
    elevator_objects: list[ElevatorCore] = field(default_factory=list)
    interior_doors: list[Door] = field(default_factory=list)
    furniture: list[FurnitureItem] = field(default_factory=list)
    interior_mesh: Mesh3D | None = None
    # Faz 1.2 — bodrum/otopark katı desteği: yer altı kat olup olmadığı.
    # `True` iken pencere üretimi bilinçli olarak boş bırakılır ("ışıksız")
    # ve oda tipleri GARAJ/DEPO'ya sabitlenir (farklı cephe kuralı).
    is_below_grade: bool = False


@dataclass(slots=True)
class Building:
    """Roadmap Phase 3 veri modeli: `Building`."""

    footprint: Footprint
    floors: list[Floor] = field(default_factory=list)
    roof: Mesh3D | None = None
    facade: Facade | None = None
    building_type: BuildingType = BuildingType.APARTMAN
    # Faz 1.2 — bodrum katlarının dış zarfı (facade'dan bağımsız, çünkü
    # `FacadeGenerator` yalnızca zemin-üstü (z>=0) cephe için tasarlanmıştır).
    basement_envelope: Mesh3D | None = None
    # ROADMAP_V5 M2.4 — gerçek DEM verisiyle (`heightmap` verildiyse)
    # üretilen kaide/istinat duvarı mesh'leri + kesişim raporu. Varsayılan
    # `None` (opt-in, `heightmap` verilmezse hiçbiri üretilmez).
    terrain_foundation: Mesh3D | None = None
    retaining_wall: Mesh3D | None = None
    terrain_intersection: object | None = None  # TerrainIntersectionReport | None
    # ROADMAP_V5 M2.2 (kalan madde) — `add_double_skin=True` verildiyse
    # üretilen ikinci cam kabuk + gölgeleme kanatları. Varsayılan `None`
    # (opt-in, mevcut çağıranlar hiçbir fark görmez).
    double_skin: "DoubleSkinFacade | None" = None

    @property
    def total_height_m(self) -> float:
        return sum(f.height_m for f in self.floors)

    def full_mesh(
        self, include_interior: bool = False,
        generate_uvs: bool = False, texture_size_m: float = 2.0,
        bake_ao: bool = False, ao_sample_count: int = 8, ao_max_distance: float = 5.0,
        pack_uv_atlas: bool = False, atlas_texture_px: int = 512,
        atlas_width_px: int = 2048, atlas_height_px: int = 2048,
    ) -> Mesh3D:
        """Facade + roof mesh'lerini (ve `include_interior=True` ise Faz 2
        iç mekan mesh'lerini: bölme duvarları, merdiven, asansör kuyusu,
        sabit donatı) tek bir `Mesh3D`'de birleştirir.

        `include_interior` varsayılan olarak `False`'dur: iç mekan
        donatıları (küçük, birbirinden kopuk mobilya/ekipman kutuları)
        mesh basitleştirme (QEM) gibi tüm-gövde geometrik kalite
        metriklerini bozar - bu yüzden yapısal zarf (envelope) ile iç mekan
        detayını ayrı tutuyoruz. Görselleştirme/export gibi tam detay
        isteyen tüketiciler `include_interior=True` ile çağırmalı.

        `generate_uvs` (ROADMAP_V7 Faz C4 devamı, varsayılan `False` -
        opt-in, geriye dönük tam uyumlu): `True` ise birleştirilmiş mesh'e
        `WorldScaleUVMapper.box_mapping_world_scale` (M1.4, mevcut -
        değiştirilmedi) uygulanır, böylece `PBRMaterial.albedo_map` dolu
        bir gerçek doku (bkz. `material_engine.texture_presets`) binanın
        boyutundan bağımsız, dünya-ölçeğinde tutarlı bir tekrar
        sıklığıyla render edilebilir. `texture_size_m`: dokunun metre
        cinsinden fiziksel tekrar boyutu (varsayılan 2m).

        `bake_ao` (ROADMAP_V8 Faz 6.4, varsayılan `False` - opt-in, geriye
        dönük tam uyumlu): `True` ise birleştirilmiş mesh'e
        `AmbientOcclusionBaker.apply_vertex_ao` (`lighting`, mevcut -
        yalnızca performans için genişletildi, bkz. o dosyadaki not)
        uygulanır ve sonuç her `Vertex3D.ao`'da saklanır - cephe köşeleri/
        girinti-çıkıntılar (balkon, çıkma, saçak) gölgeleme katkısı almış
        olur. Maliyetli olabileceğinden (vertex sayısına bağlı) varsayılan
        kapalıdır; büyük sahne/toplu üretimde açılmadan önce
        `ao_sample_count`/`ao_max_distance` ile dengelenmelidir.

        `pack_uv_atlas` (ROADMAP_V8 Faz 6.4 kalan madde, varsayılan
        `False` - opt-in, geriye dönük tam uyumlu, `bake_ao` ile aynı
        desen): `True` ise (yalnızca `generate_uvs=True` iken anlamlı)
        parçalar tek bir mesh'e birleştirilip UV'lenmek yerine,
        **birleştirmeden önce her parça ayrı ayrı** dünya-ölçekli UV
        alır, sonra `MeshUVAtlasBaker.bake()` (mevcut, `mesh_engine.
        uv_atlas` - değiştirilmedi) ile tek bir `atlas_width_px x
        atlas_height_px` doku sayfasına paketlenir ve UV'leri buna göre
        yeniden eşlenir. Sonuç: cephe/çatı/bodrum/istinat duvarı/çift-
        kabuk gibi ayrı parçalar artık **tek doku sayfası + tek
        malzeme** ile çizilebilir (M1.2'nin draw-call azaltma
        hedefiyle aynı doğrultuda, şimdi bina-üretim ardılına gerçekten
        bağlı). `atlas_texture_px`: her parçaya atlas içinde ayrılan
        kare doku boyutu (piksel); parça sayısı arttıkça
        `atlas_width_px`/`atlas_height_px` büyütülmelidir.
        """
        parts = []
        if self.facade and self.facade.mesh:
            parts.append(self.facade.mesh)
        if self.roof:
            parts.append(self.roof)
        if self.basement_envelope and self.basement_envelope.triangle_count() > 0:
            parts.append(self.basement_envelope)
        if self.terrain_foundation and self.terrain_foundation.triangle_count() > 0:
            parts.append(self.terrain_foundation)
        if self.retaining_wall and self.retaining_wall.triangle_count() > 0:
            parts.append(self.retaining_wall)
        if self.double_skin is not None:
            if self.double_skin.outer_skin_mesh and self.double_skin.outer_skin_mesh.triangle_count() > 0:
                parts.append(self.double_skin.outer_skin_mesh)
            if self.double_skin.shading_fin_mesh and self.double_skin.shading_fin_mesh.triangle_count() > 0:
                parts.append(self.double_skin.shading_fin_mesh)
        if include_interior:
            for floor in self.floors:
                if floor.interior_mesh and floor.interior_mesh.triangle_count() > 0:
                    parts.append(floor.interior_mesh)
        if not parts:
            return Mesh3D(name=f"building_{self.building_type.value}")

        if generate_uvs and pack_uv_atlas and len(parts) > 1:
            # Her parçayı önce dünya-ölçekli UV ile işaretle, sonra tek
            # doku sayfasına paketleyip birleştir (atlas UV'leri [0,1]
            # aralığında olur - WorldScaleUVMapper çıktısı taşabilir ama
            # MeshUVAtlasBaker._remap_uvs bunu `% 1.0` ile sarıyor, bu
            # yüzden `merged` çağrısına ek bir merge adımı gerekmiyor).
            atlas_parts: list[tuple[str, Mesh3D, int, int]] = []
            for idx, part in enumerate(parts):
                if not part.triangles:
                    continue
                uv_part = WorldScaleUVMapper.box_mapping_world_scale(
                    part, texture_size_m=texture_size_m,
                )
                atlas_parts.append(
                    (f"part_{idx}", uv_part, atlas_texture_px, atlas_texture_px)
                )
            if atlas_parts:
                bake_result = MeshUVAtlasBaker.bake(
                    atlas_parts, atlas_width=atlas_width_px,
                    atlas_height=atlas_height_px,
                    name=f"building_{self.building_type.value}",
                )
                merged = bake_result.merged_mesh
            else:
                merged = Mesh3D(name=f"building_{self.building_type.value}")
        else:
            merged = MeshMerger.merge(parts, name=f"building_{self.building_type.value}")
            if generate_uvs and merged.triangles:
                merged = WorldScaleUVMapper.box_mapping_world_scale(merged, texture_size_m=texture_size_m)

        if bake_ao and merged.triangles:
            merged = AmbientOcclusionBaker.apply_vertex_ao(
                merged, sample_count=ao_sample_count, max_distance=ao_max_distance,
            )
        return merged


class ProceduralBuildingGenerator:
    """Footprint -> tam `Building` (kat planları, çatı, cephe, dolaşım
    elemanları dahil) üretimini orkestre eder."""

    @staticmethod
    def generate(
        footprint: Footprint,
        building_type: BuildingType | str | None = None,
        floor_count: int | None = None,
        seed: int | None = None,
        generate_interior: bool = False,
        basement_floor_count: int = 0,
        basement_floor_height: float = 2.8,
        setback_floor_index: int | None = None,
        setback_inset_m: float = 1.0,
        heightmap: "HeightmapGrid | None" = None,
        add_retaining_wall: bool = False,
        add_double_skin: bool = False,
        double_skin_gap_m: float = 0.9,
        double_skin_gap_profile=None,
        min_room_size: float | None = None,
        window_spacing: float | None = None,
        vertical_scale_at=None,
        vertical_rotation_deg_at=None,
        vertical_offset_at=None,
    ) -> Building:
        """Footprint -> `Building`.

        `generate_interior` (Faz 2, varsayılan `False`): oda/bölme duvarı/
        merdiven/asansör kuyusu/sabit donatı üretimi ve bunların gerçek
        mesh'e dönüştürülmesi gerçek maliyetli bir iştir (ear-clipping,
        çoklu mesh merge). Şehir ölçeğinde binlerce bina üretilirken (ör.
        harita üzerinde genel görünüm / LOD0-1) bu maliyeti varsayılan
        olarak almamak gerekir - bu yüzden `RoomGenerator` her zaman
        çalışır (hafif, veri-only) ama `interior_mesh` yalnızca
        `generate_interior=True` iken kurulur. Kullanıcı bir binayı
        haritada seçip yakınlaştırdığında (LOD2/3, tek bina detayı)
        `generate_interior=True` ile tekrar üretilmelidir.

        `basement_floor_count` (Faz 1.2): 0'dan büyükse, z=0'ın altına
        `n` adet yer altı kat eklenir (`Floor.is_below_grade=True`).
        Bu katlar bilinçli olarak penceresiz üretilir ve oda tipleri
        GARAJ/DEPO'ya sabitlenir - roadmap'in "yer altı kat, ışıksız/
        farklı cephe kuralları" maddesi.

        `setback_floor_index` (ROADMAP_V5 M2.2, varsayılan `None` -
        opsiyonel/opt-in, eski çağıranlar hiçbir fark görmez): verilirse,
        bu indeksten itibaren üstteki katlar `setback_inset_m` kadar içe
        ofsetlenmiş bir footprint ile üretilir ("çekme kat"). Cephe iki
        ayrı segmentte (`FacadeGenerator.generate` iki kez, alt segment
        orijinal poligonla, üst segment içe-ofsetli poligonla) üretilip
        birleştirilir; çatı da içe-ofsetli poligon üzerine oturtulur -
        böylece M2.2 kabul kriteri (üst kat en az 1m içeride, geometrik
        doğrulanabilir) doğrudan `SetbackFloorGenerator.min_inset_m` ile
        kontrol edilebilir hale gelir.

        `heightmap` (ROADMAP_V5 M2.4, varsayılan `None` - opt-in): gerçek
        DEM verisi (`terrain_engine.HeightmapGrid`) verilirse, bina tabanı
        ile eğimli zemin arasındaki boşluğu kapatan bir kaide
        (`Building.terrain_foundation`) üretilir ve bina-arazi kesişim
        raporu (`Building.terrain_intersection`) doldurulur.
        `add_retaining_wall=True` ise ayrıca eğim belirginse
        (`Building.retaining_wall`) bir istinat duvarı da eklenir.

        `add_double_skin` (ROADMAP_V5 M2.2 kalan madde, varsayılan
        `False` - opt-in): verilirse, birincil footprint'in `double_skin_gap_m`
        kadar dışında ikinci bir cam kabuk + gölgeleme kanadı üretilir
        (`Building.double_skin`) — birincil `facade` hiç değişmez, çift
        kabuk tamamen ek/ayrı bir mesh katmanıdır.

        `double_skin_gap_profile` (RFC_FAZ6_1 "İlk aşama", varsayılan
        `None` - opt-in, `add_double_skin=True` ile birlikte anlamlı):
        sabit `double_skin_gap_m` yerine bir `Callable[[float], float]`
        (taban-göreli yükseklik -> boşluk metre) verilirse, dış kabuk
        düşey eksende daralan/genişleyen ("tapered"/"twisted") bir
        silüet alır — ana yapısal kat/oda/pencere sistemi hiç etkilenmez
        (bkz. `docs/RFC_FAZ6_1_KAVISLI_CEPHE.md`).

        `vertical_scale_at`/`vertical_rotation_deg_at`/`vertical_offset_at`
        (ROADMAP_V8 Faz 6.1 "Seçenek A - tam yapısal versiyon", varsayılan
        `None` - opt-in, geriye dönük tam uyumlu, üçü de `None` iken
        davranış birebir eskisiyle aynı): verilirse
        `VerticalProfileGenerator.floor_polygons()` ile her kat için ayrı
        bir taban çokgeni üretilir ve `FacadeGenerator.generate(
        floor_polygons=...)`'a geçirilir - `double_skin_gap_profile`'ın
        yalnızca kozmetik dış kabuğu eğriltmesinin aksine, bu parametreler
        pencere/kapı/oda/döşeme dahil **ana yapısal zarfın kendisini**
        düşey eksende daraltır/genişletir/döndürür/kaydırır (ör. "yukarı
        doğru büzülen kule" veya "eğik/twisted kule" - RFC Bölüm 3
        Seçenek A). Çatı da otomatik olarak en üst katın (gerçekte
        üretilen) çokgenine oturur.
        """
        bt = ProceduralBuildingGenerator._resolve_type(footprint, building_type)
        rule = BuildingTypeRules.get(bt)

        n_floors = floor_count or footprint.floor_count or rule.default_floor_count
        floor_height = footprint.height_m / n_floors if footprint.height_m else rule.default_floor_height

        floors = ProceduralBuildingGenerator._generate_floors(
            footprint.polygon, bt, rule, n_floors, floor_height, seed, generate_interior,
            min_room_size=min_room_size, window_spacing=window_spacing,
        )

        basement_envelope = None
        if basement_floor_count > 0:
            basement_floors, basement_envelope = ProceduralBuildingGenerator._generate_basement_floors(
                footprint.polygon, bt, rule, basement_floor_count, basement_floor_height,
                seed, generate_interior,
            )
            # Bodrum katlar negatif seviyede, yer üstü katların önüne eklenir
            # (level sırası: en alt bodrum -> zemin kat -> üst katlar).
            floors = basement_floors + floors

        roof_footprint_polygon = footprint.polygon
        base_z = n_floors * floor_height

        has_setback = (
            setback_floor_index is not None
            and 0 < setback_floor_index < n_floors
            and setback_inset_m > 0
        )
        if has_setback:
            setback_polygon = SetbackFloorGenerator.offset_footprint(footprint.polygon, setback_inset_m)
            setback_base_z = setback_floor_index * floor_height
            lower_floor_count = setback_floor_index
            upper_floor_count = n_floors - setback_floor_index

            lower_facade = FacadeGenerator.generate(
                footprint.polygon, bt.value, base_z=0.0, floor_height=floor_height,
                floor_count=lower_floor_count, seed=seed, material_override=rule.facade_material,
            )
            upper_facade = FacadeGenerator.generate(
                setback_polygon, bt.value, base_z=setback_base_z, floor_height=floor_height,
                floor_count=upper_floor_count, seed=seed, material_override=rule.facade_material,
            )
            facade_meshes = [m for m in (lower_facade.mesh, upper_facade.mesh) if m and m.triangle_count() > 0]
            facade = lower_facade
            if facade_meshes:
                facade.mesh = MeshMerger.merge(facade_meshes, name="facade_with_setback")
            roof_footprint_polygon = setback_polygon
        else:
            has_vertical_profile = (
                vertical_scale_at is not None or vertical_rotation_deg_at is not None
                or vertical_offset_at is not None
            )
            vertical_floor_polygons = None
            if has_vertical_profile:
                vertical_floor_polygons = VerticalProfileGenerator.floor_polygons(
                    footprint.polygon, n_floors,
                    scale_at=vertical_scale_at, rotation_deg_at=vertical_rotation_deg_at,
                    offset_at=vertical_offset_at,
                )
                # Çatı, en üst katın (gerçekte üretilen) gerçek çokgenine
                # otursun (`setback` dalıyla tutarlı desen).
                roof_footprint_polygon = vertical_floor_polygons[-1]
            facade = FacadeGenerator.generate(
                footprint.polygon, bt.value, base_z=0.0, floor_height=floor_height,
                floor_count=n_floors, seed=seed, material_override=rule.facade_material,
                floor_polygons=vertical_floor_polygons,
            )

        roof_mesh = RoofGenerator.generate(
            roof_footprint_polygon, base_z=base_z,
            roof_type=rule.default_roof_type, pitch_deg=rule.roof_pitch_deg,
        )

        terrain_foundation = None
        retaining_wall = None
        terrain_intersection = None
        if heightmap is not None:
            terrain_intersection = TerrainFoundationGenerator.analyze_intersection(
                footprint.polygon, building_base_z=0.0, heightmap=heightmap,
            )
            terrain_foundation = TerrainFoundationGenerator.foundation_skirt_mesh(
                footprint.polygon, building_base_z=0.0, heightmap=heightmap,
            )
            if add_retaining_wall:
                retaining_wall = RetainingWallGenerator.generate(footprint.polygon, heightmap)

        double_skin = None
        if add_double_skin:
            double_skin = DoubleSkinFacadeGenerator.generate(
                footprint.polygon, base_z=0.0, floor_count=n_floors,
                floor_height=floor_height, gap_m=double_skin_gap_m,
                gap_profile=double_skin_gap_profile,
            )

        return Building(
            footprint=footprint, floors=floors, roof=roof_mesh,
            facade=facade, building_type=bt, basement_envelope=basement_envelope,
            terrain_foundation=terrain_foundation, retaining_wall=retaining_wall,
            terrain_intersection=terrain_intersection, double_skin=double_skin,
        )

    @staticmethod
    def _generate_basement_floors(
        polygon: Polygon,
        bt: BuildingType,
        rule: BuildingTypeRule,
        basement_floor_count: int,
        basement_floor_height: float,
        seed: int | None,
        generate_interior: bool,
    ) -> tuple[list[Floor], Mesh3D]:
        """Faz 1.2 — z=0'ın altına `basement_floor_count` adet yer altı kat
        üretir. Pencere yok, oda tipleri GARAJ/DEPO'ya sabitlenir. Zemin-üstü
        `FacadeGenerator`'dan bağımsız olarak, her kat için basit bir
        (pencersiz) kutu-ekstrüzyon dış zarf mesh'i üretilir."""
        floors: list[Floor] = []
        envelope_parts: list[Mesh3D] = []
        for i in range(basement_floor_count):
            level = -(basement_floor_count - i)  # en alttaki en negatif
            floor_base_z = level * basement_floor_height
            floor_seed = None if seed is None else seed + level

            rooms = RoomGenerator.generate(
                polygon, building_type=bt.value,
                min_room_size=rule.room_generation_min_size, seed=floor_seed,
            )
            # Yer altı kat: oda tipleri GARAJ/DEPO'ya sabitlenir (gerçek
            # kural: yer altı katlar konut/ofis olarak kullanılmaz).
            for room in rooms:
                room.room_type = RoomType.GARAJ if i == basement_floor_count - 1 else RoomType.DEPO

            corridors = CorridorGenerator.from_rooms(rooms)
            interior_doors = DoorGenerator.interior_doors(rooms)

            interior_mesh = None
            if generate_interior:
                interior_parts: list[Mesh3D] = []
                slab_thickness = min(0.15, basement_floor_height * 0.08)
                floor_slab = FloorPlateBuilder.build(
                    polygon, floor_base_z, slab_thickness=slab_thickness,
                    name=f"basement_floor_slab_{level}",
                )
                if floor_slab.triangle_count() > 0:
                    interior_parts.append(floor_slab)
                wall_mesh = InteriorWallBuilder.build_floor_walls(
                    rooms, polygon, base_z=floor_base_z, floor_height=basement_floor_height,
                    interior_doors=interior_doors,
                )
                if wall_mesh.triangle_count() > 0:
                    interior_parts.append(wall_mesh)
                interior_mesh = MeshMerger.merge(interior_parts, name=f"basement_interior_{level}") if interior_parts else None

            # Dış zarf: penceresiz kutu ekstrüzyonu (istinat duvarı yaklaşıklığı).
            envelope = MeshBuilder.extrude_polygon(
                polygon, base_z=floor_base_z, height=basement_floor_height,
                name=f"basement_envelope_{level}",
            )
            if envelope.triangle_count() > 0:
                envelope_parts.append(envelope)

            floors.append(Floor(
                level=level, height_m=basement_floor_height, rooms=rooms,
                corridors=corridors, doors=[d.position for d in interior_doors],
                windows=[],  # bilinçli olarak boş - "ışıksız" yer altı kat
                interior_doors=interior_doors, interior_mesh=interior_mesh,
                is_below_grade=True,
            ))

        envelope_mesh = MeshMerger.merge(envelope_parts, name="basement_envelope") if envelope_parts else Mesh3D(name="basement_envelope")
        return floors, envelope_mesh

    @staticmethod
    def _resolve_type(footprint: Footprint, override: BuildingType | str | None) -> BuildingType:
        if override is not None:
            return override if isinstance(override, BuildingType) else BuildingType(override.lower())
        raw = (footprint.building_type or "").lower()
        for bt in BuildingType:
            if bt.value in raw:
                return bt
        return BuildingType.APARTMAN

    @staticmethod
    def _generate_floors(
        polygon: Polygon,
        bt: BuildingType,
        rule: BuildingTypeRule,
        n_floors: int,
        floor_height: float,
        seed: int | None,
        generate_interior: bool = False,
        min_room_size: float | None = None,
        window_spacing: float | None = None,
    ) -> list[Floor]:
        floors: list[Floor] = []
        centroid = ProceduralBuildingGenerator._centroid(polygon)
        total_height = n_floors * floor_height
        effective_min_room_size = min_room_size if min_room_size is not None else rule.room_generation_min_size
        effective_window_spacing = window_spacing if window_spacing is not None else rule.window_spacing

        # Asansör çekirdeği bina genelinde tek/sabit konumda olmalı (tüm
        # katlarda aynı şaft) - bina bazında bir kere hesaplanır.
        elevator_core = None
        if rule.has_elevator:
            elevator_core = ElevatorCoreGenerator.generate(
                centroid, total_building_height=total_height, base_z=0.0,
            )

        for level in range(n_floors):
            floor_seed = None if seed is None else seed + level
            floor_base_z = level * floor_height
            rooms = RoomGenerator.generate(
                polygon, building_type=bt.value,
                min_room_size=effective_min_room_size, seed=floor_seed,
            )
            corridors = CorridorGenerator.from_rooms(rooms)
            interior_doors = DoorGenerator.interior_doors(rooms)
            entrance = DoorGenerator.exterior_entrance(polygon) if level == 0 else None

            windows = WindowGenerator.place_on_footprint(
                polygon, spacing=effective_window_spacing, seed=floor_seed,
            )

            stair_position = centroid
            if elevator_core is not None:
                # merdiveni asansör çekirdeğinin yanına kaydır ki çakışmasınlar
                stair_position = Point2D(centroid.x + elevator_core.width, centroid.y)
            stair = StairGenerator.generate(stair_position, floor_height)

            # -- Faz 2: gerçek iç mekan mesh'i (yalnızca istenirse - LOD) --- #
            interior_mesh = None
            furniture: list[FurnitureItem] = []
            if generate_interior:
                interior_parts: list[Mesh3D] = []

                # Kat zemini (Faz 1 düzeltmesi): eskiden yalnızca Facade.mesh
                # içindeki dış-kabuk döşemesi vardı - x-ray/kesit/patlatma
                # gibi "sadece iç mekan" görünümlerinde (facade mesh'i devre
                # dışı bırakıldığında) katlar arasında görünür bir zemin
                # kalmıyordu, kullanıcı katları yalnızca pencere sırasından
                # sayabiliyordu. Artık her katın `interior_mesh`'i kendi
                # zemin döşemesini de taşır - facade'dan bağımsız olarak.
                slab_thickness = min(0.15, floor_height * 0.08)
                floor_slab = FloorPlateBuilder.build(
                    polygon, floor_base_z, slab_thickness=slab_thickness,
                    name=f"interior_floor_slab_{level}",
                )
                if floor_slab.triangle_count() > 0:
                    interior_parts.append(floor_slab)

                wall_mesh = InteriorWallBuilder.build_floor_walls(
                    rooms, polygon, base_z=floor_base_z, floor_height=floor_height,
                    interior_doors=interior_doors,
                )
                if wall_mesh.triangle_count() > 0:
                    interior_parts.append(wall_mesh)

                stair_mesh = StairMeshBuilder.build(stair, base_z=floor_base_z)
                if stair_mesh.triangle_count() > 0:
                    interior_parts.append(stair_mesh)

                if level == 0 and elevator_core is not None:
                    # Asansör kuyusu taban-tavan arası tek parça olarak zemin
                    # katta bir kere üretilip bina mesh'ine eklenir (üst
                    # katlarda tekrar üretilmez - aynı şaft tüm katları
                    # kat eder).
                    shaft_mesh = ElevatorShaftMeshBuilder.build(
                        elevator_core, floor_height=floor_height, floor_count=n_floors,
                    )
                    if shaft_mesh.triangle_count() > 0:
                        interior_parts.append(shaft_mesh)

                furniture = FurnitureGenerator.place_for_floor(
                    rooms, base_z=floor_base_z, floor_level=level,
                    stairs=[stair], exterior_door=entrance,
                )
                furniture_mesh = FurnitureGenerator.build_mesh(furniture)
                if furniture_mesh.triangle_count() > 0:
                    interior_parts.append(furniture_mesh)

                interior_mesh = MeshMerger.merge(interior_parts, name=f"interior_floor_{level}") if interior_parts else None

            door_positions = [d.position for d in interior_doors]
            if entrance:
                door_positions.append(entrance.position)

            floors.append(Floor(
                level=level, height_m=floor_height, rooms=rooms,
                corridors=corridors, stairs=[stair.position],
                elevators=[elevator_core.position] if elevator_core else [],
                doors=door_positions, windows=[w.position for w in windows],
                stair_objects=[stair],
                elevator_objects=[elevator_core] if (elevator_core and level == 0) else [],
                interior_doors=interior_doors,
                furniture=furniture,
                interior_mesh=interior_mesh,
            ))
        return floors

    @staticmethod
    def _centroid(polygon: Polygon) -> Point2D:
        xs = [p.x for p in polygon.points]
        ys = [p.y for p in polygon.points]
        return Point2D(sum(xs) / len(xs), sum(ys) / len(ys))


class CityScenePacker:
    """ROADMAP_V8 Faz 6.4 (şehir-ölçeği kalan madde - bkz. RFC/PROGRESS
    notu "atlas paketleme şu an yalnızca tek bina içindeki parçalar
    arasında çalışıyor"): birden fazla `Building` arasında, her binayı
    sahnede **ayrı bir yerleştirilebilir nesne olarak koruyarak**
    (`MeshUVAtlasBaker.bake_scene`, bkz. o metodun docstring'i - neden
    tam mega-mesh birleştirme değil) ortak bir doku atlası + (opsiyonel)
    binalar-arası gölgeleme dahil AO sağlar.

    Tamamen opt-in, ayrı bir üst-seviye orkestratördür - `Building`/
    `ProceduralBuildingGenerator`'ın var olan tek-bina API'sini hiç
    değiştirmez, üzerine ek bir sahne-kompozisyon katmanı ekler.
    """

    @staticmethod
    def pack_shared_atlas(
        buildings: dict[str, Building],
        texture_size_m: float = 2.0,
        atlas_texture_px: int = 512,
        atlas_width_px: int = 4096,
        atlas_height_px: int = 4096,
        include_interior: bool = False,
    ) -> dict[str, Mesh3D]:
        """`buildings`: `{bina_id: Building}`. Her binanın `full_mesh(
        generate_uvs=True)` çıktısı alınır (bina-içi `pack_uv_atlas`
        kasıtlı olarak kullanılmaz - burada amaç binalar-arası tek bir
        atlas), sonra hepsi `MeshUVAtlasBaker.bake_scene` ile **tek bir
        paylaşılan doku sayfasına** paketlenir. Her bina kendi ayrı
        `Mesh3D`'sini korur (sahne grafiğinde ayrı ayrı yerleştirilebilir/
        seçilebilir kalır) - yalnızca UV'leri artık ortak atlasın kendi
        alt-bölgesine işaret eder.

        Döner: `{bina_id: atlas-UV'li Mesh3D}` - girdi `buildings` sözlüğü
        değişmez (her `full_mesh()` zaten kendi klonunu üretir).
        """
        if not buildings:
            return {}
        parts: list[tuple[str, Mesh3D, int, int]] = []
        for key, building in buildings.items():
            mesh = building.full_mesh(
                include_interior=include_interior, generate_uvs=True,
                texture_size_m=texture_size_m,
            )
            if mesh.triangle_count() > 0:
                parts.append((key, mesh, atlas_texture_px, atlas_texture_px))
        if not parts:
            return {}
        remapped, _atlas = MeshUVAtlasBaker.bake_scene(
            parts, atlas_width=atlas_width_px, atlas_height=atlas_height_px,
        )
        return remapped

    @staticmethod
    def bake_shared_ao(
        meshes: dict[str, Mesh3D], sample_count: int = 8, max_distance: float = 5.0,
        cell_size: float | None = None,
    ) -> dict[str, Mesh3D]:
        """`meshes`: `{bina_id: Mesh3D}` (ör. `pack_shared_atlas()`
        çıktısı, ya da doğrudan `Building.full_mesh()` sonuçları).
        `SceneAOBaker.apply_scene_ao` (`lighting`, spatial-hash tabanlı,
        `AmbientOcclusionBaker`'ın bina-içi tek-mesh sınırını aşar) ile
        binalar-arası gölgeleme dahil AO uygular - ör. iki bina yan yana
        ise, birinin cephesi diğerinin gölgesinden gerçekten etkilenir
        (tek-bina `bake_ao=True` yolunda bu mümkün değildi).

        Döner: `{bina_id: AO uygulanmış Mesh3D}` (klon-güvenli, girdi
        değişmez).
        """
        if not meshes:
            return {}
        keys = list(meshes.keys())
        mesh_list = [meshes[k] for k in keys]
        ao_applied = SceneAOBaker.apply_scene_ao(
            mesh_list, sample_count=sample_count, max_distance=max_distance,
            cell_size=cell_size,
        )
        return dict(zip(keys, ao_applied))
