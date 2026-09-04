# vegetation — Prosedürel Bitki Örtüsü

Roadmap V4 — Track E / **Faz E18**.

## Ne yapar

- `TreeGenerator.generate(species, height, canopy_radius, seed)` — mevcut
  `mesh_engine` altyapısı (`MeshBuilder.extrude_polygon`, `MeshMerger`,
  `NormalGenerator`) yeniden kullanılarak deterministik, düşük-poli bir
  ağaç/çalı `Mesh3D`'i üretir. Dört tür: `CONIFER` (tek koni), `DECIDUOUS`/
  `GENERIC` (iki basık koni üst üste — yuvarlak taç yaklaşıklaması),
  `SHRUB` (gövdesiz, tek geniş koni).
- `VegetationScatterer.scatter(grid, target_count, seed, species=...)` —
  bir `terrain_engine.HeightmapGrid` üzerinde, `terrain_engine.
  FlowAccumulation` çıktısını **nem vekili** olarak kullanarak (D8 akümüle
  akış yüksek = vadi tabanı/nemli), yerel eğimi bir ceza terimi olarak
  ekleyerek, ağırlıklı-örneklemeli (weighted, with-replacement) bir
  `VegetationInstance` listesi üretir.

## Neden gövde+taç primitifleri (tam L-sistemi değil)

Roadmap "basit L-sistem **veya** prosedürel budaklanma kuralları" diyor.
Tam bir L-sistem (rekürsif dallanma grameri) şehir-ölçekli sahnelerde
binlerce ağaç için gerçek-zamanlı üretim/bellek maliyetini artırır ve
düşük-poli/uzak-mesafe kullanım senaryosunda (bu projenin ana kullanım
durumu — bkz. `performance.LODManager`, `InstancingBatch`) görsel fayda
sağlamaz. Gövde+taç yaklaşımı stdlib-only kalır, mevcut mesh altyapısını
değiştirmeden yeniden kullanır ve tür başına farklı taç şekliyle yeterli
görsel çeşitlilik sağlar.

## Nem vekili neden `log1p` ile normalize ediliyor

D8 akümüle akış (`FlowAccumulation.accumulate`) tipik olarak birkaç
büyüklük mertebesinde (order-of-magnitude) değişir — bir vadi tabanı
hücresi, tek bir izole tepe hücresinin binlerce katı akümüle değere sahip
olabilir. Doğrusal min-max normalizasyon bu durumda dağılımı aşırı
sivri/tek-modlu hale getirir (neredeyse tüm hücreler ~0'a, yalnızca bir
avuç hücre ~1'e yakın olur). `log1p` bu büyüklük farkını sıkıştırarak
orta-nemli alanların da anlamlı bir ağırlık almasını sağlar.

## Kabul kriteri (roadmap) ve doğrulama

> Bir arazi parçası üzerinde üretilen bitki örtüsü yoğunluğu,
> `FlowAccumulation` değeri yüksek (nemli) bölgelerde ölçülebilir şekilde
> daha fazladır (istatistiksel regresyon testi).

Bkz. `tests/test_phaseE18_vegetation.py::test_scatter_density_higher_in_high_flow_accumulation_region`
— yapay olarak tek bir alçak "vadi" hücresi olan bir arazi üzerinde,
o hücreye ve komşularına düşen örnek sayısının, arazinin geri kalanındaki
eş büyüklükteki bir bölgeden istatistiksel olarak anlamlı derecede fazla
olduğu doğrulanır (sabit `seed` ile deterministik).

## Entegrasyon noktaları

- `terrain_engine.HeightmapGrid` / `FlowAccumulation` (girdi)
- `mesh_engine.Mesh3D` / `MeshBuilder` / `MeshMerger` / `NormalGenerator` (mesh üretimi)
- Sahne/render entegrasyonu ileride `render_engine.scene_from_meshes` ile
  aynı şekilde yapılabilir (bu fazın kapsamı dışında — mevcut render
  entegrasyonu binalar için zaten var, ağaçlar da aynı yolu izler).
