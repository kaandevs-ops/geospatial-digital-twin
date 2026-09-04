"""
Point Cloud (LAS/LAZ)
======================

ROADMAP_V4 — Track E / Faz E4: Core Engine'e LiDAR/nokta bulutu desteği.

`core_engine/gis_core/__init__.py` 7 vektör/raster/mesh formatını
(Shapefile, KML, KMZ, GeoPackage, DXF, Mesh3D, Heightmap) destekliyordu ama
modern 3D şehir tarama/rekonstrüksiyon iş akışlarının standart girdi
formatı olan LiDAR nokta bulutu (LAS/LAZ, ASPRS) hiç yoktu. Bu modül onu
ekler:

    - **LAS** (sıkıştırılmamış): tamamen stdlib `struct` ile, ASPRS LAS
      1.2/1.3/1.4 public header'ı + Point Data Record Format 0/1/2/3
      (baseline XYZ + intensity + classification + opsiyonel GPS time/RGB)
      okunur. Format 4+ (waveform) ve 6-10 (LAS 1.4 genişletilmiş,
      ek bit alanları) bilinçli olarak kapsam dışıdır — `LASParseError`
      ile açıkça reddedilir (bkz. `LASPointCloudParser.SUPPORTED_POINT_FORMATS`).
    - **LAZ** (sıkıştırılmış): opsiyonel `laspy` + `lazrs`/`laszip`
      bağımlılığı (`pyproject.toml` `[cloud]` extra'sı) üzerinden; kurulu
      değilse içe aktarma anında değil, **çağrı zamanında** açık
      `UnsupportedFormatError` fırlatılır (roadmap'in "sessizce mevcut
      davranışa düş" ilkesiyle tutarlı — burada düşülecek bir "mevcut
      davranış" yok, açıkça reddedilir).

`PointCloud` → `terrain_engine.HeightmapGrid` gridleme köprüsü
(`PointCloud.to_heightmap_grid`) ile nokta bulutundan basit bir yüzey
yeniden yapılandırması, mevcut `TerrainMeshGenerator`'ı hiç değiştirmeden
yeniden kullanılabilir hale gelir (E3'ün de bağımlı olabileceği bir temel).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from .. import coordinate_systems as _cs

GeoPoint = _cs.GeoPoint


class LASParseError(ValueError):
    """Bozuk/desteklenmeyen bir LAS dosyası ayrıştırılırken fırlatılır
    (diğer `gis_core` parser'larıyla aynı `GISParseError` deseninin
    LAS'a özel karşılığı — döngüsel import'tan kaçınmak için burada
    ayrı bir `ValueError` alt sınıfı olarak tanımlanır; `gis_core.
    GISParseError` ile birebir aynı sözleşmeyi taşır)."""


class UnsupportedFormatError(LASParseError):
    """Dosya sözdizimsel olarak tanınıyor (LAS imzası doğru) ama bu
    derlemede işlenemiyor — sıkıştırılmış LAZ ve `laspy` kurulu değil,
    veya desteklenmeyen bir Point Data Format (>= 4) kullanılmış."""


@dataclass
class PointCloudBounds:
    """Bir nokta bulutunun eksen-hizalı sınır kutusu (dosya header'ından
    doğrudan okunur — LAS zaten min/max XYZ'yi header'da taşır)."""

    min_x: float
    min_y: float
    min_z: float
    max_x: float
    max_y: float
    max_z: float

    def size(self) -> tuple[float, float, float]:
        return (self.max_x - self.min_x, self.max_y - self.min_y, self.max_z - self.min_z)


@dataclass
class PointCloud:
    """Bellek içi nokta bulutu temsili — format-agnostik (LAS/LAZ'dan
    üretilir, ama tüketiciler yalnızca bu sınıfı görür)."""

    points: list[tuple[float, float, float]]
    bounds: PointCloudBounds
    point_format: int
    version: tuple[int, int]
    intensity: list[int] | None = None
    classification: list[int] | None = None
    rgb: list[tuple[int, int, int]] | None = None
    gps_time: list[float] | None = None
    source_file: str | None = None

    def __len__(self) -> int:
        return len(self.points)

    def __post_init__(self) -> None:
        n = len(self.points)
        for name, seq in (
            ("intensity", self.intensity),
            ("classification", self.classification),
            ("rgb", self.rgb),
            ("gps_time", self.gps_time),
        ):
            if seq is not None and len(seq) != n:
                raise LASParseError(
                    f"'{name}' uzunluğu ({len(seq)}) nokta sayısıyla ({n}) eşleşmeli."
                )

    def to_heightmap_grid(
        self,
        resolution_m: float,
        origin: GeoPoint | None = None,
        aggregation: str = "max",
        fill_value: float | None = None,
    ) -> object:
        """E4 hedefi: nokta bulutundan `terrain_engine.HeightmapGrid`
        üretir (basit binning/gridleme — regular-grid rasterizasyon).

        Her hücre, o hücreye düşen noktaların Z değerlerinin
        `aggregation`'ına (``"max"`` en yüksek nokta - bina/ağaç üst
        yüzeyi için doğru varsayılan; ``"min"`` zemin yaklaşıklığı;
        ``"mean"``) göre doldurulur. Boş hücreler `fill_value` (verilmezse
        bulut geneli minimum Z) ile doldurulur — komşu-enterpolasyonu
        kasıtlı olarak yapılmaz (E3'ün kapsamına bırakılmıştır,
        bkz. `terrain_engine/README.md`).

        `terrain_engine` burada fonksiyon-seviyesinde (lazy) import
        edilir: `core_engine` çekirdek paketinin `terrain_engine`'e sert
        bir üst-seviye bağımlılığı olmaması ilkesi korunur (yalnızca bu
        köprü fonksiyonu çağrıldığında gerekir).
        """
        if aggregation not in ("max", "min", "mean"):
            raise ValueError("aggregation 'max' | 'min' | 'mean' olmalı.")
        if resolution_m <= 0:
            raise ValueError("resolution_m pozitif olmalı.")

        from ...terrain_engine import HeightmapGrid  # lazy import - bkz. docstring

        width = max(1, int((self.bounds.max_x - self.bounds.min_x) / resolution_m) + 1)
        height = max(1, int((self.bounds.max_y - self.bounds.min_y) / resolution_m) + 1)

        sums: list[list[float]] = [[0.0] * width for _ in range(height)]
        counts: list[list[int]] = [[0] * width for _ in range(height)]
        best: list[list[float | None]] = [[None] * width for _ in range(height)]

        for x, y, z in self.points:
            col = min(width - 1, int((x - self.bounds.min_x) / resolution_m))
            # LAS'ta Y kuzeye doğru artar; grid satır-0'ı "üst/kuzey" kabul
            # eder (diğer `gis_core`/`terrain_engine` gridleriyle tutarlı) -
            # bu yüzden satır indeksini ters çeviriyoruz.
            row_from_bottom = int((y - self.bounds.min_y) / resolution_m)
            row = min(height - 1, max(0, (height - 1) - row_from_bottom))
            col = max(0, col)

            sums[row][col] += z
            counts[row][col] += 1
            current = best[row][col]
            if current is None:
                best[row][col] = z
            elif aggregation == "max" and z > current:
                best[row][col] = z
            elif aggregation == "min" and z < current:
                best[row][col] = z

        empty_fill = self.bounds.min_z if fill_value is None else fill_value
        elevations: list[list[float]] = []
        for r in range(height):
            row_vals: list[float] = []
            for c in range(width):
                if counts[r][c] == 0:
                    row_vals.append(empty_fill)
                elif aggregation == "mean":
                    row_vals.append(sums[r][c] / counts[r][c])
                else:
                    row_vals.append(float(best[r][c]))
            elevations.append(row_vals)

        grid_origin = origin if origin is not None else GeoPoint(lat=0.0, lon=0.0)
        return HeightmapGrid(
            width=width,
            height=height,
            resolution_m=resolution_m,
            elevations=elevations,
            origin=grid_origin,
        )


class LASPointCloudParser:
    """ASPRS LAS 1.2/1.3/1.4 (sıkıştırılmamış) ayrıştırıcısı — stdlib-only.

    Desteklenen Point Data Record Format'lar: 0, 1, 2, 3 (LAS 1.2
    baseline'ı ve 1.3/1.4'ün geriye-uyumlu alt kümesi — X/Y/Z + intensity
    + classification + opsiyonel GPS time (1/3) ve RGB (2/3)). Format
    4/5 (waveform) ve 6-10 (LAS 1.4 genişletilmiş bit düzeni, farklı
    classification/return-count kodlaması) bilinçli olarak kapsam
    dışıdır — bu formatlar tespit edilirse `UnsupportedFormatError`
    fırlatılır (sessizce yanlış ayrıştırma yerine).
    """

    SUPPORTED_POINT_FORMATS = (0, 1, 2, 3)
    _SIGNATURE = b"LASF"

    # (X,Y,Z int32) + intensity(u16) + flags(u8) + classification(u8)
    # + scan_angle(i8) + user_data(u8) + point_source_id(u16) = 20 bayt.
    _BASE_STRUCT = struct.Struct("<iiiHBBbBH")
    _GPS_STRUCT = struct.Struct("<d")
    _RGB_STRUCT = struct.Struct("<HHH")

    @classmethod
    def parse_file(cls, path: str | Path) -> PointCloud:
        path = Path(path)
        with open(path, "rb") as fh:
            cloud = cls.parse_stream(fh)
        cloud.source_file = str(path)
        return cloud

    @classmethod
    def parse_bytes(cls, data: bytes) -> PointCloud:
        import io

        return cls.parse_stream(io.BytesIO(data))

    @classmethod
    def parse_stream(cls, fh: BinaryIO) -> PointCloud:
        header_bytes = fh.read(227)
        if len(header_bytes) < 227:
            raise LASParseError("Dosya LAS public header'ı (227 bayt minimum) için çok kısa.")

        signature = header_bytes[0:4]
        if signature != cls._SIGNATURE:
            raise LASParseError(
                f"Geçersiz LAS imzası: {signature!r} (beklenen b'LASF'). "
                "LAZ (sıkıştırılmış) dosyalar için `LASPointCloudParser.parse_laz_file` kullanın."
            )

        version_major = header_bytes[24]
        version_minor = header_bytes[25]
        header_size = struct.unpack_from("<H", header_bytes, 94)[0]
        offset_to_point_data = struct.unpack_from("<I", header_bytes, 96)[0]
        point_data_format_raw = header_bytes[104]
        # Üst 2 bit, LAS 1.4'te "point data is compressed" (LASzip) bayrağı
        # olarak kullanılabilir (bazı yazıcılar burada işaretler); format
        # numarası alt 6 bit.
        point_data_format = point_data_format_raw & 0b0011_1111
        point_data_record_length = struct.unpack_from("<H", header_bytes, 105)[0]
        legacy_num_points = struct.unpack_from("<I", header_bytes, 107)[0]

        x_scale, y_scale, z_scale = struct.unpack_from("<ddd", header_bytes, 131)
        x_offset, y_offset, z_offset = struct.unpack_from("<ddd", header_bytes, 155)
        max_x, min_x, max_y, min_y, max_z, min_z = struct.unpack_from("<dddddd", header_bytes, 179)

        num_points = legacy_num_points
        if version_major == 1 and version_minor >= 4:
            # LAS 1.4 header'ı 375 bayt - kalan kısmı okuyup (varsa) 64-bit
            # gerçek nokta sayısını (offset 247, extended header içinde)
            # kullanıyoruz; legacy alan 0 olabilir (>4 milyar nokta durumu).
            rest = fh.read(max(0, 375 - 227))
            if len(rest) >= 20:
                # extended header offset 247 (global) = rest[20:28]
                # (247 - 227 = 20). "Number of point records" (u64).
                ext_num_points = struct.unpack_from("<Q", rest, 20)[0]
                if ext_num_points:
                    num_points = ext_num_points

        if point_data_format not in cls.SUPPORTED_POINT_FORMATS:
            raise UnsupportedFormatError(
                f"LAS Point Data Format {point_data_format} desteklenmiyor "
                f"(desteklenen: {cls.SUPPORTED_POINT_FORMATS}) - waveform/genişletilmiş "
                "1.4 formatları (4-10) bu ayrıştırıcının kapsamı dışındadır."
            )

        has_gps_time = point_data_format in (1, 3)
        has_rgb = point_data_format in (2, 3)

        fh.seek(offset_to_point_data)
        raw = fh.read(num_points * point_data_record_length)
        if len(raw) != num_points * point_data_record_length:
            raise LASParseError(
                f"Beklenen {num_points} nokta kaydı ({point_data_record_length} bayt/kayıt) "
                f"okunamadı - dosya kesilmiş olabilir (okunan {len(raw)} bayt)."
            )

        points: list[tuple[float, float, float]] = []
        intensities: list[int] = []
        classifications: list[int] = []
        rgb_values: list[tuple[int, int, int]] = [] if has_rgb else None
        gps_times: list[float] = [] if has_gps_time else None

        base_size = cls._BASE_STRUCT.size
        for i in range(num_points):
            rec_start = i * point_data_record_length
            record = raw[rec_start : rec_start + point_data_record_length]
            xi, yi, zi, intensity, _flags, classification, _scan_angle, _user_data, _psid = (
                cls._BASE_STRUCT.unpack_from(record, 0)
            )
            x = xi * x_scale + x_offset
            y = yi * y_scale + y_offset
            z = zi * z_scale + z_offset
            points.append((x, y, z))
            intensities.append(intensity)
            classifications.append(classification)

            cursor = base_size
            if has_gps_time:
                (gps,) = cls._GPS_STRUCT.unpack_from(record, cursor)
                gps_times.append(gps)
                cursor += cls._GPS_STRUCT.size
            if has_rgb:
                r, g, b = cls._RGB_STRUCT.unpack_from(record, cursor)
                rgb_values.append((r, g, b))
                cursor += cls._RGB_STRUCT.size

        bounds = PointCloudBounds(
            min_x=min_x, min_y=min_y, min_z=min_z, max_x=max_x, max_y=max_y, max_z=max_z
        )

        return PointCloud(
            points=points,
            bounds=bounds,
            point_format=point_data_format,
            version=(version_major, version_minor),
            intensity=intensities,
            classification=classifications,
            rgb=rgb_values,
            gps_time=gps_times,
        )

    @classmethod
    def parse_laz_file(cls, path: str | Path) -> PointCloud:
        """LAZ (sıkıştırılmış LAS) - opsiyonel `laspy` bağımlılığı
        (`pyproject.toml` `[cloud]` extra'sı) üzerinden. `laspy` (ve onun
        sıkıştırma arka ucu `lazrs`/`laszip`) kurulu değilse, import
        hatası çağrı zamanında yakalanıp açık `UnsupportedFormatError`'a
        dönüştürülür - modül import zamanında hiçbir zaman patlamaz
        (roadmap'in "yoksa açık hata, sessiz çökme yok" ilkesi).
        """
        try:
            import laspy  # type: ignore[import-not-found]
        except ImportError as exc:
            raise UnsupportedFormatError(
                "LAZ (sıkıştırılmış LAS) okumak için opsiyonel 'laspy' bağımlılığı "
                "gerekli. Kurulum: pip install 'harita-modelleme[cloud]' "
                "(ayrıca bir sıkıştırma arka ucu - 'lazrs' veya 'laszip' - gerekir)."
            ) from exc

        path = Path(path)
        try:
            with laspy.open(str(path)) as reader:
                las = reader.read()
        except Exception as exc:  # laspy kendi hata tiplerini fırlatabilir
            raise LASParseError(f"LAZ dosyası okunamadı: {path}") from exc

        xs = las.x.tolist()
        ys = las.y.tolist()
        zs = las.z.tolist()
        points = list(zip(xs, ys, zs))
        classifications = (
            [int(c) for c in las.classification.tolist()]
            if hasattr(las, "classification")
            else None
        )
        intensities = (
            [int(v) for v in las.intensity.tolist()] if hasattr(las, "intensity") else None
        )

        rgb_values = None
        if hasattr(las, "red") and hasattr(las, "green") and hasattr(las, "blue"):
            reds, greens, blues = las.red.tolist(), las.green.tolist(), las.blue.tolist()
            rgb_values = list(zip(reds, greens, blues))

        header = las.header
        bounds = PointCloudBounds(
            min_x=header.mins[0],
            min_y=header.mins[1],
            min_z=header.mins[2],
            max_x=header.maxs[0],
            max_y=header.maxs[1],
            max_z=header.maxs[2],
        )
        cloud = PointCloud(
            points=points,
            bounds=bounds,
            point_format=int(header.point_format.id),
            version=(header.version.major, header.version.minor),
            intensity=intensities,
            classification=classifications,
            rgb=rgb_values,
        )
        cloud.source_file = str(path)
        return cloud
