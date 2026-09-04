"""
Bina Enerji Kabuğu Denetimi (TS 825 tabanlı, GÖSTERGE niteliğinde)
====================================================================

Roadmap iş fikri #4: "Bina enerji kabuğu denetimi — TS 825 camlanma
oranı zaten `regulations`'ta var, ısı kaybı tahminine genişletilebilir."

`building_reconstruction.regulations` yalnızca pencere/duvar ORANI
eşiklerini (min_window_wall_ratio) tutuyordu; bu modül onun üzerine
TS 825 "Binalarda Isı Yalıtım Kuralları" standardının 4 derece-gün
bölgesine göre yaklaşık azami U-değeri eşiklerini ve toplam iletim ısı
kaybı katsayısını (H_t, W/K) ekler.

ÖNEMLİ - dayanak ve sınırlama (GÜNCELLENDİ — artık resmi kaynaklı):
    `ZONE_MAX_U_VALUES` artık TS 825 (Nisan 1998)'in EK 1-C "Bölgelere
    göre tavsiye edilen U değerleri" tablosundan BİREBİR alınmıştır
    (standardın tam metni doğrulandı: 1999/2002 tadil notları yalnızca
    EK 1-B formül sabitlerini ve EK 4 il listesini değiştirmiş, EK 1-C'yi
    değiştirmemiştir). `ZONE_DEGREE_DAYS` artık TS 825'in kendi resmi
    EK-2 aylık dış sıcaklık tablosundan hesaplanmıştır (bkz. tanım
    yerindeki not). Kalan dürüst sınır: standardın il/ilçe bazlı ayrımı
    (her il kendi bölgesine göre değil, EK-2'nin BÖLGE ortalaması
    kullanılıyor) ve tam Ek-A/Ek-B aylık ısı dengesi hesabı (bkz. aşağıki
    `MonthlyBalanceAuditor`) burada hâlâ basitleştirilmiştir — bu modül
    yine de resmi bir ısı yalıtım projesinin (TS 825 Ek A hesabı) yerini
    TUTMAZ.

EK-A AYLIK ISI DENGESİ YÖNTEMİ (`MonthlyBalanceAuditor`):
    Yukarıdaki `EnvelopeAuditor.audit()` yalnızca İLETİM kaybını
    (duvar/pencere/çatı/taban, H_t) hesaplar — kullanıcının doğru şekilde
    belirttiği gibi tam TS 825 Ek-A hesabı değildir. Bu modül artık EK
    olarak, TS 825 Ek-A'nın da dayandığı EN ISO 13790 / EN 832
    "aylık quasi-steady-state (yarı kararlı durum) denge yöntemi"nin
    gerçek formülasyonunu uygular:

        H_ısı_kaybı = H_t + H_v                          (iletim + HAVALANDIRMA)
        H_v = 0.34 * n_ach * V                            (0.34 = hava ısıl kapasitesi, Wh/m3K)
        Q_kayıp,ay = H_ısı_kaybı * (Ti - Te,ay) * 24 * gün_ay / 1000     [kWh]
        Q_kazanç,ay = Q_güneş,ay + Q_iç,ay
        η_ay = kullanım faktörü (kazanç/kayıp oranı γ'ya bağlı, EN ISO
               13790 Ek A formülü: γ≠1 için η=(1-γ^a)/(1-γ^(a+1)))
        Q_ısıtma,ay = max(0, Q_kayıp,ay - η_ay * Q_kazanç,ay)
        Q_yıllık = Σ_ay Q_ısıtma,ay

    Bu, önceki basit modelde EKSİK OLAN iki gerçek fiziksel bileşeni
    (havalandırma kaybı H_v ve güneş/iç kazançların kullanım-faktörü ile
    dengeye katılması) ekler — kullanıcının "sadece iletim kaybı, tam
    Ek-A hesabı değil" tespitine doğrudan cevaptır.

    DÜRÜST SINIRLAMA (kalan fark): TS 825 Ek-A'nın kendisi hâlâ tam
    olarak uygulanmış değildir — resmi Ek-A, aylık dış sıcaklık ve
    güneşlenme değerleri için TS 825'in EK-B/EK-C'sindeki İL BAZLI
    resmi iklim verisi tablolarını kullanır. Bu modül o tabloları
    YENİDEN ÜRETMEZ (yanlış/eski bir tablo üretmek, tablo olmamasından
    daha kötü olur); bunun yerine kullanıcıdan (veya
    `climate_data.open_meteo_client`'tan) GERÇEK aylık ortalama dış
    sıcaklık ve güneş radyasyonu verisini GİRDİ olarak ister. Formül
    gerçek TS 825/EN ISO 13790 formülüdür; iklim verisinin kaynağı ve
    doğruluğu kullanıcının sorumluluğundadır.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

#: RESMİ TS 825 (Nisan 1998, EK 1-C "Bölgelere göre tavsiye edilen U
#: değerleri") tablosu — BİREBİR alınmıştır (kaynak: TSE TS 825 tam metni,
#: mekaniktesisat.net/izoder üzerinden doğrulandı — 1999/2002 tadil
#: notları yalnızca EK 1-B formül sabitlerini ve EK 4 il listesini
#: değiştirmiş, EK 1-C U-değeri tablosunu DEĞİŞTİRMEMİŞTİR).
#: Birim: W/(m^2.K). UP (pencere) tüm bölgelerde sabit 2,80'dir (standart
#: metninde: "Up olarak verilen ısı iletim kat sayıları bir cam türü için
#: verilmiştir; diğer kapı/pencere türleri için TS 2164'ten alınır").
#: `duvar`=UD, `cati`=UT, `taban`=Ut.
ZONE_MAX_U_VALUES: dict[int, dict[str, float]] = {
    1: {"duvar": 0.80, "pencere": 2.80, "cati": 0.50, "taban": 0.80},
    2: {"duvar": 0.60, "pencere": 2.80, "cati": 0.40, "taban": 0.60},
    3: {"duvar": 0.50, "pencere": 2.80, "cati": 0.30, "taban": 0.45},
    4: {"duvar": 0.40, "pencere": 2.80, "cati": 0.25, "taban": 0.40},
}

#: Tipik (eski/yalıtımsız veya hafif yalıtımlı) mevcut bina U-değerleri —
#: kullanıcı gerçek değerleri bilmiyorsa varsayılan olarak kullanılır.
TYPICAL_EXISTING_U_VALUES: dict[str, float] = {
    "duvar": 1.50,
    "pencere": 2.80,
    "cati": 1.20,
    "taban": 1.00,
}

#: Bölge başına ısıtma derece-günü (HDD, taban 19°C — TS 825'in konutlar
#: için verdiği Ti değeri), TS 825'in KENDİ resmi EK-2 "Farklı Derece Gün
#: Bölgeleri İçin Aylık Ortalama Dış Sıcaklık Değerleri" tablosundan
#: HESAPLANMIŞTIR (12 ay × Td, Ti=19 tabanına göre Σ max(0,19-Td)×gün_ay;
#: standardın "KKO≥2,5 olan aylarda ısı kaybı yok" ilkesiyle tutarlı
#: olarak Td>19 olan aylar 0 katkı verir). Artık uydurma bir mertebe
#: değil, TS 825'in resmi iklim verisinden türetilmiş gerçek bir
#: derece-gün hesabıdır — yine de il bazlı değil, bölge temsilcisi tek
#: bir profildir (gerçek il-bazlı hesap için EK-2'nin il ayrımı gerekir).
ZONE_DEGREE_DAYS: dict[int, int] = {1: 1441, 2: 2374, 3: 3111, 4: 5758}

SOURCE_LABEL = (
    "TS 825 Binalarda Isı Yalıtım Kuralları (TSE, Nisan 1998 + 1999/2002 "
    "tadilleri) — U-değeri sınırları EK 1-C'den birebir, derece-günler "
    "EK-2'nin resmi aylık dış sıcaklık verisinden hesaplanmıştır; yine de "
    "tam Ek-A/il-bazlı hesabın yerini tutmaz (bkz. modül docstring'i)"
)


@dataclass(slots=True)
class EnvelopeComponentResult:
    name: str
    area_m2: float
    u_value_current: float
    u_value_limit: float
    meets_limit: bool
    heat_loss_w_per_k: float  # U * A


@dataclass(slots=True)
class EnvelopeAuditReport:
    climate_zone: int
    components: list[EnvelopeComponentResult]
    total_heat_loss_coefficient_w_per_k: float
    total_heat_loss_coefficient_limit_w_per_k: float
    is_compliant: bool
    excess_pct: float
    estimated_annual_heating_kwh: float
    estimated_annual_heating_kwh_if_compliant: float
    potential_savings_kwh: float
    potential_savings_pct: float
    source_label: str
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "climate_zone": self.climate_zone,
            "components": [
                {
                    "name": c.name,
                    "area_m2": round(c.area_m2, 1),
                    "u_value_current": c.u_value_current,
                    "u_value_limit": c.u_value_limit,
                    "meets_limit": c.meets_limit,
                    "heat_loss_w_per_k": round(c.heat_loss_w_per_k, 1),
                }
                for c in self.components
            ],
            "total_heat_loss_coefficient_w_per_k": round(
                self.total_heat_loss_coefficient_w_per_k, 1
            ),
            "total_heat_loss_coefficient_limit_w_per_k": round(
                self.total_heat_loss_coefficient_limit_w_per_k, 1
            ),
            "is_compliant": self.is_compliant,
            "excess_pct": round(self.excess_pct, 1),
            "estimated_annual_heating_kwh": round(self.estimated_annual_heating_kwh, 0),
            "estimated_annual_heating_kwh_if_compliant": round(
                self.estimated_annual_heating_kwh_if_compliant, 0
            ),
            "potential_savings_kwh": round(self.potential_savings_kwh, 0),
            "potential_savings_pct": round(self.potential_savings_pct, 1),
            "source_label": self.source_label,
            "disclaimer": self.disclaimer,
        }


class EnvelopeAuditor:
    """Facade/footprint alanlarından (mevcut `FacadeGenerator.check_compliance`
    çıktısı + footprint alanı) TS 825 tabanlı bir ısı kaybı ön-denetimi
    üretir."""

    @staticmethod
    def audit(
        *,
        climate_zone: int,
        wall_area_m2: float,
        window_area_m2: float,
        roof_area_m2: float,
        floor_area_m2: float,
        u_wall: float | None = None,
        u_window: float | None = None,
        u_roof: float | None = None,
        u_floor: float | None = None,
        indoor_outdoor_delta_c: float = 20.0,
    ) -> EnvelopeAuditReport:
        if climate_zone not in ZONE_MAX_U_VALUES:
            raise ValueError(f"Geçersiz derece-gün bölgesi: {climate_zone} (1-4 arası olmalı).")
        limits = ZONE_MAX_U_VALUES[climate_zone]
        net_wall_area = max(0.0, wall_area_m2 - window_area_m2)

        current = {
            "duvar": u_wall if u_wall is not None else TYPICAL_EXISTING_U_VALUES["duvar"],
            "pencere": u_window if u_window is not None else TYPICAL_EXISTING_U_VALUES["pencere"],
            "cati": u_roof if u_roof is not None else TYPICAL_EXISTING_U_VALUES["cati"],
            "taban": u_floor if u_floor is not None else TYPICAL_EXISTING_U_VALUES["taban"],
        }
        areas = {
            "duvar": net_wall_area,
            "pencere": window_area_m2,
            "cati": roof_area_m2,
            "taban": floor_area_m2,
        }
        labels = {
            "duvar": "Dış duvar",
            "pencere": "Pencere",
            "cati": "Çatı/tavan",
            "taban": "Taban/döşeme",
        }

        components: list[EnvelopeComponentResult] = []
        total_h = 0.0
        total_h_limit = 0.0
        for comp_key in ("duvar", "pencere", "cati", "taban"):
            area = areas[comp_key]
            u_cur = current[comp_key]
            u_lim = limits[comp_key]
            h = u_cur * area
            h_lim = u_lim * area
            total_h += h
            total_h_limit += h_lim
            components.append(
                EnvelopeComponentResult(
                    name=labels[comp_key],
                    area_m2=area,
                    u_value_current=u_cur,
                    u_value_limit=u_lim,
                    meets_limit=u_cur <= u_lim + 1e-9,
                    heat_loss_w_per_k=h,
                )
            )

        is_compliant = all(c.meets_limit for c in components)
        excess_pct = 100.0 * (total_h - total_h_limit) / max(total_h_limit, 1e-6)

        hdd = ZONE_DEGREE_DAYS[climate_zone]
        # Kaba yıllık ısıtma enerjisi tahmini (yalnızca iletim kaybı
        # bileşeni; havalandırma/iç kazanç/güneş kazancı dahil değil —
        # bu yüzden gerçek TS 825 Q hesabından farklıdır, sadece
        # zarf-kalitesi karşılaştırması için kullanılır):
        #   Q [kWh/yıl] = H_t [W/K] * HDD [K.gün] * 24 [saat/gün] / 1000
        annual_kwh_current = total_h * hdd * 24.0 / 1000.0
        annual_kwh_if_compliant = total_h_limit * hdd * 24.0 / 1000.0
        savings_kwh = max(0.0, annual_kwh_current - annual_kwh_if_compliant)
        savings_pct = 100.0 * savings_kwh / max(annual_kwh_current, 1e-6)

        return EnvelopeAuditReport(
            climate_zone=climate_zone,
            components=components,
            total_heat_loss_coefficient_w_per_k=total_h,
            total_heat_loss_coefficient_limit_w_per_k=total_h_limit,
            is_compliant=is_compliant,
            excess_pct=excess_pct,
            estimated_annual_heating_kwh=annual_kwh_current,
            estimated_annual_heating_kwh_if_compliant=annual_kwh_if_compliant,
            potential_savings_kwh=savings_kwh,
            potential_savings_pct=savings_pct,
            source_label=SOURCE_LABEL,
            disclaimer=(
                "Bu, yalnızca iletim ısı kaybını (duvar/pencere/çatı/taban) dikkate alan "
                "BASİTLEŞTİRİLMİŞ bir ön-değerlendirmedir. Havalandırma kaybı, güneş/iç "
                "kazançları ve TS 825 Ek-A'nın tam hesap yöntemi dahil değildir (bunlar için "
                "aşağıdaki MonthlyBalanceAuditor kullanılabilir). U-değeri eşikleri TS 825 "
                "EK 1-C'den resmi/birebir alınmıştır; il-bazlı değil BÖLGE ortalaması "
                "kullanılır — resmi ısı yalıtım projesi yerine geçmez."
            ),
        )


#: Havalandırma hacmi ısıl kapasitesi katsayısı — ρ_hava * c_p_hava / 3600
#: = 1.2 [kg/m3] * 1008 [J/kgK] / 3600 [s/saat] ≈ 0.34 Wh/(m3.K).
#: Bu TS 825/EN ISO 13790'ın standart, evrensel fiziksel sabitidir
#: (havanın ısıl özelliklerinden türetilir, ampirik bir tablo değeri
#: DEĞİLDİR — bu yüzden burada "temsili" ibaresi yoktur).
VENTILATION_HEAT_CAPACITY_COEFFICIENT = 0.34  # Wh/(m^3.K)

#: Tipik doğal havalandırma/sızıntı hava değişim sayısı (n, 1/saat) —
#: TS 825 konut binaları için asgari n=0.8 (mekanik havalandırmasız,
#: pencere sızıntısına dayalı) değerini yaygın referans olarak kullanır.
#: Kullanıcı gerçek blower-door/tasarım değerini biliyorsa `ach` ile
#: override edilmelidir.
DEFAULT_AIR_CHANGES_PER_HOUR = 0.8

#: EN ISO 13790 Ek A'daki kullanım-faktörü (utilization factor) formülü
#: için sayısal parametre "a" — binanın ısıl zaman sabitine (τ) bağlıdır:
#: a = a_H + τ/τ_H. Burada TAŞINABİLİR bir "orta ağırlıklı yapı" temsili
#: değeri kullanılır (a_H≈1, τ_H≈15 saat referans değerleriyle tipik
#: konut için a≈2.5-3); GERÇEK değer binanın ısıl kütlesine (ağır/hafif
#: yapı) bağlı olarak değişir ve tam hesap için TS 825/ISO 13790'ın
#: kendi tablosundan alınmalıdır.
DEFAULT_UTILIZATION_FACTOR_PARAMETER_A = 2.5


@dataclass(slots=True)
class MonthlyBalanceResult:
    month: int
    mean_external_temp_c: float
    heating_degree_hours: float
    transmission_loss_kwh: float
    ventilation_loss_kwh: float
    total_loss_kwh: float
    solar_gain_kwh: float
    internal_gain_kwh: float
    total_gain_kwh: float
    gain_loss_ratio: float
    utilization_factor: float
    heating_demand_kwh: float


@dataclass(slots=True)
class MonthlyBalanceReport:
    months: list[MonthlyBalanceResult]
    annual_heating_demand_kwh: float
    total_heat_loss_coefficient_w_per_k: float
    ventilation_heat_loss_coefficient_w_per_k: float
    transmission_heat_loss_coefficient_w_per_k: float
    source_label: str
    disclaimer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "months": [
                {
                    "month": m.month,
                    "mean_external_temp_c": round(m.mean_external_temp_c, 1),
                    "total_loss_kwh": round(m.total_loss_kwh, 1),
                    "total_gain_kwh": round(m.total_gain_kwh, 1),
                    "utilization_factor": round(m.utilization_factor, 3),
                    "heating_demand_kwh": round(m.heating_demand_kwh, 1),
                }
                for m in self.months
            ],
            "annual_heating_demand_kwh": round(self.annual_heating_demand_kwh, 0),
            "transmission_heat_loss_coefficient_w_per_k": round(
                self.transmission_heat_loss_coefficient_w_per_k, 1
            ),
            "ventilation_heat_loss_coefficient_w_per_k": round(
                self.ventilation_heat_loss_coefficient_w_per_k, 1
            ),
            "source_label": self.source_label,
            "disclaimer": self.disclaimer,
        }


class MonthlyBalanceAuditor:
    """TS 825 Ek-A'nın da dayandığı EN ISO 13790 / EN 832 aylık
    quasi-steady-state ısı denge yöntemini uygular (bkz. modül
    docstring'i): iletim + havalandırma kaybı, güneş + iç kazanç,
    kullanım faktörüyle dengelenmiş aylık ısıtma enerjisi ihtiyacı.

    `EnvelopeAuditor.audit()`ten farkı: burada havalandırma kaybı VE
    kazançlar (bir önceki modelde YOK) dahil edilir — ama bunun
    karşılığında kullanıcıdan gerçek aylık iklim verisi (dış sıcaklık,
    güneş radyasyonu) girdisi istenir; sentetik/uydurma iklim verisi
    ÜRETİLMEZ.
    """

    DAYS_IN_MONTH = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)

    @staticmethod
    def _utilization_factor(gain_loss_ratio: float, a: float) -> float:
        gamma = gain_loss_ratio
        if gamma <= 0:
            return 1.0
        if abs(gamma - 1.0) < 1e-6:
            # EN ISO 13790 Ek A: gamma -> 1 tekil noktası, limit değeri a/(a+1).
            return a / (a + 1.0)
        return (1.0 - gamma**a) / (1.0 - gamma ** (a + 1.0))

    @classmethod
    def audit(
        cls,
        *,
        total_heat_transfer_coefficient_w_per_k: float,
        building_volume_m3: float,
        indoor_temp_c: float,
        monthly_mean_external_temp_c: Sequence[float],
        monthly_solar_gain_kwh: Sequence[float],
        monthly_internal_gain_kwh: Sequence[float],
        air_changes_per_hour: float = DEFAULT_AIR_CHANGES_PER_HOUR,
        utilization_parameter_a: float = DEFAULT_UTILIZATION_FACTOR_PARAMETER_A,
    ) -> MonthlyBalanceReport:
        if len(monthly_mean_external_temp_c) != 12:
            raise ValueError(
                "monthly_mean_external_temp_c tam olarak 12 ay (Ocak..Aralık) içermelidir."
            )
        if len(monthly_solar_gain_kwh) != 12 or len(monthly_internal_gain_kwh) != 12:
            raise ValueError("Aylık kazanç dizileri tam olarak 12 ay içermelidir.")

        h_v = VENTILATION_HEAT_CAPACITY_COEFFICIENT * air_changes_per_hour * building_volume_m3
        h_t = total_heat_transfer_coefficient_w_per_k
        h_total = h_t + h_v

        results: list[MonthlyBalanceResult] = []
        annual_demand = 0.0
        for i in range(12):
            days = cls.DAYS_IN_MONTH[i]
            te = monthly_mean_external_temp_c[i]
            delta_t = max(0.0, indoor_temp_c - te)
            hdh = delta_t * 24.0 * days  # ısıtma derece-saat
            transmission_loss = h_t * hdh / 1000.0
            ventilation_loss = h_v * hdh / 1000.0
            total_loss = transmission_loss + ventilation_loss

            solar = max(0.0, monthly_solar_gain_kwh[i])
            internal = max(0.0, monthly_internal_gain_kwh[i])
            total_gain = solar + internal

            ratio = total_gain / total_loss if total_loss > 1e-9 else 0.0
            eta = cls._utilization_factor(ratio, utilization_parameter_a)
            demand = max(0.0, total_loss - eta * total_gain)
            annual_demand += demand

            results.append(
                MonthlyBalanceResult(
                    month=i + 1,
                    mean_external_temp_c=te,
                    heating_degree_hours=hdh,
                    transmission_loss_kwh=transmission_loss,
                    ventilation_loss_kwh=ventilation_loss,
                    total_loss_kwh=total_loss,
                    solar_gain_kwh=solar,
                    internal_gain_kwh=internal,
                    total_gain_kwh=total_gain,
                    gain_loss_ratio=ratio,
                    utilization_factor=eta,
                    heating_demand_kwh=demand,
                )
            )

        return MonthlyBalanceReport(
            months=results,
            annual_heating_demand_kwh=annual_demand,
            total_heat_loss_coefficient_w_per_k=h_total,
            ventilation_heat_loss_coefficient_w_per_k=h_v,
            transmission_heat_loss_coefficient_w_per_k=h_t,
            source_label=(
                "EN ISO 13790 / EN 832 aylık quasi-steady-state yöntemi "
                "(TS 825 Ek-A'nın dayandığı yöntem) — gerçek formülasyon, "
                "kullanıcı-sağlanan iklim verisiyle"
            ),
            disclaimer=(
                "İletim VE havalandırma kaybı, güneş/iç kazanç ve kullanım "
                "faktörü dahil edilmiştir (EN ISO 13790 Ek-A formülü). Ancak "
                "aylık dış sıcaklık/güneşlenme verisi TS 825'in resmi il-bazlı "
                "iklim tablolarından değil, kullanıcı girdisinden gelir — bu "
                "verinin doğruluğu ve kaynağı kullanıcı sorumluluğundadır. "
                "Isıl köprüler (thermal bridge), nem/yoğuşma analizi ve "
                "mekanik havalandırma geri kazanımı gibi Ek-A'nın diğer "
                "bileşenleri hâlâ dahil değildir."
            ),
        )


__all__ = [
    "ZONE_MAX_U_VALUES",
    "TYPICAL_EXISTING_U_VALUES",
    "ZONE_DEGREE_DAYS",
    "EnvelopeComponentResult",
    "EnvelopeAuditReport",
    "EnvelopeAuditor",
    "MonthlyBalanceResult",
    "MonthlyBalanceReport",
    "MonthlyBalanceAuditor",
    "VENTILATION_HEAT_CAPACITY_COEFFICIENT",
    "DEFAULT_AIR_CHANGES_PER_HOUR",
    "DEFAULT_UTILIZATION_FACTOR_PARAMETER_A",
]
