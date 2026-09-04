"""
physics.building_shake — Roadmap V10 / Faz 3.A
==================================================

"Bina Fiziği: Sallanma — Standart seviye (varsayılan, tüm binalar için) —
inandırıcı temsil" fazının **veri/mantık katmanı**. `agent_visuals.py`
(Faz 2) ile aynı disiplin: bu modül GPU/shader kodu içermez, render
motorunun vertex-shader'da uygulayacağı deterministik bir **bina kök
transform sözleşmesi** üretir (roadmap 3.1: "vertex-shader tabanlı rijit
sallanma" — shader'ın kendisi render tarafında, girdisi burada).

Kapsanan roadmap maddeleri:
- **3.1** — `physics.GroundShakeForceModel` (zaten var, `RigidBox` fizik
  dünyasında kullanılıyordu) buraya **bina kök transform'u** üretecek
  şekilde yeniden kullanıldı (yeni bir sallanma modeli icat edilmedi).
- **3.2** — `BuildingShakeSimulator.state_at()`: genlik `floor_index`
  arttıkça büyür (basit, açık bir doğrusal-üstü yükseklik çarpanı).
- **3.3** — `panic_probability_from_intensity()` + `apply_shake_panic()`:
  sallanma şiddeti, ajan davranış motoruna (`mobility.crowd_simulation`)
  geri besleme olarak bağlanır.
- **3.4** — `STRUCTURE_SHAKE_PROFILES`: `hazard_data.risk_scoring.
  BasicBuildingType`'ın (zaten var, roadmap'in "hazard_data'daki bina
  yapı tipi bilgisi" notuyla doğrulandı) her kategorisi için 3-4 kategorik
  frekans/sönüm profili — gerçek modal analiz değil, roadmap'in açıkça
  belirttiği "kategorik farklılaşma" kapsamı.
- **3.5** — `debris_particle_state()`: sallanma şiddeti eşiği aşıldığında
  döküntü parçacık sistemi tetikleme sözleşmesi (oran + parçacık tipi).

Dürüstlük notu (roadmap 0. + kalıcı dürüstlük notlarıyla tutarlı): bu,
gerçek bir yapısal mühendislik/modal analiz değildir — `physics.
GroundShakeForceModel`'in kendi docstring'inde belirtildiği gibi
"pseudo-static", sinüzoidal bir yaklaşımdır. Faz 3.B (mühendislik-yakın
SDOF/shear-frame modu) bu modülün kapsamı DIŞINDADIR; ayrı, ihtiyari bir
katman olarak roadmap'te tanımlı kalır.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from ..hazard_data.risk_scoring import BasicBuildingType
from . import GRAVITY, GroundShakeForceModel

__all__ = [
    "StructureShakeProfile",
    "STRUCTURE_SHAKE_PROFILES",
    "BuildingShakeState",
    "BuildingShakeSimulator",
    "panic_probability_from_intensity",
    "apply_shake_panic",
    "DebrisParticleState",
    "debris_particle_state",
]


# ============================================================================ #
# 3.4 — Yapısal tip farkı (kategorik frekans/sönüm profili)
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class StructureShakeProfile:
    """Roadmap 3.4: "gerçek modal analiz değil ama kategorik
    farklılaşma". `natural_frequency_hz` binanın karakteristik salınım
    frekansı (yaklaşık — literatürde tipik yapı-tipi eğilimleriyle
    kalibre edilmiş bir *gösterge* değeri, belirli bir binanın ölçülmüş
    doğal periyodu değil), `damping_ratio` (0-1) sönümlenme oranı
    (yüksek = titreşim daha hızlı söner)."""

    natural_frequency_hz: float
    damping_ratio: float
    label: str


#: Roadmap 3.4 örnek eşleme: "yığma: yüksek frekans, düşük sönüm",
#: "betonarme: orta frekans/sönüm". `BasicBuildingType`
#: (`hazard_data.risk_scoring`, FEMA P-154 tarzı kaba tipoloji) burada
#: yeniden kullanıldı — yeni bir yapı-tipi sınıflandırması icat edilmedi.
#: Değerler gösterge niteliğindedir (risk_scoring.py'deki TEMSILI_TEMEL_
#: SKORLAR ile aynı disiplin — kesin mühendislik değeri değildir).
STRUCTURE_SHAKE_PROFILES: dict[BasicBuildingType, StructureShakeProfile] = {
    BasicBuildingType.YIGMA: StructureShakeProfile(
        natural_frequency_hz=4.0,
        damping_ratio=0.03,
        label="Yığma — yüksek frekans, düşük sönüm (gevrek, sert davranış)",
    ),
    BasicBuildingType.BETONARME_CERCEVE: StructureShakeProfile(
        natural_frequency_hz=2.2,
        damping_ratio=0.05,
        label="Betonarme çerçeve — orta frekans/sönüm",
    ),
    BasicBuildingType.BETONARME_PERDELI: StructureShakeProfile(
        natural_frequency_hz=2.8,
        damping_ratio=0.07,
        label="Betonarme perdeli — perde duvar rijitliği nedeniyle "
        "çerçeveye göre biraz daha yüksek frekans/sönüm",
    ),
    BasicBuildingType.CELIK_CERCEVE: StructureShakeProfile(
        natural_frequency_hz=1.4,
        damping_ratio=0.04,
        label="Çelik çerçeve — düşük frekans (esnek), sünek davranış "
        "nedeniyle nispeten daha yüksek enerji sönümü",
    ),
    BasicBuildingType.AHSAP: StructureShakeProfile(
        natural_frequency_hz=3.2,
        damping_ratio=0.06,
        label="Ahşap — hafif, orta-yüksek frekans/sönüm",
    ),
}

#: `STRUCTURE_SHAKE_PROFILES`'ta yapı tipi bulunamazsa (veri eksik —
#: roadmap 3.B.2'nin "bu veri yoksa çalışamaz, dürüstçe etiketlenmeli"
#: notuyla aynı dürüstlük ilkesi 3.A için de geçerli, ama 3.A veri
#: eksikliğinde tamamen durmaz, güvenli bir varsayılana düşer): betonarme
#: çerçeve, en yaygın/ortalama profil olduğu için varsayılan seçildi.
DEFAULT_STRUCTURE_SHAKE_PROFILE = STRUCTURE_SHAKE_PROFILES[BasicBuildingType.BETONARME_CERCEVE]


#: Roadmap V10 arayüz entegrasyonu: projenin bina üretim tipolojisi
#: (`building_reconstruction.procedural_generator.BuildingType` — örn.
#: "apartman", "ofis", "fabrika") ile bu modülün/`hazard_data.risk_scoring`
#: taşıyıcı-sistem tipolojisi (`BasicBuildingType`) FARKLI eksenlerdedir
#: (biri kullanım amacı, diğeri taşıyıcı sistem) — aralarında kesin bir
#: eşleme YOKTUR. Bu, dashboard'da bir bina seçildiğinde makul bir
#: varsayılan taşıyıcı-sistem tipi göstermek için KABA, açıkça etiketli
#: bir sezgisel eşleme (gerçek malzeme/donatı verisi olmadığı için
#: `building_shake_engineering.check_engineering_mode_precondition()`
#: ile aynı dürüstlük ilkesi geçerli — bu sadece görsel/oyunsal bir
#: varsayılan seçimdir, mühendislik tespiti değildir).
USAGE_TO_STRUCTURE_TYPE_HEURISTIC: dict[str, BasicBuildingType] = {
    "apartman": BasicBuildingType.BETONARME_CERCEVE,
    "ofis": BasicBuildingType.BETONARME_PERDELI,
    "avm": BasicBuildingType.BETONARME_PERDELI,
    "hastane": BasicBuildingType.BETONARME_PERDELI,
    "okul": BasicBuildingType.BETONARME_CERCEVE,
    "villa": BasicBuildingType.AHSAP,
    "fabrika": BasicBuildingType.CELIK_CERCEVE,
    "sanayi_tesisi": BasicBuildingType.CELIK_CERCEVE,
    "depo": BasicBuildingType.CELIK_CERCEVE,
    "hangar": BasicBuildingType.CELIK_CERCEVE,
    "terminal": BasicBuildingType.CELIK_CERCEVE,
    "stadyum": BasicBuildingType.CELIK_CERCEVE,
}


def structure_type_for_usage(building_type: str | None) -> BasicBuildingType | None:
    """`USAGE_TO_STRUCTURE_TYPE_HEURISTIC`'ten kaba bir varsayılan döner;
    eşleşme yoksa `None` (çağıran, `DEFAULT_STRUCTURE_SHAKE_PROFILE`'a
    düşer — sessizce yanlış bir tip UYDURULMAZ)."""
    if not building_type:
        return None
    return USAGE_TO_STRUCTURE_TYPE_HEURISTIC.get(str(building_type).lower())


# ============================================================================ #
# 3.1 / 3.2 — Bina kök transform + kat-bazlı genlik
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class BuildingShakeState:
    """Render'ın vertex-shader'da doğrudan uygulayabileceği, bir bina/kat
    için o andaki sallanma durumu."""

    building_id: str
    floor_index: int
    horizontal_offset_m: tuple[float, float]  # (x, y) — bina kökünden yatay kayma
    rotation_rad: float  # basit rijit eğim (taban etrafında)
    intensity: float  # 0..1 normalize şiddet (görsel/ses/panik ortak girdisi)


@dataclass
class BuildingShakeSimulator:
    """`GroundShakeForceModel`'i (Faz E17'den, `physics.__init__`) bir
    binanın kök transform'una bağlar.

    Roadmap 3.1: "gerçek bina root transform'una vertex-shader tabanlı
    rijit sallanma" — burada üretilen `BuildingShakeState` tam olarak bu
    transform verisidir; her bina tek bir rijit gövde gibi ele alınır
    (Faz 3.B'nin çok-kütleli/shear-frame modelinden farklı olarak, burada
    kasıtlı olarak basit bir sönümlü-salınım yaklaşımı kullanılır — bu
    ayrım roadmap 3.A/3.B'nin kendi ayrımıdır).
    """

    building_id: str
    shake_model: GroundShakeForceModel
    structure_type: BasicBuildingType | None = None
    num_floors: int = 1
    floor_height_m: float = 3.0
    # Roadmap 3.2: "yükseklikle artan genlik" - kat başına genlik artış
    # oranı (0.15 => en üst kat, zemin kata göre kabaca %15*kat-sayısı
    # daha fazla salınır - basit doğrusal büyüme, roadmap'in "kat bazlı"
    # notuyla tutarlı, karmaşık bir mod-şekli fonksiyonu değil).
    amplitude_per_floor: float = 0.15
    # Roadmap 3.5: döküntü eşiği (yerel ivme, g cinsinden).
    debris_threshold_g: float = 0.15

    def _profile(self) -> StructureShakeProfile:
        if self.structure_type is None:
            return DEFAULT_STRUCTURE_SHAKE_PROFILE
        return STRUCTURE_SHAKE_PROFILES.get(self.structure_type, DEFAULT_STRUCTURE_SHAKE_PROFILE)

    def state_at(self, t: float, floor_index: int) -> BuildingShakeState:
        """Roadmap 3.1+3.2+3.4'ün birleşik çıktısı.

        Zemin ivmesi `GroundShakeForceModel.acceleration_at(t)`'ten
        (Faz E17, değiştirilmeden yeniden kullanıldı) alınır; bina/kat,
        yapı tipinin karakteristik frekansı/sönümüyle **sönümlü harmonik
        tepki** verir (basit ikinci dereceden lineer yaklaşım — tam bir
        zaman-integrasyonlu Newmark-beta çözücü DEĞİLDİR, roadmap'in
        "modal analiz değil" kapsam sınırıyla tutarlı bir kapalı-form
        yaklaşıklık).
        """
        if floor_index < 0:
            raise ValueError("floor_index negatif olamaz")
        profile = self._profile()
        ground_accel = self.shake_model.acceleration_at(t)

        omega_n = 2.0 * math.pi * profile.natural_frequency_hz
        zeta = profile.damping_ratio
        drive_omega = 2.0 * math.pi * self.shake_model.frequency_hz

        # Sönümlü zorlanmış-titreşimin kapalı-form genlik büyütme oranı
        # (klasik tek-serbestlik-dereceli sistem transfer fonksiyonu,
        # Rayleigh/SDOF ön-tasarım yaklaşımlarında da kullanılan standart
        # form): r = drive_omega/omega_n, büyütme = 1 / sqrt((1-r^2)^2 + (2*zeta*r)^2)
        r = drive_omega / omega_n if omega_n > 1e-6 else 0.0
        denom = math.sqrt((1.0 - r * r) ** 2 + (2.0 * zeta * r) ** 2)
        amplification = 1.0 / denom if denom > 1e-6 else 1.0
        # Aşırı rezonans durumunda (r≈1, zeta küçük) büyütme sınırsız
        # büyüyebilir - görsel/oyun bağlamında makul bir tavanla kesiliyor
        # (dürüstlük: bu bir güvenlik/estetik sınırlaması, fiziksel bir
        # iddia değil).
        amplification = min(amplification, 8.0)

        floor_multiplier = 1.0 + self.amplitude_per_floor * floor_index
        offset_x = ground_accel * amplification * floor_multiplier / GRAVITY
        # Basit ikincil eksen bileşeni (görsel çeşitlilik için, birincil
        # eksenin küçük bir kesri - gerçek 2 eksenli modal analiz değil).
        offset_y = offset_x * 0.15 * math.sin(drive_omega * t * 0.5)

        height_m = self.floor_height_m * floor_index
        rotation_rad = math.atan2(offset_x, max(height_m, self.floor_height_m)) * 0.3

        # 0..1 normalize şiddet: debris/panik/ses katmanlarının ortak
        # girdisi. `debris_threshold_g`'yi 1.0'a eşleyen basit bir oran.
        local_peak_accel_g = abs(ground_accel) / GRAVITY * amplification * floor_multiplier
        intensity = (
            min(local_peak_accel_g / max(self.debris_threshold_g, 1e-6) * 0.5, 1.0)
            if self.debris_threshold_g > 0
            else 0.0
        )

        return BuildingShakeState(
            building_id=self.building_id,
            floor_index=floor_index,
            horizontal_offset_m=(offset_x, offset_y),
            rotation_rad=rotation_rad,
            intensity=max(0.0, min(intensity, 1.0)),
        )

    def local_peak_acceleration_g(self, t: float, floor_index: int) -> float:
        """3.5'in eşik kontrolü için ham (g cinsinden) yerel ivmeyi
        döner - `state_at()`'in normalize `intensity`'sinden ayrı olarak,
        çağıranın kendi eşik mantığını kurabilmesi için ham veri de açık
        tutuluyor (kara kutu değil)."""
        state = self.state_at(t, floor_index)
        # offset_x zaten ground_accel*amplification*floor_multiplier/GRAVITY
        # olarak hesaplandı - g cinsine geri çevirmek yerine, tutarlılık
        # için doğrudan aynı ara hesaplamayı tekrar üretmek yerine devlet
        # nesnesinden türetiyoruz (yaklaşık, birincil eksen büyüklüğü).
        return abs(state.horizontal_offset_m[0])


# ============================================================================ #
# 3.3 — Sallanma -> ajan panik/rota geri beslemesi
# ============================================================================ #


def panic_probability_from_intensity(intensity: float) -> float:
    """Roadmap 3.3: sallanma şiddeti arttıkça panik olasılığı artar.

    Basit, açık (kara kutu olmayan) parçalı-doğrusal bir eşleme:
    intensity < 0.2  -> 0.0 (fark edilir ama panik tetiklemez)
    0.2 <= intensity < 0.6 -> 0..0.5 arası doğrusal artış
    intensity >= 0.6 -> 0.5..1.0 arası doğrusal artış (0.6'da 0.5, 1.0'da 1.0)
    """
    intensity = max(0.0, min(intensity, 1.0))
    if intensity < 0.2:
        return 0.0
    if intensity < 0.6:
        return (intensity - 0.2) / 0.4 * 0.5
    return 0.5 + (intensity - 0.6) / 0.4 * 0.5


def apply_shake_panic(agents: list, intensity: float, *, seed: int = 0) -> int:
    """Roadmap 3.3'ün somut bağlantı noktası: `mobility.crowd_simulation.
    Agent` listesini alır, `panic_probability_from_intensity(intensity)`
    olasılığına göre (deterministik, agent_id+seed tabanlı hash — Faz
    1.5/2.4'teki aynı determinizm deseni) bazı agent'ları `PANIC`
    durumuna geçirir. Zaten `PANIC`/`evacuated` olan agent'lara
    dokunulmaz. Dönen değer, panik durumuna **yeni geçen** agent sayısıdır
    (çağıran taraf bunu olay-günlüğüne/realism_audit'e yazabilir).

    `Agent`/`AgentBehavior` burada tip olarak import edilmiyor (döngüsel
    bağımlılığı önlemek için `duck typing` kullanılıyor - agent nesnesinin
    yalnızca `agent_id`, `behavior`, `evacuated` alanları okunur/yazılır);
    bu, `population.synthetic_population.individual_group_ids()`'te de
    kullanılan aynı "tek yönlü bağımlılık" deseniyle tutarlıdır.
    """
    probability = panic_probability_from_intensity(intensity)
    if probability <= 0.0:
        return 0
    from ..mobility.crowd_simulation import AgentBehavior

    newly_panicked = 0
    for agent in agents:
        if getattr(agent, "evacuated", False):
            continue
        if agent.behavior == AgentBehavior.PANIC:
            continue
        digest = hashlib.sha256(f"shake:{seed}:{agent.agent_id}".encode()).digest()
        roll = digest[0] / 255.0
        if roll < probability:
            agent.behavior = AgentBehavior.PANIC
            newly_panicked += 1
    return newly_panicked


# ============================================================================ #
# 3.5 — Enkaz/toz parçacık efekti
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class DebrisParticleState:
    """Roadmap 3.5: sallanma şiddeti eşiği aşıldığında render'ın
    tetikleyeceği basit parçacık sistemi sözleşmesi. `emit=False` ise
    render hiçbir parçacık üretmemeli (varsayılan, dürüst sessizlik)."""

    emit: bool
    particle_type: str  # "cam_kirigi" | "siva_tozu" | ""
    emission_rate_per_s: float  # 0 ise emit=False ile tutarlı


def debris_particle_state(
    intensity: float,
    *,
    glass_threshold: float = 0.35,
    dust_threshold: float = 0.15,
) -> DebrisParticleState:
    """Roadmap 3.5: "Sallanma şiddeti eşiği aşıldığında binadan küçük
    döküntü parçacıkları (cam kırığı, sıva tozu) düşen basit parçacık
    sistemi". İki kademeli eşik: düşük şiddette sıva tozu, yüksek
    şiddette (daha nadir/daha ciddi görünüm için) cam kırığı da eklenir.
    Emisyon oranı, eşiği aşan fazla şiddetle orantılı (doğrusal, tavanlı)
    - ani "hep ya da hiç" bir parçacık patlaması değil.
    """
    intensity = max(0.0, min(intensity, 1.0))
    if intensity < dust_threshold:
        return DebrisParticleState(emit=False, particle_type="", emission_rate_per_s=0.0)
    if intensity < glass_threshold:
        rate = (intensity - dust_threshold) / max(glass_threshold - dust_threshold, 1e-6) * 20.0
        return DebrisParticleState(
            emit=True, particle_type="siva_tozu", emission_rate_per_s=round(rate, 2)
        )
    rate = 20.0 + (intensity - glass_threshold) / max(1.0 - glass_threshold, 1e-6) * 40.0
    return DebrisParticleState(
        emit=True, particle_type="cam_kirigi", emission_rate_per_s=round(rate, 2)
    )
