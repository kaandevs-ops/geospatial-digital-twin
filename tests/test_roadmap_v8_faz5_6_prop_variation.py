"""
ROADMAP_V8 Faz 5.6 — Prosedürel prop çeşitliliği (rastgelelik)
================================================================

`street_furniture.StreetFurnitureGenerator.generate` artık varsayılan
olarak konum-tabanlı deterministik ±%12.5 ölçek + serbest Z-rotasyonu
uyguluyor. Bu testler:

1. Varyasyonun gerçekten farklı çıktılar ürettiğini (aynı tip, farklı
   konum -> farklı bounding box / hacim),
2. Aynı konum + tip için deterministik/tekrarlanabilir olduğunu,
3. `apply_variation=False` ile eski (varyasyonsuz) davranışın hâlâ
   erişilebilir olduğunu,
4. Varyasyonun geometriyi bozmadığını (üçgen/vertex sayısı korunur,
   hacim sıfırlanmaz) doğrular.
"""
from __future__ import annotations

import unittest

from harita.core_engine.geometry_engine import Point2D
from harita.street_furniture import (
    StreetFurnitureGenerator,
    StreetFurnitureItem,
    StreetFurnitureType,
)


class TestPropVariation(unittest.TestCase):
    def test_variation_differs_across_positions(self) -> None:
        positions = [Point2D(x * 3.0, 0.0) for x in range(6)]
        bboxes = []
        for pos in positions:
            item = StreetFurnitureItem(StreetFurnitureType.BENCH, pos)
            mesh = StreetFurnitureGenerator.generate(item)
            bboxes.append(mesh.bounding_box())
        # En az bir çift birbirinden farklı olmalı (kopyala-yapıştır değil).
        widths = {round(bb[1][0] - bb[0][0], 6) for bb in bboxes}
        self.assertGreater(len(widths), 1)

    def test_variation_is_deterministic(self) -> None:
        pos = Point2D(12.5, -7.25)
        item = StreetFurnitureItem(StreetFurnitureType.STREET_LAMP, pos)
        mesh_a = StreetFurnitureGenerator.generate(item)
        mesh_b = StreetFurnitureGenerator.generate(item)
        self.assertEqual(mesh_a.bounding_box(), mesh_b.bounding_box())

    def test_apply_variation_false_preserves_legacy_output(self) -> None:
        pos = Point2D(4.0, 4.0)
        item = StreetFurnitureItem(StreetFurnitureType.TRASH_BIN, pos)
        legacy = StreetFurnitureGenerator.trash_bin(pos, 0.0)
        via_generate = StreetFurnitureGenerator.generate(item, apply_variation=False)
        self.assertEqual(legacy.bounding_box(), via_generate.bounding_box())

    def test_variation_preserves_topology(self) -> None:
        pos = Point2D(1.0, 2.0)
        item = StreetFurnitureItem(StreetFurnitureType.BUS_STOP, pos)
        base = StreetFurnitureGenerator.generate(item, apply_variation=False)
        varied = StreetFurnitureGenerator.generate(item, apply_variation=True)
        self.assertEqual(base.vertex_count(), varied.vertex_count())
        self.assertEqual(base.triangle_count(), varied.triangle_count())
        # Ölçek asla sıfır olmamalı (bounding box dejenere değil).
        (minx, miny, minz), (maxx, maxy, maxz) = varied.bounding_box()
        self.assertGreater(maxx - minx, 0.0)
        self.assertGreater(maxz - minz, 0.0)

    def test_all_furniture_types_survive_variation(self) -> None:
        pos = Point2D(0.0, 0.0)
        for ftype in StreetFurnitureType:
            item = StreetFurnitureItem(ftype, pos)
            mesh = StreetFurnitureGenerator.generate(item)
            self.assertGreater(mesh.vertex_count(), 0, msg=f"{ftype} boş mesh üretti")


if __name__ == "__main__":
    unittest.main()
