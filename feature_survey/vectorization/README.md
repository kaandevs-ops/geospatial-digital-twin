# `feature_survey/vectorization/` — ROADMAP_V6 FAZ S4

Saha kodlarından (`codes.py`) ve ölçüm sırasından **otomatik linework**
(çizgi/poligon) üretimi.

| Modül | Kapsam | Durum |
|---|---|---|
| `linework.py` | `FieldSurveySession` -> `VectorFeature` listesi (Point/LineString/Polygon), açık (`string_id`/bağlantı kodu) ve örtük (ardışık aynı-kod) gruplama | S4 tamamlandı |
| `qa.py` | Geometrik QA: kesin poligon alanı (shoelace), ızgara-tabanlı IoU, bağıl alan farkı | S4 tamamlandı |
| `export_bridge.py` | Linework -> GeoJSON (`export/reports.JSONReportExporter`) ve SVG kat planı (`export/vector_2d.SVGCanvas`) — var olan export zincirine köprü | S4 tamamlandı |

## Tasarım ilkesi

- **Hiçbir enterpolasyon/tahmin yok.** Üretilen her koordinat, sahada
  ölçülmüş bir `FieldPoint`'ten doğrudan kopyalanır. Tek istisna: Polygon
  geometrisi olan bir grubun ilk/son noktası çakışık değilse, **kapatma
  için ilk nokta sona eklenir** (yeni koordinat üretilmez, var olan nokta
  tekrarlanır) — `VectorFeature.closed` alanı bunu açıkça işaretler.
- **İki gruplama stratejisi:** açık (`FieldPoint.string_id` — geleneksel
  "bağlantı kodu"/link-code pratiği, örn. `BLD_COR:1`, `BLD_COR:2`) ve
  örtük (string_id yoksa, ölçüm sırasında ardışık aynı-kodlu noktalar
  otomatik gruplanır, kod değişince grup kapanır).
- **Yetersiz veri durumunda sessiz hata yok.** Bir grup, kodun
  `geometry_hint`'ine göre gereken minimum nokta sayısına (LineString: 2,
  Polygon: 3) sahip değilse `VectorizationError` fırlatılır.
- **Yeni export formatı icat edilmez.** `export_bridge.py`, var olan
  `export/vector_2d.SVGCanvas` ve `export/reports.JSONReportExporter`
  exporter'larını çağırır.

## Kabul kriteri (roadmap'ten)

> Gerçek bir saha ölçüm senaryosu (10+ bina, yol, bordür, alt yapı noktası
> karışık) uçtan uca test: ham nokta listesi -> otomatik çizim -> görsel QA
> (referans CAD çizimiyle geometrik karşılaştırma, IoU/alan farkı metriği).

`tests/test_phaseS4_vectorization.py` içinde:
- 10+ binalık karışık bir saha senaryosu (`string_id` ile açıkça
  gruplanmış binalar + örtük gruplanmış yol/bordür) uçtan uca
  vektörleştirilir.
- Üretilen bina poligonları, bilinen referans (test verisinde önceden
  tanımlanmış) poligonlarla `qa.grid_iou` (IoU) ve `qa.area_difference_ratio`
  (alan farkı) ile karşılaştırılır; kendisiyle karşılaştırma IoU=1.0,
  alan farkı=0.0 vermelidir (kimlik testi) ve `grid_iou`'nun çözünürlük
  arttıkça yakınsadığı ayrı bir testle kanıtlanır.
- Negatif senaryo: yetersiz nokta sayısına sahip bir grup
  `VectorizationError` ile reddedilir (sessizce boş/varsayılan geometri
  üretilmez).

## `qa.grid_iou` üzerine not

Proje bağımlılık felsefesi (`stdlib-only çekirdek + opsiyonel extra`)
gereği tam bir polygon-clipping kütüphanesi (Shapely vb.) zorunlu
kılınmadı. Bunun yerine ızgara-tabanlı sayısal integrasyon kullanılır —
bu **yaklaşık ama gerçek ve deterministik** bir yöntemdir, `resolution`
arttıkça gerçek IoU değerine yakınsar. Kesin sonuç gereken senaryolarda
(örn. yüksek hassasiyetli mühendislik QA'sı) `polygon_area` (shoelace,
kesin) kullanılmalı veya opsiyonel bir `shapely` extra'sı ileride
eklenebilir (mimari bunu engellemiyor, `export_bridge.py`'nin dışında
kalan saf bir hesaplama katmanı).
