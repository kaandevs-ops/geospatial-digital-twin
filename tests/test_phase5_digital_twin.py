"""Phase 5 (Digital Twin) için birim testleri."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from harita.core_engine.geometry_engine import Point2D, Polygon
from harita.mesh_engine import MeshBuilder
from harita.material_engine import ProceduralMaterials

from harita.digital_twin import (
    DigitalTwin,
    TwinEvent,
    SensorBinding,
    Annotation,
    Measurement,
    TwinDiff,
    diff_twins,
    DigitalTwinRegistry,
)


def _sample_mesh():
    footprint = Polygon(points=[
        Point2D(0, 0), Point2D(10, 0), Point2D(10, 8), Point2D(0, 8),
    ])
    return MeshBuilder.extrude_polygon(footprint, base_z=0.0, height=3.0)


def _sample_material():
    return ProceduralMaterials.create("cam")


# ============================================================================ #
# DigitalTwin - temel davranış
# ============================================================================ #

class TestDigitalTwinBasics:
    def test_create_empty_twin(self):
        twin = DigitalTwin(id="twin-1")
        assert twin.id == "twin-1"
        assert twin.geometry is None
        assert twin.version == 1
        assert twin.history == []

    def test_set_geometry_logs_event(self):
        twin = DigitalTwin(id="twin-1")
        mesh = _sample_mesh()
        twin.set_geometry(mesh)
        assert twin.geometry is mesh
        assert len(twin.history) == 1
        assert twin.history[0].event_type == "geometry_updated"
        assert twin.history[0].payload["vertex_count"] == len(mesh.vertices)

    def test_add_material(self):
        twin = DigitalTwin(id="twin-1")
        mat = _sample_material()
        twin.add_material(mat)
        assert len(twin.materials) == 1
        assert twin.materials[0] is mat
        assert twin.events_of_type("material_added")

    def test_set_metadata(self):
        twin = DigitalTwin(id="twin-1")
        twin.set_metadata("building_type", "office")
        assert twin.metadata["building_type"] == "office"

    def test_update_simulation_state(self):
        twin = DigitalTwin(id="twin-1")
        twin.update_simulation_state("evacuation_time_s", 92.5)
        assert twin.simulation_state["evacuation_time_s"] == 92.5

    def test_update_ai_data_with_confidence(self):
        twin = DigitalTwin(id="twin-1")
        twin.update_ai_data("floor_count", 5, confidence=0.83)
        assert twin.ai_data["floor_count"]["value"] == 5
        assert twin.ai_data["floor_count"]["confidence"] == 0.83

    def test_layer_visibility(self):
        twin = DigitalTwin(id="twin-1")
        twin.set_layer_visibility("furniture", False)
        assert twin.layers["furniture"] is False
        twin.set_layer_visibility("furniture", True)
        assert twin.layers["furniture"] is True


# ============================================================================ #
# Sensörler
# ============================================================================ #

class TestSensorBinding:
    def test_bind_and_update_sensor(self):
        twin = DigitalTwin(id="twin-1")
        sensor = SensorBinding(sensor_id="temp-01", sensor_type="temperature",
                                target_ref="floor:2", unit="C")
        twin.bind_sensor(sensor)
        assert len(twin.sensors) == 1

        ok = twin.update_sensor("temp-01", 23.4)
        assert ok is True
        assert twin.sensors[0].last_value == 23.4
        assert twin.sensors[0].last_updated is not None

    def test_update_unknown_sensor_returns_false(self):
        twin = DigitalTwin(id="twin-1")
        assert twin.update_sensor("nope", 1.0) is False

    def test_sensors_for_target(self):
        twin = DigitalTwin(id="twin-1")
        twin.bind_sensor(SensorBinding("s1", "temp", "floor:1"))
        twin.bind_sensor(SensorBinding("s2", "humidity", "floor:1"))
        twin.bind_sensor(SensorBinding("s3", "temp", "floor:2"))
        assert len(twin.sensors_for_target("floor:1")) == 2
        assert len(twin.sensors_for_target("floor:2")) == 1


# ============================================================================ #
# Anotasyonlar
# ============================================================================ #

class TestAnnotations:
    def test_add_annotation(self):
        twin = DigitalTwin(id="twin-1")
        ann = twin.add_annotation("Çatlak tespit edildi", (1.0, 2.0, 3.0),
                                    category="issue")
        assert ann in twin.annotations
        assert ann.resolved is False
        assert ann.category == "issue"

    def test_resolve_annotation(self):
        twin = DigitalTwin(id="twin-1")
        ann = twin.add_annotation("Not", (0, 0, 0))
        assert len(twin.open_annotations()) == 1
        resolved = twin.resolve_annotation(ann.id)
        assert resolved is True
        assert len(twin.open_annotations()) == 0

    def test_resolve_unknown_annotation(self):
        twin = DigitalTwin(id="twin-1")
        assert twin.resolve_annotation("does-not-exist") is False


# ============================================================================ #
# Ölçümler
# ============================================================================ #

class TestMeasurements:
    def test_add_measurement(self):
        twin = DigitalTwin(id="twin-1")
        m = twin.add_measurement("distance", 12.5, "m",
                                  points=[(0, 0, 0), (12.5, 0, 0)])
        assert m in twin.measurements
        assert m.kind == "distance"

    def test_measurements_of_kind(self):
        twin = DigitalTwin(id="twin-1")
        twin.add_measurement("distance", 5.0, "m")
        twin.add_measurement("area", 20.0, "m2")
        twin.add_measurement("distance", 7.0, "m")
        assert len(twin.measurements_of_kind("distance")) == 2
        assert len(twin.measurements_of_kind("area")) == 1


# ============================================================================ #
# History sorguları
# ============================================================================ #

class TestHistoryQueries:
    def test_events_since(self):
        twin = DigitalTwin(id="twin-1")
        twin.log_event("event_a")
        cutoff = time.time()
        time.sleep(0.001)
        twin.log_event("event_b")
        recent = twin.events_since(cutoff)
        assert any(e.event_type == "event_b" for e in recent)

    def test_events_of_type(self):
        twin = DigitalTwin(id="twin-1")
        twin.set_metadata("a", 1)
        twin.set_metadata("b", 2)
        events = twin.events_of_type("metadata_updated")
        assert len(events) == 2


# ============================================================================ #
# Serialization
# ============================================================================ #

class TestSerialization:
    def test_roundtrip_empty_twin(self):
        twin = DigitalTwin(id="twin-1")
        twin.set_metadata("name", "Test Building")
        data = twin.to_dict()
        restored = DigitalTwin.from_dict(data)
        assert restored.id == twin.id
        assert restored.metadata == twin.metadata

    def test_roundtrip_with_geometry_and_material(self):
        twin = DigitalTwin(id="twin-1")
        mesh = _sample_mesh()
        twin.set_geometry(mesh)
        twin.add_material(_sample_material())
        twin.bind_sensor(SensorBinding("s1", "temp", "roof"))
        twin.add_annotation("test note", (1, 1, 1))
        twin.add_measurement("area", 80.0, "m2")

        data = twin.to_dict()
        restored = DigitalTwin.from_dict(data)

        assert len(restored.geometry.vertices) == len(mesh.vertices)
        assert len(restored.geometry.triangles) == len(mesh.triangles)
        assert len(restored.materials) == 1
        assert len(restored.sensors) == 1
        assert len(restored.annotations) == 1
        assert len(restored.measurements) == 1
        assert len(restored.history) == len(twin.history)

    def test_json_serializable(self):
        import json
        twin = DigitalTwin(id="twin-1")
        twin.set_geometry(_sample_mesh())
        raw = json.dumps(twin.to_dict())
        parsed = json.loads(raw)
        restored = DigitalTwin.from_dict(parsed)
        assert restored.id == "twin-1"


# ============================================================================ #
# TwinDiff
# ============================================================================ #

class TestTwinDiff:
    def test_diff_detects_metadata_change(self):
        old = DigitalTwin(id="t1")
        new = DigitalTwin(id="t1", version=2)
        new.metadata["x"] = 1
        d = diff_twins(old, new)
        assert "metadata" in d.changed_fields

    def test_diff_detects_geometry_change(self):
        old = DigitalTwin(id="t1")
        new = DigitalTwin(id="t1", version=2)
        new.geometry = _sample_mesh()
        d = diff_twins(old, new)
        assert "geometry" in d.changed_fields

    def test_diff_raises_on_mismatched_ids(self):
        a = DigitalTwin(id="a")
        b = DigitalTwin(id="b")
        try:
            diff_twins(a, b)
            assert False, "ValueError bekleniyordu"
        except ValueError:
            pass

    def test_diff_no_change(self):
        old = DigitalTwin(id="t1")
        new = DigitalTwin(id="t1")
        d = diff_twins(old, new)
        assert d.changed_fields == []


# ============================================================================ #
# DigitalTwinRegistry
# ============================================================================ #

class TestDigitalTwinRegistry:
    def test_create_and_get(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        fetched = reg.get("b-1")
        assert fetched is not None
        assert fetched.id == twin.id
        # get() bağımsız bir kopya döndürür (checkout) - registry'nin
        # dahili durumu, çağıranın elindeki nesne üzerinden save() çağrılmadan
        # değişmemelidir.
        assert fetched is not twin
        assert reg.exists("b-1")

    def test_create_generates_id_if_missing(self):
        reg = DigitalTwinRegistry()
        twin = reg.create()
        assert twin.id
        assert reg.exists(twin.id)

    def test_create_duplicate_raises(self):
        reg = DigitalTwinRegistry()
        reg.create("dup")
        try:
            reg.create("dup")
            assert False, "ValueError bekleniyordu"
        except ValueError:
            pass

    def test_save_increments_version_and_snapshots(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        assert twin.version == 1

        twin.set_metadata("floors", 3)
        twin = reg.save(twin)
        assert twin.version == 2
        assert len(reg.history_versions("b-1")) == 1

        twin.set_metadata("floors", 4)
        twin = reg.save(twin)
        assert twin.version == 3
        assert len(reg.history_versions("b-1")) == 2

    def test_get_version(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        twin.set_metadata("floors", 3)
        reg.save(twin)  # version 2

        v1 = reg.get_version("b-1", 1)
        v2 = reg.get_version("b-1", 2)
        assert v1 is not None and v1.metadata.get("floors") is None
        assert v2 is not None and v2.metadata.get("floors") == 3

    def test_rollback(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        twin.set_metadata("floors", 3)
        reg.save(twin)  # v2, floors=3

        current = reg.get("b-1")
        current.set_metadata("floors", 99)
        reg.save(current)  # v3, floors=99

        restored = reg.rollback("b-1", 2)
        assert restored is not None
        assert restored.metadata["floors"] == 3
        assert restored.version == 4  # rollback yeni versiyon olarak eklenir

    def test_rollback_unknown_version_returns_none(self):
        reg = DigitalTwinRegistry()
        reg.create("b-1")
        assert reg.rollback("b-1", 99) is None

    def test_delete(self):
        reg = DigitalTwinRegistry()
        reg.create("b-1")
        assert reg.delete("b-1") is True
        assert not reg.exists("b-1")
        assert reg.delete("b-1") is False

    def test_query_and_find_by_metadata(self):
        reg = DigitalTwinRegistry()
        t1 = reg.create("b-1")
        t1.set_metadata("type", "office")
        reg.save(t1)
        t2 = reg.create("b-2")
        t2.set_metadata("type", "residential")
        reg.save(t2)

        offices = reg.find_by_metadata("type", "office")
        assert len(offices) == 1
        assert offices[0].id == "b-1"

        all_twins = reg.query(lambda t: True)
        assert len(all_twins) == 2

    def test_diff_via_registry(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        twin.set_metadata("floors", 3)
        reg.save(twin)  # v2

        d = reg.diff("b-1", 1, 2)
        assert d is not None
        assert "metadata" in d.changed_fields

    def test_listener_notified_on_create_save_delete(self):
        reg = DigitalTwinRegistry()
        events = []
        reg.on_change(lambda action, twin: events.append((action, twin.id)))

        twin = reg.create("b-1")
        reg.save(twin)
        reg.delete("b-1")

        actions = [e[0] for e in events]
        assert actions == ["created", "saved", "deleted"]

    def test_export_import_json_roundtrip(self):
        reg = DigitalTwinRegistry()
        twin = reg.create("b-1")
        twin.set_geometry(_sample_mesh())
        twin.set_metadata("name", "Test")
        reg.save(twin)

        raw = reg.export_json()

        reg2 = DigitalTwinRegistry()
        reg2.import_json(raw)

        restored = reg2.get("b-1")
        assert restored is not None
        assert restored.metadata["name"] == "Test"
        assert len(restored.geometry.vertices) > 0

    def test_list_ids_and_all(self):
        reg = DigitalTwinRegistry()
        reg.create("b-1")
        reg.create("b-2")
        assert set(reg.list_ids()) == {"b-1", "b-2"}
        assert len(reg.all()) == 2
