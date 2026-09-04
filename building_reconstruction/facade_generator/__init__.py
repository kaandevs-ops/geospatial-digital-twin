"""
Facade Generator
================

Roadmap Phase 3 - "Facade Generator" (AI destekli - burada kural tabanlı
varsayılan; gerçek AI tahmini Phase 4 AIMaterialPredictor'da).

Malzeme + pencere paterni (Window Generator) ile duvar mesh'ine doku/
malzeme ataması yapar.

Desteklenen malzemeler: Cam, Beton, Tuğla, Metal, Kompozit, Taş, Ahşap,
Endüstriyel.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ...core_engine.geometry_engine import Point2D, Polygon
from ...material_engine import PBRMaterial, ProceduralMaterials
from ...mesh_engine import (
    FacadeElementMeshBuilder,
    FloorPlateBuilder,
    Mesh3D,
    MeshBuilder,
    MeshMerger,
    UVGenerator,
    WallOpening,
    WallOpeningMeshBuilder,
)
from ..building_elements import (
    BalconyGenerator,
    BayWindowGenerator,
    DoorGenerator,
    EntranceCanopyGenerator,
    WindowGenerator,
    WindowPlacement,
)
from ..regulations import RegulationProfile, default_profile


class FacadeMaterial(str, Enum):
    CAM = "cam"
    BETON = "beton"
    TUGLA = "tugla"
    METAL = "metal"
    KOMPOZIT = "kompozit"
    TAS = "tas"
    AHSAP = "ahsap"
    ENDUSTRIYEL = "endustriyel"


# Bina tipine göre en olası cephe malzemesi (kural tabanlı varsayılan
# dağılım; Phase 4'te AIMaterialPredictor bu tabloyu ML tahmini ile
# değiştirebilir/override edebilir).
_TYPE_TO_MATERIAL: dict[str, FacadeMaterial] = {
    "apartman": FacadeMaterial.BETON,
    "villa": FacadeMaterial.TAS,
    "ofis": FacadeMaterial.CAM,
    "avm": FacadeMaterial.CAM,
    "fabrika": FacadeMaterial.ENDUSTRIYEL,
    "depo": FacadeMaterial.METAL,
    "hastane": FacadeMaterial.KOMPOZIT,
    "okul": FacadeMaterial.TUGLA,
    "sanayi_tesisi": FacadeMaterial.ENDUSTRIYEL,
    "terminal": FacadeMaterial.CAM,
    "hangar": FacadeMaterial.METAL,
    "stadyum": FacadeMaterial.KOMPOZIT,
}


@dataclass(slots=True)
class Facade:
    """Roadmap Phase 3 veri modeli: `Facade`."""

    material: FacadeMaterial
    pbr_material: PBRMaterial
    windows: list[WindowPlacement]
    mesh: Mesh3D | None = None
    doors: list = None  # type: ignore[assignment]  # list[Door], per-floor entrance/exterior doors
    floor_count: int = 1
    floor_height: float = 3.0
    # FAZ 0 tanı desteği: `mesh` tüm katların MeshMerger.merge sonucudur ve
    # kat sınırları merge sonrası artık ayırt edilemez. Kat hizalama
    # denetimi (bkz. mesh_engine.quality_metrics.FloorAlignmentAnalyzer)
    # her katın kendi duvar mesh'ine ihtiyaç duyar; bu yüzden merge'den
    # önceki kat-kat mesh listesi de ayrıca saklanır (opsiyonel, geriye
    # dönük uyumlu - eski kod bu alanı hiç bilmeden çalışmaya devam eder).
    floor_meshes: list[Mesh3D] | None = None
    # Roadmap yeni_roadmap.md - Faz 1.3: cephe zenginleştirme elemanları
    # (opsiyonel, `generate(add_balconies=...)` vb. ile üretilir).
    balconies: list = None  # type: ignore[assignment]  # list[Balcony]
    bay_windows: list = None  # type: ignore[assignment]  # list[BayWindow]
    canopy: object = None  # EntranceCanopy | None

    def __post_init__(self) -> None:
        if self.doors is None:
            self.doors = []
        if self.balconies is None:
            self.balconies = []
        if self.bay_windows is None:
            self.bay_windows = []


# Roadmap V3 - D13: gerçek, alıntılanabilir standart/yönetmelik madde
# referanslarına dayalı pencere/duvar oranı ve kaçış yolu eşikleri.
# Kaynaklar (tam metin kopyalanmadan, yalnızca sayısal eşik + madde
# referansı):
#
#   [PAİY-8] Planlı Alanlar İmar Yönetmeliği, Madde 8 - konut/işyeri
#            mekanlarında doğal aydınlatma/havalandırma için asgari
#            pencere alanı / döşeme alanı oranı hükümleri; burada duvar
#            alanına oranlanmış eşdeğer bir yaklaşım kullanılmıştır.
#   [TS 825] TS 825 "Binalarda Isı Yalıtım Kuralları" - bina kabuğu
#            (opak/saydam yüzey) oranı, iklim bölgesine göre camlanma
#            oranı üst/alt sınırlarını etkiler; endüstriyel/depo gibi
#            düşük-ısıtmalı hacimlerde düşük camlanma oranı beklenir.
#   [BYKHY]  Binaların Yangından Korunması Hakkında Yönetmelik (RG
#            19.12.2007/26735) - kat sayısına bağlı ikinci kaçış yolu/
#            yangın merdiveni zorunluluğu hükümleri.
MIN_WINDOW_WALL_RATIO: dict[str, float] = {
    # Konut: [PAİY-8] iç mekan aydınlatma/havalandırma için asgari pencere/duvar oranı.
    "apartman": 0.12,
    "villa": 0.12,
    "ofis": 0.18,  # [PAİY-8] çalışma mekânı için daha yüksek asgari aydınlatma oranı
    "avm": 0.10,  # [PAİY-8] ticari mekân, yapay aydınlatmayla desteklenebilir
    "okul": 0.15,  # [PAİY-8] derslik için asgari doğal aydınlatma oranı
    "hastane": 0.15,  # [PAİY-8] hasta odası için asgari doğal aydınlatma oranı
    "fabrika": 0.05,  # [TS 825] düşük-ısıtmalı endüstriyel hacim, düşük camlanma
    "depo": 0.03,  # [TS 825] ısıtılmayan/az ısıtılan hacim, minimum camlanma
    "sanayi_tesisi": 0.05,  # [TS 825] düşük-ısıtmalı endüstriyel hacim
    "terminal": 0.20,  # [PAİY-8] kamuya açık, yüksek doğal aydınlatma beklentisi
    "hangar": 0.03,  # [TS 825] ısıtılmayan/az ısıtılan büyük hacim
    "stadyum": 0.05,  # [TS 825] açık/yarı-açık hacim, düşük camlanma gereksinimi
    "_default": 0.10,
}

# [BYKHY] kat sayısına bağlı ikinci kaçış yolu zorunluluğu: 4 kat ve üzeri
# yapılarda tek kaçış yolu yeterli kabul edilmez.
FIRE_ESCAPE_MIN_FLOORS = 4

# Kaynak künyesi - README'de ve raporlarda gösterim için.
THRESHOLD_SOURCES: dict[str, str] = {
    "PAİY-8": "Planlı Alanlar İmar Yönetmeliği, Madde 8 (RG 3.7.2017/30113)",
    "TS 825": "TS 825 Binalarda Isı Yalıtım Kuralları (TSE)",
    "BYKHY": "Binaların Yangından Korunması Hakkında Yönetmelik (RG 19.12.2007/26735)",
}


@dataclass(slots=True)
class FacadeComplianceReport:
    """`FacadeGenerator.check_compliance()` çıktısı — bir cephenin
    parametrik bina-yönetmeliği kurallarına göre denetim sonucu."""

    wall_area_m2: float
    window_area_m2: float
    window_wall_ratio: float
    min_required_ratio: float
    meets_window_ratio: bool
    floor_count: int
    requires_fire_escape: bool
    issues: list[str]

    @property
    def is_compliant(self) -> bool:
        return self.meets_window_ratio and not self.issues


class FacadeGenerator:
    """Footprint + bina tipi -> `Facade` (malzeme + pencere yerleşimi +
    (opsiyonel) duvar mesh'i)."""

    @staticmethod
    def material_for_building_type(building_type: str | None) -> FacadeMaterial:
        key = (building_type or "").lower()
        return _TYPE_TO_MATERIAL.get(key, FacadeMaterial.BETON)

    @staticmethod
    def material_for_building_type_with_profile(
        building_type: str | None,
        profile: RegulationProfile | None,
    ) -> FacadeMaterial:
        """Roadmap 1.4: 'Malzeme-yönetmelik ilişkisi'. Varsayılan bina-tipi
        malzemesi, verilen `profile`'da izinli değilse (örn. tarihi doku
        bölgesinde cam/beton yasaklıysa) profildeki ilk izinli malzemeye
        düşer. `profile=None` veya kısıt tanımsızsa eski davranışla
        birebir aynıdır (geriye dönük uyumlu)."""
        default_material = FacadeGenerator.material_for_building_type(building_type)
        if profile is None:
            return default_material
        if profile.facade_material_allowed(building_type, default_material.value):
            return default_material
        key = (building_type or "").lower()
        allowed = (profile.allowed_facade_materials or {}).get(
            key, (profile.allowed_facade_materials or {}).get("_default")
        )
        if not allowed:
            return default_material
        for candidate in FacadeMaterial:
            if candidate.value in allowed:
                return candidate
        return default_material

    @staticmethod
    def build_pbr_material(material: FacadeMaterial, seed: int | None = None) -> PBRMaterial:
        return ProceduralMaterials.create(material.value, variation_seed=seed)

    @staticmethod
    def generate(
        polygon: Polygon,
        building_type: str | None,
        base_z: float,
        floor_height: float,
        window_width: float = 1.2,
        window_height: float = 1.4,
        seed: int | None = None,
        material_override: FacadeMaterial | None = None,
        build_mesh: bool = True,
        floor_count: int = 1,
        wall_thickness: float = 0.25,
        door_width: float = 1.2,
        door_height: float = 2.1,
        add_balconies: bool = False,
        balcony_every_nth: int = 3,
        balcony_depth: float = 1.2,
        add_bay_windows: bool = False,
        bay_window_every_nth: int = 4,
        add_entrance_canopy: bool = False,
        use_shape_grammar: bool = False,
        floor_polygons: list[Polygon] | None = None,
    ) -> Facade:
        """Footprint + bina tipi + kat sayısı -> gerçek, kat kat modellenmiş
        `Facade`.

        Faz 1 düzeltmesi: eskiden burada tüm footprint tek seferde
        `base_z .. base_z+floor_height` arasında extrude edilip tek bir
        prizma (küp benzeri) mesh üretiliyordu; pencereler yalnızca
        uyumluluk kontrolü için hesaplanıp mesh'e hiç işlenmiyordu, kat
        ayrımı görsel olarak yoktu. Artık:
          - her kat için ayrı ayrı, her duvar kenarı için pencereler (ve
            zemin katta ana giriş kapısı) gerçekten mesh'ten kesiliyor,
          - her kat arasına ince bir döşeme (floor plate) plakası ekleniyor,
          - tüm kat/pencere/kapı/döşeme mesh'leri tek bir `Facade.mesh`'te
            birleştiriliyor (`MeshMerger.merge`).

        `floor_polygons` (ROADMAP_V8 Faz 6.1 "Seçenek A - tam yapısal
        versiyon", varsayılan `None` - opt-in, geriye dönük tam uyumlu):
        verilmezse davranış birebir eskisiyle aynıdır (tüm katlar `polygon`
        argümanının tek bir taban çokgenini paylaşır). Verilirse, uzunluğu
        `floor_count` ile eşleşmesi gereken bir liste olmalı ve her kat
        (`floor_idx`) kendi `floor_polygons[floor_idx]` çokgenini alır -
        bu, ana yapısal cephe sistemini (pencere/kapı/duvar/döşeme, gerçek
        NURBS yüzey matematiği yazmadan) düşey eksende gerçekten eğrilen/
        daralan/dönen bir silüete kavuşturur. Her katın çokgeni, taban
        çokgenle **aynı köşe sayısı ve kenar sırasına** sahip olmalıdır
        (yalnızca ölçek/rotasyon/öteleme farklı olmalı) - aksi halde pencere
        `wall_edge_index` eşlemesi (taban `polygon`'dan hesaplanır) yanlış
        kenara düşer. `VerticalProfileGenerator.floor_polygons()`
        (`building_reconstruction.curved_facade`) bu koşulu garanti eden
        bir üretici sağlar. Bkz. `docs/RFC_FAZ6_1_KAVISLI_CEPHE.md` Seçenek A.
        """
        if floor_polygons is not None and len(floor_polygons) != max(1, floor_count):
            raise ValueError(
                f"floor_polygons uzunluğu ({len(floor_polygons)}) floor_count "
                f"({max(1, floor_count)}) ile eşleşmeli"
            )
        material = material_override or FacadeGenerator.material_for_building_type(building_type)
        pbr = FacadeGenerator.build_pbr_material(material, seed=seed)

        # Zemin kat penceresi/duvar oranı denetimi geriye dönük uyumluluk
        # için tek-kat footprint bazlı hesaplanmaya devam eder.
        #
        # ROADMAP V5 - M2.3: `use_shape_grammar=True` iken sabit-ızgara
        # `WindowGenerator.place_on_footprint` yerine, bina tipine göre
        # kısıt setiyle çalışan `ShapeGrammarFacadeGenerator` kullanılır -
        # her seed farklı bay bölünmesi/pencere oranı üreterek "mekanik"
        # tekrar hissini kırar (varsayılan davranış DEĞİŞMEDİ: eski
        # çağıranlar hiçbir fark görmeden aynı sonucu almaya devam eder).
        if use_shape_grammar:
            from ..facade_grammar import ShapeGrammarFacadeGenerator

            windows = ShapeGrammarFacadeGenerator.place_on_footprint(
                polygon,
                building_type=building_type,
                seed=seed,
            )
        else:
            windows = WindowGenerator.place_on_footprint(
                polygon,
                window_width=window_width,
                window_height=window_height,
                sill_height=floor_height * 0.35,
                spacing=2.5,
                seed=seed,
            )
        entrance = DoorGenerator.exterior_entrance(polygon, width=door_width)

        mesh = None
        doors: list = []
        if build_mesh:
            ring = polygon.closed_ring()[:-1]
            if not Polygon(ring).is_ccw():
                ring = list(reversed(ring))
            edge_lengths = [
                ring[i].distance_to(ring[(i + 1) % len(ring)]) for i in range(len(ring))
            ]

            floor_meshes: list[Mesh3D] = []
            per_floor_meshes: list[Mesh3D] = []  # FAZ 0: kat başına ayrı (merge edilmemiş) mesh
            facade_balconies: list = []
            facade_bay_windows: list = []
            for floor_idx in range(max(1, floor_count)):
                floor_base_z = base_z + floor_idx * floor_height
                this_floor_wall_meshes: list[Mesh3D] = []
                sill = floor_height * 0.35
                # `floor_polygons` verildiyse bu katın kendi (ölçeklenmiş/
                # döndürülmüş) çokgenini kullan - Faz 6.1 Seçenek A: ana
                # yapısal sistem düşey eksende gerçekten eğrilir. Kenar
                # sayısı/sırası taban `ring` ile aynı olduğu için
                # `wall_edge_index` eşlemesi (pencereler taban `polygon`'dan
                # hesaplandı) hâlâ doğru kenara denk gelir.
                floor_ring = ring
                if floor_polygons is not None:
                    fp_ring = floor_polygons[floor_idx].closed_ring()[:-1]
                    if not Polygon(fp_ring).is_ccw():
                        fp_ring = list(reversed(fp_ring))
                    if len(fp_ring) == len(ring):
                        floor_ring = fp_ring
                # Kat başına pencere yerleşimi: her kenarı ayrı ayrı, kenar
                # yerel u-koordinatına göre boşluk listesine çevir.
                for edge_idx, (a, b) in enumerate(zip(floor_ring, floor_ring[1:] + floor_ring[:1])):
                    edge_windows = [w for w in windows if w.wall_edge_index == edge_idx]
                    is_entrance_edge = floor_idx == 0 and edge_idx == entrance.wall_edge_index
                    if is_entrance_edge:
                        # FAZ 1 kök neden düzeltmesi: giriş kapısı en uzun
                        # kenarın ORTASINA, simetrik pencere yerleşimi de
                        # aynı kenarın ortasına odaklandığı için kapı ve
                        # pencere neredeyse çakışan (birkaç cm arayla)
                        # ayrı açıklıklar üretiyordu (bkz. FAZ0 raporu -
                        # ~50mm "kat hizalama" sapması aslında buydu).
                        # Kapıyla u-aralığı (pay dahil) çakışan pencereler
                        # zemin kat girişinde elenir.
                        door_clearance = 0.3  # m, kapı kenarından ek boşluk
                        door_u = a.distance_to(entrance.position)
                        door_u0 = door_u - entrance.width / 2.0 - door_clearance
                        door_u1 = door_u + entrance.width / 2.0 + door_clearance
                        edge_windows = [
                            w
                            for w in edge_windows
                            if not (
                                (a.distance_to(w.position) + w.width / 2.0) > door_u0
                                and (a.distance_to(w.position) - w.width / 2.0) < door_u1
                            )
                        ]
                    openings: list[WallOpening] = []
                    for w in edge_windows:
                        u = a.distance_to(w.position)
                        openings.append(
                            WallOpening(
                                u_start=u - w.width / 2.0,
                                u_end=u + w.width / 2.0,
                                v_start=sill,
                                v_end=sill + w.height,
                                kind="window",
                            )
                        )
                    if is_entrance_edge:
                        du = a.distance_to(entrance.position)
                        openings.append(
                            WallOpening(
                                u_start=du - entrance.width / 2.0,
                                u_end=du + entrance.width / 2.0,
                                v_start=0.0,
                                v_end=door_height,
                                kind="door",
                            )
                        )
                        doors.append(entrance)
                        if add_entrance_canopy:
                            canopy = EntranceCanopyGenerator.for_entrance(entrance)
                            canopy_mesh = FacadeElementMeshBuilder.build_entrance_canopy(
                                a,
                                b,
                                entrance.position,
                                width=canopy.width,
                                depth=canopy.depth,
                                base_z=floor_base_z + canopy.height_above_door,
                                thickness=canopy.thickness,
                                name=f"canopy_f{floor_idx}",
                            )
                            # ROADMAP_V7.md'nin son "Kalan" maddesi: bu üretim
                            # yolu (extrude_polygon tabanlı) UV atamıyordu -
                            # `UVGenerator.box_mapping` (mevcut, değiştirilmedi)
                            # ile gerçek doku koordinatı kazandırılır.
                            floor_meshes.append(UVGenerator.box_mapping(canopy_mesh))
                    if add_balconies and floor_idx > 0 and not is_entrance_edge and edge_windows:
                        balconies_here = BalconyGenerator.place_on_windows(
                            edge_windows,
                            depth=balcony_depth,
                            every_nth=balcony_every_nth,
                            floor_level=floor_idx,
                            min_floor_for_balcony=1,
                        )
                        for bi, bal in enumerate(balconies_here):
                            balcony_mesh = FacadeElementMeshBuilder.build_balcony(
                                a,
                                b,
                                bal.position,
                                width=bal.width,
                                depth=bal.depth,
                                base_z=floor_base_z,
                                name=f"balcony_f{floor_idx}_e{edge_idx}_{bi}",
                            )
                            floor_meshes.append(UVGenerator.box_mapping(balcony_mesh))
                            facade_balconies.append(bal)
                    if add_bay_windows and edge_windows:
                        bays_here = BayWindowGenerator.place_on_windows(
                            edge_windows,
                            every_nth=bay_window_every_nth,
                        )
                        for bwi, bw in enumerate(bays_here):
                            bay_mesh = FacadeElementMeshBuilder.build_bay_window(
                                a,
                                b,
                                bw.window.position,
                                side_width=bw.side_width,
                                protrusion=bw.protrusion,
                                base_z=floor_base_z,
                                height=floor_height * 0.6,
                                name=f"bay_f{floor_idx}_e{edge_idx}_{bwi}",
                            )
                            floor_meshes.append(UVGenerator.box_mapping(bay_mesh))
                            facade_bay_windows.append(bw)
                    wall_mesh = WallOpeningMeshBuilder.build_wall_segment(
                        a,
                        b,
                        floor_base_z,
                        floor_height,
                        openings,
                        thickness=wall_thickness,
                        name=f"wall_f{floor_idx}_e{edge_idx}",
                    )
                    if wall_mesh.triangle_count() > 0:
                        floor_meshes.append(wall_mesh)
                        this_floor_wall_meshes.append(wall_mesh)

                if this_floor_wall_meshes:
                    per_floor_meshes.append(
                        MeshMerger.merge(this_floor_wall_meshes, name=f"floor_{floor_idx}_walls")
                    )

                # Kat döşemesi (zemin katta temel/taban, üst katlarda ara
                # döşeme) - kat ayrımını mesh'te görsel olarak temsil eder.
                floor_meshes.append(
                    UVGenerator.box_mapping(
                        FloorPlateBuilder.build(
                            Polygon(floor_ring),
                            floor_base_z,
                            slab_thickness=min(0.25, floor_height * 0.1),
                            name=f"floor_plate_{floor_idx}",
                        )
                    )
                )

            # Çatı seviyesindeki üst döşeme (en üst kat tavanı / çatı tabanı).
            # `floor_polygons` verildiyse en üst katın çokgenini kullanır
            # (böylece çatı, gerçekte üretilen daralan/genişleyen üst kat
            # profiline oturur - `floor_ring` döngüden sonra son katın
            # değerini taşır).
            roof_z = base_z + floor_count * floor_height
            floor_meshes.append(
                UVGenerator.box_mapping(
                    FloorPlateBuilder.build(
                        Polygon(floor_ring),
                        roof_z,
                        slab_thickness=min(0.25, floor_height * 0.1),
                        name="roof_plate",
                    )
                )
            )

            mesh = MeshMerger.merge(floor_meshes, name="facade_building")

        return Facade(
            material=material,
            pbr_material=pbr,
            windows=windows,
            mesh=mesh,
            floor_meshes=(per_floor_meshes if build_mesh else None),
            doors=doors,
            floor_count=max(1, floor_count),
            floor_height=floor_height,
            balconies=(facade_balconies if build_mesh else []),
            bay_windows=(facade_bay_windows if build_mesh else []),
        )

    # ------------------------------------------------------------------ #
    # Roadmap V2 - A3: parametrik bina yönetmeliği kural motoru
    # ------------------------------------------------------------------ #
    @staticmethod
    def check_compliance(
        facade: Facade,
        polygon: Polygon,
        floor_height: float,
        floor_count: int,
        building_type: str | None,
        has_second_egress: bool = False,
        profile: RegulationProfile | None = None,
    ) -> FacadeComplianceReport:
        """Üretilmiş bir `Facade`'ı basitleştirilmiş pencere/duvar oranı ve
        kaçış yolu kurallarına göre denetler.

        - `window_wall_ratio`: toplam pencere alanı / duvar (cephe) alanı.
          `profile`'ın pencere/duvar oranı eşiğiyle karşılaştırılır.
        - `requires_fire_escape`: kat sayısı `profile.fire_escape_min_floors`
          ve üzeriyse `has_second_egress=False` olduğu sürece `issues`
          listesine eklenir.

        Roadmap V4 - Faz E19: eşikler artık `RegulationProfile` üzerinden
        parametriktir. `profile=None` verilirse (geriye uyumlu varsayılan)
        modül seviyesindeki TS/ISO tabanlı `MIN_WINDOW_WALL_RATIO` /
        `FIRE_ESCAPE_MIN_FLOORS` sabitleriyle birebir aynı olan
        `default_profile()` kullanılır - mevcut davranış değişmez.
        """
        active_profile = profile if profile is not None else default_profile()
        wall_area = polygon.perimeter() * floor_height
        window_area = sum(w.width * w.height for w in facade.windows)
        ratio = window_area / wall_area if wall_area > 1e-9 else 0.0

        key = (building_type or "").lower()
        min_ratio = active_profile.window_ratio_threshold(key)
        meets_ratio = ratio >= min_ratio

        issues: list[str] = []
        if not meets_ratio:
            issues.append(
                f"pencere/duvar orani {ratio:.3f} < asgari {min_ratio:.3f} "
                f"(bina_tipi={key or 'bilinmiyor'}) "
                f"[PAİY-8/TS 825 | profil={active_profile.name}]"
            )

        requires_escape = floor_count >= active_profile.fire_escape_min_floors
        if requires_escape and not has_second_egress:
            issues.append(
                f"{floor_count} kat >= {active_profile.fire_escape_min_floors}: "
                "ikinci kacis yolu/yangin merdiveni gerekli ancak saglanmamis "
                f"[BYKHY | profil={active_profile.name}]"
            )

        # yeni_roadmap.md Faz 1.4: malzeme-yönetmelik ilişkisi (örn. tarihi
        # doku/sit alanı bölgelerinde cephe malzemesi kısıtlı olabilir).
        if not active_profile.facade_material_allowed(building_type, facade.material.value):
            issues.append(
                f"cephe malzemesi '{facade.material.value}' bu bölge profilinde "
                f"izinli degil (bina_tipi={key or 'bilinmiyor'}) "
                f"[profil={active_profile.name}]"
            )

        return FacadeComplianceReport(
            wall_area_m2=wall_area,
            window_area_m2=window_area,
            window_wall_ratio=ratio,
            min_required_ratio=min_ratio,
            meets_window_ratio=meets_ratio,
            floor_count=floor_count,
            requires_fire_escape=requires_escape,
            issues=issues,
        )
