"""
Curved / Circular Facade Support
=================================

ROADMAP_V5 — M2.2 (kalan madde): "Kavisli/eğrisel cephe desteği (şu an
muhtemelen sadece düz poligon kenarları destekleniyor) — dairesel/eliptik
footprint segmentlerinin tesselasyonu."

Tasarım kararı (mimariyi koruma ilkesi): `FacadeGenerator.generate()`,
`RoofGenerator`, `WallOpeningMeshBuilder` vb. hiçbiri "eğri" kavramını
bilmez — hepsi düz kenarlı `Polygon` üzerinde çalışır. Gerçek bir eğik
duvar segmenti eklemek yerine (ki bu tüm alt sistemlerin — pencere
yerleştirme, boşluk kesme, çatı saçağı ofsetleme — yeniden yazılmasını
gerektirirdi), bu modül **tesselasyon** yaklaşımını kullanır: dairesel/
eliptik bir kenarı yeterince küçük düz segmentlere bölüp normal bir
`Polygon` üretir. Böylece:

* `FacadeGenerator.generate()` hiç değişmeden, üretilen çok-köşeli
  `Polygon`'u her zamanki gibi kenar kenar işler (her segment kendi
  penceresini/duvarını alır — sonuç, gerçek bir silindirik cephenin
  ayrık (faceted) ama yüksek çözünürlükte yaklaşıklamasıdır, tıpkı gerçek
  3D motorlarının eğrileri çokgenlerle temsil etmesi gibi).
* `RoofGenerator._offset_footprint()` gibi merkez-radyal ofset yöntemleri
  de değişmeden çalışır (çok köşeli bir poligon zaten çember gibi
  davranır).

Bu yüzden bu modül **opt-in bir footprint ön-işlemcisidir**: var olan
API'lere hiçbir zorunlu parametre eklemez, yalnızca "eğrisel bir
footprint istiyorsan bunu kullan, sonra normal şekilde devam et" şeklinde
çalışır.
"""

from __future__ import annotations

import math

from ..core_engine.geometry_engine import Point2D, Polygon


class CurvedFootprintGenerator:
    """Dairesel/eliptik ve yuvarlatılmış-köşeli footprint üretimi.

    Tüm metodlar düz kenarlı ama yeterince yoğun `Polygon` döndürür —
    aşağı akışta (facade/roof/setback) hiçbir özel işlem gerekmez.
    """

    @staticmethod
    def circular_footprint(
        center: Point2D, radius: float, segments: int = 32,
    ) -> Polygon:
        """Tam dairesel footprint (örn. silindirik kule/atrium tipi bina).

        `segments` arttıkça facet açısı küçülür (gerçek eğriye yaklaşır);
        32 segment ~11.25° facet açısı verir — görsel olarak "köşeli"
        hissi vermeyecek kadar yoğun, mesh maliyeti düşük kalacak kadar az.
        """
        segments = max(8, segments)
        pts = [
            Point2D(
                center.x + radius * math.cos(2 * math.pi * i / segments),
                center.y + radius * math.sin(2 * math.pi * i / segments),
            )
            for i in range(segments)
        ]
        return Polygon(pts)

    @staticmethod
    def elliptical_footprint(
        center: Point2D, radius_x: float, radius_y: float,
        rotation_deg: float = 0.0, segments: int = 32,
    ) -> Polygon:
        """Eliptik footprint (örn. oval plan AVM/stadyum-benzeri kütle)."""
        segments = max(8, segments)
        theta = math.radians(rotation_deg)
        cos_r, sin_r = math.cos(theta), math.sin(theta)
        pts = []
        for i in range(segments):
            a = 2 * math.pi * i / segments
            lx, ly = radius_x * math.cos(a), radius_y * math.sin(a)
            # rotasyon uygula, sonra merkeze öteleme
            rx = lx * cos_r - ly * sin_r
            ry = lx * sin_r + ly * cos_r
            pts.append(Point2D(center.x + rx, center.y + ry))
        return Polygon(pts)

    @staticmethod
    def rounded_rectangle_footprint(
        width: float, depth: float, corner_radius: float,
        center: Point2D | None = None, segments_per_corner: int = 6,
    ) -> Polygon:
        """Dikdörtgen footprint'in dört köşesini yuvarlatır (örn. modern
        ofis binalarında sık görülen yumuşatılmış köşe kütlesi).

        `corner_radius`, `min(width, depth) / 2`'yi aşarsa otomatik olarak
        o üst sınıra kırpılır (dejenere/self-intersecting köşe üretmemek
        için — kabul kriteri: hiçbir çıktı kendi kendini kesmemeli).
        """
        center = center or Point2D(0.0, 0.0)
        r = max(0.0, min(corner_radius, min(width, depth) / 2.0 - 1e-6))
        hw, hd = width / 2.0, depth / 2.0
        segments_per_corner = max(2, segments_per_corner)

        if r <= 1e-9:
            # Dejenere durum: normal keskin köşeli dikdörtgene düş.
            return Polygon([
                Point2D(center.x - hw, center.y - hd),
                Point2D(center.x + hw, center.y - hd),
                Point2D(center.x + hw, center.y + hd),
                Point2D(center.x - hw, center.y + hd),
            ])

        # Her köşe için merkez + başlangıç/bitiş açısı (CCW, sağ-alttan
        # başlayarak): (cx, cy, start_deg, end_deg)
        corners = [
            (hw - r, -(hd - r), 270.0, 360.0),   # sağ-alt
            (hw - r, hd - r, 0.0, 90.0),          # sağ-üst
            (-(hw - r), hd - r, 90.0, 180.0),     # sol-üst
            (-(hw - r), -(hd - r), 180.0, 270.0), # sol-alt
        ]
        ring: list[Point2D] = []
        for (ccx, ccy, start_deg, end_deg) in corners:
            for i in range(segments_per_corner + 1):
                t = i / segments_per_corner
                ang = math.radians(start_deg + (end_deg - start_deg) * t)
                ring.append(Point2D(
                    center.x + ccx + r * math.cos(ang),
                    center.y + ccy + r * math.sin(ang),
                ))
        return Polygon(ring)

    @staticmethod
    def is_self_intersecting(polygon: Polygon) -> bool:
        """Basit O(n^2) kenar-kenar kesişim testi — kabul kriteri
        doğrulaması için (tesselasyon sonucu üretilen poligonun kendi
        kendini kesmediğini garanti eder). Küçük poligonlar (< 200 köşe)
        için yeterli; büyük sahnelerde kullanılmaz."""
        ring = polygon.closed_ring()[:-1]
        n = len(ring)
        if n < 4:
            return False

        def _segments_intersect(p1, p2, p3, p4) -> bool:
            def _orient(a, b, c) -> float:
                return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)

            d1 = _orient(p3, p4, p1)
            d2 = _orient(p3, p4, p2)
            d3 = _orient(p1, p2, p3)
            d4 = _orient(p1, p2, p4)
            if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and \
               ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)):
                return True
            return False

        for i in range(n):
            a1, a2 = ring[i], ring[(i + 1) % n]
            # komşu kenarlarla (paylaşılan köşe) kesişim testi anlamsız,
            # yalnızca uzak kenar çiftlerini kontrol et.
            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue
                b1, b2 = ring[j], ring[(j + 1) % n]
                if _segments_intersect(a1, a2, b1, b2):
                    return True
        return False


class VerticalProfileGenerator:
    """ROADMAP_V8 Faz 6.1 "Seçenek A - tam yapısal versiyon" (bkz.
    `docs/RFC_FAZ6_1_KAVISLI_CEPHE.md`): ana yapısal cephe sistemini
    (kat/oda/pencere - `FacadeGenerator`'ın kat-bazlı üretim döngüsü)
    düşey eksende gerçekten eğrilen bir silüete kavuşturur.

    RFC'nin Seçenek B'si (gerçek NURBS/spline yüzey + pencere/oda
    sisteminin (u,v) parametrik yüzeyde yeniden yazılması) bilinçli
    olarak seçilmedi: stdlib-only ortamda sıfırdan yüzey matematiği +
    `WallOpening`/`RoomGenerator`'ın tamamının yeniden yazılması
    gerektirir, projenin "mevcut mimariyi koru" ilkesiyle en çok gerilim
    yaratan seçenektir (RFC Bölüm 3). Bunun yerine bu sınıf, mevcut
    kat-bazlı mimariyi (`FacadeGenerator.generate(floor_count=...)`
    zaten her katı ayrı ayrı işliyor) genişletir: her kata, taban
    çokgenin merkez etrafında ölçeklenmiş/döndürülmüş/ötelenmiş kendi
    çokgenini vererek düşey profili üretir. Sonuç, kat sayısı kadar
    (gerçekçi bir bina için 3-50) düz segmentten oluşan **poligonal
    yaklaşıklamalı** bir eğri - true NURBS'ün pürüzsüzlüğüne sahip
    değil, ama pencere/kapı/oda/döşeme dahil **tüm yapısal sistem**
    gerçekten eğrilen yüzeyde çalışıyor (yalnızca kozmetik dış kabuk
    değil - RFC'nin Seçenek C'sinin ötesine geçiyor).
    """

    @staticmethod
    def floor_polygons(
        base_polygon: Polygon,
        floor_count: int,
        scale_at=None,
        rotation_deg_at=None,
        offset_at=None,
    ) -> list[Polygon]:
        """Her kat için `base_polygon`'un merkez etrafında ölçeklenmiş/
        döndürülmüş/ötelenmiş bir kopyasını üretir.

        `scale_at`/`rotation_deg_at`/`offset_at`: `Callable[[float], X]`
        - argüman, kat indeksinin `[0, 1]` aralığına normalize edilmiş
        hâlidir (`t = floor_idx / max(1, floor_count - 1)`, tek katlı
        binada `t=0`). `scale_at(t) -> float` (1.0 = orijinal boyut),
        `rotation_deg_at(t) -> float` (derece, taban merkezine göre),
        `offset_at(t) -> Point2D` (merkez kayması, "eğik kule" efekti
        için). Hiçbiri verilmezse tüm katlar taban çokgenle birebir aynı
        olur (dejenere durum: `FacadeGenerator`'ın eski davranışıyla
        matematiksel olarak özdeş - test edilmiştir).

        Döndürülen her `Polygon`, `base_polygon` ile **aynı köşe sayısı
        ve kenar sırasına** sahiptir (yalnızca koordinatlar dönüşür) -
        `FacadeGenerator.generate(floor_polygons=...)`'ın gerektirdiği
        koşul budur.
        """
        n = max(1, floor_count)
        center = base_polygon.centroid()
        base_ring = base_polygon.closed_ring()[:-1]
        result: list[Polygon] = []
        for floor_idx in range(n):
            t = floor_idx / (n - 1) if n > 1 else 0.0
            scale = scale_at(t) if scale_at is not None else 1.0
            rot_deg = rotation_deg_at(t) if rotation_deg_at is not None else 0.0
            offset = offset_at(t) if offset_at is not None else Point2D(0.0, 0.0)
            theta = math.radians(rot_deg)
            cos_r, sin_r = math.cos(theta), math.sin(theta)
            pts = []
            for p in base_ring:
                lx, ly = (p.x - center.x) * scale, (p.y - center.y) * scale
                rx = lx * cos_r - ly * sin_r
                ry = lx * sin_r + ly * cos_r
                pts.append(Point2D(center.x + rx + offset.x, center.y + ry + offset.y))
            result.append(Polygon(pts))
        return result
