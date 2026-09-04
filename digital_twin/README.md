# digital_twin

**Durum: ✅ Uygulandı** (çalışan kod + testler — bkz. `../tests/test_phase5_digital_twin.py`)

Roadmap Phase 5. Önceki fazların (Mesh Engine, Material Engine, Building
Reconstruction, AI Reconstruction) ürettiği verileri, tek kimlikli bir
`DigitalTwin` nesnesinde birleştirir. Bu, sonraki fazların (Analysis Engine,
Mobility, Editor, Export, AI Assistant) üzerinde çalışacağı ortak "nesne"
soyutlamasıdır.

## İçerik

- `DigitalTwin` — geometry, metadata, materials, history, simulation_state,
  ai_data, sensors, annotations, measurements, layers.
- `TwinEvent` — append-only olay günlüğü (her mutasyon otomatik loglanır).
- `SensorBinding`, `Annotation`, `Measurement` — yardımcı veri tipleri.
- `TwinDiff` / `diff_twins` — versiyonlar arası fark raporu.
- `DigitalTwinRegistry` — id bazlı CRUD + versiyonlama.
  **Önemli:** registry checkout/commit semantiği kullanır — `create()`,
  `get()`, `query()`, `all()` bağımsız kopyalar döndürür; değişiklikler
  yalnızca `save(twin)` çağrısıyla kalıcı olur (ve `save()` mutasyon
  öncesi durumun bir snapshot'ını otomatik saklar). Bu sayede
  `rollback()`/`diff()`/versiyon geçmişi tutarlı kalır.

## Kullanım

```python
from harita import DigitalTwinRegistry

registry = DigitalTwinRegistry()
twin = registry.create("bina-42")
twin.set_metadata("building_type", "office")
twin = registry.save(twin)  # v2 olarak commit edilir

twin.bind_sensor(...)
twin = registry.save(twin)  # v3

old = registry.get_version("bina-42", 1)
diff = registry.diff("bina-42", 1, 3)
```

Tam teknik spesifikasyon için bkz: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## ROADMAP_V3 — Faz D11: Gerçek IoT Akış Modeli + Twin Hiyerarşisi — ✅ Tamamlandı

Yeni dosya: [`hierarchy.py`](./hierarchy.py).

### `TwinHierarchy`

Twin id'leri arasında parent/child ilişkisi (örn. `mahalle -> blok -> bina`)
kurar ve üst seviyeden agregasyon sorguları sağlar (bir bloktaki tüm
binaların toplam enerji tüketimi gibi). `DigitalTwin`/`DigitalTwinRegistry`
değiştirilmeden, ayrı bir katman olarak çalışır.

**Performans stratejisi (A5 kabul kriteri — 1000 twin'de O(log n)):**
her `aggregate()` çağrısı sonucunu `(cache_key, twin_id)` anahtarıyla
önbelleğe alır. Bir yaprak `invalidate()` edildiğinde yalnızca o yapraktan
köke giden yol (dengeli bir ağaçta O(log n) düğüm) "kirli" işaretlenir;
sonraki `aggregate()` çağrısı, kirli olmayan kardeş alt-ağaçları
önbellekten O(1) okur ve yalnızca kirli yolu yeniden hesaplar. Bu, ilk
("soğuk") sorgunun O(n) olduğu ama art arda gelen tekil-değişiklik
sorgularının toplam yaprak sayısından bağımsız kaldığı anlamına gelir —
test paketinde 1000 bina'lık (20 blok × 50 bina) bir hiyerarşide bunu
`metric_fn` çağrı sayısını sayarak doğrudan kanıtlıyoruz
(`test_1000_twin_hierarchy_query_after_single_leaf_change_is_sublinear`).

`direct_leaf_sum()` kasıtlı olarak O(n) bir referans/regresyon fonksiyonu
sağlar — `aggregate()` sonucu her zaman bununla eşleşmelidir
(`test_aggregate_matches_direct_leaf_sum`).

```python
from harita import DigitalTwinRegistry, TwinHierarchy

registry = DigitalTwinRegistry()
h = TwinHierarchy()
h.add("neighborhood-1")
h.add("block-1", parent_id="neighborhood-1")

for i in range(50):
    bldg_id = f"bldg-{i}"
    h.add(bldg_id, parent_id="block-1")
    t = registry.create(bldg_id)
    t.set_metadata("energy_kwh", 12.5)
    registry.save(t)


def energy(twin):
    return twin.metadata.get("energy_kwh", 0.0) if twin else 0.0


total = h.aggregate_leaf_sum(registry, "neighborhood-1", energy)

# Bir bina güncellendiğinde:
t = registry.get("bldg-3")
t.set_metadata("energy_kwh", 999.0)
registry.save(t)
h.invalidate("bldg-3")  # yalnızca bldg-3 -> block-1 -> neighborhood-1 yolu kirlenir
total = h.aggregate_leaf_sum(
    registry, "neighborhood-1", energy
)  # yalnızca bu yol yeniden hesaplanır
```

### Gerçekçi sensör zaman-serisi üretimi

`generate_sensor_timeseries()` tamamen rastgele değerler yerine adı konmuş,
deterministik (seed'li) bir model kullanır:

```
value(t) = base_value
         + daily_amplitude   * sin(2π · t_of_day  / day_seconds)
         + seasonal_amplitude * sin(2π · t_of_year / year_seconds)
         + N(0, noise_std)
```

`SensorSeriesConfig` ile profil (ör. sıcaklık sensörü vs enerji sayacı)
tekrar kullanılabilir; `min_value`/`max_value` ile fiziksel olarak
anlamsız değerler (ör. negatif nem) kırpılabilir.

`apply_timeseries_to_sensor(twin, sensor_id, series)` üretilen seriyi bir
`SensorBinding`'e uygular. Varsayılan modda (`log_each_update=False`)
yalnızca son değer uygulanır ve **tek bir** özet `sensor_timeseries_applied`
olayı loglanır — büyük serilerde (`count` yüzlerce/binlerce nokta) `history`
listesinin şişmesini önlemek için. Küçük seriler için `log_each_update=True`
ile her nokta ayrı ayrı (tam history ile) uygulanabilir.

### Testler

[`../tests/test_phaseD11_twin_hierarchy_iot.py`](../tests/test_phaseD11_twin_hierarchy_iot.py)
— 19 test: hiyerarşi yapı/sorguları, agregasyon doğruluğu (doğrudan yaprak
toplamıyla eşleşme), özelleştirilebilir `reduce_fn`, 1000 twin'lik
hiyerarşide sub-linear sorgu kanıtı (2 senaryo), zaman serisi
determinizm/farklı-seed/günlük-tepe-çukur/mevsimsel-bileşen/clamp/override,
`apply_timeseries_to_sensor` event-log davranışı, serialization roundtrip
ve üst seviye `harita` paketi re-export doğrulaması. Toplam proje testi:
**880 geçiyor** (5 opsiyonel `pyproj` testi atlanıyor), hiçbir mevcut test
kırılmadı.

---

## ROADMAP_V4 — Faz E12 (IoT Protokol Entegrasyonu)

`digital_twin/iot_bridge.py` eklendi:

- **`TopicBus`** — stdlib-only, MQTT'nin topic/payload semantiğini
  (hiyerarşik konular, `+`/`#` joker karakterleri, QoS 0 teslimat) taklit
  eden in-process pub/sub veri yolu.
- **`MqttBridge`** — opsiyonel `paho-mqtt` (`[iot]` extra) ile gerçek bir
  MQTT broker'ına bağlanıp mesajları `TopicBus`'a köprüler; kurulu değilse
  açık `MqttBackendUnavailable` fırlatır.
- **`SensorIotBinding`** — bir `DigitalTwin`'in `SensorBinding`'ini bir
  `TopicBus` konusuna abone eder; gelen her mesaj `update_sensor()`'ı
  çağırır ve otomatik bir `TwinEvent` (`sensor_value_updated`) günlüğe
  yazar (roadmap'in kendi kabul kriteri).

`harita/__init__.py`'ye re-export edildi (C3 deseniyle tutarlı).

### Testler
[`../tests/test_phaseE12_iot_bridge.py`](../tests/test_phaseE12_iot_bridge.py)
— 9 test, hepsi geçti.
