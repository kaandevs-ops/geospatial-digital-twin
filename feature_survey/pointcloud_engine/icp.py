"""FAZ S3 — Nokta Bulutu Kayıt (Registration): Iterative Closest Point.

Roadmap ROADMAP_V6.md S3: "Kayıt (registration): ICP (Iterative Closest
Point) — gerçek algoritma, yakınsama kriteri, hata metriği (mean squared
error) raporlanır."

Bu, klasik Besl & McKay (1992) nokta-nokta ICP algoritmasının stdlib-only
bir implementasyonudur:

1. Her kaynak (`source`) noktası için hedef (`target`) bulutunda en yakın
   nokta bulunur (`data_engine.spatial_index.KDTree` — kesin en-yakın-komşu,
   yaklaşık değil).
2. Eşleşen çiftlerin ağırlık merkezleri hesaplanır, merkeze göre kaydırılır.
3. Kovaryans matrisinin SVD'siyle (3x3, stdlib — Jacobi eigenvalue metodu
   ile kendi implementasyonumuz, harici lineer cebir bağımlılığı yok)
   optimal dönüş matrisi bulunur (Kabsch algoritması / Arun et al. 1987).
4. Öteleme, kaydırılmış merkezlerin farkından hesaplanır.
5. RMS hata (mean squared error'ın karekökü) yakınsama kriteri olarak
   izlenir; ardışık iterasyonlar arası değişim `tolerance`'ın altına
   düşerse veya `max_iterations`'a ulaşılırsa durur.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ...data_engine.spatial_index import KDTree

Point3 = tuple[float, float, float]
Matrix3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]


class ICPError(ValueError):
    """Kayıt için yetersiz veri (boş bulut, nokta sayısı < 3) durumunda."""


@dataclass(slots=True)
class ICPResult:
    rotation: Matrix3
    translation: Point3
    rmse_history: list[float] = field(default_factory=list)
    iterations: int = 0
    converged: bool = False

    @property
    def final_rmse(self) -> float:
        if not self.rmse_history:
            raise ICPError("RMSE geçmişi boş — kayıt hiç çalıştırılmamış.")
        return self.rmse_history[-1]

    def apply(self, points: list[Point3]) -> list[Point3]:
        """`rotation`/`translation`'ı bir nokta listesine uygular (kaynak
        bulutun kayıtlı hâlini elde etmek için)."""
        r = self.rotation
        t = self.translation
        out = []
        for x, y, z in points:
            nx = r[0][0] * x + r[0][1] * y + r[0][2] * z + t[0]
            ny = r[1][0] * x + r[1][1] * y + r[1][2] * z + t[1]
            nz = r[2][0] * x + r[2][1] * y + r[2][2] * z + t[2]
            out.append((nx, ny, nz))
        return out


def _mat_mul(a: Matrix3, b: Matrix3) -> Matrix3:
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _transpose(a: Matrix3) -> Matrix3:
    return tuple(tuple(a[j][i] for j in range(3)) for i in range(3))


def _det3(a: Matrix3) -> float:
    return (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )


def _jacobi_eigen_symmetric(a: Matrix3, max_sweeps: int = 100, tol: float = 1e-12):
    """3x3 simetrik matris için Jacobi özdeğer/özvektör algoritması
    (stdlib-only, klasik nümerik metot — LAPACK'e eşdeğer sonuç, harici
    bağımlılık yok). SVD hesaplamak için A^T*A'nın özvektörlerini kullanır
    (Kabsch/Arun algoritmasının standart uygulaması)."""
    m = [list(row) for row in a]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]

    def off_diag_norm(mat):
        return math.sqrt(sum(mat[i][j] ** 2 for i in range(3) for j in range(3) if i != j))

    for _ in range(max_sweeps):
        if off_diag_norm(m) < tol:
            break
        for p in range(3):
            for q in range(p + 1, 3):
                if abs(m[p][q]) < 1e-15:
                    continue
                theta = (m[q][q] - m[p][p]) / (2 * m[p][q])
                t = (1 if theta >= 0 else -1) / (abs(theta) + math.sqrt(theta**2 + 1))
                c = 1.0 / math.sqrt(t**2 + 1)
                s = t * c
                mpp, mqq, mpq = m[p][p], m[q][q], m[p][q]
                m[p][p] = c * c * mpp - 2 * s * c * mpq + s * s * mqq
                m[q][q] = s * s * mpp + 2 * s * c * mpq + c * c * mqq
                m[p][q] = m[q][p] = 0.0
                for i in range(3):
                    if i != p and i != q:
                        mip, miq = m[i][p], m[i][q]
                        m[i][p] = m[p][i] = c * mip - s * miq
                        m[i][q] = m[q][i] = s * mip + c * miq
                for i in range(3):
                    vip, viq = v[i][p], v[i][q]
                    v[i][p] = c * vip - s * viq
                    v[i][q] = s * vip + c * viq

    eigenvalues = [m[i][i] for i in range(3)]
    eigenvectors = v  # sütunlar özvektör
    return eigenvalues, eigenvectors


def _kabsch_rotation(source_centered: list[Point3], target_centered: list[Point3]) -> Matrix3:
    """Kabsch algoritması: iki eşleşmiş, merkezlenmiş nokta kümesi arasında
    optimal (en küçük kareler anlamında) dönüş matrisini bulur."""
    h = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    for (sx, sy, sz), (tx, ty, tz) in zip(source_centered, target_centered):
        s_vec = (sx, sy, sz)
        t_vec = (tx, ty, tz)
        for i in range(3):
            for j in range(3):
                h[i][j] += s_vec[i] * t_vec[j]

    # SVD(H) = U S V^T, R = V U^T. H^T H'nin özvektörleri V'yi verir;
    # H H^T'nin özvektörleri U'yu verir (klasik SVD-via-eigendecomposition).
    ht_h = _mat_mul(_transpose(tuple(map(tuple, h))), tuple(map(tuple, h)))
    eigvals, v = _jacobi_eigen_symmetric(ht_h)

    # Özdeğerleri büyükten küçüğe sırala (V sütunlarını da aynı sırayla)
    order = sorted(range(3), key=lambda i: -eigvals[i])
    v_sorted = [[v[r][c] for c in order] for r in range(3)]
    singular = [math.sqrt(max(eigvals[i], 0.0)) for i in order]

    h_mat = tuple(map(tuple, h))
    u_cols = []
    for i in range(3):
        if singular[i] > 1e-12:
            col = [
                sum(h_mat[r][c] * v_sorted[c][i] for c in range(3)) / singular[i] for r in range(3)
            ]
        else:
            col = [0.0, 0.0, 0.0]
        u_cols.append(col)
    u = tuple(tuple(u_cols[c][r] for c in range(3)) for r in range(3))
    v_mat = tuple(map(tuple, v_sorted))

    r = _mat_mul(v_mat, _transpose(u))
    if _det3(r) < 0:
        # Yansıma (reflection) düzeltmesi — Kabsch algoritmasının bilinen
        # kararlılık koşulu (det(R) her zaman +1 olmalı, gerçek bir dönüş).
        v_fixed = [list(row) for row in v_mat]
        for row in v_fixed:
            row[2] = -row[2]
        r = _mat_mul(tuple(map(tuple, v_fixed)), _transpose(u))
    return r


def run_icp(
    source: list[Point3],
    target: list[Point3],
    max_iterations: int = 50,
    tolerance: float = 1e-6,
) -> ICPResult:
    """`source` bulutunu `target` bulutuna hizalayan dönüş+öteleme
    dönüşümünü ICP ile hesaplar.

    Yakınsama kriteri: ardışık iterasyonlar arasındaki RMSE değişimi
    `tolerance`'ın altına düşerse `converged=True` ile durur; aksi halde
    `max_iterations` sonunda `converged=False` ile durur (sessizce
    "başarılı" denmez — çağıran taraf `converged`'i kontrol etmelidir).
    """
    if len(source) < 3 or len(target) < 3:
        raise ICPError("ICP için her iki bulutta da en az 3 nokta gerekir.")

    target_tree = KDTree(target)
    current = list(source)
    rotation_total: Matrix3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    translation_total: Point3 = (0.0, 0.0, 0.0)

    rmse_history: list[float] = []
    converged = False
    iterations = 0

    for iteration in range(max_iterations):
        iterations = iteration + 1
        matches = [target_tree.nearest(p) for p in current]
        matched_target = [m[1] for m in matches]  # data == point (target'ta data=points)
        sq_dists = [m[2] for m in matches]
        rmse = math.sqrt(sum(sq_dists) / len(sq_dists))
        rmse_history.append(rmse)

        if len(rmse_history) >= 2 and abs(rmse_history[-2] - rmse_history[-1]) < tolerance:
            converged = True
            break

        cx = sum(p[0] for p in current) / len(current)
        cy = sum(p[1] for p in current) / len(current)
        cz = sum(p[2] for p in current) / len(current)
        tx = sum(p[0] for p in matched_target) / len(matched_target)
        ty = sum(p[1] for p in matched_target) / len(matched_target)
        tz = sum(p[2] for p in matched_target) / len(matched_target)

        src_centered = [(p[0] - cx, p[1] - cy, p[2] - cz) for p in current]
        tgt_centered = [(p[0] - tx, p[1] - ty, p[2] - tz) for p in matched_target]

        r_step = _kabsch_rotation(src_centered, tgt_centered)

        def _rotate(pt: Point3, r: Matrix3) -> Point3:
            return (
                r[0][0] * pt[0] + r[0][1] * pt[1] + r[0][2] * pt[2],
                r[1][0] * pt[0] + r[1][1] * pt[1] + r[1][2] * pt[2],
                r[2][0] * pt[0] + r[2][1] * pt[1] + r[2][2] * pt[2],
            )

        t_step = (
            tx - _rotate((cx, cy, cz), r_step)[0],
            ty - _rotate((cx, cy, cz), r_step)[1],
            tz - _rotate((cx, cy, cz), r_step)[2],
        )

        current = [
            (
                _rotate(p, r_step)[0] + t_step[0],
                _rotate(p, r_step)[1] + t_step[1],
                _rotate(p, r_step)[2] + t_step[2],
            )
            for p in current
        ]
        rotation_total = _mat_mul(r_step, rotation_total)
        translation_total = (
            _rotate(translation_total, r_step)[0] + t_step[0],
            _rotate(translation_total, r_step)[1] + t_step[1],
            _rotate(translation_total, r_step)[2] + t_step[2],
        )

    return ICPResult(
        rotation=rotation_total,
        translation=translation_total,
        rmse_history=rmse_history,
        iterations=iterations,
        converged=converged,
    )
