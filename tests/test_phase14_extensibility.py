"""Phase 14 (Extensibility) için birim testleri."""

import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from harita.mesh_engine import Mesh3D, Vertex3D
from harita.digital_twin import DigitalTwin

from harita.extensibility import (
    Event,
    EventSystem,
    MacroRecorder,
    AutomationRule,
    AutomationEngine,
    ModuleManager,
    ModuleNotEnabledError,
    ThemeSystem,
    Plugin,
    PluginMeta,
    FunctionPlugin,
    PluginDependencyError,
    PluginManager,
    RestRouter,
    RestNotFoundError,
    build_default_router,
    ScriptAPI,
    ScriptEngineUnavailable,
    WebSocketRouter,
    WSConnection,
    WSMessage,
    WSConnectionClosedError,
    CLI,
    ProjectFile,
    ProjectFileError,
    UnknownSchemaVersionError,
    migrate_project_file,
    SCHEMA_VERSION,
)


# ============================================================ #
# EventSystem
# ============================================================ #

def test_event_system_basic_pubsub():
    bus = EventSystem()
    received = []
    bus.subscribe("building.created", lambda e: received.append(e))
    bus.emit("building.created", payload={"id": 1})
    assert len(received) == 1
    assert received[0].payload == {"id": 1}


def test_event_system_wildcard():
    bus = EventSystem()
    received = []
    bus.subscribe("*", lambda e: received.append(e.name))
    bus.emit("a.b")
    bus.emit("c.d")
    assert received == ["a.b", "c.d"]


def test_event_system_glob_pattern():
    bus = EventSystem()
    received = []
    bus.subscribe("building.*", lambda e: received.append(e.name))
    bus.emit("building.created")
    bus.emit("terrain.created")
    assert received == ["building.created"]


def test_event_system_unsubscribe():
    bus = EventSystem()
    received = []
    unsub = bus.subscribe("x", lambda e: received.append(e))
    unsub()
    bus.emit("x")
    assert received == []


def test_event_system_history():
    bus = EventSystem()
    bus.emit("a")
    bus.emit("b")
    assert [e.name for e in bus.history()] == ["a", "b"]


def test_event_system_bridge():
    bus = EventSystem()
    bridged = []
    bus.bridge_to(lambda e: bridged.append(e.name))
    bus.emit("z")
    assert bridged == ["z"]


# ============================================================ #
# MacroSystem
# ============================================================ #

def test_macro_recorder_and_playback():
    calls = []

    recorder = MacroRecorder()
    recorder.start(name="test-macro")
    recorder.capture("step-a", lambda: calls.append("a"))
    recorder.capture("step-b", lambda: calls.append("b"))
    macro = recorder.stop()

    assert macro.name == "test-macro"
    assert calls == ["a", "b"]  # capture() eylemi hemen de çalıştırır

    calls.clear()
    macro.play()
    assert calls == ["a", "b"]


def test_automation_engine_event_triggered():
    bus = EventSystem()
    calls = []

    from harita.extensibility.macro_system import Macro

    macro = Macro(name="on-create")
    macro.add_step("fire", lambda: calls.append("fired"))

    engine = AutomationEngine(event_system=bus)
    engine.add_rule(AutomationRule(name="r1", event_pattern="building.created", macro=macro))

    bus.emit("building.created")
    assert calls == ["fired"]


# ============================================================ #
# ModuleManager / ThemeSystem
# ============================================================ #

def test_module_manager_lazy_enable_disable():
    mgr = ModuleManager()
    assert mgr.is_enabled("mesh_engine")  # varsayılan modüller etkin kayıtlı gelir

    module = mgr.get("mesh_engine")
    assert hasattr(module, "Mesh3D")

    mgr.disable("mesh_engine")
    with pytest.raises(ModuleNotEnabledError):
        mgr.get("mesh_engine")

    mgr.enable("mesh_engine")
    assert mgr.get("mesh_engine") is not None


def test_theme_system_switch():
    from harita.extensibility.module_manager import Theme

    themes = ThemeSystem()
    themes.register_theme(Theme(name="custom", colors={"background": "#111111"}))
    themes.set_active("custom")
    assert themes.active_theme.colors["background"] == "#111111"
    themes.set_active("light")
    assert themes.active_theme.colors["background"] == "#ffffff"


# ============================================================ #
# PluginManager
# ============================================================ #

def test_plugin_register_enable_disable():
    manager = PluginManager()
    loaded = []

    class MyPlugin(Plugin):
        def on_load(self, mgr):
            loaded.append("loaded")

        def on_unload(self, mgr):
            loaded.append("unloaded")

    manager.register(MyPlugin(PluginMeta(name="my-plugin")))
    manager.enable("my-plugin")
    assert manager.is_enabled("my-plugin")
    assert loaded == ["loaded"]

    manager.disable("my-plugin")
    assert not manager.is_enabled("my-plugin")
    assert loaded == ["loaded", "unloaded"]


def test_plugin_function_plugin():
    manager = PluginManager()
    manager.register_function("adder", lambda a, b: a + b)
    manager.enable("adder")
    plugin = manager.get("adder")
    assert plugin.obj(2, 3) == 5


def test_plugin_dependency_order():
    manager = PluginManager()
    order = []

    class Base(Plugin):
        def on_load(self, mgr):
            order.append("base")

    class Dependent(Plugin):
        def on_load(self, mgr):
            order.append("dependent")

    manager.register(Base(PluginMeta(name="base")))
    manager.register(Dependent(PluginMeta(name="dependent", depends_on=["base"])))
    manager.enable("dependent")
    assert order == ["base", "dependent"]


def test_plugin_missing_dependency_raises():
    manager = PluginManager()

    class Dependent(Plugin):
        pass

    manager.register(Dependent(PluginMeta(name="dependent", depends_on=["ghost"])))
    with pytest.raises(PluginDependencyError):
        manager.enable("dependent")


def test_plugin_error_isolation():
    manager = PluginManager()

    class Broken(Plugin):
        def on_load(self, mgr):
            raise RuntimeError("boom")

    manager.register(Broken(PluginMeta(name="broken")))
    manager.enable("broken")
    assert not manager.is_enabled("broken")


def test_plugin_discover_directory(tmp_path):
    plugin_file = tmp_path / "sample_plugin.py"
    plugin_file.write_text(
        "from harita.extensibility.plugin_system import FunctionPlugin\n"
        "PLUGIN = FunctionPlugin('sample', object())\n"
    )
    manager = PluginManager()
    discovered = manager.discover_directory(str(tmp_path))
    assert discovered == ["sample"]
    assert "sample" in manager.list_plugins()


# ============================================================ #
# RestRouter
# ============================================================ #

def test_rest_router_dispatch_get():
    router = RestRouter()

    @router.get("/buildings/<id>")
    def _handler(id, **_):  # noqa: A002
        return {"id": id}

    response = router.dispatch("GET", "/buildings/42")
    assert response.status == 200
    assert response.body == {"id": "42"}


def test_rest_router_not_found():
    router = RestRouter()
    with pytest.raises(RestNotFoundError):
        router.dispatch("GET", "/nope")


def test_build_default_router():
    registry = {"twin-1": {"id": "twin-1"}}
    router = build_default_router(digital_twin_registry=registry)
    response = router.dispatch("GET", "/buildings/twin-1")
    assert response.status == 200


# ============================================================ #
# ScriptAPI
# ============================================================ #

def test_script_api_python_execution():
    api = ScriptAPI()
    result = api.run("python", "_result = 1 + 2")
    assert result.ok
    assert result.result == 3


def test_script_api_lua_unavailable_returns_error_result():
    api = ScriptAPI()
    if "lua" in api.available_languages():
        pytest.skip("lupa kurulu - unavailable senaryosu test edilemez")
    result = api.run("lua", "return 1")
    assert not result.ok
    assert "lupa" in result.error or "kurulu" in result.error


# ============================================================ #
# WebSocketRouter
# ============================================================ #

def test_websocket_router_dispatch_handler():
    router = WebSocketRouter()
    router.register("ping", lambda conn, payload: "pong")
    conn = router.connect()
    reply = router.dispatch(conn, WSMessage(type="ping", payload=None))
    assert reply.type == "ping.reply"
    assert reply.payload == "pong"


def test_websocket_router_subscribe_and_publish():
    router = WebSocketRouter()
    conn = router.connect()
    router.dispatch(conn, WSMessage(type="subscribe", payload="topic-a"))
    count = router.publish("topic-a", {"hello": "world"})
    assert count == 1
    assert conn.outbox[-1].payload == {"hello": "world"}


def test_websocket_connection_closed_raises():
    conn = WSConnection()
    conn.close()
    with pytest.raises(WSConnectionClosedError):
        conn.send(WSMessage(type="x"))


def test_websocket_bridges_event_system():
    bus = EventSystem()
    router = WebSocketRouter()
    conn = router.connect()
    conn.subscribe("building.created")
    router.bridge_events(bus)
    bus.emit("building.created", payload={"id": 1})
    assert conn.outbox[-1].payload == {"id": 1}


def test_websocket_message_json_roundtrip():
    msg = WSMessage(type="ping", payload={"a": 1})
    raw = msg.to_json()
    restored = WSMessage.from_json(raw)
    assert restored.type == "ping"
    assert restored.payload == {"a": 1}


# ============================================================ #
# ProjectFile / .harita format & migration
# ============================================================ #

def test_project_file_create_and_roundtrip():
    pf = ProjectFile.create(name="Test Şehri")
    twin = DigitalTwin(id="t1")
    pf.add_twin("t1", twin)

    raw = pf.to_json()
    restored = ProjectFile.from_json(raw)
    assert restored.name == "Test Şehri"
    assert "t1" in restored.twins
    assert restored.schema_version == SCHEMA_VERSION


def test_project_file_save_load(tmp_path):
    pf = ProjectFile.create(name="Kayıt Testi")
    path = tmp_path / "scene.harita"
    pf.save(str(path))

    loaded = ProjectFile.load(str(path))
    assert loaded.name == "Kayıt Testi"


def test_project_file_migration_from_old_schema():
    pf = ProjectFile.create(name="Eski Proje")
    raw = pf.to_dict()
    raw["schema_version"] = "1.0.0"
    del raw["tags"]

    old_pf = ProjectFile(
        name=raw["name"],
        schema_version="1.0.0",
        tags=[],
    )
    # force an "old" instance without calling from_dict validation
    old_pf.schema_version = "1.0.0"

    migrated, old_v, new_v = migrate_project_file(old_pf)
    assert old_v == "1.0.0"
    assert new_v == SCHEMA_VERSION
    assert migrated.schema_version == SCHEMA_VERSION


def test_project_file_migration_noop_when_current():
    pf = ProjectFile.create(name="Güncel")
    migrated, old_v, new_v = migrate_project_file(pf)
    assert old_v == new_v == SCHEMA_VERSION
    assert migrated is pf


def test_project_file_unknown_schema_raises():
    pf = ProjectFile.create(name="X")
    raw = pf.to_dict()
    raw["schema_version"] = "99.0.0"
    with pytest.raises(UnknownSchemaVersionError):
        ProjectFile.from_dict(raw)


# ============================================================ #
# CLI
# ============================================================ #

def test_cli_project_new_and_info(tmp_path):
    cli = CLI()
    path = str(tmp_path / "scene.harita")

    result = cli.run(["project", "new", path, "--name", "CLI Projesi"])
    assert result.exit_code == 0
    assert os.path.exists(path)

    result = cli.run(["project", "info", path])
    assert result.exit_code == 0
    assert "CLI Projesi" in result.output


def test_cli_project_migrate_noop(tmp_path):
    cli = CLI()
    path = str(tmp_path / "scene.harita")
    cli.run(["project", "new", path, "--name", "X"])
    result = cli.run(["project", "migrate", path])
    assert result.exit_code == 0
    assert "güncel" in result.output


def test_cli_plugin_list_empty():
    cli = CLI()
    result = cli.run(["plugin", "list"])
    assert result.exit_code == 0


def test_cli_invalid_command_returns_nonzero():
    cli = CLI()
    result = cli.run(["project", "info", "/nonexistent/path.harita"])
    assert result.exit_code == 1
