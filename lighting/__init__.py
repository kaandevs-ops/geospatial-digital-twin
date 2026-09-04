"""
Lighting
=========

Roadmap Phase 2 - "Lighting".

Kapsam: Sun, Moon, HDR Sky, Dynamic Shadows, Ambient Lighting,
Global Illumination approximation.

Not: `SolarPosition` hesaplayıcısı burada tanımlanır ve Phase 6 (Analysis
Engine -> `sun_simulation`) ile paylaşılır - roadmap'in katmanlı mimari
ilkesine uygun olarak tek bir doğruluk kaynağı (single source of truth).
Gerçek GPU shadow-map render pass'i bu modülün kapsamı dışındadır (bkz.
Phase 9 Visualization); burada render-agnostic pass **tanımı** ve CPU
tarafında hesaplanabilecek astronomik/ambient büyüklükler yer alır.
Roadmap V3 - Faz D4 ("Lighting: Tam SPA"): `SolarPositionCalculator.compute`
artık NOAA'nın kaba yaklaşıklaması yerine, Meeus'un "Astronomical
Algorithms" (2. baskı, Böl. 25 "Solar Coordinates" + Böl. 12 "Sidereal
Time" + Böl. 13 "Transformation of Coordinates") kitabındaki düşük-hassas
ama titiz (VSOP87'nin küçük bir alt kümesi yerine kapalı-form seri
açılımı) algoritmayı kullanıyor - 1900-2100 aralığında ekliptik boylamda
tipik ~0.01° hassasiyet (kitabın kendi verdiği doğruluk sınırı).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..core_engine.coordinate_systems import GeoPoint
from ..mesh_engine import Mesh3D, NormalGenerator, Vertex3D

# ======================================================================== #
# Julian Day / Sidereal Time yardımcıları (Meeus, Böl. 7 ve 12)
# ======================================================================== #


def _julian_day(when_utc: datetime) -> float:
    """Meeus Böl. 7: Gregoryen takvimden Julian Day (JD) hesabı."""
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    else:
        when_utc = when_utc.astimezone(timezone.utc)

    year, month = when_utc.year, when_utc.month
    day = when_utc.day + (when_utc.hour + when_utc.minute / 60.0 + when_utc.second / 3600.0) / 24.0
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (year + 4716)) + math.floor(30.6001 * (month + 1)) + day + b - 1524.5


def _greenwich_mean_sidereal_time_deg(jd: float, t: float) -> float:
    """Meeus 12.4: JD'deki Greenwich Ortalama Yıldız Zamanı (derece)."""
    theta0 = (
        280.46061837
        + 360.98564736629 * (jd - 2451545.0)
        + 0.000387933 * t * t
        - (t**3) / 38710000.0
    )
    return theta0 % 360.0


# ======================================================================== #
# Solar Position (Meeus - Astronomical Algorithms, Böl. 25 + 12 + 13)
# ======================================================================== #


@dataclass(slots=True)
class SolarPosition:
    azimuth_deg: float  # 0=Kuzey, 90=Doğu, 180=Güney, 270=Batı
    elevation_deg: float  # ufuk üstü açı (negatifse güneş ufkun altında)

    @property
    def is_daylight(self) -> bool:
        return self.elevation_deg > 0.0

    def direction_vector(self) -> tuple[float, float, float]:
        """Güneşten yere doğru birim vektör (ENU: x=doğu, y=kuzey, z=yukarı)."""
        az = math.radians(self.azimuth_deg)
        el = math.radians(self.elevation_deg)
        x = math.cos(el) * math.sin(az)
        y = math.cos(el) * math.cos(az)
        z = math.sin(el)
        return (x, y, z)


class SolarPositionCalculator:
    """Meeus'un "Astronomical Algorithms" (2. baskı) kitabındaki düşük-hassas
    güneş konumu algoritması. Roadmap V3 / Faz D4: eski NOAA basitleştirmesinin
    (±0.1-0.5° mertebesinde hata) yerini alır; adımlar:

    1. Julian Day (JD) ve Julian yüzyıl T = (JD-2451545)/36525 (Böl. 7).
    2. Güneşin geometrik ortalama boylamı L0, ortalama anomalisi M, Dünya
       yörüngesinin dışmerkezliği e, denklem-merkezi (equation of center) C
       (Böl. 25) - gerçek boylam = L0 + C.
    3. Nütasyon/aberasyon düzeltmesiyle görünür boylam λ (Ω = ayın çıkış
       düğümü boylamı üzerinden basitleştirilmiş düzeltme, Böl. 25).
    4. Ekliptik eğikliği ε (ortalama + nütasyon düzeltmesi, Böl. 22).
    5. Sağ açıklık α ve deklinasyon δ'ya dönüşüm (Böl. 25 sonu).
    6. Greenwich Ortalama Yıldız Zamanı (GMST, Böl. 12) + gözlemci boylamı
       ile yerel saat açısı H.
    7. Ekvatoral -> ufuk (alt-azimut) koordinat dönüşümü (Böl. 13).

    Doğruluk: kitabın kendi belirttiği sınır, 1900-2100 aralığında ekliptik
    boylamda tipik ~0.01° - tam VSOP87 seri açılımı (yüzlerce terim) değil,
    ama NOAA'nın kaba yaklaşıklamasından çok daha yüksek hassasiyette, kapalı
    formda, stdlib-only bir hesap.
    """

    @staticmethod
    def compute(location: GeoPoint, when_utc: datetime) -> SolarPosition:
        if when_utc.tzinfo is None:
            when_utc = when_utc.replace(tzinfo=timezone.utc)
        else:
            when_utc = when_utc.astimezone(timezone.utc)

        jd = _julian_day(when_utc)
        t = (jd - 2451545.0) / 36525.0

        # -- Böl. 25: Güneşin geometrik konumu ---------------------------- #
        l0 = (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0
        m = (357.52911 + t * (35999.05029 - t * 0.0001537)) % 360.0
        m_rad = math.radians(m)

        c = (
            (1.914602 - t * (0.004817 + t * 0.000014)) * math.sin(m_rad)
            + (0.019993 - t * 0.000101) * math.sin(2.0 * m_rad)
            + 0.000289 * math.sin(3.0 * m_rad)
        )
        true_longitude = (l0 + c) % 360.0

        # Nütasyon/aberasyon düzeltmesi (basitleştirilmiş - Ω üzerinden)
        omega = 125.04 - 1934.136 * t
        apparent_longitude = true_longitude - 0.00569 - 0.00478 * math.sin(math.radians(omega))

        # Ekliptik eğikliği (Böl. 22): ortalama + nütasyon düzeltmesi
        eps0_arcsec = (
            23.0 * 3600.0 + 26.0 * 60.0 + 21.448 - 46.8150 * t - 0.00059 * t * t + 0.001813 * (t**3)
        )
        eps0 = eps0_arcsec / 3600.0
        epsilon = eps0 + 0.00256 * math.cos(math.radians(omega))

        lam_rad = math.radians(apparent_longitude)
        eps_rad = math.radians(epsilon)

        right_ascension = (
            math.degrees(math.atan2(math.cos(eps_rad) * math.sin(lam_rad), math.cos(lam_rad)))
            % 360.0
        )
        declination = math.degrees(math.asin(math.sin(eps_rad) * math.sin(lam_rad)))

        # -- Böl. 12/13: yıldız zamanı -> saat açısı -> ufuk koordinatları - #
        gmst = _greenwich_mean_sidereal_time_deg(jd, t)
        local_sidereal_time = (gmst + location.lon) % 360.0
        hour_angle = ((local_sidereal_time - right_ascension + 180.0) % 360.0) - 180.0

        lat_rad = math.radians(location.lat)
        decl_rad = math.radians(declination)
        hour_rad = math.radians(hour_angle)

        elevation = math.asin(
            math.sin(lat_rad) * math.sin(decl_rad)
            + math.cos(lat_rad) * math.cos(decl_rad) * math.cos(hour_rad)
        )

        # Meeus 13.5: azimut, güneyden batıya doğru ölçülür; standart pusula
        # açısına (0=Kuzey, saat yönünde) çevirmek için +180° kaydırılır.
        az_from_south = math.atan2(
            math.sin(hour_rad),
            math.cos(hour_rad) * math.sin(lat_rad) - math.tan(decl_rad) * math.cos(lat_rad),
        )
        azimuth_from_north = (math.degrees(az_from_south) + 180.0) % 360.0

        return SolarPosition(
            azimuth_deg=azimuth_from_north,
            elevation_deg=math.degrees(elevation),
        )


# ======================================================================== #
# Sun / Moon
# ======================================================================== #


@dataclass(slots=True)
class SunLight:
    """Roadmap: 'Sun'. Tek bir directional light kaynağı olarak modellenir;
    yönü `SolarPositionCalculator` ile hesaplanır."""

    color: tuple[float, float, float] = (1.0, 0.98, 0.92)
    intensity_lux: float = 100_000.0
    position: SolarPosition = field(default_factory=lambda: SolarPosition(0.0, 45.0))

    @classmethod
    def at(
        cls,
        location: GeoPoint,
        when_utc: datetime,
        color: tuple[float, float, float] = (1.0, 0.98, 0.92),
    ) -> SunLight:
        pos = SolarPositionCalculator.compute(location, when_utc)
        intensity = 120_000.0 * max(0.0, math.sin(math.radians(max(pos.elevation_deg, 0.0) + 5)))
        return cls(color=color, intensity_lux=intensity, position=pos)

    def direction(self) -> tuple[float, float, float]:
        return self.position.direction_vector()


@dataclass(slots=True)
class MoonLight:
    """Roadmap: 'Moon'. Basitleştirilmiş gece aydınlatma kaynağı - fazı
    (0=yeni ay, 1=dolunay) yoğunluğu doğrusal olarak ölçekler."""

    phase: float = 0.5  # 0..1
    color: tuple[float, float, float] = (0.6, 0.65, 0.85)
    base_intensity_lux: float = 0.25

    def intensity_lux(self) -> float:
        return self.base_intensity_lux * max(0.0, min(1.0, self.phase))


# ======================================================================== #
# HDR Sky
# ======================================================================== #


@dataclass(slots=True)
class HDRSky:
    """Roadmap: 'HDR Sky'. Equirectangular HDR panorama referansı + basit
    prosedürel gökyüzü renk-gradyanı (gerçek HDR dosyası yoksa fallback)."""

    hdri_path: str | None = None
    zenith_color: tuple[float, float, float] = (0.3, 0.5, 0.9)
    horizon_color: tuple[float, float, float] = (0.8, 0.85, 0.9)
    turbidity: float = 2.0  # atmosferik pus/toz yoğunluğu

    def sample_direction(self, elevation_deg: float) -> tuple[float, float, float]:
        """HDRI dosyası yoksa: ufuk-zenit arası lineer renk enterpolasyonu."""
        t = max(0.0, min(1.0, elevation_deg / 90.0))
        return tuple(h + (z - h) * t for h, z in zip(self.horizon_color, self.zenith_color))  # type: ignore[return-value]


# ======================================================================== #
# Dynamic Shadows (render-agnostic pass tanımı)
# ======================================================================== #


@dataclass(slots=True)
class ShadowMapPass:
    """Roadmap: 'Dynamic Shadows'. Gerçek GPU render Phase 9'da; burada
    herhangi bir backend'in (WebGL/Vulkan/Metal) tüketebileceği pass
    parametreleri tanımlanır."""

    light_direction: tuple[float, float, float]
    resolution: int = 2048
    cascade_count: int = 4  # cascaded shadow maps (CSM)
    bias: float = 0.005
    max_distance_m: float = 500.0

    @classmethod
    def from_sun(cls, sun: SunLight, resolution: int = 2048) -> ShadowMapPass:
        return cls(light_direction=sun.direction(), resolution=resolution)


class ShadowCalculator:
    """CPU tarafında basit gölge testi (offline analiz / raporlama için;
    gerçek zamanlı render değil - o Phase 9'a aittir). Phase 6 Analysis
    Engine'deki `ShadowAnalysis` bu sınıfı temel alır."""

    @staticmethod
    def point_in_shadow(
        point: tuple[float, float, float],
        sun: SunLight,
        occluder_meshes: list[Mesh3D],
        max_distance: float = 500.0,
    ) -> bool:
        """Möller–Trumbore ray-triangle intersection ile, noktadan güneşe
        doğru ışın atıp herhangi bir engelleyici üçgene çarpıp çarpmadığını
        kontrol eder."""
        direction = sun.direction()
        # ışın güneşe doğru gitmeli (yerden gökyüzüne)
        ray_dir = direction
        for mesh in occluder_meshes:
            for tri in mesh.triangles:
                a, b, c = mesh.triangle_positions(tri)
                hit = _ray_triangle_intersect(
                    point, ray_dir, a.as_tuple(), b.as_tuple(), c.as_tuple()
                )
                if hit is not None and 1e-4 < hit < max_distance:
                    return True
        return False


def _ray_triangle_intersect(
    origin: tuple[float, float, float],
    direction: tuple[float, float, float],
    v0: tuple[float, float, float],
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
) -> float | None:
    """Möller–Trumbore algoritması. Kesişim varsa ışın parametresi t (mesafe), yoksa None."""
    eps = 1e-9
    edge1 = tuple(v1[i] - v0[i] for i in range(3))
    edge2 = tuple(v2[i] - v0[i] for i in range(3))
    h = _cross3(direction, edge2)
    a = _dot3(edge1, h)
    if -eps < a < eps:
        return None
    f = 1.0 / a
    s = tuple(origin[i] - v0[i] for i in range(3))
    u = f * _dot3(s, h)
    if u < 0.0 or u > 1.0:
        return None
    q = _cross3(s, edge1)
    v = f * _dot3(direction, q)
    if v < 0.0 or u + v > 1.0:
        return None
    t = f * _dot3(edge2, q)
    return t if t > eps else None


def _cross3(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot3(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


# ======================================================================== #
# Ambient Lighting / GI approximation
# ======================================================================== #


@dataclass(slots=True)
class AmbientLight:
    """Roadmap: 'Ambient Lighting'. Sabit/gökyüzü-tabanlı ambient katkı."""

    color: tuple[float, float, float] = (0.5, 0.55, 0.6)
    intensity: float = 0.3

    @classmethod
    def from_sky(cls, sky: HDRSky, intensity: float = 0.3) -> AmbientLight:
        avg = tuple((z + h) / 2.0 for z, h in zip(sky.zenith_color, sky.horizon_color))
        return cls(color=avg, intensity=intensity)  # type: ignore[arg-type]


class AmbientOcclusionBaker:
    """Roadmap: 'Global Illumination approximation'. Tam path-tracing yerine,
    her vertex için komşu geometriye olan yakınlığa dayalı basit bir ambient
    occlusion (AO) yaklaşıklaması - hemisphere ray-sampling."""

    @staticmethod
    def bake_vertex_ao(
        mesh: Mesh3D,
        sample_count: int = 8,
        max_distance: float = 5.0,
        spatial_prune: bool = True,
    ) -> list[float]:
        """Her vertex için 0 (tamamen kapalı/gölgeli) - 1 (tamamen açık) arası
        bir AO değeri döndürür. Küçük/orta mesh'ler için pratik; büyük
        sahnelerde Phase 13 (Performance Engine) GPU compute pipeline'ına
        taşınması önerilir (bkz. SPEC).

        ROADMAP_V8 Faz 6.4: `spatial_prune=True` (varsayılan) iken her üçgen
        için ışın testinden önce ucuz bir bounding-sphere mesafe kontrolü
        yapılır (üçgen merkezi ile vertex arası mesafe - üçgenin yarıçapı >
        max_distance ise Möller-Trumbore'a hiç girilmez). Bu, sonucu
        DEĞİŞTİRMEZ (aynı AO değerleri üretilir), yalnızca bina ölçeğindeki
        mesh'lerde (binlerce üçgen) pratik çalışma süresini sağlar - tam
        brute-force O(vertex*sample*triangle) hâlâ çok büyük sahnelerde
        (Faz 5.8'in şehir ölçeği) yetersiz kalır, o durum için GPU/BVH ayrı
        bir XL iştir ve kasıtlı olarak bu fazın dışında bırakılmıştır."""
        ao_values = []
        tri_cache = None
        if spatial_prune:
            tri_cache = []
            for tri in mesh.triangles:
                a, b, c = mesh.triangle_positions(tri)
                pa, pb, pc = a.as_tuple(), b.as_tuple(), c.as_tuple()
                centroid = tuple((pa[k] + pb[k] + pc[k]) / 3.0 for k in range(3))
                radius = max(_dist3(centroid, pa), _dist3(centroid, pb), _dist3(centroid, pc))
                tri_cache.append((pa, pb, pc, centroid, radius))
        for v in mesh.vertices:
            normal = v.normal or (0.0, 0.0, 1.0)
            occluded = 0
            samples = _hemisphere_samples(normal, sample_count)
            origin = (v.x + normal[0] * 1e-3, v.y + normal[1] * 1e-3, v.z + normal[2] * 1e-3)
            if spatial_prune:
                candidates = [
                    (pa, pb, pc)
                    for (pa, pb, pc, centroid, radius) in tri_cache
                    if _dist3(origin, centroid) - radius <= max_distance
                ]
            else:
                candidates = [
                    (a.as_tuple(), b.as_tuple(), c.as_tuple())
                    for tri in mesh.triangles
                    for a, b, c in (mesh.triangle_positions(tri),)
                ]
            for sample_dir in samples:
                hit_found = False
                for pa, pb, pc in candidates:
                    hit = _ray_triangle_intersect(origin, sample_dir, pa, pb, pc)
                    if hit is not None and hit < max_distance:
                        hit_found = True
                        break
                if hit_found:
                    occluded += 1
            ao_values.append(1.0 - occluded / max(1, sample_count))
        return ao_values

    @staticmethod
    def apply_vertex_ao(
        mesh: Mesh3D,
        sample_count: int = 8,
        max_distance: float = 5.0,
    ) -> Mesh3D:
        """ROADMAP_V8 Faz 6.4: `bake_vertex_ao` sonucunu doğrudan mesh'in
        `Vertex3D.ao` alanına yazar (opt-in, yeni bir `clone()` üzerinde
        çalışır - girdi mesh değiştirilmez). Böylece AO, malzeme/render
        katmanına ayrı bir liste taşımadan mesh'in kendisiyle birlikte akar
        (export, `Scene.to_dict()` gibi tüketiciler tek bir mesh nesnesinden
        okuyabilir)."""
        result = mesh.clone()
        if not result.vertices:
            return result
        if not result.vertices[0].normal:
            NormalGenerator.compute_face_averaged_normals(result)
        ao_values = AmbientOcclusionBaker.bake_vertex_ao(
            result,
            sample_count=sample_count,
            max_distance=max_distance,
        )
        for v, ao in zip(result.vertices, ao_values):
            v.ao = ao
        return result


def _hemisphere_samples(
    normal: tuple[float, float, float], count: int
) -> list[tuple[float, float, float]]:
    """Normal etrafında basit, deterministik (Fibonacci-küre tabanlı)
    hemisphere örnekleme - rastgelelik gerektirmez, tekrarlanabilir."""
    samples = []
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(count):
        t = (i + 0.5) / count
        radius = math.sqrt(1.0 - t * t)
        theta = golden_angle * i
        x, y, z = radius * math.cos(theta), radius * math.sin(theta), t
        # normal'e göre yönlendirme: basit referans-eksen tabanlı rotasyon
        ref = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.999 else (1.0, 0.0, 0.0)
        tangent = _normalize3(_cross3(ref, normal))
        bitangent = _cross3(normal, tangent)
        world = tuple(tangent[k] * x + bitangent[k] * y + normal[k] * z for k in range(3))
        samples.append(world)
    return samples


def _normalize3(v):
    length = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2) or 1e-9
    return (v[0] / length, v[1] / length, v[2] / length)


def _dist3(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


class SpatialHashGrid:
    """ROADMAP_V8 Faz 6.4 (şehir-ölçeği kalan madde): üçgenler için basit
    uniform-grid tabanlı bir uzamsal hash yapısı (gerçek bir BVH değil,
    ama aynı amaca hizmet eden - komşu olmayan geometriyi ışın testinden
    O(1)'e yakın maliyetle eleyen - stdlib-only bir alternatif).

    `AmbientOcclusionBaker.bake_vertex_ao`'nun `spatial_prune` seçeneği
    tek bir mesh içindeki üçgenler için **doğrusal** bir bounding-sphere
    taraması yapıyordu (`O(triangle)` her vertex için) - bina ölçeğinde
    (binlerce üçgen) kabul edilebilir, ama Faz 5.8'in şehir ölçeği
    (onlarca bina, on binlerce üçgen) için pratik değildi (bu, önceki
    oturumların "hâlâ gerçek bir BVH/spatial-hash yok" notuydu). Bu sınıf
    üçgenleri `cell_size` boyutunda küp hücrelere gruplar
    (`(floor(x/cell_size), floor(y/cell_size), floor(z/cell_size))` ->
    üçgen indeksleri); bir sorgu noktası çevresindeki adayları yalnızca
    ilgili hücreler + komşularından toplar, tüm sahneyi taramaz.
    """

    def __init__(self, cell_size: float = 5.0) -> None:
        self.cell_size = max(1e-6, cell_size)
        self._cells: dict[tuple[int, int, int], list[int]] = {}
        self._triangles: list[tuple[tuple, tuple, tuple, tuple, float]] = []

    def _cell_key(self, point: tuple) -> tuple[int, int, int]:
        return (
            math.floor(point[0] / self.cell_size),
            math.floor(point[1] / self.cell_size),
            math.floor(point[2] / self.cell_size),
        )

    def add_triangle(self, pa: tuple, pb: tuple, pc: tuple) -> None:
        centroid = tuple((pa[k] + pb[k] + pc[k]) / 3.0 for k in range(3))
        radius = max(_dist3(centroid, pa), _dist3(centroid, pb), _dist3(centroid, pc))
        idx = len(self._triangles)
        self._triangles.append((pa, pb, pc, centroid, radius))
        # Yarıçapı hücre boyutuna göre kaç hücreye yayıldığını hesapla,
        # üçgeni etkilediği tüm hücrelere ekle (basit AABB-hücre eşlemesi).
        spread = max(1, math.ceil(radius / self.cell_size))
        base = self._cell_key(centroid)
        for dx in range(-spread, spread + 1):
            for dy in range(-spread, spread + 1):
                for dz in range(-spread, spread + 1):
                    key = (base[0] + dx, base[1] + dy, base[2] + dz)
                    self._cells.setdefault(key, []).append(idx)

    def query_near(self, point: tuple, max_distance: float) -> list[tuple]:
        """`point`'e `max_distance` içinde olabilecek üçgen aday listesini
        döndürür (bounding-sphere ile kesin filtrelenmiş - hücre taşması
        nedeniyle bazı uzak adaylar girebilir, bu yüzden çağıran taraf
        yine de mesafe kontrolü yapmalı; `AmbientOcclusionBaker` bunu zaten
        yapıyor)."""
        cell_span = max(1, math.ceil(max_distance / self.cell_size))
        base = self._cell_key(point)
        seen: set[int] = set()
        result = []
        for dx in range(-cell_span, cell_span + 1):
            for dy in range(-cell_span, cell_span + 1):
                for dz in range(-cell_span, cell_span + 1):
                    key = (base[0] + dx, base[1] + dy, base[2] + dz)
                    for idx in self._cells.get(key, ()):
                        if idx in seen:
                            continue
                        seen.add(idx)
                        pa, pb, pc, centroid, radius = self._triangles[idx]
                        if _dist3(point, centroid) - radius <= max_distance:
                            result.append((pa, pb, pc))
        return result


class SceneAOBaker:
    """ROADMAP_V8 Faz 6.4 (şehir-ölçeği kalan madde): birden fazla bina
    mesh'i arasında (komşu bina gölgesi de dahil) ambient occlusion
    hesaplar - `AmbientOcclusionBaker` bina-içi tek mesh ile sınırlıydı,
    bir binanın alt katları komşu bir binanın gölgesinden etkilenmiyordu.

    `SpatialHashGrid` kullanarak sahnedeki **tüm** binaların üçgenlerini
    tek bir uzamsal yapıda birleştirir, sonra her bina için kendi
    vertex'lerinin AO'sunu (yalnızca kendi üçgenleriyle değil, sahnedeki
    tüm yakın üçgenlerle) hesaplar. Opt-in, stdlib-only, mevcut
    `AmbientOcclusionBaker`'ı (tek-mesh API) DEĞİŞTİRMEZ - onun üzerine
    inşa eden ayrı, sahne-ölçeği bir API'dir.
    """

    @staticmethod
    def bake_scene_ao(
        meshes: list[Mesh3D],
        sample_count: int = 8,
        max_distance: float = 5.0,
        cell_size: float | None = None,
    ) -> list[list[float]]:
        """Her mesh için, o mesh'in vertex sırasına denk gelen bir AO
        değer listesi döndürür (sahnedeki tüm binaların üçgenleri dikkate
        alınarak - binalar-arası gölgeleme dahil).

        `cell_size`: `None` ise `max_distance` kullanılır (hücre boyutu
        arama yarıçapıyla aynı büyüklük sınıfında olmalı - çok küçük
        hücre gereksiz çok hücre sorgusu, çok büyük hücre gereksiz aday
        taşması yaratır).
        """
        if not meshes:
            return []
        grid = SpatialHashGrid(cell_size=cell_size or max_distance)
        for mesh in meshes:
            for tri in mesh.triangles:
                a, b, c = mesh.triangle_positions(tri)
                grid.add_triangle(a.as_tuple(), b.as_tuple(), c.as_tuple())

        results: list[list[float]] = []
        for mesh in meshes:
            if not mesh.vertices:
                results.append([])
                continue
            if not mesh.vertices[0].normal:
                NormalGenerator.compute_face_averaged_normals(mesh)
            ao_values = []
            for v in mesh.vertices:
                normal = v.normal or (0.0, 0.0, 1.0)
                samples = _hemisphere_samples(normal, sample_count)
                origin = (
                    v.x + normal[0] * 1e-3,
                    v.y + normal[1] * 1e-3,
                    v.z + normal[2] * 1e-3,
                )
                candidates = grid.query_near(origin, max_distance)
                occluded = 0
                for sample_dir in samples:
                    hit_found = False
                    for pa, pb, pc in candidates:
                        hit = _ray_triangle_intersect(origin, sample_dir, pa, pb, pc)
                        if hit is not None and hit < max_distance:
                            hit_found = True
                            break
                    if hit_found:
                        occluded += 1
                ao_values.append(1.0 - occluded / max(1, sample_count))
            results.append(ao_values)
        return results

    @staticmethod
    def apply_scene_ao(
        meshes: list[Mesh3D],
        sample_count: int = 8,
        max_distance: float = 5.0,
        cell_size: float | None = None,
    ) -> list[Mesh3D]:
        """`bake_scene_ao` sonucunu her mesh'in klonuna (`Vertex3D.ao`)
        yazar - girdi mesh'ler değişmez (`AmbientOcclusionBaker.
        apply_vertex_ao` ile aynı klon-güvenli desen)."""
        if not meshes:
            return []
        clones = [m.clone() for m in meshes]
        for clone in clones:
            if clone.vertices and not clone.vertices[0].normal:
                NormalGenerator.compute_face_averaged_normals(clone)
        ao_lists = SceneAOBaker.bake_scene_ao(
            clones,
            sample_count=sample_count,
            max_distance=max_distance,
            cell_size=cell_size,
        )
        for clone, ao_values in zip(clones, ao_lists):
            for v, ao in zip(clone.vertices, ao_values):
                v.ao = ao
        return clones
