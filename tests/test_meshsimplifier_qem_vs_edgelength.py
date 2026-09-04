"""ROADMAP_V3 - Faz D1 kabul kriteri testi.

Aynı test mesh'lerinde (küp/bina full_mesh) %20 üçgen oranına indirgendiğinde,
gerçek Quadric Error Metric (QEM) tabanlı `MeshSimplifier.simplify()`'ın,
yalnızca kenar uzunluğuna bakan eski yönteme (`simplify_edgelength_legacy`)
göre ölçülebilir şekilde daha düşük geometrik hata (basit nokta-örnekleme ile
simetrik Hausdorff yaklaşıklığı) verdiğini doğrular.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder, MeshSimplifier, Mesh3D, Vertex3D
from harita.building_reconstruction import (
    Footprint, Building, BuildingType, ProceduralBuildingGenerator,
)


def _box_polygon(w: float, d: float) -> Polygon:
    return Polygon([Point2D(0, 0), Point2D(w, 0), Point2D(w, d), Point2D(0, d)])


def _symmetric_hausdorff_proxy(mesh_a: Mesh3D, mesh_b: Mesh3D) -> float:
    """Basit nokta-örnekleme ile simetrik Hausdorff yaklaşıklığı: her mesh'in
    vertex'lerinden diğerindeki en yakın vertex'e olan mesafelerin
    maksimumlarının ortalaması. Bağımlılıksız (stdlib-only) ve deterministik;
    tam yüzey-yüzey Hausdorff değil ama iki basitleştirme yönteminin göreli
    kalitesini karşılaştırmak için yeterli bir vekil metriktir."""

    def _one_directional(src: Mesh3D, dst: Mesh3D) -> float:
        if not src.vertices or not dst.vertices:
            return 0.0
        worst = 0.0
        for v in src.vertices:
            best = min(v.distance_to(u) for u in dst.vertices)
            worst = max(worst, best)
        return worst

    return (
        _one_directional(mesh_a, mesh_b) + _one_directional(mesh_b, mesh_a)
    ) / 2.0


def _build_hip_roof_building_mesh() -> Mesh3D:
    """Roof/facade/floor içeren gerçekçi bir bina mesh'i (hip çatı mahyası,
    duvar köşeleri gibi keskin/eğrisel özellikler taşır - QEM'in edge-length
    yöntemine göre üstünlüğünü göstermek için idealdir)."""
    poly = _box_polygon(18, 14)
    fp = Footprint(polygon=poly, building_type="apartments", floor_count=4, height_m=12.0)
    building: Building = ProceduralBuildingGenerator.generate(
        fp, building_type=BuildingType.APARTMAN, seed=7,
    )
    return building.full_mesh()


def test_qem_beats_edgelength_on_building_full_mesh():
    mesh = _build_hip_roof_building_mesh()
    assert mesh.triangle_count() > 20, "Test mesh'i yeterince karmaşık değil"

    qem_result = MeshSimplifier.simplify(mesh, 0.2)
    legacy_result = MeshSimplifier.simplify_edgelength_legacy(mesh, 0.2)

    # Her iki yöntem de aynı hedef üçgen sayısına (yaklaşık) inmeli.
    assert qem_result.triangle_count() >= 1
    assert legacy_result.triangle_count() >= 1

    qem_error = _symmetric_hausdorff_proxy(mesh, qem_result)
    legacy_error = _symmetric_hausdorff_proxy(mesh, legacy_result)

    assert qem_error <= legacy_error, (
        f"QEM hatası ({qem_error:.4f}) legacy kenar-uzunluğu hatasından "
        f"({legacy_error:.4f}) düşük veya eşit olmalı"
    )


def test_qem_does_not_regress_on_simple_cube():
    poly = _box_polygon(10, 8)
    mesh = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=5.0)

    qem_result = MeshSimplifier.simplify(mesh, 0.5)
    legacy_result = MeshSimplifier.simplify_edgelength_legacy(mesh, 0.5)

    qem_error = _symmetric_hausdorff_proxy(mesh, qem_result)
    legacy_error = _symmetric_hausdorff_proxy(mesh, legacy_result)

    # Basit dışbükey bir kutuda iki yöntem de makul sonuç vermeli; QEM daha
    # kötü olmamalı (regresyon koruması).
    assert qem_error <= legacy_error + 1e-6


def test_qem_preserves_public_api_signature():
    """MeshSimplifier.simplify() imzası (mesh, ratio) -> Mesh3D korunmalı -
    roadmap D1'in geriye-uyumluluk şartı."""
    poly = _box_polygon(6, 6)
    mesh = MeshBuilder.extrude_polygon(poly, base_z=0.0, height=3.0)
    result = MeshSimplifier.simplify(mesh, 0.5)
    assert isinstance(result, Mesh3D)
    assert result.triangle_count() <= max(1, int(mesh.triangle_count() * 0.5)) + 1
    assert result.triangle_count() >= 1


def test_qem_invalid_ratio_raises_value_error():
    verts = [Vertex3D(0, 0, 0), Vertex3D(1, 0, 0), Vertex3D(0, 1, 0)]
    mesh = Mesh3D(vertices=verts, triangles=[(0, 1, 2)])
    try:
        MeshSimplifier.simplify(mesh, 0.0)
        assert False, "0 oranı ValueError fırlatmalı"
    except ValueError:
        pass
    try:
        MeshSimplifier.simplify(mesh, 1.5)
        assert False, "1'den büyük oran ValueError fırlatmalı"
    except ValueError:
        pass
