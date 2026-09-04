# Physics (Roadmap V4 — Track E / Faz E17)

Rijit cisim fiziği temeli — "Hiçbir fizik motoru yok" boşluğunu kapatan
minimal, stdlib-only bir çekirdek.

## Kapsam

- `RigidBox`: eksene-hizalı dikdörtgenler prizması gövde (kütle, hız,
  basitleştirilmiş tek-eksenli açısal hız/yönelim).
- Çarpışma tespiti: mevcut `data_engine.spatial_index.AABB3D` yeniden
  kullanılır — kaba-faz N² AABB kesişim taraması + eksen-örtüşmesi
  tabanlı penetrasyon/normal hesabı.
- İmpuls-tabanlı çözümleyici: sequential-impulse yaklaşımının
  basitleştirilmiş hali + Baumgarte konum düzeltmesi (sızıntı önleme).
- `GroundShakeForceModel`: sinüzoidal taban ivmesi (pseudo-static basit
  deprem yükü yaklaşımı — **tam bir zaman-tanım-alanlı sismik analiz
  motoru değildir**, bilinçli kapsam sınırı).
- `TowerStabilityScenario`: bir blok yığınının artan taban ivmesi altında
  devrilme/stabilite davranışını doğrulayan hazır kabul-testi senaryosu.

## Kabul kriteri (roadmap E17)

> Basit bir kule/blok yığını, yeterince güçlü bir yatay taban ivmesi
> altında (test parametreleriyle) devrilirken, düşük ivmede stabil kalır.

`tests/test_phaseE17_physics.py` bunu doğrudan doğrular: düşük
`peak_acceleration_g` (örn. 0.02g) altında hiçbir blok devrilmez; yüksek
`peak_acceleration_g` (örn. 1.0g) altında en az bir blok
`RigidBox.is_toppled()` kriterine göre devrilir.

## Bilinçli kapsam dışı

- Tam 3D dönme dinamiği (quaternion tabanlı) — yalnızca tek eksenli
  (z-ekseni etrafında) basitleştirilmiş açısal davranış.
- Eşik-üstü büyük gövde sayıları için broad-phase optimizasyonu (Octree/
  BVH entegrasyonu) — mevcut `AABB3D` sözleşmesiyle ileride eklenebilir,
  bu oturumun kapsamı dışında bırakıldı.
- Yumuşak cisim / kumaş / sıvı simülasyonu.

## Kullanım

```python
from harita.physics import TowerStabilityScenario

scenario = TowerStabilityScenario(num_blocks=4)
toppled = scenario.run_stability_test(peak_acceleration_g=1.0, duration_s=6.0)
```
