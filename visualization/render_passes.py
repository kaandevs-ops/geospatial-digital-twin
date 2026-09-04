"""
Visualization - Render Passes (Phase 9)
=========================================

Roadmap Phase 9 "Rendering": PBR, HDR, SSAO, Bloom, Depth of Field,
Motion Blur, FXAA, MSAA, Fog, Cloud, Volumetric Light, Reflection, Refraction.

Not: Gerçek bir GPU render pipeline (WebGL/Vulkan/Metal shader kodu) bu
roadmap'in kapsamı dışında, ayrı bir backend gerektirir. Burada
**render-agnostic** bir sahne/pass tanım katmanı var: hangi post-process
efektlerin hangi sırayla, hangi parametrelerle uygulanacağını tanımlayan
bir "recipe". Herhangi bir render backend'i (Three.js, WebGPU, Vulkan...) bu
tanımı okuyup kendi shader'larına map edebilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RenderPassType(Enum):
    PBR = "pbr"
    HDR = "hdr"
    SSAO = "ssao"
    BLOOM = "bloom"
    DEPTH_OF_FIELD = "depth_of_field"
    MOTION_BLUR = "motion_blur"
    FXAA = "fxaa"
    MSAA = "msaa"
    FOG = "fog"
    CLOUD = "cloud"
    VOLUMETRIC_LIGHT = "volumetric_light"
    REFLECTION = "reflection"
    REFRACTION = "refraction"


# Her pass tipi için varsayılan parametre seti — backend bunları shader
# uniform'larına eşleyebilir.
_DEFAULT_PARAMETERS: dict[RenderPassType, dict] = {
    RenderPassType.PBR: {"metallic_default": 0.0, "roughness_default": 0.8},
    RenderPassType.HDR: {"exposure": 1.0, "tonemap": "aces"},
    RenderPassType.SSAO: {"radius": 0.5, "intensity": 1.0, "samples": 16},
    RenderPassType.BLOOM: {"threshold": 1.0, "intensity": 0.6, "radius": 4},
    RenderPassType.DEPTH_OF_FIELD: {"focus_distance": 10.0, "aperture": 0.1, "max_blur": 1.0},
    RenderPassType.MOTION_BLUR: {"shutter_angle": 180.0, "sample_count": 8},
    RenderPassType.FXAA: {"quality": "high"},
    RenderPassType.MSAA: {"samples": 4},
    RenderPassType.FOG: {"density": 0.02, "color": (0.7, 0.75, 0.8), "start": 10.0, "end": 500.0},
    RenderPassType.CLOUD: {"coverage": 0.4, "altitude": 1500.0, "speed": 1.0},
    RenderPassType.VOLUMETRIC_LIGHT: {"scattering": 0.3, "steps": 32},
    RenderPassType.REFLECTION: {"resolution": 512, "roughness_cutoff": 0.4},
    RenderPassType.REFRACTION: {"ior": 1.33},
}


@dataclass(slots=True)
class RenderPass:
    pass_type: RenderPassType
    enabled: bool = True
    parameters: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        merged = dict(_DEFAULT_PARAMETERS.get(self.pass_type, {}))
        merged.update(self.parameters)
        self.parameters = merged

    def with_parameter(self, key: str, value) -> RenderPass:
        params = dict(self.parameters)
        params[key] = value
        return RenderPass(pass_type=self.pass_type, enabled=self.enabled, parameters=params)


@dataclass(slots=True)
class RenderPipeline:
    """Sıralı render-pass listesi. Sıra önemlidir (ör. Bloom, tonemap'ten
    önce; FXAA/MSAA en sonda uygulanır)."""

    passes: list[RenderPass] = field(default_factory=list)

    @classmethod
    def default_pbr_pipeline(cls) -> RenderPipeline:
        """Yaygın bir "iyi görünen" varsayılan sıralama."""
        order = [
            RenderPassType.PBR,
            RenderPassType.SSAO,
            RenderPassType.VOLUMETRIC_LIGHT,
            RenderPassType.REFLECTION,
            RenderPassType.REFRACTION,
            RenderPassType.FOG,
            RenderPassType.CLOUD,
            RenderPassType.BLOOM,
            RenderPassType.DEPTH_OF_FIELD,
            RenderPassType.MOTION_BLUR,
            RenderPassType.HDR,
            RenderPassType.MSAA,
            RenderPassType.FXAA,
        ]
        return cls(passes=[RenderPass(pass_type=t) for t in order])

    def add(self, render_pass: RenderPass, index: int | None = None) -> None:
        if index is None:
            self.passes.append(render_pass)
        else:
            self.passes.insert(index, render_pass)

    def remove(self, pass_type: RenderPassType) -> None:
        self.passes = [p for p in self.passes if p.pass_type != pass_type]

    def get(self, pass_type: RenderPassType) -> RenderPass | None:
        for p in self.passes:
            if p.pass_type == pass_type:
                return p
        return None

    def set_enabled(self, pass_type: RenderPassType, enabled: bool) -> None:
        p = self.get(pass_type)
        if p is not None:
            p.enabled = enabled

    def reorder(self, order: list[RenderPassType]) -> None:
        """Verilen sıraya göre pass'leri yeniden diz; listede olmayan pass
        tipleri sona, orijinal göreli sırayla eklenir."""
        by_type = {p.pass_type: p for p in self.passes}
        reordered = [by_type[t] for t in order if t in by_type]
        remaining = [p for p in self.passes if p.pass_type not in order]
        self.passes = reordered + remaining

    def active_passes(self) -> list[RenderPass]:
        return [p for p in self.passes if p.enabled]
