"""
Render Engine — Scene Bridge
=============================

Roadmap Phase 15 - "Render Engine". Bu modül, bir render/GPU KATMANI değildir;
önceki fazların ürettiği verinin (Phase 2 `Mesh3D`, Phase 2 `PBRMaterial`,
Phase 2 `SunLight`/`AmbientLight`, Phase 5 `DigitalTwin`) GPU-dostu, JSON
serileştirilebilir bir "sahne" (`Scene`) temsiline dönüştüren KÖPRÜdür.

Neden ayrı bir dosya/paket:
    - Python tarafında gerçek bir GPU pipeline'ı yok (stdlib-only ilkesi).
    - Ekrana çizim işi `viewer/index.html` içindeki WebGL2 renderer'a aittir.
    - Bu modülün tek sorumluluğu: Mesh3D/material/light nesnelerini, viewer'ın
      doğrudan `fetch()` ile okuyup GPU buffer'larına yükleyebileceği düz,
      indexed-triangle tabanlı bir JSON'a (bkz. `SCENE_SCHEMA_VERSION`)
      kayıpsız şekilde aktarmaktır.

Bağımlılık: yalnızca stdlib (json, dataclasses, math).
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..mesh_engine import Mesh3D, MeshSimplifier
from ..mesh_engine.progressive_mesh import ProgressiveMesh
from ..material_engine import PBRMaterial
from ..data_engine.spatial_index import AABB3D
from ..performance.culling import FrustumCulling

SCENE_SCHEMA_VERSION = "1.5"  # Roadmap V10 Faz 5: "overlay_layers"/"audio_events" alanları eklendi

# ======================================================================== #
# Faz E2 — Mesh Engine <-> Render Engine Köprüsü (Progressive Mesh / Geomorph)
# ======================================================================== #
#
# Mevcut kısıt (D2 sonrası): `add_mesh_with_lod()` yalnızca 2-3 sabit,
# ayrık LOD seviyesi (ratio=1.0/0.5/0.25) üretiyordu; kamera bu seviyeler
# arasındaki mesafe eşiğini geçtiğinde `select_lod_for_distance()` ANİ bir
# mesh değişimi (pop) döndürüyordu. Faz E2, `mesh_engine.progressive_mesh`
# (Hoppe 1996 tarzı vertex-split geçmişi) kullanarak bu iki ayrık seviye
# arasında sürekli (geometrik) bir ara-mesh üretebilen
# `select_lod_mesh_geomorph()`'u ekler. Varsayılan davranış DEĞİŞMEDİ:
# `use_progressive=False` (varsayılan) ile `add_mesh_with_lod()` öncekiyle
# birebir aynı sonucu üretir - geriye uyumlu.

# ======================================================================== #
# Faz D2 — Render Engine <-> Performance Köprüsü
# ======================================================================== #
#
# Roadmap V3 Faz D2: Faz 13 (`performance`) LOD/culling sınıfları şu ana
# kadar hiçbir yerde `Scene`'e bağlanmıyordu. Bu bölüm iki şeyi yapar:
#
#   1) `Scene.add_mesh_with_lod()` - D1'in ürettiği gerçek QEM tabanlı
#      `MeshSimplifier.simplify()` ile her node için 2-3 seviyeli bir LOD
#      zinciri üretip `SceneNode.lod_levels`'e gömer; `to_dict()` bunları
#      `lod_groups` olarak JSON'a yazar, viewer kamera-mesafesine göre hangi
#      seviyeyi çizeceğine kare başına karar verir (bkz. viewer/index.html).
#   2) `visible_node_names()` - Faz 13 `FrustumCulling`'i `Scene` node'larının
#      world-space AABB'lerine uygulayan köprü fonksiyonu: Python tarafında
#      hazırlanmış statik/başlangıç görünürlük listesi üretir (tam gerçek
#      zamanlı culling viewer'da JS ile yapılır - bkz. modül üstü docstring).
#
# Varsayılan LOD zinciri: tam detay (ratio=1.0, hiç basitleştirme yok),
# 0-DEFAULT_LOD_DISTANCES[0] birim; sonra %50 (ratio=0.5); sonra %25
# (ratio=0.25) sonsuza kadar. Bu değerler `add_mesh_with_lod()`'a parametre
# olarak geçilip özelleştirilebilir.
DEFAULT_LOD_RATIOS: tuple[float, ...] = (1.0, 0.5, 0.25)
DEFAULT_LOD_DISTANCES: tuple[float, ...] = (50.0, 150.0, math.inf)


# ======================================================================== #
# Sahne veri modeli
# ======================================================================== #

@dataclass(slots=True)
class SceneNode:
    """Tek bir çizilebilir nesne: bir Mesh3D + materyal referansı + transform."""

    name: str
    mesh: Mesh3D
    material_name: str | None = None
    # column-major olmayan, basit TRS (translate/rotate-euler-deg/scale)
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    # Faz D2: (max_distance, mesh) çiftleri, artan max_distance sırasıyla;
    # None ise node LOD'suz (her zaman `mesh` çizilir - geriye uyumlu).
    lod_levels: list[tuple[float, Mesh3D]] | None = None
    # Faz E2: dolu ise `lod_levels`'daki ardışık seviyeler arasında
    # `select_lod_mesh_geomorph()` ile ani "pop" yerine sürekli geçiş
    # üretilebilir. None ise (varsayılan/geriye uyumlu) yalnızca ayrık
    # `select_lod_for_distance()` davranışı geçerlidir.
    progressive: ProgressiveMesh | None = None


@dataclass(slots=True)
class SceneLight:
    kind: str  # "directional" | "ambient" | "point"
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    intensity: float = 1.0
    direction: tuple[float, float, float] | None = None  # directional için
    position: tuple[float, float, float] | None = None  # point için


@dataclass(slots=True)
class SceneSky:
    """Faz E1 — Render Engine: Faz 2 `HDRSky`'nin viewer'daki ilk görsel
    karşılığı. `hdri_path` şu an yalnızca bilgi amaçlı taşınır (gerçek bir
    equirectangular HDR dosyası çözümü Faz E1 kapsamında değil - stdlib-only
    ilkesi gereği yalnızca ufuk/zenit gradyanı gerçek zamanlı render edilir);
    dosya verilmemişse zaten `HDRSky.sample_direction()` de aynı fallback'e
    düşüyor, viewer bunu birebir yansıtır."""

    zenith_color: tuple[float, float, float] = (0.3, 0.5, 0.9)
    horizon_color: tuple[float, float, float] = (0.8, 0.85, 0.9)
    turbidity: float = 2.0
    hdri_path: str | None = None


def _world_space_mesh(node: "SceneNode") -> Mesh3D:
    """ROADMAP_V8 Faz 6.4: `node.mesh`'in `translation`/`scale`/
    `rotation_deg`'ini uygulayıp dünya-uzayı vertex pozisyonlarına sahip
    yeni bir `Mesh3D` klonu döndürür (girdi değişmez). Yalnızca
    `attach_scene_vertex_ao` içinde, binalar-arası AO hesaplaması için
    kullanılır — rotasyonun yalnızca Z (yaw) bileşeni uygulanır
    (`mesh_engine.batching._apply_transform` ile aynı 2.5D
    basitleştirme; binalar için roll/pitch anlamlı değil)."""
    mesh = node.mesh.clone()
    tx, ty, tz = node.translation
    sx, sy, sz = node.scale
    yaw_deg = node.rotation_deg[2] if len(node.rotation_deg) > 2 else 0.0
    rad = math.radians(yaw_deg)
    c, s = math.cos(rad), math.sin(rad)
    for v in mesh.vertices:
        x, y, z = v.x * sx, v.y * sy, v.z * sz
        if yaw_deg:
            x, y = c * x - s * y, s * x + c * y
        v.x, v.y, v.z = x + tx, y + ty, z + tz
        # Normal de (varsa) yaw ile birlikte döndürülmeli, aksi halde
        # hemisphere-sampling yanlış yönde örnekleme yapar (AO hatalı
        # çıkar) — konum dönüyor ama normal dönmüyorsa tutarsız olur.
        if v.normal is not None and yaw_deg:
            nx, ny, nz = v.normal
            v.normal = (c * nx - s * ny, s * nx + c * ny, nz)
    return mesh


@dataclass(slots=True)
class Scene:
    nodes: list[SceneNode] = field(default_factory=list)
    materials: dict[str, PBRMaterial] = field(default_factory=dict)
    lights: list[SceneLight] = field(default_factory=list)
    name: str = "scene"
    # Faz E1: opsiyonel gökyüzü (HDRSky köprüsü) - None ise viewer düz renk
    # arka plana (mevcut davranış) düşer, geriye uyumlu.
    sky: "SceneSky | None" = None
    # Faz E1: node adı -> vertex başına AO (0..1) listesi. `attach_vertex_ao()`
    # ile doldurulur; boşsa viewer AO=1.0 (etkisiz) varsayar - geriye uyumlu.
    vertex_ao: dict[str, list[float]] = field(default_factory=dict)
    # Faz E3: node adı -> akış ağı (dere/nehir) çizgi segmentleri listesi.
    # `attach_flow_network()` ile doldurulur; boşsa viewer hiçbir akış
    # katmanı çizmez (geriye uyumlu). Her segment: dünya-uzayı (x, y, z)
    # başlangıç/bitiş noktası çifti + o hücrenin normalize akümülasyon
    # yoğunluğu (0..1, çizgi kalınlığı/rengi için viewer'a ipucu).
    flow_lines: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Roadmap V9 / OMURGA / O.2: "Render/Viewer Köprüsü — Agent Katmanı".
    # `SimulationRecorder`'ın kaydettiği keyframe'lerin viewer-dostu, düz
    # JSON temsili. Her eleman: {"t": float, "agents": [{"id", "x", "y",
    # "state"}, ...]}. Boş liste (varsayılan) ise viewer hiçbir agent
    # katmanı çizmez ve zaman çizelgesi kontrolünü gizler (geriye uyumlu —
    # mevcut statik sahneler etkilenmez). `push_agent_frame()` /
    # `push_agent_recording()` ile doldurulur.
    agent_frames: list[dict[str, Any]] = field(default_factory=list)
    # Roadmap V10 / Faz 5 (5.1, 5.2, 5.3, 5.5): render-agnostik senaryo
    # overlay'leri (yangın/duman sprite'ları, heatmap hücreleri, acil
    # müdahale araç ikonları, trafik araç kareleri) - hepsi
    # `visualization.scenario_visual_bridge`'in ürettiği düz dict'lerdir.
    # Boş liste (varsayılan) ise viewer hiçbir overlay katmanı çizmez
    # (geriye uyumlu). `push_overlay_layer()` ile doldurulur.
    overlay_layers: list[dict[str, Any]] = field(default_factory=list)
    # Roadmap V10 / Faz 5.6: `visualization.spatial_audio`'nun ürettiği
    # ses-olay sözleşmesi (efekt kimliği + opsiyonel 3D konum + kazanç
    # parametreleri). Boş liste (varsayılan) ise viewer hiçbir ses
    # tetiklemez (geriye uyumlu). `push_audio_event()` ile doldurulur.
    audio_events: list[dict[str, Any]] = field(default_factory=list)

    # -- inşa yardımcıları ------------------------------------------- #
    def add_mesh(
        self,
        mesh: Mesh3D,
        *,
        material: PBRMaterial | None = None,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
    ) -> SceneNode:
        mat_name = None
        if material is not None:
            mat_name = material.name
            self.materials[mat_name] = material
        node = SceneNode(
            name=mesh.name,
            mesh=mesh,
            material_name=mat_name,
            translation=translation,
            rotation_deg=rotation_deg,
            scale=scale,
        )
        self.nodes.append(node)
        return node

    def add_light(self, light: SceneLight) -> None:
        self.lights.append(light)

    def attach_vertex_ao(
        self,
        node: SceneNode,
        *,
        sample_count: int = 8,
        max_distance: float = 5.0,
    ) -> list[float]:
        """Faz E1: Faz 2 `lighting.AmbientOcclusionBaker.bake_vertex_ao()`
        (mevcut, gerçek hemisphere-sampling AO algoritması) çağrılır ve
        sonuç `self.vertex_ao[node.name]`'e gömülür - `to_dict()` bunu
        `ao` anahtarıyla node JSON'una yazar, viewer ambient terimini bu
        değerle çarpar. Önceden hiçbir yerde ekrana bağlanmıyordu (denetim
        maddesi); bu artık gerçek bir GPU tüketici zinciri kurar.

        Yalnızca `node` bu `Scene`'e ait ise (bkz. `self.nodes`) çalışır;
        aksi halde `ValueError`.
        """
        if node not in self.nodes:
            raise ValueError(
                f"'{node.name}' bu Scene'e ait değil - önce add_mesh()/"
                "add_mesh_with_lod() ile ekleyin."
            )
        # Döngüsel import'tan kaçınmak için geç import (lighting -> ... zinciri
        # render_engine'i içermiyor ama simetri/gelecek-güvenlik için lokal).
        from ..lighting import AmbientOcclusionBaker

        ao_values = AmbientOcclusionBaker.bake_vertex_ao(
            node.mesh, sample_count=sample_count, max_distance=max_distance,
        )
        self.vertex_ao[node.name] = ao_values
        return ao_values

    def attach_scene_vertex_ao(
        self,
        nodes: list[SceneNode] | None = None,
        *,
        sample_count: int = 8,
        max_distance: float = 5.0,
        cell_size: float | None = None,
    ) -> dict[str, list[float]]:
        """ROADMAP_V8 Faz 6.4 (şehir-ölçeği kalan madde): `attach_vertex_ao`
        her node'u KENDİ mesh-lokal uzayında, sahnedeki diğer node'lardan
        tamamen habersiz olarak hesaplıyordu — komşu bir binanın alt katı,
        yanındaki (world-space'te bitişik) başka bir binanın gölgesinden
        hiç etkilenmiyordu (denetimde bulunan gerçek boşluk: `lighting.
        SceneAOBaker`/`SpatialHashGrid` zaten yazılmıştı ama hiçbir
        yerden çağrılmıyordu). Bu metod, verilen node'ların mesh'lerini
        (`translation`/`scale`/`rotation_deg`'in yalnızca Z bileşeni —
        binalar için yeterli, `mesh_engine.batching.InstanceTransform`
        ile aynı 2.5D basitleştirme) dünya-uzayına taşıyıp
        `SceneAOBaker.bake_scene_ao` ile TEK bir uzamsal-hash üzerinde
        (`SpatialHashGrid`) birlikte hesaplar; sonucu her node için
        (mesh-lokal vertex sırasına denk gelen) `self.vertex_ao`'ya
        yazar — `attach_vertex_ao` ile birebir aynı sözleşme, tek fark
        binalar-arası gölgelemenin de hesaba katılması.

        `nodes=None` ise `self.nodes`'un tamamı kullanılır. Opt-in;
        mevcut `attach_vertex_ao` davranışı hiç değişmedi (ayrı bir
        metod, geriye dönük tam uyumlu).
        """
        target_nodes = nodes if nodes is not None else self.nodes
        for node in target_nodes:
            if node not in self.nodes:
                raise ValueError(
                    f"'{node.name}' bu Scene'e ait değil - önce add_mesh()/"
                    "add_mesh_with_lod() ile ekleyin."
                )
        if not target_nodes:
            return {}

        from ..lighting import SceneAOBaker

        world_meshes = [_world_space_mesh(node) for node in target_nodes]
        ao_lists = SceneAOBaker.bake_scene_ao(
            world_meshes, sample_count=sample_count, max_distance=max_distance,
            cell_size=cell_size,
        )
        result: dict[str, list[float]] = {}
        for node, ao_values in zip(target_nodes, ao_lists):
            self.vertex_ao[node.name] = ao_values
            result[node.name] = ao_values
        return result

    def attach_flow_network(
        self,
        node: SceneNode,
        grid: "HeightmapGrid",
        *,
        accumulation_threshold: float = 4.0,
    ) -> list[dict[str, Any]]:
        """Faz E3 — Terrain Engine <-> Render Engine Köprüsü (Hidroloji).

        D10'un `FlowAccumulation.accumulate()` çıktısı (nehir/dere ağı)
        bugüne kadar yalnızca sayısal bir grid olarak kalıyordu - hiçbir
        yerden `Scene`'e/viewer'a bağlanmıyordu (ROADMAP_V4 Faz E3 denetim
        maddesi). Bu metod, akümülasyon değeri `accumulation_threshold`'u
        aşan her hücreyi kendi D8 akış-yönü komşusuna bağlayan bir
        dünya-uzayı çizgi-segment listesi üretir (aynı `TerrainMeshGenerator.
        generate()`'in kullandığı x=col*resolution, y=row*resolution, z=
        elevation dönüşümüyle - iki fonksiyon aynı grid'den üretildiğinde
        çizgiler mesh üzerine tam oturur) ve `self.flow_lines[node.name]`'e
        gömer. `to_dict()` bunu ayrı bir `flow_lines` anahtarıyla JSON'a
        yazar; viewer bunu isterse ayrı bir çizgi-katmanı olarak çizer,
        boşsa (varsayılan) hiçbir şey değişmez - geriye uyumlu.

        Yoğunluk (`intensity`) değeri, o segmentin akümülasyonunun grid
        genelindeki maksimuma oranı (0..1) olarak normalize edilir - viewer
        bunu çizgi kalınlığı/renk yoğunluğu ipucu olarak kullanabilir.

        Yalnızca `node` bu `Scene`'e ait ise çalışır; aksi halde `ValueError`
        (bkz. `attach_vertex_ao`'daki aynı desen).
        """
        if node not in self.nodes:
            raise ValueError(
                f"'{node.name}' bu Scene'e ait değil - önce add_mesh()/"
                "add_mesh_with_lod() ile ekleyin."
            )
        # Döngüsel import'tan kaçınmak için geç import (terrain_engine ->
        # render_engine zinciri yok, ama simetrik/gelecek-güvenli olsun).
        from ..terrain_engine import FlowAccumulation

        directions = FlowAccumulation.flow_directions(grid)
        accum = FlowAccumulation.accumulate(grid)
        max_accum = max((v for row in accum for v in row), default=1.0) or 1.0

        segments: list[dict[str, Any]] = []
        for r in range(grid.height):
            for c in range(grid.width):
                if accum[r][c] < accumulation_threshold:
                    continue
                delta = directions[r][c]
                if delta is None:
                    continue
                nr, nc = r + delta[0], c + delta[1]

                x0 = c * grid.resolution_m
                y0 = r * grid.resolution_m
                z0 = grid.elevations[r][c]
                x1 = nc * grid.resolution_m
                y1 = nr * grid.resolution_m
                z1 = grid.elevations[nr][nc]

                segments.append({
                    "start": [x0, y0, z0],
                    "end": [x1, y1, z1],
                    "intensity": min(1.0, accum[r][c] / max_accum),
                })

        self.flow_lines[node.name] = segments
        return segments

    def push_agent_frame(
        self,
        t: float,
        agents: list[Any],
    ) -> dict[str, Any]:
        """Roadmap V9 / OMURGA / O.2 — "Render/Viewer Köprüsü — Agent
        Katmanı".

        `t` anındaki agent pozisyonlarını viewer-dostu bir kareye çevirip
        `self.agent_frames`'e ekler. `agents`, `mobility.simulation_recorder.
        AgentSnapshot` listesi olabilir (tercih edilen - `SimulationRecorder.
        keyframes` doğrudan buraya beslenir) veya `(agent_id, x, y, state)`
        biçiminde herhangi bir duck-typed nesne/sözlük olabilir - yalnızca
        `agent_id`/`id`, `x`, `y`, `state` alanları okunur, sıkı bir tip
        bağımlılığı yaratılmaz (render_engine, mobility'ye bağımlı olmasın
        diye - bkz. modül başlığındaki "yalnızca stdlib" ilkesi, buradaki
        gevşek erişim mobility'yi *import etmeden* onun verisini taşımayı
        sağlar).

        Bilinçli olarak **detaylı 3D karakter değil** basit nokta/kapsül
        gösterimi hedeflenir (roadmap notu: performans, ölçek arttıkça daha
        da kritik - 50.000 agent'a kadar viewer'da tek bir `gl.POINTS` draw
        call'ıyla çizilebilir olması gerekir).
        """
        frame_agents: list[dict[str, Any]] = []
        for a in agents:
            if isinstance(a, dict):
                agent_id = a.get("agent_id", a.get("id"))
                x, y = a["x"], a["y"]
                state = a.get("state", "moving")
            else:
                agent_id = getattr(a, "agent_id", getattr(a, "id", None))
                x, y = a.x, a.y
                state = getattr(a, "state", "moving")
            state_value = getattr(state, "value", state)  # Enum ise .value
            frame_agents.append({"id": agent_id, "x": x, "y": y, "state": state_value})

        frame = {"t": t, "agents": frame_agents}
        self.agent_frames.append(frame)
        return frame

    def push_agent_recording(self, recorder: Any) -> int:
        """`mobility.simulation_recorder.SimulationRecorder`'ın tüm
        keyframe geçmişini toplu olarak bu sahneye aktarır (tek tek
        `push_agent_frame()` çağırmanın kısayolu). `recorder.keyframes`
        listesindeki her `Keyframe(t, agents=[AgentSnapshot, ...])`
        elemanı için bir çağrı yapar. Döner: aktarılan kare sayısı.

        `recorder`, gevşek tipte alınır (yalnızca `.keyframes` özniteliği
        okunur) - yine `render_engine`'in `mobility`'ye sıkı bağımlı
        olmasını önlemek için (yukarıdaki `push_agent_frame` notuyla aynı
        gerekçe).
        """
        count = 0
        for kf in recorder.keyframes:
            self.push_agent_frame(kf.t, kf.agents)
            count += 1
        return count

    def clear_agent_frames(self) -> None:
        """Yeni bir simülasyon koşumu başlamadan önce önceki agent
        kayıtlarını temizler (aynı `Scene` nesnesi birden fazla senaryo
        koşumu arasında yeniden kullanılabiliyorsa)."""
        self.agent_frames.clear()

    # -- Roadmap V10 / Faz 5: senaryo overlay + ses köprüsü ------------- #

    def push_overlay_layer(self, entries: list[dict[str, Any]]) -> int:
        """`visualization.scenario_visual_bridge`'in ürettiği düz dict
        listesini (`fire_facade_overlay`, `bind_heatmap_to_scene_layer`,
        `emergency_vehicle_icon_frame`, `traffic_vehicle_scene_frame`
        çıktıları - her biri kendi `"type"` alanıyla ayırt edilir)
        `overlay_layers`'a ekler. Bu metod hiçbir dönüşüm/doğrulama
        yapmaz (tek sorumluluk: `render_engine` içeriği icat etmez,
        yalnızca taşır) - dönüşüm sorumluluğu tamamen çağıran katmanda
        (`scenario_visual_bridge`) kalır. Döner: eklenen eleman sayısı."""
        self.overlay_layers.extend(entries)
        return len(entries)

    def clear_overlay_layers(self) -> None:
        self.overlay_layers.clear()

    def push_audio_event(self, event: dict[str, Any]) -> None:
        """`visualization.spatial_audio`'nun ürettiği tek bir ses-olay
        dict'ini (`SoundEffectEvent`/`PositionalAudioSource`'un düz
        JSON karşılığı, çağıran taraf `dataclasses.asdict()` ile üretir)
        `audio_events`'e ekler."""
        self.audio_events.append(event)

    def clear_audio_events(self) -> None:
        self.audio_events.clear()

    def add_mesh_with_lod(
        self,
        mesh: Mesh3D,
        *,
        material: PBRMaterial | None = None,
        translation: tuple[float, float, float] = (0.0, 0.0, 0.0),
        rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0),
        scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
        lod_ratios: tuple[float, ...] = DEFAULT_LOD_RATIOS,
        lod_distances: tuple[float, ...] = DEFAULT_LOD_DISTANCES,
        use_progressive: bool = False,
    ) -> SceneNode:
        """Faz D2: `add_mesh()` ile aynı ama ek olarak D1'in gerçek QEM
        `MeshSimplifier`'ı ile `lod_ratios`'a karşılık gelen basitleştirilmiş
        mesh varyantlarını üretip node'a `lod_levels` olarak gömer.

        `lod_ratios[i]` == 1.0 ise o seviye orijinal mesh'in ta kendisidir
        (gereksiz simplify çağrısı yapılmaz). `lod_distances[i]` o seviyenin
        çizileceği azami kamera mesafesidir; son eleman genelde `math.inf`
        olmalı ki en uzak/en düşük detay her zaman bir karşılık bulsun.

        Faz E2: `use_progressive=True` verilirse, `lod_ratios`'un en küçüğüne
        kadar tüm QEM edge-collapse geçmişi `mesh_engine.progressive_mesh`
        ile ayrıca kaydedilip `node.progressive`'e gömülür - ayrık
        `lod_levels` listesini DEĞİŞTİRMEZ (geriye uyumlu), yalnızca
        `select_lod_mesh_geomorph()`'un ardışık seviyeler arasında sürekli
        geçiş üretebilmesi için ek veri sağlar. Varsayılan `False`'dır.
        """
        if len(lod_ratios) != len(lod_distances):
            raise ValueError("lod_ratios ve lod_distances aynı uzunlukta olmalı.")
        if not lod_ratios:
            raise ValueError("En az bir LOD seviyesi gerekli.")

        node = self.add_mesh(
            mesh, material=material, translation=translation,
            rotation_deg=rotation_deg, scale=scale,
        )
        levels: list[tuple[float, Mesh3D]] = []
        for ratio, max_dist in zip(lod_ratios, lod_distances):
            if ratio >= 1.0:
                lod_mesh = mesh
            else:
                lod_mesh = MeshSimplifier.simplify(mesh, ratio)
            levels.append((max_dist, lod_mesh))
        node.lod_levels = levels

        if use_progressive:
            node.progressive = ProgressiveMesh.build(mesh, min_triangle_ratio=min(lod_ratios))

        return node

    # -- geometrik yardımcılar ----------------------------------------- #
    def bounding_box(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Tüm node'ların world-space (transform uygulanmış) sınır kutusu."""
        if not self.nodes:
            return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)

        min_x = min_y = min_z = math.inf
        max_x = max_y = max_z = -math.inf

        for node in self.nodes:
            (lx, ly, lz), (hx, hy, hz) = node.mesh.bounding_box()
            corners = [
                (lx, ly, lz), (lx, ly, hz), (lx, hy, lz), (lx, hy, hz),
                (hx, ly, lz), (hx, ly, hz), (hx, hy, lz), (hx, hy, hz),
            ]
            for cx, cy, cz in corners:
                wx, wy, wz = _apply_trs(
                    (cx, cy, cz), node.translation, node.rotation_deg, node.scale
                )
                min_x, max_x = min(min_x, wx), max(max_x, wx)
                min_y, max_y = min(min_y, wy), max(max_y, wy)
                min_z, max_z = min(min_z, wz), max(max_z, wz)

        return (min_x, min_y, min_z), (max_x, max_y, max_z)

    def default_camera(self) -> dict[str, Any]:
        """Sahnenin tamamını çerçeveleyen makul bir orbit-kamera başlangıcı."""
        (lx, ly, lz), (hx, hy, hz) = self.bounding_box()
        center = ((lx + hx) / 2.0, (ly + hy) / 2.0, (lz + hz) / 2.0)
        diag = math.sqrt((hx - lx) ** 2 + (hy - ly) ** 2 + (hz - lz) ** 2)
        radius = max(diag, 1.0) * 1.4
        return {
            "target": list(center),
            "distance": radius,
            "yaw_deg": 45.0,
            "pitch_deg": 30.0,
            "fov_deg": 50.0,
            "near": max(radius * 0.001, 0.01),
            "far": max(radius * 10.0, 100.0),
        }

    # -- serileştirme --------------------------------------------------- #
    def to_dict(self) -> dict[str, Any]:
        materials_json = {}
        for name, mat in self.materials.items():
            entry: dict[str, Any] = {
                "albedo": list(mat.albedo),
                "roughness": mat.roughness,
                "metallic": mat.metallic,
                "emissive": list(mat.emissive),
                "opacity": mat.opacity,
            }
            # Faz C4 (impostor doku ataması) ile eklendi: önceden materyal
            # nesnesindeki `*_map` alanları hiç serileştirilmiyordu (yalnızca
            # skaler PBR değerleri çıkıyordu), yani `PBRMaterial.albedo_map`
            # dolu olsa bile viewer'a hiç ulaşmıyordu. Yalnızca dolu (None
            # olmayan) alanlar eklenir - mevcut, doku içermeyen materyaller
            # için JSON çıktısı **birebir aynı** kalır (regresyon yok,
            # `test_phase15_render_engine.py` bunu doğruluyor).
            for map_attr in ("albedo_map", "roughness_map", "metallic_map", "normal_map", "ao_map"):
                value = getattr(mat, map_attr, None)
                if value is not None:
                    entry[map_attr] = value
            materials_json[name] = entry

        nodes_json = []
        for node in self.nodes:
            positions: list[float] = []
            normals: list[float] = []
            uvs: list[float] = []
            has_normals = all(v.normal is not None for v in node.mesh.vertices) and len(node.mesh.vertices) > 0
            has_uvs = all(v.uv is not None for v in node.mesh.vertices) and len(node.mesh.vertices) > 0

            for v in node.mesh.vertices:
                positions.extend((v.x, v.y, v.z))
                if has_normals:
                    normals.extend(v.normal)  # type: ignore[arg-type]
                if has_uvs:
                    uvs.extend(v.uv)  # type: ignore[arg-type]

            indices: list[int] = []
            for tri in node.mesh.triangles:
                indices.extend(tri)

            lod_groups_json = None
            if node.lod_levels:
                lod_groups_json = [
                    _mesh_to_lod_group(max_dist, lod_mesh)
                    for max_dist, lod_mesh in node.lod_levels
                ]

            ao = self.vertex_ao.get(node.name)
            if ao is not None and len(ao) != node.mesh.vertex_count():
                raise ValueError(
                    f"'{node.name}' için AO uzunluğu ({len(ao)}) vertex "
                    f"sayısıyla ({node.mesh.vertex_count()}) uyuşmuyor - "
                    "mesh, AO bake edildikten sonra değişmiş olabilir."
                )

            nodes_json.append({
                "name": node.name,
                "material": node.material_name,
                "translation": list(node.translation),
                "rotation_deg": list(node.rotation_deg),
                "scale": list(node.scale),
                "positions": positions,
                "normals": normals if has_normals else None,
                "uvs": uvs if has_uvs else None,
                # Faz E1: D9 `AmbientOcclusionBaker` çıktısı - viewer bunu
                # vertex attribute location=2 olarak yükler (yoksa AO=1.0
                # sabitine düşülür, geriye uyumlu).
                "ao": ao,
                "indices": indices,
                "vertex_count": node.mesh.vertex_count(),
                "triangle_count": node.mesh.triangle_count(),
                # Faz D2: kamera-mesafesi tabanlı LOD seçimi için (bkz.
                # viewer/index.html `pickLodLevel`). None ise node LOD'suz.
                "lod_groups": lod_groups_json,
            })

        lights_json = [
            {
                "kind": light.kind,
                "color": list(light.color),
                "intensity": light.intensity,
                "direction": list(light.direction) if light.direction else None,
                "position": list(light.position) if light.position else None,
            }
            for light in self.lights
        ]

        sky_json = None
        if self.sky is not None:
            sky_json = {
                "zenith_color": list(self.sky.zenith_color),
                "horizon_color": list(self.sky.horizon_color),
                "turbidity": self.sky.turbidity,
                "hdri_path": self.sky.hdri_path,
            }

        return {
            "schema_version": SCENE_SCHEMA_VERSION,
            "name": self.name,
            "camera": self.default_camera(),
            "materials": materials_json,
            "lights": lights_json,
            # Faz E1: HDRSky köprüsü - None ise viewer eski düz-renk arka
            # plana düşer (geriye uyumlu).
            "sky": sky_json,
            "nodes": nodes_json,
            # Faz E3: node adı -> akış ağı çizgi segmentleri. Boş sözlük
            # (varsayılan) ise viewer eski davranışına düşer (geriye uyumlu).
            "flow_lines": self.flow_lines,
            # Roadmap V9 / OMURGA / O.2: zaman-damgalı agent kareleri. Boş
            # liste (varsayılan) ise viewer agent katmanını/zaman çizelgesi
            # kontrolünü hiç göstermez (geriye uyumlu, statik sahneler
            # etkilenmez).
            "agent_frames": self.agent_frames,
            # Roadmap V10 / Faz 5.1-5.3/5.5: senaryo overlay katmanları
            # (yangın sprite, heatmap, acil müdahale, trafik). Boş liste
            # (varsayılan) ise viewer eski davranışına düşer (geriye
            # uyumlu, statik/agent-only sahneler etkilenmez).
            "overlay_layers": self.overlay_layers,
            # Roadmap V10 / Faz 5.6: ses-olay listesi. Boş liste
            # (varsayılan) ise viewer hiçbir ses çalmaz (geriye uyumlu).
            "audio_events": self.audio_events,
        }

    def to_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def write(self, path: str | Path, *, indent: int | None = 2) -> Path:
        path = Path(path)
        path.write_text(self.to_json(indent=indent), encoding="utf-8")
        return path


# ======================================================================== #
# Yardımcılar
# ======================================================================== #

def _apply_trs(
    point: tuple[float, float, float],
    translation: tuple[float, float, float],
    rotation_deg: tuple[float, float, float],
    scale: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = point
    sx, sy, sz = scale
    x, y, z = x * sx, y * sy, z * sz

    rx, ry, rz = (math.radians(a) for a in rotation_deg)

    # X ekseni etrafında
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    # Y ekseni etrafında
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    # Z ekseni etrafında
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)

    tx, ty, tz = translation
    return (x + tx, y + ty, z + tz)


def _mesh_to_lod_group(max_distance: float, mesh: Mesh3D) -> dict[str, Any]:
    """Tek bir LOD seviyesini `to_dict()`'in ana node bloğuyla aynı düz
    positions/normals/indices sözleşmesiyle JSON'a çevirir."""
    positions: list[float] = []
    normals: list[float] = []
    has_normals = all(v.normal is not None for v in mesh.vertices) and len(mesh.vertices) > 0
    for v in mesh.vertices:
        positions.extend((v.x, v.y, v.z))
        if has_normals:
            normals.extend(v.normal)  # type: ignore[arg-type]
    indices: list[int] = []
    for tri in mesh.triangles:
        indices.extend(tri)
    return {
        "max_distance": None if math.isinf(max_distance) else max_distance,
        "positions": positions,
        "normals": normals if has_normals else None,
        "indices": indices,
        "vertex_count": mesh.vertex_count(),
        "triangle_count": mesh.triangle_count(),
    }


def select_lod_for_distance(node: SceneNode, distance: float) -> Mesh3D:
    """Faz D2: verilen kamera mesafesine göre bir node için hangi mesh'in
    çizileceğine karar veren tek doğru kaynak (Python tarafı) - viewer'daki
    JS `pickLodLevel()` bununla aynı mantığı (artan max_distance sırasıyla
    ilk uyan seviye) izler."""
    if not node.lod_levels:
        return node.mesh
    for max_dist, mesh in node.lod_levels:
        if distance <= max_dist:
            return mesh
    return node.lod_levels[-1][1]


def select_lod_mesh_geomorph(node: SceneNode, distance: float) -> Mesh3D:
    """Faz E2: `select_lod_for_distance()`'ın sürekli-geçiş (geomorph)
    varyantı. `node.progressive` doldurulmamışsa (`use_progressive=False`
    ile eklenmiş node - varsayılan) davranış birebir `select_lod_for_
    distance()` ile aynıdır (geriye uyumlu ani-pop). Doldurulmuşsa, `distance`
    iki ardışık `lod_levels` eşiği arasındaysa (geçiş bandı), QEM
    collapse geçmişi üzerinden `t` oranına karşılık gelen ara-mesh
    (`ProgressiveMesh.interpolate_between_counts`) döner - üçgen sayısı iki
    uç seviye arasında sürekli ve monoton değişir, ani pop oluşmaz."""
    if not node.lod_levels:
        return node.mesh
    if node.progressive is None:
        return select_lod_for_distance(node, distance)

    # `distance`'ı içeren bandı (önceki eşik, bu eşik] bul.
    lower_bound = 0.0
    for idx, (max_dist, mesh) in enumerate(node.lod_levels):
        if distance <= max_dist:
            if idx == 0:
                return mesh  # en yüksek detay bandı - geçilecek daha ince seviye yok
            prev_dist, prev_mesh = node.lod_levels[idx - 1]
            band = max_dist - lower_bound
            if math.isinf(band) or band <= 0:
                return mesh
            t = (distance - lower_bound) / band
            finer_count = prev_mesh.triangle_count()
            coarser_count = mesh.triangle_count()
            if finer_count <= coarser_count:
                return mesh
            return node.progressive.interpolate_between_counts(finer_count, coarser_count, t)
        lower_bound = max_dist
    return node.lod_levels[-1][1]


def node_world_center(node: SceneNode) -> tuple[float, float, float]:
    """Node'un world-space (transform uygulanmış) bounding box merkezi -
    LOD mesafe hesaplaması ve frustum culling için ortak referans noktası."""
    (lx, ly, lz), (hx, hy, hz) = node.mesh.bounding_box()
    local_center = ((lx + hx) / 2.0, (ly + hy) / 2.0, (lz + hz) / 2.0)
    return _apply_trs(local_center, node.translation, node.rotation_deg, node.scale)


def total_triangle_count_for_camera(
    scene: Scene, camera_position: tuple[float, float, float],
) -> int:
    """Faz D2 kabul kriteri yardımcı fonksiyonu: verilen kamera konumundan,
    her node için LOD seçimi yapıldıktan SONRA sahnenin toplam üçgen
    sayısını döndürür. `benchmark_scene_lod_reduction()` bunu LOD'suz
    (her zaman tam mesh) toplamla karşılaştırarak D2'nin kabul kriterini
    (kamera uzaklaştıkça çizilen üçgen sayısı ölçülebilir şekilde azalır)
    doğrular."""
    total = 0
    for node in scene.nodes:
        if node.lod_levels:
            center = node_world_center(node)
            dist = math.dist(camera_position, center)
            mesh = select_lod_for_distance(node, dist)
        else:
            mesh = node.mesh
        total += mesh.triangle_count()
    return total


def total_triangle_count_full_detail(scene: Scene) -> int:
    """LOD'suz referans toplamı: her node her zaman tam detayla (`node.mesh`)
    sayılır - `total_triangle_count_for_camera()` ile karşılaştırma için."""
    return sum(node.mesh.triangle_count() for node in scene.nodes)


def _node_world_aabb(node: SceneNode) -> AABB3D:
    (lx, ly, lz), (hx, hy, hz) = node.mesh.bounding_box()
    corners = [
        (lx, ly, lz), (lx, ly, hz), (lx, hy, lz), (lx, hy, hz),
        (hx, ly, lz), (hx, ly, hz), (hx, hy, lz), (hx, hy, hz),
    ]
    xs, ys, zs = [], [], []
    for c in corners:
        wx, wy, wz = _apply_trs(c, node.translation, node.rotation_deg, node.scale)
        xs.append(wx); ys.append(wy); zs.append(wz)
    return AABB3D(min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def visible_node_names(
    scene: Scene,
    camera,  # type: ignore[no-untyped-def]  # visualization.camera_rig.Camera
    *,
    aspect: float = 16 / 9,
    near: float = 0.1,
    far: float = 1000.0,
) -> list[str]:
    """Faz D2: Faz 13 `FrustumCulling`'i `Scene` node'larına uygulayan köprü.
    Python tarafında hazırlanmış statik/başlangıç görünürlük listesi üretir;
    viewer bu listeyi başlangıç sahnesi için kullanabilir, kare-başına gerçek
    zamanlı culling ise JS tarafında (kamera her hareket ettiğinde) yapılır."""
    culler = FrustumCulling(aspect=aspect, near=near, far=far)
    boxes = {node.name: _node_world_aabb(node) for node in scene.nodes}
    return culler.cull(camera, boxes)


def sun_light_to_scene_light(sun) -> SceneLight:  # type: ignore[no-untyped-def]
    """`lighting.SunLight` -> `SceneLight` (directional). Döngüsel import'tan
    kaçınmak için tip anotasyonu bilerek gevşek bırakılmıştır."""
    dx, dy, dz = sun.direction()
    # Işık YÖNÜ, güneşten yüzeye doğru olmalı (viewer shader konvansiyonu).
    return SceneLight(
        kind="directional",
        color=sun.color,
        intensity=min(1.0, sun.intensity_lux / 120_000.0),
        direction=(-dx, -dy, -dz),
    )


def ambient_light_to_scene_light(ambient) -> SceneLight:  # type: ignore[no-untyped-def]
    return SceneLight(kind="ambient", color=ambient.color, intensity=ambient.intensity)


def hdr_sky_to_scene_sky(sky) -> SceneSky:  # type: ignore[no-untyped-def]
    """Faz E1: `lighting.HDRSky` -> `SceneSky`. Döngüsel import'tan
    kaçınmak için tip anotasyonu bilerek gevşek bırakılmıştır (bkz.
    `sun_light_to_scene_light` ile aynı desen)."""
    return SceneSky(
        zenith_color=sky.zenith_color,
        horizon_color=sky.horizon_color,
        turbidity=sky.turbidity,
        hdri_path=sky.hdri_path,
    )


def compute_light_space_matrix(
    light_direction: tuple[float, float, float],
    scene_bounds: tuple[tuple[float, float, float], tuple[float, float, float]],
) -> list[list[float]]:
    """Faz E1: Ortografik ışık-uzayı (light-space) view*proj matrisini,
    sahneyi tam kapsayacak şekilde hesaplayan SAF Python referans
    implementasyonu.

    Bu fonksiyon, `viewer/index.html`'deki JS `computeLightSpaceMatrix()`
    ile **birebir aynı matematiği** (bkz. dosya üstü paritenin belgelendiği
    yorum) uygular; amacı gerçek bir GPU'ya bağlı olmadan (headless test
    ortamında tarayıcı yok) shadow-map projeksiyon mantığının doğruluğunu
    Python tarafında regresyonla doğrulayabilmektir - `ShadowCalculator.
    point_in_shadow` ile çapraz kontrol `test_phaseE1_shadow_pipeline.py`
    içinde yapılır.

    Dönen değer 4x4 matris, satır-öncelikli (row-major) `list[list[float]]`;
    `apply_light_space_matrix()` ile bir dünya noktasına uygulanabilir.
    """
    (lx, ly, lz), (hx, hy, hz) = scene_bounds
    center = ((lx + hx) / 2.0, (ly + hy) / 2.0, (lz + hz) / 2.0)
    diag = math.sqrt((hx - lx) ** 2 + (hy - ly) ** 2 + (hz - lz) ** 2) or 1.0
    half_extent = diag * 0.75  # kenarlarda kırpılmayı önlemek için pay

    dx, dy, dz = light_direction
    dl = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    dx, dy, dz = dx / dl, dy / dl, dz / dl

    # Işık kaynağının (güneşin ters yönünde), sahne merkezinden uzaktaki
    # sanal konumu - "eye" bu konum, bakış yönü sahne merkezine.
    eye = (center[0] - dx * diag, center[1] - dy * diag, center[2] - dz * diag)

    up = (0.0, 1.0, 0.0) if abs(dy) < 0.999 else (1.0, 0.0, 0.0)

    # lookAt (sağ-el, view matrisi) - viewer/index.html M4.lookAt ile aynı sıra.
    zx, zy, zz = eye[0] - center[0], eye[1] - center[1], eye[2] - center[2]
    zl = math.sqrt(zx * zx + zy * zy + zz * zz) or 1.0
    zx, zy, zz = zx / zl, zy / zl, zz / zl
    xx, xy, xz = up[1] * zz - up[2] * zy, up[2] * zx - up[0] * zz, up[0] * zy - up[1] * zx
    xl = math.sqrt(xx * xx + xy * xy + xz * xz) or 1.0
    xx, xy, xz = xx / xl, xy / xl, xz / xl
    yx, yy, yz = zy * xz - zz * xy, zz * xx - zx * xz, zx * xy - zy * xx

    view = [
        [xx, xy, xz, -(xx * eye[0] + xy * eye[1] + xz * eye[2])],
        [yx, yy, yz, -(yx * eye[0] + yy * eye[1] + yz * eye[2])],
        [zx, zy, zz, -(zx * eye[0] + zy * eye[1] + zz * eye[2])],
        [0.0, 0.0, 0.0, 1.0],
    ]

    # Ortografik projeksiyon: [-half_extent, half_extent] küpü [-1, 1]'e.
    near, far = 0.01, diag * 3.0
    proj = [
        [1.0 / half_extent, 0.0, 0.0, 0.0],
        [0.0, 1.0 / half_extent, 0.0, 0.0],
        [0.0, 0.0, -2.0 / (far - near), -(far + near) / (far - near)],
        [0.0, 0.0, 0.0, 1.0],
    ]

    # proj * view (4x4 çarpım, satır-öncelikli)
    result = [[0.0] * 4 for _ in range(4)]
    for r in range(4):
        for c in range(4):
            result[r][c] = sum(proj[r][k] * view[k][c] for k in range(4))
    return result


def apply_light_space_matrix(
    matrix: list[list[float]], point: tuple[float, float, float],
) -> tuple[float, float, float]:
    """`compute_light_space_matrix()`'in döndürdüğü matrisi bir dünya
    noktasına uygular, ışık-uzayı NDC koordinatını (x, y, z) döndürür.
    z değeri [-1, 1] aralığında derinlik; x/y [-1, 1] aralığında ekran-
    benzeri koordinat (shadow-map dokusunda [0,1]'e eşlenmesi viewer'ın
    işi, bkz. `computeLightSpaceMatrix`/`worldToShadowUV` JS karşılığı)."""
    x, y, z = point
    w = [x, y, z, 1.0]
    out = [sum(matrix[r][k] * w[k] for k in range(4)) for r in range(4)]
    if abs(out[3]) > 1e-12:
        out = [v / out[3] for v in out]
    return (out[0], out[1], out[2])


def scene_from_meshes(
    meshes: list[Mesh3D],
    *,
    materials: list[PBRMaterial | None] | None = None,
    name: str = "scene",
) -> Scene:
    """Hızlı yol: düz bir Mesh3D listesinden (opsiyonel materyal listesiyle
    birebir eşleşen) bir Scene üretir. Building Reconstruction / Digital Twin
    gibi üst katmanlar bunu doğrudan çağırabilir."""
    scene = Scene(name=name)
    materials = materials or [None] * len(meshes)
    for mesh, mat in zip(meshes, materials):
        scene.add_mesh(mesh, material=mat)
    if not scene.lights:
        scene.add_light(SceneLight(kind="ambient", color=(1.0, 1.0, 1.0), intensity=0.35))
        scene.add_light(SceneLight(
            kind="directional", color=(1.0, 0.98, 0.92), intensity=1.0,
            direction=(-0.4, -1.0, -0.3),
        ))
    return scene
