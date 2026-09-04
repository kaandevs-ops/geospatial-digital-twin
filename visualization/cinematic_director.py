"""
visualization.cinematic_director — Roadmap V10 / Faz 7
=============================================================================

"Sinematik Yönetmenlik ve Sunum Katmanı" fazının veri/mantık katmanı.
`visualization.camera_rig` (Faz 9, zaten var) **değiştirilmeden** yeniden
kullanılır — bu modül yeni bir kamera hareket motoru icat etmez, yalnızca
"hangi olay, ne zaman, kaç saniyeliğine kameraya önerilir" kararını üretir
ve bunu `CameraRig.cinematic_at()`'in (zaten var) tükettiği
`CinematicKeyframe` dizisine çevirir.

Kapsanan roadmap maddeleri:
- **7.1** — `SceneEvent` + `InterestScorer`: sahne olaylarını (bina
  çatlağı, ajan yoğunluğu zirvesi, asansör mahsur kalma) kural tabanlı
  bir öncelik-skorlamasıyla değerlendirir; en yüksek skorlu olay kamera
  hedefine önerilir. Yapay zeka gerekmez (roadmap'in kendi notu).
- **7.2** — `build_transition_keyframes()`: `camera_rig.
  CinematicKeyframe`/`CameraRig.cinematic_at()` (zaten var, DEĞİŞTİRİLMEDEN)
  üzerine, olaylar arası yumuşak (ease-in/out) geçiş üretir — ani kesme
  yapılmaz, "belgesel" hissi veren sabit-genişlik açıları korunur.
- **7.3** — `AutoCameraController`: kullanıcı manuel müdahale ettiği an
  otomatik kamera devre dışı kalır; yalnızca kullanıcı boşta bırakıldığında
  öneri sunar. Kullanıcı kontrolü her zaman önceliklidir.

Dürüstlük notu: `InterestScorer`'ın ürettiği skor, öznel/kural-tabanlı bir
sezgiseldir (heuristic) — "gerçek önem" iddiası taşımaz, yalnızca
roadmap'in kendi verdiği örnek sırayla (çatlak > grup davranışı > sıradan
yürüyüş) tutarlı, ayarlanabilir bir öncelik listesidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .camera_rig import Camera, CameraRig, CinematicKeyframe, Vec3, _lerp, _lerp_vec3

__all__ = [
    "SceneEventKind",
    "SceneEvent",
    "InterestScorer",
    "DEFAULT_EVENT_PRIORITY",
    "build_transition_keyframes",
    "AutoCameraController",
]


# ======================================================================== #
# 7.1 — Otomatik "ilginç an" tespiti
# ======================================================================== #

class SceneEventKind(str, Enum):
    """Roadmap 7.1'in kendi örnek listesiyle birebir: "binaya ilk çatlak"
    > "ajan grup davranışı" > "sıradan yürüyüş" - bu sıra `DEFAULT_EVENT_
    PRIORITY`'de aynen korunur. Diğer fazların ürettiği olaylarla (Faz
    3.A sallanma, Faz 4 hasar, Faz 5.6 ses) doğrudan eşlenir - yeni bir
    olay taksonomisi icat edilmedi, mevcut event-bus'ın (roadmap'in
    kendi ifadesiyle) tüketicisidir."""

    BUILDING_FIRST_CRACK = "building_first_crack"        # Faz 4.1 hasar kademesi geçişi
    BUILDING_COLLAPSE_START = "building_collapse_start"   # Faz 4.2/4.3 tetiklenmesi
    ELEVATOR_STRANDED = "elevator_stranded"               # Faz 1.3 asansör kapalı + sıkışma
    CROWD_DENSITY_PEAK = "crowd_density_peak"             # Faz 2.5 crowd_pressure=SQUEEZE
    GROUP_BEHAVIOR = "group_behavior"                     # Faz 2.7 grup bağı
    ORDINARY_WALK = "ordinary_walk"                       # hiçbiri - varsayılan/dolgu olay


@dataclass(slots=True, frozen=True)
class SceneEvent:
    """Tek bir sahne olayı - `event_bus`'ın (varsa) yayınladığı ham
    olayın, bu modülün anlayacağı minimum alanlara indirgenmiş hali.
    `position` kameranın hedefleyeceği dünya konumu (x, y, z)."""

    kind: SceneEventKind
    time_s: float
    position: Vec3
    magnitude: float = 1.0   # 0.0-1.0, aynı türden olaylar arası ince ayrım


#: Roadmap 7.1'in kendi verdiği örnek öncelik sırası - kural tabanlı,
#: sabit ve test edilebilir (kara kutu değil). Daha yüksek değer = daha
#: ilginç. Yeni bir tür eklenirse yalnızca bu tabloya bir satır eklenir,
#: skorlama mantığı değişmez.
DEFAULT_EVENT_PRIORITY: dict[SceneEventKind, float] = {
    SceneEventKind.BUILDING_COLLAPSE_START: 100.0,
    SceneEventKind.BUILDING_FIRST_CRACK: 90.0,
    SceneEventKind.ELEVATOR_STRANDED: 70.0,
    SceneEventKind.CROWD_DENSITY_PEAK: 50.0,
    SceneEventKind.GROUP_BEHAVIOR: 30.0,
    SceneEventKind.ORDINARY_WALK: 5.0,
}


@dataclass(slots=True)
class InterestScorer:
    """Roadmap 7.1: her olaya `DEFAULT_EVENT_PRIORITY[kind] * magnitude`
    ile bir "ilginçlik skoru" atar, en yüksek skorlu olayı döner. Aynı
    türden ve aynı skordan birden fazla olay varsa, `time_s`'i en yeni
    olan tercih edilir (roadmap'in kendisi bunu belirtmez ama
    determinizm için gerekli - iki eşit-skorlu olaydan rastgele biri
    seçilmemeli)."""

    priority_table: dict[SceneEventKind, float] = field(
        default_factory=lambda: dict(DEFAULT_EVENT_PRIORITY)
    )

    def score(self, event: SceneEvent) -> float:
        base = self.priority_table.get(event.kind, DEFAULT_EVENT_PRIORITY[SceneEventKind.ORDINARY_WALK])
        return base * max(0.0, min(1.0, event.magnitude))

    def most_interesting(self, events: list[SceneEvent]) -> Optional[SceneEvent]:
        if not events:
            return None
        return max(events, key=lambda e: (self.score(e), e.time_s))

    def ranked(self, events: list[SceneEvent]) -> list[SceneEvent]:
        """Tüm olayları skora göre azalan sırada döner - ör. bir "ilginç
        anlar" zaman çizelgesi UI'ı için."""
        return sorted(events, key=lambda e: (self.score(e), e.time_s), reverse=True)


# ======================================================================== #
# 7.2 — Kamera geçiş dili (yumuşak ease-in/out)
# ======================================================================== #

def _smoothstep(t: float) -> float:
    """Klasik ease-in/out (Hermite): t=0/1'de eğim sıfır - roadmap'in
    "aşırı ani kesme yapılmaması" notunun matematiksel karşılığı. Yeni
    bir eğrilik fonksiyonu icat edilmedi, grafik/animasyon endüstrisinde
    standart olan `3t^2 - 2t^3` kullanıldı."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def build_transition_keyframes(
    from_camera: Camera,
    to_event: SceneEvent,
    *,
    transition_duration_s: float = 2.5,
    hold_duration_s: float = 4.0,
    viewing_distance_m: float = 25.0,
    fov_deg: float = 45.0,
    ease_samples: int = 6,
) -> list[CinematicKeyframe]:
    """Mevcut kameradan (`from_camera`) hedef olaya (`to_event`) doğru,
    `CameraRig.cinematic_at()`'in (zaten var, DEĞİŞTİRİLMEDEN) tükettiği
    bir `CinematicKeyframe` dizisi üretir. Ara noktalar `_smoothstep` ile
    örneklenir - roadmap'in "belgesel hissi veren sabit-genişlik açıları"
    notuyla tutarlı, `fov_deg` geçiş boyunca sabit tutulur (yalnızca
    konum/hedef değişir, ani zoom yapılmaz). Geçişten sonra `hold_
    duration_s` kadar hedefte sabit kalınır (izleyici olayı anlayabilsin
    diye - roadmap'in ima ettiği "belgesel" hissinin bir parçası).
    """
    ox, oy, oz = to_event.position
    # Hedefin biraz gerisinden/yukarısından bakan sabit bir gözlem noktası
    # - roadmap'in "sabit-genişlik açıları" notuna uygun basit ve
    # öngörülebilir bir yerleşim (yeni bir sinematografi kural motoru
    # icat edilmedi).
    target_camera_pos: Vec3 = (ox - viewing_distance_m * 0.7, oy - viewing_distance_m * 0.7,
                                oz + viewing_distance_m * 0.4)

    keyframes: list[CinematicKeyframe] = []
    t0 = 0.0
    for i in range(ease_samples + 1):
        t = i / ease_samples
        eased = _smoothstep(t)
        pos = _lerp_vec3(from_camera.position, target_camera_pos, eased)
        tgt = _lerp_vec3(from_camera.target, to_event.position, eased)
        keyframes.append(CinematicKeyframe(
            time_s=t0 + t * transition_duration_s,
            position=pos, target=tgt, fov_deg=fov_deg,
        ))

    hold_start = t0 + transition_duration_s
    keyframes.append(CinematicKeyframe(
        time_s=hold_start + hold_duration_s,
        position=target_camera_pos, target=to_event.position, fov_deg=fov_deg,
    ))
    return keyframes


# ======================================================================== #
# 7.3 — Kullanıcı kontrolü her zaman öncelikli
# ======================================================================== #

@dataclass(slots=True)
class AutoCameraController:
    """Roadmap 7.3: otomatik kamera yalnızca kullanıcı `idle_threshold_s`
    kadar süredir hiçbir manuel girdi vermemişse devreye girer; kullanıcı
    girdisi anında (bir sonraki `notify_user_input()` çağrısında) devre
    dışı kalır. `CameraRig`'in kendisi DEĞİŞTİRİLMEZ - bu sınıf yalnızca
    "ne zaman `cinematic_at()` çağrılmalı" kararını sarmalar."""

    rig: CameraRig
    scorer: InterestScorer = field(default_factory=InterestScorer)
    idle_threshold_s: float = 8.0

    _last_user_input_t: float = field(default=0.0, init=False)
    _auto_active: bool = field(default=False, init=False)

    def notify_user_input(self, current_t: float) -> None:
        """Kullanıcı manuel müdahale ettiğinde çağrılır (kamera
        sürükleme/tuş vb. herhangi bir girdi). Otomatik kamera anında
        devre dışı kalır - roadmap 7.3'ün "kullanıcıyı asla ele
        geçirmemeli" ilkesinin doğrudan karşılığı."""
        self._last_user_input_t = current_t
        self._auto_active = False

    def is_user_idle(self, current_t: float) -> bool:
        return (current_t - self._last_user_input_t) >= self.idle_threshold_s

    def maybe_suggest(self, current_t: float, events: list[SceneEvent]) -> Optional[SceneEvent]:
        """Kullanıcı boşta değilse `None` döner (hiçbir öneri sunulmaz -
        roadmap'in "yalnızca boşta bırakıldığında öneri sunar" ifadesi).
        Boştaysa en ilginç olayı önerir ve otomatik modu aktive eder."""
        if not self.is_user_idle(current_t):
            self._auto_active = False
            return None
        best = self.scorer.most_interesting(events)
        if best is not None:
            self._auto_active = True
        return best

    @property
    def auto_active(self) -> bool:
        return self._auto_active

    def apply_suggestion(self, event: SceneEvent, current_t: float, **kwargs) -> list[CinematicKeyframe]:
        """`maybe_suggest()`'in döndürdüğü olayı gerçek bir keyframe
        dizisine çevirir ve `rig.set_cinematic_track()` (zaten var) ile
        kamerayı yönlendirir. Yalnızca `auto_active=True` iken çağrılmalı
        - çağıran taraf `notify_user_input()` sonrası bu metodu tekrar
        çağırırsa kullanıcı kontrolünü sessizce ezmemek için `RuntimeError`
        fırlatılır (kara kutu davranış yerine açık, yakalanabilir hata)."""
        if not self._auto_active:
            raise RuntimeError(
                "AutoCameraController.apply_suggestion() yalnızca auto_active=True "
                "iken çağrılabilir - kullanıcı girdisi otomatik kamerayı devre dışı "
                "bırakmış olabilir; önce maybe_suggest() ile kontrol edin."
            )
        keyframes = build_transition_keyframes(self.rig.camera, event, **kwargs)
        self.rig.set_cinematic_track(keyframes)
        return keyframes
