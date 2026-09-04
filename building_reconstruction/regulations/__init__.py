"""
Building Reconstruction - Regulations (Roadmap V4, Faz E19)
=============================================================

D13 ile TS/ISO (Turkiye) referansli sayisal esikler `room_generator` ve
`facade_generator` icine sabit olarak gomulmustu (`MIN_ROOM_AREA_M2`,
`MIN_CORRIDOR_WIDTH_M`, `MIN_WINDOW_WALL_RATIO`, `FIRE_ESCAPE_MIN_FLOORS`).
Bu modul, ayni sayisal esikleri **parametrik** bir `RegulationProfile`
dataclass'ina tasiyarak coklu-ulke/bolge yonetmelik setleri arasinda
gecis yapilabilmesini saglar - mevcut davranis (varsayilan TR profili)
degismeden korunur (geriye uyumlu).

Ilke: madde metinleri kopyalanmaz, yalnizca sayisal esik + kaynak adi
tutulur (D13'te zaten benimsenen desen).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Optional


@dataclass(slots=True, frozen=True)
class RegulationProfile:
    """Bir ulke/bolge yonetmelik setinin sayisal esikleri.

    Alanlar `room_generator`/`facade_generator` icindeki mevcut modul-
    seviyesi sabitlerle bire-bir aynı anlamdadir; yalnizca bir profile
    nesnesi icine tasinmistir.
    """

    name: str
    source_label: str
    min_room_area_m2: dict[str, float]
    min_corridor_width_m: float
    min_window_wall_ratio: dict[str, float]
    fire_escape_min_floors: int
    # yeni_roadmap.md Faz 1.4: "Malzeme-yönetmelik ilişkisi: bazı
    # bölgelerde cephe malzemesi kısıtlı olabilir (tarihi doku, iklim
    # bölgesi)". None -> kısıt yok (geriye dönük uyumlu varsayılan).
    # Anahtar: bina tipi (veya "_default"), değer: izinli malzeme
    # (`FacadeMaterial.value`) adları kümesi.
    allowed_facade_materials: dict[str, frozenset[str]] | None = None
    # Roadmap V9 / Katman 7.3 ("Bina Kapasite Simülasyonu"): "kapasite
    # eşiği uyarısı (TBDY/yönetmelikteki maksimum kabul edilebilir
    # tahliye süresine göre 'güvenli/riskli' etiketi - eşik değeri
    # `regulations` modülünden gelmeli)". Anahtar: bina tipi (veya
    # "_default"), değer: azami kabul edilebilir tahliye süresi (saniye).
    # **Gösterge niteliğindedir** - kesin bir yönetmelik maddesinin
    # birebir kopyası değil, BYKHY'nin "güvenli tahliye süresi" (RSET)
    # kavramının literatürde sık atıf yapılan genel eğilimine dayanan
    # kaba bir eşik (tek bir ulusal standardın kesin sayısı olarak
    # sunulmaz - `risk_scoring.py` ile aynı disiplin).
    max_evacuation_time_s: dict[str, float] | None = None

    def evacuation_time_threshold_s(self, building_type: str | None) -> float | None:
        """Verilen bina tipi için azami kabul edilebilir tahliye süresi
        (saniye). Tanımlı değilse `None` döner (eşik uygulanamaz -
        sessizce 'güvenli' varsayılmaz, çağıran taraf bunu ayrıca
        belirtmelidir)."""
        if not self.max_evacuation_time_s:
            return None
        key = (building_type or "").lower()
        return self.max_evacuation_time_s.get(key, self.max_evacuation_time_s.get("_default"))

    def facade_material_allowed(self, building_type: str | None, material_value: str) -> bool:
        """Verilen bina tipi için `material_value` (`FacadeMaterial.value`,
        örn. 'cam') bu profilde izinli mi? Kısıt tanımlanmamışsa (varsayılan
        None) her zaman True döner - mevcut davranış değişmez."""
        if not self.allowed_facade_materials:
            return True
        key = (building_type or "").lower()
        allowed = self.allowed_facade_materials.get(
            key, self.allowed_facade_materials.get("_default")
        )
        if allowed is None:
            return True
        return material_value in allowed

    def room_area_threshold(self, room_type: str) -> float | None:
        return self.min_room_area_m2.get(room_type)

    def window_ratio_threshold(self, building_type: str | None) -> float:
        key = (building_type or "").lower()
        return self.min_window_wall_ratio.get(key, self.min_window_wall_ratio.get("_default", 0.10))

    def with_overrides(self, **kwargs) -> RegulationProfile:
        """Belirli alanlari degistirerek yeni bir profil turetir (test/
        senaryo amacli - orijinal profil degismez, `frozen=True`)."""
        return replace(self, **kwargs)


def _build_default_tr_profile() -> RegulationProfile:
    """D13'un TS/ISO/PAIY/BYKHY tabanli mevcut esiklerini, `room_generator`
    ve `facade_generator` modullerinden **tekrar tanimlamadan** (tek
    kaynak ilkesi) ice aktararak varsayilan profili olusturur."""
    # Gecikmeli import: dongusel import onlemek icin (bu paketler de
    # ileride `regulations`'a bagimli olabilir).
    from ..facade_generator import FIRE_ESCAPE_MIN_FLOORS, MIN_WINDOW_WALL_RATIO
    from ..room_generator import MIN_CORRIDOR_WIDTH_M, MIN_ROOM_AREA_M2

    return RegulationProfile(
        name="TR_PAIY_ISO_BYKHY",
        source_label=(
            "Planli Alanlar Imar Yonetmeligi (RG 3.7.2017/30113) + "
            "ISO 21542:2011 + Binalarin Yangindan Korunmasi Hakkinda "
            "Yonetmelik (RG 19.12.2007/26735) + TS 825"
        ),
        min_room_area_m2=dict(MIN_ROOM_AREA_M2),
        min_corridor_width_m=MIN_CORRIDOR_WIDTH_M,
        min_window_wall_ratio=dict(MIN_WINDOW_WALL_RATIO),
        fire_escape_min_floors=FIRE_ESCAPE_MIN_FLOORS,
        # Roadmap V9 / Katman 7.3 - gösterge niteliğinde RSET eşikleri
        # (BYKHY'nin genel "güvenli tahliye" beklentisine dayanan kaba
        # yaklaşım; okul/hastane gibi kritik kullanımlar için daha kısa,
        # konut için daha uzun tolerans - literatürdeki genel eğilim).
        max_evacuation_time_s={
            "_default": 180.0,
            "residential": 240.0,
            "office": 180.0,
            "school": 150.0,
            "mall": 210.0,
            "hospital": 300.0,
        },
    )


def _build_strict_reference_profile() -> RegulationProfile:
    """Ornek/karsilastirma amacli daha kati bir profil - Eurocode/IBC
    tarzi bazi yargı bolgelerinde gorulen daha yuksek asgari-alan ve
    daha genis koridor beklentisini temsil eder (literatur-referansli
    genel egilim; tek bir spesifik ulusal kod yerine "daha kati bolge"
    ornegi olarak sunulur - kaynak: ROADMAP_V4 E19 hedefinde istenen
    "iki farkli profil farkli sonuc uretebilmeli" kabul kriteri icin).
    """
    base = _build_default_tr_profile()
    stricter_areas = {k: v * 1.5 for k, v in base.min_room_area_m2.items()}
    stricter_ratios = {k: min(v * 1.5, 0.6) for k, v in base.min_window_wall_ratio.items()}
    stricter_evac_times = (
        {k: v * 0.8 for k, v in base.max_evacuation_time_s.items()}
        if base.max_evacuation_time_s
        else None
    )
    return RegulationProfile(
        name="STRICT_REFERENCE",
        source_label=(
            "Ornek/karsilastirma profili - TR profilinin 1.5x daha kati "
            "asgari alan/pencere-orani esikleri (Eurocode/IBC benzeri "
            "bolgelerde gorulen genel egilim referans alinarak turetildi)"
        ),
        min_room_area_m2=stricter_areas,
        min_corridor_width_m=base.min_corridor_width_m * 1.2,
        min_window_wall_ratio=stricter_ratios,
        fire_escape_min_floors=max(1, base.fire_escape_min_floors - 1),
        max_evacuation_time_s=stricter_evac_times,
    )


def _build_historic_zone_profile() -> RegulationProfile:
    """Roadmap 1.4: 'bazı bölgelerde cephe malzemesi kısıtlı olabilir
    (tarihi doku ...)'. TR profilinin aynı sayısal eşiklerini korur,
    yalnızca cephe malzemesini geleneksel/doğal malzemelerle (taş, tuğla,
    ahşap) sınırlayan bir örnek bölgesel varyant (örn. sit alanı/tarihi
    kent merkezi imar şartnamelerinde görülen genel eğilim)."""
    base = _build_default_tr_profile()
    allowed = frozenset({"tas", "tugla", "ahsap"})
    return RegulationProfile(
        name="TR_HISTORIC_ZONE",
        source_label=(
            base.source_label + " + örnek sit alanı/tarihi doku cephe "
            "malzemesi kısıtı (geleneksel malzemelerle sınırlı - genel "
            "eğilim referans alınarak türetildi, belirli bir sit "
            "kararının yerini tutmaz)"
        ),
        min_room_area_m2=dict(base.min_room_area_m2),
        min_corridor_width_m=base.min_corridor_width_m,
        min_window_wall_ratio=dict(base.min_window_wall_ratio),
        fire_escape_min_floors=base.fire_escape_min_floors,
        allowed_facade_materials={"_default": allowed},
        max_evacuation_time_s=(
            dict(base.max_evacuation_time_s) if base.max_evacuation_time_s else None
        ),
    )


# ------------------------------------------------------------------ #
# Kayit defteri - isimden profile erisim
# ------------------------------------------------------------------ #
_REGISTRY: dict[str, RegulationProfile] = {}


def _ensure_registry() -> None:
    if not _REGISTRY:
        tr = _build_default_tr_profile()
        strict = _build_strict_reference_profile()
        historic = _build_historic_zone_profile()
        _REGISTRY[tr.name] = tr
        _REGISTRY[strict.name] = strict
        _REGISTRY[historic.name] = historic


def default_profile() -> RegulationProfile:
    """Mevcut (D13 ile gelen) varsayilan TR profili - geriye uyumluluk
    icin `check_compliance(profile=None)` cagrilarinda kullanilir."""
    _ensure_registry()
    return _REGISTRY["TR_PAIY_ISO_BYKHY"]


def strict_reference_profile() -> RegulationProfile:
    _ensure_registry()
    return _REGISTRY["STRICT_REFERENCE"]


def historic_zone_profile() -> RegulationProfile:
    _ensure_registry()
    return _REGISTRY["TR_HISTORIC_ZONE"]


def get_profile(name: str) -> RegulationProfile:
    _ensure_registry()
    if name not in _REGISTRY:
        raise KeyError(f"Bilinmeyen regulation profili: {name!r}")
    return _REGISTRY[name]


def register_profile(profile: RegulationProfile) -> None:
    """Ozel/harici bir profili kayit defterine ekler (ornegin plugin'ler
    veya kullanici-tanimli bolgesel yonetmelikler icin)."""
    _ensure_registry()
    _REGISTRY[profile.name] = profile


def available_profiles() -> list[str]:
    _ensure_registry()
    return sorted(_REGISTRY.keys())


__all__ = [
    "RegulationProfile",
    "default_profile",
    "strict_reference_profile",
    "historic_zone_profile",
    "get_profile",
    "register_profile",
    "available_profiles",
]
