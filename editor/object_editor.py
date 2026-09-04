"""
Object Editor
=============

Roadmap Phase 8 - "Object Editor": Move, Rotate, Scale, Align, Mirror,
Snap, Duplicate, Grouping, Prefab, Hierarchy.

Sahnedeki her nesne (bina, ağaç, yol parçası, ...) bir `Mesh3D` (Phase 2
`mesh_engine`) sarmalayan bir `SceneNode`'dur. `SceneNode` kendi
transformunu (position/rotation/scale) taşır; gerçek vertex mutasyonu
yalnızca `bake()` çağrıldığında (veya export sırasında) uygulanır - bu,
transform komutlarının ucuz (O(1)) olmasını sağlar ve undo/redo'yu basit
tutar (yalnızca transform state'i geri alınır, mesh yeniden hesaplanmaz).

Hiyerarşi (parent/children) desteklenir: bir parent taşındığında/
döndürüldüğünde/ölçeklendiğinde çocukların `world transform`'u da değişir
(transform miras alınır, `local` transform çocuklarda sabit kalır).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from ..mesh_engine import Mesh3D, Vertex3D
from .commands import EditorCommand, FunctionCommand


# ============================================================================ #
# Vec3
# ============================================================================ #

@dataclass(slots=True, frozen=True)
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, s: float) -> "Vec3":
        return Vec3(self.x * s, self.y * s, self.z * s)

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def length(self) -> float:
        return math.sqrt(self.x ** 2 + self.y ** 2 + self.z ** 2)


ZERO = Vec3(0.0, 0.0, 0.0)
ONE = Vec3(1.0, 1.0, 1.0)


# ============================================================================ #
# SceneNode (Hierarchy)
# ============================================================================ #

@dataclass(slots=True)
class SceneNode:
    """Sahnedeki tek bir nesne. `mesh` local-space'te (origin merkezli)
    saklanır; `position/rotation_deg/scale` local transformdur (parent'a
    göre)."""

    node_id: str
    name: str
    mesh: Mesh3D | None = None
    position: Vec3 = field(default_factory=lambda: ZERO)
    rotation_deg: Vec3 = field(default_factory=lambda: ZERO)  # euler (x,y,z)
    scale: Vec3 = field(default_factory=lambda: ONE)
    parent: "SceneNode | None" = None
    children: list["SceneNode"] = field(default_factory=list)
    is_prefab_instance: bool = False
    prefab_source: "str | None" = None

    # -- hiyerarşi -------------------------------------------------------- #
    def add_child(self, child: "SceneNode") -> None:
        if child.parent is not None:
            child.parent.children.remove(child)
        child.parent = self
        self.children.append(child)

    def remove_child(self, child: "SceneNode") -> None:
        if child in self.children:
            self.children.remove(child)
            child.parent = None

    def world_position(self) -> Vec3:
        """Parent zincirini toplayarak world-space konum (rotasyon/ölçek
        etkisi basitleştirilmiştir - yalnızca konum zinciri toplanır;
        tam bir 4x4 matris pipeline'ı Phase 9 render katmanında kurulur)."""
        pos = self.position
        node = self.parent
        while node is not None:
            pos = pos + node.position
            node = node.parent
        return pos

    def descendants(self) -> Iterable["SceneNode"]:
        for child in self.children:
            yield child
            yield from child.descendants()

    # -- bake --------------------------------------------------------------- #
    def bake(self) -> Mesh3D:
        """Local transformu gerçek vertex konumlarına uygulayarak yeni bir
        `Mesh3D` üretir (world-space, parent transformları dahil)."""
        if self.mesh is None:
            return Mesh3D(name=self.name)
        wp = self.world_position()
        rx, ry, rz = math.radians(self.rotation_deg.x), math.radians(self.rotation_deg.y), math.radians(self.rotation_deg.z)
        sx, sy, sz = self.scale.x, self.scale.y, self.scale.z

        def transform(v: Vertex3D) -> Vertex3D:
            x, y, z = v.x * sx, v.y * sy, v.z * sz
            # rotate Z
            x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
            # rotate Y
            x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
            # rotate X
            y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
            return Vertex3D(x + wp.x, y + wp.y, z + wp.z, v.normal, v.tangent, v.uv)

        return Mesh3D(
            vertices=[transform(v) for v in self.mesh.vertices],
            triangles=list(self.mesh.triangles),
            uvs=list(self.mesh.uvs),
            name=self.name,
        )


# ============================================================================ #
# Prefab
# ============================================================================ #

@dataclass(slots=True)
class Prefab:
    """Yeniden kullanılabilir nesne şablonu (örn. "standart ağaç",
    "standart sokak lambası"). `PrefabLibrary` bunları isimle saklar;
    `ObjectEditor.instantiate_prefab` her çağrıda bağımsız bir `SceneNode`
    kopyası üretir."""

    prefab_id: str
    mesh: Mesh3D
    default_scale: Vec3 = field(default_factory=lambda: ONE)


class PrefabLibrary:
    def __init__(self) -> None:
        self._prefabs: dict[str, Prefab] = {}

    def register(self, prefab: Prefab) -> None:
        self._prefabs[prefab.prefab_id] = prefab

    def get(self, prefab_id: str) -> Prefab:
        return self._prefabs[prefab_id]

    def __contains__(self, prefab_id: str) -> bool:
        return prefab_id in self._prefabs


# ============================================================================ #
# ObjectEditor
# ============================================================================ #

_AUTO_ID = 0


def _next_id(prefix: str) -> str:
    global _AUTO_ID
    _AUTO_ID += 1
    return f"{prefix}_{_AUTO_ID}"


class ObjectEditor:
    """Sahne nesneleri üzerinde işlem yapan, her operasyonu bir
    `EditorCommand` olarak döndüren editör. Komutlar çağıran taraf
    (`UndoRedoStack.execute`) tarafından uygulanır - `ObjectEditor`'ın
    kendisi state tutmaz (stateless operasyon fabrikası), bu da onu test
    edilebilir ve editör UI'sinden bağımsız kılar.
    """

    # -- Move / Rotate / Scale -------------------------------------------- #
    @staticmethod
    def move(node: SceneNode, delta: Vec3) -> EditorCommand:
        before = node.position

        def do() -> None:
            node.position = before + delta

        def undo() -> None:
            node.position = before

        return FunctionCommand(do, undo, label=f"move:{node.name}")

    @staticmethod
    def set_position(node: SceneNode, new_position: Vec3) -> EditorCommand:
        before = node.position

        def do() -> None:
            node.position = new_position

        def undo() -> None:
            node.position = before

        return FunctionCommand(do, undo, label=f"set_position:{node.name}")

    @staticmethod
    def rotate(node: SceneNode, delta_deg: Vec3) -> EditorCommand:
        before = node.rotation_deg

        def do() -> None:
            node.rotation_deg = before + delta_deg

        def undo() -> None:
            node.rotation_deg = before

        return FunctionCommand(do, undo, label=f"rotate:{node.name}")

    @staticmethod
    def scale(node: SceneNode, factor: Vec3) -> EditorCommand:
        before = node.scale

        def do() -> None:
            node.scale = Vec3(before.x * factor.x, before.y * factor.y, before.z * factor.z)

        def undo() -> None:
            node.scale = before

        return FunctionCommand(do, undo, label=f"scale:{node.name}")

    # -- Align -------------------------------------------------------------- #
    @staticmethod
    def align(nodes: list[SceneNode], axis: str = "x", mode: str = "center") -> EditorCommand:
        """Birden fazla nesneyi verilen eksende hizalar.
        mode: 'min' | 'center' | 'max'."""
        if not nodes:
            return FunctionCommand(lambda: None, lambda: None, label="align:noop")

        before = {n.node_id: n.position for n in nodes}
        axis_get = {"x": lambda p: p.x, "y": lambda p: p.y, "z": lambda p: p.z}[axis]
        values = [axis_get(n.position) for n in nodes]
        if mode == "min":
            target = min(values)
        elif mode == "max":
            target = max(values)
        else:
            target = sum(values) / len(values)

        def apply(v: float, p: Vec3) -> Vec3:
            if axis == "x":
                return Vec3(v, p.y, p.z)
            if axis == "y":
                return Vec3(p.x, v, p.z)
            return Vec3(p.x, p.y, v)

        def do() -> None:
            for n in nodes:
                n.position = apply(target, n.position)

        def undo() -> None:
            for n in nodes:
                n.position = before[n.node_id]

        return FunctionCommand(do, undo, label=f"align:{axis}:{mode}")

    # -- Mirror --------------------------------------------------------------- #
    @staticmethod
    def mirror(node: SceneNode, axis: str = "x") -> EditorCommand:
        """Bir eksende ölçeği -1 ile çarparak aynalar (mesh flip; local
        space'te - render katmanı winding order'ı buna göre çevirmelidir)."""
        before = node.scale

        def flipped() -> Vec3:
            if axis == "x":
                return Vec3(-before.x, before.y, before.z)
            if axis == "y":
                return Vec3(before.x, -before.y, before.z)
            return Vec3(before.x, before.y, -before.z)

        def do() -> None:
            node.scale = flipped()

        def undo() -> None:
            node.scale = before

        return FunctionCommand(do, undo, label=f"mirror:{axis}:{node.name}")

    # -- Snap ----------------------------------------------------------------- #
    @staticmethod
    def snap_to_grid(node: SceneNode, grid_size: float) -> EditorCommand:
        before = node.position

        def do() -> None:
            node.position = Vec3(
                round(before.x / grid_size) * grid_size,
                round(before.y / grid_size) * grid_size,
                round(before.z / grid_size) * grid_size,
            )

        def undo() -> None:
            node.position = before

        return FunctionCommand(do, undo, label=f"snap:{node.name}")

    # -- Duplicate -------------------------------------------------------------- #
    @staticmethod
    def duplicate(node: SceneNode, offset: Vec3 = Vec3(1.0, 0.0, 0.0)) -> tuple[SceneNode, EditorCommand]:
        """Yeni bir `SceneNode` kopyası oluşturur ve onu parent'a ekleyen
        komutu döndürür. Kopyanın kendisi çağıran tarafa hemen döner (id
        atamak/referans tutmak için), ama sahneye eklenmesi `do()`
        çağrılana kadar gerçekleşmez."""
        clone = SceneNode(
            node_id=_next_id("obj"),
            name=f"{node.name}_copy",
            mesh=node.mesh.clone() if node.mesh else None,
            position=node.position + offset,
            rotation_deg=node.rotation_deg,
            scale=node.scale,
        )
        parent = node.parent

        def do() -> None:
            if parent is not None:
                parent.add_child(clone)

        def undo() -> None:
            if parent is not None:
                parent.remove_child(clone)

        return clone, FunctionCommand(do, undo, label=f"duplicate:{node.name}")

    # -- Grouping --------------------------------------------------------------- #
    @staticmethod
    def group(nodes: list[SceneNode], group_name: str = "group") -> tuple[SceneNode, EditorCommand]:
        """Birden fazla nesneyi ortak bir parent (`SceneNode`, mesh=None)
        altında toplar - grubu taşımak tüm çocukları taşır."""
        group_node = SceneNode(node_id=_next_id("group"), name=group_name)
        original_parents = {n.node_id: n.parent for n in nodes}

        def do() -> None:
            for n in nodes:
                group_node.add_child(n)

        def undo() -> None:
            for n in nodes:
                parent = original_parents[n.node_id]
                if parent is not None:
                    parent.add_child(n)
                else:
                    group_node.remove_child(n)

        return group_node, FunctionCommand(do, undo, label=f"group:{group_name}")

    @staticmethod
    def ungroup(group_node: SceneNode) -> EditorCommand:
        children_snapshot = list(group_node.children)
        parent = group_node.parent

        def do() -> None:
            for c in children_snapshot:
                if parent is not None:
                    parent.add_child(c)
                else:
                    group_node.remove_child(c)

        def undo() -> None:
            for c in children_snapshot:
                group_node.add_child(c)

        return FunctionCommand(do, undo, label=f"ungroup:{group_node.name}")

    # -- Prefab --------------------------------------------------------------- #
    @staticmethod
    def instantiate_prefab(
        library: PrefabLibrary, prefab_id: str, position: Vec3 = ZERO
    ) -> tuple[SceneNode, Prefab]:
        prefab = library.get(prefab_id)
        node = SceneNode(
            node_id=_next_id("inst"),
            name=f"{prefab_id}_instance",
            mesh=prefab.mesh,  # prefab mesh'i paylaşılır (kopyalanmaz - hafıza tasarrufu)
            position=position,
            scale=prefab.default_scale,
            is_prefab_instance=True,
            prefab_source=prefab_id,
        )
        return node, prefab

    # -- Hierarchy sorgulama --------------------------------------------------- #
    @staticmethod
    def flatten(root: SceneNode) -> list[SceneNode]:
        """Root dahil tüm hiyerarşiyi düz listeye çevirir (depth-first)."""
        result = [root]
        result.extend(root.descendants())
        return result
