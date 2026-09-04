"""
Building Editor
===============

Roadmap Phase 8 - "Building Editor": kat ekle, kat sil, çatı değiştir,
cephe değiştir, kapı, pencere.

Phase 3 `building_reconstruction.Building` dataclass'ını mutasyona uğratan
komutlar üretir. Çatı/cephe değişiklikleri, ilgili Phase 3 üreticilerini
(`RoofGenerator`, `FacadeGenerator`) yeniden çağırarak geometriyi güncel
tutar - yani "Çatıyı Mansard yap" gibi bir komut yalnızca bir enum alanını
değiştirmez, `building.roof` mesh'ini de yeniden üretir. Bu, roadmap
Phase 12 "AI Assistant"ın doğal dil komutlarını ("bu binaya bir kat daha
ekle ve çatıyı düz yap") doğrudan bu editöre eşleyebilmesi için tasarlanmıştır.
"""

from __future__ import annotations

from ..building_reconstruction.facade_generator import FacadeGenerator, FacadeMaterial
from ..building_reconstruction.procedural_generator import Building, Floor
from ..building_reconstruction.roof_generator import RoofGenerator, RoofType
from ..core_engine.geometry_engine import Point2D
from .commands import EditorCommand, FunctionCommand


class BuildingEditor:
    """`Building` üzerinde kat/çatı/cephe/kapı/pencere düzenleme
    operasyonları. Her metod bir `EditorCommand` döndürür."""

    # -- Kat ekle / sil ----------------------------------------------------- #
    @staticmethod
    def add_floor(
        building: Building, height_m: float = 3.0, at_index: int | None = None
    ) -> EditorCommand:
        insert_at = len(building.floors) if at_index is None else at_index
        new_floor = Floor(level=insert_at, height_m=height_m)

        def do() -> None:
            building.floors.insert(insert_at, new_floor)
            BuildingEditor._renumber(building)

        def undo() -> None:
            building.floors.remove(new_floor)
            BuildingEditor._renumber(building)

        return FunctionCommand(do, undo, label=f"building:add_floor@{insert_at}")

    @staticmethod
    def remove_floor(building: Building, index: int) -> EditorCommand:
        removed = building.floors[index]

        def do() -> None:
            del building.floors[index]
            BuildingEditor._renumber(building)

        def undo() -> None:
            building.floors.insert(index, removed)
            BuildingEditor._renumber(building)

        return FunctionCommand(do, undo, label=f"building:remove_floor@{index}")

    @staticmethod
    def _renumber(building: Building) -> None:
        for i, floor in enumerate(building.floors):
            floor.level = i

    # -- Çatı değiştir --------------------------------------------------------- #
    @staticmethod
    def change_roof(
        building: Building,
        roof_type: RoofType | str,
        pitch_deg: float = 25.0,
        overhang_m: float = 0.4,
    ) -> EditorCommand:
        before_roof = building.roof

        def do() -> None:
            building.roof = RoofGenerator.generate(
                polygon=building.footprint.polygon,
                base_z=building.total_height_m,
                roof_type=roof_type,
                pitch_deg=pitch_deg,
                overhang_m=overhang_m,
            )

        def undo() -> None:
            building.roof = before_roof

        label_type = roof_type.value if isinstance(roof_type, RoofType) else roof_type
        return FunctionCommand(do, undo, label=f"building:change_roof:{label_type}")

    # -- Cephe değiştir ---------------------------------------------------------- #
    @staticmethod
    def change_facade(
        building: Building,
        material: FacadeMaterial | None = None,
        window_width: float = 1.2,
        window_height: float = 1.4,
        seed: int | None = None,
    ) -> EditorCommand:
        before_facade = building.facade

        def do() -> None:
            floor_height = building.floors[0].height_m if building.floors else 3.0
            building.facade = FacadeGenerator.generate(
                polygon=building.footprint.polygon,
                building_type=building.building_type.value,
                base_z=0.0,
                floor_height=floor_height,
                floor_count=max(1, len(building.floors)),
                window_width=window_width,
                window_height=window_height,
                seed=seed,
                material_override=material,
            )

        def undo() -> None:
            building.facade = before_facade

        return FunctionCommand(do, undo, label="building:change_facade")

    # -- Kapı ekle --------------------------------------------------------------- #
    @staticmethod
    def add_door(building: Building, floor_index: int, position: Point2D) -> EditorCommand:
        floor = building.floors[floor_index]

        def do() -> None:
            floor.doors.append(position)

        def undo() -> None:
            floor.doors.remove(position)

        return FunctionCommand(do, undo, label=f"building:add_door@floor{floor_index}")

    @staticmethod
    def remove_door(building: Building, floor_index: int, position: Point2D) -> EditorCommand:
        floor = building.floors[floor_index]

        def do() -> None:
            floor.doors.remove(position)

        def undo() -> None:
            floor.doors.append(position)

        return FunctionCommand(do, undo, label=f"building:remove_door@floor{floor_index}")

    # -- Pencere ekle -------------------------------------------------------------- #
    @staticmethod
    def add_window(building: Building, floor_index: int, position: Point2D) -> EditorCommand:
        floor = building.floors[floor_index]

        def do() -> None:
            floor.windows.append(position)

        def undo() -> None:
            floor.windows.remove(position)

        return FunctionCommand(do, undo, label=f"building:add_window@floor{floor_index}")

    @staticmethod
    def remove_window(building: Building, floor_index: int, position: Point2D) -> EditorCommand:
        floor = building.floors[floor_index]

        def do() -> None:
            floor.windows.remove(position)

        def undo() -> None:
            floor.windows.append(position)

        return FunctionCommand(do, undo, label=f"building:remove_window@floor{floor_index}")
