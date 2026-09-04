"""
visualization.spatial_audio — Roadmap V10 / Faz 5.6 (Ses/işitsel katman)
============================================================================

"Genişletilmiş — tam faz, artık zorunlu ağırlıkta" olarak işaretli 5.6'nın
veri/mantık katmanı. `scenario_visual_bridge` ile aynı disiplin: bu modül
hiçbir gerçek ses motoru/DOM kodu içermez (WebAudio API tarayıcı
tarafında yaşar) — üretir olduğu, viewer'ın `AudioContext` + `PannerNode`
ile doğrudan tüketebileceği düz, JSON-serileştirilebilir bir **ses-olay
sözleşmesi**dir.

Kapsanan roadmap maddeleri:
- **5.6.1** — `SoundEffectEvent` + `trigger_event_sound()`: olay-
  tetiklemeli efektler (deprem gürleme+sallanma, yangın alarmı, cam
  kırılması, enkaz sesi) — `Faz 3.A`/`Faz 4`'ün ürettiği olaylarla
  (sallanma şiddeti eşiği, hasar kademesi geçişi) doğrudan bağlanır,
  yeni bir olay sistemi icat edilmez.
- **5.6.2** — `CrowdAmbienceLayer` + `crowd_ambience_mix()`: yoğunluk/
  panik seviyesine göre katmanlı kalabalık gürültüsü — birden fazla ses
  katmanının crossfade ile karışımı (tek sabit ses dosyası değil).
  `mobility.crowd_simulation.agent_visuals.CrowdPressureLevel` (zaten
  var) girdi olarak kullanılır.
- **5.6.3** — `PositionalAudioSource`: WebAudio `PannerNode`'un
  ihtiyaç duyduğu konum + kazanç (gain) parametrelerini üretir — yeni
  bir 3D ses motoru icat edilmez, WebAudio'nun kendi panner modeli
  parametrelenir.
- **5.6.4 kabul kriteri:** `positional_gain()` mesafeye göre azalan
  (inverse-distance rolloff) bir kazanç üretir — kamera yaklaşınca ses
  şiddeti artar/uzaklaşınca azalır; `SCENARIO_SOUND_SIGNATURE` her
  senaryo türü için ayrı bir ses-imzası (katman kombinasyonu) tanımlar,
  bu da farklı senaryo türlerinin (deprem/yangın/kalabalık) birbirinden
  ayırt edilebilir olmasını garanti eder.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..mobility.crowd_simulation.agent_visuals import CrowdPressureLevel

__all__ = [
    "SoundEffectId",
    "SoundEffectEvent",
    "trigger_event_sound",
    "CrowdAmbienceLayer",
    "crowd_ambience_mix",
    "PositionalAudioSource",
    "positional_gain",
    "ScenarioKind",
    "SCENARIO_SOUND_SIGNATURE",
]


# ======================================================================== #
# 5.6.1 — Olay-tetiklemeli efektler
# ======================================================================== #

class SoundEffectId(str, Enum):
    """Sabit, önceden tanımlı efekt kütüphanesi kimlikleri - gerçek ses
    dosyası (.ogg/.wav) eşlemesi viewer tarafında yaşar, bu modül yalnızca
    "hangi efekt, ne zaman" kararını üretir."""

    EARTHQUAKE_RUMBLE = "earthquake_rumble"      # deprem gürlemesi
    STRUCTURE_CREAK = "structure_creak"          # sallanma (bina gıcırtısı)
    FIRE_ALARM = "fire_alarm"                    # yangın alarmı
    GLASS_SHATTER = "glass_shatter"               # cam kırılması
    DEBRIS_IMPACT = "debris_impact"               # enkaz/çökme sesi


@dataclass(slots=True, frozen=True)
class SoundEffectEvent:
    """Tek bir olay-tetiklemeli efekt çağrısı - `PositionalAudioSource`
    ile birlikte kullanılırsa 3D konumlu, `source=None` ise genel/UI
    sesi (ör. arayüz uyarısı) olarak yorumlanır."""

    effect: SoundEffectId
    source: Optional["PositionalAudioSource"]
    intensity: float = 1.0   # 0.0-1.0, oynatma hacmi/varyant seçimi için


def trigger_event_sound(
    *,
    shake_intensity: Optional[float] = None,
    fire_alarm_active: bool = False,
    glass_shatter_triggered: bool = False,
    debris_impact_triggered: bool = False,
    source: Optional["PositionalAudioSource"] = None,
) -> list[SoundEffectEvent]:
    """Faz 3.A (`physics.building_shake`) ve Faz 4 (`physics.
    building_damage`) katmanlarının ürettiği durumdan doğrudan bir
    ses-olay listesi türetir - yeni bir eşik/olay sistemi icat edilmez,
    var olan eşiklerin (çağıran tarafça zaten hesaplanmış) doğal
    tüketicisidir. Hiçbir koşul sağlanmazsa boş liste döner (sessiz
    devam - roadmap'in "gösterge niteliğinde" disipliniyle tutarlı)."""
    events: list[SoundEffectEvent] = []
    if shake_intensity is not None and shake_intensity > 0.0:
        events.append(SoundEffectEvent(SoundEffectId.EARTHQUAKE_RUMBLE, source,
                                        intensity=min(1.0, shake_intensity)))
        if shake_intensity >= 0.4:
            events.append(SoundEffectEvent(SoundEffectId.STRUCTURE_CREAK, source,
                                            intensity=min(1.0, shake_intensity)))
    if fire_alarm_active:
        events.append(SoundEffectEvent(SoundEffectId.FIRE_ALARM, source, intensity=1.0))
    if glass_shatter_triggered:
        events.append(SoundEffectEvent(SoundEffectId.GLASS_SHATTER, source, intensity=1.0))
    if debris_impact_triggered:
        events.append(SoundEffectEvent(SoundEffectId.DEBRIS_IMPACT, source, intensity=1.0))
    return events


# ======================================================================== #
# 5.6.2 — Kalabalık ambiyansı (crowd audio)
# ======================================================================== #

class CrowdAmbienceLayer(str, Enum):
    """Katmanlı kalabalık gürültüsü - roadmap'in "birden fazla ses
    katmanının crossfade ile karışımı, tek sabit ses dosyası değil"
    notuyla birebir: her `CrowdPressureLevel`/panik oranı kombinasyonu,
    birden çok katmanın ağırlıklı karışımına eşlenir (tek bir "kalabalık
    sesi" seçilmez)."""

    LIGHT_CHATTER = "light_chatter"       # hafif konuşma uğultusu
    DENSE_MURMUR = "dense_murmur"         # yoğun ama sakin uğultu
    PANIC_SHOUTING = "panic_shouting"     # çığlık/koşuşturma sesi


def crowd_ambience_mix(
    pressure_level: CrowdPressureLevel,
    panic_ratio: float,
) -> dict[CrowdAmbienceLayer, float]:
    """`crowd_pressure_level()` (zaten var, Faz 2.5) + `panic_ratio`
    (o anki sahnedeki PANIC durumundaki ajan oranı, 0.0-1.0) girdisinden
    üç katmanın **crossfade ağırlıklarını** (0.0-1.0, toplamı 1.0'a
    normalize edilir) üretir. Tek bir kesin eşik yerine sürekli bir
    karışım kullanılır - roadmap'in "crossfade" ifadesinin doğrudan
    karşılığı, viewer birden fazla `AudioBufferSourceNode`'u bu
    ağırlıklarla eş zamanlı çalar."""
    panic_ratio = max(0.0, min(1.0, panic_ratio))

    base_density = {
        CrowdPressureLevel.NONE: 0.15,
        CrowdPressureLevel.MILD: 0.45,
        CrowdPressureLevel.SQUEEZE: 0.85,
    }[pressure_level]

    panic_weight = panic_ratio
    dense_weight = base_density * (1.0 - panic_weight)
    light_weight = (1.0 - base_density) * (1.0 - panic_weight)

    total = panic_weight + dense_weight + light_weight
    if total <= 0.0:
        return {
            CrowdAmbienceLayer.LIGHT_CHATTER: 1.0,
            CrowdAmbienceLayer.DENSE_MURMUR: 0.0,
            CrowdAmbienceLayer.PANIC_SHOUTING: 0.0,
        }
    return {
        CrowdAmbienceLayer.LIGHT_CHATTER: light_weight / total,
        CrowdAmbienceLayer.DENSE_MURMUR: dense_weight / total,
        CrowdAmbienceLayer.PANIC_SHOUTING: panic_weight / total,
    }


# ======================================================================== #
# 5.6.3 — 3D pozisyonel ses
# ======================================================================== #

#: `camera_rig.Vec3` ile aynı tip takma adı (proje genelinde tekrar
#: kullanılan desen - yeni bir vektör tipi icat edilmedi).
Vec3 = tuple[float, float, float]


@dataclass(slots=True, frozen=True)
class PositionalAudioSource:
    """WebAudio `PannerNode`'un doğrudan tüketebileceği konum - roadmap
    5.6.3'ün "ekstra motor gerekmez" notuyla tutarlı, burada yalnızca
    kaynak konumu + rolloff parametreleri üretilir; `PannerNode`'un
    kendisi viewer tarafında kurulur."""

    position: Vec3
    ref_distance_m: float = 5.0    # bu mesafede kazanç = max_gain
    max_distance_m: float = 200.0  # bu mesafenin ötesinde kazanç sabitlenir
    rolloff_factor: float = 1.0


def _distance(a: Vec3, b: Vec3) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def positional_gain(source: PositionalAudioSource, listener_position: Vec3) -> float:
    """WebAudio'nun standart "inverse" distance-model'iyle aynı formül
    (`gain = ref / (ref + rolloff * max(dist - ref, 0))`) - yeni bir
    azalma modeli icat edilmez, tarayıcının kendi standart modeli
    Python tarafında öngörülebilir/test edilebilir hale getirilir.
    Kabul kriteri (5.6.4): kamera kaynağa yaklaşınca kazanç artar,
    uzaklaşınca azalır, `max_distance_m` ötesinde asla sıfıra
    düşmez ama sabit bir minimuma yaklaşır."""
    dist = _distance(source.position, listener_position)
    dist = min(dist, source.max_distance_m)
    ref = source.ref_distance_m
    denom = ref + source.rolloff_factor * max(dist - ref, 0.0)
    if denom <= 0.0:
        return 1.0
    return max(0.0, min(1.0, ref / denom))


# ======================================================================== #
# 5.6.4 — Senaryo türüne göre ayırt edilebilir ses imzası
# ======================================================================== #

class ScenarioKind(str, Enum):
    EARTHQUAKE = "earthquake"
    FIRE = "fire"
    CROWD_SURGE = "crowd_surge"


#: Her senaryo türü hangi efekt/ambiyans katman kombinasyonunu **öncelikli**
#: kullanır - roadmap 5.6.4'ün "farklı senaryo türleri birbirinden ayırt
#: edilebilir ses imzasına sahip olmalı" kabul kriterinin somut karşılığı.
#: Bu bir davranış motoru değildir, yalnızca dokümante edilmiş bir eşleme -
#: `trigger_event_sound()`/`crowd_ambience_mix()` zaten bağımsız olarak
#: doğru katmanları üretir, bu tablo yalnızca imzaların çakışmadığını
#: test edilebilir kılar.
SCENARIO_SOUND_SIGNATURE: dict[ScenarioKind, tuple[SoundEffectId, ...]] = {
    ScenarioKind.EARTHQUAKE: (SoundEffectId.EARTHQUAKE_RUMBLE, SoundEffectId.STRUCTURE_CREAK,
                               SoundEffectId.DEBRIS_IMPACT),
    ScenarioKind.FIRE: (SoundEffectId.FIRE_ALARM, SoundEffectId.GLASS_SHATTER),
    ScenarioKind.CROWD_SURGE: (),  # yalnızca crowd_ambience_mix() katmanları, ek efekt yok
}
