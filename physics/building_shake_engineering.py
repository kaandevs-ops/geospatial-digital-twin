"""
physics.building_shake_engineering — Roadmap V10 / Faz 3.B (İhtiyari)
========================================================================

"Bina Fiziği: Sallanma — Mühendislik-yakın seviye (SDOF/shear-frame
modu)" — roadmap'in kendi notu gereği bu faza yalnızca (a) Faz 1-5
tamamen stabil olduktan SONRA ve (b) veri ön koşulu doğrulandıktan
SONRA başlanmalı.

**Veri ön koşulu doğrulaması (bu modülün ilk sorumluluğu):**
`ai_reconstruction/building_analyzer.py` incelendi — `BuildingAnalysis`
yalnızca `height_m`, `floor_count`, `usage`, `architectural_style`,
`estimated_age_years`, `facade_material`, `window_ratio` alanlarını
taşır. Kolon/kiriş boyutu, donatı oranı, malzeme elastisite modülü gibi
GERÇEK yapısal mühendislik verisi **YOKTUR** ve proje genelinde hiçbir
yerde üretilmez. Bu, `check_engineering_mode_precondition()` ile kod
içinde açıkça doğrulanmış ve `EngineeringModeDataStatus.available`
DAİMA `False` döner — roadmap'in "veri ön koşulu doğrulanmadan
başlanmamalı" ilkesine tam uyum.

**Bu modülün kapsamı (dürüstçe sınırlı):** Gerçek ölçülmüş yapısal
veri olmadığı için, bu modül GERÇEK bir mühendislik simülatörü OLARAK
SUNULMAZ (roadmap'in kalıcı dürüstlük notu: "3.B ... asla 'kesin
deprem simülatörü' olarak sunulmaz"). Bunun yerine, `physics.
building_shake.STRUCTURE_SHAKE_PROFILES`'daki (zaten var, Faz 3.A'da
tanımlı, DEĞİŞTİRİLMEDEN yeniden kullanıldı) kategorik doğal frekans
değerinden literatür-tipik kütle/rijitlik değerleri türetilir ve
GERÇEK bir Newmark-beta zaman-integrasyonlu çok-serbestlik-dereceli
(MDOF) kesme-çerçevesi (shear-frame) çözücüsü ile 3.A'nın kapalı-form
yaklaşıklığından daha ayrıntılı (kat-bazlı deplasman/ivme/öteleme-oranı
zaman geçmişi) ama YİNE DE tahmini parametrelerle çalışan bir mod
sunulur. Bu, roadmap'in "mühendislik-YAKIN" (mühendislik değil,
mühendisliğe yakın) tanımıyla birebir tutarlıdır.

Kapsanan roadmap maddeleri:
- **Veri ön koşulu doğrulaması** — `check_engineering_mode_precondition()`.
- **6.2/3.B çözücüsü** — `MDOFShearFrameModel`: Newmark-beta (ortalama
  ivme, koşulsuz kararlı) zaman-integrasyonu, tridiagonal kesme-çerçevesi
  rijitlik matrisi (Thomas algoritması ile O(N) çözüm, N=kat sayısı).
- **Kat-bazlı öteleme oranı (inter-story drift)** — `drift_based_damage_
  hint()`: ATC-40/FEMA 356 tarzı kamuya açık, standart mühendislik
  eşikleri (Immediate Occupancy / Life Safety / Collapse Prevention) —
  `physics.building_damage.DamageLevel` ile aynı 5 kademeye eşlenir
  (yeni bir sınıflandırma icat edilmedi).
- **Zorunlu dürüstlük etiketi** — `ENGINEERING_MODE_HONESTY_NOTE`, her
  `EngineeringShakeState`'e otomatik eklenir; `physics.building_damage.
  DAMAGE_HONESTY_NOTE_WITH_ENGINEERING_MODE` ile aynı ailede.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..hazard_data.risk_scoring import BasicBuildingType
from . import GroundShakeForceModel
from .building_damage import DamageLevel
from .building_shake import STRUCTURE_SHAKE_PROFILES, StructureShakeProfile

__all__ = [
    "EngineeringModeDataStatus",
    "check_engineering_mode_precondition",
    "ENGINEERING_MODE_HONESTY_NOTE",
    "FloorEstimatedProperties",
    "estimate_floor_properties",
    "EngineeringShakeState",
    "MDOFShearFrameModel",
    "DriftDamageHint",
    "drift_based_damage_hint",
]


# ======================================================================== #
# Veri ön koşulu doğrulaması (roadmap 3.B'nin "önce doğrula" şartı)
# ======================================================================== #


@dataclass(frozen=True, slots=True)
class EngineeringModeDataStatus:
    """`check_engineering_mode_precondition()`'ın döndürdüğü, kod içinde
    kalıcı olarak belgelenmiş doğrulama sonucu."""

    available: bool
    reason: str


def check_engineering_mode_precondition() -> EngineeringModeDataStatus:
    """Roadmap 3.B'nin zorunlu ilk adımı: `ai_reconstruction.
    building_analyzer.BuildingAnalysis`'in gerçek kolon/malzeme detay
    seviyesi taşıyıp taşımadığını doğrular.

    Sonuç (incelemeyle doğrulandı, kod-içi sabit): `BuildingAnalysis`
    yalnızca yükseklik/kat-sayısı/kullanım/stil/yaş/cephe-malzemesi
    taşır — kolon kesiti, donatı oranı, malzeme elastisite modülü gibi
    GERÇEK yapısal veri YOKTUR. Bu yüzden `available=False` sabit
    döner (gelecekte `ai_reconstruction`'a gerçek yapısal veri
    eklenirse, bu fonksiyon güncellenmeli — o zamana kadar bu modül her
    zaman \"tahmini parametre\" modunda çalışır, asla \"gerçek veri\"
    modunda değil)."""
    return EngineeringModeDataStatus(
        available=False,
        reason=(
            "ai_reconstruction.building_analyzer.BuildingAnalysis yalnızca "
            "height_m/floor_count/usage/architectural_style/estimated_age_"
            "years/facade_material/window_ratio taşır; kolon kesiti, "
            "donatı oranı, malzeme elastisite modülü gibi gerçek yapısal "
            "mühendislik verisi projede hiçbir yerde üretilmiyor. Bu "
            "yüzden MDOFShearFrameModel yalnızca STRUCTURE_SHAKE_PROFILES "
            "(Faz 3.A, kategorik/gösterge niteliğinde) frekans "
            "değerinden TAHMİNİ kütle/rijitlik türetir - gerçek ölçülmüş "
            "yapısal veriyle çalışmaz."
        ),
    )


#: Roadmap'in kalıcı dürüstlük notuyla (bölüm "Kalıcı dürüstlük notları")
#: birebir tutarlı - silinmeyecek.
ENGINEERING_MODE_HONESTY_NOTE = (
    "Bu 'mühendislik-yakın' sismik tepki, GERÇEK ölçülmüş yapısal veriyle "
    "(kolon/donatı/malzeme) çalışmaz - check_engineering_mode_precondition() "
    "bunu doğrular ve her zaman available=False döner. Kütle/rijitlik "
    "değerleri, Faz 3.A'nın kategorik doğal frekans profilinden türetilmiş "
    "TAHMİNİDİR. Bu bir 'kesin deprem simülatörü' DEĞİLDİR; Newmark-beta "
    "zaman-integrasyonu, 3.A'nın kapalı-form yaklaşıklığına göre daha "
    "ayrıntılı (kat-bazlı zaman geçmişi) ama yine de tahmini bir moddur."
)


# ======================================================================== #
# Kat kütlesi/rijitliği tahmini (literatür-tipik, gösterge niteliğinde)
# ======================================================================== #


#: kg/m^2 - tipik sismik ağırlık (ölü yük + hareketli yükün sismik payı)
#: kabaca değerleri, yapı tipine göre. Gösterge niteliğinde (kesin
#: mühendislik hesap tablosu değil) - `hazard_data.risk_scoring`'teki
#: TEMSILI_TEMEL_SKORLAR ile aynı disiplin.
_TYPICAL_SEISMIC_MASS_KG_PER_M2: dict[BasicBuildingType, float] = {
    BasicBuildingType.YIGMA: 550.0,
    BasicBuildingType.BETONARME_CERCEVE: 500.0,
    BasicBuildingType.BETONARME_PERDELI: 550.0,
    BasicBuildingType.CELIK_CERCEVE: 350.0,
    BasicBuildingType.AHSAP: 250.0,
}
_DEFAULT_SEISMIC_MASS_KG_PER_M2 = 500.0


@dataclass(frozen=True, slots=True)
class FloorEstimatedProperties:
    """Bir kat için tahmini (ÖLÇÜLMEMİŞ) kütle + öykü (story) rijitliği."""

    mass_kg: float
    story_stiffness_n_per_m: float
    source_note: str = (
        "Tahmini değer - gerçek kolon/malzeme verisi yok, bkz. "
        "check_engineering_mode_precondition()."
    )


def _uniform_shear_building_k_from_f1(
    *,
    mass_kg: float,
    num_floors: int,
    target_f1_hz: float,
) -> float:
    """N eşit-kütleli/eşit-rijitlikli, sabit-tabanlı kesme-çerçevesinin
    (uniform shear building) TEMEL moduna ait bilinen kapalı-form
    özdeğer çözümünü (Chopra, "Dynamics of Structures", standart shear-
    building özdeğer formülü) TERSİNE ÇEVİRİR: hedef bir birinci-mod
    doğal frekansı (f1, Faz 3.A'nın kategorik profilinden) veriliyorsa,
    bu frekansı üretecek öykü rijitliğini (k) çözer.

    omega_1 = 2*sqrt(k/m) * sin( pi / (2*(2N+1)) )
    => k = m * ( omega_1 / (2*sin(pi/(2*(2N+1)))) )^2

    Bu GERÇEK bir mühendislik ölçümü değildir - yalnızca kategorik bir
    hedef frekansı tutarlı bir rijitlik değerine çeviren analitik bir
    araçtır (girdi zaten tahminidir, çıktı da tahminidir - "çöp girer,
    çöp çıkar" ilkesiyle dürüstçe tutarlı)."""
    omega_1 = 2.0 * math.pi * target_f1_hz
    angle = math.pi / (2.0 * (2 * num_floors + 1))
    sin_term = math.sin(angle)
    if sin_term <= 1e-9:
        sin_term = 1e-9
    k = mass_kg * (omega_1 / (2.0 * sin_term)) ** 2
    return k


def estimate_floor_properties(
    *,
    structure_type: BasicBuildingType | None,
    floor_area_m2: float,
    num_floors: int,
) -> FloorEstimatedProperties:
    """Faz 3.A'nın `STRUCTURE_SHAKE_PROFILES`'ından (DEĞİŞTİRİLMEDEN)
    tahmini kat kütlesi + öykü rijitliği türetir. `structure_type=None`
    ise `physics.building_shake.DEFAULT_STRUCTURE_SHAKE_PROFILE` ile
    aynı düşüş (betonarme çerçeve, en yaygın/ortalama profil)."""
    if floor_area_m2 <= 0:
        raise ValueError("floor_area_m2 > 0 olmalı")
    if num_floors < 1:
        raise ValueError("num_floors >= 1 olmalı")

    mass_per_m2 = (
        _TYPICAL_SEISMIC_MASS_KG_PER_M2.get(
            structure_type,
            _DEFAULT_SEISMIC_MASS_KG_PER_M2,
        )
        if structure_type is not None
        else _DEFAULT_SEISMIC_MASS_KG_PER_M2
    )
    mass_kg = mass_per_m2 * floor_area_m2

    profile: StructureShakeProfile = (
        STRUCTURE_SHAKE_PROFILES.get(
            structure_type,
            STRUCTURE_SHAKE_PROFILES[BasicBuildingType.BETONARME_CERCEVE],
        )
        if structure_type is not None
        else STRUCTURE_SHAKE_PROFILES[BasicBuildingType.BETONARME_CERCEVE]
    )

    k = _uniform_shear_building_k_from_f1(
        mass_kg=mass_kg,
        num_floors=num_floors,
        target_f1_hz=profile.natural_frequency_hz,
    )
    return FloorEstimatedProperties(mass_kg=mass_kg, story_stiffness_n_per_m=k)


# ======================================================================== #
# MDOF kesme-çerçevesi + Newmark-beta zaman-integrasyonu
# ======================================================================== #


@dataclass(frozen=True, slots=True)
class EngineeringShakeState:
    """Bir zaman adımında, bir kat için MDOF çözücünün ürettiği durum."""

    floor_index: int  # 1..N (0 = sabit taban, dahil edilmez)
    displacement_m: float  # taban-göreli yatay yerdeğiştirme
    velocity_m_s: float
    acceleration_m_s2: float
    interstory_drift_ratio: float  # (bu kat - alt kat yerdeğiştirmesi) / kat yüksekliği
    honesty_note: str = ENGINEERING_MODE_HONESTY_NOTE


def _solve_tridiagonal(
    lower: list[float], diag: list[float], upper: list[float], rhs: list[float]
) -> list[float]:
    """Thomas algoritması (tridiagonal doğrusal sistem, O(N)) - kesme-
    çerçevesi rijitlik matrisinin doğal (tridiagonal) yapısını
    kullanır, genel amaçlı (O(N^3)) bir Gauss eliminasyonuna gerek
    yoktur."""
    n = len(diag)
    c_prime = [0.0] * n
    d_prime = [0.0] * n
    c_prime[0] = upper[0] / diag[0] if n > 1 else 0.0
    d_prime[0] = rhs[0] / diag[0]
    for i in range(1, n):
        denom = diag[i] - lower[i] * c_prime[i - 1]
        if abs(denom) < 1e-12:
            denom = 1e-12
        if i < n - 1:
            c_prime[i] = upper[i] / denom
        d_prime[i] = (rhs[i] - lower[i] * d_prime[i - 1]) / denom
    x = [0.0] * n
    x[-1] = d_prime[-1]
    for i in range(n - 2, -1, -1):
        x[i] = d_prime[i] - c_prime[i] * x[i + 1]
    return x


@dataclass
class MDOFShearFrameModel:
    """N-katlı, sabit-tabanlı kesme-çerçevesi (shear-frame) - her kat tek
    bir yatay serbestlik derecesi (lumped mass), komşu katlar arası öykü
    yayı ile bağlı. Newmark-beta (ortalama ivme yöntemi, beta=1/4,
    gamma=1/2 - koşulsuz kararlı, roadmap'in "sayısal stabilite" endişesini
    bilinçli olarak minimize eden standart mühendislik seçimi) ile zaman-
    integrasyonu yapılır.

    Girdi ivmesi `physics.GroundShakeForceModel.acceleration_at(t)`
    (Faz E17'den, DEĞİŞTİRİLMEDEN) - Faz 3.A ile AYNI zemin hareketi
    kaynağı, iki modun tutarlı/karşılaştırılabilir kalması için."""

    building_id: str
    shake_model: GroundShakeForceModel
    floor_properties: list[FloorEstimatedProperties]
    floor_height_m: float = 3.0
    damping_ratio: float = 0.05

    _u: list[float] = field(init=False, repr=False)  # yerdeğiştirme
    _v: list[float] = field(init=False, repr=False)  # hız
    _a: list[float] = field(init=False, repr=False)  # ivme
    _t: float = field(init=False, default=0.0)
    diverged: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if not self.floor_properties:
            raise ValueError("floor_properties boş olamaz - en az 1 kat gerekli")
        n = len(self.floor_properties)
        self._u = [0.0] * n
        self._v = [0.0] * n
        self._a = [0.0] * n
        self._alpha_mass_damping = self._estimate_mass_proportional_damping()

    @property
    def num_floors(self) -> int:
        return len(self.floor_properties)

    def _estimate_mass_proportional_damping(self) -> float:
        """Rayleigh sönümünün kütle-orantılı bileşeni (basitleştirme:
        rijitlik-orantılı terim ihmal edilir - roadmap'in "gerçek modal
        analiz değil" kapsam sınırıyla tutarlı, tam Rayleigh-damping
        eigen-çözümü YAPILMAZ). alpha = 2*zeta*omega_1, omega_1 ilk
        katın kütle/rijitliğinden kaba bir tahmin."""
        m0 = self.floor_properties[0].mass_kg
        k0 = self.floor_properties[0].story_stiffness_n_per_m
        omega_1_estimate = math.sqrt(k0 / m0) if m0 > 0 else 1.0
        return 2.0 * self.damping_ratio * omega_1_estimate

    def _assemble_tridiagonal(
        self, a0: float, a1: float
    ) -> tuple[list[float], list[float], list[float]]:
        """K_eff = K + a0*M + a1*C matrisinin tridiagonal köşegenlerini
        kurar (M, C köşegen olduğu için K_eff de tridiagonal kalır)."""
        n = self.num_floors
        lower = [0.0] * n
        diag = [0.0] * n
        upper = [0.0] * n
        for i in range(n):
            m_i = self.floor_properties[i].mass_kg
            k_i = self.floor_properties[i].story_stiffness_n_per_m  # i. kat ile alt kat arası
            k_ip1 = self.floor_properties[i + 1].story_stiffness_n_per_m if i + 1 < n else 0.0
            c_i = self._alpha_mass_damping * m_i
            diag[i] = (k_i + k_ip1) + a0 * m_i + a1 * c_i
            if i > 0:
                lower[i] = -self.floor_properties[i].story_stiffness_n_per_m
            if i < n - 1:
                upper[i] = -k_ip1
        return lower, diag, upper

    def step(self, dt: float) -> list[EngineeringShakeState]:
        """Newmark-beta ortalama-ivme yöntemiyle tek bir zaman adımı
        ilerletir; her kat için `EngineeringShakeState` listesi döner
        (`floor_index` 1'den başlar, 1 = zemine en yakın kat)."""
        if dt <= 0:
            raise ValueError("dt > 0 olmalı")
        beta, gamma = 0.25, 0.5
        n = self.num_floors

        a0 = 1.0 / (beta * dt * dt)
        a1 = gamma / (beta * dt)
        a2 = 1.0 / (beta * dt)
        a3 = 1.0 / (2.0 * beta) - 1.0
        a4 = gamma / beta - 1.0
        a5 = dt / 2.0 * (gamma / beta - 2.0)

        ground_accel = self.shake_model.acceleration_at(self._t + dt)

        lower, diag, upper = self._assemble_tridiagonal(a0, a1)

        rhs = [0.0] * n
        for i in range(n):
            m_i = self.floor_properties[i].mass_kg
            c_i = self._alpha_mass_damping * m_i
            force_i = -m_i * ground_accel  # göreli-yerdeğiştirme formülasyonu, eylemsizlik yükü
            rhs[i] = (
                force_i
                + m_i * (a0 * self._u[i] + a2 * self._v[i] + a3 * self._a[i])
                + c_i * (a1 * self._u[i] + a4 * self._v[i] + a5 * self._a[i])
            )

        u_new = _solve_tridiagonal(lower, diag, upper, rhs)

        states: list[EngineeringShakeState] = []
        prev_u = 0.0  # taban (kat 0) her zaman sabit
        for i in range(n):
            a_new = a0 * (u_new[i] - self._u[i]) - a2 * self._v[i] - a3 * self._a[i]
            v_new = self._v[i] + dt * ((1.0 - gamma) * self._a[i] + gamma * a_new)
            drift = (u_new[i] - prev_u) / max(self.floor_height_m, 1e-6)
            states.append(
                EngineeringShakeState(
                    floor_index=i + 1,
                    displacement_m=u_new[i],
                    velocity_m_s=v_new,
                    acceleration_m_s2=a_new,
                    interstory_drift_ratio=drift,
                )
            )
            self._u[i], self._v[i], self._a[i] = u_new[i], v_new, a_new
            prev_u = u_new[i]

        self._t += dt
        self._check_divergence()
        return states

    def _check_divergence(self) -> None:
        for value in (*self._u, *self._v, *self._a):
            if value != value or abs(value) > 1e6:  # NaN veya patlama
                self.diverged = True
                return


# ======================================================================== #
# Kat-bazlı öteleme oranı -> hasar ipucu (ATC-40/FEMA 356 tarzı, kamuya açık eşikler)
# ======================================================================== #


@dataclass(frozen=True, slots=True)
class DriftDamageHint:
    """`interstory_drift_ratio`'dan türeyen, `physics.building_damage.
    DamageLevel`'i (DEĞİŞTİRİLMEDEN, yeni sınıflandırma icat edilmedi)
    doğrudan kullanan bir GÖSTERGE - `building_damage`'daki risk-skoru
    tabanlı hesaplamanın YERİNE geçmez, çağıran taraf ikisini
    karşılaştırıp tutarlılık kontrolü yapabilir."""

    damage_level: DamageLevel
    drift_ratio: float
    threshold_label: str  # okunabilir eşik adı (IO/LS/CP)


#: Kamuya açık, standart mühendislik referans eşikleri (ATC-40 / FEMA 356
#: "Immediate Occupancy" / "Life Safety" / "Collapse Prevention" öteleme
#: oranı sınırları - literatürde yaygın kullanılan yaklaşık değerler,
#: belirli bir binaya özel mühendislik hesabı değildir).
_DRIFT_IO = 0.007  # Immediate Occupancy sınırı
_DRIFT_LS = 0.025  # Life Safety sınırı
_DRIFT_CP = 0.05  # Collapse Prevention sınırı


def drift_based_damage_hint(drift_ratio: float) -> DriftDamageHint:
    """Öteleme oranını, kamuya açık ATC-40/FEMA 356 sınırlarıyla
    karşılaştırıp `physics.building_damage.DamageLevel`'e (DEĞİŞTİRİLMEDEN)
    eşleyen bir hasar İPUCU döner (kesin hasar kararı değil)."""
    drift = abs(drift_ratio)
    if drift < _DRIFT_IO:
        return DriftDamageHint(
            damage_level=DamageLevel.NONE,
            drift_ratio=drift,
            threshold_label="< IO (Immediate Occupancy)",
        )
    if drift < _DRIFT_LS:
        return DriftDamageHint(
            damage_level=DamageLevel.LIGHT, drift_ratio=drift, threshold_label="IO..LS arası"
        )
    if drift < _DRIFT_CP:
        return DriftDamageHint(
            damage_level=DamageLevel.MODERATE, drift_ratio=drift, threshold_label="LS..CP arası"
        )
    if drift < _DRIFT_CP * 1.5:
        return DriftDamageHint(
            damage_level=DamageLevel.SEVERE,
            drift_ratio=drift,
            threshold_label=">= CP (Collapse Prevention)",
        )
    return DriftDamageHint(
        damage_level=DamageLevel.COLLAPSED,
        drift_ratio=drift,
        threshold_label=">> CP (muhtemel çökme)",
    )
