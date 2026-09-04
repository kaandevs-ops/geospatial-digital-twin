"""
Input Bindings
==============

Roadmap Phase 8 (Editor) + Roadmap V3 - Faz D5.

`ObjectEditor` (move/rotate/scale) ve `gizmo` (ray -> tek-eksenli hareket)
hazırdı, ama ikisini gerçek bir mouse/klavye etkileşim **dizisine**
(down -> move* -> up) bağlayan bir katman eksikti; bu olmadan editör
"mantık var, kullanılamıyor" durumundaydı (bkz. ROADMAP_V3 Faz D5).

`GizmoInputSession`, tek bir sürükleme oturumunu yönetir:

    mouse-down (hangi eksen tıklandı)
        -> N x mouse-move (canlı önizleme; `SceneNode` anlık güncellenir,
           ama undo/redo yığınına HENÜZ eklenmez)
        -> mouse-up (nihai transform TEK bir `EditorCommand` olarak
           undo/redo yığınına eklenir - kullanıcı Ctrl+Z yaptığında tüm
           sürüklemeyi tek adımda geri alır, ara mouse-move'ların her biri
           ayrı bir undo adımı OLMAZ).

Bu, tüm modern 3D DCC araçlarının (Blender, Unity, vb.) standart
davranışıdır ve roadmap'in "gizmo matematiği" + "input-binding" ihtiyacını
birlikte karşılar. Klavye kısayolları (`KeyBindingRegistry`) için de basit,
genişletilebilir bir eşleme tablosu sağlanır (Ctrl+Z/Ctrl+Y, G/R/S gibi
Blender-tarzı mod kısayolları, W/E/R gibi Unity-tarzı gizmo seçimi).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from .commands import EditorCommand, FunctionCommand, UndoRedoStack
from .gizmo import AXES, Ray, RotateGizmo, ScaleGizmo, TranslateGizmo
from .object_editor import SceneNode, Vec3


class GizmoMode(Enum):
    """Roadmap: Move / Rotate / Scale gizmo modları."""

    TRANSLATE = "translate"
    ROTATE = "rotate"
    SCALE = "scale"


# ============================================================================ #
# Mouse Event'leri (render/DOM-bağımsız soyutlama)
# ============================================================================ #


@dataclass(slots=True)
class MouseDownEvent:
    """Viewer, gizmo handle picking'ini (hangi eksene tıklandığını) kendi
    render tarafında (örn. renk-kodlu picking buffer veya bounding-box ray
    testiyle) belirler ve sonucu burada `axis` olarak iletir - bu katman
    picking algoritmasından bağımsızdır."""

    ray: Ray
    axis: str  # "x" | "y" | "z"


@dataclass(slots=True)
class MouseMoveEvent:
    ray: Ray


@dataclass(slots=True)
class MouseUpEvent:
    pass


# ============================================================================ #
# GizmoInputSession
# ============================================================================ #


class GizmoInputSession:
    """Roadmap V3 / Faz D5 kabul kriteri (A8): "mouse down gizmo-x
    ekseni üstünde -> 5 birim sürükle -> mouse up" senaryosunda nesnenin
    doğru eksende doğru miktarda hareket ettiğini garanti eder. Test:
    `tests/test_phaseD5_editor_gizmo_input.py`.

    Kullanım:
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)
        session.on_mouse_down(MouseDownEvent(ray=ray0, axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=ray1))   # canlı önizleme
        session.on_mouse_move(MouseMoveEvent(ray=ray2))   # canlı önizleme
        command = session.on_mouse_up(MouseUpEvent())     # tek undo adımı
    """

    def __init__(
        self, node: SceneNode, mode: GizmoMode, undo_stack: UndoRedoStack | None = None
    ) -> None:
        self.node = node
        self.mode = mode
        self.undo_stack = undo_stack

        self._active = False
        self._axis: str | None = None
        self._start_ray: Ray | None = None
        self._gizmo_origin: tuple[float, float, float] = node.position.as_tuple()
        self._start_position: Vec3 = node.position
        self._start_rotation: Vec3 = node.rotation_deg
        self._start_scale: Vec3 = node.scale

    @property
    def is_active(self) -> bool:
        return self._active

    # -- yaşam döngüsü ------------------------------------------------------ #
    def on_mouse_down(self, event: MouseDownEvent) -> None:
        if event.axis not in AXES:
            raise ValueError(f"Bilinmeyen gizmo ekseni: {event.axis!r}")
        self._axis = event.axis
        self._start_ray = event.ray
        # Sürüklemenin başlangıcındaki durumu "sıfır nokta" olarak sakla -
        # tüm mouse-move'lar bu referansa göre hesaplanır (kümülatif hata
        # birikmez, her hareket doğrudan başlangıca göre yeniden hesaplanır).
        self._gizmo_origin = self.node.position.as_tuple()
        self._start_position = self.node.position
        self._start_rotation = self.node.rotation_deg
        self._start_scale = self.node.scale
        self._active = True

    def on_mouse_move(self, event: MouseMoveEvent) -> None:
        if not self._active or self._axis is None or self._start_ray is None:
            return
        axis_vec = AXES[self._axis]

        if self.mode is GizmoMode.TRANSLATE:
            delta_scalar = TranslateGizmo.axis_drag_delta(
                self._gizmo_origin,
                self._axis,
                self._start_ray,
                event.ray,
            )
            self.node.position = Vec3(
                self._start_position.x + axis_vec[0] * delta_scalar,
                self._start_position.y + axis_vec[1] * delta_scalar,
                self._start_position.z + axis_vec[2] * delta_scalar,
            )
        elif self.mode is GizmoMode.ROTATE:
            angle_deg = RotateGizmo.axis_drag_angle_deg(
                self._gizmo_origin,
                self._axis,
                self._start_ray,
                event.ray,
            )
            self.node.rotation_deg = Vec3(
                self._start_rotation.x + axis_vec[0] * angle_deg,
                self._start_rotation.y + axis_vec[1] * angle_deg,
                self._start_rotation.z + axis_vec[2] * angle_deg,
            )
        else:  # SCALE
            factor = ScaleGizmo.axis_drag_factor(
                self._gizmo_origin,
                self._axis,
                self._start_ray,
                event.ray,
            )
            # Yalnızca seçilen eksen ölçeklenir; diğer eksenler 1.0 çarpanı
            # alır (tek-eksenli scale handle davranışı - standart DCC).
            per_axis_factor = tuple(1.0 + (factor - 1.0) * axis_vec[i] for i in range(3))
            self.node.scale = Vec3(
                self._start_scale.x * per_axis_factor[0],
                self._start_scale.y * per_axis_factor[1],
                self._start_scale.z * per_axis_factor[2],
            )

    def on_mouse_up(self, event: MouseUpEvent) -> EditorCommand | None:
        if not self._active:
            return None
        self._active = False
        node = self.node
        axis = self._axis
        mode = self.mode

        if mode is GizmoMode.TRANSLATE:
            end_value, start_value = node.position, self._start_position
            setter = lambda v: setattr(node, "position", v)  # noqa: E731
        elif mode is GizmoMode.ROTATE:
            end_value, start_value = node.rotation_deg, self._start_rotation
            setter = lambda v: setattr(node, "rotation_deg", v)  # noqa: E731
        else:
            end_value, start_value = node.scale, self._start_scale
            setter = lambda v: setattr(node, "scale", v)  # noqa: E731

        # mouse-move sırasında zaten uygulanmış olan canlı önizlemeyi geri
        # alıp, nihai değeri TEK bir `EditorCommand` üzerinden (yeniden)
        # uygulayarak undo/redo tutarlılığını sağla.
        setter(start_value)

        def do() -> None:
            setter(end_value)

        def undo() -> None:
            setter(start_value)

        command = FunctionCommand(do, undo, label=f"gizmo:{mode.value}:{axis}:{node.name}")
        if self.undo_stack is not None:
            self.undo_stack.execute(command)
        else:
            command.do()

        self._axis = None
        self._start_ray = None
        return command

    def cancel(self) -> None:
        """Sürüklemeyi undo/redo yığınına eklemeden iptal eder (örn. Esc
        tuşu) - nesne sürükleme başlangıcındaki durumuna döner."""
        if not self._active:
            return
        node = self.node
        node.position = self._start_position
        node.rotation_deg = self._start_rotation
        node.scale = self._start_scale
        self._active = False
        self._axis = None
        self._start_ray = None


# ============================================================================ #
# Klavye Kısayolları
# ============================================================================ #


@dataclass(slots=True)
class KeyBindingRegistry:
    """Basit, genişletilebilir klavye kısayolu -> eylem eşlemesi.
    Render/DOM'dan bağımsızdır; viewer tarafı gerçek `keydown` event'ini
    normalize edilmiş bir tuş adına (`"ctrl+z"`, `"g"`, ...) çevirip
    `dispatch()`'e iletir.

    Varsayılan (Blender-tarzı) eşlemeler `default_bindings()` ile
    kurulabilir: G/R/S gizmo modu, Ctrl+Z/Ctrl+Y undo/redo."""

    bindings: dict[str, Callable[[], None]] = field(default_factory=dict)

    def bind(self, key_combo: str, action: Callable[[], None]) -> None:
        self.bindings[key_combo.lower()] = action

    def dispatch(self, key_combo: str) -> bool:
        """Eşlenen bir eylem varsa çalıştırır ve `True` döndürür; yoksa
        `False` (viewer başka bir varsayılan davranışa düşebilir)."""
        action = self.bindings.get(key_combo.lower())
        if action is None:
            return False
        action()
        return True

    @classmethod
    def default_bindings(
        cls,
        set_mode: Callable[[GizmoMode], None],
        undo_stack: UndoRedoStack,
    ) -> KeyBindingRegistry:
        registry = cls()
        registry.bind("g", lambda: set_mode(GizmoMode.TRANSLATE))
        registry.bind("r", lambda: set_mode(GizmoMode.ROTATE))
        registry.bind("s", lambda: set_mode(GizmoMode.SCALE))
        registry.bind("ctrl+z", undo_stack.undo)
        registry.bind("ctrl+y", undo_stack.redo)
        registry.bind("ctrl+shift+z", undo_stack.redo)
        return registry
