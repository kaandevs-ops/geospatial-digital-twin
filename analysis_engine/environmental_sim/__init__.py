"""
Environmental Simulation
========================

Roadmap Phase 6 - "Wind/Rain/Flood/Heat Island/Noise/Reflection Simulation".

Bu alt sistemler roadmap'te açıkça "yaklaşık" (Wind: "Yaklaşık CFD") olarak
tanımlanmıştır. Burada gerçek Navier-Stokes/lattice-Boltzmann çözücüleri
yerine, engelleyici geometriyi ve temel fizik prensiplerini (kütle korunumu,
ısı/albedo ilişkisi, mesafeye bağlı ses azalımı, ayna-görüntü yansıması)
dikkate alan **grid-tabanlı, deterministik ve hafif** yaklaşıklamalar
uygulanır. Her sınıfın docstring'inde model varsayımları açıkça belirtilir.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...core_engine.geometry_engine import GeometryEngine, Point2D, Polygon
from ...terrain_engine import HeightmapGrid

# Faz E11 - ThermalComfort ayrı bir dosyada tanımlanır (roadmap'in belirttiği
# hedef dosya: `environmental_sim/thermal_comfort.py`) ama bu paketin genel
# "tek dosya" düzenini bozmamak için `WindSimulation`/`WindField` gibi diğer
# tüm sınıflarla aynı `environmental_sim` isim uzayından da erişilebilir hale
# getirilir - dosyanın sonunda import edilir (bkz. modül sonu), çünkü
# `thermal_comfort.py` bu dosyada TANIMLI `WindField`'a yalnızca tip
# kontrolü (TYPE_CHECKING) seviyesinde referans verir; çalışma zamanı
# bağımlılığı yoktur, dolayısıyla import sırası esasen serbesttir - sona
# konması yalnızca okunabilirlik: "önce bu dosyanın kendi sınıfları, sonra
# alt-modül genişletmeleri" sırasını korur.


# ============================================================================ #
# Wind Simulation (basitleştirilmiş 2D grid akışı)
# ============================================================================ #


@dataclass(slots=True)
class WindField:
    width: int
    height: int
    cell_size_m: float
    speed: list[list[float]]  # m/s, hücre başına skaler hız büyüklüğü
    direction_deg: list[list[float]]  # hücre başına yerel yön (serbest akış + saptırma)

    def at(self, col: int, row: int) -> tuple[float, float]:
        return self.speed[row][col], self.direction_deg[row][col]


class WindSimulation:
    """Roadmap: 'Wind Simulation' - 'Yaklaşık CFD'.

    Model: serbest akış rüzgarı sabit (speed, direction) olarak grid'e
    uygulanır; her engelleyici (bina footprint) hücresi rüzgarı söndürür ve
    downstream'de bir "wake" (rüzgar gölgesi) bölgesi oluşturur. Bu, tam bir
    Navier-Stokes çözümü değil, şehir planlama için kabul edilebilir kaba
    bir yaklaşıklamadır - fakat *adı konmuş* bir ampirik ilişkiye dayanır:

    Roadmap V3 / Faz D3: wake uzunluğu artık **bina yüksekliğiyle orantılı**
    (Wise, 1970'in rüzgar-gölgesi gözlemleriyle uyumlu şekilde, tipik
    downstream wake uzunluğu ~10-15x bina yüksekliği H olarak alınır - bkz.
    Wise, J.J.H. (1970), "Wind effects due to groups of buildings", Building
    Research Establishment). `heights_m` verilirse her engelleyici için bu
    ilişki uygulanır (`wake_length_m = wake_length_factor * height`);
    verilmezse eskisi gibi sabit 10 m varsayılan yükseklik kullanılır
    (geriye uyumlu davranış).
    """

    #: Wise (1970) yaklaşıklamasında tipik downstream wake uzunluğu, bina
    #: yüksekliğinin katı olarak (10-15H aralığının orta değeri).
    WAKE_LENGTH_FACTOR = 12.0

    @staticmethod
    def simulate(
        width: int,
        height: int,
        cell_size_m: float,
        obstacles: list[Polygon],
        free_stream_speed: float,
        free_stream_direction_deg: float,
        grid_origin: tuple[float, float] = (0.0, 0.0),
        heights_m: list[float] | None = None,
    ) -> WindField:
        speed = [[free_stream_speed for _ in range(width)] for _ in range(height)]
        direction = [[free_stream_direction_deg for _ in range(width)] for _ in range(height)]

        rad = math.radians(free_stream_direction_deg)
        dir_x, dir_y = math.sin(rad), math.cos(rad)
        ox, oy = grid_origin

        # Her hücre için: içinde bulunduğu engel (varsa) ve o engelin
        # Wise (1970) ilişkisiyle hesaplanan wake uzunluğu (hücre cinsinden).
        obstacle_mask = [[False] * width for _ in range(height)]
        obstacle_wake_cells: list[list[int]] = [[0] * width for _ in range(height)]
        default_height_m = 10.0
        for row in range(height):
            for col in range(width):
                p = Point2D(ox + col * cell_size_m, oy + row * cell_size_m)
                for idx, poly in enumerate(obstacles):
                    if GeometryEngine.point_in_polygon(p, poly):
                        obstacle_mask[row][col] = True
                        h = (
                            heights_m[idx]
                            if heights_m is not None and idx < len(heights_m)
                            else default_height_m
                        )
                        wake_length_m = WindSimulation.WAKE_LENGTH_FACTOR * max(h, 0.1)
                        obstacle_wake_cells[row][col] = max(
                            3, int(wake_length_m / max(cell_size_m, 0.1))
                        )
                        break

        # En uzun wake mesafesi kadar geriye (upstream) taransın - farklı
        # bina yükseklikleri farklı wake uzunlukları üretebildiğinden, tarama
        # mesafesi sahnedeki en yüksek bina baz alınarak belirlenir.
        max_wake_cells = max((c for row in obstacle_wake_cells for c in row if c > 0), default=0)

        for row in range(height):
            for col in range(width):
                if obstacle_mask[row][col]:
                    speed[row][col] = 0.0
                    continue
                # Rüzgar-yukarısı (upstream) yönde bir engel var mı taransın;
                # varsa, o engelin kendi wake uzunluğuna göre üstel sönüm
                # uygula (Wise 1970 tipi wake-decay yaklaşıklaması).
                min_dist_cells = None
                source_wake_cells = 0
                for step in range(1, max_wake_cells + 1):
                    src_col = col - round(dir_x * step)
                    src_row = row - round(dir_y * step)
                    if 0 <= src_row < height and 0 <= src_col < width:
                        if obstacle_mask[src_row][src_col]:
                            min_dist_cells = step
                            source_wake_cells = obstacle_wake_cells[src_row][src_col]
                            break
                if min_dist_cells is not None and min_dist_cells <= source_wake_cells:
                    damping = 1.0 - math.exp(-min_dist_cells / max(source_wake_cells / 3, 1))
                    speed[row][col] = free_stream_speed * damping
        return WindField(
            width=width,
            height=height,
            cell_size_m=cell_size_m,
            speed=speed,
            direction_deg=direction,
        )


# ============================================================================ #
# Rain Simulation
# ============================================================================ #


@dataclass(slots=True)
class RainRunoffResult:
    accumulation: list[list[float]]  # mm, hücre başına biriken yağmur suyu
    flow_direction: list[list[tuple[int, int]]]  # her hücrenin akış yönü (dcol, drow)


class RainSimulation:
    """Roadmap: 'Rain Simulation'. D8 akış yönü algoritması (her hücre,
    8 komşusundan en dik iniş eğimine sahip olana suyunu akıtır) + basit
    üniform yağış girdisiyle akümülasyon (tek geçişli, iteratif olmayan
    yaklaşıklama - gerçek hidrolojik modellerin basitleştirilmiş hali)."""

    _NEIGHBORS = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

    @classmethod
    def simulate(cls, grid: HeightmapGrid, rainfall_mm: float) -> RainRunoffResult:
        width, height = grid.width, grid.height
        flow_direction = [[(0, 0) for _ in range(width)] for _ in range(height)]
        accumulation = [[rainfall_mm for _ in range(width)] for _ in range(height)]

        # D8: her hücre için en dik komşuyu bul.
        order: list[tuple[int, int, float]] = []
        for row in range(height):
            for col in range(width):
                z = grid.elevation_at(row, col)
                best_drop = 0.0
                best_dir = (0, 0)
                for dcol, drow in cls._NEIGHBORS:
                    nc, nr = col + dcol, row + drow
                    if 0 <= nr < height and 0 <= nc < width:
                        nz = grid.elevation_at(nr, nc)
                        dist = math.hypot(dcol, drow) * grid.resolution_m
                        drop = (z - nz) / dist if dist > 0 else 0.0
                        if drop > best_drop:
                            best_drop = drop
                            best_dir = (dcol, drow)
                flow_direction[row][col] = best_dir
                order.append((row, col, z))

        # Yüksekten alçağa doğru işleyerek suyu aşağı akıt (basit akümülasyon).
        order.sort(key=lambda t: -t[2])
        for row, col, _ in order:
            dcol, drow = flow_direction[row][col]
            if dcol == 0 and drow == 0:
                continue  # çukur (sink) - su birikir
            nr, nc = row + drow, col + dcol
            if 0 <= nr < height and 0 <= nc < width:
                accumulation[nr][nc] += accumulation[row][col] * 0.9  # %10 sızma/buharlaşma kaybı

        return RainRunoffResult(accumulation=accumulation, flow_direction=flow_direction)


# ============================================================================ #
# Flood Estimation
# ============================================================================ #


@dataclass(slots=True)
class FloodResult:
    flooded: list[list[bool]]
    water_level_m: float
    flooded_cell_count: int

    def flooded_area_m2(self, cell_size_m: float) -> float:
        return self.flooded_cell_count * (cell_size_m**2)


class FloodEstimation:
    """Roadmap: 'Flood Estimation'. DEM + su seviyesi -> flood-fill.

    Model: verilen `water_level_m` altındaki ve harita kenarından (veya
    verilen kaynak hücrelerden) bağlantılı (4-komşuluk BFS) hücreler
    "su altında" kabul edilir - kapalı çukurlar (dışarıyla bağlantısı
    olmayan alçak alanlar) taşkın kaynağı belirtilmedikçe su almaz, bu da
    gerçekçi bir "dıştan gelen taşkın" senaryosunu modeller.
    """

    @staticmethod
    def estimate(
        grid: HeightmapGrid, water_level_m: float, source_cells: list[tuple[int, int]] | None = None
    ) -> FloodResult:
        width, height = grid.width, grid.height
        flooded = [[False] * width for _ in range(height)]

        if source_cells is None:
            # Varsayılan kaynak: haritanın dört kenarındaki, su seviyesinin
            # altındaki hücreler (dış taşkın senaryosu).
            source_cells = []
            for col in range(width):
                source_cells.append((0, col))
                source_cells.append((height - 1, col))
            for row in range(height):
                source_cells.append((row, 0))
                source_cells.append((row, width - 1))

        queue: list[tuple[int, int]] = []
        for row, col in source_cells:
            if 0 <= row < height and 0 <= col < width:
                if grid.elevation_at(row, col) <= water_level_m and not flooded[row][col]:
                    flooded[row][col] = True
                    queue.append((row, col))

        head = 0
        while head < len(queue):
            row, col = queue[head]
            head += 1
            for drow, dcol in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nr, nc = row + drow, col + dcol
                if 0 <= nr < height and 0 <= nc < width and not flooded[nr][nc]:
                    if grid.elevation_at(nr, nc) <= water_level_m:
                        flooded[nr][nc] = True
                        queue.append((nr, nc))

        count = sum(1 for row in flooded for v in row if v)
        return FloodResult(flooded=flooded, water_level_m=water_level_m, flooded_cell_count=count)


# ============================================================================ #
# Heat Island Simulation
# ============================================================================ #


@dataclass(slots=True)
class HeatIslandResult:
    temperature_delta: list[list[float]]  # ambient'e göre fark, °C
    average_delta: float


class HeatIslandSimulation:
    """Roadmap: 'Heat Island Simulation'. Yüzey malzeme albedo bazlı ısı
    haritası: düşük albedo (asfalt, koyu çatı) -> daha fazla ısı emilimi ->
    yüksek sıcaklık artışı; yüksek albedo (yeşil alan, açık renk, su) ->
    serinletici etki. Basit doğrusal model: delta_T = k * (1 - albedo) *
    solar_irradiance_factor - yeşil_alan_serinletme_katkısı."""

    # Roadmap'in bahsettiği yüzey tiplerine göre tipik albedo değerleri.
    DEFAULT_ALBEDO = {
        "asfalt": 0.05,
        "beton": 0.30,
        "cati_koyu": 0.10,
        "cati_acik": 0.55,
        "cam": 0.20,
        "yesil_alan": 0.25,
        "su": 0.06,
        "toprak": 0.17,
    }

    GREEN_COOLING_C = {"yesil_alan": -2.5, "su": -3.5}

    @classmethod
    def simulate(
        cls, surface_grid: list[list[str]], irradiance_factor: float = 1.0, k: float = 8.0
    ) -> HeatIslandResult:
        height = len(surface_grid)
        width = len(surface_grid[0]) if height else 0
        delta = [[0.0 for _ in range(width)] for _ in range(height)]
        total = 0.0
        count = 0
        for row in range(height):
            for col in range(width):
                surface = surface_grid[row][col]
                albedo = cls.DEFAULT_ALBEDO.get(surface, 0.2)
                heat = k * (1.0 - albedo) * irradiance_factor
                heat += cls.GREEN_COOLING_C.get(surface, 0.0)
                delta[row][col] = heat
                total += heat
                count += 1
        avg = total / count if count else 0.0
        return HeatIslandResult(temperature_delta=delta, average_delta=avg)


# ============================================================================ #
# Noise Simulation
# ============================================================================ #


@dataclass(slots=True)
class NoiseResult:
    spl_db: float  # ses basınç seviyesi (dB SPL), kaynak + azalım + kırınım sonrası


class NoiseSimulation:
    """Roadmap: 'Noise Simulation'.

    Roadmap V3 / Faz D3: sönümleme artık ISO 9613-2 ("Acoustics -
    Attenuation of sound during propagation outdoors") standardının
    basitleştirilmiş toplam sönümleme formülüne göre iki terime ayrılır:

        Lp = Lw - A_div - A_atm  (- A_engel, görüş hattı kesikse)

    - `A_div` (geometric divergence / küresel yayılım): nokta kaynak için
      `20*log10(d/d0)` - inverse-square-law'ın dB karşılığı, standardın
      "Adiv" terimiyle birebir örtüşür.
    - `A_atm` (atmospheric absorption / atmosferik soğurma): mesafeyle
      **doğrusal** artan, standardın "Aatm = α·d/1000" terimi (`α`,
      dB/km cinsinden frekans/sıcaklık/nem'e bağlı soğurma katsayısıdır;
      burada tipik bir orta-frekans değeri, `atmospheric_absorption_db_per_km`
      parametresiyle, varsayılan olarak kullanılır - tam ISO 9613-1 frekans
      tablosu yerine tek bir temsili katsayı, roadmap'in 'basitleştirilmiş'
      kapsamına uygun).
    - Engelleyici (bina) nedeniyle görüş hattı kesikse, standardın "Abar"
      (bariyer) terimi yerine basit sabit bir ek zayıflama uygulanır (tam
      dalga-kırınımı hesaplanmaz - roadmap'te açıkça 'yaklaşıklama' olarak
      belirtilmiştir).
    """

    DIFFRACTION_LOSS_DB = (
        10.0  # görüş hattı (LOS) engellendiğinde ek zayıflama (Abar yaklaşıklaması)
    )

    #: ISO 9613-1'in frekansa bağlı tablosundaki tipik orta-frekans (~1 kHz),
    #: ~20°C / %70 bağıl nem koşullarındaki mertebeye karşılık gelen temsili
    #: atmosferik soğurma katsayısı (dB/km). Roadmap'in 'basitleştirilmiş'
    #: ISO 9613-2 hedefine uygun tek-katsayılı bir yaklaşıklamadır.
    DEFAULT_ATMOSPHERIC_ABSORPTION_DB_PER_KM = 1.5

    @staticmethod
    def spl_at(
        source_db: float,
        source: tuple[float, float, float],
        receiver: tuple[float, float, float],
        line_of_sight_blocked: bool = False,
        reference_distance_m: float = 1.0,
        atmospheric_absorption_db_per_km: float | None = None,
    ) -> NoiseResult:
        if atmospheric_absorption_db_per_km is None:
            atmospheric_absorption_db_per_km = (
                NoiseSimulation.DEFAULT_ATMOSPHERIC_ABSORPTION_DB_PER_KM
            )

        distance = math.sqrt(sum((source[i] - receiver[i]) ** 2 for i in range(3)))
        distance = max(distance, reference_distance_m)

        a_div = 20.0 * math.log10(distance / reference_distance_m)
        a_atm = atmospheric_absorption_db_per_km * (distance / 1000.0)

        spl = source_db - a_div - a_atm
        if line_of_sight_blocked:
            spl -= NoiseSimulation.DIFFRACTION_LOSS_DB
        return NoiseResult(spl_db=spl)

    @staticmethod
    def combine_sources(results: list[NoiseResult]) -> NoiseResult:
        """Birden fazla kaynağın (bağımsız/uyumsuz) toplam SPL'i - enerji
        (basınç karesi) toplamı ile birleştirilir."""
        if not results:
            return NoiseResult(spl_db=-math.inf)
        total_energy = sum(10 ** (r.spl_db / 10.0) for r in results)
        return NoiseResult(spl_db=10.0 * math.log10(total_energy))


# ============================================================================ #
# Reflection Simulation
# ============================================================================ #


@dataclass(slots=True)
class ReflectionResult:
    reflection_point: tuple[float, float, float]
    total_path_length_m: float
    attenuation_db: float


class ReflectionSimulation:
    """Roadmap: 'Reflection Simulation'. Düz bir yüzeyden (duvar/cephe)
    ses veya sinyal yansımasını, ayna-görüntü (image-source) yöntemiyle
    modeller: kaynak, yüzey düzlemine göre yansıtılır; yansıyan kaynaktan
    alıcıya doğru çizgi, gerçek yansıma noktasını ve toplam yol uzunluğunu
    verir. Her yansımada malzemeye bağlı sabit bir enerji kaybı uygulanır."""

    @staticmethod
    def reflect_point_across_plane(
        point: tuple[float, float, float],
        plane_point: tuple[float, float, float],
        plane_normal: tuple[float, float, float],
    ) -> tuple[float, float, float]:
        n = plane_normal
        n_len = math.sqrt(sum(c * c for c in n))
        n = tuple(c / n_len for c in n) if n_len > 1e-12 else n
        d = sum((point[i] - plane_point[i]) * n[i] for i in range(3))
        return tuple(point[i] - 2 * d * n[i] for i in range(3))

    @classmethod
    def compute(
        cls,
        source: tuple[float, float, float],
        receiver: tuple[float, float, float],
        plane_point: tuple[float, float, float],
        plane_normal: tuple[float, float, float],
        material_absorption_db: float = 3.0,
    ) -> ReflectionResult:
        image_source = cls.reflect_point_across_plane(source, plane_point, plane_normal)

        # Yansıma noktası: image_source'tan receiver'a olan doğru, düzlemi
        # nerede kesiyorsa orasıdır.
        n = plane_normal
        n_len = math.sqrt(sum(c * c for c in n))
        n = tuple(c / n_len for c in n) if n_len > 1e-12 else n
        direction = tuple(receiver[i] - image_source[i] for i in range(3))
        denom = sum(direction[i] * n[i] for i in range(3))
        if abs(denom) < 1e-9:
            t = 0.5
        else:
            numer = sum((plane_point[i] - image_source[i]) * n[i] for i in range(3))
            t = numer / denom
        reflection_point = tuple(image_source[i] + direction[i] * t for i in range(3))

        d1 = math.sqrt(sum((source[i] - reflection_point[i]) ** 2 for i in range(3)))
        d2 = math.sqrt(sum((receiver[i] - reflection_point[i]) ** 2 for i in range(3)))
        total = d1 + d2

        return ReflectionResult(
            reflection_point=reflection_point,
            total_path_length_m=total,
            attenuation_db=20.0 * math.log10(max(total, 1.0)) + material_absorption_db,
        )


# ============================================================================ #
# Air Pollution Dispersion — Gaussian Plume Model
# (Roadmap V3 - Faz D20)
# ============================================================================ #


@dataclass(slots=True)
class PlumeConcentrationResult:
    """Tek bir alıcı noktasındaki (x, y, z) kararlı-durum (steady-state)
    kirletici konsantrasyonu, µg/m³ (kaynak yayım hızıyla aynı kütle
    biriminin hacim başına düşen miktarı)."""

    concentration: float
    sigma_y_m: float
    sigma_z_m: float
    effective_height_m: float


class PasquillGiffordStability:
    """Pasquill-Gifford atmosferik kararlılık sınıfları (A-F).

    A = son derece kararsız, F = son derece kararlı - standart
    meteorolojik/regülasyon literatüründe (örn. ABD EPA, Turner'ın
    "Workbook of Atmospheric Dispersion Estimates", 1970/1994) kullanılan
    altı sınıftan biri. Bu proje yalnızca sınıf harfini bir dizi ampirik
    katsayıya eşler; kararlılık sınıfının kendisinin (güneş ışınımı, bulut
    örtüsü, rüzgar hızından) türetilmesi kapsam dışıdır - çağıran kod
    (örn. `lighting`/`sun_simulation`'dan güneş yüksekliği + rüzgar hızı)
    uygun sınıfı seçip burada geçirir.
    """

    CLASSES = ("A", "B", "C", "D", "E", "F")


class GaussianPlumeSimulation:
    """Roadmap V3 - Faz D20: Hava kirliliği için Gaussian plume modelinin
    basitleştirilmiş bir versiyonu.

    Model (klasik kararlı-durum Gaussian plume denklemi - bkz. Pasquill
    (1961) / Gifford (1961); standart biçimi örn. Turner (1970), "Workbook
    of Atmospheric Dispersion Estimates", ya da Beychok, M.R. (2005),
    "Fundamentals of Stack Gas Dispersion" içinde bulunur):

        C(x, y, z) = Q / (2*pi*u*sigma_y(x)*sigma_z(x))
                     * exp(-y^2 / (2*sigma_y(x)^2))
                     * [ exp(-(z-H)^2 / (2*sigma_z(x)^2))
                       + exp(-(z+H)^2 / (2*sigma_z(x)^2)) ]

    - `Q`: kaynak yayım hızı (kütle/zaman, örn. g/s)
    - `u`: rüzgar hızı (m/s), kaynak yüksekliğinde, x-ekseni yönünde
    - `x`: rüzgar-yönündeki (downwind) mesafe (m) - kaynağın "arkasında"
      (x<=0) tanımsız/sıfır kabul edilir (plume henüz oluşmamıştır)
    - `y`: rüzgara dik (crosswind) yatay mesafe (m)
    - `z`: yerden yükseklik (m)
    - `H`: efektif kaynak yüksekliği (baca yüksekliği + plume kalkışı -
      burada plume kalkışı basitleştirilmiş kapsamda 0 kabul edilir, yalnızca
      geometrik baca yüksekliği kullanılır)
    - İkinci üstel terim (`z+H`), yer yüzeyinde **tam yansıma** (perfect
      reflection) sınır koşulunu temsil eder - literatürün standart
      "image source" yaklaşımı (Pasquill 1961).

    `sigma_y`/`sigma_z` (yatay/dikey dağılım katsayıları), Briggs (1973)
    tarafından tablolanmış kırsal/kentsel Pasquill-Gifford ampirik
    formülleriyle hesaplanır (bkz. `_SIGMA_Y_RURAL`/`_SIGMA_Z_RURAL`
    katsayı tabloları) - `sigma = a*x / (1 + b*x)^c` biçiminde, `x` km
    cinsinden.
    """

    #: Briggs (1973) kırsal (rural) sigma-y katsayıları: sigma_y(x) = a*x*(1+b*x)^-0.5,
    #: x metre cinsinden. Kaynak: Briggs, G.A. (1973), "Diffusion Estimation
    #: for Small Emissions", ATDL Contribution File No. 79, NOAA.
    _SIGMA_Y_RURAL: dict[str, tuple[float, float]] = {
        "A": (0.22, 0.0001),
        "B": (0.16, 0.0001),
        "C": (0.11, 0.0001),
        "D": (0.08, 0.0001),
        "E": (0.06, 0.0001),
        "F": (0.04, 0.0001),
    }
    #: sigma_z(x) = a*x*(1+b*x)^c biçiminde (a, b, c) katsayıları (kırsal).
    _SIGMA_Z_RURAL: dict[str, tuple[float, float, float]] = {
        "A": (0.20, 0.0, 1.0),
        "B": (0.12, 0.0, 1.0),
        "C": (0.08, 0.0002, -0.5),
        "D": (0.06, 0.0015, -0.5),
        "E": (0.03, 0.0003, -1.0),
        "F": (0.016, 0.0003, -1.0),
    }

    @classmethod
    def dispersion_coefficients(cls, x_m: float, stability_class: str) -> tuple[float, float]:
        """Briggs (1973) kırsal formülleriyle `sigma_y`, `sigma_z` (metre).

        `x_m <= 0` için (kaynağın rüzgar-üstü tarafı ya da tam kaynak
        konumu) plume henüz gelişmediğinden `(0.0, 0.0)` döner - çağıran
        kod bunu `concentration_at`'te sıfır konsantrasyon olarak yorumlar.
        """
        if stability_class not in cls._SIGMA_Y_RURAL:
            raise ValueError(
                f"Bilinmeyen kararlılık sınıfı: {stability_class!r} "
                f"(beklenen: {PasquillGiffordStability.CLASSES})"
            )
        if x_m <= 0:
            return 0.0, 0.0
        a_y, b_y = cls._SIGMA_Y_RURAL[stability_class]
        sigma_y = a_y * x_m * (1.0 + b_y * x_m) ** -0.5

        a_z, b_z, c_z = cls._SIGMA_Z_RURAL[stability_class]
        sigma_z = a_z * x_m * (1.0 + b_z * x_m) ** c_z
        return sigma_y, sigma_z

    @classmethod
    def concentration_at(
        cls,
        *,
        emission_rate: float,
        wind_speed_mps: float,
        stack_height_m: float,
        stability_class: str,
        downwind_x_m: float,
        crosswind_y_m: float,
        receptor_height_m: float = 0.0,
    ) -> PlumeConcentrationResult:
        """Tek bir noktadaki kararlı-durum konsantrasyonu hesaplar.

        `wind_speed_mps <= 0` (sakin/rüzgarsız hava) fiziksel olarak
        tanımsızdır (plume modeli sıfır rüzgarda geçerli değildir - model
        varsayımlarından biri sabit yönlü bir taşıyıcı akış olmasıdır);
        bu durumda `ValueError` fırlatılır (sessizce yanlış/sonsuz bir
        sayı üretmek yerine).
        """
        if wind_speed_mps <= 0:
            raise ValueError(
                "Gaussian plume modeli rüzgar hızı > 0 gerektirir (sakin hava tanımsız)."
            )
        if downwind_x_m <= 0:
            return PlumeConcentrationResult(
                concentration=0.0,
                sigma_y_m=0.0,
                sigma_z_m=0.0,
                effective_height_m=stack_height_m,
            )

        sigma_y, sigma_z = cls.dispersion_coefficients(downwind_x_m, stability_class)
        if sigma_y <= 0 or sigma_z <= 0:
            return PlumeConcentrationResult(
                concentration=0.0,
                sigma_y_m=sigma_y,
                sigma_z_m=sigma_z,
                effective_height_m=stack_height_m,
            )

        H = stack_height_m
        z = receptor_height_m
        crosswind_term = math.exp(-(crosswind_y_m**2) / (2.0 * sigma_y**2))
        vertical_term = math.exp(-((z - H) ** 2) / (2.0 * sigma_z**2)) + math.exp(
            -((z + H) ** 2) / (2.0 * sigma_z**2)
        )
        prefactor = emission_rate / (2.0 * math.pi * wind_speed_mps * sigma_y * sigma_z)
        concentration = prefactor * crosswind_term * vertical_term

        return PlumeConcentrationResult(
            concentration=concentration,
            sigma_y_m=sigma_y,
            sigma_z_m=sigma_z,
            effective_height_m=H,
        )

    @classmethod
    def ground_level_centerline_concentration(
        cls,
        *,
        emission_rate: float,
        wind_speed_mps: float,
        stack_height_m: float,
        stability_class: str,
        downwind_x_m: float,
    ) -> float:
        """Yer-seviyesi (`z=0`), merkez-hattı (`y=0`) konsantrasyonu için
        kapalı-form kısaltılmış formül:

            C(x, 0, 0) = Q / (pi*u*sigma_y*sigma_z) * exp(-H^2 / (2*sigma_z^2))

        Bu, `concentration_at(y=0, z=0)`'ın cebirsel olarak sadeleştirilmiş
        hâlidir (`z=0` için iki dikey terim özdeş hâle gelip `2*` çarpanı
        `1/pi`'ye sadeleşir) - roadmap D20 kabul kriterindeki "formülün
        kendi analitik çözümü" karşılaştırması için ayrı, bağımsız bir kod
        yolu olarak sağlanır.
        """
        if wind_speed_mps <= 0:
            raise ValueError(
                "Gaussian plume modeli rüzgar hızı > 0 gerektirir (sakin hava tanımsız)."
            )
        if downwind_x_m <= 0:
            return 0.0
        sigma_y, sigma_z = cls.dispersion_coefficients(downwind_x_m, stability_class)
        if sigma_y <= 0 or sigma_z <= 0:
            return 0.0
        H = stack_height_m
        return (
            emission_rate
            / (math.pi * wind_speed_mps * sigma_y * sigma_z)
            * math.exp(-(H**2) / (2.0 * sigma_z**2))
        )


# ============================================================================ #
# ROADMAP_V4 - Faz E11: Termal Konfor Endeksi (bkz. thermal_comfort.py)
# ============================================================================ #

from .thermal_comfort import ThermalComfort, ThermalComfortResult  # noqa: E402
