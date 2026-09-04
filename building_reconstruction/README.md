# building_reconstruction (Phase 3) — ✅ Uygulandı

Haritadaki bina polygon'undan (footprint) tam bir `Building` nesnesi
(kat planları, çatı, cephe, dolaşım elemanları) üreten katman.

Teknik spesifikasyon: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md#phase-3--building-reconstruction)

## Alt modüller

| Modül | Sorumluluk |
|---|---|
| `footprint_parser/` | `GeoFeature` -> `Footprint` (alan, çevre, yön, eğim, çatı tahmini) |
| `roof_generator/` | 11 çatı tipi (Flat/Hip/Gable/Cross Gable/Mansard/Pyramid/Sawtooth/Industrial/Modern/Solar/Green) -> `Mesh3D` |
| `facade_generator/` | Malzeme seçimi (Cam/Beton/Tuğla/Metal/Kompozit/Taş/Ahşap/Endüstriyel) + pencere paterni -> `Facade` |
| `room_generator/` | BSP (Binary Space Partitioning) + adjacency-graph tabanlı oda üretimi (13 oda tipi) |
| `building_elements.py` | Window/Balcony/Stair/Elevator/Corridor/Door generator'ları |
| `procedural_generator/` | Tüm alt sistemleri birleştiren orkestratör: `Footprint` -> `Building` (12 bina tipi için `BuildingTypeRules` registry) |
| `regulations/` | Çok-ülkeli/bölgeli parametrik yönetmelik motoru: `RegulationProfile` + kayıt defteri (`TR_PAIY_ISO_BYKHY` varsayılan, `STRICT_REFERENCE` örnek) (Faz E19) |

## Hızlı kullanım

```python
from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.building_reconstruction import (
    Footprint,
    ProceduralBuildingGenerator,
    BuildingType,
)

footprint = Footprint(
    polygon=Polygon([Point2D(0, 0), Point2D(20, 0), Point2D(20, 15), Point2D(0, 15)]),
    building_type="apartments",
    floor_count=5,
    height_m=15.0,
)
building = ProceduralBuildingGenerator.generate(
    footprint,
    building_type=BuildingType.APARTMAN,
    seed=11,
)

print(len(building.floors))  # 5
print(building.roof.triangle_count())
print(building.facade.material)  # FacadeMaterial.BETON
full_mesh = building.full_mesh()  # cephe + çatı birleşik Mesh3D
```

## Roadmap V2 — A3 derinleştirme (bu oturum)

- **`footprint_parser.FootprintShape`** — `concave_vertex_count()` (baskın
  dönüş yönüne göre içbükey/reflex köşe sayımı) + `classify_shape()`
  (convex-hull alan eksikliği + içbükey köşe sayısına göre
  `rectangle`/`l_shape`/`t_shape`/`u_shape`/`complex`). `Footprint.shape`
  ve `Footprint.concave_vertex_count` alanları olarak otomatik hesaplanır.
  `guess_roof_type()` artık konkav (L/T/U/complex) tabanlarda tanımsız bir
  tek-mahyalı `gable`/`pyramid` yerine güvenli `hip`'e düşer — yalnızca
  dikdörtgen-benzeri tabanlarda `gable`/`pyramid` önerilir.
- **`facade_generator.FacadeGenerator.check_compliance()`** — parametrik
  bina-yönetmeliği kural motoru: `MIN_WINDOW_WALL_RATIO` tablosuyla bina
  tipine göre asgari pencere/duvar oranı denetimi ve
  `FIRE_ESCAPE_MIN_FLOORS` (varsayılan 4) eşiğinin üzerindeki binalarda
  ikinci kaçış yolu/yangın merdiveni gerekliliği (`has_second_egress`
  parametresiyle karşılanabilir). `FacadeComplianceReport` ile sonuç raporu.
- **`room_generator.RoomGenerator.check_compliance()`** — `MIN_ROOM_AREA_M2`
  tablosuyla oda tipine göre asgari alan denetimi + koridor tipindeki
  odalar için `MIN_CORRIDOR_WIDTH_M` (1.2m) asgari genişlik denetimi
  (bounding-box kısa kenarı üzerinden). `RoomComplianceReport` /
  `RoomComplianceIssue` ile sonuç raporu.

Testler: `harita/tests/test_a3_building_reconstruction_deepening.py`
(15 test — şekil sınıflandırma, konkav-taban çatı fallback'i, cephe ve
oda uygunluk denetimleri).

## Roadmap V2 — A3 kabul kriteri: toplu üretim + geometrik geçerlilik (bu oturum)

**Kabul kriteri:** *"500 gerçek OSM bina footprint'i ile toplu üretim,
üretilen binaların %100'ü geometrik olarak geçerli (self-intersection
yok, manifold mesh)."*

**Dürüst not:** Bu ortamda gerçek bir OSM/Overpass sunucusuna ağ erişimi
yok (izin verilen domain listesi yalnızca paket kaynaklarını kapsıyor).
Bunun yerine `tests/test_a3_bulk_footprint_validation.py`, **500 adet
prosedürel olarak üretilmiş, geometrik olarak çeşitli** footprint
(dikdörtgen, L/T/U şekilli, ve rastgele çok köşeli "star-shaped" complex
poligonlar; 8m–80m kenar, 1–20 kat, 12 bina tipinin tamamı turlanarak)
kullanır. Her footprint için:

1. Kaynak poligonun kendisiyle kesişmediği doğrulanır (özel bir
   segment-kesişim denetleyicisiyle),
2. `ProceduralBuildingGenerator.generate()` ile tam bir `Building`
   üretilir,
3. `full_mesh()`'in `mesh_engine.MeshRepair.is_manifold()` testini
   geçtiği (her kenarın en fazla 2 üçgene ait olduğu) doğrulanır.

**Sonuç: 500/500 (%100) geometrik olarak geçerli** — hiçbir footprint
self-intersecting değil, hiçbir üretilen mesh non-manifold değil (5 test,
hepsi yeşil). Gerçek OSM verisiyle uçtan uca entegrasyon testi (ağ
erişimi olan bir ortamda) hâlâ açık kalan iştir.

## Roadmap V3 — D13: Gerçek TS/ISO Sayısal Eşikleri — ✅ Tamamlandı

`room_generator` ve `facade_generator`'daki eşik tabloları artık kaynaksız
"basitleştirilmiş" yer tutucu değil — her sayısal eşik, alıntılanabilir bir
yönetmelik/standart maddesine atıfla belgelendi (tam metin kopyalanmadan,
yalnızca madde adı + sayısal değer):

- **`room_generator.MIN_ROOM_AREA_M2` / `MIN_CORRIDOR_WIDTH_M`** —
  `[PAİY-27]` Planlı Alanlar İmar Yönetmeliği Madde 27 (asgari oda net
  alanları) ve `[ISO 21542]` ISO 21542:2011 Bölüm 10 (erişilebilir koridor
  asgari genişliği 1200mm) referanslarıyla; her tablo satırı ve
  `check_compliance()`'ın ürettiği ihlal metni ilgili kaynak kodunu içerir.
  Kaynak künyesi: `room_generator.THRESHOLD_SOURCES`.
- **`facade_generator.MIN_WINDOW_WALL_RATIO` / `FIRE_ESCAPE_MIN_FLOORS`** —
  `[PAİY-8]` Planlı Alanlar İmar Yönetmeliği Madde 8 (doğal aydınlatma),
  `[TS 825]` Binalarda Isı Yalıtım Kuralları (düşük-ısıtmalı hacimlerde
  düşük camlanma oranı) ve `[BYKHY]` Binaların Yangından Korunması
  Hakkında Yönetmelik (4+ katta ikinci kaçış yolu) referanslarıyla. Kaynak
  künyesi: `facade_generator.THRESHOLD_SOURCES`.

Kabul kriteri testi:
[`tests/test_phaseD13_ts_iso_thresholds.py`](../tests/test_phaseD13_ts_iso_thresholds.py)
— her tablonun kaynak künyesine sahip olduğu + mevcut `check_compliance()`
davranışının (oda/koridor/pencere-oranı/kaçış-yolu senaryoları) hiç
kırılmadığı doğrulanır: 11 yeni test, tamamı yeşil; mevcut
`test_a3_building_reconstruction_deepening.py` (15 test) hiç kırılmadı.

## Roadmap V4 — Faz E19: Çok-Ülkeli Bina Yönetmeliği Motoru — ✅ Tamamlandı

D13'ün TS/ISO tabanlı sabit eşikleri tek bir yönetmelik setine kilitliydi.
Yeni `building_reconstruction/regulations/` paketi bu eşikleri parametrik
bir `RegulationProfile` (frozen dataclass) içine taşır:

- **`RegulationProfile`** alanları: `min_room_area_m2` (oda tipi ->
  m² eşik sözlüğü), `min_corridor_width_m`, `min_window_wall_ratio`
  (bina tipi -> oran sözlüğü), `fire_escape_min_floors`, `source_label`,
  `name`. `room_area_threshold()` / `window_ratio_threshold()` yardımcı
  metotları ve `with_overrides()` ile türetilmiş profil oluşturma
  desteklenir.
- **`default_profile()`** — D13'ün `MIN_ROOM_AREA_M2`,
  `MIN_CORRIDOR_WIDTH_M`, `MIN_WINDOW_WALL_RATIO`,
  `FIRE_ESCAPE_MIN_FLOORS` sabitlerini (tek kaynak ilkesiyle, gecikmeli
  import ile) aynen sarmalayan `TR_PAIY_ISO_BYKHY` profili — geriye
  uyumlu varsayılan.
- **`strict_reference_profile()`** — TR profilinin 1.5x daha katı asgari
  alan/pencere-oranı eşiklerine sahip `STRICT_REFERENCE` örnek profili
  (Eurocode/IBC benzeri bölgelerde görülen genel eğilimi temsil eder).
- **Kayıt defteri** — `get_profile(name)`, `register_profile(profile)`,
  `available_profiles()` ile özel/harici profillerin (plugin'ler,
  bölgesel yönetmelikler) çalışma zamanında eklenip aranabilmesi.

`RoomGenerator.check_compliance(rooms, profile=None)` ve
`FacadeGenerator.check_compliance(..., profile=None)` artık opsiyonel
bir `profile` parametresi alır; `profile=None` verildiğinde
`default_profile()` kullanılır ve **davranış D13 ile birebir aynıdır**
(mevcut `test_phaseD13_ts_iso_thresholds.py` ve
`test_a3_building_reconstruction_deepening.py` değişmeden geçer).
Üst seviye paket bu API'yi `RegulationProfile`,
`default_regulation_profile`, `strict_reference_profile`,
`get_regulation_profile`, `register_regulation_profile`,
`available_regulation_profiles` adlarıyla re-export eder.

Kabul kriteri testi:
[`tests/test_phaseE19_regulation_profiles.py`](../tests/test_phaseE19_regulation_profiles.py)
— aynı binanın (3.5m×4.0m = 14 m² salon) `TR_PAIY_ISO_BYKHY` profilinde
uygun, `STRICT_REFERENCE` profilinde (asgari 18 m²) uygunsuz sonuç
ürettiğini; cephe pencere/duvar oranı asgarisinin `STRICT_REFERENCE`'ta
TR profiline eşit ya da daha yüksek olduğunu; özel profil kaydı/getirme
ve bilinmeyen isimde `KeyError` davranışını doğrular — 5/5 test yeşil.

Kapsam dışı bırakılanlar (ROADMAP_V2.md A3 kalan maddeleri): gerçek OSM
verisiyle uçtan uca entegrasyon (ağ erişimi gerektirir), gerçek mimari
standart (TS/ISO) referans dokümanlarıyla tam uyumlu sayısal eşikler
(mevcut tablo basitleştirilmiş/yer tutucu), oda üretiminde konkav-BSP.

## Testler

`../tests/test_phase3_building_reconstruction.py` — 25 test (footprint
analizi, 11 çatı tipi, cephe malzeme seçimi, BSP oda üretimi + adjacency
simetrisi, merdiven/asansör/kapı/pencere/balkon üretimi, uçtan uca 3 bina
tipi senaryosu, seed determinizmi).

```
python -m pytest harita/tests/test_phase3_building_reconstruction.py -q
```
