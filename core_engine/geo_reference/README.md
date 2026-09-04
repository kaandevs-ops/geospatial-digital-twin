# Faz 17 (başlangıç) — Coğrafi Doğruluk

`core_engine.geo_reference`, `CoordinateConverter`'ın WGS84 <-> Web Mercator
<-> UTM dönüşümlerini **gerçek, yayınlanmış referans koordinatlara** karşı
doğrulayan bir regresyon paketidir.

## Ne test ediyor

1. **UTM zon ataması** — 6 gerçek referans nokta (Ankara, İstanbul, Londra,
   New York, Sidney, (0,0)) için beklenen UTM zonu, `utm_zone_for()`
   sonucuyla eşleşiyor mu.
2. **Round-trip hassasiyeti** — her referans noktada WGS84→UTM→WGS84 ve
   WGS84→Web Mercator→WGS84 sonrası haversine hata < 1mm mi.
3. **Bağımsız mesafe çapraz kontrolü** — Ankara-İstanbul haversine mesafesi,
   yayınlanmış "kuş uçuşu" değeriyle (349 km, Himmera) ±5km içinde mi.
4. **Yanlış-pozitif üretmediğinin kanıtı** — `round_trip_tolerance_m=0.0`
   ile çağrıldığında suite'in gerçekten FAIL ürettiği doğrulanır
   (`test_intentional_regression_is_detected`).

## Dürüst sınırlama

Bu, ROADMAP_V2'nin tam kabul kriterini ("gerçek bir şehrin OSM verisiyle
üretilen modeli ±1m içinde örtüşür") karşılamaz — o, gerçek OSM bina
verisi + üretim pipeline'ı + gerçek GPS ground-truth gerektirir ve bu
oturumda mevcut değildir. Burada doğrulanan, aynı iddianın bağımsız
olarak doğrulanabilir bir alt kümesi: formüllerin kendi içinde tutarlı
olduğu ve bilinen coğrafi noktalarla doğru eşleştiği.

## `core_engine.geo_reference.proj_backend` — Gerçek PROJ/pyproj entegrasyonu

Faz 17'nin son kalan maddesi ("Tam PROJ-uyumlu CRS dönüşüm kütüphanesi
entegrasyonu") bu oturumda kapatıldı. `proj_backend` modülü, opsiyonel
`pyproj` bağımlılığı (`pip install harita-modelleme[geo]`) kuruluysa
gerçek datum dönüşümü sağlar:

- `is_available()` / `proj_version()` — ortamda `pyproj` var mı, hangi
  PROJ sürümü.
- `transform_datum(point, source_epsg, target_epsg)` — gerçek PROJ
  pipeline'ı ile datum-arası dönüşüm (örn. **ED50 (EPSG:4230) → WGS84
  (EPSG:4326)** — stdlib `CoordinateConverter`'ın **yapamadığı**, gerçek
  bir 7-parametreli Helmert/grid-shift dönüşümü).
- `round_trip_error_m(point, via_epsg)` — gerçek PROJ pipeline'ı üzerinden
  round-trip hata ölçümü, stdlib tarafındaki aynı mantığın PROJ karşılığı.
- `pyproj` kurulu değilse **sessizce yanlış sonuç üretmek yerine** açık
  `ProjBackendUnavailable` fırlatılır — çağıran kod isterse stdlib
  `CoordinateConverter`'a bilinçli olarak düşer. Ana pakette bu modüle
  bağımlı hiçbir test/kod yoktur, dolayısıyla `pyproj` kurulu olmayan bir
  ortamda da paketin geri kalanı sorunsuz çalışır (regresyon riski yok).

Bu oturumda `pyproj` gerçekten kurulup (ağ erişimi mevcuttu) gerçek
dönüşümler doğrulandı: ED50→WGS84'ün stdlib'in tek-datum varsayımından
farklı, gerçek bir kayma ürettiği; WGS84→Web Mercator sonucunun stdlib
formülüyle ~metre mertebesinde örtüştüğü; 6 referans noktanın tümünde
PROJ round-trip hatasının <1cm kaldığı test edildi
(`tests/test_phase17b_proj_backend.py`, 7 test — `pyproj` kurulu değilse
bu testlerin çoğu otomatik `skip` edilir, sadece "unavailable" davranışı
her zaman çalışır).

**Kalan:** gerçek bir kamu WMTS/WMS sunucusuna karşı uçtan uca doğrulama,
"gerçek şehir OSM verisiyle ±1m" tam kabul kriteri (gerçek OSM bina
verisi + ground-truth GPS gerektirir).

## `core_engine.tile_sources`

Aynı fazın ikinci parçası: WMTS (KVP + RESTful) ve WMS (GetMap) URL
üretimi, artı ağ-agnostik `TileFetcher` Protocol'ü (`TileSourceConsumer`
ile birlikte) — gerçek bir HTTP çağrısı `UrllibFetcher` içinde
uygulanmıştır ama testler her zaman sahte bir fetcher enjekte eder,
böylece ağsız ortamlarda da tam kapsam doğrulanabilir. Gerçek bir
kamu WMTS/WMS sunucusuna karşı uçtan uca doğrulama henüz yapılmadı.

Testler: `tests/test_phase17_geo_reference.py` (19 test).
