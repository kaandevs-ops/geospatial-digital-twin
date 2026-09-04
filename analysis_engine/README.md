# Analysis Engine

Roadmap Phase 6 — ölçüm, görünürlük, güneş/gölge simülasyonu ve çevresel
modeller (rüzgar, yağmur, sel, ısı adası, gürültü, yansıma, hava kirliliği
dağılımı).

Alt modüller:
- `measurement/` — mesafe/alan/hacim ölçüm araçları
- `visibility/` — ışın izleme, görüş hattı, kör nokta analizi, görünürlük
  ısı haritası
- `sun_simulation/` — mevsimsel güneş yolu, çatı solar potansiyeli
  (irradiance), **`ShadowProjection`** (bina ayak izinin güneş yönüne göre
  zemine izdüşümü)
- `environmental_sim/` — rüzgar (wake-etkili, Wise 1970), yağmur/akış, sel
  tahmini, ısı adası, gürültü, yansıma, **`GaussianPlumeSimulation`**
  (hava kirliliği dağılımı, Pasquill-Gifford + Briggs 1973 katsayıları)

## Roadmap V4 / Track R — R2: D20 kabul kriterinin izlenebilir testi

Roadmap V3'ün D20 kabul kriteri ("tek nokta kaynak, düz arazi senaryosunda
gürültü/kirlilik modeli çıktısının analitik elle-hesaplanan çözümle ±%5
içinde eşleşmesi") daha önce mevcut testlere dolaylı olarak gömülüydü,
ayrı isimlendirilmiş bir kabul testi yoktu.

`tests/test_phaseD20_environmental_models_acceptance.py` bunu doğrudan
kapatır:
- `GaussianPlumeSimulation.ground_level_centerline_concentration()`
  çıktısı, koddan tamamen bağımsız yazılmış bir "elle hesaplama"
  fonksiyonuyla karşılaştırılır — göreli hata testte açıkça raporlanır
  (`print` ile) ve `assert rel_error <= 0.05` ile ±%5 toleransı doğrulanır.
  Pratikte iki bağımsız yol matematiksel olarak neredeyse özdeş olduğundan
  gerçek hata `<1e-9` mertebesindedir — ±%5 toleransı rahatça karşılanır.
- Genel 3B formül (`concentration_at`) ile kısayol formülünün
  (`ground_level_centerline_concentration`) `y=0, z=0`'da tutarlılığı
  ayrıca doğrulanır.
- Fiziksel makullük: konsantrasyonun rüzgara-dik mesafeyle azalması,
  sakin havada (`wind_speed<=0`) sessizce yanlış sonuç yerine açık
  `ValueError` fırlatılması.
- D4'ün tam SPA'sı (`SolarPositionCalculator`) ile `ShadowProjection`
  entegrasyonu: güneş azimutu değiştiğinde gölge merkezi tutarlı biçimde
  kayıyor mu, gece (`is_daylight=False`) gölge doğru şekilde `None`
  dönüyor mu — ayrı test sınıfıyla kanıtlanmıştır.

Testler: `../tests/test_phaseD20_environmental_models_acceptance.py` (6 test).

---

## ROADMAP_V4 — Faz E11: Termal Konfor Endeksi (Tamamlandı)

**Kapanmadan önceki durum:** D3 (`WindSimulation` — wake-etkili "yaklaşık
CFD" rüzgar, Wise 1970) ve D20 (`GaussianPlumeSimulation`) ile açık-hava
çevresel simülasyonlar güçlendirildi; `sun_simulation.RoofIrradiance` (D4
SPA tabanlı clear-sky ışınım) ve `visibility.ShadowAnalysis` (gerçek gölge/
occluder testi) da ayrı ayrı mevcuttu — ama bu üç alt sistem hiçbir yerde
tek bir açık-hava termal konfor endeksinde birleştirilmiyordu.

**Uygulanan çözüm:** yeni `environmental_sim/thermal_comfort.py` —
`ThermalComfort` sınıfı:

- **Tmrt (ortalama ışınım sıcaklığı):** basitleştirilmiş radyatif denge
  (`Tmrt⁴ = Tair⁴ + a_k·I_direct / (eps·sigma)`, VDI 3787 Part 2 /
  Jendritzky et al. 2012'nin insan-vücudu soğurma/yayınım katsayılarıyla).
  Yalnızca doğrudan kısa-dalga bileşeni modellenir — difüz/yansıyan
  gökyüzü ışınımı ve uzun-dalga alışverişi kasıtlı olarak dışarıda
  bırakılmıştır (modülün kendi docstring'inde açıkça belirtilir).
- **Apparent temperature:** Tmrt'nin katkısı rüzgar hızıyla üstel olarak
  söner + doğrudan orantılı bir rüzgar-soğutma terimi (Newton soğuma
  kanununun niteliksel yansıması).
- **Kategori etiketleme:** UTCI'nin gerçek ısı-stresi kategori sınırları
  (Bröde et al. 2012) okunabilirlik için kullanılır — endeksin kendisi
  tam UTCI/PET DEĞİLDİR, bu dürüstçe belgelenmiştir.
- `ThermalComfort.evaluate()`: `ShadowAnalysis.evaluate()` ile gölge
  kontrolü (gölgedeyse `RoofIrradiance` hiç çağrılmaz, ışınım sıfır kabul
  edilir) + `SolarPositionCalculator` ile gece kontrolü.
- `ThermalComfort.evaluate_with_wind_field()`: D3 `WindSimulation.
  simulate()`'in ürettiği wake-etkili `WindField`'dan `point`'e en yakın
  hücrenin rüzgar hızını örnekleyip `evaluate()`'e besler — D3 ile bu
  modülü birleştiren asıl köprü.

`ThermalComfort`/`ThermalComfortResult`, `environmental_sim/__init__.py` →
`analysis_engine/__init__.py` → kök `harita/__init__.py` zincirinden
re-export edilir (tek-giriş-noktası ilkesi).

**Kabul kriteri (roadmap'in kendi ölçütü):** Bilinen bir referans
senaryoda (gölgeli+rüzgarsız vs. güneşli+rüzgarlı bir nokta) endeks
beklenen yönde farklılaşır —
`test_shadowed_calm_point_is_more_comfortable_than_sunny_windy_hot_point`
bunu, gerçek güneş azimut/yükseklik açısıyla (`SolarPositionCalculator`)
hesaplanmış bir bina-gölgesi senaryosunda sayısal olarak doğrular.

Testler: `../tests/test_phaseE11_thermal_comfort.py` (9 test) — tamamı
yeşil; mevcut `test_phase6_analysis_engine.py` (46 test) regresyonsuz
geçmeye devam ediyor.

**Dokunulan dosyalar:** yeni `environmental_sim/thermal_comfort.py`,
`environmental_sim/__init__.py`, `analysis_engine/__init__.py`,
kök `harita/__init__.py`, yeni `tests/test_phaseE11_thermal_comfort.py`,
bu README, `ROADMAP_V4.md`.
