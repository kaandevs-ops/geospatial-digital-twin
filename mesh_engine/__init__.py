"""
Mesh Engine
============

Roadmap Phase 2 - "Mesh Engine" (tamamen procedural).

Kapsam:
    Mesh Builder, Mesh Optimizer, Mesh Simplifier, Mesh Splitter, Mesh Merger,
    Mesh Repair, Vertex Optimizer, UV Generator, Normal Generator,
    Tangent Generator.

Girdi: Phase 1 `Polygon` (core_engine.geometry_engine) + yükseklik parametresi.
Çıktı: `Mesh3D` - sonraki tüm fazların (Building Reconstruction, Digital Twin,
Visualization, Export ...) ortak 3D veri sözleşmesi.

Bağımlılık: yalnızca stdlib (roadmap'in "harici CAD/oyun motoru yok" ilkesi).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core_engine.geometry_engine import GeometryEngine, Point2D, Polygon

# ======================================================================== #
# Temel tipler
# ======================================================================== #


@dataclass(slots=True)
class Vertex3D:
    x: float
    y: float
    z: float
    normal: tuple[float, float, float] | None = None
    tangent: tuple[float, float, float, float] | None = None  # xyz + handedness (w)
    uv: tuple[float, float] | None = None
    # ROADMAP_V8 Faz 6.4 (UV Atlas + AO Baking): vertex başına önceden
    # hesaplanmış ambient occlusion katsayısı (0=tam kapalı, 1=tam açık).
    # Varsayılan 1.0 (AO uygulanmamış gibi davranır) - opt-in, geriye dönük
    # tam uyumlu; mevcut hiçbir Vertex3D(...) çağrısı bu alanı vermiyor.
    ao: float = 1.0

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def distance_to(self, other: Vertex3D) -> float:
        return math.sqrt(
            (self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2
        )


Triangle = tuple[int, int, int]  # vertex index üçlüsü


@dataclass(slots=True)
class Mesh3D:
    """Tüm 3D geometrinin ortak temsili: indexed triangle mesh."""

    vertices: list[Vertex3D] = field(default_factory=list)
    triangles: list[Triangle] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    name: str = "mesh"

    # -- yardımcılar --------------------------------------------------- #
    def vertex_count(self) -> int:
        return len(self.vertices)

    def triangle_count(self) -> int:
        return len(self.triangles)

    def triangle_positions(self, tri: Triangle) -> tuple[Vertex3D, Vertex3D, Vertex3D]:
        i, j, k = tri
        return self.vertices[i], self.vertices[j], self.vertices[k]

    def bounding_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        if not self.vertices:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
        xs = [v.x for v in self.vertices]
        ys = [v.y for v in self.vertices]
        zs = [v.z for v in self.vertices]
        return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))

    def clone(self) -> Mesh3D:
        return Mesh3D(
            vertices=[Vertex3D(v.x, v.y, v.z, v.normal, v.tangent, v.uv) for v in self.vertices],
            triangles=list(self.triangles),
            uvs=list(self.uvs),
            name=self.name,
        )

    def surface_area(self) -> float:
        total = 0.0
        for tri in self.triangles:
            a, b, c = self.triangle_positions(tri)
            total += _triangle_area(a, b, c)
        return total

    def volume(self) -> float:
        """İşaretli tetrahedron toplamı ile hacim (mesh kapalı/manifold olmalı)."""
        total = 0.0
        for i, j, k in self.triangles:
            a, b, c = self.vertices[i], self.vertices[j], self.vertices[k]
            total += (
                a.x * (b.y * c.z - c.y * b.z)
                - a.y * (b.x * c.z - c.x * b.z)
                + a.z * (b.x * c.y - c.x * b.y)
            )
        return abs(total) / 6.0


def _cross(
    a: tuple[float, float, float], b: tuple[float, float, float]
) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _sub(a: Vertex3D, b: Vertex3D) -> tuple[float, float, float]:
    return (a.x - b.x, a.y - b.y, a.z - b.z)


def _length(v: tuple[float, float, float]) -> float:
    return math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)


def _normalize(v: tuple[float, float, float]) -> tuple[float, float, float]:
    length = _length(v)
    if length < 1e-12:
        return (0.0, 0.0, 0.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def _triangle_area(a: Vertex3D, b: Vertex3D, c: Vertex3D) -> float:
    cr = _cross(_sub(b, a), _sub(c, a))
    return _length(cr) / 2.0


def _triangle_normal(a: Vertex3D, b: Vertex3D, c: Vertex3D) -> tuple[float, float, float]:
    return _normalize(_cross(_sub(b, a), _sub(c, a)))


# ======================================================================== #
# Mesh Builder - polygon -> prizma extrusion
# ======================================================================== #


class MeshBuilder:
    """Roadmap: 'Mesh Builder'. Polygon + yükseklik -> Mesh3D (prizma extrusion)."""

    @staticmethod
    def extrude_polygon(
        polygon: Polygon, base_z: float, height: float, name: str = "extrusion"
    ) -> Mesh3D:
        """Bir 2D polygon'u dikey olarak yükselterek (taban + tavan + yan
        duvarlar) kapalı bir prizma mesh'ine çevirir. Polygon konveks olmak
        zorunda değildir - taban/tavan fan triangulation yerine ear-clipping
        ile üçgenlenir (konkav destekli)."""
        ring = polygon.closed_ring()[:-1]  # son nokta ilkiyle aynıysa at
        if len(ring) < 3:
            raise ValueError("Extrusion için en az 3 köşeli bir polygon gerekir.")

        # CCW garantile (taban normali +Z, tavan normali +Z ile tutarlı yön için)
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))

        n = len(ring)
        vertices: list[Vertex3D] = []
        # Taban (z = base_z) ve tavan (z = base_z + height) köşe noktaları
        for p in ring:
            vertices.append(Vertex3D(p.x, p.y, base_z))
        for p in ring:
            vertices.append(Vertex3D(p.x, p.y, base_z + height))

        triangles: list[Triangle] = []

        # Yan duvarlar: her kenar için 2 üçgen (quad)
        for i in range(n):
            i2 = (i + 1) % n
            bl, br = i, i2  # taban-sol, taban-sağ
            tl, tr = i + n, i2 + n  # tavan-sol, tavan-sağ
            triangles.append((bl, br, tr))
            triangles.append((bl, tr, tl))

        # Taban ve tavan: ear-clipping triangulation
        base_indices = list(range(n))
        cap_tris = _ear_clip_triangulate(ring, base_indices)
        for a, b, c in cap_tris:
            # taban: normal -Z olacak şekilde ters çevir
            triangles.append((a, c, b))
        for a, b, c in cap_tris:
            triangles.append((a + n, b + n, c + n))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        # ROADMAP_V7 Faz C4 - son "Kalan" madde: bina/yol/su gibi
        # extrude_polygon tabanlı üretim yollarının UV koordinatı
        # taşımaması (impostor'lar dışında hiçbir mesh dokulanamıyordu).
        # UVGenerator.box_mapping (mevcut, değiştirilmedi) her üçgenin
        # baskın normal eksenine göre planar izdüşüm seçtiği için
        # duvar/taban/tavan için ayrı, doğal bir mapping üretir -
        # ek bir UV-unwrap algoritmasına gerek kalmadan.
        mesh = UVGenerator.box_mapping(mesh)
        return mesh

    @staticmethod
    def build_box(
        width: float,
        depth: float,
        height: float,
        center_x: float = 0.0,
        center_y: float = 0.0,
        base_z: float = 0.0,
        name: str = "box",
    ) -> Mesh3D:
        """Eksen hizalı kutu (mobilya/donatı/basamak için temel primitif)."""
        hw, hd = width / 2.0, depth / 2.0
        ring = [
            Point2D(center_x - hw, center_y - hd),
            Point2D(center_x + hw, center_y - hd),
            Point2D(center_x + hw, center_y + hd),
            Point2D(center_x - hw, center_y + hd),
        ]
        return MeshBuilder.extrude_polygon(Polygon(ring), base_z, height, name=name)

    @staticmethod
    def build_cylinder(
        radius: float,
        height: float,
        center_x: float = 0.0,
        center_y: float = 0.0,
        base_z: float = 0.0,
        segments: int = 12,
        name: str = "cylinder",
    ) -> Mesh3D:
        """Basit silindir (yangın söndürücü tüpü, sütun, vb. donatılar için)."""
        segments = max(6, segments)
        ring = [
            Point2D(
                center_x + radius * math.cos(2 * math.pi * i / segments),
                center_y + radius * math.sin(2 * math.pi * i / segments),
            )
            for i in range(segments)
        ]
        return MeshBuilder.extrude_polygon(Polygon(ring), base_z, height, name=name)

    @staticmethod
    def build_flat_quad(width: float, depth: float, z: float = 0.0, name: str = "quad") -> Mesh3D:
        """Basit düzlem (zemin/su yüzeyi vb. için)."""
        hw, hd = width / 2.0, depth / 2.0
        verts = [
            Vertex3D(-hw, -hd, z, uv=(0.0, 0.0)),
            Vertex3D(hw, -hd, z, uv=(1.0, 0.0)),
            Vertex3D(hw, hd, z, uv=(1.0, 1.0)),
            Vertex3D(-hw, hd, z, uv=(0.0, 1.0)),
        ]
        tris = [(0, 1, 2), (0, 2, 3)]
        mesh = Mesh3D(vertices=verts, triangles=tris, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def build_cone(
        radius: float,
        height: float,
        center_x: float = 0.0,
        center_y: float = 0.0,
        base_z: float = 0.0,
        segments: int = 12,
        name: str = "cone",
    ) -> Mesh3D:
        """Koni (minare külahı, çatı sivrisi vb. için düşük-poly primitif).
        Taban dairesi + tek bir tepe (apex) noktası; `MeshBuilder.
        extrude_polygon`'daki taban/tavan kapak yönlendirme kuralıyla
        tutarlı (taban normali -Z olacak şekilde ters çevrilir)."""
        segments = max(6, segments)
        base_ring = [
            Point2D(
                center_x + radius * math.cos(2 * math.pi * i / segments),
                center_y + radius * math.sin(2 * math.pi * i / segments),
            )
            for i in range(segments)
        ]
        vertices = [Vertex3D(p.x, p.y, base_z) for p in base_ring]
        apex_idx = len(vertices)
        vertices.append(Vertex3D(center_x, center_y, base_z + height))

        triangles: list[Triangle] = []
        for i in range(segments):
            i2 = (i + 1) % segments
            triangles.append((i, i2, apex_idx))
        # Taban kapağı (normal -Z olacak şekilde fan triangulation)
        for i in range(1, segments - 1):
            triangles.append((0, i + 1, i))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def build_dome(
        radius: float,
        height: float | None = None,
        center_x: float = 0.0,
        center_y: float = 0.0,
        base_z: float = 0.0,
        segments: int = 12,
        rings: int = 6,
        name: str = "dome",
    ) -> Mesh3D:
        """Kubbe (dini yapı siluet elemanı) — enlem bantlı yarım küre
        yaklaşıklaması. `height` verilmezse `radius`'a eşit alınır (tam
        yarım küre); farklı verilirse kubbe dikey olarak sıkıştırılıp/
        uzatılabilir (gerçek kubbe profilleri nadiren tam yarım küredir,
        bu basit ama parametrik bir yaklaşım - B1'in "basit parametrik
        ekler" ifadesiyle tutarlı, botanik/mimari hassasiyet iddiası
        taşımaz)."""
        if height is None:
            height = radius
        segments = max(6, segments)
        rings = max(2, rings)

        vertices: list[Vertex3D] = []
        ring_indices: list[list[int]] = []
        for j in range(rings):
            theta = (math.pi / 2.0) * (j / rings)
            r = radius * math.cos(theta)
            z = base_z + height * math.sin(theta)
            idxs = []
            for i in range(segments):
                phi = 2 * math.pi * i / segments
                vertices.append(
                    Vertex3D(center_x + r * math.cos(phi), center_y + r * math.sin(phi), z)
                )
                idxs.append(len(vertices) - 1)
            ring_indices.append(idxs)
        apex_idx = len(vertices)
        vertices.append(Vertex3D(center_x, center_y, base_z + height))

        triangles: list[Triangle] = []
        for j in range(rings - 1):
            cur, nxt = ring_indices[j], ring_indices[j + 1]
            for i in range(segments):
                i2 = (i + 1) % segments
                triangles.append((cur[i], cur[i2], nxt[i2]))
                triangles.append((cur[i], nxt[i2], nxt[i]))
        last = ring_indices[-1]
        for i in range(segments):
            i2 = (i + 1) % segments
            triangles.append((last[i], last[i2], apex_idx))
        # Taban kapağı (ekvator halkası, normal -Z)
        base_ring_idx = ring_indices[0]
        for i in range(1, segments - 1):
            triangles.append((base_ring_idx[0], base_ring_idx[i + 1], base_ring_idx[i]))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh


@dataclass(slots=True)
class WallOpening:
    """Bir duvar segmenti üzerinde kesilecek dikdörtgen boşluk (pencere/kapı).

    u_start/u_end: duvar başlangıcından (a noktası) itibaren yatay mesafe (m).
    v_start/v_end: taban_z'den itibaren dikey mesafe (m, sill/lento).
    """

    u_start: float
    u_end: float
    v_start: float
    v_end: float
    kind: str = "window"  # "window" | "door"


class WallOpeningMeshBuilder:
    """Roadmap Faz 1: pencere/kapı boşluklu duvar paneli mesh'i.

    Önceden `FacadeGenerator.generate()` tüm footprint'i tek seferde extrude
    ediyordu (küp/prizma sonucu) ve pencereler yalnızca uyumluluk kontrolü
    (window/wall ratio) için hesaplanıyordu, mesh'e hiç işlenmiyordu. Bu
    builder tek bir duvar segmentini (a->b, taban_z, yükseklik) düzlemsel bir
    "delikli panel" olarak, verilen dikdörtgen boşlukları (pencere/kapı)
    gerçekten keserek üçgenler. Yöntem: duvar yerel (u boyunca kenar, v
    dikey) düzleminde, tüm boşluk kenar koordinatlarından bir grid çıkarılır;
    boşluk içine düşen hücreler atlanır, diğerleri quad (2 üçgen) olarak
    eklenir. Basit ama sağlam bir "polygon-with-rectangular-holes"
    triangulasyonu.
    """

    @staticmethod
    def build_wall_segment(
        a: Point2D,
        b: Point2D,
        base_z: float,
        height: float,
        openings: list[WallOpening] | None = None,
        thickness: float = 0.0,
        name: str = "wall_segment",
    ) -> Mesh3D:
        length = a.distance_to(b)
        if length < 1e-9 or height <= 0:
            return Mesh3D(name=name)

        openings = openings or []
        dx, dy = (b.x - a.x) / length, (b.y - a.y) / length

        us = {0.0, length}
        vs = {0.0, height}
        for op in openings:
            u0 = max(0.0, min(length, op.u_start))
            u1 = max(0.0, min(length, op.u_end))
            v0 = max(0.0, min(height, op.v_start))
            v1 = max(0.0, min(height, op.v_end))
            if u1 - u0 < 1e-6 or v1 - v0 < 1e-6:
                continue
            us.add(u0)
            us.add(u1)
            vs.add(v0)
            vs.add(v1)

        # ROADMAP_V8 doğrulama turu — dejenere (sıfır alanlı) üçgen düzeltmesi:
        # birden çok açıklığın kenar koordinatları (kayan nokta yuvarlaması
        # nedeniyle) birbirine 1e-6'dan daha yakın ama tam eşit olmayan `u`/`v`
        # değerleri üretebiliyordu (örn. bitişik iki pencerenin kenarları çok
        # yakın konumlandığında) — bu, `u_list`/`v_list`'te neredeyse-sıfır
        # genişlikte bir grid hücresine, dolayısıyla neredeyse-sıfır alanlı
        # (ama `in_opening` testini geçen) üçgenlere yol açıyordu. Sıralı
        # listedeki birbirine `1e-6`'dan yakın değerler tek bir değere
        # birleştirilerek bu sliver hücreler baştan elenir.
        def _dedup_sorted(values: set[float], eps: float = 1e-6) -> list[float]:
            ordered = sorted(values)
            merged: list[float] = [ordered[0]] if ordered else []
            for v in ordered[1:]:
                if v - merged[-1] < eps:
                    continue
                merged.append(v)
            return merged

        u_list = _dedup_sorted(us)
        v_list = _dedup_sorted(vs)

        def in_opening(uc: float, vc: float) -> bool:
            for op in openings:
                if op.u_start <= uc <= op.u_end and op.v_start <= vc <= op.v_end:
                    return True
            return False

        def outer_mesh() -> Mesh3D:
            verts: list[Vertex3D] = []
            index_of: dict[tuple[int, int], int] = {}

            def vidx(iu: int, iv: int) -> int:
                key = (iu, iv)
                if key in index_of:
                    return index_of[key]
                u, v = u_list[iu], v_list[iv]
                x = a.x + dx * u
                y = a.y + dy * u
                z = base_z + v
                vi = len(verts)
                verts.append(
                    Vertex3D(
                        x, y, z, uv=(u / length if length else 0.0, v / height if height else 0.0)
                    )
                )
                index_of[key] = vi
                return vi

            tris: list[Triangle] = []
            for iu in range(len(u_list) - 1):
                for iv in range(len(v_list) - 1):
                    uc = (u_list[iu] + u_list[iu + 1]) / 2.0
                    vc = (v_list[iv] + v_list[iv + 1]) / 2.0
                    if in_opening(uc, vc):
                        continue
                    bl = vidx(iu, iv)
                    br = vidx(iu + 1, iv)
                    tr = vidx(iu + 1, iv + 1)
                    tl = vidx(iu, iv + 1)
                    tris.append((bl, br, tr))
                    tris.append((bl, tr, tl))
            mesh = Mesh3D(vertices=verts, triangles=tris, name=name)
            NormalGenerator.compute_face_averaged_normals(mesh)
            return mesh

        panel = outer_mesh()
        if thickness <= 0:
            return panel

        inward = (-dy * thickness, dx * thickness)
        inner_a = Point2D(a.x + inward[0], a.y + inward[1])
        inner_b = Point2D(b.x + inward[0], b.y + inward[1])
        inner_panel = WallOpeningMeshBuilder.build_wall_segment(
            inner_a,
            inner_b,
            base_z,
            height,
            openings,
            thickness=0.0,
            name=name + "_inner",
        )
        inner_panel.triangles = [(c, b_, a_) for (a_, b_, c) in inner_panel.triangles]
        merged = MeshMerger.merge([panel, inner_panel], name=name)
        NormalGenerator.compute_face_averaged_normals(merged)
        return merged


class FloorPlateBuilder:
    """Roadmap Faz 1: her kat için ince bir döşeme (slab) plakası - kat
    ayrımının mesh'te görsel/fiziksel olarak temsil edilmesi için."""

    @staticmethod
    def build(
        polygon: Polygon, z: float, slab_thickness: float = 0.25, name: str = "floor_plate"
    ) -> Mesh3D:
        return MeshBuilder.extrude_polygon(polygon, z - slab_thickness, slab_thickness, name=name)


class FacadeElementMeshBuilder:
    """Roadmap Faz 1.3: cephe zenginleştirme elemanları (balkon, çıkma/bay
    window, giriş sundurması) için gerçek 3D mesh üretimi.

    Ortak desen: bir duvar kenarı (a->b) + o kenarın dışa bakan normali
    boyunca `MeshBuilder.build_box` ile prizmatik bir hacim + (balkon için)
    basit dikey çubuklardan oluşan bir korkuluk (railing) üretilir. Bu,
    projenin "yalnızca stdlib, harici CAD motoru yok" ilkesine uygun,
    roof_generator'daki "ayrı hacim ekleme" (add_chimney/add_skylight)
    yaklaşımıyla tutarlıdır.
    """

    @staticmethod
    def _outward_normal(a: Point2D, b: Point2D) -> tuple[float, float]:
        length = a.distance_to(b)
        if length < 1e-9:
            return (0.0, 1.0)
        dx, dy = (b.x - a.x) / length, (b.y - a.y) / length
        # Polygon CCW kabul edilir -> dış normal (dy, -dx)
        return (dy, -dx)

    @staticmethod
    def build_balcony(
        wall_a: Point2D,
        wall_b: Point2D,
        position: Point2D,
        width: float,
        depth: float,
        base_z: float,
        slab_thickness: float = 0.15,
        railing_height: float = 1.0,
        railing_bar_count: int = 8,
        name: str = "balcony",
    ) -> Mesh3D:
        """Balkon plakası + basit dikey çubuklu korkuluk. `position`,
        balkonun duvara bitiştiği orta noktasıdır (bkz.
        `building_elements.Balcony.position`, pencere konumundan türetilir)."""
        nx, ny = FacadeElementMeshBuilder._outward_normal(wall_a, wall_b)
        cx, cy = position.x + nx * depth / 2.0, position.y + ny * depth / 2.0
        # Duvara paralel eksende genişlik, normal eksende derinlik olacak
        # şekilde döndürülmüş bir kutu -> local ring ile inşa edilir.
        length = wall_a.distance_to(wall_b)
        tx, ty = (
            ((wall_b.x - wall_a.x) / length, (wall_b.y - wall_a.y) / length)
            if length > 1e-9
            else (1.0, 0.0)
        )
        hw, hd = width / 2.0, depth / 2.0
        corners = [(-hw, 0.0), (hw, 0.0), (hw, hd), (-hw, hd)]
        ring = [
            Point2D(position.x + tx * ux + nx * vy, position.y + ty * ux + ny * vy)
            for (ux, vy) in corners
        ]
        slab = MeshBuilder.extrude_polygon(
            Polygon(ring), base_z, slab_thickness, name=f"{name}_slab"
        )

        parts = [slab]
        if railing_height > 0 and railing_bar_count > 0:
            bar_radius = 0.02
            outer_edge = [ring[1], ring[2], ring[3]]  # dış üç kenar (duvara bitişen kenar hariç)
            edge_pts: list[Point2D] = [ring[0]] + outer_edge
            perimeter_pts: list[Point2D] = []
            for i in range(len(edge_pts) - 1):
                p0, p1 = edge_pts[i], edge_pts[i + 1]
                seg_len = p0.distance_to(p1)
                n_bars = max(1, int(seg_len / max(0.3, seg_len / railing_bar_count)))
                for k in range(n_bars + 1):
                    t = k / n_bars if n_bars else 0.0
                    perimeter_pts.append(
                        Point2D(p0.x + (p1.x - p0.x) * t, p0.y + (p1.y - p0.y) * t)
                    )
            for pt in perimeter_pts:
                parts.append(
                    MeshBuilder.build_cylinder(
                        bar_radius,
                        railing_height,
                        center_x=pt.x,
                        center_y=pt.y,
                        base_z=base_z + slab_thickness,
                        segments=6,
                        name=f"{name}_bar",
                    )
                )
        merged = MeshMerger.merge(parts, name=name)
        NormalGenerator.compute_face_averaged_normals(merged)
        return merged

    @staticmethod
    def build_bay_window(
        wall_a: Point2D,
        wall_b: Point2D,
        position: Point2D,
        side_width: float,
        protrusion: float,
        base_z: float,
        height: float,
        name: str = "bay_window",
    ) -> Mesh3D:
        """Cepheden dışa taşan trapez-kesitli (basitleştirilmiş: dikdörtgen
        kesitli) bir çıkma hacmi. Duvardaki gerçek pencere boşluğunu kesmek
        `WallOpeningMeshBuilder`'ın işi olarak kalır; bu yalnızca dışarı
        taşan ek hacmi üretir."""
        nx, ny = FacadeElementMeshBuilder._outward_normal(wall_a, wall_b)
        length = wall_a.distance_to(wall_b)
        tx, ty = (
            ((wall_b.x - wall_a.x) / length, (wall_b.y - wall_a.y) / length)
            if length > 1e-9
            else (1.0, 0.0)
        )
        hw = side_width / 2.0
        corners = [(-hw, 0.0), (hw, 0.0), (hw, protrusion), (-hw, protrusion)]
        ring = [
            Point2D(position.x + tx * ux + nx * vy, position.y + ty * ux + ny * vy)
            for (ux, vy) in corners
        ]
        return MeshBuilder.extrude_polygon(Polygon(ring), base_z, height, name=name)

    @staticmethod
    def build_entrance_canopy(
        wall_a: Point2D,
        wall_b: Point2D,
        position: Point2D,
        width: float,
        depth: float,
        base_z: float,
        thickness: float = 0.15,
        bracket_count: int = 2,
        name: str = "canopy",
    ) -> Mesh3D:
        """Giriş kapısının üzerine, duvardan dışarı taşan yatay bir
        sundurma plakası + basit destek konsolları (bracket)."""
        nx, ny = FacadeElementMeshBuilder._outward_normal(wall_a, wall_b)
        length = wall_a.distance_to(wall_b)
        tx, ty = (
            ((wall_b.x - wall_a.x) / length, (wall_b.y - wall_a.y) / length)
            if length > 1e-9
            else (1.0, 0.0)
        )
        hw = width / 2.0
        corners = [(-hw, 0.0), (hw, 0.0), (hw, depth), (-hw, depth)]
        ring = [
            Point2D(position.x + tx * ux + nx * vy, position.y + ty * ux + ny * vy)
            for (ux, vy) in corners
        ]
        slab = MeshBuilder.extrude_polygon(Polygon(ring), base_z, thickness, name=f"{name}_slab")
        parts = [slab]
        bracket_count = max(1, bracket_count)
        bracket_drop = 0.25  # konsol, plakadan aşağı doğru sarkan basit destek
        for i in range(bracket_count):
            t = 0.5 if bracket_count == 1 else i / (bracket_count - 1)
            offset = (t - 0.5) * (width - 0.3)
            bx = position.x + tx * offset + nx * depth / 2.0
            by = position.y + ty * offset + ny * depth / 2.0
            parts.append(
                MeshBuilder.build_box(
                    0.08,
                    depth * 0.8,
                    bracket_drop,
                    center_x=bx,
                    center_y=by,
                    base_z=base_z - bracket_drop,
                    name=f"{name}_bracket",
                )
            )
        merged = MeshMerger.merge(parts, name=name)
        NormalGenerator.compute_face_averaged_normals(merged)
        return merged


def _ear_clip_triangulate(ring: list[Point2D], indices: list[int]) -> list[Triangle]:
    """Basit ear-clipping (O(n^2)) - küçük/orta bina footprint'leri için yeterli."""
    idx = indices[:]
    triangles: list[Triangle] = []
    guard = 0
    while len(idx) > 3 and guard < 10_000:
        guard += 1
        ear_found = False
        for k in range(len(idx)):
            i_prev = idx[k - 1]
            i_curr = idx[k]
            i_next = idx[(k + 1) % len(idx)]
            pa = ring[_pos(indices, i_prev)]
            pb = ring[_pos(indices, i_curr)]
            pc = ring[_pos(indices, i_next)]
            if not _is_convex(pa, pb, pc):
                continue
            if _any_point_inside_triangle(ring, idx, i_prev, i_curr, i_next, pa, pb, pc):
                continue
            triangles.append((i_prev, i_curr, i_next))
            idx.remove(i_curr)
            ear_found = True
            break
        if not ear_found:
            # dejenere/self-intersecting durum: fan triangulation'a düş
            break
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            triangles.append((idx[0], idx[k], idx[k + 1]))
    return triangles


def _pos(indices: list[int], value: int) -> int:
    return indices.index(value)


def _is_convex(a: Point2D, b: Point2D, c: Point2D) -> bool:
    cross = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
    return cross > 0


def _any_point_inside_triangle(
    ring: list[Point2D],
    idx: list[int],
    i_prev: int,
    i_curr: int,
    i_next: int,
    a: Point2D,
    b: Point2D,
    c: Point2D,
) -> bool:
    for other in idx:
        if other in (i_prev, i_curr, i_next):
            continue
        p = ring[other]
        if _point_in_triangle(p, a, b, c):
            return True
    return False


def _point_in_triangle(p: Point2D, a: Point2D, b: Point2D, c: Point2D) -> bool:
    def sign(p1: Point2D, p2: Point2D, p3: Point2D) -> float:
        return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)

    d1 = sign(p, a, b)
    d2 = sign(p, b, c)
    d3 = sign(p, c, a)
    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not (has_neg and has_pos)


# ======================================================================== #
# Mesh Optimizer - vertex welding
# ======================================================================== #


class MeshOptimizer:
    """Roadmap: 'Mesh Optimizer' / 'Vertex Optimizer'. Çakışan (aynı konumdaki)
    vertex'leri birleştirir (welding) - render/export öncesi mesh boyutunu
    küçültür ve normal hesaplarını tutarlı hale getirir."""

    @staticmethod
    def weld_vertices(mesh: Mesh3D, tolerance: float = 1e-6) -> Mesh3D:
        key_to_index: dict[tuple[int, int, int], int] = {}
        new_vertices: list[Vertex3D] = []
        remap: list[int] = []

        def quantize(v: Vertex3D) -> tuple[int, int, int]:
            return (
                round(v.x / tolerance),
                round(v.y / tolerance),
                round(v.z / tolerance),
            )

        for v in mesh.vertices:
            key = quantize(v)
            if key in key_to_index:
                remap.append(key_to_index[key])
            else:
                new_index = len(new_vertices)
                key_to_index[key] = new_index
                new_vertices.append(v)
                remap.append(new_index)

        new_triangles = []
        for a, b, c in mesh.triangles:
            na, nb, nc = remap[a], remap[b], remap[c]
            if na == nb or nb == nc or na == nc:
                continue  # dejenere üçgeni at
            new_triangles.append((na, nb, nc))

        return Mesh3D(vertices=new_vertices, triangles=new_triangles, name=mesh.name)


# ======================================================================== #
# Mesh Simplifier - Garland-Heckbert (1997) Quadric Error Metric
# ======================================================================== #
#
# ROADMAP V3 - Faz D1. Önceki versiyon (bkz. `_shortest_edge`/`_collapse_edge`
# altında, artık yalnızca `simplify_edgelength_legacy()` karşılaştırma amaçlı
# saklanıyor) yalnızca kenar uzunluğunu maliyet fonksiyonu olarak kullanıyordu;
# bu, düzlemsellik/eğrilikten bağımsız olduğu için düz yüzeylerde gereksiz
# delinmeye, keskin hatlarda (çatı mahya çizgisi, köşe) siluet bozulmasına yol
# açabiliyordu. Bu versiyon her vertex için komşu üçgenlerin düzlem
# denklemlerinden gerçek 4x4 quadric hata matrisini biriktirir (Garland &
# Heckbert, "Surface Simplification Using Quadric Error Metrics", SIGGRAPH
# 1997) ve her aday kenar birleşimi için o matrise göre optimal birleşim
# noktasını ve hatasını hesaplayıp en düşük maliyetli kenarı seçer.

# Simetrik 4x4 quadric matrisinin üst-üçgensel 10 bağımsız bileşeni için
# (satır, sütun) indeks çiftleri - (a,b,c,d) düzlem katsayıları üzerinden.
_QUADRIC_INDEX_PAIRS: tuple[tuple[int, int], ...] = (
    (0, 0),
    (0, 1),
    (0, 2),
    (0, 3),
    (1, 1),
    (1, 2),
    (1, 3),
    (2, 2),
    (2, 3),
    (3, 3),
)

Quadric = tuple[float, float, float, float, float, float, float, float, float, float]
_ZERO_QUADRIC: Quadric = (0.0,) * 10


def _add_quadrics(q1: Quadric, q2: Quadric) -> Quadric:
    return tuple(a + b for a, b in zip(q1, q2))  # type: ignore[return-value]


def _triangle_plane(
    v0: Vertex3D, v1: Vertex3D, v2: Vertex3D
) -> tuple[float, float, float, float, float] | None:
    """Üçgenin birim-normalli düzlem denklemini (a,b,c,d) ve alanını döner.
    Dejenere (sıfır alanlı) üçgenler için None."""
    ux, uy, uz = v1.x - v0.x, v1.y - v0.y, v1.z - v0.z
    wx, wy, wz = v2.x - v0.x, v2.y - v0.y, v2.z - v0.z
    nx = uy * wz - uz * wy
    ny = uz * wx - ux * wz
    nz = ux * wy - uy * wx
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return None
    area = length * 0.5
    nx, ny, nz = nx / length, ny / length, nz / length
    d = -(nx * v0.x + ny * v0.y + nz * v0.z)
    return (nx, ny, nz, d, area)


def _plane_quadric(a: float, b: float, c: float, d: float, weight: float) -> Quadric:
    p = (a, b, c, d)
    return tuple(weight * p[i] * p[j] for (i, j) in _QUADRIC_INDEX_PAIRS)  # type: ignore[return-value]


def _build_vertex_quadrics(mesh: Mesh3D) -> list[Quadric]:
    """Her vertex için komşu üçgen düzlemlerinden (alan ağırlıklı) biriken
    quadric hata matrisi - Garland-Heckbert Kp toplamı."""
    quadrics: list[Quadric] = [_ZERO_QUADRIC] * len(mesh.vertices)
    for ia, ib, ic in mesh.triangles:
        plane = _triangle_plane(mesh.vertices[ia], mesh.vertices[ib], mesh.vertices[ic])
        if plane is None:
            continue
        a, b, c, d, area = plane
        kp = _plane_quadric(a, b, c, d, area)
        for vi in (ia, ib, ic):
            quadrics[vi] = _add_quadrics(quadrics[vi], kp)
    return quadrics


def _quadric_error(q: Quadric, x: float, y: float, z: float) -> float:
    q_aa, q_ab, q_ac, q_ad, q_bb, q_bc, q_bd, q_cc, q_cd, q_dd = q
    return (
        q_aa * x * x
        + 2 * q_ab * x * y
        + 2 * q_ac * x * z
        + 2 * q_ad * x
        + q_bb * y * y
        + 2 * q_bc * y * z
        + 2 * q_bd * y
        + q_cc * z * z
        + 2 * q_cd * z
        + q_dd
    )


def _optimal_collapse_position(
    q: Quadric,
    v1: Vertex3D,
    v2: Vertex3D,
) -> tuple[float, float, float]:
    """Birleşik quadric matrisini minimize eden noktayı bulur (üst-sol 3x3
    alt matrisin lineer sistemini Cramer kuralıyla çözerek). Matris tekil
    (singular) ise - düzlemsel/dejenere durumlar - uç noktalar ve orta nokta
    arasından en düşük hatalıyı seçen fallback'e döner."""
    q_aa, q_ab, q_ac, q_ad, q_bb, q_bc, q_bd, q_cc, q_cd, q_dd = q
    a11, a12, a13 = q_aa, q_ab, q_ac
    a21, a22, a23 = q_ab, q_bb, q_bc
    a31, a32, a33 = q_ac, q_bc, q_cc
    b1, b2, b3 = -q_ad, -q_bd, -q_cd

    det = (
        a11 * (a22 * a33 - a23 * a32)
        - a12 * (a21 * a33 - a23 * a31)
        + a13 * (a21 * a32 - a22 * a31)
    )
    if abs(det) > 1e-9:
        det_x = (
            b1 * (a22 * a33 - a23 * a32) - a12 * (b2 * a33 - a23 * b3) + a13 * (b2 * a32 - a22 * b3)
        )
        det_y = (
            a11 * (b2 * a33 - a23 * b3) - b1 * (a21 * a33 - a23 * a31) + a13 * (a21 * b3 - b2 * a31)
        )
        det_z = (
            a11 * (a22 * b3 - b2 * a32) - a12 * (a21 * b3 - b2 * a31) + b1 * (a21 * a32 - a22 * a31)
        )
        return (det_x / det, det_y / det, det_z / det)

    # Fallback: tekil matris (örn. düzlemsel bölge) - uç noktalar/orta nokta
    # arasından quadric hatası en düşük olanı seç (Garland-Heckbert makalesinde
    # önerilen standart fallback).
    midpoint = ((v1.x + v2.x) / 2.0, (v1.y + v2.y) / 2.0, (v1.z + v2.z) / 2.0)
    candidates = ((v1.x, v1.y, v1.z), (v2.x, v2.y, v2.z), midpoint)
    return min(candidates, key=lambda p: _quadric_error(q, *p))


def _best_qem_collapse(
    mesh: Mesh3D,
) -> tuple[tuple[int, int], tuple[float, float, float], float] | None:
    """Mevcut mesh'teki tüm benzersiz kenarlar arasından en düşük quadric-hata
    maliyetli birleşimi (kenar, optimal_nokta, maliyet) olarak döner."""
    if not mesh.triangles:
        return None
    quadrics = _build_vertex_quadrics(mesh)
    seen: set[tuple[int, int]] = set()
    best_key: tuple[int, int] | None = None
    best_pos = (0.0, 0.0, 0.0)
    best_cost = math.inf
    for a, b, c in mesh.triangles:
        for i, j in ((a, b), (b, c), (c, a)):
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            seen.add(key)
            q = _add_quadrics(quadrics[i], quadrics[j])
            pos = _optimal_collapse_position(q, mesh.vertices[i], mesh.vertices[j])
            cost = _quadric_error(q, *pos)
            if cost < best_cost:
                best_cost = cost
                best_key = key
                best_pos = pos
    if best_key is None:
        return None
    return best_key, best_pos, best_cost


def _collapse_edge_to(mesh: Mesh3D, i: int, j: int, position: tuple[float, float, float]) -> Mesh3D:
    x, y, z = position
    merged = Vertex3D(x, y, z)
    new_vertices = mesh.vertices[:]
    new_vertices[i] = merged

    new_triangles: list[Triangle] = []
    for a, b, c in mesh.triangles:
        tri = tuple(i if v == j else v for v in (a, b, c))
        if len(set(tri)) < 3:
            continue  # dejenere (bu kenarı içeren üçgen çöktü)
        new_triangles.append(tri)  # type: ignore[arg-type]

    return MeshOptimizer.weld_vertices(
        Mesh3D(vertices=new_vertices, triangles=new_triangles, name=mesh.name),
        tolerance=1e-9,
    )


class MeshSimplifier:
    """Roadmap: 'Mesh Simplifier'. Garland-Heckbert (1997) Quadric Error
    Metric tabanlı edge-collapse decimation: her vertex için komşu üçgen
    düzlemlerinden biriken 4x4 quadric hata matrisi hesaplanır, her aday kenar
    birleşimi bu matrise göre puanlanır (birleşim maliyeti = optimal noktadaki
    quadric hatası) ve en düşük maliyetli kenar öncelikli olarak birleştirilir.
    Bu, yalnızca kenar uzunluğuna bakan saf bir yaklaşıma göre düz bölgelerde
    gereksiz delinmeyi engeller ve keskin/eğri yüzey detaylarını (çatı mahya
    çizgisi, köşe) daha uzun süre korur."""

    @staticmethod
    def simplify(mesh: Mesh3D, target_triangle_ratio: float) -> Mesh3D:
        """target_triangle_ratio: 0 < r <= 1, örn. 0.5 -> üçgen sayısını yarıya indir."""
        if not (0 < target_triangle_ratio <= 1.0):
            raise ValueError("target_triangle_ratio 0 ile 1 arasında olmalı.")
        working = mesh.clone()
        target_count = max(1, int(len(working.triangles) * target_triangle_ratio))

        guard = 0
        while len(working.triangles) > target_count and guard < 50_000:
            guard += 1
            collapse = _best_qem_collapse(working)
            if collapse is None:
                break
            (i, j), position, _cost = collapse
            working = _collapse_edge_to(working, i, j, position)
        return working

    @staticmethod
    def simplify_edgelength_legacy(mesh: Mesh3D, target_triangle_ratio: float) -> Mesh3D:
        """Faz D1 öncesi kullanılan, yalnızca en kısa kenarı birleştiren
        basitleştirilmiş yöntem. Artık üretim kodunda kullanılmıyor; yalnızca
        `tests/test_meshsimplifier_qem_vs_edgelength.py` içindeki karşılaştırma
        regresyon testi için saklanıyor (roadmap'in kabul kriteri: yeni QEM
        yönteminin bu yönteme göre ölçülebilir şekilde daha iyi olduğunu
        göstermek)."""
        if not (0 < target_triangle_ratio <= 1.0):
            raise ValueError("target_triangle_ratio 0 ile 1 arasında olmalı.")
        working = mesh.clone()
        target_count = max(1, int(len(working.triangles) * target_triangle_ratio))

        guard = 0
        while len(working.triangles) > target_count and guard < 50_000:
            guard += 1
            edge = _shortest_edge(working)
            if edge is None:
                break
            working = _collapse_edge(working, edge)
        return working


def _shortest_edge(mesh: Mesh3D) -> tuple[int, int] | None:
    best, best_len = None, math.inf
    seen: set[tuple[int, int]] = set()
    for a, b, c in mesh.triangles:
        for i, j in ((a, b), (b, c), (c, a)):
            key = (min(i, j), max(i, j))
            if key in seen:
                continue
            seen.add(key)
            d = mesh.vertices[i].distance_to(mesh.vertices[j])
            if d < best_len:
                best_len, best = d, key
    return best


def _collapse_edge(mesh: Mesh3D, edge: tuple[int, int]) -> Mesh3D:
    i, j = edge
    vi, vj = mesh.vertices[i], mesh.vertices[j]
    merged = Vertex3D(
        (vi.x + vj.x) / 2.0,
        (vi.y + vj.y) / 2.0,
        (vi.z + vj.z) / 2.0,
    )
    new_vertices = mesh.vertices[:]
    new_vertices[i] = merged

    new_triangles: list[Triangle] = []
    for a, b, c in mesh.triangles:
        tri = tuple(i if v == j else v for v in (a, b, c))
        if len(set(tri)) < 3:
            continue  # dejenere (bu kenarı içeren üçgen çöktü)
        new_triangles.append(tri)  # type: ignore[arg-type]

    return MeshOptimizer.weld_vertices(
        Mesh3D(vertices=new_vertices, triangles=new_triangles, name=mesh.name),
        tolerance=1e-9,
    )


# ======================================================================== #
# Mesh Splitter / Merger
# ======================================================================== #


class MeshSplitter:
    """Roadmap: 'Mesh Splitter'. Bir mesh'i bağlı bileşenlerine (connected
    components) ayırır - ör. tek bir sahne mesh'inden ayrı binaları çıkarmak."""

    @staticmethod
    def split_by_connectivity(mesh: Mesh3D) -> list[Mesh3D]:
        adjacency: dict[int, set[int]] = {i: set() for i in range(len(mesh.vertices))}
        for a, b, c in mesh.triangles:
            adjacency[a].update((b, c))
            adjacency[b].update((a, c))
            adjacency[c].update((a, b))

        visited = [False] * len(mesh.vertices)
        components: list[set[int]] = []
        for start in range(len(mesh.vertices)):
            if visited[start]:
                continue
            stack = [start]
            comp: set[int] = set()
            while stack:
                node = stack.pop()
                if visited[node]:
                    continue
                visited[node] = True
                comp.add(node)
                stack.extend(adjacency[node] - comp)
            components.append(comp)

        result = []
        for comp in components:
            comp_indices = sorted(comp)
            remap = {old: new for new, old in enumerate(comp_indices)}
            sub_vertices = [mesh.vertices[i] for i in comp_indices]
            sub_triangles = [
                (remap[a], remap[b], remap[c])
                for (a, b, c) in mesh.triangles
                if a in comp and b in comp and c in comp
            ]
            result.append(Mesh3D(vertices=sub_vertices, triangles=sub_triangles, name=mesh.name))
        return result

    @staticmethod
    def split_by_plane(mesh: Mesh3D, plane_z: float) -> tuple[Mesh3D, Mesh3D]:
        """Roadmap 'Section View' (Phase 9) için de kullanılan temel araç:
        mesh'i verilen yatay Z düzlemine göre alt/üst olarak ikiye böler
        (üçgen bazlı, kesişen üçgenler üst tarafa dahil edilir - basit ama
        pratik bir yaklaşım)."""
        below, above = MeshSplitter.split_by_axis_plane(mesh, axis=2, value=plane_z)
        below.name, above.name = f"{mesh.name}_below", f"{mesh.name}_above"
        return below, above

    @staticmethod
    def split_by_axis_plane(mesh: Mesh3D, *, axis: int, value: float) -> tuple[Mesh3D, Mesh3D]:
        """`split_by_plane`'in eksen-genel hali (Roadmap V10 Faz 4.3.1:
        grid-tabanlı ön-parçalama için X/Y eksenlerinde de bölme
        gerekiyor). `axis`: 0=X, 1=Y, 2=Z. Davranış `split_by_plane` ile
        birebir aynı (üçgen ağırlık-merkezi eşiğe göre iki tarafa
        dağıtılır) - yeni bir algoritma değil, aynı yöntemin
        genellemesi."""
        below = Mesh3D(name=f"{mesh.name}_lo")
        above = Mesh3D(name=f"{mesh.name}_hi")

        def add_tri(target: Mesh3D, verts: tuple[Vertex3D, Vertex3D, Vertex3D]) -> None:
            base = len(target.vertices)
            target.vertices.extend(verts)
            target.triangles.append((base, base + 1, base + 2))

        def coord(v: Vertex3D) -> float:
            return (v.x, v.y, v.z)[axis]

        for tri in mesh.triangles:
            a, b, c = mesh.triangle_positions(tri)
            avg = (coord(a) + coord(b) + coord(c)) / 3.0
            if avg <= value:
                add_tri(below, (a, b, c))
            else:
                add_tri(above, (a, b, c))
        return below, above


class MeshMerger:
    """Roadmap: 'Mesh Merger'. Birden fazla mesh'i tek bir mesh'te birleştirir."""

    @staticmethod
    def merge(meshes: list[Mesh3D], name: str = "merged") -> Mesh3D:
        merged = Mesh3D(name=name)
        for m in meshes:
            offset = len(merged.vertices)
            merged.vertices.extend(m.vertices)
            merged.triangles.extend(
                (a + offset, b + offset, c + offset) for (a, b, c) in m.triangles
            )
        return merged


# ======================================================================== #
# Mesh Repair
# ======================================================================== #


class MeshRepair:
    """Roadmap: 'Mesh Repair'. Non-manifold kenarları ve dejenere üçgenleri
    tespit edip temizler; delikleri basit fan-triangulation ile kapatır."""

    @staticmethod
    def remove_degenerate_triangles(mesh: Mesh3D, min_area: float = 1e-9) -> Mesh3D:
        clean_triangles = []
        for tri in mesh.triangles:
            a, b, c = mesh.triangle_positions(tri)
            if _triangle_area(a, b, c) >= min_area:
                clean_triangles.append(tri)
        return Mesh3D(vertices=list(mesh.vertices), triangles=clean_triangles, name=mesh.name)

    @staticmethod
    def find_boundary_edges(mesh: Mesh3D) -> list[tuple[int, int]]:
        """Yalnızca bir üçgene ait olan (yani 'delik' sınırındaki) kenarları bulur."""
        edge_count: dict[tuple[int, int], int] = {}
        for a, b, c in mesh.triangles:
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                edge_count[key] = edge_count.get(key, 0) + 1
        return [edge for edge, count in edge_count.items() if count == 1]

    @staticmethod
    def is_manifold(mesh: Mesh3D) -> bool:
        edge_count: dict[tuple[int, int], int] = {}
        for a, b, c in mesh.triangles:
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                edge_count[key] = edge_count.get(key, 0) + 1
        return all(count <= 2 for count in edge_count.values())

    @staticmethod
    def fill_holes(mesh: Mesh3D) -> Mesh3D:
        """Sınır kenarlarından basit döngüler bulup fan-triangulation ile kapatır."""
        boundary = MeshRepair.find_boundary_edges(mesh)
        if not boundary:
            return mesh.clone()

        adjacency: dict[int, list[int]] = {}
        for a, b in boundary:
            adjacency.setdefault(a, []).append(b)
            adjacency.setdefault(b, []).append(a)

        visited_edges: set[tuple[int, int]] = set()
        result = mesh.clone()
        for start_edge in boundary:
            key = (min(start_edge), max(start_edge))
            if key in visited_edges:
                continue
            loop = _trace_loop(start_edge[0], adjacency, visited_edges)
            if len(loop) >= 3:
                for k in range(1, len(loop) - 1):
                    result.triangles.append((loop[0], loop[k], loop[k + 1]))
        return result

    # ==================================================================== #
    # ROADMAP V5 - Track M / M1.3: Topoloji sağlığı ve onarım (tamamlayıcı)
    # ==================================================================== #

    @staticmethod
    def fix_non_manifold_edges(mesh: Mesh3D) -> Mesh3D:
        """Bir kenarı ikiden fazla üçgen paylaşıyorsa (non-manifold), o
        kenarı paylaşan fazladan üçgenleri kendi izole vertex kopyalarına
        ayırır (vertex-split). Geometri pozisyonları aynı kalır; ayrılan
        parçalar `fill_holes` ile ayrı ayrı kapatılabilir sınır kenarlarına
        kavuşur."""
        edge_count: dict[tuple[int, int], int] = {}
        for a, b, c in mesh.triangles:
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                edge_count[key] = edge_count.get(key, 0) + 1
        bad_edges = {e for e, cnt in edge_count.items() if cnt > 2}
        if not bad_edges:
            return mesh.clone()

        new_vertices = list(mesh.vertices)
        new_triangles: list[Triangle] = []
        seen_extra: dict[tuple[int, int], int] = {}
        for tri in mesh.triangles:
            a, b, c = tri
            needs_split = False
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                if key in bad_edges:
                    seen_extra[key] = seen_extra.get(key, 0) + 1
                    if seen_extra[key] > 2:
                        needs_split = True
            if needs_split:
                remap = {}
                for idx in (a, b, c):
                    v = mesh.vertices[idx]
                    new_idx = len(new_vertices)
                    new_vertices.append(Vertex3D(v.x, v.y, v.z, normal=v.normal, uv=v.uv))
                    remap[idx] = new_idx
                new_triangles.append((remap[a], remap[b], remap[c]))
            else:
                new_triangles.append(tri)
        return Mesh3D(vertices=new_vertices, triangles=new_triangles, name=mesh.name)

    @staticmethod
    def fix_inverted_normals(mesh: Mesh3D) -> Mesh3D:
        """Bağlı bileşen (BFS) gezintisiyle üçgen sarma yönünü (winding
        order) tutarlı hale getirir - bir kısmı dışa, bir kısmı içe bakan
        'ters normal' hatasını düzeltir. Paylaşılan kenar iki komşu üçgende
        de aynı yönde geçiyorsa (yani biri diğerine göre ters sarılmışsa),
        komşu üçgen çevrilir."""
        tri_list = list(mesh.triangles)
        n = len(tri_list)
        if n == 0:
            return mesh.clone()

        edge_to_tris: dict[tuple[int, int], list[int]] = {}
        for ti, (a, b, c) in enumerate(tri_list):
            for i, j in ((a, b), (b, c), (c, a)):
                edge_to_tris.setdefault((min(i, j), max(i, j)), []).append(ti)

        def neighbors(ti: int) -> list[tuple[int, bool]]:
            a, b, c = tri_list[ti]
            result = []
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                for other in edge_to_tris.get(key, []):
                    if other == ti:
                        continue
                    oa, ob, oc = tri_list[other]
                    other_edges = ((oa, ob), (ob, oc), (oc, oa))
                    same_direction = (i, j) in other_edges
                    result.append((other, same_direction))
            return result

        visited = [False] * n
        flipped = list(tri_list)
        for start in range(n):
            if visited[start]:
                continue
            visited[start] = True
            stack = [start]
            while stack:
                cur = stack.pop()
                for other, same_dir in neighbors(cur):
                    if visited[other]:
                        continue
                    visited[other] = True
                    if same_dir:
                        a, b, c = flipped[other]
                        flipped[other] = (a, c, b)
                    stack.append(other)

        return Mesh3D(vertices=list(mesh.vertices), triangles=flipped, name=mesh.name)

    @staticmethod
    def auto_repair(
        mesh: Mesh3D,
        *,
        weld_tolerance: float = 1e-6,
        min_area: float = 1e-9,
        fix_normals: bool = True,
        fill_holes: bool = True,
    ) -> Mesh3D:
        """M1.3 kabul kriteri: `quality_metrics`'in tespit ettiği her hata
        sınıfı için tek çağrıda tam onarım zinciri.

        Sıra: (1) vertex welding, (2) dejenere üçgen temizliği,
        (3) non-manifold kenar onarımı, (4) delik kapama (watertight),
        (5) normal/winding tutarlılığı düzeltmesi.
        """
        repaired = MeshOptimizer.weld_vertices(mesh, tolerance=weld_tolerance)
        repaired = MeshRepair.remove_degenerate_triangles(repaired, min_area=min_area)
        repaired = MeshRepair.fix_non_manifold_edges(repaired)
        if fill_holes:
            repaired = MeshRepair.fill_holes(repaired)
        if fix_normals:
            repaired = MeshRepair.fix_inverted_normals(repaired)
        return repaired


def _trace_loop(
    start: int, adjacency: dict[int, list[int]], visited_edges: set[tuple[int, int]]
) -> list[int]:
    loop = [start]
    current = start
    prev = None
    guard = 0
    while guard < 10_000:
        guard += 1
        neighbors = [n for n in adjacency.get(current, []) if n != prev]
        if not neighbors:
            break
        nxt = neighbors[0]
        edge_key = (min(current, nxt), max(current, nxt))
        if edge_key in visited_edges:
            break
        visited_edges.add(edge_key)
        if nxt == start:
            break
        loop.append(nxt)
        prev, current = current, nxt
    return loop


# ======================================================================== #
# UV Generator
# ======================================================================== #


class UVGenerator:
    """Roadmap: 'UV Generator'. Planar ve box (cube) mapping - texture
    atlas'a ihtiyaç duymadan hızlı, procedural doku koordinatı üretimi."""

    @staticmethod
    def planar_mapping(mesh: Mesh3D, axis: str = "z") -> Mesh3D:
        result = mesh.clone()
        (minx, miny, minz), (maxx, maxy, maxz) = result.bounding_box()
        for v in result.vertices:
            if axis == "z":
                u = (v.x - minx) / (maxx - minx + 1e-9)
                w = (v.y - miny) / (maxy - miny + 1e-9)
            elif axis == "y":
                u = (v.x - minx) / (maxx - minx + 1e-9)
                w = (v.z - minz) / (maxz - minz + 1e-9)
            else:  # "x"
                u = (v.y - miny) / (maxy - miny + 1e-9)
                w = (v.z - minz) / (maxz - minz + 1e-9)
            v.uv = (u, w)
        return result

    @staticmethod
    def box_mapping(mesh: Mesh3D) -> Mesh3D:
        """Her üçgenin baskın normal eksenine göre en uygun planar izdüşümü seçer."""
        result = mesh.clone()
        if not result.vertices[0].normal:
            NormalGenerator.compute_face_averaged_normals(result)
        (minx, miny, minz), (maxx, maxy, maxz) = result.bounding_box()
        for tri in result.triangles:
            a, b, c = result.triangle_positions(tri)
            nx, ny, nz = _triangle_normal(a, b, c)
            ax, ay, az = abs(nx), abs(ny), abs(nz)
            for v in (a, b, c):
                if az >= ax and az >= ay:
                    v.uv = (
                        (v.x - minx) / (maxx - minx + 1e-9),
                        (v.y - miny) / (maxy - miny + 1e-9),
                    )
                elif ay >= ax and ay >= az:
                    v.uv = (
                        (v.x - minx) / (maxx - minx + 1e-9),
                        (v.z - minz) / (maxz - minz + 1e-9),
                    )
                else:
                    v.uv = (
                        (v.y - miny) / (maxy - miny + 1e-9),
                        (v.z - minz) / (maxz - minz + 1e-9),
                    )
        return result


# ======================================================================== #
# Normal / Tangent Generator
# ======================================================================== #


class NormalGenerator:
    """Roadmap: 'Normal Generator'. Face-normal ortalaması (smooth shading)."""

    @staticmethod
    def compute_face_normals(mesh: Mesh3D) -> list[tuple[float, float, float]]:
        return [_triangle_normal(*mesh.triangle_positions(tri)) for tri in mesh.triangles]

    @staticmethod
    def compute_face_averaged_normals(mesh: Mesh3D) -> Mesh3D:
        """Her vertex'e, komşu yüzlerin alan-ağırlıklı normal ortalamasını atar."""
        accum = [(0.0, 0.0, 0.0) for _ in mesh.vertices]
        for tri in mesh.triangles:
            a, b, c = mesh.triangle_positions(tri)
            normal = _triangle_normal(a, b, c)
            area = _triangle_area(a, b, c)
            for idx in tri:
                ax, ay, az = accum[idx]
                accum[idx] = (ax + normal[0] * area, ay + normal[1] * area, az + normal[2] * area)
        for v, acc in zip(mesh.vertices, accum):
            v.normal = _normalize(acc)
        return mesh


class TangentGenerator:
    """Roadmap: 'Tangent Generator' (normal-map tabanlı malzemeler için)."""

    @staticmethod
    def compute_tangents(mesh: Mesh3D) -> Mesh3D:
        if not mesh.vertices or mesh.vertices[0].uv is None:
            mesh = UVGenerator.planar_mapping(mesh)

        accum_t = [(0.0, 0.0, 0.0) for _ in mesh.vertices]
        for i, j, k in mesh.triangles:
            v0, v1, v2 = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
            if v0.uv is None or v1.uv is None or v2.uv is None:
                continue
            edge1 = _sub(v1, v0)
            edge2 = _sub(v2, v0)
            du1, dv1 = v1.uv[0] - v0.uv[0], v1.uv[1] - v0.uv[1]
            du2, dv2 = v2.uv[0] - v0.uv[0], v2.uv[1] - v0.uv[1]
            denom = du1 * dv2 - du2 * dv1
            f = 1.0 / denom if abs(denom) > 1e-12 else 0.0
            tangent = (
                f * (dv2 * edge1[0] - dv1 * edge2[0]),
                f * (dv2 * edge1[1] - dv1 * edge2[1]),
                f * (dv2 * edge1[2] - dv1 * edge2[2]),
            )
            for idx in (i, j, k):
                ax, ay, az = accum_t[idx]
                accum_t[idx] = (ax + tangent[0], ay + tangent[1], az + tangent[2])

        for v, t in zip(mesh.vertices, accum_t):
            tn = _normalize(t)
            handedness = 1.0
            if v.normal is not None:
                # Gram-Schmidt ortogonalizasyonu
                n = v.normal
                dot = tn[0] * n[0] + tn[1] * n[1] + tn[2] * n[2]
                tn = _normalize((tn[0] - n[0] * dot, tn[1] - n[1] * dot, tn[2] - n[2] * dot))
                handedness = (
                    1.0
                    if (_cross(n, tn)[0] * t[0] + _cross(n, tn)[1] * t[1] + _cross(n, tn)[2] * t[2])
                    >= 0
                    else -1.0
                )
            v.tangent = (tn[0], tn[1], tn[2], handedness)
        return mesh
