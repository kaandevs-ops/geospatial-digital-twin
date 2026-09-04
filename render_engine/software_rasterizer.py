"""Roadmap V7 - "bilinçli kapsam-dışı" madde 2'nin kapanışı: **gerçek
piksel-tabanlı görsel regresyon**.

Önceki durum (`scripts/visual_regression.py` başlığında dürüstçe
belgelenmişti): proje stdlib-only'dir, gerçek bir GPU render pipeline'ı
yoktur (tarayıcıdaki WebGL2 renderer Python tarafında erişilemez) — bu
yüzden yalnızca "yapısal imza" (vertex/üçgen sayısı, bounding box vb.)
karşılaştırılıyordu, gerçek piksel üretilmiyordu.

Bu modül, **harici bağımlılık olmadan** (yalnızca stdlib: `struct`,
`zlib`, `math`) çalışan minimal bir **yazılım (software) üçgen
rasterizer'ı** sağlar: gerçek bir kamera projeksiyonu + z-buffer + düz
gölgelendirme (flat/Lambertian shading) ile bir `Mesh3D`'yi gerçek bir
piksel matrisine (RGB) çevirir. Bu, GPU/WebGL kullanmaz — CPU'da, saf
matematikle çalışan klasik bir "software renderer"dır (aynı ders
kitabı algoritması: Bresenham/kenar-fonksiyonu tarama dönüşümü +
barycentric interpolasyon). Ortaya çıkan piksel matrisi gerçek bir
görüntüdür; `pixel_diff` ile önceki bir baseline görüntüyle
byte-byte/piksel-piksel karşılaştırılabilir — "yapısal imza"nın
yakalayamayacağı saf görsel regresyonları (örn. normal yönü ters
dönmüş bir yüzey, delinmiş/örtüşen üçgenler, yanlış gölgelendirme)
gerçekten yakalar.

Sınırlama (dürüstçe belirtilmeli): bu, tarayıcıdaki gerçek WebGL2
renderer'ın *bire bir* pikselini üretmez (farklı bir rasterizer, farklı
gölgelendirme modeli) — amaç, tarayıcıyı taklit etmek değil, **geometri
üretim kodundaki bir regresyonu** (`building_reconstruction` vb.)
Python tarafında, headless biçimde, gerçek piksellerle yakalamaktır.
Playwright/gerçek tarayıcı görüntü karşılaştırması istenirse, o farklı
bir araç seti gerektirir (bkz. `scripts/visual_regression.py` başlığındaki
not) ve bu modülün kapsamı dışındadır.
"""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass

from ..mesh_engine import Mesh3D

__all__ = [
    "Camera", "Image", "rasterize_mesh", "write_ppm", "read_ppm",
    "write_png", "pixel_diff", "PixelDiffResult",
]


# ======================================================================== #
# Kamera / projeksiyon
# ======================================================================== #

@dataclass(slots=True)
class Camera:
    """Basit bakış-hedef (look-at) perspektif kamera."""

    eye: tuple[float, float, float]
    target: tuple[float, float, float]
    up: tuple[float, float, float] = (0.0, 0.0, 1.0)
    fov_y_deg: float = 45.0
    near: float = 0.1
    far: float = 1000.0

    def view_matrix(self) -> list[list[float]]:
        ex, ey, ez = self.eye
        tx, ty, tz = self.target
        ux, uy, uz = self.up

        fx, fy, fz = tx - ex, ty - ey, tz - ez
        flen = math.sqrt(fx * fx + fy * fy + fz * fz) or 1e-9
        fx, fy, fz = fx / flen, fy / flen, fz / flen

        # side = f x up
        sx, sy, sz = fy * uz - fz * uy, fz * ux - fx * uz, fx * uy - fy * ux
        slen = math.sqrt(sx * sx + sy * sy + sz * sz) or 1e-9
        sx, sy, sz = sx / slen, sy / slen, sz / slen

        # true up = side x f
        ux2, uy2, uz2 = sy * fz - sz * fy, sz * fx - sx * fz, sx * fy - sy * fx

        return [
            [sx, sy, sz, -(sx * ex + sy * ey + sz * ez)],
            [ux2, uy2, uz2, -(ux2 * ex + uy2 * ey + uz2 * ez)],
            [-fx, -fy, -fz, (fx * ex + fy * ey + fz * ez)],
            [0.0, 0.0, 0.0, 1.0],
        ]

    def projection_matrix(self, aspect: float) -> list[list[float]]:
        fov_rad = math.radians(self.fov_y_deg)
        f = 1.0 / math.tan(fov_rad / 2.0)
        n, fa = self.near, self.far
        return [
            [f / aspect, 0.0, 0.0, 0.0],
            [0.0, f, 0.0, 0.0],
            [0.0, 0.0, (fa + n) / (n - fa), (2 * fa * n) / (n - fa)],
            [0.0, 0.0, -1.0, 0.0],
        ]


def _mat_vec_mul(m: list[list[float]], v: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    return tuple(
        m[r][0] * v[0] + m[r][1] * v[1] + m[r][2] * v[2] + m[r][3] * v[3]
        for r in range(4)
    )


# ======================================================================== #
# Görüntü tamponu
# ======================================================================== #

@dataclass(slots=True)
class Image:
    width: int
    height: int
    pixels: bytearray  # RGB, satır-major, 3 byte/piksel

    @classmethod
    def new(cls, width: int, height: int, fill: tuple[int, int, int] = (24, 26, 32)) -> "Image":
        buf = bytearray(width * height * 3)
        for i in range(0, len(buf), 3):
            buf[i], buf[i + 1], buf[i + 2] = fill
        return cls(width, height, buf)

    def set_pixel(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            idx = (y * self.width + x) * 3
            self.pixels[idx], self.pixels[idx + 1], self.pixels[idx + 2] = rgb


# ======================================================================== #
# Rasterizasyon (tarama dönüşümü + z-buffer + düz gölgelendirme)
# ======================================================================== #

def rasterize_mesh(
    mesh: Mesh3D, *, width: int = 320, height: int = 240,
    camera: Camera | None = None,
    light_dir: tuple[float, float, float] = (-0.4, -0.6, -0.7),
    base_color: tuple[int, int, int] = (150, 160, 180),
    background: tuple[int, int, int] = (24, 26, 32),
) -> Image:
    """Bir `Mesh3D`'yi gerçek bir RGB piksel matrisine tarama-dönüşümü
    (scanline/edge-function rasterization) ile çevirir.

    Algoritma: her üçgen için (1) dünya->görüş->projeksiyon dönüşümü,
    (2) perspektif bölme ile NDC, (3) ekran uzayına ölçekleme,
    (4) kenar fonksiyonu (edge function) ile üçgenin ekran-uzay bounding
    box'ı içindeki her pikselin içeride olup olmadığını test etme,
    (5) barycentric ağırlıklarla derinlik interpolasyonu + z-buffer testi,
    (6) düz yüzey normali ile Lambertian (N·L) gölgelendirme.
    """
    if camera is None:
        # Mesh'in bounding box'ına göre otomatik, deterministik bir kamera.
        if mesh.vertices:
            xs = [v.x for v in mesh.vertices]
            ys = [v.y for v in mesh.vertices]
            zs = [v.z for v in mesh.vertices]
            cx, cy, cz = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (min(zs) + max(zs)) / 2
            radius = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs), 1.0)
        else:
            cx = cy = cz = 0.0
            radius = 1.0
        eye = (cx + radius * 1.6, cy - radius * 1.6, cz + radius * 1.2)
        camera = Camera(eye=eye, target=(cx, cy, cz))

    img = Image.new(width, height, fill=background)
    depth_buffer = [math.inf] * (width * height)

    view = camera.view_matrix()
    proj = camera.projection_matrix(aspect=width / height)

    lx, ly, lz = light_dir
    llen = math.sqrt(lx * lx + ly * ly + lz * lz) or 1e-9
    lx, ly, lz = -lx / llen, -ly / llen, -lz / llen  # ışığa doğru bakan birim vektör

    def to_screen(v) -> tuple[float, float, float] | None:
        world = (v.x, v.y, v.z, 1.0)
        eye_space = _mat_vec_mul(view, world)
        clip = _mat_vec_mul(proj, eye_space)
        w = clip[3]
        if w <= 1e-6:
            return None
        ndc_x, ndc_y, ndc_z = clip[0] / w, clip[1] / w, clip[2] / w
        sx = (ndc_x * 0.5 + 0.5) * width
        sy = (1.0 - (ndc_y * 0.5 + 0.5)) * height
        return (sx, sy, ndc_z)

    for (i, j, k) in mesh.triangles:
        va, vb, vc = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]

        # Düz yüzey normali (world space) - flat shading için.
        e1 = (vb.x - va.x, vb.y - va.y, vb.z - va.z)
        e2 = (vc.x - va.x, vc.y - va.y, vc.z - va.z)
        nx = e1[1] * e2[2] - e1[2] * e2[1]
        ny = e1[2] * e2[0] - e1[0] * e2[2]
        nz = e1[0] * e2[1] - e1[1] * e2[0]
        nlen = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nlen < 1e-9:
            continue  # dejenere (sıfır alanlı) üçgen
        nx, ny, nz = nx / nlen, ny / nlen, nz / nlen

        pa, pb, pc = to_screen(va), to_screen(vb), to_screen(vc)
        if pa is None or pb is None or pc is None:
            continue
        ax, ay, az = pa
        bx, by, bz = pb
        cx_, cy_, cz_ = pc

        area = (bx - ax) * (cy_ - ay) - (by - ay) * (cx_ - ax)
        if abs(area) < 1e-9:
            continue  # ekranda görünmeyen (kenardan) üçgen

        # Geri-yüz ayıklama (backface culling): ekran-uzay sarma yönüne göre.
        if area > 0:
            continue

        min_x = max(int(math.floor(min(ax, bx, cx_))), 0)
        max_x = min(int(math.ceil(max(ax, bx, cx_))), width - 1)
        min_y = max(int(math.floor(min(ay, by, cy_))), 0)
        max_y = min(int(math.ceil(max(ay, by, cy_))), height - 1)
        if min_x > max_x or min_y > max_y:
            continue

        # Lambertian gölgelendirme: N·L, [0.15, 1.0] aralığına kırpılmış
        # ambient taban ile (tamamen siyah yüzey olmasın diye).
        ndotl = max(0.15, nx * lx + ny * ly + nz * lz)
        shaded = (
            min(255, int(base_color[0] * ndotl)),
            min(255, int(base_color[1] * ndotl)),
            min(255, int(base_color[2] * ndotl)),
        )

        inv_area = 1.0 / area
        for py in range(min_y, max_y + 1):
            for px in range(min_x, max_x + 1):
                sx, sy = px + 0.5, py + 0.5
                w0 = ((bx - sx) * (cy_ - sy) - (by - sy) * (cx_ - sx)) * inv_area
                w1 = ((cx_ - sx) * (ay - sy) - (cy_ - sy) * (ax - sx)) * inv_area
                w2 = 1.0 - w0 - w1
                if w0 < 0 or w1 < 0 or w2 < 0:
                    continue
                depth = w0 * az + w1 * bz + w2 * cz_
                idx = py * width + px
                if depth < depth_buffer[idx]:
                    depth_buffer[idx] = depth
                    img.set_pixel(px, py, shaded)

    return img


# ======================================================================== #
# Dosya G/Ç: PPM (P6, ham binary - sıfır bağımlılıklı en basit format)
# + PNG (stdlib zlib ile, görüntüleyici uyumluluğu için)
# ======================================================================== #

def write_ppm(image: Image, path: str) -> None:
    header = f"P6\n{image.width} {image.height}\n255\n".encode("ascii")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(bytes(image.pixels))


def read_ppm(path: str) -> Image:
    with open(path, "rb") as fh:
        magic = fh.readline().strip()
        if magic != b"P6":
            raise ValueError(f"PPM: beklenmeyen magic {magic!r} (P6 bekleniyordu).")
        dims_line = fh.readline()
        while dims_line.startswith(b"#"):
            dims_line = fh.readline()
        width, height = (int(v) for v in dims_line.split())
        maxval_line = fh.readline()
        assert int(maxval_line.strip()) == 255
        data = fh.read(width * height * 3)
    return Image(width, height, bytearray(data))


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data)) + tag + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def write_png(image: Image, path: str) -> None:
    """Stdlib `zlib` ile minimal, sıkıştırılmış (filter=None) bir PNG
    yazar — yalnızca insan doğrulaması / diff aracı uyumluluğu için;
    diff mantığı `pixel_diff` doğrudan `Image.pixels` üzerinde çalışır."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    raw = bytearray()
    stride = image.width * 3
    for row in range(image.height):
        raw.append(0)  # filter type: None
        raw += image.pixels[row * stride:(row + 1) * stride]
    idat = zlib.compress(bytes(raw), level=6)
    with open(path, "wb") as fh:
        fh.write(sig)
        fh.write(_png_chunk(b"IHDR", ihdr))
        fh.write(_png_chunk(b"IDAT", idat))
        fh.write(_png_chunk(b"IEND", b""))


# ======================================================================== #
# Piksel karşılaştırma
# ======================================================================== #

@dataclass(slots=True)
class PixelDiffResult:
    width: int
    height: int
    max_channel_diff: int
    mean_channel_diff: float
    changed_pixel_count: int
    changed_pixel_ratio: float

    def within_tolerance(self, *, max_diff: int = 12, max_ratio: float = 0.02) -> bool:
        """Regresyon eşiği: her CPU/derleyicide kayan-nokta birikimi
        farklı olabileceğinden tam byte-eşitliği yerine tolerans
        kullanılır (aynı yaklaşım `scripts/visual_regression.py`'deki
        `FLOAT_TOLERANCE`'la tutarlı)."""
        return self.max_channel_diff <= max_diff and self.changed_pixel_ratio <= max_ratio


def pixel_diff(a: Image, b: Image) -> PixelDiffResult:
    if a.width != b.width or a.height != b.height:
        raise ValueError(
            f"pixel_diff: boyut uyuşmazlığı {a.width}x{a.height} vs {b.width}x{b.height}"
        )
    n_pixels = a.width * a.height
    max_diff = 0
    total_diff = 0
    changed = 0
    for p in range(n_pixels):
        i = p * 3
        d0 = abs(a.pixels[i] - b.pixels[i])
        d1 = abs(a.pixels[i + 1] - b.pixels[i + 1])
        d2 = abs(a.pixels[i + 2] - b.pixels[i + 2])
        pixel_max = max(d0, d1, d2)
        if pixel_max:
            changed += 1
        if pixel_max > max_diff:
            max_diff = pixel_max
        total_diff += d0 + d1 + d2
    mean_diff = total_diff / (n_pixels * 3) if n_pixels else 0.0
    return PixelDiffResult(
        width=a.width, height=a.height,
        max_channel_diff=max_diff, mean_channel_diff=mean_diff,
        changed_pixel_count=changed,
        changed_pixel_ratio=(changed / n_pixels) if n_pixels else 0.0,
    )
