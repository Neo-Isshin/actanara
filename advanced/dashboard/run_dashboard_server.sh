#!/bin/zsh
set -eu

SCRIPT_DIR="${0:A:h}"
SOURCE_ROOT="${SCRIPT_DIR:h:h}"
RESOLVER_PYTHON="${OPEN_NOVA_DASHBOARD_RESOLVER_PYTHON:-python3}"

export NOVA_HOME="${NOVA_HOME:-${HOME}/.open-nova}"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONPATH="${SOURCE_ROOT}:${SOURCE_ROOT}/src"
export OPEN_NOVA_DASHBOARD_SOURCE_ROOT="${SOURCE_ROOT}"

eval "$("${RESOLVER_PYTHON}" - <<'PY'
import shlex

try:
    from data_foundation.paths import load_paths
    from data_foundation.settings import resolve_dashboard_settings

    settings = resolve_dashboard_settings(load_paths())
    values = {
        "PROJECT_ROOT": settings["projectRoot"],
        "PYTHON_BIN": settings["pythonExecutable"],
        "HOST": settings["host"],
        "PORT": str(settings["port"]),
    }
except Exception:
    import os
    from pathlib import Path

    source = Path(os.environ["OPEN_NOVA_DASHBOARD_SOURCE_ROOT"])
    values = {
        "PROJECT_ROOT": str(source),
        "PYTHON_BIN": "python3",
        "HOST": "127.0.0.1",
        "PORT": "3036",
    }

for key, value in values.items():
    print(f"{key}={shlex.quote(str(value))}")
PY
)"
export PYTHONPATH="${PROJECT_ROOT}:${PROJECT_ROOT}/src"

cd "${PROJECT_ROOT}"
exec "${PYTHON_BIN}" -m uvicorn app.main:app \
  --app-dir "${PROJECT_ROOT}/src/dashboard" \
  --host "${HOST}" \
  --port "${PORT}"
