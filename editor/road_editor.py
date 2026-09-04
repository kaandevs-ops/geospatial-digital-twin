"""
Road Editor
===========

Roadmap Phase 8 - "Road Editor" (spline tabanlı).

Kullanıcı bir dizi kontrol noktası (`control_points`) yerleştirir; editör
bunları **Catmull-Rom spline** ile yumuşak bir merkez hattına (centerline)
dönüştürür, sonra bu hattı sabit genişlikte bir şerit olarak `Mesh3D`'ye
extrude eder (`mesh_engine.MeshBuilder` deseniyle uyumlu, ama yol için
"tape extrusion" - iki paralel kenar + üçgenleme - roadmap'in `Mesh
Builder`'ından ayrı, yol-özel bir extrusion).

Kontrol noktası ekleme/taşıma/silme işlemleri `EditorCommand` olarak
üretilir; spline/mesh her komuttan sonra `RoadEditor.rebuild()` ile lazy
şekilde yeniden hesaplanır (çağıran taraf sorumludur - performans için
otomatik tetiklenmez, çünkü çoklu ardışık düzenlemede tekrar tekrar
yeniden hesaplamayı önler).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core_engine.geometry_engine import Point2D
from ..mesh_engine import Mesh3D, UVGenerator, Vertex3D
from .commands import EditorCommand, FunctionCommand

# ============================================================================ #
# Catmull-Rom Spline
# ============================================================================ #


def catmull_rom_point(p0: Point2D, p1: Point2D, p2: Point2D, p3: Point2D, t: float) -> Point2D:
    """Tek bir Catmull-Rom segmentinde `t` (0..1) parametresine karşılık
    gelen noktayı hesaplar (uniform Catmull-Rom, tau=0.5)."""
    t2 = t * t
    t3 = t2 * t
    x = 0.5 * (
        (2 * p1.x)
        + (-p0.x + p2.x) * t
        + (2 * p0.x - 5 * p1.x + 4 * p2.x - p3.x) * t2
        + (-p0.x + 3 * p1.x - 3 * p2.x + p3.x) * t3
    )
    y = 0.5 * (
        (2 * p1.y)
        + (-p0.y + p2.y) * t
        + (2 * p0.y - 5 * p1.y + 4 * p2.y - p3.y) * t2
        + (-p0.y + 3 * p1.y - 3 * p2.y + p3.y) * t3
    )
    return Point2D(x, y)


def catmull_rom_spline(
    control_points: list[Point2D], samples_per_segment: int = 12
) -> list[Point2D]:
    """Kontrol noktası dizisini yumuşak bir polylineye (samples_per_segment
    örnek/segment) dönüştürür. Uçlarda faz kaybını önlemek için ilk/son
    nokta kopyalanarak "phantom" kontrol noktaları eklenir (standart
    Catmull-Rom açık-uç tekniği)."""
    n = len(control_points)
    if n < 2:
        return list(control_points)
    if n == 2:
        # Tek segment: doğrusal enterpolasyon yeterli (spline eğrilik
        # için en az 2 komşu nokta gerektirir).
        p0, p1 = control_points
        return [
            Point2D(
                p0.x + (p1.x - p0.x) * t / samples_per_segment,
                p0.y + (p1.y - p0.y) * t / samples_per_segment,
            )
            for t in range(samples_per_segment + 1)
        ]

    extended = [control_points[0]] + control_points + [control_points[-1]]
    result: list[Point2D] = []
    for i in range(1, len(extended) - 2):
        p0, p1, p2, p3 = extended[i - 1], extended[i], extended[i + 1], extended[i + 2]
        segment_samples = samples_per_segment if i == 1 else samples_per_segment
        start = 0 if i == 1 else 1
        for s in range(start, segment_samples + 1):
            t = s / segment_samples
            result.append(catmull_rom_point(p0, p1, p2, p3, t))
    return result


# ============================================================================ #
# Road
# ============================================================================ #


@dataclass(slots=True)
class Road:
    road_id: str
    control_points: list[Point2D] = field(default_factory=list)
    width_m: float = 6.0
    elevation_z: float = 0.0
    samples_per_segment: int = 12
    name: str = "road"

    def centerline(self) -> list[Point2D]:
        return catmull_rom_spline(self.control_points, self.samples_per_segment)

    def to_mesh(self) -> Mesh3D:
        """Merkez hattını `width_m` genişliğinde bir şerit olarak
        extrude eder (her nokta çiftinin normaline dik ofsetle sol/sağ
        kenar üretilir, sonra dörtgen şeritler üçgenlenir)."""
        line = self.centerline()
        if len(line) < 2:
            return Mesh3D(name=self.name)

        half_w = self.width_m / 2.0
        left: list[Point2D] = []
        right: list[Point2D] = []
        for i, p in enumerate(line):
            if i == 0:
                direction = line[i + 1] - p
            elif i == len(line) - 1:
                direction = p - line[i - 1]
            else:
                direction = line[i + 1] - line[i - 1]
            length = (direction.x**2 + direction.y**2) ** 0.5 or 1.0
            nx, ny = -direction.y / length, direction.x / length
            left.append(Point2D(p.x + nx * half_w, p.y + ny * half_w))
            right.append(Point2D(p.x - nx * half_w, p.y - ny * half_w))

        vertices: list[Vertex3D] = []
        for p in left:
            vertices.append(Vertex3D(p.x, p.y, self.elevation_z))
        for p in right:
            vertices.append(Vertex3D(p.x, p.y, self.elevation_z))

        n = len(line)
        triangles = []
        for i in range(n - 1):
            l0, l1 = i, i + 1
            r0, r1 = n + i, n + i + 1
            triangles.append((l0, r0, l1))
            triangles.append((l1, r0, r1))

        mesh = Mesh3D(vertices=vertices, triangles=triangles, name=self.name)
        # ROADMAP_V7.md'nin son "Kalan" maddesi: yol şeridi mesh'i daha önce
        # hiç UV atamıyordu - `UVGenerator.planar_mapping` (mevcut,
        # değiştirilmedi) yukarıdan bakış düzlemsel izdüşümüyle gerçek doku
        # koordinatı kazandırır (asfalt/yol markası dokuları için).
        return UVGenerator.planar_mapping(mesh, axis="z")


# ============================================================================ #
# RoadEditor
# ============================================================================ #


class RoadEditor:
    """`Road` kontrol noktaları üzerinde ekleme/taşıma/silme işlemleri.
    Her metod bir `EditorCommand` döndürür; mesh yeniden üretimi çağıran
    tarafın sorumluluğundadır (`road.to_mesh()`)."""

    @staticmethod
    def add_point(road: Road, point: Point2D, index: int | None = None) -> EditorCommand:
        insert_at = len(road.control_points) if index is None else index

        def do() -> None:
            road.control_points.insert(insert_at, point)

        def undo() -> None:
            del road.control_points[insert_at]

        return FunctionCommand(do, undo, label=f"road:add_point:{road.road_id}")

    @staticmethod
    def move_point(road: Road, index: int, new_position: Point2D) -> EditorCommand:
        before = road.control_points[index]

        def do() -> None:
            road.control_points[index] = new_position

        def undo() -> None:
            road.control_points[index] = before

        return FunctionCommand(do, undo, label=f"road:move_point:{road.road_id}")

    @staticmethod
    def remove_point(road: Road, index: int) -> EditorCommand:
        removed = road.control_points[index]

        def do() -> None:
            del road.control_points[index]

        def undo() -> None:
            road.control_points.insert(index, removed)

        return FunctionCommand(do, undo, label=f"road:remove_point:{road.road_id}")

    @staticmethod
    def set_width(road: Road, width_m: float) -> EditorCommand:
        before = road.width_m

        def do() -> None:
            road.width_m = width_m

        def undo() -> None:
            road.width_m = before

        return FunctionCommand(do, undo, label=f"road:set_width:{road.road_id}")
