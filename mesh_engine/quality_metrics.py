"""
Mesh Quality Metrics
=====================

Roadmap FAZ 0 - "Temel Denetim ve Ölçüm Altyapısı".

Amaç: "daha kaliteli oldu" gibi subjektif değerlendirmeler yerine, her
mesh/bina için tekrarlanabilir, sayısal bir kalite raporu üretmek. Bu
modül olmadan roof_generator / duvar-kat sistemi gibi alanlardaki
iyileştirmeler ölçülemez.

Kapsam (roadmap Faz 0 maddeleri):
    - Non-manifold (açık) kenar sayısı
    - Watertight (su geçirmez / delik yok) kontrolü
    - Dejenere (sıfır alanlı) üçgen sayısı
    - Normal tutarlılığı (komşu üçgenler arası normal açısı)
    - Bina başına vertex / üçgen sayısı
    - Kat hizalama hatası (mm toleransı ile) — üst üste gelmesi gereken
      kat sınırlarının (Z ekseni) ve taşıyıcı duvarların (XY izdüşümü)
      sapması

`MeshRepair.is_manifold` / `find_boundary_edges` (bkz. `mesh_engine`)
zaten bir "evet/hayır" cevabı veriyordu; bu modül onun üzerine sayısal
bir rapor katmanı ekler ve kat hizalama gibi Faz 1'e özgü metrikleri de
kapsar.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import Mesh3D, _triangle_area, _triangle_normal

# ======================================================================== #
# Tekil mesh kalite raporu
# ======================================================================== #


@dataclass(slots=True)
class MeshQualityReport:
    mesh_name: str
    vertex_count: int
    triangle_count: int
    non_manifold_edge_count: int
    boundary_edge_count: int
    degenerate_triangle_count: int
    normal_consistency_ratio: float  # 0..1, 1 = tamamen tutarlı
    is_watertight: bool
    is_manifold: bool
    hard_edge_count: int = 0
    normal_consistency_ratio_adjusted: float = 1.0  # sert kenarlar hariç tutulmuş

    def summary_line(self) -> str:
        return (
            f"{self.mesh_name}: v={self.vertex_count} t={self.triangle_count} "
            f"non_manifold_edges={self.non_manifold_edge_count} "
            f"boundary_edges={self.boundary_edge_count} "
            f"degenerate_tris={self.degenerate_triangle_count} "
            f"normal_consistency={self.normal_consistency_ratio:.3f} "
            f"normal_consistency_adj={self.normal_consistency_ratio_adjusted:.3f} "
            f"(hard_edges={self.hard_edge_count}) "
            f"watertight={self.is_watertight}"
        )


class MeshQualityAnalyzer:
    """Bir `Mesh3D` üzerinde sayısal kalite metrikleri hesaplar."""

    #: Mimari geometride kasıtlı olarak keskin olması beklenen açılar
    #: (derece). Örn. düz çatı parapeti, duvar-döşeme kesişimi gibi 90°/180°
    #: köşeler "hata" değil, tasarımın bir parçasıdır — bkz. FAZ0_DENETIM_
    #: RAPORU.md madde 5 (yanlış alarm bulgusu).
    HARD_EDGE_REFERENCE_ANGLES_DEG: tuple[float, ...] = (90.0, 180.0)

    @staticmethod
    def analyze(
        mesh: Mesh3D,
        degenerate_min_area: float = 1e-9,
        normal_angle_threshold_deg: float = 45.0,
        hard_edge_tolerance_deg: float = 12.0,
    ) -> MeshQualityReport:
        edge_count: dict[tuple[int, int], int] = {}
        edge_triangles: dict[tuple[int, int], list[int]] = {}
        for t_idx, (a, b, c) in enumerate(mesh.triangles):
            for i, j in ((a, b), (b, c), (c, a)):
                key = (min(i, j), max(i, j))
                edge_count[key] = edge_count.get(key, 0) + 1
                edge_triangles.setdefault(key, []).append(t_idx)

        non_manifold = sum(1 for c in edge_count.values() if c > 2)
        boundary = sum(1 for c in edge_count.values() if c == 1)

        degenerate = 0
        normals: list[tuple[float, float, float] | None] = []
        for tri in mesh.triangles:
            a, b, c = mesh.triangle_positions(tri)
            area = _triangle_area(a, b, c)
            if area < degenerate_min_area:
                degenerate += 1
                normals.append(None)
            else:
                normals.append(_triangle_normal(a, b, c))

        # Normal tutarlılığı: ortak kenarı paylaşan üçgen çiftlerinin
        # normalleri arasındaki açı eşik değerin altında mı?
        consistent = 0
        consistent_adjusted = 0
        hard_edge_count = 0
        compared = 0
        threshold_cos = math.cos(math.radians(normal_angle_threshold_deg))
        for key, tri_indices in edge_triangles.items():
            if len(tri_indices) != 2:
                continue  # sadece manifold (paylaşımlı) kenarlar kıyaslanabilir
            n1, n2 = normals[tri_indices[0]], normals[tri_indices[1]]
            if n1 is None or n2 is None:
                continue
            compared += 1
            dot = max(-1.0, min(1.0, n1[0] * n2[0] + n1[1] * n2[1] + n1[2] * n2[2]))
            if dot >= threshold_cos:
                consistent += 1
                consistent_adjusted += 1
                continue

            # Tutarsız (keskin) kenar — kasıtlı bir mimari "sert kenar" mı
            # (90°/180°'ye yakın) yoksa gerçek bir kalite sorunu mu (rastgele
            # açılı, muhtemelen bozuk normal) ayırt et.
            angle_deg = math.degrees(math.acos(dot))
            is_hard_edge = any(
                abs(angle_deg - ref) <= hard_edge_tolerance_deg
                for ref in MeshQualityAnalyzer.HARD_EDGE_REFERENCE_ANGLES_DEG
            )
            if is_hard_edge:
                hard_edge_count += 1
                consistent_adjusted += 1

        normal_consistency_ratio = (consistent / compared) if compared else 1.0
        normal_consistency_ratio_adjusted = (consistent_adjusted / compared) if compared else 1.0
        is_manifold = non_manifold == 0
        is_watertight = is_manifold and boundary == 0

        return MeshQualityReport(
            mesh_name=mesh.name,
            vertex_count=mesh.vertex_count(),
            triangle_count=mesh.triangle_count(),
            non_manifold_edge_count=non_manifold,
            boundary_edge_count=boundary,
            degenerate_triangle_count=degenerate,
            normal_consistency_ratio=normal_consistency_ratio,
            is_watertight=is_watertight,
            is_manifold=is_manifold,
            hard_edge_count=hard_edge_count,
            normal_consistency_ratio_adjusted=normal_consistency_ratio_adjusted,
        )


# ======================================================================== #
# Kat hizalama metrikleri (Faz 1.2 "katlar tam çıkmıyor" hatası için)
# ======================================================================== #


@dataclass(slots=True)
class FloorAlignmentReport:
    floor_count: int
    expected_base_z: list[float]
    actual_base_z: list[float]
    z_errors_m: list[float]
    max_z_error_m: float
    tolerance_m: float
    within_tolerance: bool
    wall_xy_errors_m: list[float] = field(default_factory=list)
    max_wall_xy_error_m: float = 0.0

    def summary_line(self) -> str:
        return (
            f"floors={self.floor_count} max_z_error_mm={self.max_z_error_m * 1000:.2f} "
            f"max_wall_xy_error_mm={self.max_wall_xy_error_m * 1000:.2f} "
            f"tolerance_mm={self.tolerance_m * 1000:.1f} "
            f"OK={self.within_tolerance}"
        )


class FloorAlignmentAnalyzer:
    """Kat sınırlarının (Z) ve taşıyıcı duvarların (XY) beklenen konumdan
    ne kadar saptığını ölçer. Roadmap Faz 1.2'de belirtilen "katlar tam
    çıkmıyor" hatasının kök nedenini teşhis etmek ve regresyonu
    engellemek için kullanılır.

    `actual_floor_meshes[i]` katının duvar/tabanına ait mesh'i; taban
    Z'si mesh'teki en düşük vertex Z'si olarak kabul edilir.
    """

    @staticmethod
    def check(
        floor_heights_m: list[float],
        actual_floor_meshes: list[Mesh3D] | None = None,
        tolerance_m: float = 0.005,  # 5 mm varsayılan tolerans
    ) -> FloorAlignmentReport:
        expected_base_z: list[float] = []
        acc = 0.0
        for h in floor_heights_m:
            expected_base_z.append(acc)
            acc += h

        actual_base_z: list[float] = list(expected_base_z)
        wall_xy_errors: list[float] = []

        if actual_floor_meshes:
            actual_base_z = []
            for mesh in actual_floor_meshes:
                if mesh.vertices:
                    actual_base_z.append(min(v.z for v in mesh.vertices))
                else:
                    actual_base_z.append(float("nan"))

            # Ardışık katlar arasında taşıyıcı duvarların (XY) üst üste
            # gelip gelmediğini kontrol et.
            #
            # NOT (FAZ 1 teşhis düzeltmesi): İlk versiyon burada "en yakın
            # nokta" (nearest-vertex) eşleştirmesi kullanıyordu. Bu YANLIŞ
            # POZİTİF üretiyordu: kapı/pencere boşlukları duvar grid'ine u
            # ekseninde ek bölünme noktaları ekliyor (bkz.
            # `WallOpeningMeshBuilder` - bir açıklığın u-sınırları TÜM
            # yükseklik boyunca sütun olarak yayılıyor), bu da bir katta
            # (örn. girişi olan zemin kat) diğerinde olmayan fazladan
            # vertex'ler oluşturuyor - gerçek duvar hattı kaymamış olsa
            # bile "en yakın nokta" 5cm'e kadar uzakta çıkabiliyor. Doğru
            # ölçüt: iki katın duvar taban çizgisinin (XY bounding box'ı)
            # aynı olup olmadığı - noktaların BİREBİR eşleşmesi değil.
            for i in range(len(actual_floor_meshes) - 1):
                top_pts = [
                    (v.x, v.y)
                    for v in actual_floor_meshes[i].vertices
                    if math.isclose(
                        v.z, max(vv.z for vv in actual_floor_meshes[i].vertices), abs_tol=1e-6
                    )
                ]
                bottom_pts = [
                    (v.x, v.y)
                    for v in actual_floor_meshes[i + 1].vertices
                    if math.isclose(
                        v.z, min(vv.z for vv in actual_floor_meshes[i + 1].vertices), abs_tol=1e-6
                    )
                ]
                if not top_pts or not bottom_pts:
                    continue
                top_minx, top_maxx = min(p[0] for p in top_pts), max(p[0] for p in top_pts)
                top_miny, top_maxy = min(p[1] for p in top_pts), max(p[1] for p in top_pts)
                bot_minx, bot_maxx = min(p[0] for p in bottom_pts), max(p[0] for p in bottom_pts)
                bot_miny, bot_maxy = min(p[1] for p in bottom_pts), max(p[1] for p in bottom_pts)
                worst = max(
                    abs(top_minx - bot_minx),
                    abs(top_maxx - bot_maxx),
                    abs(top_miny - bot_miny),
                    abs(top_maxy - bot_maxy),
                )
                wall_xy_errors.append(worst)

        z_errors = [
            abs(a - e) if not math.isnan(a) else float("inf")
            for a, e in zip(actual_base_z, expected_base_z)
        ]
        max_z_error = max(z_errors) if z_errors else 0.0
        max_wall_xy_error = max(wall_xy_errors) if wall_xy_errors else 0.0

        within_tolerance = max_z_error <= tolerance_m and max_wall_xy_error <= tolerance_m

        return FloorAlignmentReport(
            floor_count=len(floor_heights_m),
            expected_base_z=expected_base_z,
            actual_base_z=actual_base_z,
            z_errors_m=z_errors,
            max_z_error_m=max_z_error,
            tolerance_m=tolerance_m,
            within_tolerance=within_tolerance,
            wall_xy_errors_m=wall_xy_errors,
            max_wall_xy_error_m=max_wall_xy_error,
        )


# ======================================================================== #
# Çoklu-mesh (bina/şehir) rapor toplayıcı
# ======================================================================== #


@dataclass(slots=True)
class BatchQualityReport:
    reports: list[MeshQualityReport]

    @property
    def total_vertices(self) -> int:
        return sum(r.vertex_count for r in self.reports)

    @property
    def total_triangles(self) -> int:
        return sum(r.triangle_count for r in self.reports)

    @property
    def watertight_ratio(self) -> float:
        if not self.reports:
            return 1.0
        return sum(1 for r in self.reports if r.is_watertight) / len(self.reports)

    def to_markdown(self) -> str:
        lines = [
            "| Mesh | Vertex | Üçgen | Non-manifold kenar | Boundary kenar | Dejenere üçgen | Normal tutarlılık | Watertight |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in self.reports:
            lines.append(
                f"| {r.mesh_name} | {r.vertex_count} | {r.triangle_count} | "
                f"{r.non_manifold_edge_count} | {r.boundary_edge_count} | "
                f"{r.degenerate_triangle_count} | {r.normal_consistency_ratio:.3f} | "
                f"{'✅' if r.is_watertight else '❌'} |"
            )
        lines.append("")
        lines.append(
            f"Toplam: {len(self.reports)} mesh, {self.total_vertices} vertex, "
            f"{self.total_triangles} üçgen, watertight oranı = {self.watertight_ratio:.1%}"
        )
        return "\n".join(lines)


class BatchQualityAnalyzer:
    @staticmethod
    def analyze_all(meshes: list[Mesh3D]) -> BatchQualityReport:
        return BatchQualityReport(reports=[MeshQualityAnalyzer.analyze(m) for m in meshes])
