"""
Digital Twin
============

Roadmap Phase 5 - "Digital Twin".

Her rekonstrükte edilmiş bina/nesne, kendi geometrisi, malzemeleri, geçmişi,
simülasyon durumu, AI verisi, sensör bağlantıları, anotasyonları, ölçümleri ve
katmanlarıyla birlikte tek bir `DigitalTwin` nesnesine dönüşür. Bu faz,
önceki fazların (Mesh Engine, Material Engine, Building Reconstruction, AI
Reconstruction) ürettiği verileri tek bir kalıcı/kimlikli kayda birleştirir ve
sonraki fazların (Analysis Engine, Mobility, Editor, Export, AI Assistant)
üzerinde çalışacağı ortak "nesne" soyutlamasını sağlar.

Kapsam:
    DigitalTwin (veri modeli), TwinEvent (geçmiş/history kaydı),
    SensorBinding, Annotation, Measurement, DigitalTwinRegistry
    (CRUD + versiyonlama + sorgulama), TwinDiff (versiyon karşılaştırma),
    TwinSerializer (JSON import/export).

Bağımlılık: yalnızca stdlib.
"""

from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional

from ..mesh_engine import Mesh3D, Vertex3D
from ..material_engine import PBRMaterial


# ======================================================================== #
# Mesh3D / PBRMaterial için serialization yardımcıları
# ======================================================================== #
# Not: `Mesh3D` ve `PBRMaterial` (Phase 2) kendi başlarına to_dict/from_dict
# taşımıyor; roadmap ilkesi gereği mevcut modüller değiştirilmeden, bu
# serileştirme mantığı burada (Phase 5 tarafında) sağlanır.

def _mesh_to_dict(mesh: Mesh3D) -> dict:
    return {
        "name": mesh.name,
        "vertices": [
            {"x": v.x, "y": v.y, "z": v.z, "normal": v.normal,
             "tangent": v.tangent, "uv": v.uv}
            for v in mesh.vertices
        ],
        "triangles": [list(t) for t in mesh.triangles],
        "uvs": [list(uv) for uv in mesh.uvs],
    }


def _mesh_from_dict(data: dict) -> Mesh3D:
    vertices = [
        Vertex3D(
            x=v["x"], y=v["y"], z=v["z"],
            normal=tuple(v["normal"]) if v.get("normal") else None,
            tangent=tuple(v["tangent"]) if v.get("tangent") else None,
            uv=tuple(v["uv"]) if v.get("uv") else None,
        )
        for v in data.get("vertices", [])
    ]
    triangles = [tuple(t) for t in data.get("triangles", [])]
    uvs = [tuple(uv) for uv in data.get("uvs", [])]
    return Mesh3D(vertices=vertices, triangles=triangles, uvs=uvs,
                  name=data.get("name", "mesh"))


def _material_to_dict(mat: PBRMaterial) -> dict:
    return asdict(mat)


def _material_from_dict(data: dict) -> PBRMaterial:
    return PBRMaterial(**data)


# ======================================================================== #
# Yardımcı veri tipleri
# ======================================================================== #

@dataclass(slots=True)
class TwinEvent:
    """Bir digital twin üzerinde gerçekleşen tekil olay (history kaydı)."""

    timestamp: float
    event_type: str
    payload: dict = field(default_factory=dict)
    actor: str = "system"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "payload": self.payload,
            "actor": self.actor,
        }

    @staticmethod
    def from_dict(data: dict) -> "TwinEvent":
        return TwinEvent(
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            payload=data.get("payload", {}),
            actor=data.get("actor", "system"),
        )


@dataclass(slots=True)
class SensorBinding:
    """Bir digital twin'e bağlı fiziksel/sanal sensör referansı.

    Gerçek zamanlı değer bu sınıfta tutulmaz (o, dışarıdaki bir
    telemetry/event-bus katmanının işi) - burada yalnızca sensörün twin
    üzerindeki bağlanma noktası (hangi eleman/kat/oda) ve son bilinen
    değeri (cache) tutulur.
    """

    sensor_id: str
    sensor_type: str
    target_ref: str  # örn. "floor:2" / "room:server_room_1" / "roof"
    unit: str = ""
    last_value: Optional[float] = None
    last_updated: Optional[float] = None
    metadata: dict = field(default_factory=dict)

    def update(self, value: float, timestamp: Optional[float] = None) -> None:
        self.last_value = value
        self.last_updated = timestamp if timestamp is not None else time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "SensorBinding":
        return SensorBinding(**data)


@dataclass(slots=True)
class Annotation:
    """Kullanıcı veya AI tarafından bina üzerine eklenen not/işaret."""

    id: str
    text: str
    position: tuple  # (x, y, z)
    author: str = "user"
    created_at: float = field(default_factory=time.time)
    category: str = "general"  # general | issue | ai_finding | measurement_note
    resolved: bool = False

    def to_dict(self) -> dict:
        d = asdict(self)
        d["position"] = list(self.position)
        return d

    @staticmethod
    def from_dict(data: dict) -> "Annotation":
        d = dict(data)
        d["position"] = tuple(d.get("position", (0.0, 0.0, 0.0)))
        return Annotation(**d)


@dataclass(slots=True)
class Measurement:
    """Phase 6 (Analysis Engine / Measurement) sonuçlarının twin üstünde
    kalıcı olarak saklanan kopyası (distance/area/volume/height/angle/slope).
    """

    id: str
    kind: str  # distance | area | volume | height | angle | slope
    value: float
    unit: str
    points: list = field(default_factory=list)  # ilgili nokta(lar), (x,y,z) tuple listesi
    created_at: float = field(default_factory=time.time)
    label: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "value": self.value,
            "unit": self.unit,
            "points": [list(p) for p in self.points],
            "created_at": self.created_at,
            "label": self.label,
        }

    @staticmethod
    def from_dict(data: dict) -> "Measurement":
        d = dict(data)
        d["points"] = [tuple(p) for p in d.get("points", [])]
        return Measurement(**d)


# ======================================================================== #
# DigitalTwin
# ======================================================================== #

@dataclass(slots=True)
class DigitalTwin:
    """Roadmap Phase 5 - tek bir bina/nesne için tüm platform verisinin
    birleştiği kanonik nesne.
    """

    id: str
    geometry: Optional[Mesh3D] = None
    metadata: dict = field(default_factory=dict)
    materials: list = field(default_factory=list)          # list[PBRMaterial]
    history: list = field(default_factory=list)             # list[TwinEvent]
    simulation_state: dict = field(default_factory=dict)
    ai_data: dict = field(default_factory=dict)
    sensors: list = field(default_factory=list)             # list[SensorBinding]
    annotations: list = field(default_factory=list)         # list[Annotation]
    measurements: list = field(default_factory=list)        # list[Measurement]
    layers: dict = field(default_factory=dict)              # katman adı -> görünür mü
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # -- mutasyon API'leri (her biri history'e otomatik event yazar) ---- #

    def log_event(self, event_type: str, payload: Optional[dict] = None,
                   actor: str = "system") -> TwinEvent:
        evt = TwinEvent(timestamp=time.time(), event_type=event_type,
                         payload=payload or {}, actor=actor)
        self.history.append(evt)
        self.updated_at = evt.timestamp
        return evt

    def set_geometry(self, mesh: Mesh3D, actor: str = "system") -> None:
        self.geometry = mesh
        self.log_event("geometry_updated",
                        {"vertex_count": len(mesh.vertices),
                         "triangle_count": len(mesh.triangles)},
                        actor)

    def add_material(self, material: PBRMaterial, actor: str = "system") -> None:
        self.materials.append(material)
        self.log_event("material_added", {"material_name": getattr(material, "name", "")}, actor)

    def bind_sensor(self, sensor: SensorBinding, actor: str = "system") -> None:
        self.sensors.append(sensor)
        self.log_event("sensor_bound",
                        {"sensor_id": sensor.sensor_id, "target_ref": sensor.target_ref}, actor)

    def update_sensor(self, sensor_id: str, value: float,
                       timestamp: Optional[float] = None) -> bool:
        for s in self.sensors:
            if s.sensor_id == sensor_id:
                s.update(value, timestamp)
                self.log_event("sensor_value_updated",
                                {"sensor_id": sensor_id, "value": value}, "sensor")
                return True
        return False

    def add_annotation(self, text: str, position: tuple, author: str = "user",
                        category: str = "general") -> Annotation:
        ann = Annotation(id=str(uuid.uuid4()), text=text, position=position,
                          author=author, category=category)
        self.annotations.append(ann)
        self.log_event("annotation_added", {"annotation_id": ann.id, "text": text}, author)
        return ann

    def resolve_annotation(self, annotation_id: str) -> bool:
        for a in self.annotations:
            if a.id == annotation_id:
                a.resolved = True
                self.log_event("annotation_resolved", {"annotation_id": annotation_id})
                return True
        return False

    def add_measurement(self, kind: str, value: float, unit: str,
                         points: Optional[list] = None, label: str = "") -> Measurement:
        m = Measurement(id=str(uuid.uuid4()), kind=kind, value=value, unit=unit,
                         points=points or [], label=label)
        self.measurements.append(m)
        self.log_event("measurement_added",
                        {"measurement_id": m.id, "kind": kind, "value": value}, "system")
        return m

    def set_layer_visibility(self, layer_name: str, visible: bool) -> None:
        self.layers[layer_name] = visible
        self.log_event("layer_visibility_changed", {"layer": layer_name, "visible": visible})

    def update_simulation_state(self, key: str, value: Any) -> None:
        self.simulation_state[key] = value
        self.log_event("simulation_state_updated", {"key": key})

    def update_ai_data(self, key: str, value: Any, confidence: Optional[float] = None) -> None:
        entry: dict = {"value": value}
        if confidence is not None:
            entry["confidence"] = confidence
        self.ai_data[key] = entry
        self.log_event("ai_data_updated", {"key": key})

    def set_metadata(self, key: str, value: Any) -> None:
        self.metadata[key] = value
        self.log_event("metadata_updated", {"key": key})

    # -- sorgular ---------------------------------------------------------- #

    def sensors_for_target(self, target_ref: str) -> list:
        return [s for s in self.sensors if s.target_ref == target_ref]

    def open_annotations(self) -> list:
        return [a for a in self.annotations if not a.resolved]

    def measurements_of_kind(self, kind: str) -> list:
        return [m for m in self.measurements if m.kind == kind]

    def events_since(self, timestamp: float) -> list:
        return [e for e in self.history if e.timestamp >= timestamp]

    def events_of_type(self, event_type: str) -> list:
        return [e for e in self.history if e.event_type == event_type]

    # -- serialization ------------------------------------------------------ #

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "geometry": _mesh_to_dict(self.geometry) if self.geometry is not None else None,
            "metadata": self.metadata,
            "materials": [_material_to_dict(m) for m in self.materials],
            "history": [e.to_dict() for e in self.history],
            "simulation_state": self.simulation_state,
            "ai_data": self.ai_data,
            "sensors": [s.to_dict() for s in self.sensors],
            "annotations": [a.to_dict() for a in self.annotations],
            "measurements": [m.to_dict() for m in self.measurements],
            "layers": self.layers,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict) -> "DigitalTwin":
        geometry = _mesh_from_dict(data["geometry"]) if data.get("geometry") else None
        materials = [_material_from_dict(m) for m in data.get("materials", [])]
        return DigitalTwin(
            id=data["id"],
            geometry=geometry,
            metadata=data.get("metadata", {}),
            materials=materials,
            history=[TwinEvent.from_dict(e) for e in data.get("history", [])],
            simulation_state=data.get("simulation_state", {}),
            ai_data=data.get("ai_data", {}),
            sensors=[SensorBinding.from_dict(s) for s in data.get("sensors", [])],
            annotations=[Annotation.from_dict(a) for a in data.get("annotations", [])],
            measurements=[Measurement.from_dict(m) for m in data.get("measurements", [])],
            layers=data.get("layers", {}),
            version=data.get("version", 1),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )


# ======================================================================== #
# TwinDiff - iki versiyon arasındaki farkı raporlar
# ======================================================================== #

@dataclass(slots=True)
class TwinDiff:
    twin_id: str
    from_version: int
    to_version: int
    changed_fields: list = field(default_factory=list)
    event_count_delta: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


def diff_twins(old: DigitalTwin, new: DigitalTwin) -> TwinDiff:
    """İki `DigitalTwin` anlık görüntüsünü (aynı id) yüzeysel olarak
    karşılaştırır ve hangi alanların değiştiğini raporlar."""
    if old.id != new.id:
        raise ValueError("diff_twins: farklı id'ye sahip twin'ler karşılaştırılamaz")

    changed = []
    simple_fields = ("metadata", "simulation_state", "ai_data", "layers")
    for f in simple_fields:
        if getattr(old, f) != getattr(new, f):
            changed.append(f)

    if (old.geometry is None) != (new.geometry is None):
        changed.append("geometry")
    elif old.geometry is not None and new.geometry is not None:
        if len(old.geometry.vertices) != len(new.geometry.vertices) or \
           len(old.geometry.triangles) != len(new.geometry.triangles):
            changed.append("geometry")

    if len(old.materials) != len(new.materials):
        changed.append("materials")
    if len(old.sensors) != len(new.sensors):
        changed.append("sensors")
    if len(old.annotations) != len(new.annotations):
        changed.append("annotations")
    if len(old.measurements) != len(new.measurements):
        changed.append("measurements")

    return TwinDiff(
        twin_id=old.id,
        from_version=old.version,
        to_version=new.version,
        changed_fields=changed,
        event_count_delta=len(new.history) - len(old.history),
    )


# ======================================================================== #
# DigitalTwinRegistry - CRUD + versiyonlama
# ======================================================================== #

class DigitalTwinRegistry:
    """Tüm `DigitalTwin` nesnelerinin id bazlı deposu.

    Her `save()` çağrısı, mevcut kaydın önceki durumunu snapshot olarak
    saklar (Phase 10 Data Engine'in `History`/`Versioning` bileşeniyle
    paylaşılan basit mekanizma - burada bağımsız/self-contained olarak
    yeniden uygulanmıştır ki Phase 5 tek başına da çalışabilsin).
    """

    def __init__(self) -> None:
        self._twins: dict = {}
        self._snapshots: dict = {}  # id -> list[DigitalTwin] (geçmiş versiyonlar)
        self._listeners: list = []  # Callable[[str, DigitalTwin], None]

    # -- CRUD --------------------------------------------------------------- #

    def create(self, twin_id: Optional[str] = None, **kwargs) -> DigitalTwin:
        tid = twin_id or str(uuid.uuid4())
        if tid in self._twins:
            raise ValueError(f"DigitalTwin zaten mevcut: {tid}")
        twin = DigitalTwin(id=tid, **kwargs)
        twin.log_event("twin_created")
        self._twins[tid] = copy.deepcopy(twin)
        self._snapshots[tid] = []
        self._notify("created", twin)
        return twin

    def get(self, twin_id: str) -> Optional[DigitalTwin]:
        """Kayıtlı twin'in bağımsız bir kopyasını döndürür (checkout).

        Registry'nin dahili durumunun, çağıranın elindeki nesneyi
        `save()` çağırmadan mutasyona uğratması engellenir - aksi halde
        versiyon geçmişi/snapshot'lar anlamsız hale gelirdi.
        """
        stored = self._twins.get(twin_id)
        return copy.deepcopy(stored) if stored is not None else None

    def exists(self, twin_id: str) -> bool:
        return twin_id in self._twins

    def save(self, twin: DigitalTwin) -> DigitalTwin:
        """Twin'in (mutasyona uğramış) bir kopyasını commit eder; önceki
        durumun bir snapshot'ını saklar ve versiyon numarasını artırır.
        Döndürülen nesne de bağımsız bir kopyadır (checkout)."""
        existing = self._twins.get(twin.id)
        incoming = copy.deepcopy(twin)
        if existing is not None:
            self._snapshots.setdefault(twin.id, []).append(existing)
            incoming.version = existing.version + 1
        else:
            self._snapshots.setdefault(twin.id, [])
        incoming.updated_at = time.time()
        self._twins[twin.id] = incoming
        self._notify("saved", incoming)
        return copy.deepcopy(incoming)

    def delete(self, twin_id: str) -> bool:
        if twin_id not in self._twins:
            return False
        twin = self._twins.pop(twin_id)
        self._snapshots.pop(twin_id, None)
        self._notify("deleted", twin)
        return True

    def list_ids(self) -> list:
        return list(self._twins.keys())

    def all(self) -> list:
        return [copy.deepcopy(t) for t in self._twins.values()]

    def query(self, predicate: Callable[[DigitalTwin], bool]) -> list:
        return [copy.deepcopy(t) for t in self._twins.values() if predicate(t)]

    def find_by_metadata(self, key: str, value: Any) -> list:
        return self.query(lambda t: t.metadata.get(key) == value)

    # -- versiyonlama --------------------------------------------------------- #

    def history_versions(self, twin_id: str) -> list:
        """Bir twin'in tüm geçmiş (kaydedilmiş) versiyonlarını, en yeni
        hariç, kronolojik sırayla döndürür."""
        return [copy.deepcopy(t) for t in self._snapshots.get(twin_id, [])]

    def get_version(self, twin_id: str, version: int) -> Optional[DigitalTwin]:
        current = self._twins.get(twin_id)
        if current is not None and current.version == version:
            return copy.deepcopy(current)
        for snap in self._snapshots.get(twin_id, []):
            if snap.version == version:
                return copy.deepcopy(snap)
        return None

    def rollback(self, twin_id: str, version: int) -> Optional[DigitalTwin]:
        """Belirtilen versiyona geri döner (yeni bir versiyon olarak kaydeder,
        geçmişi silmez - append-only)."""
        target = self.get_version(twin_id, version)
        if target is None:
            return None
        restored = copy.deepcopy(target)
        restored.log_event("rolled_back", {"to_version": version})
        return self.save(restored)

    def diff(self, twin_id: str, version_a: int, version_b: int) -> Optional[TwinDiff]:
        a = self.get_version(twin_id, version_a)
        b = self.get_version(twin_id, version_b)
        if a is None or b is None:
            return None
        return diff_twins(a, b)

    # -- olay dinleyicileri (Phase 14 Extensibility ile paylaşılacak basit hook) - #

    def on_change(self, listener: Callable[[str, DigitalTwin], None]) -> None:
        self._listeners.append(listener)

    def _notify(self, action: str, twin: DigitalTwin) -> None:
        for listener in self._listeners:
            listener(action, twin)

    # -- toplu serialization --------------------------------------------------- #

    def export_json(self) -> str:
        return json.dumps(
            {tid: t.to_dict() for tid, t in self._twins.items()},
            ensure_ascii=False, indent=2,
        )

    def import_json(self, data: str) -> None:
        parsed = json.loads(data)
        for tid, twin_data in parsed.items():
            self._twins[tid] = DigitalTwin.from_dict(twin_data)
            self._snapshots.setdefault(tid, [])


__all__ = [
    "DigitalTwin",
    "TwinEvent",
    "SensorBinding",
    "Annotation",
    "Measurement",
    "TwinDiff",
    "diff_twins",
    "DigitalTwinRegistry",
    "TwinHierarchy",
    "SensorSeriesConfig",
    "generate_sensor_timeseries",
    "apply_timeseries_to_sensor",
]


def __getattr__(name: str):
    # ROADMAP_V3 Faz D11: TwinHierarchy / sensör zaman-serisi araçları
    # `digital_twin.hierarchy` alt modülünde tanımlıdır; bu modül `digital_twin`
    # paketini (DigitalTwin/DigitalTwinRegistry) içe aktardığı için döngüsel
    # import'tan kaçınmak amacıyla lazy olarak burada re-export edilir.
    hierarchy_names = {
        "TwinHierarchy",
        "SensorSeriesConfig",
        "generate_sensor_timeseries",
        "apply_timeseries_to_sensor",
    }
    if name in hierarchy_names:
        from . import hierarchy as _hierarchy
        return getattr(_hierarchy, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
