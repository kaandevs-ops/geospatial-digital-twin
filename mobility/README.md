# mobility (Phase 7 — ✅ Uygulandı)

Roadmap Phase 7: Navigation Graph, Path Finding, Indoor Navigation, Crowd
Simulation, Traffic Simulation.

Tam teknik spesifikasyon: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)
(Phase 7 bölümü). Teslim edilenlerin özeti: [`../ROADMAP.md`](../ROADMAP.md).

## Alt modüller

| Modül | İçerik |
|---|---|
| `pathfinding/` | `NavGraph`, `AStar`, `Dijkstra`, `JumpPointSearch`, `ThetaStar` |
| `indoor_navigation/` | `Floor`, `IndoorNavigationBuilder` (kat + merdiven/asansör birleştirme) |
| `crowd_simulation/` | `Agent`, `SocialForceModel`, `EvacuationSimulator`, `OccupancyHeatmap` |
| `traffic_simulation/` | `VehicleType`, `TrafficAgent`, `IDMModel`, `TrafficSimulator`, `GreenshieldsModel`, `TrafficSignalPhase` |

## Roadmap V3 — Faz D6: Trafik Kapasite Modeli + Crowd Sim Doğrulama — ✅ Tamamlandı

**Trafik kapasitesi:** `GreenshieldsModel` (Greenshields, 1935 - klasik
doğrusal hız-yoğunluk temel akış diyagramı) yol kapasitesini (`capacity()`
→ `q_max = v_f * k_j / 4`, kritik yoğunlukta `k_j/2`) hesaplar.
`TrafficSignalPhase`, sabit-zamanlı (fixed-time) kavşak sinyalizasyonunu
modelleyip `TrafficSimulator.add_signal()` ile bir rota grubuna
bağlanabilir; kırmızı fazda `TrafficSimulator`, durma çizgisinde
hareketsiz bir "sanal öncü araç" (`_VirtualStopLeader`) besleyerek
IDM'in kendi öncü-araç takip mantığıyla kuyruk oluşumunu/boşalmasını
doğal olarak üretir - ek bir kuyruk modeli yazılmadı.

**Crowd sim doğrulaması (A7 kabul kriteri):** `ReferenceEvacuationScenario`
+ `EvacuationBenchmark`, SFPE Handbook (Nelson & Mowrer) ve
Predtechenskii & Milinskii (1978)'nin yayınladığı **özgül darboğaz akış
hızı (1.3 kişi/(m·s))** formülünü referans alır. `EvacuationBenchmark.
run_and_compare()`, `SocialForceModel` ile ajanları çıkışa yönlendirirken
çıkışın kendisini bu formülle kapasite-kısıtlı bir "gate" olarak
modeller (fiziksel dar-geçit geometrisi yerine, literatürdeki adı konmuş
oranla doğrudan kalibre edilmiş); simülasyon süresi, referans formülün
öngördüğü süreden **%20'nin altında** sapar (bkz.
[`tests/test_phaseD6_traffic_capacity_crowd_validation.py`](../tests/test_phaseD6_traffic_capacity_crowd_validation.py)).
14 yeni test, tamamı yeşil; mevcut 480 test hiç kırılmadı (toplam 494).

## Hızlı örnek

```python
from harita.mobility import NavGraph, AStar

g = NavGraph.from_grid(20, 20)
result = AStar.find_path(g, (0, 0), (19, 19))
print(result.found, result.cost, len(result.path))
```

Tahliye simülasyonu ve trafik simülasyonu için `crowd_simulation`/
`traffic_simulation` modüllerindeki docstring'lere ve
`harita/tests/test_phase7_mobility.py` içindeki kullanım örneklerine bakın.
