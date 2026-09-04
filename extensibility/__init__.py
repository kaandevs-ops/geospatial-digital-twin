"""
Extensibility (Genişletilebilirlik)
=====================================

Roadmap Phase 14 - "Gelecekte büyümesi için".

Bu faz, platformun kendisini değil, platformun *etrafında büyüyebileceği*
altyapıyı sağlar: eklenti sistemi, script API, olay sistemi, modül/tema
yönetimi, makro & otomasyon, REST API, WebSocket desteği, CLI ve
proje dosya formatı + sürüm yönetimi.

Alt modüller:
    * `plugin_system`    - `Plugin`, `PluginManager` (kayıt/keşif/bağımlılık sıralı yükleme)
    * `script_api`        - `ScriptAPI` (Python doğrudan; Lua/JS opsiyonel bağımlılıkla)
    * `event_system`      - `EventSystem` (glob destekli senkron pub/sub)
    * `module_manager`    - `ModuleManager` (lazy alt-sistem açma/kapama), `ThemeSystem`
    * `macro_system`      - `MacroRecorder`/`Macro`, `AutomationEngine` (olay-tetikli otomasyon)
    * `rest_api`          - `RestRouter` (framework-agnostic, dispatch-tabanlı)
    * `websocket_api`     - `WebSocketRouter` (soket-agnostik, topic pub/sub)
    * `cli`               - `CLI` (argparse tabanlı, `run(argv)` ile test edilebilir)
    * `project_format`    - `ProjectFile` (`.harita` JSON formatı) + migration altyapısı

Bağımlılık: yalnızca stdlib (Lua/JS script motorları hariç - onlar opsiyonel).
"""

from __future__ import annotations

from .ai_script_draft import ScriptDraft, generate_script_draft
from .cli import CLI, CLIResult
from .event_system import Event, EventSystem, Listener, default_bus
from .macro_system import (
    AutomationEngine,
    AutomationRule,
    Macro,
    MacroRecorder,
    MacroStep,
)
from .module_manager import (
    ModuleManager,
    ModuleNotEnabledError,
    ModuleRecord,
    Theme,
    ThemeSystem,
)
from .plugin_registry import (
    IncompatiblePluginError,
    PluginVersionRegistry,
    Version,
    VersionParseError,
    VersionRange,
    parse_version,
)
from .plugin_signing import (
    PluginTrustStore,
    RSAKeyPair,
    RSAPrivateKey,
    RSAPublicKey,
    SignatureError,
    sign_bytes,
    sign_file,
    verify_bytes,
    verify_file,
)
from .plugin_system import (
    FunctionPlugin,
    Plugin,
    PluginDependencyError,
    PluginManager,
    PluginMeta,
    PluginRecord,
)
from .project_format import (
    MIGRATIONS,
    SCHEMA_VERSION,
    ProjectFile,
    ProjectFileError,
    UnknownSchemaVersionError,
    migrate_project_file,
    register_migration,
)
from .rest_api import (
    RestNotFoundError,
    RestResponse,
    RestRouter,
    Route,
    build_default_router,
)
from .script_api import (
    JavaScriptEngine,
    LuaScriptEngine,
    PythonScriptEngine,
    ScriptAPI,
    ScriptContext,
    ScriptEngineUnavailable,
    ScriptError,
    ScriptResult,
)
from .websocket_api import (
    WebSocketRouter,
    WSConnection,
    WSConnectionClosedError,
    WSMessage,
)

__all__ = [
    # event_system
    "Event",
    "EventSystem",
    "Listener",
    "default_bus",
    # macro_system
    "MacroStep",
    "Macro",
    "MacroRecorder",
    "AutomationRule",
    "AutomationEngine",
    # module_manager
    "ModuleNotEnabledError",
    "ModuleRecord",
    "ModuleManager",
    "Theme",
    "ThemeSystem",
    # plugin_system
    "PluginMeta",
    "Plugin",
    "FunctionPlugin",
    "PluginRecord",
    "PluginDependencyError",
    "PluginManager",
    # plugin_registry (ROADMAP_V4 - Faz E14)
    "Version",
    "VersionParseError",
    "VersionRange",
    "parse_version",
    "IncompatiblePluginError",
    "PluginVersionRegistry",
    # plugin_signing
    "RSAPublicKey",
    "RSAPrivateKey",
    "RSAKeyPair",
    "SignatureError",
    "sign_bytes",
    "verify_bytes",
    "sign_file",
    "verify_file",
    "PluginTrustStore",
    # rest_api
    "Route",
    "RestResponse",
    "RestNotFoundError",
    "RestRouter",
    "build_default_router",
    # script_api
    "ScriptEngineUnavailable",
    "ScriptError",
    "ScriptResult",
    "ScriptContext",
    "PythonScriptEngine",
    "LuaScriptEngine",
    "JavaScriptEngine",
    "ScriptAPI",
    # websocket_api
    "WSMessage",
    "WSConnectionClosedError",
    "WSConnection",
    "WebSocketRouter",
    # cli
    "ScriptDraft",
    "generate_script_draft",
    "CLI",
    "CLIResult",
    # project_format
    "SCHEMA_VERSION",
    "MIGRATIONS",
    "ProjectFileError",
    "UnknownSchemaVersionError",
    "ProjectFile",
    "migrate_project_file",
    "register_migration",
]
