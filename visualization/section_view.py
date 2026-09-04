"""
Visualization - Section View (Phase 9)
=========================================

Binayı bir düzlemle keser: her üçgeni düzleme göre clip eder (0, 1 ya da 2
çıktı üçgeni üretir) ve kalan iki yarı-mesh'i (kesilen tarafın "iç" yüzünü
kapatan cap dahil değil — sadece geometri kesimi) döner.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..mesh_engine import Mesh3D, Vertex3D

Vec3 = tuple[float, float, float]


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _lerp_vertex(a: Vertex3D, b: Vertex3D, t: float) -> Vertex3D:
    return Vertex3D(
        x=a.x + (b.x - a.x) * t,
        y=a.y + (b.y - a.y) * t,
        z=a.z + (b.z - a.z) * t,
        uv=(
            (a.uv[0] + (b.uv[0] - a.uv[0]) * t, a.uv[1] + (b.uv[1] - a.uv[1]) * t)
            if a.uv and b.uv else None
        ),
    )


def _signed_distance(v: Vertex3D, plane_point: Vec3, plane_normal: Vec3) -> float:
    return _dot(_sub((v.x, v.y, v.z), plane_point), plane_normal)


@dataclass(slots=True)
class SectionPlane:
    point: Vec3
    normal: Vec3


class SectionView:
    """`Mesh3D`'i bir düzlemle kesip iki tarafı ayrı `Mesh3D` olarak döner."""

    @staticmethod
    def cut(mesh: Mesh3D, plane: SectionPlane) -> tuple[Mesh3D, Mesh3D]:
        """Returns (keep_side, cut_away_side) — normal yönündeki taraf 'keep_side'."""
        keep = Mesh3D(name=f"{mesh.name}_section_keep")
        away = Mesh3D(name=f"{mesh.name}_section_cutaway")

        for tri in mesh.triangles:
            verts = [mesh.vertices[i] for i in tri]
            dists = [_signed_distance(v, plane.point, plane.normal) for v in verts]

            all_keep = all(d >= 0 for d in dists)
            all_away = all(d < 0 for d in dists)

            if all_keep:
                _append_triangle(keep, verts)
                continue
            if all_away:
                _append_triangle(away, verts)
                continue

            # Karışık durum: üçgeni düzlemle clip et.
            keep_pts, away_pts = _clip_triangle(verts, dists)
            for poly, target in ((keep_pts, keep), (away_pts, away)):
                _append_polygon_as_fan(target, poly)

        return keep, away


def _append_triangle(mesh: Mesh3D, verts: list[Vertex3D]) -> None:
    base = len(mesh.vertices)
    mesh.vertices.extend(verts)
    mesh.triangles.append((base, base + 1, base + 2))


def _append_polygon_as_fan(mesh: Mesh3D, poly: list[Vertex3D]) -> None:
    if len(poly) < 3:
        return
    base = len(mesh.vertices)
    mesh.vertices.extend(poly)
    for i in range(1, len(poly) - 1):
        mesh.triangles.append((base, base + i, base + i + 1))


def _clip_triangle(verts: list[Vertex3D], dists: list[float]) -> tuple[list[Vertex3D], list[Vertex3D]]:
    """Sutherland-Hodgman tarzı tek-üçgen clip. `keep` (d>=0) ve `away` (d<0)
    poligonlarını (fan-triangulate edilebilir sıralı köşe listesi) döner."""
    keep_poly: list[Vertex3D] = []
    away_poly: list[Vertex3D] = []
    n = len(verts)

    for i in range(n):
        cur_v, cur_d = verts[i], dists[i]
        nxt_v, nxt_d = verts[(i + 1) % n], dists[(i + 1) % n]

        if cur_d >= 0:
            keep_poly.append(cur_v)
        else:
            away_poly.append(cur_v)

        if (cur_d >= 0) != (nxt_d >= 0):
            t = cur_d / (cur_d - nxt_d)
            inter = _lerp_vertex(cur_v, nxt_v, t)
            keep_poly.append(inter)
            away_poly.append(inter)

    return keep_poly, away_poly
