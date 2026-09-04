"""
mobility.weather_traffic_effect - Hava Durumu -> IDM Etkisi (Katman 3.3 madde 4)
====================================================================================

Roadmap V9 / Faz VI / Katman 3.3 madde 4:

"Hava durumu etkisi: Katman 5'ten gelen yağış/görüş mesafesi verisi IDM
parametrelerini (tepki mesafesi, hız) hafifçe değiştirir."

**Dürüstlük notu (bu oturumda giderilen bir eksik):** Önceki oturumlara
kadar `climate_data.open_meteo_client.HourlyClimateSample` yalnızca
sıcaklık/bulutluluk/güneşlenme alanları taşıyordu - yağış/görüş mesafesi
**hiç çekilmiyordu**. Bu oturumda `open_meteo_client.py`'ye (geriye uyumlu,
opsiyonel) `precipitation_mm`/`visibility_m` alanları ve
`TRAFFIC_WEATHER_HOURLY_VARS` eklendi (bkz. o modülün değişikliği).

Bu modül `mobility.traffic_simulation.IDMParams`'ı **değiştirmez**
(roadmap ilkesi #2) - `HourlyClimateSample`'dan yeni, hava-koşullu bir
`IDMParams` **kopyası** üretir. Gösterge disiplini: katsayılar literatürün
kaba eğilimlerinin (yağmurda fren mesafesi/tepki süresi artışı) basit bir
yaklaşımıdır, kalibre edilmiş bir model değildir.
"""

from __future__ import annotations

from dataclasses import replace

from ..climate_data.open_meteo_client import HourlyClimateSample
from .traffic_simulation import IDMParams


def weather_severity(sample: HourlyClimateSample) -> float:
    """Tek bir 0.0 (etkisiz/veri yok) - 1.0 (şiddetli: yoğun yağış +
    çok düşük görüş) şiddet skoru. Roadmap'in "hafifçe değiştirir"
    notuyla tutarlı - `apply_weather_effect` bu skoru küçük çarpanlara
    dönüştürür, aşırı iddialı bir "trafik çöktü" modeli üretmez.

    Veri eksikse (precipitation_mm/visibility_m `None`) o bileşen skora
    katkı yapmaz - sessizce "hava iyi" varsayılmaz, yalnızca o bileşenin
    etkisi devre dışı kalır (kısmi veri kısmi etki üretir, tam veri yoksa
    skor 0.0'dır ve `apply_weather_effect` orijinal parametreleri
    değiştirmeden döner).
    """
    score = 0.0
    contributions = 0

    if sample.precipitation_mm is not None:
        # >=10mm/saat "şiddetli yağış" kabul edilir (kaba eşik, gösterge).
        score += min(1.0, max(0.0, sample.precipitation_mm) / 10.0)
        contributions += 1

    if sample.visibility_m is not None:
        # <1000m görüş "düşük görüş" kabul edilir (kaba eşik, gösterge).
        visibility_penalty = max(0.0, 1.0 - min(1.0, sample.visibility_m / 1000.0))
        score += visibility_penalty
        contributions += 1

    if contributions == 0:
        return 0.0
    return min(1.0, score / contributions)


def apply_weather_effect(
    params: IDMParams,
    sample: HourlyClimateSample,
    *,
    max_speed_reduction: float = 0.25,
    max_headway_increase: float = 0.5,
) -> IDMParams:
    """`params`'ın hava-koşullu bir kopyasını döner (orijinal
    değiştirilmez). `severity=0.0` ise (veri yok veya hava iyi) `params`
    birebir aynı değerlerle döner - hiçbir sessiz varsayılan uygulanmaz.

    - `desired_speed` düşer (ıslak/kaygan zeminde konforlu seyir hızı azalır).
    - `safe_time_headway` (tepki mesafesi/süresi) artar (fren mesafesi
      literatüründeki genel eğilimle tutarlı, kaba bir yaklaşım).
    """
    severity = weather_severity(sample)
    if severity <= 0.0:
        return params

    speed_factor = 1.0 - (max_speed_reduction * severity)
    headway_factor = 1.0 + (max_headway_increase * severity)

    return replace(
        params,
        desired_speed=params.desired_speed * speed_factor,
        safe_time_headway=params.safe_time_headway * headway_factor,
        comfortable_braking=params.comfortable_braking * (1.0 - 0.15 * severity),
    )


def apply_weather_effect_to_all(
    defaults: dict,
    sample: HourlyClimateSample,
    **kwargs,
) -> dict:
    """`VEHICLE_IDM_DEFAULTS` tarzı bir `{VehicleType: IDMParams}`
    sözlüğünün tamamına hava etkisini uygular - yeni bir sözlük döner,
    girdi değiştirilmez."""
    return {
        vehicle_type: apply_weather_effect(params, sample, **kwargs)
        for vehicle_type, params in defaults.items()
    }
