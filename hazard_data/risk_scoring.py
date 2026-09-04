"""Bina deprem risk skorlama — roadmap Faz 2.3.

    "Bina risk skorlama: yapım yılı, kat sayısı, zemin tipi (varsa jeoteknik
    veri), deprem bölgesi PGA değeri kombinasyonuyla basit bir risk indeksi
    — bunun 'kesin mühendislik raporu' olmadığı, gösterge niteliğinde
    olduğu net şekilde belirtilmeli (yanlış güven yaratmamak için önemli)."

Bu modül tam olarak bunu yapar: 0-100 arası bir GÖSTERGE İNDEKSİ üretir,
her `BuildingRiskReport` içinde açık bir uyarı taşır ve hiçbir yerde
"kesin", "güvenli", "onaylı" gibi yanlış güven veren dil kullanmaz.

Faktörler (ağırlıklı toplam, her biri 0-100 alt skor):
    - PGA (bölgesel deprem tehlikesi) — en yüksek ağırlık.
    - Yapım yılı — 1999 (Türkiye deprem yönetmeliği milat) öncesi/sonrası
      ve yönetmelik güncellemeleri (2007, 2018) kaba eşik olarak kullanılır.
    - Kat sayısı — çok yüksek yapılarda deprem kuvveti/burulma riski artar.
    - Narinlik oranı — `building_reconstruction.structural_validation`
      raporundan (varsa) doğrudan alınır, aynı prensip tekrar edilmez.
    - Zemin tipi — jeoteknik veri yoksa `unknown` olarak işaretlenir ve
      skora dahil edilmez (veri yokken varsayım üretmemek için).

İKİNCİ YÖNTEM — `rapid_visual_screening()` (FEMA P-154 / TBDY Ek-2 usulü):
    Yukarıdaki ağırlıklı-toplam indeksten AYRI ve ONA EK olarak, bu modül
    artık gerçek bir "hızlı görsel tarama" (Rapid Visual Screening, RVS)
    metodolojisinin YAPISINI da uygular — FEMA P-154 (ABD) ve TBDY 2018
    Ek-2'nin ("Mevcut Binaların Deprem Performansının Ön Değerlendirme
    Yöntemi") ortak mantığı:

        Nihai Skor = Temel Skor(bina tipi, sismik bölge)
                     + Σ Skor Değiştiricileri (düzensizlik, zemin, yaş, ...)

    ve bu skor bir EŞİK değerle karşılaştırılır: eşiğin altında kalan
    binalar için "detaylı değerlendirme önerilir" sonucu üretilir.

    DAYANAK VE DÜRÜST SINIRLAMA: FEMA P-154'ün resmi Temel Skor tabloları
    (bina tipine ve sismik bölgeye göre onlarca kalibre edilmiş sayısal
    değer içerir) burada BİREBİR YENİDEN ÜRETİLMEZ — hem bu tablolar sahada
    toplanmış hasar istatistikleriyle kalibre edilmiştir (yazılımla
    türetilemez) hem de resmi FEMA P-154 el kitabına ait tam tablo bu
    modülün kapsamı dışındadır. Bunun yerine, METODOLOJİNİN YAPISI (temel
    skor + additif düzeltme terimleri + eşik-tabanlı karar) doğru şekilde
    uygulanır; `TEMSILI_TEMEL_SKORLAR` ve düzeltme katsayıları AÇIKÇA
    "temsili/kalibrasyon gerektirir" olarak işaretlenmiştir. Gerçek bir RVS
    taraması için bu sabitlerin resmi FEMA P-154 (3. baskı) veya TBDY 2018
    Ek-2 tablolarıyla değiştirilmesi gerekir — bu, "gösterge" ile "resmi
    tarama formu" arasındaki farktır ve modülün amacı bu farkı gizlemek
    değil, doğru iskeleti hazır bulundurmaktır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

DISCLAIMER = (
    "ÖN DEĞERLENDİRME NİTELİĞİNDEDİR — FEMA P-154 / TBDY 2018 Ek-2 "
    "\"hızlı görsel tarama\" yönteminin YAPISINA dayanır. YUKSEK/COK_YUKSEK "
    "sismisitede RESMİ FEMA P-154 3. Baskı HIGH seismicity form değerleri "
    "kullanılır (rapor is_official=True ile işaretler); DUSUK/ORTA "
    "sismisitede henüz doğrulanmamış TEMSİLİ katsayılar kullanılır "
    "(is_official=False). Her iki durumda da saha gözlemi (görsel "
    "düzensizlik tespiti, zemin etüdü) gerektirir. "
    "Kesin bir mühendislik/deprem güvenliği raporu DEĞİLDİR. Bağlayıcı bir "
    "karar (tahliye, güçlendirme, satın alma vb.) için mutlaka yetkili bir "
    "inşaat/deprem mühendisinden resmi bir değerlendirme alınmalıdır."
)


class RiskLevel(str, Enum):
    LOW = "dusuk"
    MODERATE = "orta"
    HIGH = "yuksek"
    VERY_HIGH = "cok_yuksek"


class SoilType(str, Enum):
    UNKNOWN = "bilinmiyor"
    ROCK = "kaya"
    STIFF_SOIL = "sert_zemin"
    SOFT_SOIL = "yumusak_zemin"
    VERY_SOFT_SOIL = "cok_yumusak_zemin"


#: Yumuşak zeminler deprem dalgasını büyütür (site amplification) — kaba
#: çarpanlar (gerçek zemin büyütme katsayıları için jeoteknik rapor gerekir).
_SOIL_SUBSCORE = {
    SoilType.UNKNOWN: None,
    SoilType.ROCK: 10.0,
    SoilType.STIFF_SOIL: 30.0,
    SoilType.SOFT_SOIL: 60.0,
    SoilType.VERY_SOFT_SOIL: 90.0,
}


@dataclass(frozen=True, slots=True)
class BuildingRiskFactor:
    name: str
    subscore_0_100: Optional[float]
    weight: float
    note: str


@dataclass(frozen=True, slots=True)
class BuildingRiskReport:
    risk_index_0_100: float
    risk_level: RiskLevel
    factors: tuple[BuildingRiskFactor, ...]
    disclaimer: str = DISCLAIMER

    def summary_lines(self) -> list[str]:
        lines = [
            f"Risk indeksi: {self.risk_index_0_100:.1f}/100 ({self.risk_level.value})",
        ]
        for f_ in self.factors:
            sub = "veri yok (skora dahil edilmedi)" if f_.subscore_0_100 is None else f"{f_.subscore_0_100:.1f}/100"
            lines.append(f"  - {f_.name} (ağırlık {f_.weight:.2f}): {sub} — {f_.note}")
        lines.append(self.disclaimer)
        return lines


def _pga_subscore(pga_g: float) -> float:
    # 0.10g -> ~15, 0.40g -> ~75, 0.60g+ -> 100 (kaba, parçalı-doğrusal).
    return max(0.0, min(100.0, (pga_g / 0.60) * 100.0))


def _construction_year_subscore(year: Optional[int]) -> Optional[float]:
    if year is None:
        return None
    if year < 1999:
        return 90.0  # 1999 Marmara depremi sonrası yönetmelik öncesi
    if year < 2007:
        return 60.0
    if year < 2019:
        return 35.0  # 2007 yönetmeliği
    return 15.0  # 2018/2019 Türkiye Bina Deprem Yönetmeliği sonrası


def _floor_count_subscore(floor_count: Optional[int]) -> Optional[float]:
    if floor_count is None:
        return None
    if floor_count <= 4:
        return 15.0
    if floor_count <= 8:
        return 35.0
    if floor_count <= 15:
        return 60.0
    return 85.0


def _slenderness_subscore(slenderness_ratio: Optional[float]) -> Optional[float]:
    if slenderness_ratio is None:
        return None
    # roadmap'in structural_validation modülündeki varsayılan limit ~4.0
    return max(0.0, min(100.0, (slenderness_ratio / 8.0) * 100.0))


class BasicBuildingType(str, Enum):
    """FEMA P-154'ün taşıyıcı sistem tipolojisiyle aynı mantıkta kaba
    sınıflandırma (tam FEMA kodları — W1, C1, S2 vb. — yerine, bu
    projenin `procedural_generator.BuildingType` ile eşleşen basit bir
    alt küme kullanılır)."""

    BETONARME_CERCEVE = "betonarme_cerceve"      # FEMA benzeri: C1
    BETONARME_PERDELI = "betonarme_perdeli"       # FEMA benzeri: C2
    YIGMA = "yigma"                                # FEMA benzeri: URM/RM
    CELIK_CERCEVE = "celik_cerceve"                # FEMA benzeri: S1/S3
    AHSAP = "ahsap"                                # FEMA benzeri: W1


class SeismicityLevel(str, Enum):
    """PGA'dan türetilen kaba sismisite sınıfı — TBDY'nin resmi tasarım
    spektral ivme kategorileriyle (SDS bazlı) BİREBİR AYNI EŞİKLERİ
    kullanmaz (bu, resmi spektral analiz gerektirir); yalnızca RVS temel
    skor tablosunu seçmek için basit bir PGA eşiklemesidir."""

    DUSUK = "dusuk"
    ORTA = "orta"
    YUKSEK = "yuksek"
    COK_YUKSEK = "cok_yuksek"


def _seismicity_from_pga(pga_g: float) -> SeismicityLevel:
    if pga_g < 0.10:
        return SeismicityLevel.DUSUK
    if pga_g < 0.20:
        return SeismicityLevel.ORTA
    if pga_g < 0.40:
        return SeismicityLevel.YUKSEK
    return SeismicityLevel.COK_YUKSEK


#: RESMİ FEMA P-154 (3. Baskı, Ocak 2015) Temel Skor tablosu — HIGH
#: seismicity sütunu, "FEMA P-154 Data Collection Form — HIGH Seismicity"
#: resmi tarama formundan (ATC/FEMA yayını, ABD federal kamu malı)
#: BİREBİR alınmıştır: 17 FEMA Bina Tipi (W1 W1A W2 S1 S2 S3 S4 S5 C1 C2
#: C3 PC1 PC2 RM1 RM2 URM MH) → Basic Score (3.6 3.2 2.9 2.1 2.0 2.6 2.0
#: 1.7 1.5 2.0 1.2 1.6 1.4 1.7 1.7 1.0 1.5).
#:
#: DÜRÜST KAPSAM SINIRI: Bu oturumda yalnızca "High Seismicity" formu
#: güvenilir/tam OCR kalitesiyle doğrulanabildi (Moderately High/
#: Moderate/Low/Very High formlarının resmi sayıları henüz doğrulanmadı
#: — yanlış hizalanmış bir sayıyı "resmi" diye sunmak, temsili değer
#: sunmaktan daha kötü bir hata olur). Bu yüzden DUSUK/ORTA sismisite
#: seviyeleri hâlâ TEMSİLİ (kalibrasyon gerektiren) değerler kullanır;
#: YUKSEK/COK_YUKSEK ise resmi HIGH formuna eşlenir. `_basic_score_for`
#: hangi kaynağın kullanıldığını (`is_official`) açıkça döndürür.
FEMA_P154_BASIC_SCORE_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: 3.6,               # W1 — Light wood frame
    BasicBuildingType.CELIK_CERCEVE: 2.1,       # S1 — Steel moment frame
    BasicBuildingType.BETONARME_PERDELI: 2.0,   # C2 — Concrete shear wall
    BasicBuildingType.BETONARME_CERCEVE: 1.5,   # C1 — Concrete moment frame
    BasicBuildingType.YIGMA: 1.0,               # URM — Unreinforced masonry
}

#: Resmi FEMA P-154 HIGH seismicity form modifier satırları — aynı 5 bina
#: tipi (W1/S1/C2/C1/URM) için, aynı kaynak formdan. `None` = formda "NA".
FEMA_P154_SEVERE_VERTICAL_IRR_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: -1.2, BasicBuildingType.CELIK_CERCEVE: -1.0,
    BasicBuildingType.BETONARME_PERDELI: -1.0, BasicBuildingType.BETONARME_CERCEVE: -0.9,
    BasicBuildingType.YIGMA: -0.7,
}
FEMA_P154_PLAN_IRR_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: -1.1, BasicBuildingType.CELIK_CERCEVE: -0.8,
    BasicBuildingType.BETONARME_PERDELI: -0.8, BasicBuildingType.BETONARME_CERCEVE: -0.6,
    BasicBuildingType.YIGMA: -0.4,
}
FEMA_P154_PRE_CODE_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: -1.1, BasicBuildingType.CELIK_CERCEVE: -0.6,
    BasicBuildingType.BETONARME_PERDELI: -0.7, BasicBuildingType.BETONARME_CERCEVE: -0.4,
    BasicBuildingType.YIGMA: 0.0,
}
FEMA_P154_POST_BENCHMARK_HIGH: dict[BasicBuildingType, Optional[float]] = {
    BasicBuildingType.AHSAP: 1.6, BasicBuildingType.CELIK_CERCEVE: 1.4,
    BasicBuildingType.BETONARME_PERDELI: 2.1, BasicBuildingType.BETONARME_CERCEVE: 1.9,
    BasicBuildingType.YIGMA: None,  # NA formda
}
FEMA_P154_SOIL_AB_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: 0.1, BasicBuildingType.CELIK_CERCEVE: 0.4,
    BasicBuildingType.BETONARME_PERDELI: 0.5, BasicBuildingType.BETONARME_CERCEVE: 0.4,
    BasicBuildingType.YIGMA: 0.3,
}
FEMA_P154_SOIL_E_1_3_HIGH: dict[BasicBuildingType, float] = {
    BasicBuildingType.AHSAP: 0.2, BasicBuildingType.CELIK_CERCEVE: -0.2,
    BasicBuildingType.BETONARME_PERDELI: 0.0, BasicBuildingType.BETONARME_CERCEVE: 0.0,
    BasicBuildingType.YIGMA: -0.2,
}

#: TEMSİLİ temel skorlar (bina tipi x sismisite) — YALNIZCA DUSUK/ORTA
#: sismisite için kullanılır (YUKSEK/COK_YUKSEK artık yukarıdaki resmi
#: FEMA_P154_BASIC_SCORE_HIGH tablosunu kullanır, bkz `_basic_score_for`).
TEMSILI_TEMEL_SKORLAR: dict[BasicBuildingType, dict[SeismicityLevel, float]] = {
    BasicBuildingType.AHSAP: {SeismicityLevel.DUSUK: 3.4, SeismicityLevel.ORTA: 3.0},
    BasicBuildingType.CELIK_CERCEVE: {SeismicityLevel.DUSUK: 3.0, SeismicityLevel.ORTA: 2.6},
    BasicBuildingType.BETONARME_PERDELI: {SeismicityLevel.DUSUK: 2.8, SeismicityLevel.ORTA: 2.4},
    BasicBuildingType.BETONARME_CERCEVE: {SeismicityLevel.DUSUK: 2.2, SeismicityLevel.ORTA: 1.8},
    BasicBuildingType.YIGMA: {SeismicityLevel.DUSUK: 1.8, SeismicityLevel.ORTA: 1.4},
}


def _basic_score_for(building_type: BasicBuildingType, seismicity: SeismicityLevel) -> tuple[float, bool]:
    """(skor, is_official) döndürür. is_official=True ise skor
    FEMA_P154_BASIC_SCORE_HIGH'tan (resmi form), False ise
    TEMSILI_TEMEL_SKORLAR'dan (kalibrasyon gerektiren temsili değer)."""
    if seismicity in (SeismicityLevel.YUKSEK, SeismicityLevel.COK_YUKSEK):
        return FEMA_P154_BASIC_SCORE_HIGH[building_type], True
    return TEMSILI_TEMEL_SKORLAR[building_type][seismicity], False


#: Karar eşiği — FEMA P-154 resmi formlarında S = 2.0 (RESMİ, bkz. FEMA
#: P-155 §2.6: "permit retaining the same cut-off score of S = 2.0").
DECISION_CUTOFF_SCORE = 2.0


@dataclass(frozen=True, slots=True)
class ScoreModifier:
    name: str
    delta: float
    note: str


@dataclass(frozen=True, slots=True)
class RVSReport:
    """FEMA P-154 / TBDY Ek-2 tarzı hızlı görsel tarama sonucu."""

    basic_score: float
    modifiers: tuple[ScoreModifier, ...]
    final_score: float
    cutoff_score: float
    detailed_evaluation_recommended: bool
    building_type: BasicBuildingType
    seismicity_level: SeismicityLevel
    is_official: bool = False
    disclaimer: str = DISCLAIMER

    def summary_lines(self) -> list[str]:
        kaynak = ("RESMİ FEMA P-154 3. Baskı HIGH seismicity formu"
                  if self.is_official else "TEMSİLİ (kalibrasyon gerektirir)")
        lines = [
            f"RVS temel skor ({self.building_type.value}, {self.seismicity_level.value} sismisite, "
            f"kaynak: {kaynak}): {self.basic_score:.2f}",
        ]
        for m in self.modifiers:
            sign = "+" if m.delta >= 0 else ""
            lines.append(f"  - {m.name}: {sign}{m.delta:.2f} — {m.note}")
        lines.append(f"Nihai skor: {self.final_score:.2f} (eşik: {self.cutoff_score:.2f})")
        lines.append(
            "SONUÇ: Detaylı değerlendirme ÖNERİLİR (eşik altı)."
            if self.detailed_evaluation_recommended
            else "SONUÇ: Eşiğin üzerinde — yine de bu bir ön-tarama sonucudur, "
                 "kesin güvenlik onayı değildir."
        )
        lines.append(self.disclaimer)
        return lines


def rapid_visual_screening(
    *,
    building_type: BasicBuildingType,
    pga_g: float,
    vertical_irregularity: bool = False,
    plan_irregularity: bool = False,
    short_column_risk: bool = False,
    soft_story_suspected: bool = False,
    pounding_risk: bool = False,
    pre_1999_construction: bool = False,
    post_2019_construction: bool = False,
    soil_type: SoilType = SoilType.UNKNOWN,
) -> RVSReport:
    """FEMA P-154 / TBDY Ek-2 metodolojisinin YAPISINI uygular: Temel Skor
    + additif Skor Değiştiricileri = Nihai Skor; eşik altı -> "detaylı
    değerlendirme önerilir".

    Bu, `score_building_risk()`in ağırlıklı-ortalama indeksinden FARKLI
    (ve ona ek) bir yöntemdir: burada skorlar TOPLANIR (FEMA P-154'ün
    kendine özgü additif yapısı), 0-100 aralığına normalize edilmez.
    Girdi bayrakları (düzensizlik, yumuşak kat vb.) sahada bir mühendis
    tarafından gözlemlenmelidir — bu fonksiyon bu gözlemleri ÜRETMEZ,
    yalnızca verilen gözlemleri doğru additif yapıyla birleştirir.
    """
    seismicity = _seismicity_from_pga(pga_g)
    basic, is_official = _basic_score_for(building_type, seismicity)

    modifiers: list[ScoreModifier] = []
    if is_official:
        # Resmi FEMA P-154 3. Baskı HIGH seismicity formundan birebir
        # değerler (bkz. modül seviyesi FEMA_P154_* tabloları ve kaynak
        # notu). Vertical irregularity Level-1 formunda tek bir "Severe"
        # satırı vardır (ayrı moderate satırı Level-2 formundadır) — bu
        # yüzden vertical_irregularity/soft_story_suspected bayrakları
        # aynı resmi "Severe Vertical Irregularity" satırına eşlenir.
        if vertical_irregularity or soft_story_suspected:
            modifiers.append(ScoreModifier(
                "Düşey düzensizlik (Severe Vertical Irregularity, VL1)",
                FEMA_P154_SEVERE_VERTICAL_IRR_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri.",
            ))
        if plan_irregularity:
            modifiers.append(ScoreModifier(
                "Plan düzensizliği (Plan Irregularity, PL1)",
                FEMA_P154_PLAN_IRR_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri.",
            ))
        if short_column_risk:
            # Resmi formda ayrı bir "kısa kolon" satırı yok — bu gözlem
            # Level-1 formunda Plan/Vertical düzensizlik kapsamına girer;
            # burada dürüstçe TEMSİLİ bir ek düzeltme olarak işaretlenir.
            modifiers.append(ScoreModifier(
                "Kısa kolon riski (kısmi dolgu duvar/bant pencere)", -0.4,
                "TEMSİLİ — resmi formda ayrı bir satırı yok, mühendislik "
                "literatüründeki genel etkiye dayanır (kalibrasyon gerektirir).",
            ))
        if pounding_risk:
            modifiers.append(ScoreModifier(
                "Bitişik nizam çarpışma (pounding) riski", -0.3,
                "TEMSİLİ — resmi Level-1 formunda pounding ayrı bir sayısal "
                "modifier değil, doğrudan 'Detaylı Değerlendirme' tetikleyicisidir "
                "(FEMA P-155 §2.14); burada nicel etki temsili olarak modellenmiştir.",
            ))
        if pre_1999_construction:
            modifiers.append(ScoreModifier(
                "Pre-Code (1999 öncesi yönetmelik)",
                FEMA_P154_PRE_CODE_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri.",
            ))
        if post_2019_construction and FEMA_P154_POST_BENCHMARK_HIGH[building_type] is not None:
            modifiers.append(ScoreModifier(
                "Post-Benchmark (2019 sonrası TBDY 2018 dönemi)",
                FEMA_P154_POST_BENCHMARK_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri.",
            ))
        if soil_type is SoilType.ROCK or soil_type is SoilType.STIFF_SOIL:
            modifiers.append(ScoreModifier(
                f"Zemin tipi ({soil_type.value}, FEMA Soil A/B karşılığı)",
                FEMA_P154_SOIL_AB_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri.",
            ))
        elif soil_type is SoilType.VERY_SOFT_SOIL:
            modifiers.append(ScoreModifier(
                f"Zemin tipi ({soil_type.value}, FEMA Soil E karşılığı)",
                FEMA_P154_SOIL_E_1_3_HIGH[building_type],
                "RESMİ FEMA P-154 3. Baskı HIGH seismicity form değeri (Soil E, 1-3 kat).",
            ))
        elif soil_type is SoilType.SOFT_SOIL:
            modifiers.append(ScoreModifier(
                f"Zemin tipi ({soil_type.value})", -0.3,
                "TEMSİLİ — resmi form Soil C/D'yi temel (Soil Type CD) kabul eder, "
                "ayrı bir sayısal modifier vermez; burada A/B ile E arası ara değer kullanılmıştır.",
            ))
    else:
        # DUSUK/ORTA sismisite — resmi tablo henüz doğrulanmadı, dürüstçe
        # temsili katsayılar kullanılır (bkz. modül docstring'i).
        if vertical_irregularity:
            modifiers.append(ScoreModifier(
                "Düşey düzensizlik (yumuşak/zayıf kat, kütle düzensizliği)", -0.9,
                "TEMSİLİ — FEMA P-154'te en ağır negatif düzeltmelerden biri, "
                "TBDY B1/B2 düzensizlikleriyle eşdeğer kavram (kalibrasyon gerektirir).",
            ))
        if soft_story_suspected:
            modifiers.append(ScoreModifier(
                "Yumuşak/zayıf kat şüphesi (örn. zemin kat dükkan/açık cephe)", -0.5,
                "TEMSİLİ — sahada doğrulanmalı, TBDY B2 düzensizliği ile örtüşür.",
            ))
        if plan_irregularity:
            modifiers.append(ScoreModifier(
                "Plan düzensizliği (L/U/T biçimi, burulma potansiyeli)", -0.4,
                "TEMSİLİ — TBDY A1 (burulma düzensizliği) ile örtüşen görsel gösterge.",
            ))
        if short_column_risk:
            modifiers.append(ScoreModifier(
                "Kısa kolon riski (kısmi dolgu duvar/bant pencere)", -0.4,
                "TEMSİLİ — kısa kolon etkisi, deprem hasarlarında sık görülen bir mekanizmadır.",
            ))
        if pounding_risk:
            modifiers.append(ScoreModifier(
                "Bitişik nizam çarpışma (pounding) riski", -0.3,
                "TEMSİLİ — komşu bina ile dilatasyon derzi yetersiz/yok ise geçerli.",
            ))
        if pre_1999_construction:
            modifiers.append(ScoreModifier(
                "1999 öncesi yönetmelik (pre-code)", -0.6,
                "TEMSİLİ — 1999 Marmara depremi öncesi yönetmelik dönemi.",
            ))
        if post_2019_construction:
            modifiers.append(ScoreModifier(
                "2019 sonrası TBDY 2018 dönemi (post-benchmark)", +0.4,
                "TEMSİLİ — güncel Türkiye Bina Deprem Yönetmeliği dönemi.",
            ))
        soil_modifier_map = {
            SoilType.VERY_SOFT_SOIL: -0.5, SoilType.SOFT_SOIL: -0.3,
            SoilType.STIFF_SOIL: 0.0, SoilType.ROCK: +0.2,
        }
        if soil_type in soil_modifier_map and soil_type is not SoilType.UNKNOWN:
            modifiers.append(ScoreModifier(
                f"Zemin tipi ({soil_type.value})", soil_modifier_map[soil_type],
                "TEMSİLİ — zemin büyütmesi (site amplification) etkisi, jeoteknik veriyle doğrulanmalı.",
            ))

    final_score = basic + sum(m.delta for m in modifiers)
    return RVSReport(
        basic_score=basic,
        modifiers=tuple(modifiers),
        final_score=final_score,
        cutoff_score=DECISION_CUTOFF_SCORE,
        detailed_evaluation_recommended=final_score < DECISION_CUTOFF_SCORE,
        building_type=building_type,
        seismicity_level=seismicity,
        is_official=is_official,
    )


def _risk_level_from_index(index: float) -> RiskLevel:
    if index < 30:
        return RiskLevel.LOW
    if index < 55:
        return RiskLevel.MODERATE
    if index < 75:
        return RiskLevel.HIGH
    return RiskLevel.VERY_HIGH


def score_building_risk(
    *,
    pga_g: float,
    construction_year: Optional[int] = None,
    floor_count: Optional[int] = None,
    slenderness_ratio: Optional[float] = None,
    soil_type: SoilType = SoilType.UNKNOWN,
) -> BuildingRiskReport:
    """Roadmap Faz 2.3'ün tarif ettiği "basit risk indeksi"ni hesaplar.

    Tüm alanlar opsiyonel (`pga_g` hariç) — eksik veri varsayımla
    doldurulmaz, o faktör skora dahil edilmeden `note` içinde açıkça
    "veri yok" olarak raporlanır ve ağırlığı normalize edilirken düşülür.
    """
    candidates = [
        BuildingRiskFactor("Bölgesel PGA", _pga_subscore(pga_g), 0.40,
                            f"pga={pga_g:.2f}g"),
        BuildingRiskFactor("Yapım yılı / yönetmelik dönemi",
                            _construction_year_subscore(construction_year), 0.25,
                            "1999/2007/2018 yönetmelik eşikleri" if construction_year is not None else "yapım yılı verilmedi"),
        BuildingRiskFactor("Kat sayısı", _floor_count_subscore(floor_count), 0.15,
                            "yükseklik arttıkça deprem kuvveti/burulma riski artar" if floor_count is not None else "kat sayısı verilmedi"),
        BuildingRiskFactor("Narinlik oranı (structural_validation)",
                            _slenderness_subscore(slenderness_ratio), 0.10,
                            "structural_validation.validate_building raporundan" if slenderness_ratio is not None else "narinlik oranı verilmedi"),
        BuildingRiskFactor("Zemin tipi", _SOIL_SUBSCORE[soil_type], 0.10,
                            "jeoteknik veri yok" if soil_type is SoilType.UNKNOWN else f"zemin={soil_type.value}"),
    ]

    weighted_sum = 0.0
    weight_total = 0.0
    for f_ in candidates:
        if f_.subscore_0_100 is not None:
            weighted_sum += f_.subscore_0_100 * f_.weight
            weight_total += f_.weight

    index = (weighted_sum / weight_total) if weight_total > 0 else _pga_subscore(pga_g)
    return BuildingRiskReport(
        risk_index_0_100=index,
        risk_level=_risk_level_from_index(index),
        factors=tuple(candidates),
    )
