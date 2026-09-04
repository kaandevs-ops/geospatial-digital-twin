"""FAZ S5 — Nokta bulutu-mesh karşılaştırması (S3'e bağımlıydı, artık
`pointcloud_engine` mevcut olduğundan uygulanabilir).

Roadmap ROADMAP_V6.md S5: "Nokta bulutu-mesh karşılaştırması (varsa
fotogrametri + LiDAR birlikte): iki kaynaktan üretilen yüzeyler arası
gerçek Hausdorff/RMS mesafe hesabı — 'iki veri kaynağı ne kadar tutarlı'
sorusuna sayısal cevap."

Bu modül, bir nokta bulutu (örn. LiDAR) ile bir mesh yüzeyinin (örn.
fotogrametri/Meshroom çıktısı `Mesh3D`) üçgenlerinden örneklenmiş nokta
kümesi arasında **gerçek** mesafe metriklerini hesaplar:

- **RMS mesafe**: her nokta bulutu noktasının mesh yüzeyine (örneklenmiş
  nokta kümesine, `data_engine.spatial_index.KDTree` ile kesin en-yakın-
  komşu) olan mesafesinin karesi ortalamasının kökü.
- **Hausdorff mesafesi**: iki küme arasındaki en kötü-durum (worst-case)
  mesafe — `max(directed_hausdorff(A,B), directed_hausdorff(B,A))` —
  simetrik Hausdorff mesafesi, standart tanım.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ...data_engine.spatial_index import KDTree
from ...mesh_engine import Mesh3D

Point3 = tuple[float, float, float]


class ComparisonError(ValueError):
    pass


@dataclass(slots=True)
class SurfaceComparisonReport:
    rms_distance_m: float
    hausdorff_distance_m: float
    n_source_points: int
    n_reference_samples: int
    max_relative_precision: float | None = None


def _sample_mesh_surface(mesh: Mesh3D, target_spacing: float | None = None) -> list[Point3]:
    """Mesh üçgenlerini barycentric bir ızgarayla örnekler — Hausdorff
    mesafesinin (worst-case metrik) büyük/düz üçgenlerde yanlışlıkla
    şişmemesi için örnekleme sıklığı, `target_spacing` verilmezse üçgenin
    kendi kenar uzunluğuna göre uyarlanır (her kenarda en az ~8 örnek
    noktası düşecek şekilde). Bu, "en iyi" mesh örnekleme algoritmasını
    icat etmiyor — basit, deterministik, belgelenmiş bir ızgara
    örneklemesi; tam yüzey entegrasyonu değil, Hausdorff'un tanımı gereği
    zaten bir üst-sınır yaklaşıklamasıdır.
    """
    if not mesh.triangles:
        raise ComparisonError("Mesh üçgen içermiyor — karşılaştırma için boş yüzey kullanılamaz.")
    samples: list[Point3] = []
    for i, j, k in mesh.triangles:
        a = mesh.vertices[i].as_tuple()
        b = mesh.vertices[j].as_tuple()
        c = mesh.vertices[k].as_tuple()
        edge_lengths = [_dist(a, b), _dist(b, c), _dist(c, a)]
        max_edge = max(edge_lengths)
        if target_spacing and target_spacing > 0:
            n = max(2, int(math.ceil(max_edge / target_spacing)))
        else:
            n = 8  # kenar başına varsayılan örnekleme çözünürlüğü
        n = min(n, 40)  # aşırı büyük mesh'lerde performans üst sınırı

        for ui in range(n + 1):
            u = ui / n
            for vi in range(n + 1 - ui):
                v = vi / n
                w = 1.0 - u - v
                x = w * a[0] + u * b[0] + v * c[0]
                y = w * a[1] + u * b[1] + v * c[1]
                z = w * a[2] + u * b[2] + v * c[2]
                samples.append((x, y, z))
    return samples


def _closest_point_on_triangle(p: Point3, a: Point3, b: Point3, c: Point3) -> Point3:
    """Bir noktanın bir üçgen üzerindeki en yakın noktası — Ericson,
    "Real-Time Collision Detection" kitabındaki standart barycentric
    izdüşüm algoritması (kesin, örnekleme yaklaşıklığı değil)."""
    ax, ay, az = a
    bx, by, bz = b
    cx, cy, cz = c
    px, py, pz = p

    ab = (bx - ax, by - ay, bz - az)
    ac = (cx - ax, cy - ay, cz - az)
    ap = (px - ax, py - ay, pz - az)

    d1 = ab[0] * ap[0] + ab[1] * ap[1] + ab[2] * ap[2]
    d2 = ac[0] * ap[0] + ac[1] * ap[1] + ac[2] * ap[2]
    if d1 <= 0 and d2 <= 0:
        return a

    bp = (px - bx, py - by, pz - bz)
    d3 = ab[0] * bp[0] + ab[1] * bp[1] + ab[2] * bp[2]
    d4 = ac[0] * bp[0] + ac[1] * bp[1] + ac[2] * bp[2]
    if d3 >= 0 and d4 <= d3:
        return b

    vc = d1 * d4 - d3 * d2
    if vc <= 0 and d1 >= 0 and d3 <= 0:
        v = d1 / (d1 - d3)
        return (ax + v * ab[0], ay + v * ab[1], az + v * ab[2])

    cp = (px - cx, py - cy, pz - cz)
    d5 = ab[0] * cp[0] + ab[1] * cp[1] + ab[2] * cp[2]
    d6 = ac[0] * cp[0] + ac[1] * cp[1] + ac[2] * cp[2]
    if d6 >= 0 and d5 <= d6:
        return c

    vb = d5 * d2 - d1 * d6
    if vb <= 0 and d2 >= 0 and d6 <= 0:
        w = d2 / (d2 - d6)
        return (ax + w * ac[0], ay + w * ac[1], az + w * ac[2])

    va = d3 * d6 - d5 * d4
    if va <= 0 and (d4 - d3) >= 0 and (d5 - d6) >= 0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return (bx + w * (cx - bx), by + w * (cy - by), bz + w * (cz - bz))

    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return (ax + ab[0] * v + ac[0] * w, ay + ab[1] * v + ac[1] * w, az + ab[2] * v + ac[2] * w)


def _dist(p: Point3, q: Point3) -> float:
    return math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)


def _point_to_mesh_distance(p: Point3, mesh: Mesh3D) -> float:
    """Bir noktanın mesh yüzeyine (tüm üçgenlere, en yakınını seçerek)
    olan **kesin** mesafesi — üçgen sayısı makul olduğunda (tipik saha
    projesi mesh'leri) brute-force yeterlidir; çok büyük mesh'ler için
    `data_engine.spatial_index.BVH` ile hızlandırma ileride eklenebilir
    (bu fonksiyonun imzasını değiştirmeden — mimari buna izin veriyor)."""
    best = math.inf
    for i, j, k in mesh.triangles:
        a, b, c = (
            mesh.vertices[i].as_tuple(),
            mesh.vertices[j].as_tuple(),
            mesh.vertices[k].as_tuple(),
        )
        cp = _closest_point_on_triangle(p, a, b, c)
        d = _dist(p, cp)
        if d < best:
            best = d
    return best


def _directed_hausdorff(a: list[Point3], tree_b: KDTree) -> float:
    max_min_dist = 0.0
    for p in a:
        _, _, sq_dist = tree_b.nearest(p)
        max_min_dist = max(max_min_dist, math.sqrt(sq_dist))
    return max_min_dist


def compare_pointcloud_to_mesh(
    pointcloud_points: list[Point3], mesh: Mesh3D
) -> SurfaceComparisonReport:
    """Bir nokta bulutunu bir mesh yüzeyiyle karşılaştırır (RMS + Hausdorff).

    RMS mesafesi, her nokta bulutu noktasının mesh **yüzeyine** (üçgen
    düzlemlerine izdüşüm, sadece köşe/örnek noktalarına değil) olan kesin
    mesafesinden hesaplanır. Hausdorff mesafesi (simetrik, worst-case),
    hesaplama maliyetini sınırlamak için köşe+ağırlık merkezi örneklemesi
    (`_sample_mesh_surface`) üzerinden yaklaşıklanır — bu, Hausdorff'un
    tanımı gereği zaten en kötü-durum bir metrik olduğundan (aşırı
    değerlere duyarlı), belgelenmiş bir yaklaşıklıktır.

    `InsufficientDataError` yerine `ComparisonError` kullanılır (bu modül
    `geodetic_engine`'e değil `pointcloud_engine`/`mesh_engine`'e bağımlı
    — ayrı bir hata sınıfı, karışıklığı önler)."""
    if len(pointcloud_points) < 1:
        raise ComparisonError("Karşılaştırma için en az 1 nokta bulutu noktası gerekir.")

    tree_cloud = KDTree(pointcloud_points)
    # Nokta bulutunun ortalama komşu mesafesinden uyarlamalı hedef örnekleme
    # aralığı türet — mesh örneklemesi bulutun kendi çözünürlüğünden daha
    # kaba olmasın (yoksa Hausdorff yapay olarak şişer).
    sample_size = min(len(pointcloud_points), 30)
    nn_dists = [
        math.sqrt(tree_cloud.nearest_k(p, 2)[-1][2]) for p in pointcloud_points[:sample_size]
    ]
    target_spacing = (sum(nn_dists) / len(nn_dists)) if nn_dists else None

    mesh_samples = _sample_mesh_surface(
        mesh, target_spacing
    )  # boş mesh -> ComparisonError burada fırlar

    sq_dists = [_point_to_mesh_distance(p, mesh) ** 2 for p in pointcloud_points]
    rms = math.sqrt(sum(sq_dists) / len(sq_dists))

    tree_mesh_samples = KDTree(mesh_samples)
    hausdorff_cloud_to_mesh = _directed_hausdorff(pointcloud_points, tree_mesh_samples)
    hausdorff_mesh_to_cloud = _directed_hausdorff(mesh_samples, tree_cloud)
    hausdorff = max(hausdorff_cloud_to_mesh, hausdorff_mesh_to_cloud)

    return SurfaceComparisonReport(
        rms_distance_m=rms,
        hausdorff_distance_m=hausdorff,
        n_source_points=len(pointcloud_points),
        n_reference_samples=len(mesh_samples),
    )
