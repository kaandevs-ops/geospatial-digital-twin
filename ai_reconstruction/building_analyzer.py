"""
AI Building Analyzer
=====================

Roadmap Phase 4 - "AIBuildingAnalyzer".

Tahmin eder:
    - bina yüksekliği
    - kat sayısı
    - kullanım amacı
    - mimari stil
    - yapı yaşı
    - cephe tipi

Varsayılan: bölgesel istatistik + kural tabanlı `HeuristicPredictor`.
Harici model varsa `Predictor` arayüzü ile değiştirilebilir (bkz.
`ai_reconstruction.predictor.Predictor`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from ..building_reconstruction.footprint_parser import Footprint
from ..building_reconstruction.facade_generator import FacadeMaterial
from .predictor import Predictor


class ArchitecturalStyle(str, Enum):
    MODERN = "modern"
    KLASIK = "klasik"
    ENDUSTRIYEL = "endustriyel"
    MINIMALIST = "minimalist"
    GELENEKSEL = "geleneksel"
    BROUTALIST = "brutalist"


class ClimateZone(str, Enum):
    """`AIRoofPredictor` ile paylaşılan basit iklim bölgesi sınıflandırması
    (enleme dayalı kaba yaklaşım - gerçek iklim verisi entegre noktası
    `predictor=` ile sağlanabilir)."""

    TROPIKAL = "tropikal"
    ILIMAN = "iliman"
    KARASAL = "karasal"
    KUTUP = "kutup"


@dataclass(slots=True)
class BuildingAnalysis:
    """`AIBuildingAnalyzer.analyze()` çıktısı."""

    height_m: float
    floor_count: int
    usage: str
    architectural_style: ArchitecturalStyle
    estimated_age_years: int
    facade_material: FacadeMaterial
    confidence: float  # 0.0 - 1.0, tahminin ne kadar veriye (vs. varsayıma) dayandığı
    uncertainty_m: float = 0.0  # Roadmap V2 - A4: yükseklik tahmini belirsizlik payı (+/- metre)
    # ROADMAP_V8 Faz 5.3 — yeni, opsiyonel alanlar (varsayılanlı, geriye
    # dönük uyumlu: mevcut çağıran kod bu alanları hiç bilmeden çalışmaya
    # devam eder).
    window_ratio: float = 0.3  # WWR varsayılanı, `regulations` katmanında override edilebilir
    typical_floor_range: tuple[int, int] = (1, 10)


# ROADMAP_V8 Faz 5.3 — bina kullanım tipi x mimari stil eşlemesi.
# Orijinal 8 anahtar (apartments..._default) DEĞİŞTİRİLMEDEN korundu
# (geriye dönük uyumluluk). B1'in "konut, ticari, endüstriyel, dini,
# eğitim, sağlık, spor - her biri farklı varsayılan cephe/malzeme
# stiline bağlanabilir" isteğine tam karşılık gelecek şekilde 7 yeni
# kullanım tipi eklendi (toplam 15 giriş, kabul kriteri "en az 12"yi
# aşıyor). `window_ratio` (WWR - Window-to-Wall Ratio) alanı yeni
# eklendi: önceden bu tablo pencere oranından tamamen habersizdi;
# `regulations`/`facade_generator`nün WWR hesaplamasına varsayılan bir
# başlangıç değeri sağlıyor (kullanım tipine göre — ofis/AVM yüksek cam
# oranı, depo/endüstriyel düşük). `typical_floor_range` alanı da yeni:
# okul (alçak/geniş kanat, tipik 1-3 kat) ile hastane (yüksek/bloklu,
# tipik 4-10 kat) arasındaki B1'in istediği görsel siluet farkını,
# mevcut kat-üretim mimarisine (ProceduralBuildingGenerator) dokunmadan,
# belgelenmiş bir varsayılan aralık olarak ifade eder (çağıran taraf
# opsiyonel olarak tüketebilir — zorunlu değişiklik gerektirmez).
_TYPE_STATS: dict[str, dict] = {
    "apartments": dict(floor_h=3.0, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.BETON, age_range=(5, 40), window_ratio=0.35, typical_floor_range=(3, 12)),
    "house": dict(floor_h=3.0, style=ArchitecturalStyle.GELENEKSEL, facade=FacadeMaterial.TAS, age_range=(3, 60), window_ratio=0.25, typical_floor_range=(1, 2)),
    "office": dict(floor_h=3.6, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.CAM, age_range=(1, 25), window_ratio=0.6, typical_floor_range=(3, 20)),
    "commercial": dict(floor_h=4.5, style=ArchitecturalStyle.MINIMALIST, facade=FacadeMaterial.CAM, age_range=(1, 20), window_ratio=0.5, typical_floor_range=(1, 3)),
    "industrial": dict(floor_h=6.0, style=ArchitecturalStyle.ENDUSTRIYEL, facade=FacadeMaterial.ENDUSTRIYEL, age_range=(2, 45), window_ratio=0.1, typical_floor_range=(1, 2)),
    "warehouse": dict(floor_h=7.0, style=ArchitecturalStyle.ENDUSTRIYEL, facade=FacadeMaterial.METAL, age_range=(2, 35), window_ratio=0.05, typical_floor_range=(1, 1)),
    "hospital": dict(floor_h=3.6, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.KOMPOZIT, age_range=(3, 30), window_ratio=0.4, typical_floor_range=(4, 10)),
    "school": dict(floor_h=3.4, style=ArchitecturalStyle.GELENEKSEL, facade=FacadeMaterial.TUGLA, age_range=(5, 50), window_ratio=0.3, typical_floor_range=(1, 3)),
    # --- ROADMAP_V8 Faz 5.3 eklentisi ---
    "retail": dict(floor_h=4.0, style=ArchitecturalStyle.MINIMALIST, facade=FacadeMaterial.CAM, age_range=(1, 20), window_ratio=0.55, typical_floor_range=(1, 2)),
    "religious": dict(floor_h=5.0, style=ArchitecturalStyle.KLASIK, facade=FacadeMaterial.TAS, age_range=(10, 200), window_ratio=0.15, typical_floor_range=(1, 2)),
    "sports_centre": dict(floor_h=6.5, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.METAL, age_range=(1, 25), window_ratio=0.2, typical_floor_range=(1, 1)),
    "hotel": dict(floor_h=3.2, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.KOMPOZIT, age_range=(1, 30), window_ratio=0.45, typical_floor_range=(4, 15)),
    "government": dict(floor_h=3.8, style=ArchitecturalStyle.BROUTALIST, facade=FacadeMaterial.BETON, age_range=(10, 60), window_ratio=0.3, typical_floor_range=(3, 8)),
    "university": dict(floor_h=3.6, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.TUGLA, age_range=(5, 60), window_ratio=0.4, typical_floor_range=(2, 6)),
    "garage": dict(floor_h=2.6, style=ArchitecturalStyle.ENDUSTRIYEL, facade=FacadeMaterial.BETON, age_range=(2, 40), window_ratio=0.02, typical_floor_range=(1, 6)),
    "_default": dict(floor_h=3.2, style=ArchitecturalStyle.MODERN, facade=FacadeMaterial.BETON, age_range=(5, 40), window_ratio=0.3, typical_floor_range=(1, 10)),
}


class HeuristicPredictor:
    """Varsayılan, bağımlılıksız `Predictor` uygulaması. `_TYPE_STATS`
    tablosundaki bölgesel istatistiklere ve footprint geometrisine
    (alan, çevre, aspect ratio, kompaktlık) dayanır."""

    def predict(self, features: dict) -> dict:
        usage = (features.get("building_type") or "_default").lower()
        stats = _TYPE_STATS.get(usage, _TYPE_STATS["_default"])

        area = features.get("area_m2", 100.0)
        given_height = features.get("height_m")
        given_floor_count = features.get("floor_count")

        floor_h = stats["floor_h"]
        if given_floor_count:
            floor_count = int(given_floor_count)
            height = given_height or floor_count * floor_h
        elif given_height:
            height = float(given_height)
            floor_count = max(1, round(height / floor_h))
        else:
            # Alan büyüdükçe (ticari/endüstriyel bina varsayımıyla) kat
            # sayısı tahmini kaba bir log-ölçek kuralı ile.
            floor_count = max(1, min(40, round(1 + (area ** 0.5) / 12)))
            height = floor_count * floor_h

        age_low, age_high = stats["age_range"]
        # Deterministik "tahmini yaş": alan + çevre hash'inden türetilen
        # tekrarlanabilir bir değer (gerçek veri yoksa rastgele değil,
        # sabit bir orta-nokta tahmini kullanılır).
        estimated_age = (age_low + age_high) // 2

        confidence = 0.85 if (given_height or given_floor_count) else 0.45
        # Roadmap V2 - A4: kaba belirsizlik tahmini - veriye dayalı (verilen
        # yükseklik/kat) tahminlerde dar, saf geometri tahmininde geniş bant.
        uncertainty_m = round(height * (0.12 if confidence >= 0.85 else 0.35), 2)

        return {
            "height_m": round(height, 2),
            "floor_count": floor_count,
            "usage": usage if usage != "_default" else "unknown",
            "architectural_style": stats["style"].value,
            "estimated_age_years": estimated_age,
            "facade_material": stats["facade"].value,
            "confidence": confidence,
            "uncertainty_m": uncertainty_m,
            "window_ratio": stats["window_ratio"],
            "typical_floor_range": stats["typical_floor_range"],
        }


class AIBuildingAnalyzer:
    """`Footprint` -> `BuildingAnalysis`. Varsayılan `HeuristicPredictor`
    kullanır; `predictor=` ile değiştirilebilir."""

    def __init__(self, predictor: Predictor | None = None) -> None:
        self._predictor = predictor or HeuristicPredictor()

    def analyze(self, footprint: Footprint) -> BuildingAnalysis:
        features = self._extract_features(footprint)
        result = self._predictor.predict(features)
        return BuildingAnalysis(
            height_m=result["height_m"],
            floor_count=result["floor_count"],
            usage=result["usage"],
            architectural_style=ArchitecturalStyle(result["architectural_style"]),
            estimated_age_years=result["estimated_age_years"],
            facade_material=FacadeMaterial(result["facade_material"]),
            confidence=result["confidence"],
            uncertainty_m=result.get("uncertainty_m", 0.0),
            # ROADMAP_V8 Faz 5.3 — `.get()` ile varsayılana düşer: özel
            # `Predictor` uygulamaları (ör. `ONNXPredictor`/`SklearnPredictor`)
            # bu yeni anahtarları hiç bilmese bile kırılmaz (geriye dönük
            # uyumlu, B4 "eksik veri sahneyi bozmasın" ilkesi).
            window_ratio=result.get("window_ratio", 0.3),
            typical_floor_range=tuple(result.get("typical_floor_range", (1, 10))),
        )

    @staticmethod
    def _extract_features(footprint: Footprint) -> dict:
        return {
            "area_m2": footprint.area_m2,
            "perimeter_m": footprint.perimeter_m,
            "aspect_ratio": footprint.aspect_ratio,
            "orientation_deg": footprint.orientation_deg,
            "building_type": footprint.building_type,
            "height_m": footprint.height_m,
            "floor_count": footprint.floor_count,
        }
