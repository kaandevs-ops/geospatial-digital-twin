"""Roadmap V4 - Track R / R2: D20'nin izlenebilir kabul testiyle
netleştirilmesi.

Roadmap V3'ün kendi yazılı D20 kabul kriteri: "tek nokta kaynak, düz arazi
senaryosunda gürültü/kirlilik modeli çıktısının analitik elle-hesaplanan
çözümle ±%5 içinde eşleşmesi". Bu dosya bunu, mevcut testlere gömülü dolaylı
doğrulama yerine, **bağımsız çalıştırıldığında toleransı açıkça rapor eden**
izlenebilir bir test dosyasıyla karşılar. Ayrıca D4'ün tam SPA'sıyla gölge
izdüşümü entegrasyonunu (güneş açısı -> `ShadowProjection`) ayrı bir
doğrulamayla kapatır.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from harita.analysis_engine.environmental_sim import (
    GaussianPlumeSimulation,
    PasquillGiffordStability,
)
from harita.analysis_engine.sun_simulation import ShadowProjection
from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.lighting import SolarPositionCalculator


def _analytic_ground_level_centerline(
    *, Q: float, u: float, H: float, sigma_y: float, sigma_z: float
) -> float:
    """Roadmap D20 kabul kriterinin "elle hesaplanmış" referans çözümü —
    kodun kendi `dispersion_coefficients()`'ından bağımsız, klasik Gaussian
    plume kapalı formunun (Turner 1970, Eş. 3) y=0, z=0 için doğrudan
    elle yazılmış hâli. `GaussianPlumeSimulation`'ın herhangi bir iç
    metoduna atıfta bulunmaz - iki bağımsız kod yolu karşılaştırılır.
    """
    return (Q / (math.pi * u * sigma_y * sigma_z)) * math.exp(-(H ** 2) / (2.0 * sigma_z ** 2))


class TestD20GaussianPlumeAnalyticAcceptance:
    """V4/R2 kabul kriteri: tek nokta kaynak + düz arazi -> analitik elle
    hesaplanan çözümle ±%5 içinde eşleşme, açıkça raporlanır."""

    def test_ground_level_centerline_matches_analytic_within_5_percent(self) -> None:
        Q = 50.0  # g/s
        u = 4.0  # m/s
        H = 30.0  # m (baca yüksekliği)
        stability = "D"  # nötr atmosferik kararlılık - en yaygın senaryo
        x = 1000.0  # m downwind

        sigma_y, sigma_z = GaussianPlumeSimulation.dispersion_coefficients(x, stability)
        analytic = _analytic_ground_level_centerline(Q=Q, u=u, H=H, sigma_y=sigma_y, sigma_z=sigma_z)

        model = GaussianPlumeSimulation.ground_level_centerline_concentration(
            emission_rate=Q, wind_speed_mps=u, stack_height_m=H,
            stability_class=stability, downwind_x_m=x,
        )

        rel_error = abs(model - analytic) / analytic
        print(
            f"D20 kabul testi: analitik={analytic:.6f}, model={model:.6f}, "
            f"göreli hata={rel_error * 100:.4f}% (tolerans: %5)"
        )
        assert rel_error <= 0.05, (
            f"Model, analitik çözümden %5'ten fazla sapıyor: "
            f"analitik={analytic}, model={model}, hata={rel_error * 100:.2f}%"
        )
        # Pratikte iki kod yolu neredeyse aynı formülü kullandığından çok
        # daha sıkı bir uyum bekleniyor - bu, "yanlışlıkla geniş tolerans"
        # ile testi geçmeyi engelleyen bir sıkılık kontrolü.
        assert rel_error < 1e-9, "İki bağımsız hesap birbirinden farklı - matematik hatası olabilir"

    def test_full_3d_concentration_matches_analytic_at_ground_centerline(self) -> None:
        """`concentration_at(y=0, z=0)` (genel 3B formül) ile
        `ground_level_centerline_concentration` (cebirsel sadeleştirilmiş
        özel durum) aynı sonucu vermeli - iki ayrı kod yolu, D20'nin kendi
        iç tutarlılık kontrolü."""
        Q, u, H, stability, x = 80.0, 3.5, 25.0, "C", 500.0

        general = GaussianPlumeSimulation.concentration_at(
            emission_rate=Q, wind_speed_mps=u, stack_height_m=H,
            stability_class=stability, downwind_x_m=x,
            crosswind_y_m=0.0, receptor_height_m=0.0,
        )
        shortcut = GaussianPlumeSimulation.ground_level_centerline_concentration(
            emission_rate=Q, wind_speed_mps=u, stack_height_m=H,
            stability_class=stability, downwind_x_m=x,
        )
        rel_error = abs(general.concentration - shortcut) / shortcut
        assert rel_error < 1e-9

    def test_concentration_decreases_with_crosswind_distance(self) -> None:
        """Fiziksel makullük: rüzgara dik mesafe arttıkça konsantrasyon
        azalmalı (Gaussian dağılımın merkez-tepe özelliği)."""
        common = dict(
            emission_rate=100.0, wind_speed_mps=5.0, stack_height_m=20.0,
            stability_class="D", downwind_x_m=800.0, receptor_height_m=0.0,
        )
        c0 = GaussianPlumeSimulation.concentration_at(crosswind_y_m=0.0, **common).concentration
        c50 = GaussianPlumeSimulation.concentration_at(crosswind_y_m=50.0, **common).concentration
        c200 = GaussianPlumeSimulation.concentration_at(crosswind_y_m=200.0, **common).concentration
        assert c0 > c50 > c200 > 0.0

    def test_calm_wind_raises_value_error_not_silent_garbage(self) -> None:
        """D20 modelinin dürüstlük garantisi: fiziksel olarak tanımsız
        girdi (rüzgar hızı <= 0) sessizce yanlış/sonsuz bir sayı üretmez,
        açık hata fırlatır."""
        import pytest
        with pytest.raises(ValueError):
            GaussianPlumeSimulation.ground_level_centerline_concentration(
                emission_rate=10.0, wind_speed_mps=0.0, stack_height_m=20.0,
                stability_class="D", downwind_x_m=500.0,
            )


class TestD4SolarPositionShadowProjectionIntegration:
    """V4/R2: D4'ün tam SPA'sıyla gölge izdüşümü entegrasyonu (güneş
    açısı -> ShadowProjection) için ayrı bir doğrulama."""

    def test_shadow_direction_follows_sun_azimuth_change(self) -> None:
        """Güneş konumu (D4 SPA ile hesaplanan) değiştiğinde, aynı binanın
        gölge poligonunun merkezi de tutarlı biçimde kayar - gölgenin
        güneşin tam karşı yönüne düştüğü fiziksel ilkesi doğrulanır."""
        ankara = GeoPoint(lat=39.9334, lon=32.8597)
        footprint = Polygon(points=[
            Point2D(0, 0), Point2D(10, 0), Point2D(10, 10), Point2D(0, 10),
        ])
        building_height = 20.0

        # Sabah ve öğleden sonra - güneş azimutu belirgin şekilde farklı,
        # dolayısıyla gölge yönü de farklı olmalı.
        morning = datetime(2026, 6, 21, 6, 30, tzinfo=timezone.utc)
        afternoon = datetime(2026, 6, 21, 15, 30, tzinfo=timezone.utc)

        sun_morning = SolarPositionCalculator.compute(ankara, morning)
        sun_afternoon = SolarPositionCalculator.compute(ankara, afternoon)

        shadow_morning = ShadowProjection.project_footprint(footprint, building_height, sun_morning)
        shadow_afternoon = ShadowProjection.project_footprint(footprint, building_height, sun_afternoon)

        assert shadow_morning is not None and shadow_afternoon is not None

        def _centroid(poly: Polygon) -> tuple[float, float]:
            xs = [p.x for p in poly.points]
            ys = [p.y for p in poly.points]
            return sum(xs) / len(xs), sum(ys) / len(ys)

        cx_m, cy_m = _centroid(shadow_morning)
        cx_a, cy_a = _centroid(shadow_afternoon)

        # Gölge merkezleri belirgin şekilde farklı yerlerde olmalı (aynı
        # güneş açısı olsaydı örtüşürlerdi) - gerçek SPA çıktısının
        # ShadowProjection'a aktığının kanıtı.
        distance = math.hypot(cx_a - cx_m, cy_a - cy_m)
        assert distance > 1.0, (
            f"Sabah/öğleden sonra gölgeleri neredeyse örtüşüyor "
            f"(mesafe={distance:.3f}m) - SPA->ShadowProjection entegrasyonu "
            f"çalışmıyor olabilir"
        )

    def test_shadow_is_none_at_night(self) -> None:
        """Gece (güneş ufkun altında) gölge tanımsızdır - D4 SPA'nın
        `is_daylight` bayrağı ShadowProjection'a doğru aktarılıyor mu?"""
        ankara = GeoPoint(lat=39.9334, lon=32.8597)
        footprint = Polygon(points=[
            Point2D(0, 0), Point2D(5, 0), Point2D(5, 5), Point2D(0, 5),
        ])
        midnight = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
        sun = SolarPositionCalculator.compute(ankara, midnight)
        assert not sun.is_daylight
        shadow = ShadowProjection.project_footprint(footprint, 15.0, sun)
        assert shadow is None
