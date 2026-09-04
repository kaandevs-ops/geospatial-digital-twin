"""
Power Infrastructure
==================

ROADMAP_V7.md Bölüm B1 - "Altyapı (ek öneri - sende yoktu ama mantıklı bir
genişleme)":

    "Elektrik direği/hattı (`power=pole`, `power=line`) - şehir dışı/kırsal
    senaryolarda görsel bütünlük için. Trafo (`power=substation`), baz
    istasyonu (`man_made=tower` + `tower:type=communication`)."

`power=pole` zaten `street_furniture` (C3/3. dilim) kapsamında - bu modül
B1'in "altyapı" alt kümesinin geri kalanını (hat, trafo, baz istasyonu)
tamamlar; B1'in son işlenmemiş kategori grubu, tamamlanınca Faz C3'ün
B1 kapsamı kapanmış olur.

- **Hat** (`power=line`, LineString): B2 "çizgi feature" stratejisi -
  mevcut `editor.road_editor.Road` (Faz 8, tape-extrusion) yeniden
  kullanılır (`editor/osm_bridge.py`'nin `roads`/`waterway` için yaptığı
  gibi) direk noktalarını taşımak için; mesh üretiminde ise
  ROADMAP_V8.md Faz 5.6c ile artık gerçek bir parabolik katener
  (sarkma) yaklaşıklaması uygulanır (bkz. `mesh_for_power_line`,
  `catenary=True` varsayılan) — düz/sabit-yükseklik eski davranış
  `catenary=False` ile performans modu fallback'i olarak korunur.
- **Trafo** (`power=substation`, Polygon): B2 "alan feature" stratejisi -
  mevcut `MeshBuilder.extrude_polygon` (Faz 2) ile alçak, çitli/duvarlı
  bir platform yaklaşıklaması.
- **Baz istasyonu** (`man_made=tower` + `tower:type=communication`,
  Point): kafes kule yaklaşıklığı - daralan silindir yığını + anten
  kutuları (düşük-poly, harici asset yok).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core_engine.geometry_engine import Point2D, Polygon
from ..editor.road_editor import Road
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, UVGenerator, Vertex3D

#: Elektrik hattının sabit render yüksekliği - `street_furniture.
#: StreetFurnitureGenerator.power_pole`'daki direk yüksekliğiyle (8.0m)
#: tutarlı, hat görsel olarak direklerin tepesinden geçiyormuş gibi
#: görünsün diye.
DEFAULT_POWER_LINE_HEIGHT_M = 8.0
DEFAULT_POWER_LINE_WIDTH_M = 0.08

#: Trafo platformunun varsayılan yüksekliği (B1: "basit hacim" - gerçek
#: ekipman/çit detayı üretilmez, yalnızca hacim).
DEFAULT_SUBSTATION_HEIGHT_M = 1.8

#: ROADMAP_V8.md Faz 5.6c — katener (sarkma) yaklaşıklaması. Gerçek katener
#: fiziği (cosh tabanlı) yerine, "approximate simülasyon yeterli" ilkesiyle
#: (Wind/CFD ve FrustumCulling'deki yaklaşıklamalarla aynı felsefe) basit
#: parabolik bir sarkma kullanılır: her direk-arası açıklığın (span) orta
#: noktasında en fazla, uçlarda sıfır sarkma. Sarkma miktarı açıklık
#: uzunluğuyla orantılı (gerçek hatlarda da böyledir - uzun açıklık daha
#: çok sarkar), sabit bir oran (%) ile.
DEFAULT_POWER_LINE_SAG_RATIO = 0.03
#: Her açıklık (iki direk arası) kaç ara noktayla örneklenecek - düz
#: çizgi fallback'inde 1 (örnekleme yok), katener modunda pürüzsüz bir
#: eğri için >=6 gerekir.
DEFAULT_CATENARY_SAMPLES_PER_SPAN = 8


@dataclass(slots=True)
class SubstationItem:
    """Bir `power=substation` poligonu - mesh üretimi
    `PowerInfrastructureGenerator.substation`'a bırakılır (önceki
    köprülerle aynı tasarım kararı: erken mesh üretimi dayatılmaz)."""

    polygon: Polygon


@dataclass(slots=True)
class CommunicationTowerItem:
    """Bir `man_made=tower` (`tower:type=communication`) noktası."""

    position: Point2D
    height_m: float = 25.0
    ground_z: float = 0.0


class PowerInfrastructureGenerator:
    """OSM altyapı feature'larından prosedürel mesh üretimi. Diğer OSM
    köprüleriyle (`street_furniture`, `sport_recreation`, ...) aynı
    desen: stdlib-only, yalnızca `mesh_engine`/`editor.road_editor`
    primitifleri + `MeshMerger`.
    """

    @staticmethod
    def power_line_from_points(
        points: list[Point2D],
        osm_id: str = "power_line",
    ) -> Road:
        """`power=line` (LineString) -> yeniden kullanılan `Road`
        tape-extrusion altyapısı (B2 "çizgi feature" stratejisi,
        `editor/osm_bridge.py`'nin `roads`/`waterway` ile aynı deseni)."""
        return Road(
            road_id=f"osm_power_line_{osm_id}",
            control_points=points,
            width_m=DEFAULT_POWER_LINE_WIDTH_M,
            elevation_z=DEFAULT_POWER_LINE_HEIGHT_M,
            samples_per_segment=1,
            name=f"osm_power_line_{osm_id}",
        )

    @staticmethod
    def mesh_for_power_line(
        road: Road,
        *,
        catenary: bool = True,
        sag_ratio: float = DEFAULT_POWER_LINE_SAG_RATIO,
        samples_per_span: int = DEFAULT_CATENARY_SAMPLES_PER_SPAN,
    ) -> Mesh3D:
        """ROADMAP_V8.md Faz 5.6c: `power_line_from_points`'in ürettiği
        `Road` (sabit `elevation_z`, `samples_per_segment=1` — direk
        noktalarının kendisi) burada gerçek bir sarkma eğrisine
        dönüştürülür.

        `catenary=True` (varsayılan, bu fazın kabul kriteri): her direk
        çifti arası (`road.control_points` sırasıyla) `samples_per_span`
        ara noktayla örneklenir, orta noktada en fazla olacak şekilde
        parabolik bir sarkma (`4*t*(1-t)` çan eğrisi) uygulanır — hat
        gözle görülür biçimde direkler arasında sarkar.

        `catenary=False`: eski davranış korunur — `road.to_mesh()`
        (dümdüz, sabit yükseklik, örnekleme yok) döner. Roadmap'in kendi
        kabul kriterinin ikinci yarısı ("düz-çizgi fallback'i performans
        modu için korunuyor") budur — B3 performans kaygısıyla, çok
        sayıda hat aynı sahnede olduğunda çağıran taraf `catenary=False`
        seçebilir.
        """
        if not catenary:
            return road.to_mesh()

        points = road.control_points
        if len(points) < 2:
            return road.to_mesh()

        half_w = road.width_m / 2.0
        base_z = road.elevation_z

        # Direk-arası her açıklığı (span) sarkma eğrisiyle örnekle.
        # Uçlarda (direk noktalarının kendisinde) sarkma sıfır olmalı ki
        # hat gerçekten direğin tepesinden geçiyormuş gibi görünsün.
        sampled: list[tuple[Point2D, float]] = []
        n_spans = len(points) - 1
        for i in range(n_spans):
            p0, p1 = points[i], points[i + 1]
            span_len = ((p1.x - p0.x) ** 2 + (p1.y - p0.y) ** 2) ** 0.5
            sag = span_len * sag_ratio
            steps = max(2, samples_per_span)
            # Son span dışında son örnek atlanır (bir sonraki span'ın
            # ilk örneğiyle çakışmasın diye).
            end = steps if i == n_spans - 1 else steps - 1
            for s in range(0 if i == 0 else 1, end + 1):
                t = s / steps
                x = p0.x + (p1.x - p0.x) * t
                y = p0.y + (p1.y - p0.y) * t
                z = base_z - sag * 4.0 * t * (1.0 - t)
                sampled.append((Point2D(x, y), z))

        if len(sampled) < 2:
            return road.to_mesh()

        left: list[Vertex3D] = []
        right: list[Vertex3D] = []
        for i, (p, z) in enumerate(sampled):
            if i == 0:
                dx = sampled[i + 1][0].x - p.x
                dy = sampled[i + 1][0].y - p.y
            elif i == len(sampled) - 1:
                dx = p.x - sampled[i - 1][0].x
                dy = p.y - sampled[i - 1][0].y
            else:
                dx = sampled[i + 1][0].x - sampled[i - 1][0].x
                dy = sampled[i + 1][0].y - sampled[i - 1][0].y
            length = (dx**2 + dy**2) ** 0.5 or 1.0
            nx, ny = -dy / length, dx / length
            left.append(Vertex3D(p.x + nx * half_w, p.y + ny * half_w, z))
            right.append(Vertex3D(p.x - nx * half_w, p.y - ny * half_w, z))

        vertices = left + right
        n = len(sampled)
        triangles = []
        for i in range(n - 1):
            l0, l1 = i, i + 1
            r0, r1 = n + i, n + i + 1
            triangles.append((l0, r0, l1))
            triangles.append((l1, r0, r1))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=road.name)
        return UVGenerator.planar_mapping(mesh, axis="z")

    @staticmethod
    def substation(item: SubstationItem, height_m: float = DEFAULT_SUBSTATION_HEIGHT_M) -> Mesh3D:
        """`power=substation` (Polygon) -> alçak platform prizması
        (mevcut `MeshBuilder.extrude_polygon`, değiştirilmedi)."""
        return MeshBuilder.extrude_polygon(
            item.polygon,
            base_z=0.0,
            height=height_m,
            name="power_substation",
        )

    @staticmethod
    def communication_tower(item: CommunicationTowerItem) -> Mesh3D:
        """Kafes kule yaklaşıklığı: taban geniş, tepeye doğru daralan 3
        silindir segmenti + iki anten kutusu (B1: "baz istasyonu" için
        mimari kesinlik iddiası taşımayan, siluet düzeyinde bir
        yaklaşıklama - `religious_structures` modülündeki aynı kabul
        edilmiş basitleştirme ilkesi)."""
        x, y, gz = item.position.x, item.position.y, item.ground_z
        h = item.height_m
        segment_h = h / 3.0
        base = MeshBuilder.build_cylinder(
            radius=0.6,
            height=segment_h,
            center_x=x,
            center_y=y,
            base_z=gz,
            segments=8,
            name="tower_segment_base",
        )
        mid = MeshBuilder.build_cylinder(
            radius=0.35,
            height=segment_h,
            center_x=x,
            center_y=y,
            base_z=gz + segment_h,
            segments=8,
            name="tower_segment_mid",
        )
        top = MeshBuilder.build_cylinder(
            radius=0.15,
            height=segment_h,
            center_x=x,
            center_y=y,
            base_z=gz + 2 * segment_h,
            segments=8,
            name="tower_segment_top",
        )
        antenna_a = MeshBuilder.build_box(
            0.15,
            0.6,
            1.0,
            center_x=x - 0.3,
            center_y=y,
            base_z=gz + h - 0.5,
            name="tower_antenna_a",
        )
        antenna_b = MeshBuilder.build_box(
            0.15,
            0.6,
            1.0,
            center_x=x + 0.3,
            center_y=y,
            base_z=gz + h - 0.5,
            name="tower_antenna_b",
        )
        return MeshMerger.merge(
            [base, mid, top, antenna_a, antenna_b],
            name="communication_tower",
        )


# ROADMAP_V7.md Faz C3 (7. dilim, son B1 dilimi) - OSM kategori köprüsü,
# bu modülün tanımları hazır olduktan sonra en altta import edilir
# (döngüsel importu önlemek için önceki köprülerle aynı desen).
from .osm_bridge import (  # noqa: E402
    generate_power_infrastructure_for_collection,
    infra_item_from_feature,
)

__all__ = [
    "DEFAULT_POWER_LINE_HEIGHT_M",
    "DEFAULT_POWER_LINE_WIDTH_M",
    "DEFAULT_SUBSTATION_HEIGHT_M",
    "SubstationItem",
    "CommunicationTowerItem",
    "PowerInfrastructureGenerator",
    "infra_item_from_feature",
    "generate_power_infrastructure_for_collection",
]
