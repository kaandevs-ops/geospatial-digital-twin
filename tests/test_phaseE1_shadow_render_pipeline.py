"""
Faz E1 — Render Engine: Gerçek Gölge Haritası, IBL ve Post-Processing Pipeline
===============================================================================

Roadmap V4 Track E, Faz E1. Bu test dosyası, `viewer/index.html`'e eklenen
gerçek zamanlı WebGL2 shadow-map pass'inin ARKA PLANINDAKİ projeksiyon
matematiğini (`compute_light_space_matrix`/`apply_light_space_matrix`,
`render_engine/scene_bridge.py`) doğrular — bu ortamda gerçek bir tarayıcı/
GPU bulunmadığı için (roadmap'in önerdiği "headless canvas-pixel-örnekleme"
testi burada mümkün değil), doğrulama JS ile BİREBİR AYNI matematiği
uygulayan Python referans implementasyonu üzerinden, mevcut `ShadowCalculator.
point_in_shadow` (Faz 6, ray-triangle tabanlı, bağımsız bir algoritma) ile
ÇAPRAZ KONTROL edilerek yapılır: iki farklı yöntem aynı gölge/aydınlık
sonucuna varmalı.

Ayrıca D9 `AmbientOcclusionBaker`'ın artık gerçekten `Scene`'e (ve dolayısıyla
viewer'a) bağlandığını (`Scene.attach_vertex_ao`) ve Faz 2 `HDRSky`'nin
`Scene.sky` üzerinden JSON'a taşındığını doğrular.
"""

from __future__ import annotations

import math

import pytest
from harita.lighting import AmbientOcclusionBaker, HDRSky, ShadowCalculator
from harita.mesh_engine import Mesh3D, Vertex3D
from harita.render_engine import (
    Scene,
    SceneSky,
    apply_light_space_matrix,
    compute_light_space_matrix,
    hdr_sky_to_scene_sky,
)


def _quad_mesh(y: float, half: float = 1.0, name: str = "occluder") -> Mesh3D:
    """y yüksekliğinde, xz düzleminde [-half, half] kare bir engelleyici."""
    verts = [
        Vertex3D(-half, y, -half),
        Vertex3D(half, y, -half),
        Vertex3D(half, y, half),
        Vertex3D(-half, y, half),
    ]
    tris = [(0, 1, 2), (0, 2, 3)]
    return Mesh3D(vertices=verts, triangles=tris, name=name)


class _FakeSun:
    """`ShadowCalculator.point_in_shadow` yalnızca `.direction()` çağırır -
    gerçek `SunLight`'ın ENU/SPA hesaplama zincirinden bağımsız, dogrudan
    render-engine konvansiyonunda (ışık kaynağına doğru, yukarı) bir yön
    enjekte eden sahte ışık."""

    def __init__(self, direction_towards_sun: tuple[float, float, float]):
        self._dir = direction_towards_sun

    def direction(self) -> tuple[float, float, float]:
        return self._dir


# ======================================================================== #
# 1) Işık-uzayı projeksiyonu <-> ShadowCalculator çapraz doğrulaması
# ======================================================================== #


class TestLightSpaceProjectionMatchesRayCast:
    """`compute_light_space_matrix` ile üretilen shadow-map derinlik
    karşılaştırma mantığının, bağımsız ray-triangle `ShadowCalculator` ile
    aynı gölge/aydınlık kararına vardığını doğrular."""

    def setup_method(self) -> None:
        self.bounds = ((-5.0, -5.0, -5.0), (5.0, 5.0, 5.0))
        # Güneş tam tepede: ışın sahneye doğru AŞAĞI (0,-1,0) gidiyor.
        self.light_direction_going = (0.0, -1.0, 0.0)
        self.occluder = _quad_mesh(y=2.0, half=1.0)

    def test_point_directly_under_occluder_is_in_shadow(self) -> None:
        matrix = compute_light_space_matrix(self.light_direction_going, self.bounds)
        occluder_pt = (0.0, 2.0, 0.0)
        ground_pt = (0.0, 0.0, 0.0)

        z_occluder = apply_light_space_matrix(matrix, occluder_pt)[2]
        z_ground = apply_light_space_matrix(matrix, ground_pt)[2]

        # Shadow-map mantığı: aynı (x,y) sütununda, sahnenin gerçek derinliği
        # (z_ground), haritaya kaydedilmiş en yakın-yüzey derinliğinden
        # (z_occluder) DAHA UZAKTA ise nokta gölgede demektir.
        assert z_ground > z_occluder, (
            "Engelleyicinin altındaki nokta, ışık-uzayında engelleyiciden "
            "daha uzakta (gölgede) olmalı."
        )

        # Bağımsız doğrulama: ray-triangle tabanlı ShadowCalculator da aynı
        # sonuca varmalı (ışın güneşe doğru = yukarı = (0,1,0)).
        fake_sun = _FakeSun((0.0, 1.0, 0.0))
        assert (
            ShadowCalculator.point_in_shadow(
                ground_pt,
                fake_sun,
                [self.occluder],
                max_distance=50.0,
            )
            is True
        )

    def test_point_outside_occluder_footprint_is_lit(self) -> None:
        matrix = compute_light_space_matrix(self.light_direction_going, self.bounds)
        far_pt = (4.0, 0.0, 4.0)

        # far_pt, engelleyicinin xz-izdüşümü [-1,1]x[-1,1] dışında -
        # ışık-uzayı x/y'si engelleyicininkinden belirgin şekilde farklı
        # olmalı (yani shadow-map dokusunda farklı bir piksele düşer).
        ndc_far = apply_light_space_matrix(matrix, far_pt)
        ndc_occluder = apply_light_space_matrix(matrix, (0.0, 2.0, 0.0))
        assert math.dist(ndc_far[:2], ndc_occluder[:2]) > 0.3

        fake_sun = _FakeSun((0.0, 1.0, 0.0))
        assert (
            ShadowCalculator.point_in_shadow(
                far_pt,
                fake_sun,
                [self.occluder],
                max_distance=50.0,
            )
            is False
        )

    def test_matrix_changes_with_sun_direction(self) -> None:
        """Kabul kriteri: 'güneş konumu değiştirildiğinde gölgenin yönü
        gerçek zamanlı değişir' - ışık yönü değiştiğinde aynı dünya
        noktasının ışık-uzayı izdüşümü de değişmeli (statik/donmuş bir
        gölge değil)."""
        m_noon = compute_light_space_matrix((0.0, -1.0, 0.0), self.bounds)
        m_afternoon = compute_light_space_matrix((-0.6, -0.7, 0.0), self.bounds)

        pt = (1.0, 0.0, 1.0)
        proj_noon = apply_light_space_matrix(m_noon, pt)
        proj_afternoon = apply_light_space_matrix(m_afternoon, pt)
        assert proj_noon != pytest.approx(proj_afternoon, abs=1e-6)


# ======================================================================== #
# 2) D9 AmbientOcclusionBaker -> Scene köprüsü (denetim maddesi kapatılıyor)
# ======================================================================== #


class TestVertexAOBridge:
    def test_attach_vertex_ao_matches_baker_directly(self) -> None:
        mesh = _quad_mesh(y=0.0, half=2.0, name="floor")
        scene = Scene()
        node = scene.add_mesh(mesh)

        expected = AmbientOcclusionBaker.bake_vertex_ao(mesh, sample_count=8, max_distance=5.0)
        got = scene.attach_vertex_ao(node, sample_count=8, max_distance=5.0)

        assert got == expected
        assert scene.vertex_ao["floor"] == expected

    def test_ao_serialized_in_to_dict(self) -> None:
        mesh = _quad_mesh(y=0.0, half=1.0, name="floor2")
        scene = Scene()
        node = scene.add_mesh(mesh)
        scene.attach_vertex_ao(node)

        d = scene.to_dict()
        node_json = next(n for n in d["nodes"] if n["name"] == "floor2")
        assert node_json["ao"] is not None
        assert len(node_json["ao"]) == mesh.vertex_count()
        assert all(0.0 <= v <= 1.0 for v in node_json["ao"])

    def test_node_without_ao_serializes_none_backward_compatible(self) -> None:
        mesh = _quad_mesh(y=0.0, half=1.0, name="floor3")
        scene = Scene()
        scene.add_mesh(mesh)
        d = scene.to_dict()
        assert d["nodes"][0]["ao"] is None

    def test_attach_vertex_ao_rejects_foreign_node(self) -> None:
        scene_a = Scene()
        scene_b = Scene()
        node = scene_a.add_mesh(_quad_mesh(y=0.0, name="a"))
        with pytest.raises(ValueError):
            scene_b.attach_vertex_ao(node)

    def test_ao_length_mismatch_raises_on_serialize(self) -> None:
        mesh = _quad_mesh(y=0.0, name="mismatch")
        scene = Scene()
        node = scene.add_mesh(mesh)
        scene.attach_vertex_ao(node)
        # Mesh, bake sonrası kazara değiştirilirse (ör. yeni vertex eklenirse)
        # to_dict() sessizce yanlış veriyle devam etmemeli.
        scene.vertex_ao["mismatch"] = scene.vertex_ao["mismatch"] + [0.5]
        with pytest.raises(ValueError):
            scene.to_dict()


# ======================================================================== #
# 3) Faz 2 HDRSky -> Scene.sky köprüsü
# ======================================================================== #


class TestSkyBridge:
    def test_hdr_sky_to_scene_sky_roundtrip(self) -> None:
        sky = HDRSky(zenith_color=(0.1, 0.2, 0.9), horizon_color=(0.9, 0.8, 0.7), turbidity=3.5)
        scene_sky = hdr_sky_to_scene_sky(sky)
        assert isinstance(scene_sky, SceneSky)
        assert scene_sky.zenith_color == sky.zenith_color
        assert scene_sky.horizon_color == sky.horizon_color
        assert scene_sky.turbidity == sky.turbidity

    def test_scene_without_sky_serializes_none_backward_compatible(self) -> None:
        scene = Scene()
        scene.add_mesh(_quad_mesh(y=0.0, name="x"))
        d = scene.to_dict()
        assert d["sky"] is None

    def test_scene_with_sky_serializes_full_payload(self) -> None:
        scene = Scene()
        scene.add_mesh(_quad_mesh(y=0.0, name="x"))
        scene.sky = hdr_sky_to_scene_sky(HDRSky())
        d = scene.to_dict()
        assert d["sky"]["zenith_color"] == list(HDRSky().zenith_color)
        assert d["sky"]["horizon_color"] == list(HDRSky().horizon_color)

    def test_schema_version_bumped(self) -> None:
        from harita.render_engine import SCENE_SCHEMA_VERSION

        major, minor = SCENE_SCHEMA_VERSION.split(".")
        assert (int(major), int(minor)) >= (1, 2)
