"""FAZ S2.2 (ikinci alt-faz) — En küçük kareler (least squares) ağ dengelemesi.

ROADMAP_V6.md'nin "gösterge değil gerçek" ilkesinin en kritik uygulama yeri:
güven elipsleri gerçek varyans-kovaryans matrisinden, gerçek gözlem
ağırlıklarından (stokastik model) türetilir — Bowditch/Transit'in aksine
(bunlar sadece kapanma hatasını geometrik olarak dağıtır), burada her gözlemin
kendi standart sapması normal denklemlere ağırlık olarak girer.

Kaynak (Ghilani & Wolf, "Adjustment Computations: Spatial Data Analysis"):

    Doğrusallaştırılmış gözlem denklemi:   A x = L + V
        A: dizayn matrisi (kısmi türevler, yaklaşık koordinatlarda)
        x: bilinmeyen düzeltme vektörü (yaklaşık koordinatlara eklenecek Δ)
        L: kapanma (misclosure) vektörü = gözlenen − yaklaşık-koordinattan hesaplanan
        V: artık (residual) vektörü
    Ağırlık matrisi:                       P = diag(1 / σᵢ²)
    Normal denklemler:                     N x̂ = A^T P L,  N = A^T P A
    Kofaktör matrisi:                      Qxx = N⁻¹
    Referans varyans (a-posteriori):        σ₀² = (V^T P V) / (n − u)
    Varyans-kovaryans matrisi:              Σxx = σ₀² · Qxx
    Hata elipsi (2B nokta altmatrisinden):  özdeğerler λ1,λ2 → yarı eksenler
        a = σ₀√λ_max, b = σ₀√λ_min; yönelim θ = 0.5·atan2(2·Qen, Qee−Qnn)

Bu modül **stdlib-only** kalır (proje ilkesi: `geodetic_engine` dış araca
bağımlı değil) — matris işlemleri (çözüm, tersleme) saf Python ile, kısmi
pivotlu Gauss-Jordan eliminasyonu kullanılarak uygulanır. Sayısal ölçek küçük
(tipik saha ağı: onlarca bilinmeyen/gözlem) olduğundan bu, performans sorunu
yaratmaz; büyük ölçekli ağlar için `numpy`/`scipy` opsiyonel bir hızlandırma
katmanı olarak eklenmek istenirse ayrı bir extra ile (roadmap ilkesi: dış
araç varsa ince istemci) yapılabilir — bu modülün doğruluğu buna bağlı değil.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Matrix = list[list[float]]
Vector = list[float]


class InsufficientDataError(ValueError):
    """Ağ dengelemesi için gerekli veri (yeterli gözlem/bilinmeyen sayısı,
    sigma değeri vb.) eksik veya tutarsız olduğunda fırlatılır."""


class SingularNormalEquationsError(ValueError):
    """Normal denklemler matrisi (A^T P A) tekil (rank eksikliği — örn. ağ
    yeterince kısıtlanmamış/datum eksik) olduğunda fırlatılır. Asla sessizce
    pseudo-inverse veya sıfır-bölme koruması ile "yaklaşık" bir çözüm
    üretilmez."""


# --------------------------------------------------------------------------
# Saf Python doğrusal cebir yardımcıları (stdlib-only ilkesi)
# --------------------------------------------------------------------------

def _zeros(rows: int, cols: int) -> Matrix:
    return [[0.0] * cols for _ in range(rows)]


def _transpose(m: Matrix) -> Matrix:
    return [list(row) for row in zip(*m)]


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    n, k, k2, m = len(a), len(a[0]), len(b), len(b[0])
    if k != k2:
        raise ValueError(f"Matris boyutları uyuşmuyor: ({n}x{k}) x ({k2}x{m})")
    result = _zeros(n, m)
    for i in range(n):
        ai = a[i]
        for p in range(k):
            aip = ai[p]
            if aip == 0.0:
                continue
            bp = b[p]
            ri = result[i]
            for j in range(m):
                ri[j] += aip * bp[j]
    return result


def _matvec(a: Matrix, x: Vector) -> Vector:
    return [math.fsum(a[i][j] * x[j] for j in range(len(x))) for i in range(len(a))]


def _invert(m: Matrix) -> Matrix:
    """Kısmi pivotlu Gauss-Jordan eliminasyonu ile matris tersleme.
    Tekil (rank eksikliği) matrislerde `SingularNormalEquationsError` fırlatır
    — asla yaklaşık/pseudo-inverse ile sessizce devam etmez."""

    n = len(m)
    aug = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]

    for col in range(n):
        pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot_row][col]) < 1e-12:
            raise SingularNormalEquationsError(
                "Normal denklemler matrisi (A^T P A) tekil — ağ yeterince "
                "kısıtlanmamış (datum eksik) veya gereğinden az bağımsız gözlem var. "
                "Gerçek bir çözüm üretilemez; ek gözlem/sabit nokta gerekir."
            )
        aug[col], aug[pivot_row] = aug[pivot_row], aug[col]

        pivot = aug[col][col]
        aug[col] = [v / pivot for v in aug[col]]

        for r in range(n):
            if r == col:
                continue
            factor = aug[r][col]
            if factor == 0.0:
                continue
            aug[r] = [v - factor * aug[col][k] for k, v in enumerate(aug[r])]

    return [row[n:] for row in aug]


def _solve(a: Matrix, b: Vector) -> Vector:
    inv = _invert(a)
    return _matvec(inv, b)


# --------------------------------------------------------------------------
# Ağ modeli: bilinmeyen noktalar + gözlemler (mesafe/açı/azimut)
# --------------------------------------------------------------------------

@dataclass(slots=True)
class UnknownPoint:
    """Koordinatları en küçük kareler ile kestirilecek nokta. `approx_*`
    yaklaşık (ön) koordinatlar — doğrusallaştırma bu değerler etrafında
    yapılır (Newton-Raphson benzeri iterasyonla, `adjust_network` içinde)."""

    point_id: str
    approx_easting_m: float
    approx_northing_m: float


@dataclass(slots=True)
class FixedPoint:
    """Bilinen (sabit, kontrol) nokta — ağın datumunu tanımlar. Düzeltme
    kestirilmez, sadece gözlem denklemlerinde referans olarak kullanılır."""

    point_id: str
    easting_m: float
    northing_m: float


@dataclass(slots=True)
class DistanceObservation:
    """İki nokta arasında ölçülen yatay mesafe (Total Station veya GNSS
    baseline'dan gelen indirgenmiş mesafe). `sigma_m`: gözlemin gerçek
    standart sapması — ölçüm ekipmanı/prosedür kaynaklı, uydurulmaz."""

    from_id: str
    to_id: str
    observed_m: float
    sigma_m: float


@dataclass(slots=True)
class AzimuthObservation:
    """İki nokta arasında ölçülen azimut/yöney (gon, kuzeyden saat yönünde).
    `sigma_gon`: açısal standart sapma."""

    from_id: str
    to_id: str
    observed_gon: float
    sigma_gon: float


@dataclass(slots=True)
class ErrorEllipse:
    semi_major_m: float
    semi_minor_m: float
    orientation_deg: float  # semi_major eksenin kuzeyden saat yönünde açısı


@dataclass(slots=True)
class AdjustedPoint:
    point_id: str
    easting_m: float
    northing_m: float
    std_easting_m: float
    std_northing_m: float
    error_ellipse: ErrorEllipse


@dataclass(slots=True)
class NetworkAdjustmentResult:
    points: dict[str, AdjustedPoint]
    residuals: Vector
    reference_variance: float  # σ₀² (a-posteriori birim ağırlık varyansı)
    degrees_of_freedom: int
    redundancy: int
    iterations: int
    converged: bool
    covariance_matrix: Matrix  # tam Σxx (adjusted-point sırasıyla, 2x2 blok/nokta)
    point_order: list[str]  # covariance_matrix satır/sütunlarının nokta sırası


_GON_TO_RAD = math.pi / 200.0


def _azimuth_gon(de: float, dn: float) -> float:
    """Kuzeyden saat yönünde azimut (gon), atan2(ΔE, ΔN) — bearing formülüyle
    tutarlı (bkz. `reduction.py`)."""
    return math.atan2(de, dn) / _GON_TO_RAD % 400.0


def adjust_network(
    unknowns: list[UnknownPoint],
    fixed_points: list[FixedPoint],
    distance_obs: list[DistanceObservation],
    azimuth_obs: list[AzimuthObservation],
    max_iterations: int = 10,
    convergence_tol_m: float = 1e-6,
) -> NetworkAdjustmentResult:
    """Parametrik en küçük kareler ağ dengelemesi (Gauss-Markov modeli).

    Bilinmeyenler: her `UnknownPoint` için (ΔE, ΔN) düzeltmesi (toplam
    2×len(unknowns) bilinmeyen). Gözlemler: mesafe ve/veya azimut — her ikisi
    de doğrusal olmadığından Newton-Raphson tipi iterasyonla (yaklaşık
    koordinatlar her iterasyonda güncellenerek) çözülür; `convergence_tol_m`
    değerine ulaşana veya `max_iterations` tükenene kadar.

    Fazla ölçü (redundancy) yoksa (n_obs <= n_unknowns) kestirim istatistiksel
    olarak anlamsız olur → `InsufficientDataError`.
    """

    if not unknowns:
        raise InsufficientDataError("Dengeleme için en az bir bilinmeyen nokta gerekir.")

    n_obs = len(distance_obs) + len(azimuth_obs)
    n_unknowns = 2 * len(unknowns)
    if n_obs == 0:
        raise InsufficientDataError("Dengeleme için en az bir gözlem (mesafe veya azimut) gerekir.")
    if n_obs <= n_unknowns:
        raise InsufficientDataError(
            f"Fazla ölçü (redundancy) yok: {n_obs} gözlem, {n_unknowns} bilinmeyen. "
            "En küçük kareler dengelemesi istatistiksel anlam kazanması için "
            "gözlem sayısı bilinmeyen sayısını kesinlikle aşmalıdır."
        )
    for obs in distance_obs:
        if obs.sigma_m <= 0:
            raise InsufficientDataError(
                f"Mesafe gözlemi {obs.from_id}->{obs.to_id} için geçerli (pozitif) sigma yok — "
                "gerçek ölçüm hassasiyeti olmadan ağırlıklandırma yapılamaz."
            )
    for obs in azimuth_obs:
        if obs.sigma_gon <= 0:
            raise InsufficientDataError(
                f"Azimut gözlemi {obs.from_id}->{obs.to_id} için geçerli (pozitif) sigma yok."
            )

    point_order = [u.point_id for u in unknowns]
    index_of = {pid: i for i, pid in enumerate(point_order)}
    coords: dict[str, tuple[float, float]] = {
        u.point_id: (u.approx_easting_m, u.approx_northing_m) for u in unknowns
    }
    fixed_coords: dict[str, tuple[float, float]] = {
        f.point_id: (f.easting_m, f.northing_m) for f in fixed_points
    }

    def _coord(pid: str) -> tuple[float, float]:
        if pid in coords:
            return coords[pid]
        if pid in fixed_coords:
            return fixed_coords[pid]
        raise InsufficientDataError(
            f"Gözlemde referans verilen nokta '{pid}' ne bilinmeyenler ne de "
            "sabit noktalar arasında bulunamadı."
        )

    n_u = n_unknowns
    A: Matrix = []
    L: Vector = []
    P_diag: Vector = []
    converged = False
    iterations = 0

    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        A = []
        L = []
        P_diag = []

        for obs in distance_obs:
            e_from, n_from = _coord(obs.from_id)
            e_to, n_to = _coord(obs.to_id)
            de, dn = e_to - e_from, n_to - n_from
            dist = math.hypot(de, dn)
            if dist < 1e-9:
                raise InsufficientDataError(
                    f"Mesafe gözlemi {obs.from_id}->{obs.to_id}: yaklaşık koordinatlar "
                    "çakışık (mesafe ~0) — kısmi türev tanımsız."
                )
            row = [0.0] * n_u
            if obs.from_id in index_of:
                i = index_of[obs.from_id]
                row[2 * i] += -de / dist
                row[2 * i + 1] += -dn / dist
            if obs.to_id in index_of:
                i = index_of[obs.to_id]
                row[2 * i] += de / dist
                row[2 * i + 1] += dn / dist
            A.append(row)
            L.append(obs.observed_m - dist)
            P_diag.append(1.0 / (obs.sigma_m ** 2))

        for obs in azimuth_obs:
            e_from, n_from = _coord(obs.from_id)
            e_to, n_to = _coord(obs.to_id)
            de, dn = e_to - e_from, n_to - n_from
            dist_sq = de ** 2 + dn ** 2
            if dist_sq < 1e-12:
                raise InsufficientDataError(
                    f"Azimut gözlemi {obs.from_id}->{obs.to_id}: yaklaşık koordinatlar "
                    "çakışık — kısmi türev tanımsız."
                )
            computed_gon = _azimuth_gon(de, dn)
            # ∂azimut/∂E = ΔN/d², ∂azimut/∂N = -ΔE/d² (radyan); gon'a çevrilir.
            d_az_de = (dn / dist_sq) / _GON_TO_RAD
            d_az_dn = (-de / dist_sq) / _GON_TO_RAD
            row = [0.0] * n_u
            if obs.from_id in index_of:
                i = index_of[obs.from_id]
                row[2 * i] += -d_az_de
                row[2 * i + 1] += -d_az_dn
            if obs.to_id in index_of:
                i = index_of[obs.to_id]
                row[2 * i] += d_az_de
                row[2 * i + 1] += d_az_dn
            A.append(row)
            # Azimut farkını -200..+200 gon aralığına sar (400 gon dolanma sorunu).
            misclosure = obs.observed_gon - computed_gon
            misclosure = (misclosure + 200.0) % 400.0 - 200.0
            L.append(misclosure)
            P_diag.append(1.0 / (obs.sigma_gon ** 2))

        At = _transpose(A)
        AtP = [[At[i][k] * P_diag[k] for k in range(n_obs)] for i in range(n_u)]
        N = _matmul(AtP, A)
        t = _matvec(AtP, L)
        x = _solve(N, t)

        for pid, i in index_of.items():
            e, n = coords[pid]
            coords[pid] = (e + x[2 * i], n + x[2 * i + 1])

        max_delta = max(abs(v) for v in x)
        if max_delta < convergence_tol_m:
            converged = True
            break

    if not converged:
        raise InsufficientDataError(
            f"Ağ dengelemesi {max_iterations} iterasyonda yakınsamadı — yaklaşık "
            "koordinatlar gerçek çözümden çok uzak olabilir veya ağ geometrisi zayıf. "
            "Sessizce yakınsamamış bir sonuç raporlanmaz."
        )

    # Son iterasyondaki A/L/P ile artıklar V = A x - L hesaplanır (x, son
    # iterasyonda kestirilen düzeltme vektörü — yakınsama sonrası ~0'a yakın
    # olsa da referans varyans hesabı için tam artık tanımı kullanılır).
    V = [math.fsum(A[k][j] * x[j] for j in range(n_u)) - L[k] for k in range(n_obs)]
    vt_p_v = math.fsum(P_diag[k] * V[k] ** 2 for k in range(n_obs))
    redundancy = n_obs - n_u
    if redundancy <= 0:
        raise InsufficientDataError("Fazla ölçü (redundancy) sıfır veya negatif — referans varyans hesaplanamaz.")
    sigma0_sq = vt_p_v / redundancy

    At = _transpose(A)
    AtP = [[At[i][k] * P_diag[k] for k in range(n_obs)] for i in range(n_u)]
    N = _matmul(AtP, A)
    Qxx = _invert(N)
    Sigma_xx = [[sigma0_sq * v for v in row] for row in Qxx]

    points: dict[str, AdjustedPoint] = {}
    for pid, i in index_of.items():
        e, n = coords[pid]
        qee = Qxx[2 * i][2 * i]
        qnn = Qxx[2 * i + 1][2 * i + 1]
        qen = Qxx[2 * i][2 * i + 1]
        std_e = math.sqrt(sigma0_sq * qee)
        std_n = math.sqrt(sigma0_sq * qnn)

        # 2x2 varyans-kovaryans altmatrisinden hata elipsi (özdeğer çözümü,
        # 2x2 için kapalı formül — genel özdeğer ayrıştırmasına gerek yok).
        see, snn, sen = sigma0_sq * qee, sigma0_sq * qnn, sigma0_sq * qen
        trace = see + snn
        det = see * snn - sen ** 2
        disc = math.sqrt(max(trace ** 2 / 4.0 - det, 0.0))
        lambda_max = trace / 2.0 + disc
        lambda_min = trace / 2.0 - disc
        if sen == 0.0 and see == snn:
            theta = 0.0
        else:
            theta = 0.5 * math.atan2(2 * sen, see - snn)
        ellipse = ErrorEllipse(
            semi_major_m=math.sqrt(max(lambda_max, 0.0)),
            semi_minor_m=math.sqrt(max(lambda_min, 0.0)),
            orientation_deg=math.degrees(theta) % 180.0,
        )
        points[pid] = AdjustedPoint(
            point_id=pid,
            easting_m=e,
            northing_m=n,
            std_easting_m=std_e,
            std_northing_m=std_n,
            error_ellipse=ellipse,
        )

    return NetworkAdjustmentResult(
        points=points,
        residuals=V,
        reference_variance=sigma0_sq,
        degrees_of_freedom=redundancy,
        redundancy=redundancy,
        iterations=iterations,
        converged=converged,
        covariance_matrix=Sigma_xx,
        point_order=point_order,
    )
