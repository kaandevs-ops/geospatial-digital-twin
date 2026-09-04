"""
Infrastructure — Bridges, Water Surfaces, Landscape Details
=============================================================

ROADMAP_V5 — M2.5 (kalan madde): "Ulaşım yapıları (köprü, üst geçit),
su yüzeyleri (deniz/göl/nehir), peyzaj elemanları (kaldırım/bordür/yaya
geçidi/otopark çizgileri)."

`street_furniture/__init__.py`'daki mobilya (lamba/bank/duraklar) zaten
tamamlandı (önceki oturum); bu modül aynı `vegetation`/`street_furniture`
deseninin devamı olarak roadmap M2.5'in kalan üç kategorisini ekler:

1. `BridgeGenerator` — bir yol/nehir güzergâhı (polyline) üzerinden köprü
   tabliyesi (deck) + korkuluk + ayak (pier) üretimi. `mobility` modülünün
   yol ağı verisiyle (yol merkez hattı polyline'ı) doğrudan beslenebilir.
2. `WaterSurfaceGenerator` — düz (opsiyonel çok köşeli) su yüzeyi mesh'i;
   `material_engine`'de ayrı bir su malzemesi yoksa bile en azından
   geometrik olarak sahneye yerleştirilebilir bir düzlem üretir.
3. `LandscapeDetailGenerator` — kaldırım şeridi (yol kenarı boyunca ince
   yükseltilmiş şerit), bordür, yaya geçidi çizgileri (zebra), otopark
   çizgileri — hepsi ince (birkaç cm) extrude edilmiş dikdörtgenler.

Tamamen stdlib-only; yalnızca `mesh_engine` primitiflerini (box/extrude)
kullanır, harici asset/model dosyası yoktur — projenin genel ilkesiyle
tutarlı.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core_engine.geometry_engine import Point2D, Polygon
from ..mesh_engine import Mesh3D, MeshBuilder, MeshMerger, UVGenerator


# ============================================================================ #
# Köprü (Bridge)
# ============================================================================ #

@dataclass(slots=True)
class BridgeSpec:
    path: list[Point2D]
    deck_width_m: float = 8.0
    deck_thickness_m: float = 0.6
    deck_z: float = 0.0
    railing_height_m: float = 1.1
    pier_spacing_m: float = 15.0
    pier_radius_m: float = 0.6
    pier_ground_z: float = -6.0  # köprü altındaki zemin/su seviyesi


class BridgeGenerator:
    """Roadmap M2.5: 'Köprü (OSM `bridge=yes`), üst geçit, tünel ağzı -
    `mobility` modülündeki yol ağı verisiyle doğrudan bağlanabilir, ayrı
    mesh üretim mantığı gerektirir (yol kesitinin ekstrüzyonu + ayak/kolon
    üretimi).'

    `path` (merkez hattı polyline, `mobility.pathfinding`'in ürettiği yol
    segmentleriyle aynı format: sıralı `Point2D` listesi) verilir; tabliye
    bu hat boyunca sabit genişlikte bir şerit (offset sol/sağ kenar) olarak
    extrude edilir. Bu yaklaşım `MeshBuilder.extrude_polygon`'un zaten
    desteklediği "polygon -> prizma" mantığını tekrar kullanır (tabliye
    kesiti = yol şeridi genişliğinde ince bir dikdörtgen zincirinin
    birleşimi), yeni bir extrusion algoritması icat etmez.
    """

    @staticmethod
    def _deck_footprint(path: list[Point2D], width_m: float) -> Polygon:
        """Bir merkez hattı polyline'ını sabit genişlikte bir şerit
        poligonuna çevirir (sol kenar ileri, sağ kenar geri) — yol/tabliye
        kesitleri için standart bir yöntem."""
        if len(path) < 2:
            raise ValueError("Köprü güzergahı en az 2 noktadan oluşmalı.")
        half_w = width_m / 2.0
        left: list[Point2D] = []
        right: list[Point2D] = []
        n = len(path)
        for i in range(n):
            prev_pt = path[i - 1] if i > 0 else path[i]
            next_pt = path[i + 1] if i < n - 1 else path[i]
            dx, dy = next_pt.x - prev_pt.x, next_pt.y - prev_pt.y
            length = math.hypot(dx, dy) or 1e-6
            # Normal vektör (yön vektörüne dik).
            nx, ny = -dy / length, dx / length
            p = path[i]
            left.append(Point2D(p.x + nx * half_w, p.y + ny * half_w))
            right.append(Point2D(p.x - nx * half_w, p.y - ny * half_w))
        ring = left + list(reversed(right))
        return Polygon(ring)

    @staticmethod
    def generate(spec: BridgeSpec) -> Mesh3D:
        """`spec.path` boyunca tabliye + korkuluk + belirli aralıklarla
        ayak (pier) üretir. Tabliye tek bir extrude edilmiş prizma
        (kesit = tam güzergah şeridi), korkuluklar iki kenar boyunca ince
        duvar, ayaklar ise `pier_spacing_m` aralıklarla `deck_z` ile
        `pier_ground_z` arasına inen silindirlerdir."""
        deck_footprint = BridgeGenerator._deck_footprint(spec.path, spec.deck_width_m)
        deck_mesh = MeshBuilder.extrude_polygon(
            deck_footprint, spec.deck_z - spec.deck_thickness_m, spec.deck_thickness_m,
            name="bridge_deck",
        )
        parts = [deck_mesh]

        # Korkuluklar: sol ve sağ kenar boyunca ince, düşük duvar.
        half_w = spec.deck_width_m / 2.0
        n = len(spec.path)
        for side_sign, side_name in ((1, "left"), (-1, "right")):
            for i in range(n - 1):
                a, b = spec.path[i], spec.path[i + 1]
                dx, dy = b.x - a.x, b.y - a.y
                length = math.hypot(dx, dy) or 1e-6
                nx, ny = -dy / length, dx / length
                ra = Point2D(a.x + nx * half_w * side_sign, a.y + ny * half_w * side_sign)
                rb = Point2D(b.x + nx * half_w * side_sign, b.y + ny * half_w * side_sign)
                seg_len = ra.distance_to(rb)
                mx, my = (ra.x + rb.x) / 2.0, (ra.y + rb.y) / 2.0
                angle = math.atan2(rb.y - ra.y, rb.x - ra.x)
                railing = MeshBuilder.build_box(
                    width=seg_len, depth=0.08, height=spec.railing_height_m,
                    center_x=0.0, center_y=0.0, base_z=spec.deck_z,
                    name=f"bridge_railing_{side_name}_{i}",
                )
                railing = _rotate_translate_mesh_xy(railing, angle, mx, my)
                parts.append(railing)

        # Ayaklar: güzergah uzunluğu boyunca `pier_spacing_m` aralıklarla.
        total_length = sum(
            spec.path[i].distance_to(spec.path[i + 1]) for i in range(n - 1)
        )
        if total_length > 1e-6 and spec.pier_spacing_m > 0:
            n_piers = max(2, int(total_length / spec.pier_spacing_m) + 1)
            pier_height = max(0.1, spec.deck_z - spec.deck_thickness_m - spec.pier_ground_z)
            for k in range(n_piers):
                t = k / (n_piers - 1) if n_piers > 1 else 0.0
                pos = _point_along_path(spec.path, t)
                pier = MeshBuilder.build_cylinder(
                    radius=spec.pier_radius_m, height=pier_height,
                    center_x=pos.x, center_y=pos.y, base_z=spec.pier_ground_z,
                    segments=10, name=f"bridge_pier_{k}",
                )
                parts.append(pier)

        merged = MeshMerger.merge(parts, name="bridge")
        # ROADMAP_V7.md'nin son "Kalan" maddesi: bu üretim yolu (extrude_polygon/
        # build_box/build_cylinder tabanlı) daha önce hiç UV atamıyordu -
        # `UVGenerator.box_mapping` (mevcut, değiştirilmedi) her üçgeni kendi
        # baskın normaline göre projekte ederek tabliye/korkuluk/ayak
        # yüzeylerine gerçek doku koordinatı kazandırır.
        return UVGenerator.box_mapping(merged)


def _point_along_path(path: list[Point2D], t: float) -> Point2D:
    """`t` in [0, 1] -> polyline üzerinde orantısal ilerlemedeki nokta."""
    t = min(1.0, max(0.0, t))
    n = len(path)
    if n == 1:
        return path[0]
    lengths = [path[i].distance_to(path[i + 1]) for i in range(n - 1)]
    total = sum(lengths) or 1e-6
    target = t * total
    accum = 0.0
    for i, seg_len in enumerate(lengths):
        if accum + seg_len >= target or i == n - 2:
            local_t = (target - accum) / seg_len if seg_len > 1e-9 else 0.0
            local_t = min(1.0, max(0.0, local_t))
            a, b = path[i], path[i + 1]
            return Point2D(a.x + (b.x - a.x) * local_t, a.y + (b.y - a.y) * local_t)
        accum += seg_len
    return path[-1]


def _rotate_translate_mesh_xy(mesh: Mesh3D, angle_rad: float, cx: float, cy: float) -> Mesh3D:
    """Eksen-hizalı üretilmiş bir mesh'i (merkezi 0,0 varsayılan) verilen
    açıyla döndürüp `(cx, cy)`'ye taşır. `MeshBuilder.build_box`'ın her
    zaman eksen-hizalı üretmesi nedeniyle köprü korkuluğu gibi yol yönüne
    hizalanması gereken elemanlar için kullanılır (aynı desen
    `building_elements.DoubleSkinFacadeGenerator._rotate_mesh_xy` ile)."""
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
    new_vertices = []
    for v in mesh.vertices:
        rx = v.x * cos_a - v.y * sin_a
        ry = v.x * sin_a + v.y * cos_a
        new_normal = v.normal
        if v.normal is not None:
            nx, ny = v.normal[0], v.normal[1]
            new_normal = (nx * cos_a - ny * sin_a, nx * sin_a + ny * cos_a, v.normal[2])
        new_vertices.append(type(v)(cx + rx, cy + ry, v.z, normal=new_normal, tangent=v.tangent, uv=v.uv))
    return Mesh3D(vertices=new_vertices, triangles=list(mesh.triangles), uvs=list(mesh.uvs), name=mesh.name)


# ============================================================================ #
# Tünel Duvarı (Tunnel Wall) — ROADMAP_V8 Faz 6.2 tamamlama maddesi
# ============================================================================ #

@dataclass(slots=True)
class TunnelSpec:
    """`BridgeSpec`'in tünel karşılığı: `path` boyunca çukur (cut-and-cover
    tarzı) bir tünel — zemin seviyesinden `road_z`'ye inen iki yan duvar
    + taban döşemesi. Gerçek bir bindirilmiş tavan (kapalı tüp) yerine
    açık üstten görünüm tercih edildi (V7 notu: "tünel ağzı" - sahne
    içi kamera/inceleme senaryosunda kapalı tüp iç mekanı görünmez
    kılardı; bu, `BridgeGenerator`'ın "üstten görünür" felsefesiyle
    tutarlı bilinçli bir tasarım kararı)."""

    path: list[Point2D]
    road_width_m: float = 6.0
    wall_thickness_m: float = 0.3
    wall_height_above_road_m: float = 1.2  # duvar, yol tabanından ne kadar yükseğe çıkar
    road_z: float = -4.0                    # DEFAULT_TUNNEL_DEPTH_M ile tutarlı
    ground_z: float = 0.0
    floor_thickness_m: float = 0.2


class TunnelGenerator:
    """ROADMAP_V8 Faz 6.2 — 'gerçek köprü ayağı/tünel duvarı geometrisi
    bilinçli olarak üretilmedi' notunun tünel tarafını kapatır.
    `BridgeGenerator._deck_footprint` ile aynı 'merkez hattı -> sabit
    genişlikte şerit' yöntemini, taban döşemesi ve iki yan duvar için
    tekrar kullanır (yeni bir extrusion algoritması icat edilmez)."""

    @staticmethod
    def generate(spec: TunnelSpec) -> Mesh3D:
        if len(spec.path) < 2:
            raise ValueError("Tünel güzergahı en az 2 noktadan oluşmalı.")

        # Taban döşemesi (yol genişliğinde, road_z hizasında ince prizma).
        floor_footprint = BridgeGenerator._deck_footprint(spec.path, spec.road_width_m)
        floor_mesh = MeshBuilder.extrude_polygon(
            floor_footprint, spec.road_z - spec.floor_thickness_m, spec.floor_thickness_m,
            name="tunnel_floor",
        )
        parts = [floor_mesh]

        # Yan duvarlar: zemin seviyesinden `wall_height_above_road_m` kadar
        # yukarıya çıkan, yol tabanından zemine kadar uzanan iki paralel
        # ince duvar (BridgeGenerator korkuluk mantığıyla aynı desende,
        # ama tam yükseklikli ve daha kalın).
        half_w = spec.road_width_m / 2.0
        wall_top_z = spec.ground_z + spec.wall_height_above_road_m
        wall_height = wall_top_z - spec.road_z
        n = len(spec.path)
        for side_sign, side_name in ((1, "left"), (-1, "right")):
            for i in range(n - 1):
                a, b = spec.path[i], spec.path[i + 1]
                dx, dy = b.x - a.x, b.y - a.y
                length = math.hypot(dx, dy) or 1e-6
                nx, ny = -dy / length, dx / length
                offset = half_w + spec.wall_thickness_m / 2.0
                ra = Point2D(a.x + nx * offset * side_sign, a.y + ny * offset * side_sign)
                rb = Point2D(b.x + nx * offset * side_sign, b.y + ny * offset * side_sign)
                seg_len = ra.distance_to(rb)
                mx, my = (ra.x + rb.x) / 2.0, (ra.y + rb.y) / 2.0
                angle = math.atan2(rb.y - ra.y, rb.x - ra.x)
                wall = MeshBuilder.build_box(
                    width=seg_len, depth=spec.wall_thickness_m, height=wall_height,
                    center_x=0.0, center_y=0.0, base_z=spec.road_z,
                    name=f"tunnel_wall_{side_name}_{i}",
                )
                wall = _rotate_translate_mesh_xy(wall, angle, mx, my)
                parts.append(wall)

        merged = MeshMerger.merge(parts, name="tunnel")
        # BridgeGenerator.generate ile aynı gerekçe: bu üretim yolu daha
        # önce hiç UV atamıyordu, box_mapping ile gerçek doku koordinatı
        # kazandırılır (duvar/taban malzemesi material_engine'de ayrı
        # atanabilsin diye).
        return UVGenerator.box_mapping(merged)


# ============================================================================ #
# Su Yüzeyleri (Water Surfaces)
# ============================================================================ #

class WaterSurfaceGenerator:
    """Roadmap M2.5: 'Su yüzeyleri: deniz/göl/nehir düzlemleri, basit
    dalga/yansıma shader'ı (görsel gerçekçilik için düşük maliyetli
    yüksek etki).'

    Bu ortamda gerçek zamanlı shader üretilemez (stdlib-only, GPU/shader
    hattı yok) — bu yüzden kabul kriterinin karşılanabilir kısmı ele
    alınır: geometrik su yüzeyi mesh'i, `material_engine.PBRMaterial`
    ile işaretlenebilecek düşük-pürüzlülük/yüksek-yansıtıcılık malzeme
    varsayılanı. Dalga animasyonu (gerçek zamanlı vertex displacement)
    dürüstçe **kapsam dışı** bırakılmıştır (bu, gerçek zamanlı bir render
    motoru/GPU gerektirir; roadmap'in kendi "shader" ifadesi bu ortamda
    tam karşılanamaz).
    """

    @staticmethod
    def polygon_surface(polygon: Polygon, z: float = 0.0, name: str = "water_surface") -> Mesh3D:
        """Verilen (göl/nehir kıyı şeridi gibi) bir poligonun içini kaplayan
        düz su yüzeyi. `MeshBuilder.extrude_polygon`'un ear-clipping
        triangulation'ını sıfır-yükseklikte kullanarak (yalnızca üst yüz)
        tek katmanlı bir düzlem üretir."""
        # Sıfıra çok yakın (ama pozitif) yükseklikle extrude edip yalnızca
        # üst kapağı almak yerine, doğrudan aynı ear-clipping algoritmasını
        # (MeshBuilder içindeki `_ear_clip_triangulate`) tekrar kullanmak
        # için extrude_polygon'u minimal kalınlıkla çağırıp üst yarıyı
        # almak yerine burada basitçe ince bir "levha" (iki taraflı, çok
        # ince) üretiyoruz — tek taraflı su yüzeyi render'da genelde
        # yeterlidir ve mevcut extrude_polygon fonksiyonu zaten iki taraf
        # (üst+alt) üretiyor, ekstra kod gerektirmez.
        thin = 0.02
        mesh = MeshBuilder.extrude_polygon(polygon, z - thin / 2.0, thin, name=name)
        # bkz. BridgeGenerator.generate'daki UV notu - aynı gerekçeyle burada da
        # su yüzeyine gerçek (düzlemsel, yukarıdan bakış) UV atanır.
        return UVGenerator.planar_mapping(mesh, axis="z")

    @staticmethod
    def rectangular_surface(
        center: Point2D, width_m: float, depth_m: float, z: float = 0.0,
        name: str = "water_surface",
    ) -> Mesh3D:
        """Basit dikdörtgen su yüzeyi (deniz/göl için hızlı bbox yaklaşımı)."""
        hw, hd = width_m / 2.0, depth_m / 2.0
        ring = [
            Point2D(center.x - hw, center.y - hd), Point2D(center.x + hw, center.y - hd),
            Point2D(center.x + hw, center.y + hd), Point2D(center.x - hw, center.y + hd),
        ]
        return WaterSurfaceGenerator.polygon_surface(Polygon(ring), z=z, name=name)


# ============================================================================ #
# Peyzaj Detayları (Sidewalk / Curb / Crosswalk / Parking Lines)
# ============================================================================ #

class LandscapeDetailGenerator:
    """Roadmap M2.5: 'Peyzaj elemanları: kaldırım, bordür, yaya geçidi
    çizgileri, otopark çizgileri - küçük ama şehir sahnesinin "boş"
    hissini gideren detaylar.'

    Hepsi bir yol merkez hattı (polyline) veya nokta etrafında üretilen
    ince (birkaç cm) extrude edilmiş şeritlerdir — `BridgeGenerator.
    _deck_footprint`'teki "polyline -> sabit genişlikte şerit" yöntemi
    burada da (daha ince ve daha alçak versiyonlarıyla) tekrar kullanılır.
    """

    @staticmethod
    def sidewalk_strip(
        path: list[Point2D], width_m: float = 2.0, thickness_m: float = 0.12,
        z: float = 0.0, offset_from_road_m: float = 0.0, name: str = "sidewalk",
    ) -> Mesh3D:
        """Yol kenarı boyunca kaldırım şeridi. `offset_from_road_m`,
        yolun merkez hattından ne kadar uzağa (yol kenarına) yerleştirmek
        için kullanılır — çağıran taraf yolun kenar hattını path olarak
        verirse 0 bırakılabilir."""
        footprint = BridgeGenerator._deck_footprint(path, width_m)
        if offset_from_road_m:
            footprint = Polygon([
                Point2D(p.x, p.y + offset_from_road_m) for p in footprint.closed_ring()[:-1]
            ])
        return UVGenerator.planar_mapping(
            MeshBuilder.extrude_polygon(footprint, z, thickness_m, name=name), axis="z"
        )

    @staticmethod
    def curb_strip(
        path: list[Point2D], height_m: float = 0.15, width_m: float = 0.15,
        z: float = 0.0, name: str = "curb",
    ) -> Mesh3D:
        """Bordür — kaldırımdan biraz daha yüksek, dar bir şerit."""
        footprint = BridgeGenerator._deck_footprint(path, width_m)
        return UVGenerator.planar_mapping(
            MeshBuilder.extrude_polygon(footprint, z, height_m, name=name), axis="z"
        )

    @staticmethod
    def crosswalk_stripes(
        center: Point2D, direction_deg: float, road_width_m: float,
        stripe_count: int = 6, stripe_width_m: float = 0.4, stripe_length_m: float = 0.5,
        gap_m: float = 0.4, z: float = 0.02, name_prefix: str = "crosswalk",
    ) -> Mesh3D:
        """Yaya geçidi (zebra) çizgileri — yol genişliği boyunca, hareket
        yönüne dik sıralanmış ince şeritler."""
        theta = math.radians(direction_deg)
        along_x, along_y = math.cos(theta), math.sin(theta)   # yol yönü
        perp_x, perp_y = -along_y, along_x                     # yol genişliği yönü

        stripes: list[Mesh3D] = []
        total_span = stripe_count * stripe_width_m + (stripe_count - 1) * gap_m
        start_offset = -total_span / 2.0
        for i in range(stripe_count):
            offset = start_offset + i * (stripe_width_m + gap_m) + stripe_width_m / 2.0
            sx = center.x + perp_x * offset
            sy = center.y + perp_y * offset
            box = MeshBuilder.build_box(
                width=stripe_length_m, depth=stripe_width_m, height=0.02,
                center_x=0.0, center_y=0.0, base_z=z, name=f"{name_prefix}_{i}",
            )
            box = _rotate_translate_mesh_xy(box, theta, sx, sy)
            stripes.append(box)
        return UVGenerator.box_mapping(MeshMerger.merge(stripes, name=name_prefix))

    @staticmethod
    def parking_lines(
        origin: Point2D, direction_deg: float, stall_count: int,
        stall_width_m: float = 2.5, stall_depth_m: float = 5.0,
        line_thickness_m: float = 0.08, z: float = 0.02, name_prefix: str = "parking_line",
    ) -> Mesh3D:
        """Otopark çizgileri — art arda dizilmiş park yeri ayraç
        çizgileri (her ayraç, park yeri derinliği kadar uzunlukta ince
        bir şerit)."""
        theta = math.radians(direction_deg)
        along_x, along_y = math.cos(theta), math.sin(theta)

        lines: list[Mesh3D] = []
        for i in range(stall_count + 1):
            offset = i * stall_width_m
            lx = origin.x + along_x * offset
            ly = origin.y + along_y * offset
            box = MeshBuilder.build_box(
                width=line_thickness_m, depth=stall_depth_m, height=0.02,
                center_x=0.0, center_y=0.0, base_z=z, name=f"{name_prefix}_{i}",
            )
            box = _rotate_translate_mesh_xy(box, theta + math.pi / 2.0, lx, ly)
            lines.append(box)
        return UVGenerator.box_mapping(MeshMerger.merge(lines, name="parking_lines"))
