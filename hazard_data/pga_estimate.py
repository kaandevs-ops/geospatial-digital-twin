"""Bölgesel PGA (Peak Ground Acceleration) tahmini — roadmap Faz 2.3.

DÜRÜST DURUM TESPİTİ (doğrulanmış):
    Resmi Türkiye Deprem Tehlike Haritası (TDTH), AFAD'ın
    `https://tdth.afad.gov.tr/TDTH/` adresindeki etkileşimli web
    uygulaması üzerinden sunuluyor ve bu uygulama e-Devlet kimlik
    doğrulaması ARKASINDA (anonim/genel-amaçlı bir REST/GeoTIFF servisi
    YOK). Yani bu "bizim ağ erişimimiz yok" durumu değil — resmi kaynağın
    kendisi programatik/anonim erişime kapalı, kişisel e-Devlet oturumu
    gerektiriyor. Bu modül bu sınırı gizlemez, üç farklı doğruluk
    seviyesi sunar:

    1. `DEFAULT_TURKEY_PGA_ZONES`: büyük şehir merkezleri için haber/özet
       kaynaklarda yayınlanan **kabaca** DD-2 (475 yıl dönüş periyodu /
       %10-in-50) referans noktaları. Nokta-kestirimdir, resmi haritanın
       YERİNE GEÇMEZ.
    2. `RegionalPGAEstimate`: bu referans noktalar arasında artık tek-en
       -yakın-nokta yerine TERS-MESAFE-AĞIRLIKLI (IDW) enterpolasyon
       yapar — birden fazla referans noktasının etkisini mesafeyle
       ağırlıklandırarak daha yumuşak/daha savunulabilir bir ara-değer
       üretir (yine de resmi PSHA gridinin yerine geçmez).
    3. `AttenuationPGAEstimate`: kullanıcı bir depremin büyüklüğünü (Mw)
       ve fay/kaynağa olan mesafeyi (km) biliyorsa, klasik, yayınlanmış
       bir azalım (attenuation) ilişkisiyle (Esteva & Villaverde, 1973 —
       genel/küresel ortalama bir ampirik ilişki, TÜRKİYE'YE ÖZEL
       KALİBRE EDİLMEMİŞTİR) fiziksel bir kestirim üretir. Bu "tablodan
       okuma" değil gerçek bir büyüklük-mesafe azalım hesabıdır; ama
       resmi bir bölgesel GMPE (AFAD'ın PSHA modelinde kullandığı) kadar
       hassas değildir.
    4. `RegionalPGAEstimate(lookup_fn=...)`: kullanıcının kendi resmi
       TDTH oturumundan (örn. manuel export edilmiş bir GeoTIFF/CSV)
       okuduğu noktasal veriyi enjekte edebileceği bir arayüz — gerçek
       üretimde en doğru yol budur.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Optional, Sequence


@dataclass(frozen=True, slots=True)
class PGAZone:
    """Bir nokta çevresi için kaba PGA bölgesi (DD-2 / 475 yıl, g cinsinden)."""

    name: str
    latitude: float
    longitude: float
    pga_g: float
    source_note: str


#: DÜRÜST SINIRLAMA: bunlar TDTH'nin kamuya açık özetlerinden alınmış
#: KABA nokta değerleridir (şehir merkezi), resmi harita değildir.
#: Gerçek üretimde `RegionalPGAEstimate(lookup_fn=...)` ile resmi
#: TDTH/AFAD raster servisine bağlanmalıdır.
DEFAULT_TURKEY_PGA_ZONES: tuple[PGAZone, ...] = (
    PGAZone("İstanbul (Avrupa yakası)", 41.01, 28.95, 0.40, "TDTH özet - yaklaşık"),
    PGAZone("İzmir", 38.42, 27.14, 0.40, "TDTH özet - yaklaşık"),
    PGAZone("Ankara", 39.93, 32.86, 0.20, "TDTH özet - yaklaşık"),
    PGAZone("Kahramanmaraş", 37.57, 36.93, 0.55, "TDTH özet - yaklaşık"),
    PGAZone("Van", 38.49, 43.38, 0.45, "TDTH özet - yaklaşık"),
    PGAZone("Erzincan", 39.75, 39.49, 0.50, "TDTH özet - yaklaşık"),
    PGAZone("Bursa", 40.18, 29.06, 0.35, "TDTH özet - yaklaşık"),
    PGAZone("Antalya", 36.90, 30.71, 0.25, "TDTH özet - yaklaşık"),
)

#: Bu tablonun kapsamadığı yerler (örn. TR dışı) için düşük-güvenli varsayılan.
FALLBACK_PGA_G = 0.15


@dataclass
class RegionalPGAEstimate:
    """Bir (lat, lon) noktası için PGA değeri döndürür.

    `lookup_fn` verilirse önce o denenir (gerçek TDTH/ShakeMap servisine
    bağlamak için) — `None` dönerse ya da verilmezse `zones` tablosundaki
    en yakın noktaya (basit büyük-daire yaklaşıklığı, kısa mesafeler için
    yeterli) düşülür.
    """

    zones: Sequence[PGAZone] = DEFAULT_TURKEY_PGA_ZONES
    fallback_pga_g: float = FALLBACK_PGA_G
    lookup_fn: Optional[Callable[[float, float], Optional[float]]] = None
    max_zone_distance_km: float = 150.0

    #: IDW ağırlıklandırma üssü — 2.0 standart jeostatistik varsayılanı
    #: (mesafe karesiyle ters orantılı ağırlık). Daha büyük değer daha
    #: "yerel" (en yakın noktaya daha bağımlı), daha küçük değer daha
    #: "yumuşatılmış" bir yüzey verir.
    idw_power: float = 2.0
    #: IDW'ye dahil edilecek en yakın komşu sayısı (hepsi değil — çok
    #: uzaktaki bir şehrin etkisi anlamsız olur).
    idw_neighbors: int = 3

    def estimate(self, latitude: float, longitude: float) -> "PGAEstimateResult":
        if self.lookup_fn is not None:
            official = self.lookup_fn(latitude, longitude)
            if official is not None:
                return PGAEstimateResult(
                    pga_g=float(official), zone_name=None,
                    is_official_source=True,
                    note="Kullanıcı tarafından sağlanan resmi TDTH/ShakeMap verisi.",
                )

        ranked = self._ranked_zones(latitude, longitude)
        if not ranked:
            return self._fallback_result()

        nearest, nearest_dist = ranked[0]
        if nearest_dist > self.max_zone_distance_km:
            return self._fallback_result()

        # Tam üstüne denk gelme durumunda IDW ağırlığı sonsuza gider —
        # doğrudan en yakın noktanın değerini kullan.
        if nearest_dist < 1e-6:
            return PGAEstimateResult(
                pga_g=nearest.pga_g, zone_name=nearest.name, is_official_source=False,
                note=f"{nearest.name} referans noktasıyla (neredeyse) çakışık. {nearest.source_note}",
            )

        neighbors = ranked[: max(1, self.idw_neighbors)]
        neighbors = [(z, d) for z, d in neighbors if d <= self.max_zone_distance_km]
        if not neighbors:
            neighbors = [(nearest, nearest_dist)]

        weights = [1.0 / (d ** self.idw_power) for _, d in neighbors]
        weight_sum = sum(weights)
        pga_interp = sum(w * z.pga_g for (z, _), w in zip(neighbors, weights)) / weight_sum

        contributors = ", ".join(f"{z.name} (~{d:.0f} km, w={w/weight_sum:.2f})" for (z, d), w in zip(neighbors, weights))
        return PGAEstimateResult(
            pga_g=pga_interp, zone_name=nearest.name, is_official_source=False,
            note=(
                f"Ters-mesafe-ağırlıklı (IDW, p={self.idw_power:.1f}) enterpolasyon, "
                f"{len(neighbors)} referans noktasından: {contributors}. "
                "Bu bir ara-değerdir, resmi TDTH PSHA gridinin yerine geçmez."
            ),
        )

    def _fallback_result(self) -> "PGAEstimateResult":
        return PGAEstimateResult(
            pga_g=self.fallback_pga_g, zone_name=None, is_official_source=False,
            note=(
                "Bilinen hiçbir referans bölgeye yakın değil — güvenli tarafta "
                "kalan genel varsayılan PGA kullanıldı. Resmi TDTH verisiyle "
                "değiştirilmelidir."
            ),
        )

    def _ranked_zones(self, lat: float, lon: float) -> list[tuple[PGAZone, float]]:
        scored = [
            (zone, _haversine_km(lat, lon, zone.latitude, zone.longitude))
            for zone in self.zones
        ]
        scored.sort(key=lambda pair: pair[1])
        return scored

    def _nearest_zone(self, lat: float, lon: float) -> tuple[Optional[PGAZone], float]:
        """Geriye dönük uyumluluk için korunmuştur (tek-en-yakın-nokta)."""
        ranked = self._ranked_zones(lat, lon)
        if not ranked:
            return None, float("inf")
        return ranked[0]


@dataclass(frozen=True, slots=True)
class PGAEstimateResult:
    pga_g: float
    zone_name: Optional[str]
    is_official_source: bool
    note: str


@dataclass(frozen=True, slots=True)
class AttenuationPGAEstimate:
    """Büyüklük (Mw) + kaynağa mesafe (km) biliniyorsa gerçek bir azalım
    (attenuation) ilişkisiyle PGA kestirimi.

    Kullanılan ilişki: Esteva & Villaverde (1973), Latin Amerika/global
    verilerden türetilmiş, sismoloji ders kitaplarında (örn. Kramer,
    "Geotechnical Earthquake Engineering") yaygın alıntılanan basit bir
    nokta-kaynak ampirik azalım formülüdür:

        PGA [gal, cm/s^2] = 5600 * exp(0.8 * Mw) / (R + 40)^2

    burada R kaynağa (odak/fay) olan mesafedir (km). Bu formül GERÇEK bir
    büyüklük-mesafe-azalım fiziği içerir (yakın alanda doygunluk, uzak
    alanda geometrik yayılma+soğurma azalımı) — `DEFAULT_TURKEY_PGA_ZONES`
    gibi bir "tablodan okuma" değildir.

    DÜRÜST SINIRLAMA: bu ilişki TÜRKİYE'YE ÖZEL KALİBRE EDİLMEMİŞTİR
    (Türkiye'ye özgü zemin/kabuk özelliklerini, Kuzey Anadolu/Doğu Anadolu
    fay sisteminin kendine özgü hareket mekanizmalarını yansıtmaz) ve zemin
    sınıfı (Vs30) / fay mekanizması (doğrultu atımlı/ters/normal) gibi
    modern GMPE'lerde (örn. Boore-Atkinson, Akkar-Bommer, veya AFAD'ın PSHA
    modelinde kullanılan bölgesel ilişkiler) bulunan düzeltme terimlerini
    içermez. Yalnızca "hiçbir fiziksel model yok"tan daha iyi, kaba bir
    mertebe/büyüklük kestirimi olarak kullanılmalıdır; resmi bir sismik
    tehlike analizinin (PSHA) yerine geçmez.
    """

    magnitude_mw: float
    distance_km: float

    def estimate_g(self) -> "AttenuationEstimateResult":
        if self.distance_km < 0:
            raise ValueError("distance_km negatif olamaz.")
        pga_gal = 5600.0 * math.exp(0.8 * self.magnitude_mw) / (self.distance_km + 40.0) ** 2
        pga_g = pga_gal / 981.0  # 1 g = 981 gal (cm/s^2)
        return AttenuationEstimateResult(
            pga_g=pga_g,
            magnitude_mw=self.magnitude_mw,
            distance_km=self.distance_km,
            formula="Esteva & Villaverde (1973): PGA[gal] = 5600 * exp(0.8*Mw) / (R_km + 40)^2",
            note=(
                "Global/genel ampirik azalım ilişkisi — Türkiye'ye özel "
                "kalibre edilmemiştir, zemin sınıfı ve fay mekanizması "
                "düzeltmesi içermez. Resmi PSHA (AFAD/TDTH) yerine geçmez."
            ),
        )


@dataclass(frozen=True, slots=True)
class AttenuationEstimateResult:
    pga_g: float
    magnitude_mw: float
    distance_km: float
    formula: str
    note: str


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
