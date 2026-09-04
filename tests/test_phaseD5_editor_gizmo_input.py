"""Faz D5 — Editor: Input-Binding + Gizmo Matematiği testleri.

Roadmap V3, Faz D5 kabul kriteri (A8): "mouse down gizmo-x ekseni üstünde
-> 5 birim sürükle -> mouse up" senaryosunda nesnenin doğru eksende doğru
miktarda hareket ettiği doğrulanmalı. Bu dosya:

    1) `gizmo` modülünün saf matematiğini (translate/rotate/scale) sentetik
       ray'lerle,
    2) `GizmoInputSession`'ın tam mouse-down -> move* -> mouse-up
       yaşam döngüsünü (translate/rotate/scale, üç mod için de),
    3) undo/redo ile bütünleşmenin (tek sürükleme = tek undo adımı,
       ara mouse-move'ların undo yığınına ayrı ayrı girmediği),
    4) `cancel()` (Esc) davranışının,
    5) `KeyBindingRegistry`'nin varsayılan kısayollarının (G/R/S, Ctrl+Z/Y)
doğru çalıştığını doğrular.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.editor.gizmo import Ray, TranslateGizmo, RotateGizmo, ScaleGizmo
from harita.editor.object_editor import SceneNode, Vec3
from harita.editor.commands import UndoRedoStack
from harita.editor.input_bindings import (
    GizmoInputSession, GizmoMode, KeyBindingRegistry,
    MouseDownEvent, MouseMoveEvent, MouseUpEvent,
)


def _make_node(position: Vec3 = Vec3(0.0, 0.0, 0.0)) -> SceneNode:
    return SceneNode(node_id="n1", name="test_node", position=position)


def _looking_down_ray(x: float, y: float, z: float = 20.0) -> Ray:
    """Yukarıdan aşağı (-z) bakan, XY düzleminde (x,y) noktasından geçen
    sentetik bir mouse-picking ray'i - testlerde 'fareyi (x,y)'ye sürükle'
    senaryosunu temsil eder."""
    return Ray.create((x, y, z), (0.0, 0.0, -1.0))


# ============================================================================ #
# 1) Saf gizmo matematiği
# ============================================================================ #

class TestGizmoMath:
    def test_translate_axis_drag_delta_along_x(self):
        origin = (0.0, 0.0, 0.0)
        r1 = _looking_down_ray(x=5.0, y=0.0)
        r2 = _looking_down_ray(x=10.0, y=0.0)
        assert math.isclose(
            TranslateGizmo.axis_drag_delta(origin, "x", r1, r2), 5.0, abs_tol=1e-9
        )

    def test_translate_axis_drag_ignores_orthogonal_movement(self):
        # y ekseninde hareket, x ekseni sürüklemesini etkilememeli.
        origin = (0.0, 0.0, 0.0)
        r1 = _looking_down_ray(x=5.0, y=0.0)
        r2 = _looking_down_ray(x=5.0, y=100.0)
        assert math.isclose(
            TranslateGizmo.axis_drag_delta(origin, "x", r1, r2), 0.0, abs_tol=1e-9
        )

    def test_rotate_axis_drag_angle_90_degrees(self):
        origin = (0.0, 0.0, 0.0)
        r_start = _looking_down_ray(x=1.0, y=0.0)
        r_current = _looking_down_ray(x=0.0, y=1.0)
        angle = RotateGizmo.axis_drag_angle_deg(origin, "z", r_start, r_current)
        assert math.isclose(angle, 90.0, abs_tol=1e-6)

    def test_scale_axis_drag_factor_doubling(self):
        origin = (0.0, 0.0, 0.0)
        r1 = _looking_down_ray(x=5.0, y=0.0)
        r2 = _looking_down_ray(x=10.0, y=0.0)
        assert math.isclose(
            ScaleGizmo.axis_drag_factor(origin, "x", r1, r2), 2.0, abs_tol=1e-9
        )


# ============================================================================ #
# 2) GizmoInputSession: A8 kabul kriteri
# ============================================================================ #

class TestGizmoInputSessionTranslate:
    def test_mouse_down_drag_5_units_on_x_moves_object_correctly(self):
        """Roadmap A8: mouse down gizmo-x ekseni üstünde -> 5 birim
        sürükle -> mouse up -> nesne YALNIZCA x ekseninde +5 hareket eder."""
        node = _make_node(Vec3(0.0, 0.0, 0.0))
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=5.0, y=0.0)))
        command = session.on_mouse_up(MouseUpEvent())

        assert node.position.x == 5.0
        assert node.position.y == 0.0
        assert node.position.z == 0.0
        assert command is not None
        assert undo_stack.can_undo()

    def test_intermediate_mouse_moves_are_live_preview_only(self):
        """Sürükleme sırasındaki her mouse-move undo yığınına AYRI AYRI
        girmemeli - yalnızca mouse-up'ta TEK bir komut eklenmeli."""
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="x"))
        for x in (1.0, 2.0, 3.0, 4.0, 5.0):
            session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=x, y=0.0)))
            assert len(undo_stack) == 0  # henüz hiçbir undo adımı eklenmedi
        session.on_mouse_up(MouseUpEvent())

        assert len(undo_stack) == 1
        assert node.position.x == 5.0

    def test_undo_reverts_full_drag_in_one_step(self):
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=5.0, y=0.0)))
        session.on_mouse_up(MouseUpEvent())

        assert node.position.x == 5.0
        undo_stack.undo()
        assert node.position.x == 0.0
        undo_stack.redo()
        assert node.position.x == 5.0

    def test_drag_on_y_axis_only_moves_y(self):
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="y"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=0.0, y=7.0)))
        session.on_mouse_up(MouseUpEvent())

        assert node.position.x == 0.0
        assert node.position.y == 7.0
        assert node.position.z == 0.0

    def test_cancel_reverts_without_undo_entry(self):
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=5.0, y=0.0)))
        assert node.position.x == 5.0  # canlı önizleme uygulanmış

        session.cancel()

        assert node.position.x == 0.0
        assert not undo_stack.can_undo()
        assert not session.is_active


class TestGizmoInputSessionRotateScale:
    def test_rotate_session_applies_only_to_selected_axis(self):
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.ROTATE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=1.0, y=0.0), axis="z"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=0.0, y=1.0)))
        session.on_mouse_up(MouseUpEvent())

        assert math.isclose(node.rotation_deg.z, 90.0, abs_tol=1e-6)
        assert node.rotation_deg.x == 0.0
        assert node.rotation_deg.y == 0.0

    def test_scale_session_applies_only_to_selected_axis(self):
        node = _make_node()
        node.scale = Vec3(1.0, 1.0, 1.0)
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.SCALE, undo_stack)

        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=5.0, y=0.0), axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=10.0, y=0.0)))
        session.on_mouse_up(MouseUpEvent())

        assert math.isclose(node.scale.x, 2.0, abs_tol=1e-9)
        assert node.scale.y == 1.0
        assert node.scale.z == 1.0


# ============================================================================ #
# 3) KeyBindingRegistry
# ============================================================================ #

class TestKeyBindingRegistry:
    def test_default_bindings_g_r_s_switch_mode(self):
        modes_set = []
        undo_stack = UndoRedoStack()
        registry = KeyBindingRegistry.default_bindings(
            set_mode=lambda m: modes_set.append(m), undo_stack=undo_stack,
        )
        assert registry.dispatch("g") is True
        assert registry.dispatch("r") is True
        assert registry.dispatch("s") is True
        assert modes_set == [GizmoMode.TRANSLATE, GizmoMode.ROTATE, GizmoMode.SCALE]

    def test_default_bindings_undo_redo(self):
        node = _make_node()
        undo_stack = UndoRedoStack()
        session = GizmoInputSession(node, GizmoMode.TRANSLATE, undo_stack)
        session.on_mouse_down(MouseDownEvent(ray=_looking_down_ray(x=0.0, y=0.0), axis="x"))
        session.on_mouse_move(MouseMoveEvent(ray=_looking_down_ray(x=5.0, y=0.0)))
        session.on_mouse_up(MouseUpEvent())

        registry = KeyBindingRegistry.default_bindings(set_mode=lambda m: None, undo_stack=undo_stack)
        assert registry.dispatch("ctrl+z") is True
        assert node.position.x == 0.0
        assert registry.dispatch("ctrl+y") is True
        assert node.position.x == 5.0

    def test_unbound_key_returns_false(self):
        undo_stack = UndoRedoStack()
        registry = KeyBindingRegistry.default_bindings(set_mode=lambda m: None, undo_stack=undo_stack)
        assert registry.dispatch("f12") is False
