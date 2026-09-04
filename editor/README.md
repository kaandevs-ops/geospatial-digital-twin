# editor (Phase 8) — ✅ Uygulandı

Roadmap Phase 8 - "EDITOR": kendi Blender-benzeri düzenleyicin.

## İçerik

- **`commands.py`** — `EditorCommand` (soyut komut sözleşmesi), `FunctionCommand`
  (closure tabanlı genel komut), `CommandGroup` (birden fazla komutu tek
  undo/redo adımında gruplar), `UndoRedoStack` (sınırlı geçmişli undo/redo
  yığını, `history_labels()` ile bir "History" paneli beslenebilir).
- **`object_editor.py`** — `Vec3`, `SceneNode` (parent/children hiyerarşisi,
  local transform, `bake()` ile world-space mesh üretimi), `Prefab` /
  `PrefabLibrary`, `ObjectEditor` (move/rotate/scale/align/mirror/snap/
  duplicate/group/ungroup/instantiate_prefab/flatten).
- **`terrain_editor.py`** — `Brush` (dairesel, smoothstep falloff'lu fırça),
  `TerrainPaintLayer` (doku karışım ağırlığı grid'i), `TerrainEditor`
  (raise/lower/flatten/smooth/add_noise/paint — hepsi `terrain_engine.
  HeightmapGrid` üzerinde, yalnızca dokunulan hücreleri undo eden komutlar
  üretir).
- **`road_editor.py`** — `catmull_rom_spline` (bağımsız, test edilebilir
  fonksiyon), `Road` (kontrol noktaları + `to_mesh()` şerit extrusion),
  `RoadEditor` (add/move/remove point, set_width — komut deseni).
- **`building_editor.py`** — `BuildingEditor` (add/remove floor, change_roof
  ve change_facade — Phase 3 `RoofGenerator`/`FacadeGenerator`'ı yeniden
  çağırarak geometriyi günceller — add/remove door/window).
- **`gizmo.py`** *(Roadmap V3 - Faz D5)* — `Ray`, `TranslateGizmo`,
  `RotateGizmo`, `ScaleGizmo`: render-bağımsız, saf ray-geometrisi
  (skew-line en-yakın-nokta / ray-plane kesişimi) ile tek-eksenli
  öteleme/rotasyon/ölçek hesaplamaları.
- **`input_bindings.py`** *(Roadmap V3 - Faz D5)* — `GizmoInputSession`
  (mouse-down -> N x mouse-move canlı önizleme -> mouse-up = TEK undo
  adımı durum makinesi), `KeyBindingRegistry` (G/R/S mod kısayolları,
  Ctrl+Z/Y undo/redo), `MouseDownEvent`/`MouseMoveEvent`/`MouseUpEvent`.

## Testler

`tests/test_phase8_editor.py` — 46 test, tüm alt modülleri kapsar.
`tests/test_phaseD5_editor_gizmo_input.py` *(Roadmap V3 - Faz D5)* — 14 test:
gizmo matematiği (translate/rotate/scale doğruluğu), tam mouse-down/move/up
yaşam döngüsü (A8 kabul kriteri: "5 birim sürükle" senaryosu), ara
mouse-move'ların undo yığınına ayrı ayrı girmediğinin doğrulanması,
`cancel()` (Esc) davranışı, `KeyBindingRegistry` varsayılan kısayolları.

Tam teknik spesifikasyon için bkz: [`../docs/PHASE_SPECS.md`](../docs/PHASE_SPECS.md)

## Roadmap V3 — Faz D5: Input-Binding + Gizmo Matematiği — ✅ Tamamlandı

**Önceki kısıt:** `ObjectEditor.move/rotate/scale` (mantık) ve gizmo
matematiği (ray -> hareket) ayrı ayrı mevcuttu, ama ikisini gerçek bir
mouse etkileşim dizisine (down->move->up) bağlayan, undo/redo ile doğru
bütünleşen bir katman yoktu - editör "mantık var, kullanılamıyor"
durumundaydı.

**Çözüm:** `GizmoInputSession`, sürükleme sırasında `SceneNode`'u canlı
günceller (önizleme) ama undo/redo yığınına yalnızca mouse-up'ta TEK bir
komut ekler (standart DCC davranışı - Blender/Unity ile aynı). `cancel()`
(Esc) sürüklemeyi undo kaydı bırakmadan geri alır. `KeyBindingRegistry`
render/DOM'dan bağımsız, genişletilebilir bir kısayol tablosu sağlar.

