# lighting — ✅ Uygulandı (Phase 2) — Roadmap V3 / Faz D4 ile derinleştirildi

`SolarPositionCalculator` artık NOAA'nın kaba yaklaşıklaması yerine Meeus'un
"Astronomical Algorithms" (2. baskı, Böl. 25 + 12 + 13) düşük-hassas ama
titiz güneş konumu algoritmasını kullanıyor (~0.01° mertebesinde ekliptik
boylam hassasiyeti, kitabın kendi belirttiği sınır). `SunLight`, `MoonLight`,
`HDRSky`, `ShadowMapPass`/`ShadowCalculator`, `AmbientLight`,
`AmbientOcclusionBaker` değişmedi.

Kod: [`__init__.py`](./__init__.py) · Testler:
[`../tests/test_phase2_3d_world_engine.py`](../tests/test_phase2_3d_world_engine.py),
[`../tests/test_phaseD4_solar_position_spa.py`](../tests/test_phaseD4_solar_position_spa.py)
(Roadmap V3 - Faz D4: Meeus Böl. 25 örnek 25.a ile ara-adım doğrulaması)
Teknik spesifikasyon (tasarım referansı): [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Roadmap V3 — Faz D4: Tam SPA (Solar Position Algorithm) — ✅ Tamamlandı

**Önceki kısıt:** NOAA'nın basitleştirilmiş algoritması ±0.1-0.5° mertebesinde
hataya sahipti (deklinasyon için tek-terimli sinüs yaklaşıklaması, zaman
denklemi için 3-terimli kaba seri).

**Yeni algoritma (Meeus, Astronomical Algorithms, 2. baskı):**
1. Julian Day + Julian yüzyıl (Böl. 7).
2. Güneşin geometrik ortalama boylamı L0, ortalama anomali M, dışmerkezlik e,
   denklem-merkezi C (Böl. 25) → gerçek boylam.
3. Nütasyon/aberasyon düzeltmesiyle görünür boylam λ.
4. Ekliptik eğikliği ε (ortalama + nütasyon düzeltmesi, Böl. 22).
5. Sağ açıklık α / deklinasyon δ (Böl. 25).
6. Greenwich Ortalama Yıldız Zamanı → yerel saat açısı H (Böl. 12).
7. Ekvatoral → ufuk (alt/az) dönüşümü (Böl. 13).

**Doğrulama:** `test_phaseD4_solar_position_spa.py`, kitabın kendi örneği
(25.a, 1992-10-13) için ara değerleri (JD, L0, M, gerçek boylam) bağımsız bir
üçüncü-taraf referansla (PyMeeus kütüphanesinin doc-test'i: gerçek boylam =
199°54'36" = 199.910°) karşılaştırır; ayrıca ekinoksta ekvatorda öğle
elevation'ının ~90°'ye yakın olması, kuzey yarımkürede öğle azimutunun
güney yönünde (90°-270° aralığında) olması gibi fiziksel tutarlılık
testleri içerir. Mevcut tüm testler (458) hiç kırılmadı; API imzası
(`SolarPositionCalculator.compute(location, when_utc) -> SolarPosition`)
korunmuştur - geriye tam uyumlu.

