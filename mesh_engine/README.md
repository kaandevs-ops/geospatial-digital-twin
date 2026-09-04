# mesh_engine — ✅ Uygulandı (Phase 2) + ROADMAP_V3 Faz D1 (derinleştirildi)

Polygon + yükseklik → `Mesh3D`. `MeshBuilder`, `MeshOptimizer`,
`MeshSimplifier`, `MeshSplitter`, `MeshMerger`, `MeshRepair`, `UVGenerator`,
`NormalGenerator`, `TangentGenerator`.

Kod: [`__init__.py`](./__init__.py) · Testler: [`../tests/test_phase2_3d_world_engine.py`](../tests/test_phase2_3d_world_engine.py)
Teknik spesifikasyon (tasarım referansı): [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## ROADMAP_V3 — Faz D1: Gerçek Quadric Error Metric (tamamlandı)

`MeshSimplifier.simplify()` artık Garland & Heckbert (1997, "Surface
Simplification Using Quadric Error Metrics") algoritmasının gerçek bir
uygulamasını kullanıyor:

- Her vertex için komşu üçgenlerin (alan ağırlıklı) düzlem denklemlerinden
  4x4 quadric hata matrisi birikir (`_build_vertex_quadrics`).
- Her aday kenar birleşimi için birleşik quadric matrisi minimize eden
  optimal nokta 3x3 lineer sistem çözülerek (Cramer kuralı) bulunur
  (`_optimal_collapse_position`); matris tekilse (düzlemsel bölge) uç
  nokta/orta nokta arasından en düşük hatalı seçilir (standart fallback).
- En düşük maliyetli kenar öncelikli olarak birleştirilir
  (`_best_qem_collapse`), hedef üçgen sayısına ulaşana kadar tekrarlanır.
- Genel API imzası (`simplify(mesh, target_triangle_ratio) -> Mesh3D`)
  **değişmedi** — geriye uyumlu.

Eski kenar-uzunluğu tabanlı yöntem `MeshSimplifier.simplify_edgelength_legacy()`
olarak korunuyor — yalnızca
[`../tests/test_meshsimplifier_qem_vs_edgelength.py`](../tests/test_meshsimplifier_qem_vs_edgelength.py)
karşılaştırma/regresyon testi için; üretim kodunda artık kullanılmıyor.

**Kabul kriteri doğrulaması:** gerçek bir bina `full_mesh()`'i (çatı mahyası +
duvar köşeleri gibi keskin özellikler içeren) %20 üçgen oranına
indirgendiğinde, basit nokta-örnekleme ile simetrik Hausdorff yaklaşıklığı
QEM için **7.19**, eski kenar-uzunluğu yöntemi için **9.22** ölçülüyor (düşük
= daha iyi) — QEM ölçülebilir şekilde üstün. Basit bir kutuda (dışbükey,
öznitelik yok) iki yöntem de karşılaştırılabilir sonuç veriyor (regresyon
koruması).

**Sıradaki adım (tamamlandı):** ROADMAP_V3.md'deki Faz D2 (Render Engine ↔
Performance köprüsü) — bu fazın ürettiği LOD seviyeleri D2'nin
`Scene.lod_groups` alanına doğrudan girdi oldu (bkz. `render_engine/
scene_bridge.py`).

## ROADMAP_V4 — Faz E2: Progressive Mesh ve Gerçek Zamanlı LOD Geçiş Animasyonu ✅ TAMAMLANDI

**Kapatılan açık madde:** D2'nin ürettiği ayrık LOD zinciri (ratio=1.0/0.5/
0.25) seviyeler arası **ani** ("pop") geçiş yapıyordu — Hoppe (1996,
"Progressive Meshes") tarzı bir ara-kare (geomorph) mekanizması yoktu.

**Uygulama:** Yeni [`progressive_mesh.py`](./progressive_mesh.py),
`MeshSimplifier`in QEM edge-collapse sırasını (`_best_qem_collapse`) tekrar
kullanarak (API/davranış değişmedi, geriye uyumlu) her birleşimi
(`CollapseStep`: birleşen iki vertex'in eski konumları + optimal birleşim
noktası + öncesi/sonrası üçgen sayısı) sırayla kaydeden bir
`ProgressiveMesh` yapısı sağlıyor:

- `ProgressiveMesh.build(mesh, min_triangle_ratio)` — tam-detaydan hedef
  orana kadar tüm QEM geçmişini kaydeder.
- `mesh_at_step()` / `mesh_at_triangle_count()` — geçmişi ileri/geri
  oynatarak herhangi bir ayrık basitleştirme seviyesini yeniden üretir.
- `interpolate(step_index, t)` — tek bir collapse adımının uygulanmadan
  hemen önceki topolojisini koruyarak, birleşen iki vertex'i `t` (0..1)
  oranında eski konumlarından ortak birleşim noktasına doğru **lineer**
  kaydırır (klasik geomorph: topoloji `t<1` iken sabit kalır, üçgen sayısı
  yalnızca `t=1`'de ayrık olarak düşer).
- `interpolate_between_counts(count_a, count_b, t)` — iki *herhangi* ayrık
  LOD seviyesi arasında (yalnızca ardışık adımlar değil) sürekli geçiş
  üretir; her adımı sırayla oynatıp yalnızca son kısmi adımı geomorph ile
  yumuşatır.

`render_engine/scene_bridge.py` köprüsü: `Scene.add_mesh_with_lod(...,
use_progressive=True)` verilirse `node.progressive` alanına bu geçmiş
gömülür (varsayılan `False` — mevcut davranış/şema tamamen korunur);
`select_lod_mesh_geomorph(node, distance)` bu geçmişi kullanarak iki
mesafe-eşiği arasında ani pop yerine sürekli üçgen-sayısı geçişi üretir
(`node.progressive is None` ise eski `select_lod_for_distance()` ile
bire bir aynı sonucu verir).

**Kabul kriteri doğrulaması:**
[`../tests/test_phaseE2_progressive_mesh.py`](../tests/test_phaseE2_progressive_mesh.py)
(9 test) — ardışık iki LOD seviyesi arasında üretilen tüm ara-mesh'lerin
üçgen sayısının **monoton** azaldığını (asla artmadığını, pop olmadığını),
progressive geçmişin sonunun doğrudan `MeshSimplifier.simplify()` ile aynı
üçgen sayısına ulaştığını ve `use_progressive=False` (varsayılan) davranışın
birebir korunduğunu (geriye uyumluluk) doğruluyor.

**Sıradaki adım:** ROADMAP_V4.md — Faz E3 (Terrain Engine: hidroloji
görselleştirmesi ve gerçek DEM kaynak entegrasyonu).
