#!/bin/sh
# Linux bootstrap adapter. Keep it as one compound command so a truncated
# streamed adapter cannot execute a valid prefix.
if true; then
set -eu
umask 077

DEFAULT_SOURCE_URL="https://github.com/Neo-Isshin/actanara.git"
SOURCE_ROOT="${ACTANARA_INSTALL_SOURCE_ROOT:-}"
SOURCE_URL="${ACTANARA_INSTALL_SOURCE_URL:-}"
SOURCE_REF="${ACTANARA_INSTALL_REF:-}"
CACHE_ROOT="${ACTANARA_INSTALL_CACHE_ROOT:-$HOME/.cache/actanara/installer}"
GIT_BIN="${ACTANARA_INSTALL_GIT:-git}"
PYTHON_BIN="${ACTANARA_INSTALL_PYTHON:-python3}"
DRY_RUN="${ACTANARA_INSTALL_DRY_RUN:-0}"
OFFLINE="${ACTANARA_INSTALL_OFFLINE:-0}"

bootstrap_usage() {
  cat <<'EOF'
Actanara Linux setup adapter

Usage:
  install/bootstrap-linux.sh [bootstrap-options] [-- installer-options]

Bootstrap options:
  --source-root PATH   Use an existing local source tree.
  --source-url URL     Git source URL.
  --ref COMMIT         Exact 40- or 64-character commit.
  --cache-root PATH    Installer source/dependency cache root.
  --git PATH           Git executable.
  --python PATH        CPython 3.13 executable.
  --offline            Never access the network.
  --dry-run            Validate and print the Linux installation plan.
EOF
}

bootstrap_error() {
  printf 'Actanara Linux setup: %s\n' "$*" >&2
}

require_value() {
  option="$1"
  value="${2:-}"
  case "$value" in
    ""|--*) bootstrap_error "$option requires a value"; exit 2 ;;
  esac
}

is_full_commit_id() {
  value="$1"
  case "$value" in
    ""|*[!0123456789abcdefABCDEF]*) return 1 ;;
  esac
  [ "${#value}" -eq 40 ] || [ "${#value}" -eq 64 ]
}

git_exec() {
  if [ "$OFFLINE" = "1" ]; then
    GIT_NO_LAZY_FETCH=1 \
    GIT_ALLOW_PROTOCOL= \
    GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
      "$GIT_BIN" -c protocol.allow=never -c core.hooksPath=/dev/null "$@"
    return $?
  fi
  GIT_TERMINAL_PROMPT=0 \
  GIT_CONFIG_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null \
    "$GIT_BIN" -c core.hooksPath=/dev/null "$@"
}

# Bootstrap options are consumed until `--` or the first installer option.
while [ "$#" -gt 0 ]; do
  case "$1" in
    --source-root)
      require_value "$1" "${2:-}"; SOURCE_ROOT="$2"; shift 2 ;;
    --source-url)
      require_value "$1" "${2:-}"; SOURCE_URL="$2"; shift 2 ;;
    --ref)
      require_value "$1" "${2:-}"; SOURCE_REF="$2"; shift 2 ;;
    --cache-root)
      require_value "$1" "${2:-}"; CACHE_ROOT="$2"; shift 2 ;;
    --git)
      require_value "$1" "${2:-}"; GIT_BIN="$2"; shift 2 ;;
    --python)
      require_value "$1" "${2:-}"; PYTHON_BIN="$2"; shift 2 ;;
    --offline)
      OFFLINE=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      bootstrap_usage; exit 0 ;;
    --)
      shift; break ;;
    *)
      break ;;
  esac
done

if [ "$(uname -s 2>/dev/null || printf unknown)" != "Linux" ] && [ "${ACTANARA_INSTALL_TEST_MODE:-0}" != "1" ]; then
  bootstrap_error "this adapter only supports Linux"
  exit 2
fi

if [ -z "$SOURCE_ROOT" ]; then
  case "$0" in
    */*)
      script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" 2>/dev/null && pwd -P) || script_dir=""
      if [ -n "$script_dir" ] && [ -f "$script_dir/install_linux.py" ]; then
        SOURCE_ROOT=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
      fi
      ;;
  esac
fi

if [ -z "$SOURCE_ROOT" ]; then
  [ -n "$SOURCE_URL" ] || SOURCE_URL="$DEFAULT_SOURCE_URL"
  if ! command -v "$GIT_BIN" >/dev/null 2>&1 && [ ! -x "$GIT_BIN" ]; then
    bootstrap_error "Git is required to download Actanara"
    exit 2
  fi
  SOURCE_ROOT="$CACHE_ROOT/source"
  if [ "$OFFLINE" = "1" ]; then
    if [ -z "$SOURCE_REF" ] || [ ! -d "$SOURCE_ROOT/.git" ]; then
      bootstrap_error "offline setup requires an exact cached source commit"
      exit 2
    fi
  elif [ ! -d "$SOURCE_ROOT/.git" ]; then
    mkdir -p "$CACHE_ROOT"
    git_exec clone --filter=blob:none --sparse --no-checkout "$SOURCE_URL" "$SOURCE_ROOT"
    git_exec -C "$SOURCE_ROOT" sparse-checkout init --no-cone
    git_exec -C "$SOURCE_ROOT" sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src
  fi
  if [ -z "$SOURCE_REF" ]; then
    git_exec -C "$SOURCE_ROOT" fetch --force origin '+refs/heads/main:refs/remotes/origin/main'
    SOURCE_REF=$(git_exec -C "$SOURCE_ROOT" rev-parse --verify 'refs/remotes/origin/main^{commit}')
  elif [ "$OFFLINE" != "1" ]; then
    git_exec -C "$SOURCE_ROOT" fetch --force origin "$SOURCE_REF"
  fi
  if ! is_full_commit_id "$SOURCE_REF"; then
    bootstrap_error "the selected source did not resolve to an exact commit"
    exit 2
  fi
  SOURCE_REF=$(printf '%s' "$SOURCE_REF" | tr '[:upper:]' '[:lower:]')
  resolved_ref=$(git_exec -C "$SOURCE_ROOT" rev-parse --verify "$SOURCE_REF^{commit}" 2>/dev/null || true)
  resolved_ref=$(printf '%s' "$resolved_ref" | tr '[:upper:]' '[:lower:]')
  if [ "$resolved_ref" != "$SOURCE_REF" ]; then
    bootstrap_error "the cached source does not match the selected commit"
    exit 2
  fi
  git_exec -C "$SOURCE_ROOT" checkout --detach "$SOURCE_REF"
  git_exec -C "$SOURCE_ROOT" reset --hard "$SOURCE_REF"
  git_exec -C "$SOURCE_ROOT" clean -fdX
fi

if [ ! -f "$SOURCE_ROOT/install/install_linux.py" ]; then
  bootstrap_error "Linux installer not found under source root: $SOURCE_ROOT"
  exit 2
fi

if ! "$PYTHON_BIN" -c 'import platform, sys; raise SystemExit(0 if platform.python_implementation() == "CPython" and sys.version_info[:2] == (3, 13) else 1)' >/dev/null 2>&1; then
  bootstrap_error "the current Linux Runtime lock requires CPython 3.13"
  exit 2
fi

ACTANARA_INSTALL_DRY_RUN="$DRY_RUN" \
ACTANARA_INSTALL_OFFLINE="$OFFLINE" \
  "$PYTHON_BIN" "$SOURCE_ROOT/install/install_linux.py" \
    --source-root "$SOURCE_ROOT" \
    --python "$PYTHON_BIN" \
    "$@"
fi
