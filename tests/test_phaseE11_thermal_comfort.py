"""
Roadmap V4 - Faz E11: Analysis Engine - Gerçek CFD-Lite Rüzgar ve Termal
Konfor Endeksi
====================================================================

Kapanmadan önceki durum (denetim maddesi): D3 (`WindSimulation`) ve D20
(`GaussianPlumeSimulation`) ile açık-hava çevresel simülasyonlar
güçlendirildi ama `RoofIrradiance` (D4 SPA tabanlı ışınım), `WindSimulation`
(D3 wake-etkili rüzgar) ve `ShadowAnalysis` (gerçek gölge testi) hiçbir
yerde tek bir termal konfor endeksinde birleştirilmiyordu.

Bu test dosyası roadmap'in kendi kabul kriterini doğrular: "Bilinen bir
referans senaryoda (gölgeli+rüzgarsız vs. güneşli+rüzgarlı bir nokta)
endeks beklenen yönde (daha konforlu/daha az konforlu) farklılaşır" -
ayrıca D3 `WindSimulation.simulate()`'in wake-etkili gerçek rüzgar
alanından `ThermalComfort.evaluate_with_wind_field()` ile doğrudan
örnekleme yapan uçtan uca köprüyü de kapsar.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.analysis_engine.environmental_sim import (
    ThermalComfort,
    ThermalComfortResult,
    WindSimulation,
)
from harita.core_engine.coordinate_systems import GeoPoint
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder


def _box_mesh(size=10.0, height=6.0, base_z=0.0, cx=0.0, cy=0.0):
    poly = Polygon(
        points=[
            Point2D(cx - size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy - size / 2),
            Point2D(cx + size / 2, cy + size / 2),
            Point2D(cx - size / 2, cy + size / 2),
        ]
    )
    return MeshBuilder.extrude_polygon(poly, base_z=base_z, height=height)


ISTANBUL = GeoPoint(lat=41.0082, lon=28.9784)
# 21 Haziran öğlen (UTC+0 - yaz gündönümü, güneş yüksekte) - kesin gündüz.
JUNE_NOON = datetime(2024, 6, 21, 11, 0, 0, tzinfo=timezone.utc)
# Gece yarısı - kesin gece.
MIDNIGHT = datetime(2024, 6, 21, 23, 0, 0, tzinfo=timezone.utc)


# ======================================================================== #
# Tmrt / apparent_temperature çekirdek fizik doğrulaması
# ======================================================================== #


def test_mean_radiant_temperature_equals_air_temp_without_irradiance():
    tmrt = ThermalComfort.mean_radiant_temperature(air_temp_c=20.0, irradiance_watts_per_m2=0.0)
    assert math.isclose(tmrt, 20.0, abs_tol=1e-9)


def test_mean_radiant_temperature_increases_with_irradiance():
    tmrt_low = ThermalComfort.mean_radiant_temperature(20.0, 100.0)
    tmrt_high = ThermalComfort.mean_radiant_temperature(20.0, 800.0)
    assert tmrt_high > tmrt_low > 20.0


def test_apparent_temperature_matches_air_temp_when_no_radiant_gain_and_no_wind():
    apparent = ThermalComfort.apparent_temperature(
        air_temp_c=18.0,
        mean_radiant_temp_c=18.0,
        wind_speed_mps=0.0,
    )
    assert math.isclose(apparent, 18.0, abs_tol=1e-9)


def test_apparent_temperature_wind_reduces_radiant_gain():
    calm = ThermalComfort.apparent_temperature(20.0, 40.0, wind_speed_mps=0.0)
    windy = ThermalComfort.apparent_temperature(20.0, 40.0, wind_speed_mps=8.0)
    assert windy < calm


# ======================================================================== #
# Kabul kriteri: gölgeli+rüzgarsız vs güneşli+rüzgarlı farklılaşma
# ======================================================================== #


def test_shadowed_calm_point_is_more_comfortable_than_sunny_windy_hot_point():
    """Roadmap'in kendi kabul kriteri: gölgede + rüzgarsız bir nokta,
    tam güneşte + (yüksek Tmrt senaryosunda) rüzgarlı bir noktadan
    FARKLI ısı-stresi kategorisinde/apparent-temperature'da olmalı -
    burada güneşteki nokta belirgin biçimde daha sıcak/stresli çıkmalı
    (yüksek yaz güneşinin radyatif etkisi, düşük rüzgar hızında bile
    rüzgarın sönüm katkısını aşar)."""
    occluder = _box_mesh(size=20.0, height=15.0, cx=0.0, cy=0.0)

    # Gölgedeki nokta: 21 Haziran 11:00 UTC, İstanbul için gerçek güneş
    # azimut/yükseklik açısıyla (SolarPositionCalculator) hesaplanmış,
    # binanın gölge düşürdüğü taraftaki bir nokta (bina kenarı y=10,
    # gölge uzunluğu ~= height/tan(elevation) ile bu bina için ~5.7 m -
    # y=12 gölge alanı içinde; ampirik olarak `ShadowAnalysis.evaluate`
    # ile doğrulanmıştır, bkz. bu dosyanın geliştirme notları).
    shadow_point = (0.0, 12.0, 1.5)
    # Güneşteki nokta: bina etkisinden tamamen uzak, açık alanda.
    sunny_point = (500.0, 500.0, 1.5)

    shadow_result = ThermalComfort.evaluate(
        point=shadow_point,
        location=ISTANBUL,
        when_utc=JUNE_NOON,
        occluders=[occluder],
        air_temp_c=30.0,
        wind_speed_mps=0.5,
    )
    sunny_result = ThermalComfort.evaluate(
        point=sunny_point,
        location=ISTANBUL,
        when_utc=JUNE_NOON,
        occluders=[occluder],
        air_temp_c=30.0,
        wind_speed_mps=0.5,
    )

    assert shadow_result.in_shadow is True
    assert sunny_result.in_shadow is False
    assert shadow_result.irradiance_watts_per_m2 == 0.0
    assert sunny_result.irradiance_watts_per_m2 > 0.0
    # Beklenen yön: gölgeli nokta daha SERİN / daha az ısı-stresi.
    assert shadow_result.apparent_temperature_c < sunny_result.apparent_temperature_c
    assert shadow_result.mean_radiant_temp_c < sunny_result.mean_radiant_temp_c


def test_night_time_has_no_irradiance_regardless_of_shadow():
    result = ThermalComfort.evaluate(
        point=(500.0, 500.0, 1.5),
        location=ISTANBUL,
        when_utc=MIDNIGHT,
        occluders=[],
        air_temp_c=15.0,
        wind_speed_mps=1.0,
    )
    assert result.irradiance_watts_per_m2 == 0.0
    assert math.isclose(result.mean_radiant_temp_c, result.air_temp_c, abs_tol=1e-6)


def test_category_labels_are_consistent_with_apparent_temperature_ordering():
    cold = ThermalComfort.evaluate(
        point=(500.0, 500.0, 1.5),
        location=ISTANBUL,
        when_utc=MIDNIGHT,
        occluders=[],
        air_temp_c=-10.0,
        wind_speed_mps=5.0,
    )
    comfortable = ThermalComfort.evaluate(
        point=(500.0, 500.0, 1.5),
        location=ISTANBUL,
        when_utc=MIDNIGHT,
        occluders=[],
        air_temp_c=20.0,
        wind_speed_mps=0.0,
    )
    hot = ThermalComfort.evaluate(
        point=(500.0, 500.0, 1.5),
        location=ISTANBUL,
        when_utc=JUNE_NOON,
        occluders=[],
        air_temp_c=40.0,
        wind_speed_mps=0.0,
    )
    assert (
        cold.apparent_temperature_c
        < comfortable.apparent_temperature_c
        < hot.apparent_temperature_c
    )
    assert "soğuk" in cold.category
    assert comfortable.category == "termal konfor"
    assert "sıcak" in hot.category


# ======================================================================== #
# D3 WindSimulation <-> ThermalComfort köprüsü (evaluate_with_wind_field)
# ======================================================================== #


def test_evaluate_with_wind_field_samples_wake_reduced_speed_behind_building():
    """D3'ün wake-etkili `WindField`'ından örnekleme yapıldığında, bina
    arkasındaki (wake bölgesi) nokta, serbest akıştaki bir noktaya göre
    daha düşük rüzgar hızı görmeli - ve bu, rüzgar-soğutma teriminin daha
    az olması nedeniyle (radyan kazanç varsa) apparent_temperature'ı farklı
    yönde etkilemeli (daha az rüzgar soğutması -> daha yüksek apparent
    temperature, sabit diğer koşullarda)."""
    poly = Polygon(
        points=[
            Point2D(45.0, 45.0),
            Point2D(55.0, 45.0),
            Point2D(55.0, 55.0),
            Point2D(45.0, 55.0),
        ]
    )
    wind_field = WindSimulation.simulate(
        width=100,
        height=100,
        cell_size_m=1.0,
        obstacles=[poly],
        free_stream_speed=6.0,
        free_stream_direction_deg=0.0,
        heights_m=[20.0],
    )

    # Rüzgar kuzeyden güneye (direction_deg=0 -> +y yönü) estiği için wake
    # bölgesi binanın GÜNEYİNDE (y > 55) oluşur.
    wake_point = (50.0, 60.0, 1.5)
    free_point = (50.0, 5.0, 1.5)

    wake_result = ThermalComfort.evaluate_with_wind_field(
        point=wake_point,
        location=ISTANBUL,
        when_utc=MIDNIGHT,
        occluders=[],
        air_temp_c=25.0,
        wind_field=wind_field,
    )
    free_result = ThermalComfort.evaluate_with_wind_field(
        point=free_point,
        location=ISTANBUL,
        when_utc=MIDNIGHT,
        occluders=[],
        air_temp_c=25.0,
        wind_field=wind_field,
    )

    assert wake_result.wind_speed_mps < free_result.wind_speed_mps
    assert isinstance(wake_result, ThermalComfortResult)
    assert isinstance(free_result, ThermalComfortResult)


def test_evaluate_with_wind_field_higher_wind_speed_lowers_apparent_temperature_with_radiant_gain():
    """Aynı Tmrt kazancıyla, daha yüksek örneklenen rüzgar hızı daha düşük
    apparent_temperature üretmeli (rüzgar soğutma etkisinin köprü
    üzerinden de doğru yansıdığının regresyon kontrolü)."""
    poly = Polygon(
        points=[
            Point2D(45.0, 45.0),
            Point2D(55.0, 45.0),
            Point2D(55.0, 55.0),
            Point2D(45.0, 55.0),
        ]
    )
    wind_field = WindSimulation.simulate(
        width=100,
        height=100,
        cell_size_m=1.0,
        obstacles=[poly],
        free_stream_speed=6.0,
        free_stream_direction_deg=0.0,
        heights_m=[20.0],
    )
    calm_point = (50.0, 60.0, 1.5)  # wake içinde - düşük hız
    windy_point = (50.0, 5.0, 1.5)  # serbest akış - yüksek hız

    calm = ThermalComfort.evaluate_with_wind_field(
        point=calm_point,
        location=ISTANBUL,
        when_utc=JUNE_NOON,
        occluders=[],
        air_temp_c=30.0,
        wind_field=wind_field,
    )
    windy = ThermalComfort.evaluate_with_wind_field(
        point=windy_point,
        location=ISTANBUL,
        when_utc=JUNE_NOON,
        occluders=[],
        air_temp_c=30.0,
        wind_field=wind_field,
    )
    assert calm.wind_speed_mps < windy.wind_speed_mps
    assert windy.apparent_temperature_c <= calm.apparent_temperature_c
