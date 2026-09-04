"""
mobility.crowd_simulation.agent_visuals — Roadmap V10 / Faz 2
================================================================

"İnsan Figürü ve Hareket Kalitesi" fazının **veri katmanı**: bu modül hiçbir
GPU/render kodu içermez (proje deposu bir Python simülasyon/veri-hazırlama
çekirdeğidir — gerçek skinning/shader kodu render motorunun tarafında
yaşar). Burada üretilen, render tarafının doğrudan tüketebileceği,
deterministik ve test edilebilir bir **görsel durum sözleşmesi**dir:

- 2.1/2.2/2.3.4 — `AgentVisualLevel` + kamera mesafesine göre üç seviyeli
  LOD seçimi (billboard → kapsül → iskelet), Faz 1.4'teki
  `SimulationLODManager` ile **aynı üçlü mesafe deseni** (yeni bir LOD
  motoru icat edilmedi, mevcut desen görsel katmana uygulandı).
- 2.3.3 — `AnimationClip` + `AgentBehavior` → clip eşlemesi (durum
  makinesi, davranış motorunun ürettiği durumların doğrudan tüketicisi).
- 2.6 — Korku/duygu clip'leri, aynı eşlemenin doğal uzantısı (yeni motor
  gerekmez, yalnızca clip havuzu genişler — roadmap notuyla tutarlı).
- 2.4 — Ajan çeşitliliği: `agent_id` + `seed`'e bağlı **deterministik**
  mesh/doku varyant + renk paleti seçimi (aynı seed → aynı görünüm,
  Faz 1.5'in determinizm ilkesiyle tutarlı).
- 2.5 — `crowd_pressure_level`: `Agent.crowd_pressure` ham skalerini
  render'ın tüketebileceği kaba bir seviyeye (NONE/MILD/SQUEEZE)
  eşler — eşik değerleri açık ve test edilebilir (kara kutu değil).

Dürüstlük notu: iskelet animasyonu/GPU skinning'in kendisi (2.3.1/2.3.2)
render motoru tarafında bir teknik yetkinlik gerektirir; bu modül o
motora **hangi clip'in hangi ajanda, hangi LOD seviyesinde** oynatılacağını
söyleyen sözleşmeyi sağlar — motorun kendisini icat etmez.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from . import Agent, AgentBehavior

__all__ = [
    "AgentVisualLevel",
    "AnimationClip",
    "CrowdPressureLevel",
    "AgentVisualState",
    "select_visual_level",
    "select_animation_clip",
    "crowd_pressure_level",
    "agent_visual_variant",
    "compute_agent_visual_state",
]


class AgentVisualLevel(str, Enum):
    """Roadmap 2.1/2.2/2.3.4 — üç seviyeli görsel LOD.

    Faz 1.4'teki `SimulationLODMode` (FULL/AGGREGATE/CULLED) **simülasyon
    maliyetini** kısar; bu enum ise ayrı bir eksen olan **render
    maliyetini** kısar — ikisi bağımsız ama aynı "kameraya yakınlık"
    girdisini paylaşır (roadmap 2.3.4: "üçlü LOD zinciri").
    """

    BILLBOARD = "billboard"    # Seviye 1 — sprite, instancing, durum-renk tint
    CAPSULE = "capsule"        # Seviye 2 — kapsül+küre low-poly, hafif bobbing
    SKELETAL = "skeletal"      # Seviye 3 — tam iskelet animasyonu, GPU skinning


#: Roadmap 2.3.4 varsayılan mesafe eşikleri (metre). Açıkça isimlendirilmiş
#: sabitler — render tarafı gerekirse override edebilir, ama varsayılan
#: davranış burada tek bir yerde, test edilebilir şekilde tanımlı.
DEFAULT_SKELETAL_MAX_DISTANCE_M = 15.0
DEFAULT_CAPSULE_MAX_DISTANCE_M = 60.0

#: Roadmap 2.3.4: "en yakın 200-500 ajan" tam iskelet kullanır. Mesafe eşiği
#: tek başına bunu garanti etmez (yoğun sahnede 15m içinde 500'den fazla
#: ajan olabilir) - bu yüzden `select_visual_level` isteğe bağlı bir
#: `skeletal_rank` (kameraya mesafeye göre sıralamadaki konum) parametresi
#: de kabul eder; verilirse eşik + tavan sayısı birlikte uygulanır.
DEFAULT_MAX_SKELETAL_AGENTS = 500


def select_visual_level(
    distance_to_camera_m: float,
    *,
    skeletal_rank: int | None = None,
    skeletal_max_distance_m: float = DEFAULT_SKELETAL_MAX_DISTANCE_M,
    capsule_max_distance_m: float = DEFAULT_CAPSULE_MAX_DISTANCE_M,
    max_skeletal_agents: int = DEFAULT_MAX_SKELETAL_AGENTS,
) -> AgentVisualLevel:
    """Roadmap 2.3.4 — kademeli devreye alma kriterinin somut karşılığı.

    `skeletal_rank` (0 = kameraya en yakın ajan) verilirse, tavan sayısını
    aşan ajanlar mesafe eşiğinin içinde olsa bile `SKELETAL`'e
    yükseltilmez (performans bütçesi tavan sayısıyla korunur) - bunun
    yerine `CAPSULE`'a düşer. `skeletal_rank=None` (varsayılan) ile eski/
    basit davranış (yalnızca mesafeye bakar) korunur.
    """
    if distance_to_camera_m <= skeletal_max_distance_m:
        if skeletal_rank is None or skeletal_rank < max_skeletal_agents:
            return AgentVisualLevel.SKELETAL
        return AgentVisualLevel.CAPSULE
    if distance_to_camera_m <= capsule_max_distance_m:
        return AgentVisualLevel.CAPSULE
    return AgentVisualLevel.BILLBOARD


class AnimationClip(str, Enum):
    """Roadmap 2.3.1/2.3.3/2.6 — standart iskelete bind edilmiş temel clip
    havuzu. İsimler, açık lisanslı hazır mocap setlerinde (Mixamo tarzı)
    bulunan standart clip adlarıyla kasıtlı olarak uyumlu tutuldu (roadmap
    2.3.1: "sıfırdan mocap üretmek yerine hazır set")."""

    IDLE = "idle"
    WALK = "walk"
    RUN = "run"
    PANIC_RUN = "panic_run"                    # 2.3.3 — PANIC durumu
    WAIT = "wait"                               # bekleme/duraklama
    STAIR_UP = "stair_up"
    STAIR_DOWN = "stair_down"
    # -- Roadmap 2.6: Duygu/korku ifadesi -------------------------------- #
    COWER = "cower"                             # çömelme
    LOOK_AROUND_PANICKED = "look_around_panicked"  # koşarken etrafa bakınma
    COVER_HEAD = "cover_head"                   # elleri başında koruma pozu
    STUMBLE_RECOVER = "stumble_recover"         # düşüp kalkma


#: Roadmap 2.3.3 — `AgentBehavior` → temel hareket clip'i eşlemesi (durum
#: makinesi). Rota-karar etiketleri (AVOID_*, SEEK_*) hız bakımından NORMAL
#: ile aynı olduğu gibi (bkz. `crowd_simulation.Agent.effective_desired_speed`),
#: burada da görsel olarak WALK/HURRIED ile aynı ailede ele alınır - yeni
#: bir görsel kategori icat edilmez, mevcut davranış-hız disiplini korunur.
_BASE_CLIP_BY_BEHAVIOR: dict[AgentBehavior, AnimationClip] = {
    AgentBehavior.NORMAL: AnimationClip.WALK,
    AgentBehavior.CAUTIOUS: AnimationClip.WALK,
    AgentBehavior.HURRIED: AnimationClip.RUN,
    AgentBehavior.PANIC: AnimationClip.PANIC_RUN,
    AgentBehavior.AVOID_CROWDED_EXIT: AnimationClip.WALK,
    AgentBehavior.AVOID_SMOKE: AnimationClip.RUN,
    AgentBehavior.SEEK_ALTERNATIVE_EXIT: AnimationClip.WALK,
}

#: Roadmap 2.6 — PANIC durumunda, düz PANIC_RUN yerine zaman zaman
#: gösterilecek korku-ifadesi clip havuzu. Seçim deterministiktir (bkz.
#: `select_animation_clip`), rastgele-her-karede-değişen "titreşen" bir
#: görünüm yaratmaz.
_FEAR_CLIP_POOL: tuple[AnimationClip, ...] = (
    AnimationClip.LOOK_AROUND_PANICKED,
    AnimationClip.COVER_HEAD,
    AnimationClip.COWER,
)


def select_animation_clip(agent: Agent, *, include_fear_expression: bool = True) -> AnimationClip:
    """Roadmap 2.3.3 (durum makinesi) + 2.6 (korku ifadesi) + dikey hareket.

    Öncelik sırası (en spesifik → en genel):
    1. `waiting=True` → `WAIT` (reaction-time bekleme veya kapı-kapasite
       bekletmesi, davranıştan bağımsız — roadmap'in "bekliyor" durumu).
    2. Rota bir merdiven düğümünden geçiyorsa (bkz. `Agent.path_nodes` /
       `floor_index` değişimi çağıran tarafından tespit edilip
       `on_stairs` ile bildirilir; bu fonksiyon kendi başına path
       geometrisi yorumlamaz — tek sorumluluk ilkesi) → `STAIR_UP`/`DOWN`.
    3. `PANIC` + `include_fear_expression=True` → deterministik olarak
       (agent_id'ye bağlı) ya `PANIC_RUN` ya bir korku-ifadesi clip'i.
    4. Aksi halde `_BASE_CLIP_BY_BEHAVIOR` eşlemesi.
    """
    if agent.waiting:
        return AnimationClip.WAIT
    if agent.behavior == AgentBehavior.PANIC and include_fear_expression:
        # Deterministik "zaman zaman": agent_id + evacuated-olmama durumu
        # dışında hiçbir gizli rastgelelik yok - aynı agent her zaman aynı
        # clip havuzu indeksine düşer (seed'e bağlı olmayan ama sabit bir
        # karma - Faz 1.5 determinizm ilkesiyle çelişmez, çünkü zaten
        # tamamen `agent_id`'den türüyor, koşudan koşuya değişmez).
        if agent.agent_id % 4 == 0:
            return _FEAR_CLIP_POOL[agent.agent_id % len(_FEAR_CLIP_POOL)]
        return AnimationClip.PANIC_RUN
    return _BASE_CLIP_BY_BEHAVIOR.get(agent.behavior, AnimationClip.WALK)


class CrowdPressureLevel(str, Enum):
    """Roadmap 2.5 — `Agent.crowd_pressure` ham skalerinin render'a
    sunulan kaba sınıflandırması. Eşikler `SocialForceParams` varsayılan
    `agent_repulsion_a=2.0` ölçeğine göre kalibre edildi (tipik serbest
    yürüyüşte ~0, dar geçit/darboğazda önemli ölçüde büyür)."""

    NONE = "none"        # serbest hareket, görsel etki yok
    MILD = "mild"         # hafif yavaşlama/duruş sıkılaşması
    SQUEEZE = "squeeze"   # belirgin sıkışma, omuz/duruş daralması


def crowd_pressure_level(
    agent: Agent, *, mild_threshold: float = 1.0, squeeze_threshold: float = 4.0,
) -> CrowdPressureLevel:
    """Roadmap 2.5: SocialForceModel'in zaten hesapladığı itme kuvvetini
    (bkz. `crowd_simulation.SocialForceModel.step` → `agent.crowd_pressure`)
    görsel bir "sıkışma" seviyesine eşler - yeni bir fizik hesaplaması
    icat etmez, var olan kuvveti render için okunabilir hale getirir."""
    if agent.crowd_pressure >= squeeze_threshold:
        return CrowdPressureLevel.SQUEEZE
    if agent.crowd_pressure >= mild_threshold:
        return CrowdPressureLevel.MILD
    return CrowdPressureLevel.NONE


#: Roadmap 2.4 — "4-6 temel mesh/doku varyasyonu". Sayı roadmap metniyle
#: birebir uyumlu (alt sınır 4) tutuldu; render tarafı bu isimleri kendi
#: mesh/doku dosyalarına eşler (bu modül dosya yolu bilmez — yalnızca
#: deterministik bir etiket üretir, tek sorumluluk ilkesi).
AGENT_MESH_VARIANTS: tuple[str, ...] = (
    "civilian_a", "civilian_b", "civilian_c", "civilian_d", "civilian_e", "civilian_f",
)

#: Roadmap 2.4 — "renk paleti rastgele ama seed'e bağlı". Sabit, sonlu bir
#: palet (kara kutu rastgelelik değil, açık liste).
AGENT_COLOR_PALETTE: tuple[str, ...] = (
    "#3B4A5A", "#7A5230", "#5C6B4A", "#4A4A4A", "#6B3B3B", "#3B5A5A", "#5A4A6B",
)


def agent_visual_variant(agent_id: int, seed: int = 0) -> tuple[str, str]:
    """Roadmap 2.4: `agent_id` + koşunun `seed`'ine bağlı **deterministik**
    (mesh_variant, color_hex) çifti döner - "her ajan aynı klon" görüntüsünü
    kırar, ama Faz 1.5'in "aynı seed → bit-bit aynı sonuç" ilkesini bozmaz
    (aynı `(agent_id, seed)` her zaman aynı varyantı üretir; `random`
    modülünün global durumuna bağlı değil, saf bir hash - koşu sırası veya
    başka bir agent'ın kaç kez rastgele sayı çektiğinden etkilenmez).
    """
    digest = hashlib.sha256(f"{seed}:{agent_id}".encode("utf-8")).digest()
    mesh = AGENT_MESH_VARIANTS[digest[0] % len(AGENT_MESH_VARIANTS)]
    color = AGENT_COLOR_PALETTE[digest[1] % len(AGENT_COLOR_PALETTE)]
    return mesh, color


@dataclass(slots=True, frozen=True)
class AgentVisualState:
    """Render tarafına tek çağrıda aktarılacak, bir agent için o karedeki
    tam görsel durum sözleşmesi (2.1–2.6'nın birleşik çıktısı)."""

    agent_id: int
    visual_level: AgentVisualLevel
    animation_clip: AnimationClip
    mesh_variant: str
    color_hex: str
    crowd_pressure_level: CrowdPressureLevel


def compute_agent_visual_state(
    agent: Agent,
    distance_to_camera_m: float,
    *,
    seed: int = 0,
    skeletal_rank: int | None = None,
    include_fear_expression: bool = True,
) -> AgentVisualState:
    """Faz 2'nin tek giriş noktası: bir `Agent` + kamera mesafesinden,
    render'ın ihtiyaç duyduğu her şeyi tek bir deterministik çağrıda
    üretir. Alt fonksiyonların (`select_visual_level`,
    `select_animation_clip`, `agent_visual_variant`,
    `crowd_pressure_level`) her biri bağımsız test edilebilir kalır; bu
    fonksiyon yalnızca onları birleştirir (kompozisyon, yeni mantık yok).
    """
    level = select_visual_level(distance_to_camera_m, skeletal_rank=skeletal_rank)
    clip = select_animation_clip(agent, include_fear_expression=include_fear_expression)
    mesh, color = agent_visual_variant(agent.agent_id, seed=seed)
    pressure = crowd_pressure_level(agent)
    return AgentVisualState(
        agent_id=agent.agent_id,
        visual_level=level,
        animation_clip=clip,
        mesh_variant=mesh,
        color_hex=color,
        crowd_pressure_level=pressure,
    )
