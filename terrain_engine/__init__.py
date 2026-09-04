"""
Terrain Engine
===============

Roadmap Phase 2 - "Terrain Engine" (gerçek arazi üretimi).

Kapsam:
    DEM Import, HeightMap Engine, Terrain Mesh Generator, Adaptive Terrain,
    Terrain Streaming, Terrain LOD, Terrain Chunking.

Girdi: `HeightmapGrid` (DEM/DSM'den). Çıktı: Phase 2 `mesh_engine.Mesh3D`
(grid triangulation) - `core_engine.tile_engine.TileCoordinate` ile aynı
chunk/streaming deseniyle.

Roadmap V3 - Faz D10 ("Terrain Engine: Erozyon/Hidroloji Simülasyonu"):
`ErosionSimulator` (thermal + damla-tabanlı hidrolik erozyon) ve
`FlowAccumulation` (D8 flow-direction + akümülasyon) eklendi - bkz. bu
modülün alt kısmı.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..core_engine.coordinate_systems import GeoPoint
from ..core_engine.tile_engine import TileCoordinate
from ..mesh_engine import Mesh3D, NormalGenerator, UVGenerator, Vertex3D

# ======================================================================== #
# HeightmapGrid (DEM Import / HeightMap Engine)
# ======================================================================== #


@dataclass(slots=True)
class HeightmapGrid:
    """Düzenli aralıklı (regular grid) yükseklik verisi - DEM/DSM'in bellek
    içi temsili."""

    width: int
    height: int
    resolution_m: float
    elevations: list[list[float]]  # [row][col], row 0 = kuzey/üst
    origin: GeoPoint  # sol-üst köşenin coğrafi konumu

    def __post_init__(self) -> None:
        if len(self.elevations) != self.height:
            raise ValueError("elevations satır sayısı 'height' ile eşleşmeli.")
        if self.elevations and len(self.elevations[0]) != self.width:
            raise ValueError("elevations sütun sayısı 'width' ile eşleşmeli.")

    def elevation_at(self, row: int, col: int) -> float:
        row = max(0, min(self.height - 1, row))
        col = max(0, min(self.width - 1, col))
        return self.elevations[row][col]

    def sample_bilinear(self, x_m: float, y_m: float) -> float:
        """Grid içindeki keyfi bir (x, y) metre konumunda bilinear enterpolasyon."""
        col_f = x_m / self.resolution_m
        row_f = y_m / self.resolution_m
        col0, row0 = int(math.floor(col_f)), int(math.floor(row_f))
        col1, row1 = col0 + 1, row0 + 1
        tx, ty = col_f - col0, row_f - row0

        z00 = self.elevation_at(row0, col0)
        z10 = self.elevation_at(row0, col1)
        z01 = self.elevation_at(row1, col0)
        z11 = self.elevation_at(row1, col1)

        z0 = z00 * (1 - tx) + z10 * tx
        z1 = z01 * (1 - tx) + z11 * tx
        return z0 * (1 - ty) + z1 * ty

    def min_max(self) -> tuple[float, float]:
        flat = [z for row in self.elevations for z in row]
        return (min(flat), max(flat)) if flat else (0.0, 0.0)

    def downsample(self, factor: int) -> HeightmapGrid:
        """LOD üretimi için grid'i `factor` oranında küçültür (nearest sampling)."""
        if factor <= 1:
            return self
        new_w = max(1, self.width // factor)
        new_h = max(1, self.height // factor)
        new_elev = [
            [
                self.elevations[min(r * factor, self.height - 1)][min(c * factor, self.width - 1)]
                for c in range(new_w)
            ]
            for r in range(new_h)
        ]
        return HeightmapGrid(
            width=new_w,
            height=new_h,
            resolution_m=self.resolution_m * factor,
            elevations=new_elev,
            origin=self.origin,
        )


class DEMImporter:
    """Roadmap: 'DEM Import'. Ham (row-major) yükseklik dizisinden
    `HeightmapGrid` oluşturur. Gerçek GeoTIFF/ASCII-Grid binary parsing,
    ana projeye eklenecek opsiyonel `rasterio`/`gdal` bağımlılığı ile Phase 13
    performans iterasyonunda genişletilecektir (bkz. SPEC); burada format-
    agnostik, bağımlılıksız çekirdek (flat float matrisinden import) hazırdır.
    """

    @staticmethod
    def from_matrix(
        matrix: list[list[float]], resolution_m: float, origin: GeoPoint
    ) -> HeightmapGrid:
        height = len(matrix)
        width = len(matrix[0]) if height else 0
        return HeightmapGrid(
            width=width, height=height, resolution_m=resolution_m, elevations=matrix, origin=origin
        )

    @staticmethod
    def flat_terrain(
        width: int, height: int, resolution_m: float, elevation: float, origin: GeoPoint
    ) -> HeightmapGrid:
        matrix = [[elevation for _ in range(width)] for _ in range(height)]
        return HeightmapGrid(
            width=width, height=height, resolution_m=resolution_m, elevations=matrix, origin=origin
        )

    @staticmethod
    def synthetic_hills(
        width: int,
        height: int,
        resolution_m: float,
        origin: GeoPoint,
        amplitude: float = 20.0,
        frequency: float = 0.05,
    ) -> HeightmapGrid:
        """Test/demo amaçlı prosedürel arazi (sinüs tabanlı tepe deseni)."""
        matrix = [
            [
                amplitude * 0.5 * (math.sin(c * frequency) + math.cos(r * frequency * 0.8))
                for c in range(width)
            ]
            for r in range(height)
        ]
        return HeightmapGrid(
            width=width, height=height, resolution_m=resolution_m, elevations=matrix, origin=origin
        )


# ======================================================================== #
# Terrain Mesh Generator
# ======================================================================== #


class TerrainMeshGenerator:
    """Roadmap: 'Terrain Mesh Generator'. Grid'i düzenli üçgen ağa (regular
    grid triangulation) çevirir."""

    @staticmethod
    def generate(grid: HeightmapGrid, name: str = "terrain") -> Mesh3D:
        vertices: list[Vertex3D] = []
        for r in range(grid.height):
            for c in range(grid.width):
                x = c * grid.resolution_m
                y = r * grid.resolution_m
                z = grid.elevations[r][c]
                vertices.append(
                    Vertex3D(x, y, z, uv=(c / max(1, grid.width - 1), r / max(1, grid.height - 1)))
                )

        triangles = []
        for r in range(grid.height - 1):
            for c in range(grid.width - 1):
                i00 = r * grid.width + c
                i10 = r * grid.width + (c + 1)
                i01 = (r + 1) * grid.width + c
                i11 = (r + 1) * grid.width + (c + 1)
                triangles.append((i00, i10, i11))
                triangles.append((i00, i11, i01))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh


# ======================================================================== #
# Adaptive Terrain - quadtree tabanlı LOD subdivision
# ======================================================================== #


@dataclass
class QuadNode:
    row0: int
    col0: int
    row1: int
    col1: int
    children: list[QuadNode] = field(default_factory=list)

    def is_leaf(self) -> bool:
        return not self.children

    def size(self) -> int:
        return max(self.row1 - self.row0, self.col1 - self.col0)


class AdaptiveTerrain:
    """Roadmap: 'Adaptive Terrain'. `core_engine.geometry_engine` ile aynı
    ruhta - grid'i, yükseklik varyansına göre uyarlamalı olarak quadtree
    şeklinde böler: düz alanlar az üçgenle, engebeli alanlar yüksek
    çözünürlükle temsil edilir (roadmap: 'Terrain LOD' ile birleşik çalışır)."""

    def __init__(self, grid: HeightmapGrid, variance_threshold: float = 1.0, min_cell: int = 2):
        self.grid = grid
        self.variance_threshold = variance_threshold
        self.min_cell = min_cell
        self.root = self._build(0, 0, grid.height - 1, grid.width - 1)

    def _elevation_variance(self, row0: int, col0: int, row1: int, col1: int) -> float:
        values = [
            self.grid.elevations[r][c] for r in range(row0, row1 + 1) for c in range(col0, col1 + 1)
        ]
        if not values:
            return 0.0
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    def _build(self, row0: int, col0: int, row1: int, col1: int) -> QuadNode:
        node = QuadNode(row0, col0, row1, col1)
        if node.size() <= self.min_cell:
            return node
        variance = self._elevation_variance(row0, col0, row1, col1)
        if variance <= self.variance_threshold:
            return node
        mid_r = (row0 + row1) // 2
        mid_c = (col0 + col1) // 2
        node.children = [
            self._build(row0, col0, mid_r, mid_c),
            self._build(row0, mid_c, mid_r, col1),
            self._build(mid_r, col0, row1, mid_c),
            self._build(mid_r, mid_c, row1, col1),
        ]
        return node

    def leaves(self) -> list[QuadNode]:
        result: list[QuadNode] = []

        def _walk(node: QuadNode) -> None:
            if node.is_leaf():
                result.append(node)
            else:
                for child in node.children:
                    _walk(child)

        _walk(self.root)
        return result

    def to_mesh(self, name: str = "adaptive_terrain") -> Mesh3D:
        """Her yaprak hücreyi tek bir quad (2 üçgen) olarak temsil eder -
        düz bölgelerde çok daha az üçgen üretir."""
        vertices: list[Vertex3D] = []
        triangles = []
        for leaf in self.leaves():
            corners = [
                (leaf.row0, leaf.col0),
                (leaf.row0, leaf.col1),
                (leaf.row1, leaf.col1),
                (leaf.row1, leaf.col0),
            ]
            base = len(vertices)
            for r, c in corners:
                x = c * self.grid.resolution_m
                y = r * self.grid.resolution_m
                z = self.grid.elevation_at(r, c)
                vertices.append(Vertex3D(x, y, z))
            triangles.append((base, base + 1, base + 2))
            triangles.append((base, base + 2, base + 3))
        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=name)
        NormalGenerator.compute_face_averaged_normals(mesh)
        return mesh


# ======================================================================== #
# Terrain LOD
# ======================================================================== #


class TerrainLOD:
    """Roadmap: 'Terrain LOD'. 4 seviyeli çözünürlük merdiveni:
    full / half / quarter / eighth."""

    LEVELS = {"full": 1, "half": 2, "quarter": 4, "eighth": 8}

    @staticmethod
    def generate_all_levels(grid: HeightmapGrid) -> dict[str, Mesh3D]:
        result = {}
        for level_name, factor in TerrainLOD.LEVELS.items():
            downsampled = grid.downsample(factor)
            result[level_name] = TerrainMeshGenerator.generate(
                downsampled, name=f"terrain_{level_name}"
            )
        return result

    @staticmethod
    def select_level(distance_to_camera_m: float) -> str:
        """Basit mesafe eşiğine dayalı LOD seçimi (Phase 13 Performance Engine
        `LODManager` tarafından da kullanılan ortak sözleşme)."""
        if distance_to_camera_m < 200:
            return "full"
        if distance_to_camera_m < 800:
            return "half"
        if distance_to_camera_m < 2000:
            return "quarter"
        return "eighth"


# ======================================================================== #
# Terrain Streaming / Chunking
# ======================================================================== #


@dataclass(slots=True)
class TerrainChunk:
    tile: TileCoordinate
    grid: HeightmapGrid
    mesh: Mesh3D | None = None


class TerrainChunking:
    """Roadmap: 'Terrain Chunking'. Büyük bir `HeightmapGrid`'i,
    `core_engine.tile_engine.TileCoordinate` şemasıyla uyumlu sabit boyutlu
    parçalara böler (streaming'in ön koşulu)."""

    @staticmethod
    def chunk_grid(grid: HeightmapGrid, chunk_size: int, zoom: int) -> list[TerrainChunk]:
        chunks: list[TerrainChunk] = []
        for row0 in range(0, grid.height, chunk_size):
            for col0 in range(0, grid.width, chunk_size):
                row1 = min(row0 + chunk_size, grid.height)
                col1 = min(col0 + chunk_size, grid.width)
                sub_matrix = [row[col0:col1] for row in grid.elevations[row0:row1]]
                sub_origin = GeoPoint(
                    lat=grid.origin.lat,
                    lon=grid.origin.lon,
                    elevation=grid.origin.elevation,
                )
                sub_grid = HeightmapGrid(
                    width=col1 - col0,
                    height=row1 - row0,
                    resolution_m=grid.resolution_m,
                    elevations=sub_matrix,
                    origin=sub_origin,
                )
                tile_x = col0 // chunk_size
                tile_y = row0 // chunk_size
                chunks.append(
                    TerrainChunk(tile=TileCoordinate(z=zoom, x=tile_x, y=tile_y), grid=sub_grid)
                )
        return chunks


class TerrainStreaming:
    """Roadmap: 'Terrain Streaming'. Kamera konumuna göre hangi chunk'ların
    yüklü/boşaltılmış olması gerektiğini yönetir (senkron; async I/O,
    `core_engine.tile_engine.TileEngine`'deki `ThreadPoolExecutor` deseniyle
    Phase 13'te birleştirilir)."""

    def __init__(self, chunks: list[TerrainChunk], load_radius_chunks: int = 2):
        self._chunks_by_coord = {(c.tile.x, c.tile.y): c for c in chunks}
        self.load_radius_chunks = load_radius_chunks
        self.loaded: dict[tuple[int, int], TerrainChunk] = {}

    def update(
        self, camera_chunk_x: int, camera_chunk_y: int
    ) -> tuple[list[TerrainChunk], list[TerrainChunk]]:
        """Kamera etrafındaki chunk'ları yükler, menzil dışındakileri boşaltır.
        Dönen değer: (yeni_yuklenenler, boşaltılanlar)."""
        wanted: set[tuple[int, int]] = set()
        for dx in range(-self.load_radius_chunks, self.load_radius_chunks + 1):
            for dy in range(-self.load_radius_chunks, self.load_radius_chunks + 1):
                key = (camera_chunk_x + dx, camera_chunk_y + dy)
                if key in self._chunks_by_coord:
                    wanted.add(key)

        newly_loaded = []
        for key in wanted:
            if key not in self.loaded:
                chunk = self._chunks_by_coord[key]
                if chunk.mesh is None:
                    chunk.mesh = TerrainMeshGenerator.generate(
                        chunk.grid, name=f"chunk_{key[0]}_{key[1]}"
                    )
                self.loaded[key] = chunk
                newly_loaded.append(chunk)

        unloaded = []
        for key in list(self.loaded.keys()):
            if key not in wanted:
                unloaded.append(self.loaded.pop(key))

        return newly_loaded, unloaded


# ============================================================================ #
# Roadmap V3 - Faz D10: Erozyon / Hidroloji Simülasyonu
# ============================================================================ #


class FlowAccumulation:
    """Roadmap: 'Su birikim haritası (flow accumulation)' - D8 tekil-akış-
    yönü (single-flow-direction, deterministic-8 / D8) algoritması. Her
    hücre için en dik alçalan komşu "akış yönü" olarak seçilir; hücreler
    yükseklik azalan sırada işlenerek her hücrenin biriktirdiği akış,
    kendi akış yönündeki komşuya aktarılır (havza/dere ağı analizinin
    standart ön-adımı - O'Callaghan & Mark, 1984).

    `analysis_engine.environmental_sim.RainSimulation` ile paylaşılabilir
    ortak ara veri yapısı: bu sınıf yalnızca grid-geometrisi bilir,
    yağış/drenaj **miktarı** hesabı `environmental_sim`'in sorumluluğunda
    kalır (roadmap D20 notu)."""

    # 8-komşuluk (satır, sütun) delta'ları + Öklid mesafe çarpanı
    _NEIGHBORS = [
        (-1, -1, math.sqrt(2)),
        (-1, 0, 1.0),
        (-1, 1, math.sqrt(2)),
        (0, -1, 1.0),
        (0, 1, 1.0),
        (1, -1, math.sqrt(2)),
        (1, 0, 1.0),
        (1, 1, math.sqrt(2)),
    ]

    @classmethod
    def flow_directions(cls, grid: HeightmapGrid) -> list[list[tuple[int, int] | None]]:
        """Her hücre için en dik alçalan komşunun (dr, dc) delta'sını
        döndürür; hiçbir komşu daha alçak değilse (yerel çukur/havza
        ağzı) `None`."""
        directions: list[list[tuple[int, int] | None]] = [
            [None for _ in range(grid.width)] for _ in range(grid.height)
        ]
        for r in range(grid.height):
            for c in range(grid.width):
                z = grid.elevations[r][c]
                best_slope = 0.0
                best_delta: tuple[int, int] | None = None
                for dr, dc, dist in cls._NEIGHBORS:
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < grid.height and 0 <= nc < grid.width):
                        continue
                    nz = grid.elevations[nr][nc]
                    slope = (z - nz) / (dist * grid.resolution_m)
                    if slope > best_slope:
                        best_slope = slope
                        best_delta = (dr, dc)
                directions[r][c] = best_delta
        return directions

    @classmethod
    def accumulate(cls, grid: HeightmapGrid) -> list[list[float]]:
        """Her hücrenin akümüle akışını (birim: kendi hücresi + kendisine
        akan tüm yukarı-havza hücrelerinin toplamı) hesaplar. Hücreler
        yükseklik **azalan** sırada işlenir - bu, bir hücrenin akışının
        aşağı-akış komşusuna aktarılmadan önce kendi üzerine akan tüm
        yukarı-akış katkılarının tamamlanmış olmasını garantiler (D8'in
        standart topolojik-sıra çözümü, tam bir DAG-toposort'a eşdeğer
        çünkü akış grafiği çevrimsizdir: her hücre yalnızca kendinden
        alçak bir komşuya akar)."""
        directions = cls.flow_directions(grid)
        accum = [[1.0 for _ in range(grid.width)] for _ in range(grid.height)]

        cells = [(r, c) for r in range(grid.height) for c in range(grid.width)]
        cells.sort(key=lambda rc: grid.elevations[rc[0]][rc[1]], reverse=True)

        for r, c in cells:
            delta = directions[r][c]
            if delta is None:
                continue
            nr, nc = r + delta[0], c + delta[1]
            accum[nr][nc] += accum[r][c]
        return accum


@dataclass(slots=True)
class ErosionResult:
    grid: HeightmapGrid
    iterations: int
    total_material_moved: float


class ErosionSimulator:
    """Roadmap: 'ErosionSimulator' - basit thermal erosion + damla-tabanlı
    (droplet-based) hidrolik erozyon. Girdi/çıktı her zaman `HeightmapGrid`
    (mevcut `TerrainMeshGenerator`/`AdaptiveTerrain`/`TerrainLOD` API'leri
    değişmeden bu çıktı üzerinde çalışmaya devam eder - geriye uyumlu)."""

    # ------------------------------------------------------------------ #
    # Thermal erosion
    # ------------------------------------------------------------------ #

    @staticmethod
    def thermal_erosion(
        grid: HeightmapGrid,
        iterations: int = 20,
        talus_angle_deg: float = 35.0,
        transfer_rate: float = 0.5,
    ) -> ErosionResult:
        """Her yinelemede, her hücrenin komşularıyla eğim farkı `talus_angle`
        (dinlenme açısı - malzemenin kayma olmadan durabileceği maksimum
        eğim) eşiğini aştığında, fazla malzemenin bir kısmı en dik alçalan
        komşuya aktarılır. Bu, kum/toprak yığınlarının zamanla "dinlenme
        açısına" yakınsamasının basitleştirilmiş fiziksel modelidir
        (bilgisayar grafiğinde "thermal weathering" olarak bilinir,
        örn. Musgrave et al. 1989)."""
        elevations = [row[:] for row in grid.elevations]
        talus_slope = math.tan(math.radians(talus_angle_deg))
        total_moved = 0.0

        for _ in range(iterations):
            deltas = [[0.0 for _ in range(grid.width)] for _ in range(grid.height)]
            for r in range(grid.height):
                for c in range(grid.width):
                    z = elevations[r][c]
                    # En dik alçalan komşuyu bul (thermal erosion tek-yönlü
                    # aktarım kullanır - FlowAccumulation ile aynı 8-komşu deseni)
                    best_diff = 0.0
                    best_dist = 1.0
                    best_rc = None
                    for dr, dc, dist in FlowAccumulation._NEIGHBORS:
                        nr, nc = r + dr, c + dc
                        if not (0 <= nr < grid.height and 0 <= nc < grid.width):
                            continue
                        diff = z - elevations[nr][nc]
                        if diff > best_diff:
                            best_diff = diff
                            best_dist = dist
                            best_rc = (nr, nc)
                    if best_rc is None:
                        continue
                    slope = best_diff / (best_dist * grid.resolution_m)
                    if slope <= talus_slope:
                        continue
                    # Eşiği aşan fazla yükseklik farkının bir kısmı taşınır
                    excess = best_diff - talus_slope * best_dist * grid.resolution_m
                    move = excess * transfer_rate * 0.5
                    move = max(0.0, min(move, best_diff * 0.5))
                    deltas[r][c] -= move
                    deltas[best_rc[0]][best_rc[1]] += move
                    total_moved += move

            for r in range(grid.height):
                for c in range(grid.width):
                    elevations[r][c] += deltas[r][c]

        result_grid = HeightmapGrid(
            width=grid.width,
            height=grid.height,
            resolution_m=grid.resolution_m,
            elevations=elevations,
            origin=grid.origin,
        )
        return ErosionResult(
            grid=result_grid, iterations=iterations, total_material_moved=total_moved
        )

    # ------------------------------------------------------------------ #
    # Hydraulic erosion (droplet-based)
    # ------------------------------------------------------------------ #

    @staticmethod
    def hydraulic_erosion(
        grid: HeightmapGrid,
        num_droplets: int = 200,
        max_steps_per_droplet: int = 64,
        seed: int = 12345,
        inertia: float = 0.05,
        capacity_factor: float = 4.0,
        min_slope: float = 0.01,
        erosion_rate: float = 0.3,
        deposition_rate: float = 0.3,
        evaporation_rate: float = 0.02,
        gravity: float = 9.81,
    ) -> ErosionResult:
        """Damla-tabanlı (droplet-based) hidrolik erozyon (Hjulström-benzeri
        basitleştirilmiş taşıma-kapasitesi eşiği; bkz. Hans Theobald Beyer,
        "Implementation of a method for hydraulic erosion", 2015 - grafik
        literatüründe yaygın kullanılan, fizik-tabanlı ama stdlib-only
        uygulanabilir bir yöntem).

        Her damla: rastgele bir başlangıç konumundan, gradyan yönünde
        (bilinear enterpolasyonla) aşağı akar; taşıma kapasitesi hız ve su
        hacmine bağlıdır (`capacity = max(-slope, min_slope) * velocity *
        water * capacity_factor`) - kapasite aşılırsa fazla sediment
        bırakılır (deposition), kapasitenin altındaysa zeminden malzeme
        alınır (erosion). Su, her adımda `evaporation_rate` kadar buharlaşır;
        damla suyu bitince veya harita dışına çıkınca durur.
        """
        elevations = [row[:] for row in grid.elevations]
        rng_state = seed & 0x7FFFFFFF

        def _rand() -> float:
            nonlocal rng_state
            rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
            return rng_state / 0x7FFFFFFF

        def _height_and_gradient(x: float, y: float) -> tuple[float, float, float]:
            """(x, y) grid-hücre koordinatında (metre değil, hücre indeksi
            biriminde) bilinear yükseklik + gradyan (dz/dx, dz/dy)."""
            col0 = max(0, min(grid.width - 2, int(math.floor(x))))
            row0 = max(0, min(grid.height - 2, int(math.floor(y))))
            tx, ty = x - col0, y - row0

            z00 = elevations[row0][col0]
            z10 = elevations[row0][col0 + 1]
            z01 = elevations[row0 + 1][col0]
            z11 = elevations[row0 + 1][col0 + 1]

            grad_x = (z10 - z00) * (1 - ty) + (z11 - z01) * ty
            grad_y = (z01 - z00) * (1 - tx) + (z11 - z10) * tx
            height = (
                z00 * (1 - tx) * (1 - ty)
                + z10 * tx * (1 - ty)
                + z01 * (1 - tx) * ty
                + z11 * tx * ty
            )
            return height, grad_x, grad_y

        def _deposit(x: float, y: float, amount: float) -> None:
            """Bilinear ağırlıklarla 4 komşu hücreye sediment dağıtır."""
            col0 = max(0, min(grid.width - 2, int(math.floor(x))))
            row0 = max(0, min(grid.height - 2, int(math.floor(y))))
            tx, ty = x - col0, y - row0
            elevations[row0][col0] += amount * (1 - tx) * (1 - ty)
            elevations[row0][col0 + 1] += amount * tx * (1 - ty)
            elevations[row0 + 1][col0] += amount * (1 - tx) * ty
            elevations[row0 + 1][col0 + 1] += amount * tx * ty

        def _erode(x: float, y: float, amount: float) -> None:
            """`_deposit`'in tersi - bilinear ağırlıklarla malzeme alır."""
            _deposit(x, y, -amount)

        total_moved = 0.0
        for _ in range(num_droplets):
            pos_x = _rand() * (grid.width - 1)
            pos_y = _rand() * (grid.height - 1)
            dir_x, dir_y = 0.0, 0.0
            speed = 1.0
            water = 1.0
            sediment = 0.0

            for _step in range(max_steps_per_droplet):
                old_x, old_y = pos_x, pos_y
                _, grad_x, grad_y = _height_and_gradient(pos_x, pos_y)

                dir_x = dir_x * inertia - grad_x * (1 - inertia)
                dir_y = dir_y * inertia - grad_y * (1 - inertia)
                dir_len = math.sqrt(dir_x * dir_x + dir_y * dir_y)
                if dir_len < 1e-8:
                    break
                dir_x /= dir_len
                dir_y /= dir_len

                pos_x += dir_x
                pos_y += dir_y
                if not (0 <= pos_x < grid.width - 1 and 0 <= pos_y < grid.height - 1):
                    break

                old_height, _, _ = _height_and_gradient(old_x, old_y)
                new_height, _, _ = _height_and_gradient(pos_x, pos_y)
                height_diff = new_height - old_height

                capacity = max(-height_diff, min_slope) * speed * water * capacity_factor

                if sediment > capacity or height_diff > 0:
                    # Kapasite aşıldı ya da tırmanışta -> biriktir (deposition)
                    deposit_amount = (
                        sediment if height_diff > 0 else (sediment - capacity) * deposition_rate
                    )
                    deposit_amount = max(0.0, min(deposit_amount, sediment))
                    sediment -= deposit_amount
                    _deposit(old_x, old_y, deposit_amount)
                    total_moved += deposit_amount
                else:
                    # Kapasitenin altında -> zeminden malzeme al (erosion)
                    erode_amount = min(
                        (capacity - sediment) * erosion_rate,
                        -height_diff if height_diff < 0 else capacity,
                    )
                    erode_amount = max(0.0, erode_amount)
                    _erode(old_x, old_y, erode_amount)
                    sediment += erode_amount
                    total_moved += erode_amount

                speed = math.sqrt(max(0.0, speed * speed + (-height_diff) * gravity))
                water *= 1.0 - evaporation_rate
                if water < 1e-4:
                    break

        result_grid = HeightmapGrid(
            width=grid.width,
            height=grid.height,
            resolution_m=grid.resolution_m,
            elevations=elevations,
            origin=grid.origin,
        )
        return ErosionResult(
            grid=result_grid, iterations=num_droplets, total_material_moved=total_moved
        )

    # ------------------------------------------------------------------ #
    # Bileşik pipeline
    # ------------------------------------------------------------------ #

    @classmethod
    def simulate(
        cls,
        grid: HeightmapGrid,
        thermal_iterations: int = 10,
        hydraulic_droplets: int = 200,
        seed: int = 12345,
    ) -> ErosionResult:
        """Roadmap D10 kabul kriteri pipeline'ı: önce hidrolik (vadi
        oyma - daha büyük ölçekli, yönlü etki), ardından thermal (keskin
        kenarları dinlenme açısına yumuşatma) uygulanır - bu sıralama
        prosedürel arazi literatüründe (örn. Beyer 2015 + Musgrave 1989
        kombinasyonu) yaygın kullanılan bir desendir."""
        hydraulic = cls.hydraulic_erosion(grid, num_droplets=hydraulic_droplets, seed=seed)
        thermal = cls.thermal_erosion(hydraulic.grid, iterations=thermal_iterations)
        return ErosionResult(
            grid=thermal.grid,
            iterations=thermal.iterations + hydraulic.iterations,
            total_material_moved=thermal.total_material_moved + hydraulic.total_material_moved,
        )
