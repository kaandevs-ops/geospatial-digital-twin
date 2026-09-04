"""
ROADMAP V5 - Track M / M1.1: Mesh Optimizasyon Katmanı — Decimation / LOD
üretim zinciri.
============================================================================

Mevcut `MeshSimplifier` (Garland-Heckbert QEM) tekil bir basitleştirme
işlemi sunuyordu ("mesh'i %X oranına indir") ama roadmap V5 M1.1'in istediği
şey bundan bir seviye üstü: her bina için **otomatik 3-4 seviyeli bir LOD
zinciri** (LOD0..LOD3) ve kamera mesafesine göre bu zincirden **hysteresis'li**
(ileri-geri titremeyi önleyen) otomatik geçiş.

Bu modül `MeshSimplifier`'ı bir alt-bileşen olarak kullanır, onun yerine
geçmez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from . import Mesh3D, MeshSimplifier


class LODLevel(IntEnum):
    LOD0 = 0  # Tam detay (iç mekan dahil) - yakın plan
    LOD1 = 1  # Dış kabuk + pencere/kapı deliği, iç mekan yok
    LOD2 = 2  # Kutu + çatı formu, pencere sadece doku
    LOD3 = 3  # Billboard/impostor - çok uzak binalar için tek düzlem + doku


# M1.1 kabul kriterine göre varsayılan üçgen-sayısı oranları (LOD0'a göre).
# LOD0 = 1.0 (referans), diğerleri QEM ile bu orana basitleştirilir.
DEFAULT_LOD_TRIANGLE_RATIOS: dict[LODLevel, float] = {
    LODLevel.LOD0: 1.0,
    LODLevel.LOD1: 0.45,
    LODLevel.LOD2: 0.12,
    LODLevel.LOD3: 0.02,
}

# Varsayılan mesafe eşikleri (metre) - kameradan bu mesafeden sonra ilgili
# LOD seviyesi devreye girer (LOD0: [0, d1), LOD1: [d1, d2), ...).
DEFAULT_LOD_DISTANCE_THRESHOLDS: dict[LODLevel, float] = {
    LODLevel.LOD0: 0.0,
    LODLevel.LOD1: 60.0,
    LODLevel.LOD2: 180.0,
    LODLevel.LOD3: 600.0,
}


@dataclass(slots=True)
class LODChain:
    """Tek bir mesh için önceden üretilmiş LOD0..LOD3 mesh zinciri."""

    source_name: str
    meshes: dict[LODLevel, Mesh3D] = field(default_factory=dict)
    triangle_counts: dict[LODLevel, int] = field(default_factory=dict)

    def triangle_reduction_ratio(self, level: LODLevel) -> float:
        """LOD0'a göre üçgen sayısı azalma oranı (0..1, 1 = tamamen sıfır)."""
        base = self.triangle_counts.get(LODLevel.LOD0, 0)
        if base == 0:
            return 0.0
        cur = self.triangle_counts.get(level, base)
        return 1.0 - (cur / base)

    def mesh_for(self, level: LODLevel) -> Mesh3D:
        return self.meshes[level]


class LODChainBuilder:
    """Roadmap M1.1: bir bina mesh'inden (LOD0 kabul edilir - tam detay)
    otomatik 3-4 seviyeli LOD zinciri üretir.

    Not: Gerçek "iç mekan çıkarma" (LOD1) veya "kutu+çatı formuna indirgeme"
    (LOD2) mimari-anlam-farkındalıklı bir işlemdir ve bina üretim
    pipeline'ının (ör. `FacadeGenerator`, `roof_generator`) kendisinde en
    doğru şekilde yapılır (bkz. `build_lod_chain_from_variants`). Bu builder,
    yalnızca *geometrik* mesh'i (LOD0) veren çağıranlar için QEM tabanlı
    basitleştirmeyle güvenli bir yaklaşık LOD zinciri üretir - şeklin
    silüetini QEM'in doğası gereği korur.
    """

    @staticmethod
    def build_from_lod0(
        lod0_mesh: Mesh3D,
        *,
        ratios: dict[LODLevel, float] | None = None,
        levels: tuple[LODLevel, ...] = (LODLevel.LOD0, LODLevel.LOD1, LODLevel.LOD2, LODLevel.LOD3),
    ) -> LODChain:
        ratios = ratios or DEFAULT_LOD_TRIANGLE_RATIOS
        chain = LODChain(source_name=lod0_mesh.name)
        for level in levels:
            ratio = ratios[level]
            if ratio >= 1.0 or level == LODLevel.LOD0:
                mesh = lod0_mesh.clone()
            else:
                mesh = MeshSimplifier.simplify(lod0_mesh, target_triangle_ratio=ratio)
            chain.meshes[level] = mesh
            chain.triangle_counts[level] = len(mesh.triangles)
        return chain

    @staticmethod
    def build_from_variants(
        *,
        lod0: Mesh3D,
        lod1: Mesh3D | None = None,
        lod2: Mesh3D | None = None,
        lod3: Mesh3D | None = None,
        fallback_ratios: dict[LODLevel, float] | None = None,
    ) -> LODChain:
        """Bina üretim pipeline'ı zaten anlam-farkındalıklı varyantlar
        (ör. iç mekansız dış kabuk, kutu+çatı formu) üretebiliyorsa,
        bunlar doğrudan zincire konur; eksik olan seviyeler LOD0'dan QEM
        ile otomatik türetilir (hiçbir seviye boş kalmaz)."""
        ratios = fallback_ratios or DEFAULT_LOD_TRIANGLE_RATIOS
        chain = LODChain(source_name=lod0.name)
        provided = {LODLevel.LOD0: lod0, LODLevel.LOD1: lod1, LODLevel.LOD2: lod2, LODLevel.LOD3: lod3}
        for level in (LODLevel.LOD0, LODLevel.LOD1, LODLevel.LOD2, LODLevel.LOD3):
            mesh = provided[level]
            if mesh is None:
                ratio = ratios[level]
                mesh = lod0.clone() if ratio >= 1.0 else MeshSimplifier.simplify(lod0, target_triangle_ratio=ratio)
            chain.meshes[level] = mesh
            chain.triangle_counts[level] = len(mesh.triangles)
        return chain


class LODSelector:
    """Roadmap M1.1: kamera mesafesine göre otomatik LOD seçimi -
    **hysteresis'li**: bir LOD seviyesinden bir sonrakine geçiş, ileri
    yöndeki eşiğin biraz *ötesinde*; geri yöndeki geçiş ise eşiğin biraz
    *berisinde* gerçekleşir. Bu, kamera tam eşik mesafesinde durduğunda
    LOD'un her karede ileri-geri titremesini (LOD popping) engeller.

    `hysteresis_band`: eşik mesafesinin yüzdesi olarak tolerans bandı
    (varsayılan %10). Örn. LOD1 eşiği 60m ise: 60m'yi geçince LOD1'e geçilir,
    ama LOD1'den LOD0'a geri dönmek için 60m * (1 - 0.10) = 54m'nin altına
    inmek gerekir.
    """

    def __init__(
        self,
        thresholds: dict[LODLevel, float] | None = None,
        hysteresis_band: float = 0.10,
    ) -> None:
        self.thresholds = dict(thresholds or DEFAULT_LOD_DISTANCE_THRESHOLDS)
        if not (0.0 <= hysteresis_band < 0.5):
            raise ValueError("hysteresis_band 0 ile 0.5 arasında olmalı.")
        self.hysteresis_band = hysteresis_band
        self._current_level: LODLevel = LODLevel.LOD0

    @property
    def current_level(self) -> LODLevel:
        return self._current_level

    def reset(self, level: LODLevel = LODLevel.LOD0) -> None:
        self._current_level = level

    def select(self, distance_m: float) -> LODLevel:
        """Verilen kamera mesafesi için (mevcut duruma göre hysteresis
        uygulayarak) yeni LOD seviyesini döner ve iç durumu günceller."""
        levels_sorted = sorted(self.thresholds.keys())
        cur = self._current_level

        # İleri yönde (uzaklaşma): bir sonraki seviyenin eşiğini geçtik mi?
        while True:
            next_candidates = [lv for lv in levels_sorted if lv > cur]
            if not next_candidates:
                break
            nxt = min(next_candidates)
            if distance_m >= self.thresholds[nxt]:
                cur = nxt
            else:
                break

        # Geri yönde (yaklaşma): mevcut seviyenin eşiğinin hysteresis kadar
        # altına indik mi?
        while True:
            prev_candidates = [lv for lv in levels_sorted if lv < cur]
            if not prev_candidates:
                break
            prev = max(prev_candidates)
            cur_threshold = self.thresholds[cur]
            retreat_point = cur_threshold * (1.0 - self.hysteresis_band)
            if distance_m < retreat_point:
                cur = prev
            else:
                break

        self._current_level = cur
        return cur


__all__ = [
    "LODLevel",
    "LODChain",
    "LODChainBuilder",
    "LODSelector",
    "DEFAULT_LOD_TRIANGLE_RATIOS",
    "DEFAULT_LOD_DISTANCE_THRESHOLDS",
]
