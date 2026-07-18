import json
import os
from pathlib import Path

# Identify the checkout root for source/editable use, while retaining a
# self-contained installed-module fallback for standalone wheels.
_CONFIG_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR_BASE = (
    _CONFIG_DIR.parent
    if _CONFIG_DIR.name == "src" and (_CONFIG_DIR.parent / "pyproject.toml").is_file()
    else _CONFIG_DIR
)

# Provide sensible defaults, then overlay the active runtime settings file.
# Actanara does not load a workspace .env file. Non-secret runtime
# configuration is persisted in ~/.actanara/config/settings.json. Environment
# variables are limited to bootstrap/secret injection and process-local
# diagnostics; they must not override normal persisted settings here.

# Paths
DEFAULT_ACTANARA_HOME = Path.home() / ".actanara"
DEFAULT_LOCATION_FILE = Path("~/.config/actanara/location.json").expanduser()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.expanduser().read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _selected_home() -> Path:
    env_home = os.getenv("ACTANARA_HOME")
    if env_home:
        return Path(env_home).expanduser()
    location_file = Path(os.getenv("ACTANARA_LOCATION_FILE", str(DEFAULT_LOCATION_FILE))).expanduser()
    selected = _read_json(location_file).get("actanaraHome")
    return Path(selected).expanduser() if selected else DEFAULT_ACTANARA_HOME


def _get_nested(payload: dict, dotted_path: str, default=None):
    value = payload
    for part in dotted_path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


ACTANARA_HOME = _selected_home()
_SETTINGS = _read_json(ACTANARA_HOME / "config" / "settings.json")


def _settings_path(dotted_path: str, default: Path | str) -> Path:
    value = _get_nested(_SETTINGS, dotted_path)
    return Path(value).expanduser() if value else Path(default).expanduser()


def _settings_str(dotted_path: str, default: str) -> str:
    value = _get_nested(_SETTINGS, dotted_path)
    return str(value) if value not in (None, "") else default


WORKSPACE_DIR = _settings_path("general.workspaceRoot", WORKSPACE_DIR_BASE)
DIARY_OUTPUT_DIR = _settings_path("paths.diary.generatedDiary", ACTANARA_HOME / "artifacts" / "diary")
TMP_WORKSPACE = _settings_path("paths.logsCacheTmp.tmp", ACTANARA_HOME / "state" / "tmp")
ACTANARA_DATA_DB_PATH = _settings_path("paths.runtime.database", ACTANARA_HOME / "data" / "actanara_data.sqlite3")
ACTANARA_DATA_EXPORT_DIR = _settings_path("paths.runtime.snapshots", ACTANARA_HOME / "snapshots")

# LLM Configuration. Secret values may still be injected by the parent
# process or the secret store, but they are not loaded from .env.
LLM_API_KEY = ""
LLM_HOST = _settings_str("llmProvider.endpoint", "")
LLM_MODEL_NAME = _settings_str("llmProvider.model", "")

# DB Configuration
TASK_DB_PATH = str(_settings_path("paths.tasks.legacyTaskDatabase", ACTANARA_HOME / "data" / "nova_tasks.db"))

# Data foundation production defaults. Legacy remains archived for explicit
# diagnostic/import/comparison use, but is no longer the normal runtime source.
ACTANARA_DATA_FOUNDATION_ENABLED = True
DASHBOARD_READ_SOURCE = _settings_str("runtimeSources.dashboardReadSource", "foundation")
REPORT_READ_SOURCE = _settings_str("runtimeSources.reportReadSource", "foundation")
DIARY_METRICS_SOURCE = _settings_str("runtimeSources.diaryMetricsSource", "foundation")
DIARY_MEMORY_SOURCE = _settings_str("runtimeSources.diaryMemorySource", "foundation")
DIARY_TASKS_SOURCE = _settings_str("runtimeSources.diaryTasksSource", "foundation")
TASK_AUDIT_SINK = _settings_str("runtimeSources.taskAuditSink", "foundation")

# Misc
TARGET_TIMEZONE = _settings_str("general.timezone", "Asia/Hong_Kong")
