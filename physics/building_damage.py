"""
physics.building_damage — Roadmap V10 / Faz 4
==================================================

"Bina Hasarı/Yıkımı (kademeli)" fazının veri/mantık katmanı. `physics.
building_shake` (Faz 3.A) ile aynı disiplin: bu modül GPU/shader kodu
içermez, render motorunun tüketeceği deterministik bir hasar/yıkım
sözleşmesi üretir.

Kapsanan roadmap maddeleri:
- **4.1** — `compute_damage_level()` / `level1_damage_state()`: risk
  skoruna (`hazard_data.risk_scoring.RiskLevel`, zaten var — yeni bir
  sınıflandırma icat edilmedi) göre önceden tanımlı hasar durumu
  (opaklık + basit "çöküş" transform'u — `physics.building_shake` ile
  aynı offset/rotation sözleşmesi, yeni bir transform şekli icat
  edilmedi).
- **4.2** — `Level2CollapseSimulator`: kat-bazlı `physics.RigidBox` +
  `physics.PhysicsWorld` (Faz E17'den, DEĞİŞTİRİLMEDEN yeniden
  kullanıldı) ile gerçek devrilme/çöküş — her kat tek bir `RigidBox`,
  taban sarsıntısı `GroundShakeForceModel` ile tetiklenir.
- **4.3** — `precompute_fragments()` (4.3.1, ön-hesaplama, runtime
  DEĞİL — `mesh_engine.MeshSplitter.split_by_axis_plane` bu turda
  eklenen eksen-genel bölme birincil aracı olarak kullanıldı, yeni bir
  geometri motoru icat edilmedi) + `RuntimeFragmentTrigger` (4.3.2,
  runtime'da yalnızca rijit-cisim tetikleme — parçalar `RigidBox`
  olarak `PhysicsWorld`'e bırakılır, gerçek zamanlı fracture hesabı
  YAPILMAZ) + `is_fragmentation_eligible()` (4.3.3, kademeli devreye
  alma: yalnızca kamera-odaklı/tek "hikaye anı" bina).
- **4.4** — `DamagePersistenceStore`: senaryo bitince binanın hasarlı
  görsel durumda kalması (state persistence, sahne sıfırlanınca eski
  hâline dönmeme).
- **4.5** — `DAMAGE_HONESTY_NOTE`: dashboard'daki mevcut dürüstlük-notu
  deseniyle (`app_shell/session.py`) tutarlı, her hasar durumuna
  otomatik eklenen zorunlu etiket metni.

Dürüstlük notu (roadmap 4.5 + proje geneli dürüstlük deseniyle
tutarlı): bu modülün ürettiği "çöküş"/"yıkım" **temsili bir
animasyondur**, gerçek strüktürel çöküş simülasyonu (malzeme
gerilme/gevreklik/donatı modellemesi) DEĞİLDİR. `Level2CollapseSimulator`
`physics.RigidBox`'ın basit blok-fiziğini miras alır; `precompute_
fragments()` gerçek Voronoi kütüphanesi kullanmaz (proje stdlib-only) —
bunun yerine `mesh_engine.MeshSplitter`'ın eksen-hizalı düzlem
bölmesiyle bir grid-tabanlı ön-parçalama üretir (roadmap'in "10-30 parça
yeterli, aşırı detay gereksiz" notuyla tutarlı bir basitleştirme).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..hazard_data.risk_scoring import RiskLevel
from ..mesh_engine import Mesh3D, MeshSplitter
from . import GRAVITY, GroundShakeForceModel, PhysicsWorld, RigidBox

__all__ = [
    "DAMAGE_HONESTY_NOTE",
    "DamageLevel",
    "BuildingDamageState",
    "compute_damage_level",
    "level1_damage_state",
    "damage_state_for_level",
    "FloorCollapseState",
    "Level2CollapseSimulator",
    "FragmentPiece",
    "precompute_fragments",
    "is_fragmentation_eligible",
    "RuntimeFragmentTrigger",
    "DamagePersistenceStore",
]


# ============================================================================ #
# 4.5 — Dürüst etiketleme zorunluluğu
# ============================================================================ #

#: Roadmap 4.5: "Bu yıkım görselleştirmesi temsili bir animasyondur,
#: gerçek strüktürel çöküş simülasyonu değildir." — `app_shell/session.py`
#: içindeki mevcut dürüstlük-notu deseniyle (ör. SocialForceModel notu)
#: aynı üslupta, sabit metin (UI'da her zaman görünür olmalı).
DAMAGE_HONESTY_NOTE = (
    "Bu yıkım/hasar görselleştirmesi temsili bir animasyondur, gerçek "
    "strüktürel çöküş simülasyonu (malzeme gerilmesi/donatı/gevreklik "
    "modellemesi) değildir. Bağlayıcı bir mühendislik kararı için "
    "yetkili bir inşaat/deprem mühendisinden resmi değerlendirme "
    "gerekir."
)

#: Roadmap 4.5, ikinci fıkra: 3.B (mühendislik-yakın sismik mod) aktifse
#: bile not kaldırılmaz, yalnızca güncellenir.
DAMAGE_HONESTY_NOTE_WITH_ENGINEERING_MODE = (
    "Bu yıkım/hasar görselleştirmesi, ön tasarım seviyesinde "
    "mühendislik-yakın bir tahmin (Faz 3.B) kullanılarak üretildi; yine "
    "de gerçek strüktürel çöküş simülasyonu değildir. Bağlayıcı bir "
    "mühendislik kararı için yetkili bir inşaat/deprem mühendisinden "
    "resmi değerlendirme gerekir."
)


# ============================================================================ #
# 4.1 — Seviye 1: risk skoruna göre önceden tanımlı hasar durumu
# ============================================================================ #


class DamageLevel(str, Enum):
    """Roadmap 4.1'in "önceden tanımlı hasar durumu" kademeleri.
    `hazard_data.risk_scoring.RiskLevel` (4 kademe) ile 1:1 eşlenir -
    yeni bir sınıflandırma icat edilmedi, yalnızca görsel karşılığı
    eklendi."""

    NONE = "hasarsiz"
    LIGHT = "hafif"
    MODERATE = "orta"
    SEVERE = "agir"
    COLLAPSED = "cokme"


#: Roadmap 4.1: risk seviyesi -> varsayılan hasar seviyesi. Bu, yalnızca
#: statik/önceden-tanımlı bir eşlemedir (gerçek zamanlı sarsıntı şiddeti
#: `compute_damage_level()`'de ayrıca dikkate alınır - risk skoru "bu
#: bina ne kadar hassas", sarsıntı şiddeti "bu deprem ne kadar güçlü").
_RISK_TO_BASE_DAMAGE: dict[RiskLevel, DamageLevel] = {
    RiskLevel.LOW: DamageLevel.NONE,
    RiskLevel.MODERATE: DamageLevel.LIGHT,
    RiskLevel.HIGH: DamageLevel.MODERATE,
    RiskLevel.VERY_HIGH: DamageLevel.SEVERE,
}

#: Hasar seviyesi -> render opaklığı (1.0 = tam sağlam görünüm, düşük
#: değer = yıkık/eksik cephe hissi) ve "çöküş" olarak sayılıp
#: sayılmayacağı (COLLAPSED ise render bina yerine enkaz yığını
#: göstermeli - o karar render motorunda, burada yalnızca bayrak var).
_DAMAGE_OPACITY: dict[DamageLevel, float] = {
    DamageLevel.NONE: 1.0,
    DamageLevel.LIGHT: 0.92,
    DamageLevel.MODERATE: 0.75,
    DamageLevel.SEVERE: 0.55,
    DamageLevel.COLLAPSED: 0.25,
}

#: Hasar seviyesi -> ek statik eğim/çöküş (radyan) - Faz 3.A'nın geçici
#: sallanma rotasyonundan ayrı, KALICI bir yapısal eğim (4.4'ün "hasarlı
#: durumda kalmalı" ilkesiyle tutarlı).
_DAMAGE_TILT_RAD: dict[DamageLevel, float] = {
    DamageLevel.NONE: 0.0,
    DamageLevel.LIGHT: 0.01,
    DamageLevel.MODERATE: 0.04,
    DamageLevel.SEVERE: 0.12,
    DamageLevel.COLLAPSED: 0.55,
}


@dataclass(frozen=True, slots=True)
class BuildingDamageState:
    """Render'ın doğrudan tüketebileceği, bir bina için hasar
    sözleşmesi (Roadmap 4.1's "opaklık + çöküş transform'u")."""

    building_id: str
    damage_level: DamageLevel
    opacity: float
    permanent_tilt_rad: float
    is_collapsed: bool
    honesty_note: str = DAMAGE_HONESTY_NOTE


def compute_damage_level(
    risk_level: RiskLevel,
    *,
    peak_shake_intensity: float = 0.0,
) -> DamageLevel:
    """Roadmap 4.1: risk skoru tabanlı, isteğe bağlı olarak gerçekleşen
    sarsıntı şiddetiyle (Faz 3.A `BuildingShakeState.intensity`, 0..1)
    bir kademe daha ağırlaştırılan hasar seviyesi.

    Tasarım gerekçesi (dürüst, kara kutu olmayan kural): risk skoru
    "bu bina depreme ne kadar hassas" sorusunu, sarsıntı şiddeti "bu
    depremin o an ne kadar güçlü hissedildiğini" cevaplar - ikisi
    birlikte önceden-tanımlı bir kademe atlaması üretir (0.7 üzeri
    şiddet -> 1 kademe daha ağır, tam bir hasar mekaniği/olasılıksal
    model değil - roadmap 4.1'in "önceden tanımlı" kapsamıyla tutarlı).
    """
    base = _RISK_TO_BASE_DAMAGE[risk_level]
    if peak_shake_intensity < 0.7:
        return base
    levels = list(DamageLevel)
    idx = min(levels.index(base) + 1, len(levels) - 1)
    return levels[idx]


def level1_damage_state(
    building_id: str,
    risk_level: RiskLevel,
    *,
    peak_shake_intensity: float = 0.0,
    engineering_mode: bool = False,
) -> BuildingDamageState:
    """Roadmap 4.1'in tek-çağrılık üretim fonksiyonu."""
    level = compute_damage_level(risk_level, peak_shake_intensity=peak_shake_intensity)
    note = DAMAGE_HONESTY_NOTE_WITH_ENGINEERING_MODE if engineering_mode else DAMAGE_HONESTY_NOTE
    return damage_state_for_level(building_id, level, honesty_note=note)


def damage_state_for_level(
    building_id: str,
    level: DamageLevel,
    *,
    honesty_note: str = DAMAGE_HONESTY_NOTE,
) -> BuildingDamageState:
    """Web arayüzü entegrasyonu: `DamageLevel`'den (ör. Faz 3.B'nin
    `drift_based_damage_hint()` çıktısından) doğrudan render sözleşmesi
    üreten public yardımcı — `level1_damage_state()`'in risk-skoru
    tabanlı yolunun DIŞINDA, ama aynı opaklık/eğim tablolarını
    (DEĞİŞTİRİLMEDEN) yeniden kullanır."""
    return BuildingDamageState(
        building_id=building_id,
        damage_level=level,
        opacity=_DAMAGE_OPACITY[level],
        permanent_tilt_rad=_DAMAGE_TILT_RAD[level],
        is_collapsed=(level == DamageLevel.COLLAPSED),
        honesty_note=honesty_note,
    )


# ============================================================================ #
# 4.2 — Seviye 2: kat-bazlı RigidBox fizik motoruyla gerçek devrilme/çöküş
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class FloorCollapseState:
    """Bir katın çöküş simülasyonu sonundaki nihai durumu (render'ın
    kat mesh'ine uygulayacağı basit transform)."""

    floor_index: int
    position: tuple[float, float, float]
    orientation_rad: float
    toppled: bool


@dataclass
class Level2CollapseSimulator:
    """Roadmap 4.2: "Kat-bazlı RigidBox fizik motoruyla gerçek
    devrilme/çöküş". Her kat, `physics.RigidBox` olarak üst üste
    dizilir (Faz E17'nin `TowerStabilityScenario`'suyla aynı desen -
    yeni bir fizik dünyası icat edilmedi, aynı `PhysicsWorld` yeniden
    kullanıldı); risk skoru üzerinden türetilen bir "yapısal bütünlük"
    eşiği aşıldığında (yüksek risk = düşük dayanım) kolonlar zayıf
    kabul edilip devrilmeye izin verilir.
    """

    building_id: str
    num_floors: int
    floor_half_extents: tuple[float, float, float] = (5.0, 5.0, 1.5)
    floor_mass: float = 5000.0

    def build_world(
        self,
        peak_acceleration_g: float,
        *,
        frequency_hz: float = 1.8,
        structural_integrity: float = 0.5,
    ) -> PhysicsWorld:
        """`structural_integrity` (0..1, düşük = zayıf/yüksek risk):
        her kat teması `restitution`'unu düşürür ve devrilme eşiğini
        etkileyen sürtünmeyi azaltır - basit ama açık bir kural,
        gerçek bir malzeme/donatı modeli değil."""
        world = PhysicsWorld()
        ground = RigidBox(
            body_id=f"{self.building_id}_ground",
            position=(0.0, 0.0, -1.0),
            half_extents=(50.0, 50.0, 1.0),
            is_static=True,
            friction=0.3 + 0.6 * structural_integrity,
        )
        world.add_body(ground)

        hz = self.floor_half_extents[2]
        for i in range(self.num_floors):
            z = 2 * hz * i + hz
            floor = RigidBox(
                body_id=f"{self.building_id}_floor_{i}",
                position=(0.0, 0.0, z),
                half_extents=self.floor_half_extents,
                mass=self.floor_mass,
                restitution=0.02,
                friction=0.3 + 0.6 * structural_integrity,
            )
            world.add_body(floor)

        if peak_acceleration_g > 0:
            world.shake_model = GroundShakeForceModel(
                peak_acceleration_g=peak_acceleration_g,
                frequency_hz=frequency_hz,
            )
        return world

    def run(
        self,
        peak_acceleration_g: float,
        *,
        duration_s: float = 6.0,
        frequency_hz: float = 1.8,
        structural_integrity: float = 0.5,
    ) -> list[FloorCollapseState]:
        world = self.build_world(
            peak_acceleration_g,
            frequency_hz=frequency_hz,
            structural_integrity=structural_integrity,
        )
        world.run(duration_s)
        return [
            FloorCollapseState(
                floor_index=i,
                position=body.position,
                orientation_rad=body.orientation,
                toppled=body.is_toppled(),
            )
            for i, body in enumerate(b for b in world.bodies if not b.is_static)
        ]

    def any_floor_collapsed(self, states: list[FloorCollapseState]) -> bool:
        return any(s.toppled for s in states)


# ============================================================================ #
# 4.3 — Seviye 3: parçalı gerçek yıkım fiziği / mesh fragmentasyonu
# ============================================================================ #


@dataclass(frozen=True, slots=True)
class FragmentPiece:
    """Roadmap 4.3.1'in ön-hesaplanmış tek bir parçası - editör/pipeline
    aşamasında üretilir, runtime'da yalnızca okunur."""

    piece_id: str
    mesh: Mesh3D
    center: tuple[float, float, float]
    half_extents: tuple[float, float, float]


def _mesh_center_and_half_extents(
    mesh: Mesh3D,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    (min_x, min_y, min_z), (max_x, max_y, max_z) = mesh.bounding_box()
    center = ((min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0)
    half = (
        max((max_x - min_x) / 2.0, 0.05),
        max((max_y - min_y) / 2.0, 0.05),
        max((max_z - min_z) / 2.0, 0.05),
    )
    return center, half


def precompute_fragments(
    mesh: Mesh3D,
    *,
    building_id: str,
    pieces_x: int = 3,
    pieces_y: int = 3,
) -> list[FragmentPiece]:
    """Roadmap 4.3.1: "Ön-hesaplanmış fragmentasyon (runtime değil,
    hazırlık aşamasında)". Bina mesh'ini `mesh_engine.MeshSplitter.
    split_by_axis_plane` (bu oturumda eklenen eksen-genel bölme) ile
    bir X x Y grid'ine ayırır - roadmap'in kendi notuyla tutarlı bir
    dürüstlük ayrımı: bu **gerçek Voronoi değildir** (proje stdlib-only,
    hesaplamalı geometri kütüphanesi yok), eksen-hizalı bir grid-tabanlı
    yaklaşıklıktır; roadmap'in hedeflediği "10-30 parça, aşırı detay
    gereksiz" pratik sonucu aynı şekilde elde edilir.

    Boş (üçgen içermeyen) hücreler sonuca dahil edilmez.
    """
    if pieces_x < 1 or pieces_y < 1:
        raise ValueError("pieces_x/pieces_y en az 1 olmalı")
    (min_x, min_y, _min_z), (max_x, max_y, _max_z) = mesh.bounding_box()

    # X ekseninde pieces_x parçaya böl.
    x_slices: list[Mesh3D] = [mesh]
    if pieces_x > 1:
        x_slices = []
        remainder = mesh
        for i in range(1, pieces_x):
            cut = min_x + (max_x - min_x) * (i / pieces_x)
            lo, remainder = MeshSplitter.split_by_axis_plane(remainder, axis=0, value=cut)
            x_slices.append(lo)
        x_slices.append(remainder)

    # Her X diliminde Y ekseninde pieces_y parçaya böl.
    fragments: list[FragmentPiece] = []
    piece_num = 0
    for x_slice in x_slices:
        if x_slice.triangle_count() == 0:
            continue
        y_slices: list[Mesh3D] = [x_slice]
        if pieces_y > 1:
            y_slices = []
            remainder = x_slice
            for i in range(1, pieces_y):
                cut = min_y + (max_y - min_y) * (i / pieces_y)
                lo, remainder = MeshSplitter.split_by_axis_plane(remainder, axis=1, value=cut)
                y_slices.append(lo)
            y_slices.append(remainder)

        for piece_mesh in y_slices:
            if piece_mesh.triangle_count() == 0:
                continue
            center, half = _mesh_center_and_half_extents(piece_mesh)
            piece_mesh.name = f"{building_id}_frag_{piece_num}"
            fragments.append(
                FragmentPiece(
                    piece_id=piece_mesh.name,
                    mesh=piece_mesh,
                    center=center,
                    half_extents=half,
                )
            )
            piece_num += 1
    return fragments


def is_fragmentation_eligible(
    *,
    is_camera_focused: bool,
    distance_to_camera_m: float,
    focus_distance_threshold_m: float = 40.0,
) -> bool:
    """Roadmap 4.3.3: "Bu seviye sadece 'hikaye anı' binalar için (kamera
    yakın, kullanıcı odaklanmış) aktif olmalı; şehir genelinde yüzlerce
    bina aynı anda tam fragmentasyonla çökmemeli". Açık, tek-koşullu bir
    kapı fonksiyonu - kara kutu bir "önem skoru" değil."""
    return is_camera_focused and distance_to_camera_m <= focus_distance_threshold_m


@dataclass
class RuntimeFragmentTrigger:
    """Roadmap 4.3.2: "Runtime'da sadece rijit-cisim tetikleme".
    Önceden hazırlanmış `FragmentPiece` listesini, `physics.RigidBox`/
    `PhysicsWorld`'e (Faz E17, değiştirilmeden) tek seferlik bir
    "serbest bırakma" impulsuyla teslim eder - gerçek zamanlı fracture
    hesabı burada YOKTUR, yalnızca zaten var olan parçaların fiziği
    çalıştırılır."""

    building_id: str

    def trigger(
        self,
        fragments: list[FragmentPiece],
        *,
        peak_acceleration_g: float,
        is_camera_focused: bool,
        distance_to_camera_m: float,
    ) -> PhysicsWorld | None:
        """4.3.3 kapısından geçmezse `None` döner (çağıran taraf Seviye
        1/2'ye düşmeli - roadmap: "Uzak binalar Seviye 1/2'de kalmalı").
        Geçerse, her parça bir `RigidBox` olarak dünyaya eklenip PGA'dan
        türetilen başlangıç impulsu uygulanır (yerçekimi + yatay itki)."""
        if not is_fragmentation_eligible(
            is_camera_focused=is_camera_focused,
            distance_to_camera_m=distance_to_camera_m,
        ):
            return None
        world = PhysicsWorld()
        ground = RigidBox(
            body_id=f"{self.building_id}_frag_ground",
            position=(0.0, 0.0, -1.0),
            half_extents=(100.0, 100.0, 1.0),
            is_static=True,
        )
        world.add_body(ground)
        # Roadmap 4.3.2: "başlangıç impulsu risk skoru/PGA'dan türetilir".
        impulse_speed = min(peak_acceleration_g, 2.0) * GRAVITY * 0.3
        for frag in fragments:
            mass = max(
                frag.half_extents[0] * frag.half_extents[1] * frag.half_extents[2] * 800.0, 5.0
            )
            body = RigidBox(
                body_id=frag.piece_id,
                position=frag.center,
                half_extents=frag.half_extents,
                mass=mass,
                velocity=(impulse_speed * 0.2, impulse_speed * 0.2, 0.0),
                restitution=0.1,
            )
            world.add_body(body)
        return world


# ============================================================================ #
# 4.4 — Hasar sonrası kalıcı durum
# ============================================================================ #


@dataclass
class DamagePersistenceStore:
    """Roadmap 4.4: "Yıkılan/hasar gören bina, senaryo bitiminde
    'hasarlı' görsel durumda kalmalı - sahne sıfırlanınca eski hâline
    dönmemeli". Basit, süreç-içi (in-memory) bir anahtar-değer deposu;
    `(scenario_id, building_id)` çiftini `BuildingDamageState`'e eşler.

    Kapsam notu (dürüst): bu depo yalnızca bir çalışma oturumu içinde
    kalıcıdır (proses hafızası) - uygulama yeniden başlatıldığında
    sıfırlanır. Oturumlar-arası (disk/DB) kalıcılık için `persistence.
    project_manager.ProjectManager`'a bağlanmak ayrı, sonraki bir adım
    olarak roadmap notlarında bırakılmıştır (bu oturumun kapsamı
    dışında - PROGRESS.md'de kayıtlı tutulmalı)."""

    _states: dict[tuple[str, str], BuildingDamageState] = field(default_factory=dict)

    def record(self, scenario_id: str, state: BuildingDamageState) -> None:
        """Yalnızca hasar kademesi ARTARSA (veya ilk kez kaydediliyorsa)
        güncelle - "sahne sıfırlanınca eski hâline dönmemeli" ilkesi,
        yanlışlıkla daha hafif bir duruma geri düşmeyi de engellemeli
        (ör. animasyon ara karesi hasar seviyesini geçici düşürmemeli)."""
        key = (scenario_id, state.building_id)
        existing = self._states.get(key)
        if existing is None or _damage_rank(state.damage_level) >= _damage_rank(
            existing.damage_level
        ):
            self._states[key] = state

    def get(self, scenario_id: str, building_id: str) -> BuildingDamageState | None:
        return self._states.get((scenario_id, building_id))

    def all_for_scenario(self, scenario_id: str) -> list[BuildingDamageState]:
        return [s for (sid, _bid), s in self._states.items() if sid == scenario_id]

    def reset_scenario(self, scenario_id: str) -> int:
        """Kullanıcının açıkça "senaryoyu tamamen sıfırla" istediği
        (yeni bir senaryo başlatma - roadmap 4.4'ün istisnası, "sahne
        sıfırlanınca" ifadesi kamera/zaman sıfırlamasını kastediyor,
        yeni-senaryo-başlatmayı değil) nadir durum için - varsayılan
        akışta ÇAĞRILMAMALI."""
        keys = [k for k in self._states if k[0] == scenario_id]
        for k in keys:
            del self._states[k]
        return len(keys)


def _damage_rank(level: DamageLevel) -> int:
    return list(DamageLevel).index(level)
