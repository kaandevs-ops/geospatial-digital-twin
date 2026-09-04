"""
ai_reconstruction.material_bridge testleri - kullanıcı isteğiyle işlenen
kapsam dışı kalem: "gerçek doku ataması" (bina tarafı).

Sandbox ağ erişimi ambientcg.com'a izin vermediği için bu testler gerçek
ağ çağrısı yapmaz; `PBRMaterialLibrary.get()`'in kendi test dosyasıyla
(`test_faz1_4_external_material_library.py`) aynı ilke: offline'da
`source == "procedural"` fallback'inin çalıştığı ve hiçbir zaman exception
fırlatmadığı doğrulanır. Köprünün kendi sorumluluğu (tahmin sözlüğü ->
`Scene.add_mesh` ile uyumlu materyal sözlüğü) buna ek olarak test edilir.
"""

from __future__ import annotations

from harita.ai_reconstruction.material_bridge import (
    DEFAULT_MATERIAL_CACHE_DIR,
    resolve_building_materials,
    resolve_pbr_material,
)
from harita.ai_reconstruction.material_predictor import (
    AIMaterialPredictor,
    MaterialPrediction,
    SurfaceClass,
)
from harita.building_reconstruction.facade_generator import FacadeMaterial
from harita.material_engine import PBRMaterial


def _prediction(material: FacadeMaterial, confidence: float = 0.75) -> MaterialPrediction:
    return MaterialPrediction(
        surface_class=SurfaceClass.DUVAR, material=material, confidence=confidence
    )


def test_default_material_cache_dir_uses_dot_harita_convention():
    assert str(DEFAULT_MATERIAL_CACHE_DIR).endswith(".harita/material_cache") or str(
        DEFAULT_MATERIAL_CACHE_DIR
    ).endswith(".harita\\material_cache")


def test_resolve_pbr_material_never_raises_offline(tmp_path):
    """Sandbox'ta ağ yok - `resolve_pbr_material` yine de exception
    fırlatmadan (bkz. PBRMaterialLibrary'nin kendi garantisi) prosedürel
    fallback'e düşmeli."""
    prediction = _prediction(FacadeMaterial.BETON)
    material, source = resolve_pbr_material(prediction, cache_dir=tmp_path / "cache")
    assert isinstance(material, PBRMaterial)
    assert source in ("cache", "network", "procedural")
    # Ağsız sandbox'ta gerçekçi beklenti: procedural.
    assert source == "procedural"


def test_resolve_pbr_material_returns_material_matching_type(tmp_path):
    prediction = _prediction(FacadeMaterial.TUGLA)
    material, _source = resolve_pbr_material(prediction, cache_dir=tmp_path / "cache")
    assert "tugla" in material.name.lower()


def test_resolve_building_materials_empty_predictions_returns_empty_dict(tmp_path):
    assert resolve_building_materials({}, cache_dir=tmp_path / "cache") == {}


def test_resolve_building_materials_covers_all_surface_classes(tmp_path):
    predictor = AIMaterialPredictor()
    predictions = predictor.predict_all_surfaces(building_type="office")
    materials = resolve_building_materials(predictions, cache_dir=tmp_path / "cache")
    assert set(materials.keys()) == {sc.value for sc in SurfaceClass}
    for mat in materials.values():
        assert isinstance(mat, PBRMaterial)


def test_resolve_building_materials_is_directly_usable_by_scene_bridge(tmp_path):
    """`Scene.add_mesh(mesh, material=...)` ile doğrudan tüketilebilirlik -
    döndürülen değerlerin gerçek `PBRMaterial` nesneleri olduğu ve
    `Scene.to_dict()`'in bunları serileştirebildiği regresyon kontrolü."""
    from harita.mesh_engine import MeshBuilder
    from harita.render_engine.scene_bridge import Scene

    predictor = AIMaterialPredictor()
    predictions = predictor.predict_all_surfaces(building_type="house")
    materials = resolve_building_materials(predictions, cache_dir=tmp_path / "cache")

    scene = Scene()
    mesh = MeshBuilder.build_box(width=5.0, depth=5.0, height=3.0, name="wall")
    wall_material = materials[SurfaceClass.DUVAR.value]
    scene.add_mesh(mesh, material=wall_material)
    data = scene.to_dict()
    assert wall_material.name in data["materials"]


def test_resolve_building_materials_shares_single_library_instance(tmp_path, monkeypatch):
    """Bir binanın N yüzeyi için index.json N kez değil (paylaşılan
    `PBRMaterialLibrary` sayesinde) yalnızca gerekli oldukça okunmalı -
    burada davranışsal kanıt: `PBRMaterialLibrary` yalnızca bir kez
    örnekleniyor mu (constructor çağrı sayısı)."""
    import harita.ai_reconstruction.material_bridge as bridge

    call_count = {"n": 0}
    original_init = bridge.PBRMaterialLibrary.__init__

    def counting_init(self, *args, **kwargs):
        call_count["n"] += 1
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(bridge.PBRMaterialLibrary, "__init__", counting_init)

    predictor = AIMaterialPredictor()
    predictions = predictor.predict_all_surfaces(building_type="industrial")
    resolve_building_materials(predictions, cache_dir=tmp_path / "cache")
    assert call_count["n"] == 1
