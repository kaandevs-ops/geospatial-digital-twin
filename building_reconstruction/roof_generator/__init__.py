"""
Roof Generator
==============

Roadmap Phase 3 - "Roof Generator".

Destek: Flat, Hip, Gable, Cross Gable, Mansard, Pyramid, Sawtooth,
Industrial, Modern, Solar, Green.

Her fonksiyon `Footprint.polygon` + eğim parametresinden bir `Mesh3D`
üretir (`mesh_engine` üzerine kuruludur).
"""

from __future__ import annotations

import math
from enum import Enum

from ...core_engine.geometry_engine import GeometryEngine, Point2D, Polygon
from ...mesh_engine import Mesh3D, MeshBuilder, MeshMerger, NormalGenerator, Vertex3D


class RoofType(str, Enum):
    FLAT = "flat"
    HIP = "hip"
    GABLE = "gable"
    CROSS_GABLE = "cross_gable"
    MANSARD = "mansard"
    PYRAMID = "pyramid"
    SAWTOOTH = "sawtooth"
    INDUSTRIAL = "industrial"
    MODERN = "modern"
    SOLAR = "solar"
    GREEN = "green"
    DOME = "dome"
    GLASS_ATRIUM = "glass_atrium"  # ROADMAP_V5 M2.1: cam çatı/atrium (AVM/ofis)


class RoofGenerator:
    """`RoofType` -> `Mesh3D`. Girdi çatı tabanının Z konumu (`base_z`,
    genelde bina yüksekliği) ve eğim/derinlik parametreleridir."""

    # ------------------------------------------------------------------ #
    # Dispatch
    # ------------------------------------------------------------------ #
    @staticmethod
    def generate(
        polygon: Polygon,
        base_z: float,
        roof_type: RoofType | str = RoofType.FLAT,
        pitch_deg: float = 25.0,
        overhang_m: float = 0.4,
        include_soffit: bool = False,
    ) -> Mesh3D:
        if isinstance(roof_type, str):
            roof_type = RoofType(roof_type)

        eave_polygon = RoofGenerator._offset_footprint(polygon, overhang_m)

        dispatch = {
            RoofType.FLAT: RoofGenerator.flat,
            RoofType.HIP: RoofGenerator.hip,
            RoofType.GABLE: RoofGenerator.gable,
            RoofType.CROSS_GABLE: RoofGenerator.cross_gable,
            RoofType.MANSARD: RoofGenerator.mansard,
            RoofType.PYRAMID: RoofGenerator.pyramid,
            RoofType.SAWTOOTH: RoofGenerator.sawtooth,
            RoofType.INDUSTRIAL: RoofGenerator.industrial,
            RoofType.MODERN: RoofGenerator.modern,
            RoofType.SOLAR: RoofGenerator.solar,
            RoofType.GREEN: RoofGenerator.green,
            RoofType.DOME: RoofGenerator.dome,
            RoofType.GLASS_ATRIUM: RoofGenerator.glass_atrium,
        }
        fn = dispatch[roof_type]
        roof_mesh = fn(eave_polygon, base_z, pitch_deg)

        # ROADMAP_V5 M2.1: saçak altı (soffit) - varsayılan davranış
        # değişmesin diye opt-in (`include_soffit=True`); mevcut çağıran
        # kodlar (procedural_generator vb.) hiçbir değişiklik yapmadan
        # eskisi gibi çalışmaya devam eder.
        if include_soffit and overhang_m > 0:
            soffit = RoofDetailGenerator.add_soffit(polygon, eave_polygon, base_z)
            if soffit.triangle_count():
                roof_mesh = MeshMerger.merge([roof_mesh, soffit], name=roof_mesh.name + "_with_soffit")
        return roof_mesh

    @staticmethod
    def _offset_footprint(polygon: Polygon, overhang_m: float) -> Polygon:
        if overhang_m <= 0:
            return polygon
        centroid = RoofGenerator._centroid(polygon)
        ring = polygon.closed_ring()[:-1]
        offset_pts = []
        for p in ring:
            dx, dy = p.x - centroid.x, p.y - centroid.y
            dist = math.hypot(dx, dy) or 1e-6
            offset_pts.append(Point2D(p.x + dx / dist * overhang_m, p.y + dy / dist * overhang_m))
        return Polygon(offset_pts)

    @staticmethod
    def _centroid(polygon: Polygon) -> Point2D:
        ring = polygon.points
        cx = sum(p.x for p in ring) / len(ring)
        cy = sum(p.y for p in ring) / len(ring)
        return Point2D(cx, cy)

    # ------------------------------------------------------------------ #
    # Flat / Industrial / Green / Solar (düz taban prizmaları)
    # ------------------------------------------------------------------ #
    @staticmethod
    def flat(polygon: Polygon, base_z: float, pitch_deg: float = 0.0) -> Mesh3D:
        parapet_h = 0.4
        return MeshBuilder.extrude_polygon(polygon, base_z, parapet_h, name="roof_flat")

    @staticmethod
    def industrial(polygon: Polygon, base_z: float, pitch_deg: float = 5.0) -> Mesh3D:
        """Hafif eğimli tek yönlü (mono-pitch / lean-to) endüstriyel çatı."""
        return RoofGenerator._mono_pitch(polygon, base_z, pitch_deg, name="roof_industrial")

    @staticmethod
    def green(polygon: Polygon, base_z: float, pitch_deg: float = 0.0) -> Mesh3D:
        """Yeşil çatı: düz taban + 0.25m substrat kalınlığı."""
        return MeshBuilder.extrude_polygon(polygon, base_z, 0.25, name="roof_green")

    @staticmethod
    def solar(polygon: Polygon, base_z: float, pitch_deg: float = 15.0) -> Mesh3D:
        """Solar panel eğimine optimize düz tek eğimli çatı (panel yerleşimi
        Phase 4 AIEnvironmentGenerator ile eklenir; burada yalnız taşıyıcı
        çatı geometrisi üretilir)."""
        return RoofGenerator._mono_pitch(polygon, base_z, pitch_deg, name="roof_solar")

    @staticmethod
    def _mono_pitch(polygon: Polygon, base_z: float, pitch_deg: float, name: str) -> Mesh3D:
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        xs = [p.x for p in ring]
        span = (max(xs) - min(xs)) or 1.0
        min_x = min(xs)
        rise = span * math.tan(math.radians(pitch_deg))

        top_verts = [Vertex3D(p.x, p.y, base_z + (p.x - min_x) / span * rise) for p in ring]
        bottom_verts = [Vertex3D(p.x, p.y, base_z) for p in ring]
        n = len(ring)
        vertices = bottom_verts + top_verts
        triangles = []
        for i in range(n):
            i2 = (i + 1) % n
            triangles.append((i, i2, i2 + n))
            triangles.append((i, i2 + n, i + n))
        from ...mesh_engine import _ear_clip_triangulate  # taban/tavan kapakları
        cap = _ear_clip_triangulate(ring, list(range(n)))
        for (a, b, c) in cap:
            triangles.append((a, c, b))  # taban (aşağı bakan normal)
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    # ------------------------------------------------------------------ #
    # Pyramid / Hip (ridge merkeze doğru daralır)
    # ------------------------------------------------------------------ #
    @staticmethod
    def pyramid(polygon: Polygon, base_z: float, pitch_deg: float = 30.0) -> Mesh3D:
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        centroid = RoofGenerator._centroid(Polygon(ring))
        avg_radius = sum(math.hypot(p.x - centroid.x, p.y - centroid.y) for p in ring) / len(ring)
        apex_height = avg_radius * math.tan(math.radians(pitch_deg))

        n = len(ring)
        vertices = [Vertex3D(p.x, p.y, base_z) for p in ring]
        apex_index = n
        vertices.append(Vertex3D(centroid.x, centroid.y, base_z + apex_height))

        triangles = []
        for i in range(n):
            i2 = (i + 1) % n
            triangles.append((i, i2, apex_index))
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="roof_pyramid")
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def dome(
        polygon: Polygon,
        base_z: float,
        pitch_deg: float = 45.0,
        rings: int = 8,
        segments: int = 24,
    ) -> Mesh3D:
        """Kubbeli çatı: footprint'in ortalama yarıçapına oturan bir yarım
        küre (hemisphere) - dini/anıtsal/kubbeli konut varyantları için.

        `pitch_deg` burada kubbenin "diklik" oranını belirler: 45 derece
        klasik yarım küre (yükseklik = yarıçap), daha düşük değerler basık
        (soğan/segment) kubbe, daha yüksek değerler sivri kubbe verir.
        Taban çokgeni düzensizse (kare değilse) kubbe, çokgenin ortalama
        yarıçaplı bir dairesine yaklaşık oturtulur - taban halkası yine de
        gerçek footprint köşelerini kullanır ki duvarla kubbe arasında
        boşluk kalmasın.
        """
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        centroid = RoofGenerator._centroid(Polygon(ring))
        avg_radius = sum(math.hypot(p.x - centroid.x, p.y - centroid.y) for p in ring) / len(ring)
        dome_height = avg_radius * math.tan(math.radians(min(pitch_deg, 89.0)))

        n = max(3, segments)
        m = max(1, rings)

        vertices: list[Vertex3D] = []
        # Taban halkası: gerçek footprint köşeleri (duvar-kubbe boşluğu olmasın).
        vertices.extend(Vertex3D(p.x, p.y, base_z) for p in ring)
        base_ring_count = len(ring)

        # Ara enlem halkaları: idealize edilmiş daire üzerinde, yarım küre
        # profiline göre daralan yarıçap ve yükselen z.
        ring_start_indices = [0] * m
        for ring_idx in range(1, m + 1):
            phi = (math.pi / 2.0) * (ring_idx / m)  # 0 (taban) -> pi/2 (tepe)
            r = avg_radius * math.cos(phi)
            z = base_z + dome_height * math.sin(phi)
            ring_start_indices[ring_idx - 1] = len(vertices)
            if ring_idx == m:
                # Tepe noktası: tek vertex (apex).
                vertices.append(Vertex3D(centroid.x, centroid.y, z))
            else:
                for seg in range(n):
                    theta = 2.0 * math.pi * seg / n
                    vertices.append(Vertex3D(
                        centroid.x + r * math.cos(theta),
                        centroid.y + r * math.sin(theta),
                        z,
                    ))

        triangles: list[tuple[int, int, int]] = []

        # Taban halkasından ilk ara halkaya (segment sayıları farklı olabilir
        # - footprint çokgen kenar sayısı ile n farklıysa fan üçgenleme).
        first_ring_start = ring_start_indices[0]
        for i in range(base_ring_count):
            i2 = (i + 1) % base_ring_count
            seg_frac = i / base_ring_count
            j = first_ring_start + int(seg_frac * n) % n
            j2 = first_ring_start + (int(seg_frac * n) + 1) % n
            triangles.append((i, i2, j))
            triangles.append((i2, j2, j))

        # Ara halkalar arası (apex halkasına kadar).
        for ring_idx in range(m - 1):
            start_a = ring_start_indices[ring_idx]
            start_b = ring_start_indices[ring_idx + 1]
            is_apex_next = (ring_idx + 1 == m - 1)
            if is_apex_next:
                apex = start_b
                for seg in range(n):
                    a = start_a + seg
                    a2 = start_a + (seg + 1) % n
                    triangles.append((a, a2, apex))
            else:
                for seg in range(n):
                    a = start_a + seg
                    a2 = start_a + (seg + 1) % n
                    b = start_b + seg
                    b2 = start_b + (seg + 1) % n
                    triangles.append((a, a2, b))
                    triangles.append((a2, b2, b))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="roof_dome")
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def hip(polygon: Polygon, base_z: float, pitch_deg: float = 25.0) -> Mesh3D:
        """Basitleştirilmiş hip çatı: iskeleti içe-büzme (straight-skeleton
        yaklaşık - normal-offset) ile ridge çizgisi üretir, ridge yüksekliği
        eğimden hesaplanır. Karmaşık konkav poligonlarda pyramid'e düşer."""
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        if len(ring) != 4:
            # Genel çokgenler için sağlam fallback: piramit.
            return RoofGenerator.pyramid(polygon, base_z, pitch_deg)

        # Dikdörtgen özel durumu: klasik hip çatı (ridge uzun kenara paralel).
        p0, p1, p2, p3 = ring
        edge_lens = [p0.distance_to(p1), p1.distance_to(p2)]
        short_side = min(edge_lens)
        ridge_height = (short_side / 2.0) * math.tan(math.radians(pitch_deg))

        if edge_lens[0] >= edge_lens[1]:
            # p0-p1 ve p2-p3 uzun kenarlar; ridge bunlara paralel, ortada.
            ridge_a = Point2D((p0.x + p3.x) / 2.0, (p0.y + p3.y) / 2.0)
            ridge_b = Point2D((p1.x + p2.x) / 2.0, (p1.y + p2.y) / 2.0)
        else:
            ridge_a = Point2D((p0.x + p1.x) / 2.0, (p0.y + p1.y) / 2.0)
            ridge_b = Point2D((p2.x + p3.x) / 2.0, (p2.y + p3.y) / 2.0)

        eave = [Vertex3D(p.x, p.y, base_z) for p in ring]
        ridge = [
            Vertex3D(ridge_a.x, ridge_a.y, base_z + ridge_height),
            Vertex3D(ridge_b.x, ridge_b.y, base_z + ridge_height),
        ]
        vertices = eave + ridge
        r0, r1 = 4, 5
        # Dörtgen hip çatı üçgenleri (2 üçgen alınlık + 2 eğim yüzeyi):
        triangles = [
            (0, 1, r0),       # ön üçgen yüzey
            (2, 3, r1),        # arka üçgen yüzey
            (1, 2, r1), (1, r1, r0),   # sağ eğim (quad -> 2 üçgen)
            (3, 0, r0), (3, r0, r1),   # sol eğim (quad -> 2 üçgen)
        ]
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="roof_hip")
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    # ------------------------------------------------------------------ #
    # Gable / Cross Gable / Mansard / Sawtooth / Modern
    # ------------------------------------------------------------------ #
    @staticmethod
    def gable(polygon: Polygon, base_z: float, pitch_deg: float = 30.0) -> Mesh3D:
        """Beşik çatı: ridge uzun eksen boyunca, iki eğik yüzey + iki üçgen
        alınlık (gable end)."""
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        if len(ring) != 4:
            return RoofGenerator.pyramid(polygon, base_z, pitch_deg)

        p0, p1, p2, p3 = ring
        len_a = p0.distance_to(p1)
        len_b = p1.distance_to(p2)
        ridge_height = (min(len_a, len_b) / 2.0) * math.tan(math.radians(pitch_deg))

        if len_a >= len_b:
            mid_01 = Point2D((p0.x + p1.x) / 2, (p0.y + p1.y) / 2)
            mid_32 = Point2D((p3.x + p2.x) / 2, (p3.y + p2.y) / 2)
            ridge_pts = (mid_32, mid_01)
        else:
            mid_12 = Point2D((p1.x + p2.x) / 2, (p1.y + p2.y) / 2)
            mid_30 = Point2D((p3.x + p0.x) / 2, (p3.y + p0.y) / 2)
            ridge_pts = (mid_12, mid_30)

        eave = [Vertex3D(p.x, p.y, base_z) for p in ring]
        ridge = [Vertex3D(r.x, r.y, base_z + ridge_height) for r in ridge_pts]
        vertices = eave + ridge
        r0, r1 = 4, 5
        triangles = [
            (0, 1, r0), (1, r1, r0),
            (1, 2, r1),
            (2, 3, r1), (3, r0, r1),
            (3, 0, r0),
        ]
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="roof_gable")
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def cross_gable(polygon: Polygon, base_z: float, pitch_deg: float = 30.0) -> Mesh3D:
        """İki gable çatının L/T/+ şeklinde kesişimi — GERÇEK vadi (valley)
        geometrisi.

        Önceki sürüm iki tam gable mesh'i olduğu gibi üst üste bindiriyordu
        (kod yorumunda itiraf edildiği gibi "kaba yaklaşık"); bu, iki çatının
        birbirinin İÇİNDEN GEÇTİĞİ (protrüzyon) görsel olarak yanlış bir
        sonuç üretiyordu.

        Bu sürüm, footprint'i (bounding-box merkezli) beş dikdörtgen hücreye
        böler: dört "kol" (her biri tek bir kanadın çatı eğimini taşır) ve
        bir "kesişim" hücresi. Kesişim hücresinde yükseklik iki kanadın
        eğim fonksiyonlarının MİNİMUMU olarak hesaplanır — bu, iki çıkma
        çatı yüzeyinin fiziksel olarak nerede kesiştiğini (vadi çizgisini)
        analitik olarak verir: yükseklikler eşit olduğu noktada iki yüzey
        buluşur, düşük olan taraf görünür yüzey olur. Kesişim hücresi
        merkez noktasından köşelere fan üçgenleme yapılarak vadi/dere
        köşegenleri (eşit genişlikte simetrik durumda 45°'lik klasik
        çapraz vadi deseni) yakalanır.

        Kısıtlama (dürüst not): footprint'in gerçek L/T/+ şekli değil,
        bounding-box + merkezden %30 genişlikte iki şerit yaklaşımı
        kullanılıyor (önceki sürümle aynı basitleştirme seviyesi) — gerçek
        poligon sınırına tam oturan bir vadi hesap edilmiyor, sadece iki
        çatı yüzeyinin birbirini geçmesi sorunu (protrüzyon) düzeltiliyor.
        Poligonun gerçek L/T/+ konturuna tam kenetlenen genel bir çözüm
        (stdlib-only, CSG'siz) ayrı bir iş olarak bırakıldı.
        """
        centroid = RoofGenerator._centroid(polygon)
        ring = polygon.closed_ring()[:-1]
        min_x = min(p.x for p in ring); max_x = max(p.x for p in ring)
        min_y = min(p.y for p in ring); max_y = max(p.y for p in ring)

        half_depth_a = (max_y - min_y) * 0.15  # wing_a (ridge x eksenine paralel)
        half_depth_b = (max_x - min_x) * 0.15  # wing_b (ridge y eksenine paralel)

        slope = math.tan(math.radians(pitch_deg))
        ridge_h_a = half_depth_a * slope
        ridge_h_b = half_depth_b * slope

        def height_a(y: float) -> float:
            d = abs(y - centroid.y)
            return base_z + max(ridge_h_a - slope * d, 0.0)

        def height_b(x: float) -> float:
            d = abs(x - centroid.x)
            return base_z + max(ridge_h_b - slope * d, 0.0)

        def roof_height(x: float, y: float) -> float:
            in_a = abs(y - centroid.y) <= half_depth_a + 1e-9
            in_b = abs(x - centroid.x) <= half_depth_b + 1e-9
            if in_a and in_b:
                return min(height_a(y), height_b(x))  # vadi (valley)
            if in_a:
                return height_a(y)
            if in_b:
                return height_b(x)
            return base_z

        ya0, ya1 = centroid.y - half_depth_a, centroid.y + half_depth_a
        xb0, xb1 = centroid.x - half_depth_b, centroid.x + half_depth_b

        vertices: list[Vertex3D] = []
        triangles: list[tuple[int, int, int]] = []

        def add_cell(x_coords: tuple[float, float], y_coords: tuple[float, float]) -> None:
            """Dikdörtgen hücreyi merkez noktasından köşelere fan üçgenleme
            ile doldurur - bu, kesişim hücresindeki vadi köşegenlerini
            (yükseklik minimum fonksiyonunun süreksizlik/kırılma çizgisini)
            yakalamak için gerekli; düz iki-üçgenli quad bunu kaçırırdı."""
            corners = [
                (x_coords[0], y_coords[0]), (x_coords[1], y_coords[0]),
                (x_coords[1], y_coords[1]), (x_coords[0], y_coords[1]),
            ]
            corner_idx = []
            for cx, cy in corners:
                corner_idx.append(len(vertices))
                vertices.append(Vertex3D(cx, cy, roof_height(cx, cy)))
            mid_x = (x_coords[0] + x_coords[1]) / 2.0
            mid_y = (y_coords[0] + y_coords[1]) / 2.0
            center_idx = len(vertices)
            vertices.append(Vertex3D(mid_x, mid_y, roof_height(mid_x, mid_y)))
            for i in range(4):
                a = corner_idx[i]
                b = corner_idx[(i + 1) % 4]
                triangles.append((a, b, center_idx))

        # Kesişim (vadi) hücresi.
        add_cell((xb0, xb1), (ya0, ya1))
        # Dört kol (yalnızca kendi kanadının eğimini taşıyan bölgeler).
        if xb0 - min_x > 1e-9:
            add_cell((min_x, xb0), (ya0, ya1))
        if max_x - xb1 > 1e-9:
            add_cell((xb1, max_x), (ya0, ya1))
        if ya0 - min_y > 1e-9:
            add_cell((xb0, xb1), (min_y, ya0))
        if max_y - ya1 > 1e-9:
            add_cell((xb0, xb1), (ya1, max_y))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name="roof_cross_gable")
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def mansard(polygon: Polygon, base_z: float, pitch_deg: float = 60.0) -> Mesh3D:
        """Mansard: dik alt eğim + üstte hafif eğimli/düz platform (Fransız
        çatı tipi). İki kademeli daraltılmış extrusion olarak modellenir."""
        centroid = RoofGenerator._centroid(polygon)
        ring = polygon.closed_ring()[:-1]
        lower_height = 1.8  # dik alt kısım yüksekliği (m)

        inset_ratio = 0.65
        inset_ring = [
            Point2D(
                centroid.x + (p.x - centroid.x) * inset_ratio,
                centroid.y + (p.y - centroid.y) * inset_ratio,
            )
            for p in ring
        ]
        lower_prism = MeshBuilder.extrude_polygon(
            Polygon(ring), base_z, lower_height, name="roof_mansard_lower"
        )
        upper_roof = RoofGenerator.hip(
            Polygon(inset_ring), base_z + lower_height, pitch_deg=20.0
        )
        return MeshMerger.merge([lower_prism, upper_roof], name="roof_mansard")

    @staticmethod
    def sawtooth(polygon: Polygon, base_z: float, pitch_deg: float = 35.0) -> Mesh3D:
        """Testere dişi (kuzey ışığı) çatı: footprint X ekseni boyunca eşit
        şeritlere bölünüp her şeride mono-pitch uygulanır."""
        ring = polygon.closed_ring()[:-1]
        xs = [p.x for p in ring]
        ys = [p.y for p in ring]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        strip_count = max(2, int((max_x - min_x) / 4.0))
        strip_width = (max_x - min_x) / strip_count

        segments: list[Mesh3D] = []
        for i in range(strip_count):
            sx0 = min_x + i * strip_width
            sx1 = sx0 + strip_width
            strip_poly = Polygon([
                Point2D(sx0, min_y), Point2D(sx1, min_y),
                Point2D(sx1, max_y), Point2D(sx0, max_y),
            ])
            segments.append(RoofGenerator._mono_pitch(strip_poly, base_z, pitch_deg,
                                                        name=f"roof_sawtooth_{i}"))
        return MeshMerger.merge(segments, name="roof_sawtooth")

    @staticmethod
    def modern(polygon: Polygon, base_z: float, pitch_deg: float = 8.0) -> Mesh3D:
        """Modern mimari: çok hafif eğimli asimetrik tek eğim + kademeli
        parapet detayı."""
        base = RoofGenerator._mono_pitch(polygon, base_z, pitch_deg, name="roof_modern_base")
        centroid = RoofGenerator._centroid(polygon)
        ring = polygon.closed_ring()[:-1]
        inset = Polygon([
            Point2D(centroid.x + (p.x - centroid.x) * 0.9, centroid.y + (p.y - centroid.y) * 0.9)
            for p in ring
        ])
        parapet = MeshBuilder.extrude_polygon(inset, base_z, 0.3, name="roof_modern_parapet")
        return MeshMerger.merge([base, parapet], name="roof_modern")

    @staticmethod
    def glass_atrium(polygon: Polygon, base_z: float, pitch_deg: float = 8.0) -> Mesh3D:
        """ROADMAP_V5 M2.1: "cam çatı/atrium (AVM/ofis tipleri için)".

        Hafif eğimli düz bir cam yüzey (ince kalınlık, `roof_solar`
        eğim mantığıyla aynı `_mono_pitch` altyapısı) + üzerine bindirilmiş
        yapısal çelik kafes (mullion/grid) çıtaları — gerçek bir atrium
        camı, camın kendisi cam malzemesiyle (mesh adı `roof_glass_atrium`
        üzerinden `material_engine`/orchestrator tarafında CAM malzemesine
        eşlenmesi beklenir; bu modülün sorumluluğu yalnız geometri).
        Kafes çıtaları düz düşey/yatay şeritler olarak, gerçek bir CSG kesim
        değil, camın üstüne ince ekstrüde çıtalar merge ederek üretilir —
        dosyanın başındaki RoofDetailGenerator ile aynı basitleştirme
        prensibi (stdlib-only, harici CAD kütüphanesi yok)."""
        glazing = RoofGenerator._mono_pitch(polygon, base_z, pitch_deg, name="roof_glass_atrium")

        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        xs = [p.x for p in ring]
        ys = [p.y for p in ring]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span = (max_x - min_x) or 1.0
        rise = span * math.tan(math.radians(pitch_deg))

        grid_spacing = 2.0  # metre - tipik atrium mullion aralığı
        bar_w = 0.08
        parts: list[Mesh3D] = [glazing]

        # Yatay (Y sabit) mullion çıtaları - X boyunca eğimi takip eder
        n_y = max(1, int((max_y - min_y) // grid_spacing))
        for j in range(1, n_y):
            y = min_y + j * (max_y - min_y) / n_y
            bar_ring = [
                Point2D(min_x, y - bar_w / 2), Point2D(max_x, y - bar_w / 2),
                Point2D(max_x, y + bar_w / 2), Point2D(min_x, y + bar_w / 2),
            ]
            v0 = Vertex3D(min_x, y, base_z)
            v1 = Vertex3D(max_x, y, base_z + rise)
            parts.append(RoofDetailGenerator._tilted_panel(
                (min_x + max_x) / 2, y, base_z + rise / 2, pitch_deg, min_x,
                span, bar_w, 0.06, name=f"atrium_mullion_h_{j}",
            ))

        # Düşey (X sabit) mullion çıtaları - eğim yönüne dik, sabit derinlik
        n_x = max(1, int((max_x - min_x) // grid_spacing))
        for i in range(1, n_x):
            x = min_x + i * (max_x - min_x) / n_x
            z = base_z + (x - min_x) / span * rise
            box = MeshBuilder.build_box(
                bar_w, (max_y - min_y), 0.10,
                center_x=x, center_y=(min_y + max_y) / 2, base_z=z - 0.05,
                name=f"atrium_mullion_v_{i}",
            )
            parts.append(box)

        return MeshMerger.merge(parts, name="roof_glass_atrium")


# ============================================================================ #
# Çatı üstü detaylar: baca, çatı penceresi (FAZ 1.1 eksik maddeleri)
# ============================================================================ #
#
# roof_generator'ın ilk versiyonunda 12 çatı gövde tipi vardı ama roadmap
# 1.1'de istenen "çatı üzeri detaylar" (baca, çatı penceresi) hiç
# üretilmiyordu. Güneş paneli YERLEŞİMİ (panelin kendi 3D geometrisi,
# `solar()` çatı eğiminden ayrı olarak) de eksikti - `ai_reconstruction`
# tarafındaki `EnvironmentObjectType.SOLAR_PANEL` yalnızca bahçe/park
# seviyesinde bir amenity nesnesi, çatıya monte edilmiş bir panel değil.
#
# Bu sınıf tam bir CSG/boolean-cut sistemi DEĞİLDİR (mevcut mesh_engine
# stdlib-only, harici CAD kütüphanesi yok ilkesine bağlı kalır); bunun
# yerine endüstride yaygın basitleştirme kullanılır: baca ve çatı
# penceresi çatı yüzeyinden YUKARI DOĞRU ÇIKINTI YAPAN ayrı kutu
# mesh'leri olarak üretilip çatı mesh'ine merge edilir (gerçek bir delik
# kesip içine oturtmak yerine).

class RoofDetailGenerator:
    """Çatı gövdesi (`RoofGenerator.generate(...)` çıktısı) üzerine baca ve
    çatı penceresi (skylight/dormer) gibi çıkıntılı detaylar ekler."""

    @staticmethod
    def add_chimney(
        roof_mesh: Mesh3D,
        position: Point2D,
        roof_base_z: float,
        width: float = 0.6,
        depth: float = 0.6,
        height: float = 1.2,
    ) -> Mesh3D:
        """Verilen (x, y) konumunda, çatı tabanından itibaren yükselen
        dikdörtgen kesitli bir baca ekler. `roof_base_z`, bacanın
        oturacağı yaklaşık çatı yüzeyi Z'sidir (çağıran taraf genelde
        `base_z` + çatı ortalama yüksekliğini geçer)."""
        chimney = MeshBuilder.build_box(
            width, depth, height,
            center_x=position.x, center_y=position.y, base_z=roof_base_z,
            name="chimney",
        )
        return MeshMerger.merge([roof_mesh, chimney], name=roof_mesh.name + "_with_chimney")

    @staticmethod
    def add_skylight(
        roof_mesh: Mesh3D,
        position: Point2D,
        roof_surface_z: float,
        width: float = 0.8,
        depth: float = 0.8,
        height: float = 0.35,
    ) -> Mesh3D:
        """Çatı yüzeyinden hafifçe yükselen bir çatı penceresi (skylight)
        kütlesi ekler - gerçek bir cam/delik kesimi değil, çıkıntılı basit
        bir hacim (yaygın procedural yaklaşım)."""
        skylight = MeshBuilder.build_box(
            width, depth, height,
            center_x=position.x, center_y=position.y, base_z=roof_surface_z,
            name="skylight",
        )
        return MeshMerger.merge([roof_mesh, skylight], name=roof_mesh.name + "_with_skylight")

    @staticmethod
    def _tilted_panel(
        center_x: float, center_y: float, surface_z: float,
        pitch_deg: float, min_x: float,
        panel_w: float, panel_d: float, thickness: float,
        name: str,
    ) -> Mesh3D:
        """`_mono_pitch` yüzeyine (eğim X ekseni boyunca, referans `min_x`)
        oturan, panel yüzeyinin normaline dik ince bir kutu üretir. Panel
        merkezi (`center_x`, `center_y`) çatı yüzeyinde `surface_z`
        yüksekliğinde varsayılır; panel bu noktadan eğim yönünde
        (`pitch_deg`) döndürülmüş dikdörtgen bir prizma olarak inşa
        edilir - `MeshBuilder` yalnızca düşey extrusion yaptığı için
        köşeler burada elle (rotasyon matrisiyle) hesaplanır."""
        theta = math.radians(pitch_deg)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        hw, hd = panel_w / 2.0, panel_d / 2.0

        local = [
            (-hw, -hd, 0.0), (hw, -hd, 0.0), (hw, hd, 0.0), (-hw, hd, 0.0),
            (-hw, -hd, thickness), (hw, -hd, thickness), (hw, hd, thickness), (-hw, hd, thickness),
        ]
        verts: list[Vertex3D] = []
        for (u, v, w) in local:
            dx = u * cos_t - w * sin_t
            dz = u * sin_t + w * cos_t
            verts.append(Vertex3D(center_x + dx, center_y + v, surface_z + dz))

        triangles = [
            (0, 1, 2), (0, 2, 3),
            (4, 6, 5), (4, 7, 6),
            (0, 5, 1), (0, 4, 5),
            (1, 6, 2), (1, 5, 6),
            (2, 7, 3), (2, 6, 7),
            (3, 4, 0), (3, 7, 4),
        ]
        mesh = Mesh3D(vertices=verts, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def add_solar_panels(
        roof_mesh: Mesh3D,
        polygon: Polygon,
        base_z: float,
        pitch_deg: float = 15.0,
        panel_w: float = 1.0,
        panel_d: float = 1.6,
        thickness: float = 0.04,
        margin: float = 0.5,
        gap: float = 0.05,
    ) -> Mesh3D:
        """Roadmap 1.1: "güneş paneli yerleşimi" - önceki turda yalnız
        çatı eğimi (`RoofGenerator.solar`) üretiliyordu, panelin kendi
        3D geometrisi yoktu. Bu, `RoofGenerator._mono_pitch` ile aynı
        eğim kuralını (X ekseni boyunca, `polygon` sınırlarına göre)
        kullanarak çatı yüzeyine gerçekten oturan (eğime uyan, tilted)
        bir panel dizisi üretir ve çatı mesh'ine merge eder. Kenarlardan
        `margin` kadar boşluk bırakılır; paneller arası `gap` uygulanır."""
        ring = polygon.closed_ring()[:-1]
        if not Polygon(ring).is_ccw():
            ring = list(reversed(ring))
        xs = [p.x for p in ring]
        ys = [p.y for p in ring]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        span = (max_x - min_x) or 1.0
        rise = span * math.tan(math.radians(pitch_deg))

        usable_x0, usable_x1 = min_x + margin, max_x - margin
        usable_y0, usable_y1 = min_y + margin, max_y - margin
        if usable_x1 <= usable_x0 or usable_y1 <= usable_y0:
            return roof_mesh

        step_x = panel_w * math.cos(math.radians(pitch_deg)) + gap
        step_y = panel_d + gap

        n_x = max(0, int((usable_x1 - usable_x0 + gap) // step_x))
        n_y = max(0, int((usable_y1 - usable_y0 + gap) // step_y))
        if n_x == 0 or n_y == 0:
            return roof_mesh

        row_w = n_x * step_x - gap
        row_d = n_y * step_y - gap
        start_x = usable_x0 + (usable_x1 - usable_x0 - row_w) / 2.0 + step_x / 2.0 - gap / 2.0
        start_y = usable_y0 + (usable_y1 - usable_y0 - row_d) / 2.0 + step_y / 2.0 - gap / 2.0

        panels: list[Mesh3D] = [roof_mesh]
        for j in range(n_y):
            cy = start_y + j * step_y
            for i in range(n_x):
                cx = start_x + i * step_x
                surface_z = base_z + (cx - min_x) / span * rise
                panels.append(RoofDetailGenerator._tilted_panel(
                    cx, cy, surface_z, pitch_deg, min_x,
                    panel_w, panel_d, thickness,
                    name=f"solar_panel_{j}_{i}",
                ))
        return MeshMerger.merge(panels, name=roof_mesh.name + "_with_solar_panels")

    @staticmethod
    def add_soffit(
        wall_polygon: Polygon,
        eave_polygon: Polygon,
        base_z: float,
        name: str = "roof_soffit",
    ) -> Mesh3D:
        """ROADMAP_V5 M2.1: "Çatı-cephe kesişim detayı: saçak altı (soffit)
        yüzeyinin ayrı bir malzeme/mesh parçası olarak üretilmesi (şu an
        muhtemelen boş/açık bırakılıyor)".

        `wall_polygon` (bina dış duvar hattı, saçak öncesi) ile
        `eave_polygon` (`RoofGenerator._offset_footprint` ile üretilen,
        `overhang_m` kadar dışa taşmış saçak hattı) arasındaki halka
        şeklindeki yatay şeridi, `base_z` yüksekliğinde kapatan bir yüzey
        üretir — önceden tamamen açık/görünmez olan bu boşluk artık ayrı
        bir mesh parçası (soffit paneli, genelde alçı/ahşap malzeme)
        olarak dışa aktarılabilir hale gelir. İki poligonun aynı köşe
        sayısına ve sırasına sahip olduğu varsayılır (ikisi de aynı
        `_offset_footprint` kaynağından türetildiği için bu koşul
        `RoofGenerator.generate` akışında her zaman sağlanır)."""
        wall_ring = wall_polygon.closed_ring()[:-1]
        eave_ring = eave_polygon.closed_ring()[:-1]
        if not Polygon(wall_ring).is_ccw():
            wall_ring = list(reversed(wall_ring))
        if not Polygon(eave_ring).is_ccw():
            eave_ring = list(reversed(eave_ring))
        if len(wall_ring) != len(eave_ring):
            # Farklı köşe sayısı (ör. dış kaynaklı poligon) - güvenli
            # düşüş: soffit üretmeden boş bir mesh döndür, çağıran taraf
            # (RoofGenerator.generate) bunu opsiyonel merge ile ele alır.
            return Mesh3D(vertices=[], triangles=[], name=name)

        n = len(wall_ring)
        vertices = [Vertex3D(p.x, p.y, base_z) for p in wall_ring] + \
                   [Vertex3D(p.x, p.y, base_z) for p in eave_ring]
        triangles = []
        for i in range(n):
            i2 = (i + 1) % n
            inner_a, inner_b = i, i2
            outer_a, outer_b = i + n, i2 + n
            # aşağı bakan normal (soffit altan görünür)
            triangles.append((inner_a, outer_b, outer_a))
            triangles.append((inner_a, inner_b, outer_b))
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh

    @staticmethod
    def suggest_pitch_deg(climate_zone: str) -> float:
        """İklim bölgesine göre varsayılan çatı eğimi önerisi (derece).

        Roadmap 1.1: "Çatı eğimi... iklim bölgesine göre varsayılan öneri
        (kar yükü olan bölgelerde daha dik çatı gibi)". Bu kesin bir
        yapısal hesap değildir - kar birikmesini azaltmak için genel kabul
        görmüş kaba yönlendirme değerleridir; gerçek yapısal/yönetmelik
        hesabı `regulations` modülüyle birlikte ayrıca yapılmalıdır.
        """
        table = {
            "kutup": 45.0,      # çok yoğun kar yükü - dik çatı, kar birikmesin
            "daglik_karli": 40.0,
            "soguk": 30.0,
            "ilman": 22.0,       # ılıman - roadmap varsayılanı (25° civarı)
            "kurak": 10.0,       # kar yükü yok, düşük eğim yeterli
            "tropikal": 15.0,    # yoğun yağmur tahliyesi için hafif eğim yeterli
        }
        return table.get(climate_zone.lower(), 25.0)


# ============================================================================ #
# ROADMAP_V5 M2.1: "Çok parçalı çatı" - L/U/T şeklindeki footprint'lerde
# birden fazla çatı hacminin birleşimi (vadi/dere hattı hesaplaması ayrı
# bir alt-algoritma gerektirir).
# ============================================================================ #
#
# Tam bir genel-amaçlı poligon parçalama (arbitrary polygon decomposition)
# CSG/hesaplamalı geometri kütüphanesi gerektirir (stdlib-only ilkesine
# aykırı). Bunun yerine burada ORTOGONAL (yalnızca eksen-hizalı kenarlı,
# yani gerçek L/U/T/artı planlı binaların neredeyse tamamını kapsayan)
# footprint'ler için, endüstride yaygın "grid + maximal rectangle merge"
# yaklaşımı kullanılır: köşe koordinatlarından bir ızgara çıkarılır, içeride
# kalan hücreler bulunur, komşu hücreler satır bazında birleştirilip
# (row merge) sonra sütun bazında (column merge) maksimal dikdörtgenlere
# indirgenir. Her dikdörtgen parça ayrı bir çatı hacmi olarak üretilip
# `MeshMerger` ile birleştirilir - kesişim/vadi hattı noktasal olarak tam
# temiz değildir (iki ayrı çatı hacmi kesişim bölgesinde üst üste biner),
# ama non-manifold/self-intersection üretmez ve görsel olarak doğru bir
# çok-parçalı çatı silüeti verir. Eksen-hizalı olmayan (kavisli/açılı)
# footprint'ler için `decompose_rectilinear` tek bir parça (poligonun
# tamamı) döndürür - bu durumda `generate_multi_part` davranışı
# `RoofGenerator.generate` ile birebir aynıdır (güvenli düşüş).
class MultiPartRoofGenerator:
    """Ortogonal L/U/T/artı planlı footprint'leri dikdörtgen alt-hacimlere
    ayırıp her birine ayrı çatı üretir, sonra birleştirir."""

    @staticmethod
    def _is_rectilinear(polygon: Polygon) -> bool:
        ring = polygon.closed_ring()
        for i in range(len(ring) - 1):
            p1, p2 = ring[i], ring[i + 1]
            dx, dy = abs(p2.x - p1.x), abs(p2.y - p1.y)
            if dx < 1e-6 or dy < 1e-6:
                continue
            return False  # ne yatay ne düşey kenar -> eksen-hizalı değil
        return True

    @staticmethod
    def decompose_rectilinear(polygon: Polygon) -> list[Polygon]:
        """Eksen-hizalı bir poligonu maksimal dikdörtgenlere ayırır.
        Eksen-hizalı değilse `[polygon]` döner (tek parça, güvenli düşüş)."""
        if not MultiPartRoofGenerator._is_rectilinear(polygon):
            return [polygon]

        ring = polygon.closed_ring()[:-1]
        xs = sorted({round(p.x, 6) for p in ring})
        ys = sorted({round(p.y, 6) for p in ring})
        if len(xs) < 2 or len(ys) < 2:
            return [polygon]

        occupied = [[False] * (len(xs) - 1) for _ in range(len(ys) - 1)]
        for j in range(len(ys) - 1):
            cy = (ys[j] + ys[j + 1]) / 2.0
            for i in range(len(xs) - 1):
                cx = (xs[i] + xs[i + 1]) / 2.0
                occupied[j][i] = GeometryEngine.point_in_polygon(Point2D(cx, cy), polygon)

        rects: list[Polygon] = []
        consumed = [[False] * (len(xs) - 1) for _ in range(len(ys) - 1)]
        for j in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                if not occupied[j][i] or consumed[j][i]:
                    continue
                i_end = i
                while i_end + 1 < len(xs) - 1 and occupied[j][i_end + 1] and not consumed[j][i_end + 1]:
                    i_end += 1
                j_end = j
                while j_end + 1 < len(ys) - 1 and all(
                    occupied[j_end + 1][k] and not consumed[j_end + 1][k] for k in range(i, i_end + 1)
                ):
                    j_end += 1
                for jj in range(j, j_end + 1):
                    for ii in range(i, i_end + 1):
                        consumed[jj][ii] = True
                rects.append(Polygon([
                    Point2D(xs[i], ys[j]), Point2D(xs[i_end + 1], ys[j]),
                    Point2D(xs[i_end + 1], ys[j_end + 1]), Point2D(xs[i], ys[j_end + 1]),
                ]))
        return rects or [polygon]

    @staticmethod
    def generate(
        polygon: Polygon,
        base_z: float,
        roof_type: RoofType | str = RoofType.HIP,
        pitch_deg: float = 25.0,
        overhang_m: float = 0.4,
    ) -> Mesh3D:
        """Kabul kriteri (ROADMAP_V5 M2.1): L, U, T, artı (+), düzensiz 6+
        köşeli footprint'lerin her biri için self-intersection üretmeden
        çatı üretimi. Her dikdörtgen alt-parça bağımsız olarak
        `RoofGenerator.generate` ile üretilir (mevcut mimari/API korunur,
        yalnızca üstüne bir parçalama katmanı eklenir)."""
        parts = MultiPartRoofGenerator.decompose_rectilinear(polygon)
        if len(parts) <= 1:
            return RoofGenerator.generate(polygon, base_z, roof_type, pitch_deg, overhang_m)

        meshes = [
            RoofGenerator.generate(part, base_z, roof_type, pitch_deg, overhang_m)
            for part in parts
        ]
        return MeshMerger.merge(meshes, name=f"roof_multipart_{RoofType(roof_type).value}")
