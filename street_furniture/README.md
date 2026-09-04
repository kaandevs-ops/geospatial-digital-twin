# street_furniture

ROADMAP_V5 M2.5 — "Bina dışı meshler": sokak lambası, elektrik direği,
çöp kutusu, bank, otobüs durağı gibi altyapı/mobilya elemanlarının
parametrik mesh üretimi.

- `StreetFurnitureType`: desteklenen eleman tipleri.
- `OSM_TAG_MAP`: `"highway=street_lamp"` gibi OSM tag string'lerini
  `StreetFurnitureType`'a eşler (bilinmeyen tag → `None`, sessizce atlanır).
- `StreetFurnitureGenerator.from_osm_tag(tag, position)`: OSM verisinden
  tek adımda `StreetFurnitureItem` üretir.
- `StreetFurnitureGenerator.generate(item)`: tek eleman → `Mesh3D`.
- `StreetFurnitureGenerator.generate_batch(items)`: bir mahalle
  ölçeğindeki tüm öğeleri tek mesh'te birleştirir.

`vegetation/` modülüyle aynı desen: stdlib-only, yalnız `mesh_engine`
primitifleri (box/cylinder) kullanılır, harici asset/model dosyası yok.
Instancing/batching optimizasyonu bu modülün sorumluluğunda değildir —
`mesh_engine/batching.py` (ROADMAP_V5 M1.2) ile birlikte kullanılmalıdır.

**Kapsam dışı (roadmap M2.5'in diğer maddeleri, bu turda yapılmadı):**
köprü/üst geçit (`mobility` modülüyle entegrasyon gerektirir), su yüzeyi
düzlemleri, kaldırım/bordür/yaya geçidi çizgileri gibi peyzaj detayları.
