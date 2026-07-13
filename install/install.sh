#!/usr/bin/env zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
DEFAULT_SOURCE_ROOT="${SCRIPT_DIR:h}"
SOURCE_ROOT="${NOVA_INSTALL_SOURCE_ROOT:-$DEFAULT_SOURCE_ROOT}"
PYTHON_BIN="${NOVA_INSTALL_PYTHON:-python3}"
PYTHON_AUTO_INSTALL="${NOVA_INSTALL_PYTHON_AUTO_INSTALL:-1}"
PYTHON_CANDIDATES="${NOVA_INSTALL_PYTHON_CANDIDATES:-}"
PYTHON_INSTALL_PLANNED=0
PYTHON_STANDALONE_RELEASE="${NOVA_INSTALL_PYTHON_STANDALONE_RELEASE:-20260623}"
PYTHON_STANDALONE_VERSION="${NOVA_INSTALL_PYTHON_STANDALONE_VERSION:-3.13.14}"
PYTHON_STANDALONE_BASE_URL="${NOVA_INSTALL_PYTHON_STANDALONE_BASE_URL:-https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_STANDALONE_RELEASE}}"
PYTHON_STANDALONE_URL="${NOVA_INSTALL_PYTHON_STANDALONE_URL:-}"
PYTHON_STANDALONE_SHA256="${NOVA_INSTALL_PYTHON_STANDALONE_SHA256:-}"
PYTHON_STANDALONE_DIR="${NOVA_INSTALL_PYTHON_STANDALONE_DIR:-}"
CURL_BIN="${NOVA_INSTALL_CURL:-}"
TAR_BIN="${NOVA_INSTALL_TAR:-}"
SHASUM_BIN="${NOVA_INSTALL_SHASUM:-}"
OPENSSL_BIN="${NOVA_INSTALL_OPENSSL:-}"
RUNTIME_HOME="${NOVA_INSTALL_RUNTIME:-$HOME/.open-nova}"
DIARY_OUTPUT_DIR="${NOVA_INSTALL_DIARY_OUTPUT:-}"
DESKTOP_DIARY_LINK="${NOVA_INSTALL_DESKTOP_DIARY_LINK:-$HOME/Desktop/Open Nova}"
REPORTS_OUTPUT_DIR="${NOVA_INSTALL_REPORTS_OUTPUT:-}"
SNAPSHOTS_OUTPUT_DIR="${NOVA_INSTALL_SNAPSHOTS_OUTPUT:-}"
ARCHIVES_OUTPUT_DIR="${NOVA_INSTALL_ARCHIVES_OUTPUT:-}"
VENV_DIR=""
NO_SCHEDULER=0
NO_DASHBOARD_SERVER=0
ENABLE_DASHBOARD=1
ENABLE_RAG=0
DEPLOY_EMBEDDING_SERVER=0
ENABLE_NOVA_TASK=1
ENABLE_LLM_GENERATION=1
ENABLE_DEV_TEST=0
CREATE_DESKTOP_DIARY_LINK=1
ENABLE_SHELL_PATH=1
ENABLE_SKILL_REGISTRATION=0
DASHBOARD_HOST="${NOVA_INSTALL_DASHBOARD_HOST:-127.0.0.1}"
DASHBOARD_PORT="${NOVA_INSTALL_DASHBOARD_PORT:-${NOVA_DASHBOARD_PORT:-3036}}"
DASHBOARD_PORT_AUTO="${NOVA_INSTALL_DASHBOARD_PORT_AUTO:-1}"
DASHBOARD_PORT_CANDIDATES="${NOVA_INSTALL_DASHBOARD_PORT_CANDIDATES:-3036 8765 8766 8767 8768}"
LSOF_BIN="${NOVA_INSTALL_LSOF:-}"
INSTALL_TEST_MODE="${NOVA_INSTALL_TEST_MODE:-0}"
if [[ "$INSTALL_TEST_MODE" != "0" && "$INSTALL_TEST_MODE" != "1" ]]; then
  print -r -- "NOVA_INSTALL_TEST_MODE must be 0 or 1" >&2
  exit 2
fi
LAUNCHCTL_BIN=""
if [[ "$INSTALL_TEST_MODE" == "1" ]]; then
  LAUNCHCTL_BIN="${NOVA_INSTALL_LAUNCHCTL:-}"
fi
RAG_EMBEDDING_MODE="${NOVA_INSTALL_RAG_EMBEDDING_MODE:-local}"
RAG_LOCAL_MODEL="${NOVA_INSTALL_RAG_LOCAL_MODEL:-intfloat/multilingual-e5-small}"
RAG_LOCAL_DIMENSION="${NOVA_INSTALL_RAG_LOCAL_DIMENSION:-384}"
RAG_LOCAL_MODEL_SET=0
if [[ -n "${NOVA_INSTALL_RAG_LOCAL_MODEL:-}" ]]; then
  RAG_LOCAL_MODEL_SET=1
fi
LLM_PROVIDER_MODE="${NOVA_INSTALL_LLM_PROVIDER_MODE:-custom}"
LLM_PROVIDER="${NOVA_INSTALL_LLM_PROVIDER:-custom}"
LLM_API="${NOVA_INSTALL_LLM_API:-openai-compatible}"
LLM_ENDPOINT="${NOVA_INSTALL_LLM_ENDPOINT:-}"
LLM_MODEL="${NOVA_INSTALL_LLM_MODEL:-}"
LLM_API_KEY_ENV="${NOVA_INSTALL_LLM_API_KEY_ENV:-LLM_API_KEY}"
LLM_API_KEY_VALUE="${NOVA_INSTALL_LLM_API_KEY_VALUE:-}"
RAG_CLOUD_PROVIDER="${NOVA_INSTALL_RAG_CLOUD_PROVIDER:-openai-compatible}"
RAG_CLOUD_ENDPOINT="${NOVA_INSTALL_RAG_CLOUD_ENDPOINT:-}"
RAG_CLOUD_MODEL="${NOVA_INSTALL_RAG_CLOUD_MODEL:-}"
RAG_CLOUD_DIMENSION="${NOVA_INSTALL_RAG_CLOUD_DIMENSION:-}"
RAG_CLOUD_API_KEY_ENV="${NOVA_INSTALL_RAG_CLOUD_API_KEY_ENV:-NOVA_RAG_CLOUD_API_KEY}"
INSTALL_LANGUAGE="${NOVA_INSTALL_LANGUAGE:-zh-CN}"
PIPELINE_LANGUAGE_PROFILE="zh"
PIPELINE_ENGLISH_ENABLED=0
PIPELINE_DIARY_SCHEMA_VERSION="diary-v1-zh"
PIPELINE_PROMPT_PAYLOAD_PROFILE="zh-CN"
RAG_LANGUAGE_PROFILE="zh"
DRY_RUN=0
UPGRADE=0
SOURCE_ONLY=0
WIZARD_MODE="${NOVA_INSTALL_WIZARD:-auto}"
YES=0
WIZARD_CONFIRMED=0
SUMMARY_ONLY=0
UNAME_BIN="$(command -v uname 2>/dev/null || true)"
if [[ -z "$UNAME_BIN" && -x "/usr/bin/uname" ]]; then
  UNAME_BIN="/usr/bin/uname"
fi
ID_BIN="$(command -v id 2>/dev/null || true)"
if [[ -z "$ID_BIN" && -x "/usr/bin/id" ]]; then
  ID_BIN="/usr/bin/id"
fi
if [[ "$INSTALL_TEST_MODE" == "1" && -n "${NOVA_INSTALL_PLATFORM:-}" ]]; then
  PLATFORM="$NOVA_INSTALL_PLATFORM"
elif [[ -n "$UNAME_BIN" ]]; then
  PLATFORM="$("$UNAME_BIN" -s)"
else
  PLATFORM="unknown"
fi
RUNTIME_SET=0
DIARY_OUTPUT_SET=0
DESKTOP_DIARY_LINK_SET=0
REPORTS_OUTPUT_SET=0
SNAPSHOTS_OUTPUT_SET=0
ARCHIVES_OUTPUT_SET=0
PYTHON_SET=0
NO_SCHEDULER_SET=0
NO_DASHBOARD_SERVER_SET=0
DASHBOARD_PORT_SET=0
DASHBOARD_HOST_SET=0
RAG_SET=0
EMBEDDING_SERVER_SET=0
LLM_SET=0
RAG_EMBEDDING_MODE_SET=0
LANGUAGE_SET=0
LANGUAGE_SELECTED=0
SHELL_PATH_SET=0
SELECTED_EXTERNAL_TOOLS=""
CLI_SHIM=""
USER_CLI_SHIM="${NOVA_INSTALL_USER_CLI_SHIM:-$HOME/.local/bin/open-nova}"
SHELL_PATH_FILE="${NOVA_INSTALL_SHELL_PATH_FILE:-}"
INSTALLER_LOG_FILE=""
STAGED_RELEASE_ID=""
STAGED_RELEASE_TARGET=""
UPDATE_TRANSACTION_ACTIVE=0
UPDATE_TRANSACTION_ID=""
UPDATE_TRANSACTION_DIR=""
UPDATE_TRANSACTION_JOURNAL=""
UPDATE_VALIDATION_RUNTIME=""
UPDATE_SERVICE_STATE_FILE=""
UPDATE_PRIOR_SOURCE_KIND="missing"
UPDATE_PRIOR_SOURCE_TARGET=""
UPDATE_PRIOR_SOURCE_BACKUP=""
UPDATE_STAGED_VENV=""
UPDATE_PRIOR_VENV_BACKUP=""
UPDATE_MUTABLE_STATE_CAPTURED=0
UPDATE_ROLLBACK_RUNNING=0
UPDATE_COMMITTED=0
UPDATE_TEST_MODE="${INSTALL_TEST_MODE}"
UPDATE_TEST_FAIL_PHASE="${NOVA_INSTALL_TEST_FAIL_PHASE:-}"
UPDATE_TEST_HOOK="${NOVA_INSTALL_TEST_HOOK:-}"
UPDATE_TRANSACTION_HELPER="${SCRIPT_DIR}/update_transaction.py"

usage() {
  cat <<'EOF'
Open Nova installer v2

Usage:
  install/install.sh [options]

Options:
  --runtime PATH              Runtime/install target. Default: ~/.open-nova
  --diary-output PATH         Generated diary output path. Default: <runtime>/artifacts/diary
  --desktop-diary-link PATH   Desktop symlink target. Default: ~/Desktop/Open Nova
  --no-desktop-diary-link     Do not create a Desktop symlink to generated diaries.
  --no-shell-path             Do not add the user CLI shim directory to shell PATH.
  --shell-path-file PATH      Shell profile file for PATH setup. Default: ~/.zprofile on macOS/zsh.
  --reports-output PATH       Report output path. Default: <runtime>/artifacts/reports
  --snapshots-output PATH     Snapshot output path. Default: <runtime>/snapshots
  --archives-output PATH      Archive/intermediate output path. Default: <runtime>/sources/archives
  --source-root PATH          Local Open Nova source checkout. Default: script parent
  --python PATH               Python used to create the runtime venv. Default: python3
  --no-python-auto-install    Do not install managed standalone Python when Python >=3.11 is missing.
  --no-scheduler              Do not register scheduled launchd jobs.
  --no-dashboard              Deprecated: Dashboard is required; use --no-dashboard-server to skip the service.
  --no-dashboard-server       Do not install/start the SSE server service.
  --dashboard-host HOST       Dashboard/SSE server host. Default: 127.0.0.1.
  --dashboard-port PORT       Preferred Dashboard/SSE server port. Default: 3036.
  --dashboard-port-auto       Auto-select a fallback port when the preferred port is in use. Default.
  --no-dashboard-port-auto    Fail preflight instead of selecting a fallback Dashboard port.
  --enable-rag                Enable nova-RAG install choices.
  --register-rag-skills       Register the read-only nova-RAG skill into installer-selected external tools.
  --enable-dev-test           Advanced: install developer/test optional dependencies.
  --rag-embedding-mode MODE   nova-RAG embedding mode: local or cloud. Default: local
  --rag-local-model MODEL     Local nova-RAG embedding model.
  --rag-local-dimension N     Local nova-RAG embedding dimension.
  --deploy-embedding-server   Queue local embedding server deployment in the background.
                              Default when nova-RAG local mode is enabled.
  --no-deploy-embedding-server
                              Do not queue local embedding server deployment.
  --llm-provider NAME         LLM provider id/preset for non-secret settings.
  --llm-endpoint URL          LLM endpoint for non-secret settings.
  --llm-model MODEL           LLM model for non-secret settings.
  --llm-api-key-env NAME      Environment variable that contains the LLM API key.
  --rag-cloud-provider NAME   Cloud embedding provider id/preset.
  --rag-cloud-endpoint URL    Cloud embedding endpoint URL.
  --rag-cloud-model MODEL     Cloud embedding model.
  --rag-cloud-dimension N     Cloud embedding dimension.
  --rag-cloud-api-key-env NAME
                              Environment variable that contains the embedding API key.
  --language LOCALE           Install-time language profile: zh-CN or en-US. Default: zh-CN.
  --dry-run                   Print the plan without writing files or running commands.
  --upgrade                   Upgrade an existing runtime while preserving settings and secrets.
  --source-only               Only deploy/repair the runtime source snapshot.
  --wizard                    Force interactive guided setup.
  --no-wizard                 Disable interactive guided setup.
  --yes                       Skip final interactive confirmation.
  --summary-only              Print only the installer summary and useful commands.
  -h, --help                  Show this help.

Notice:
  Open Nova processes local diaries, agent/tool history, and user-selected
  source material. If those inputs already contain secrets or sensitive
  personal data, generated diaries, reports, snapshots, and indexes may
  faithfully preserve that information. The installer stores secret references
  or environment variable names, not secret values.
EOF
}

log() {
  if [[ "$SUMMARY_ONLY" == "1" ]]; then
    return 0
  fi
  local log_file=""
  log_file="$(installer_log_file)"
  if [[ -d "${log_file:h}" ]]; then
    print -r -- "==> $*" >> "$log_file"
  fi
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "==> $*"
  fi
}

warn() {
  if [[ "$SUMMARY_ONLY" == "1" ]]; then
    return 0
  fi
  local log_file=""
  log_file="$(installer_log_file)"
  if [[ -d "${log_file:h}" ]]; then
    print -r -- "WARN: $*" >> "$log_file"
  fi
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "WARN: $*" >&2
  fi
}

progress_label() {
  local cmd="$1"
  shift || true
  case "$cmd $*" in
    "mkdir -p "*)
      print -r -- "Preparing runtime directories"
      ;;
    *" -m venv "*)
      print -r -- "Creating Python environment"
      ;;
    *" -m pip install --upgrade pip"*)
      print -r -- "Updating package installer"
      ;;
    *" -m pip install "*)
      print -r -- "Installing runtime dependencies"
      ;;
    *)
      print -r -- "$cmd"
      ;;
  esac
}

progress_start() {
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  print -r -- "  ◐ $*"
}

progress_ok() {
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  print -r -- "  ✓ $*"
}

progress_fail() {
  print -r -- "  x $*" >&2
}

run_cmd() {
  local label=""
  local log_file=""
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  label="$(progress_label "$@")"
  progress_start "$label"
  if [[ "$DRY_RUN" != "1" ]]; then
    log_file="$(installer_log_file)"
    mkdir -p "${log_file:h}"
    print -r -- "" >> "$log_file"
    print -r -- "## ${label}: $*" >> "$log_file"
    if ! "$@" >> "$log_file" 2>&1; then
      progress_fail "${label} failed; see ${log_file}"
      return 1
    fi
  fi
  progress_ok "$label"
}

run_optional_cmd() {
  local label="$1"
  shift
  local log_file=""
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  progress_start "$label"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_ok "$label"
    return 0
  fi
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}: $*" >> "$log_file"
  if ! "$@" >> "$log_file" 2>&1; then
    warn "${label} failed; continuing because this is not required for core runtime install"
    return 0
  fi
  progress_ok "$label"
}

installer_log_file() {
  if [[ -n "$INSTALLER_LOG_FILE" ]]; then
    print -r -- "$INSTALLER_LOG_FILE"
  else
    print -r -- "${RUNTIME_HOME}/state/logs/installer-v2.log"
  fi
}

run_json_cmd() {
  local label="$1"
  shift
  local log_file=""
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  progress_start "$label"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_ok "$label"
    return 0
  fi
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}: $*" >> "$log_file"
  if ! "$@" >> "$log_file" 2>&1; then
    progress_fail "${label} failed; see ${log_file}"
    return 1
  fi
  progress_ok "$label"
}

run_optional_json_cmd() {
  local label="$1"
  shift
  local log_file=""
  if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  progress_start "$label"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_ok "$label"
    return 0
  fi
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}: $*" >> "$log_file"
  if ! "$@" >> "$log_file" 2>&1; then
    warn "${label} failed; continuing because this is not required for core runtime install. See ${log_file}"
    return 0
  fi
  progress_ok "$label"
}

TTY_CLEAR=$'\033[2J\033[H'
if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  TTY_BLUE=$'\033[34m'
  TTY_YELLOW=$'\033[33m'
  TTY_RED=$'\033[31m'
  TTY_RESET=$'\033[0m'
else
  TTY_BLUE=""
  TTY_YELLOW=""
  TTY_RED=""
  TTY_RESET=""
fi
TTY_GREEN="$TTY_BLUE"
TTY_DEEP_BLUE="$TTY_BLUE"
TTY_DIM=""

installer_version() {
  local version=""
  version="$(awk -F'"' '/^version = / { print $2; exit }' "${SOURCE_ROOT}/pyproject.toml" 2>/dev/null || true)"
  print -r -- "${version:-0.0.0}"
}

render_installer_header() {
  local version=""
  version="$(installer_version)"
  print -r -- "${TTY_BLUE}  ████╗ ████╗ ████╗ █  █╗    █  █╗  ███╗ █   █╗ ███╗ ${TTY_RESET}  Open Nova ${version}" > /dev/tty
  print -r -- "${TTY_BLUE} █╔══█║ █╔═█║ █▂▂▂  ██ █║    ██ █║ █╔═█║ █   █║ █╔═█║${TTY_RESET}  installer v2" > /dev/tty
  print -r -- "${TTY_BLUE} █║  █║ ████╔╝█▔▔▔  █ ██║    █ ██║ █║ █║ ╚█ █╔╝ ████║${TTY_RESET}  runtime: ${RUNTIME_HOME}" > /dev/tty
  print -r -- "${TTY_BLUE}  ████╔╝█║    ████╗ █  █║    █  █║  ███╔╝ ╚█╔╝  █║ █║${TTY_RESET}" > /dev/tty
  print -r -- "────────────────────────────────────────────────────────────" > /dev/tty
}

print_installer_data_notice() {
  log "Data notice: Open Nova processes local diaries, agent/tool history, and user-selected source material."
  log "Data notice: generated diaries, reports, snapshots, and indexes may preserve sensitive information already present in those inputs."
  log "Data notice: installer-managed settings store secret references or environment variable names, not secret values."
}

installer_text() {
  local key="$1"
  local text_language="$INSTALL_LANGUAGE"
  if [[ "$LANGUAGE_SET" != "1" && "$LANGUAGE_SELECTED" != "1" ]]; then
    text_language="en-US"
  fi
  case "$text_language" in
    en-US|en|en_US)
      case "$key" in
        nav) print -r -- "Use Up/Down or j/k, then Return." ;;
        yes_recommended) print -r -- "Yes (recommended)" ;;
        no) print -r -- "No" ;;
        welcome)
          print -r -- "Welcome to Open Nova. Core pipeline, Dashboard, and Nova-Task are installed by default."
          print -r -- "Continue only if you understand that enabled workflows process local data and may create small LLM token usage."
          ;;
        welcome_cancelled) print -r -- "Install cancelled from welcome screen" ;;
        language_prompt) print -r -- "Choose Open Nova language profile" ;;
        invalid_env) print -r -- "Invalid environment variable name. Press Return to try again." ;;
        core_dependency_title) print -r -- "Core dependency check" ;;
        core_dependency_action) print -r -- "checking non-RAG runtime dependencies" ;;
        rag_dependency_title) print -r -- "nova-RAG dependency check" ;;
        rag_dependency_action) print -r -- "checking RAG-specific dependencies" ;;
        press_return) print -r -- "Press Return to continue." ;;
        detecting_tools) print -r -- "Detecting tools... just a minute" ;;
        detected_tools) print -r -- "Detected tools" ;;
        tools_help)
          print -r -- "Selected tools will be covered by Open Nova; unselected tools will not be collected."
          print -r -- "Use Up/Down or j/k to move, Space to toggle, and Return to continue."
          ;;
        no_tools) print -r -- "No known tool paths were detected. Choose manual to add one." ;;
        manual_tool_name) print -r -- "Manual tool name" ;;
        manual_tool_path) print -r -- "Manual tool path" ;;
        rag_choice_prompt) print -r -- "Choose nova-RAG memory/search setup" ;;
        rag_not_now) print -r -- "Not now" ;;
        rag_local) print -r -- "Local embedding" ;;
        rag_cloud) print -r -- "Cloud embedding" ;;
        rag_local_model_prompt) print -r -- "Choose local nova-RAG embedding model" ;;
        llm_provider_prompt) print -r -- "Select LLM provider" ;;
        llm_provider_help) print -r -- "A small, fast model is sufficient for Open Nova's harness workflow; stronger models may improve quality." ;;
        llm_model_prompt) print -r -- "Select LLM model" ;;
        custom_input) print -r -- "custom input" ;;
        custom_llm_endpoint) print -r -- "Custom LLM endpoint URL" ;;
        custom_llm_model) print -r -- "Custom LLM model id" ;;
        llm_model_id) print -r -- "LLM model id" ;;
        llm_api_key_value_prompt) print -r -- "Paste LLM API key value (stored in local secret store; leave blank to configure later)" ;;
        llm_api_key_env_prompt) print -r -- "LLM API key environment variable name (for example LLM_API_KEY; do not paste the secret value)" ;;
        cloud_provider) print -r -- "Cloud embedding provider id/preset" ;;
        cloud_endpoint) print -r -- "Cloud embedding endpoint URL" ;;
        cloud_model) print -r -- "Cloud embedding model" ;;
        cloud_dimension) print -r -- "Cloud embedding dimension" ;;
        cloud_key_env) print -r -- "Cloud embedding API key environment variable name (secret value is not stored)" ;;
        useful_commands) print -r -- "Useful commands:" ;;
        install_summary) print -r -- "Install summary" ;;
        proceed_upgrade)
          print -r -- "Proceed with upgrade now?"
          print -r -- "Dependencies and managed services will be updated while preserving runtime settings and secrets."
          ;;
        proceed_install)
          print -r -- "Proceed with install now?"
          print -r -- "The installer will create runtime files and may register managed services."
          ;;
        upgrade_cancelled) print -r -- "Upgrade cancelled before making changes" ;;
        install_cancelled) print -r -- "Install cancelled before making changes" ;;
        *) print -r -- "$key" ;;
      esac
      ;;
    *)
      case "$key" in
        nav) print -r -- "使用方向键或 j/k 选择，然后按 Return。" ;;
        yes_recommended) print -r -- "是（推荐）" ;;
        no) print -r -- "否" ;;
        welcome)
          print -r -- "欢迎使用 Open Nova。核心管线、Dashboard 和 Nova-Task 默认安装。"
          print -r -- "请确认你理解：启用的工作流会处理本地数据，并可能产生少量 LLM token 用量。"
          ;;
        welcome_cancelled) print -r -- "已在欢迎页取消安装" ;;
        language_prompt) print -r -- "选择 Open Nova 界面语言" ;;
        invalid_env) print -r -- "环境变量名无效。按 Return 后重试。" ;;
        core_dependency_title) print -r -- "核心依赖检查" ;;
        core_dependency_action) print -r -- "正在检查非 RAG 运行时依赖" ;;
        rag_dependency_title) print -r -- "nova-RAG 依赖检查" ;;
        rag_dependency_action) print -r -- "正在检查 RAG 专用依赖" ;;
        press_return) print -r -- "按 Return 继续。" ;;
        detecting_tools) print -r -- "正在检测工具……请稍候" ;;
        detected_tools) print -r -- "已检测到的工具" ;;
        tools_help)
          print -r -- "选中的工具会纳入 Open Nova 覆盖范围；未选中的工具不会被采集。"
          print -r -- "使用方向键或 j/k 移动，空格切换，按 Return 继续。"
          ;;
        no_tools) print -r -- "未检测到已知工具路径。选择 manual 可手动添加。" ;;
        manual_tool_name) print -r -- "手动工具名称" ;;
        manual_tool_path) print -r -- "手动工具路径" ;;
        rag_choice_prompt) print -r -- "选择 nova-RAG 记忆/搜索配置" ;;
        rag_not_now) print -r -- "暂不启用" ;;
        rag_local) print -r -- "本地 embedding" ;;
        rag_cloud) print -r -- "云端 embedding" ;;
        rag_local_model_prompt) print -r -- "选择本地 nova-RAG embedding 模型" ;;
        llm_provider_prompt) print -r -- "选择 LLM provider" ;;
        llm_provider_help) print -r -- "基于我们的 harness 工程，小型、快速的模型即可承担系统要求；更强模型可能提升质量。" ;;
        llm_model_prompt) print -r -- "选择 LLM 模型" ;;
        custom_input) print -r -- "自定义输入" ;;
        custom_llm_endpoint) print -r -- "自定义 LLM endpoint URL" ;;
        custom_llm_model) print -r -- "自定义 LLM model id" ;;
        llm_model_id) print -r -- "LLM model id" ;;
        llm_api_key_value_prompt) print -r -- "粘贴 LLM API key 值（会写入本机 secret-store；留空则稍后配置）" ;;
        llm_api_key_env_prompt) print -r -- "LLM API key 环境变量名（例如 LLM_API_KEY；不要粘贴密钥值）" ;;
        cloud_provider) print -r -- "云端 embedding provider id/preset" ;;
        cloud_endpoint) print -r -- "云端 embedding endpoint URL" ;;
        cloud_model) print -r -- "云端 embedding 模型" ;;
        cloud_dimension) print -r -- "云端 embedding 维度" ;;
        cloud_key_env) print -r -- "云端 embedding API key 环境变量名（不会存储密钥值）" ;;
        useful_commands) print -r -- "常用命令：" ;;
        install_summary) print -r -- "安装摘要" ;;
        proceed_upgrade)
          print -r -- "现在继续升级吗？"
          print -r -- "升级会更新依赖和托管服务，同时保留 runtime settings 和 secrets。"
          ;;
        proceed_install)
          print -r -- "现在继续安装吗？"
          print -r -- "安装器会创建 runtime 文件，并可能注册托管服务。"
          ;;
        upgrade_cancelled) print -r -- "升级已取消，尚未修改文件" ;;
        install_cancelled) print -r -- "安装已取消，尚未修改文件" ;;
        *) print -r -- "$key" ;;
      esac
      ;;
  esac
}

clear_tty_menu() {
  print -n -- "$TTY_CLEAR" > /dev/tty
  render_installer_header
}

print_tty_copy() {
  local copy="$1"
  local width="${COLUMNS:-80}"
  local rendered=""
  if [[ "$width" != <40-999> ]]; then
    width=80
  fi
  if rendered="$(NOVA_INSTALL_COPY="$copy" NOVA_INSTALL_COPY_WIDTH="$width" "$PYTHON_BIN" -c '
import os
import re
import unicodedata

text = os.environ.get("NOVA_INSTALL_COPY", "")
limit = max(32, int(os.environ.get("NOVA_INSTALL_COPY_WIDTH", "80")) - 2)
ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

def width(value):
    clean = ansi.sub("", value)
    return sum(2 if unicodedata.east_asian_width(ch) in {"W", "F"} else 0 if unicodedata.combining(ch) else 1 for ch in clean)

for semantic_line in text.splitlines() or [""]:
    remaining = semantic_line
    while width(remaining) > limit:
        used = 0
        cut = 0
        last_space = -1
        for index, char in enumerate(remaining):
            used += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 0 if unicodedata.combining(char) else 1
            if char.isspace():
                last_space = index
            if used > limit:
                cut = last_space if last_space > 0 else index
                break
        print(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    print(remaining)
' 2>/dev/null)"; then
    print -r -- "$rendered" > /dev/tty
  else
    print -r -- "$copy" > /dev/tty
  fi
}

prompt_line() {
  local prompt="$1"
  local default_value="$2"
  local answer=""
  print -n -- "${prompt} [${default_value}]: " > /dev/tty
  IFS= read -r answer < /dev/tty || answer=""
  if [[ -n "$answer" ]]; then
    print -r -- "$answer"
  else
    print -r -- "$default_value"
  fi
}

prompt_line_page() {
  clear_tty_menu
  prompt_line "$@"
}

valid_env_var_name() {
  local value="$1"
  [[ "$value" =~ '^[A-Za-z_][A-Za-z0-9_]*$' ]]
}

safe_env_var_label() {
  local value="$1"
  if valid_env_var_name "$value"; then
    print -r -- "$value"
  else
    print -r -- "configured secret reference"
  fi
}

llm_api_key_env_error() {
  print -r -- "LLM API key environment variable name must look like LLM_API_KEY; do not paste the API key value." >&2
  print -r -- "To store an API key, use Dashboard Settings or run: open-nova model key --value-stdin" >&2
}

validate_llm_api_key_env() {
  if valid_env_var_name "$LLM_API_KEY_ENV"; then
    return 0
  fi
  llm_api_key_env_error
  return 2
}

prompt_llm_api_key_env() {
  local answer=""
  while true; do
    answer="$(prompt_line_page "$(installer_text llm_api_key_env_prompt)" "$LLM_API_KEY_ENV")"
    if valid_env_var_name "$answer"; then
      LLM_API_KEY_ENV="$answer"
      return 0
    fi
    print -r -- "$(installer_text invalid_env)" > /dev/tty
    IFS= read -r _ < /dev/tty || true
  done
}

prompt_secret_line_page() {
  local prompt="$1"
  local answer=""
  clear_tty_menu
  print -n -- "${prompt}: " > /dev/tty
  IFS= read -rs answer < /dev/tty || answer=""
  print -r -- "" > /dev/tty
  print -r -- "$answer"
}

prompt_llm_api_key_value() {
  local answer=""
  answer="$(prompt_secret_line_page "$(installer_text llm_api_key_value_prompt)")"
  if [[ -n "$answer" ]]; then
    LLM_API_KEY_VALUE="$answer"
  fi
}

prompt_yes_no() {
  local prompt="$1"
  local default_value="$2"
  local selected=1
  local key=""
  local escape=""
  local options=("$(installer_text yes_recommended)" "$(installer_text no)")
  if [[ "$default_value" != "yes" ]]; then
    selected=2
  fi
  while true; do
    clear_tty_menu
    print_tty_copy "$prompt"
    print -r -- "$(installer_text nav)" > /dev/tty
    for idx in {1..2}; do
      if [[ "$idx" == "$selected" ]]; then
        print -r -- "${TTY_GREEN}  > ${options[$idx]}${TTY_RESET}" > /dev/tty
      else
        print -r -- "    ${options[$idx]}" > /dev/tty
      fi
    done
    IFS= read -rs -k 1 key < /dev/tty || key=""
    if [[ "$key" == $'\e' ]]; then
      IFS= read -rs -k 2 escape < /dev/tty || escape=""
      case "$escape" in
        "[A") selected=$(( selected == 1 ? 2 : selected - 1 )) ;;
        "[B") selected=$(( selected == 2 ? 1 : selected + 1 )) ;;
      esac
    elif [[ "$key" == "k" ]]; then
      selected=$(( selected == 1 ? 2 : selected - 1 ))
    elif [[ "$key" == "j" ]]; then
      selected=$(( selected == 2 ? 1 : selected + 1 ))
    elif [[ "$key" == $'\n' || "$key" == $'\r' || -z "$key" ]]; then
      [[ "$selected" == "1" ]]
      return $?
    fi
  done
}

prompt_choice_impl() {
  local prompt="$1"
  local help_text="$2"
  local default_value="$3"
  shift 3
  local options=("$@")
  local selected=1
  local idx=1
  local key=""
  local escape=""
  for (( idx = 1; idx <= ${#options[@]}; idx++ )); do
    if [[ "${options[$idx]}" == "$default_value" ]]; then
      selected="$idx"
    fi
  done
  while true; do
    clear_tty_menu
    print_tty_copy "$prompt"
    if [[ -n "$help_text" ]]; then
      print_tty_copy "$help_text"
    fi
    print -r -- "$(installer_text nav)" > /dev/tty
    for (( idx = 1; idx <= ${#options[@]}; idx++ )); do
      if [[ "$idx" == "$selected" ]]; then
        print -r -- "${TTY_GREEN}  > ${options[$idx]}${TTY_RESET}" > /dev/tty
      else
        print -r -- "    ${options[$idx]}" > /dev/tty
      fi
    done
    IFS= read -rs -k 1 key < /dev/tty || key=""
    if [[ "$key" == $'\e' ]]; then
      IFS= read -rs -k 2 escape < /dev/tty || escape=""
      case "$escape" in
        "[A") selected=$(( selected == 1 ? ${#options[@]} : selected - 1 )) ;;
        "[B") selected=$(( selected == ${#options[@]} ? 1 : selected + 1 )) ;;
      esac
    elif [[ "$key" == "k" ]]; then
      selected=$(( selected == 1 ? ${#options[@]} : selected - 1 ))
    elif [[ "$key" == "j" ]]; then
      selected=$(( selected == ${#options[@]} ? 1 : selected + 1 ))
    elif [[ "$key" == $'\n' || "$key" == $'\r' || -z "$key" ]]; then
      print -r -- "${options[$selected]}"
      return 0
    fi
  done
}

prompt_choice() {
  local prompt="$1"
  local default_value="$2"
  shift 2
  prompt_choice_impl "$prompt" "" "$default_value" "$@"
}

prompt_choice_with_help() {
  local prompt="$1"
  local help_text="$2"
  local default_value="$3"
  shift 3
  prompt_choice_impl "$prompt" "$help_text" "$default_value" "$@"
}

rag_local_model_dimension() {
  case "$1" in
    "BAAI/bge-large-zh-v1.5") print -r -- "1024" ;;
    "intfloat/multilingual-e5-small") print -r -- "384" ;;
    "BAAI/bge-large-en-v1.5") print -r -- "1024" ;;
    "all-MiniLM-L6-v2") print -r -- "384" ;;
    *) print -r -- "" ;;
  esac
}

apply_language_profile() {
  case "$INSTALL_LANGUAGE" in
    zh|zh-CN|zh_CN)
      INSTALL_LANGUAGE="zh-CN"
      PIPELINE_LANGUAGE_PROFILE="zh"
      PIPELINE_ENGLISH_ENABLED=0
      PIPELINE_DIARY_SCHEMA_VERSION="diary-v1-zh"
      PIPELINE_PROMPT_PAYLOAD_PROFILE="zh-CN"
      RAG_LANGUAGE_PROFILE="zh"
      if [[ "$RAG_LOCAL_MODEL_SET" != "1" ]]; then
        RAG_LOCAL_MODEL="intfloat/multilingual-e5-small"
        RAG_LOCAL_DIMENSION="384"
      fi
      ;;
    en|en-US|en_US)
      INSTALL_LANGUAGE="en-US"
      PIPELINE_LANGUAGE_PROFILE="en"
      PIPELINE_ENGLISH_ENABLED=1
      PIPELINE_DIARY_SCHEMA_VERSION="diary-v1-en"
      PIPELINE_PROMPT_PAYLOAD_PROFILE="en-US"
      RAG_LANGUAGE_PROFILE="en"
      if [[ "$RAG_LOCAL_MODEL_SET" != "1" ]]; then
        RAG_LOCAL_MODEL="all-MiniLM-L6-v2"
        RAG_LOCAL_DIMENSION="384"
      fi
      ;;
    *)
      print -r -- "--language must be zh-CN or en-US" >&2
      exit 2
      ;;
  esac
}

prompt_language_profile() {
  local selected_label=""
  local default_label="Chinese (zh-CN)"
  if [[ "$INSTALL_LANGUAGE" == "en" || "$INSTALL_LANGUAGE" == "en-US" || "$INSTALL_LANGUAGE" == "en_US" ]]; then
    default_label="English (en-US)"
  fi
  selected_label="$(prompt_choice "$(installer_text language_prompt)" "$default_label" "Chinese (zh-CN)" "English (en-US)")"
  if [[ "$selected_label" == "English (en-US)" ]]; then
    INSTALL_LANGUAGE="en-US"
  else
    INSTALL_LANGUAGE="zh-CN"
  fi
  LANGUAGE_SET=1
  LANGUAGE_SELECTED=1
  apply_language_profile
}

prompt_rag_local_model() {
  local labels=(
    "中文/多语 384 · intfloat/multilingual-e5-small"
    "中文 1024 · BAAI/bge-large-zh-v1.5"
    "English 384 · all-MiniLM-L6-v2"
    "English 1024 · BAAI/bge-large-en-v1.5"
  )
  local models=(
    "intfloat/multilingual-e5-small"
    "BAAI/bge-large-zh-v1.5"
    "all-MiniLM-L6-v2"
    "BAAI/bge-large-en-v1.5"
  )
  local default_label="${labels[1]}"
  local selected_label=""
  local idx=1
  for (( idx = 1; idx <= ${#models[@]}; idx++ )); do
    if [[ "${models[$idx]}" == "$RAG_LOCAL_MODEL" ]]; then
      default_label="${labels[$idx]}"
      break
    fi
  done
  selected_label="$(prompt_choice "$(installer_text rag_local_model_prompt)" "$default_label" "${labels[@]}")"
  for (( idx = 1; idx <= ${#labels[@]}; idx++ )); do
    if [[ "${labels[$idx]}" == "$selected_label" ]]; then
      RAG_LOCAL_MODEL="${models[$idx]}"
      RAG_LOCAL_DIMENSION="$(rag_local_model_dimension "$RAG_LOCAL_MODEL")"
      break
    fi
  done
}

llm_provider_catalog_rows() {
  resolve_python_bin || return 0
  PYTHONPATH="${SOURCE_ROOT}:${SOURCE_ROOT}/src" "${PYTHON_BIN}" - <<'PY'
from data_foundation.llm_provider_catalog import llm_provider_catalog

for provider in llm_provider_catalog():
    if provider.get("enabled") or provider.get("id") == "custom":
        print("\t".join([
            str(provider.get("id") or ""),
            str(provider.get("name") or provider.get("id") or ""),
            str(provider.get("api") or "openai-compatible"),
            str(provider.get("endpoint") or ""),
        ]))
PY
  true
}

llm_model_catalog_rows() {
  local provider_id="$1"
  resolve_python_bin || return 0
  NOVA_SELECTED_LLM_PROVIDER="$provider_id" PYTHONPATH="${SOURCE_ROOT}:${SOURCE_ROOT}/src" "${PYTHON_BIN}" - <<'PY'
import os
from data_foundation.llm_provider_catalog import find_provider

provider = find_provider(os.environ.get("NOVA_SELECTED_LLM_PROVIDER"), require_enabled=True)
if not provider:
    raise SystemExit(0)
for model in provider.get("models") or []:
    print("\t".join([
        str(model.get("id") or ""),
        str(model.get("name") or model.get("id") or ""),
    ]))
PY
  true
}

prompt_llm_provider_from_catalog() {
  local provider_rows=("${(@f)$(llm_provider_catalog_rows)}")
  local provider_ids=()
  local provider_names=()
  local provider_apis=()
  local provider_endpoints=()
  local provider_labels=()
  local fields=()
  local row=""
  local idx=1
  local selected_label=""
  local selected_idx=1
  local provider_id=""
  local model_rows=()
  local model_ids=()
  local model_names=()
  local model_labels=()

  if [[ "${#provider_rows[@]}" -eq 0 ]]; then
    LLM_PROVIDER_MODE="custom"
    LLM_PROVIDER="custom"
    LLM_API="openai-compatible"
    LLM_ENDPOINT="$(prompt_line_page "$(installer_text custom_llm_endpoint)" "${LLM_ENDPOINT:-https://api.openai.com/v1}")"
    LLM_MODEL="$(prompt_line_page "$(installer_text custom_llm_model)" "$LLM_MODEL")"
    prompt_llm_api_key_value
    return 0
  fi

  for row in "${provider_rows[@]}"; do
    fields=("${(@ps:\t:)row}")
    provider_ids+=("${fields[1]}")
    provider_names+=("${fields[2]}")
    provider_apis+=("${fields[3]}")
    provider_endpoints+=("${fields[4]}")
    provider_labels+=("${fields[2]} (${fields[1]})")
  done
  for (( idx = 1; idx <= ${#provider_ids[@]}; idx++ )); do
    if [[ "${provider_ids[$idx]}" == "openai" ]]; then
      selected_idx="$idx"
      break
    fi
  done

  selected_label="$(prompt_choice_with_help "$(installer_text llm_provider_prompt)" "$(installer_text llm_provider_help)" "${provider_labels[$selected_idx]}" "${provider_labels[@]}")"
  for (( idx = 1; idx <= ${#provider_labels[@]}; idx++ )); do
    if [[ "${provider_labels[$idx]}" == "$selected_label" ]]; then
      selected_idx="$idx"
      break
    fi
  done

  provider_id="${provider_ids[$selected_idx]}"
  if [[ "$provider_id" == "custom" ]]; then
    LLM_PROVIDER_MODE="custom"
    LLM_PROVIDER="custom"
    LLM_API="openai-compatible"
    LLM_ENDPOINT="$(prompt_line_page "$(installer_text custom_llm_endpoint)" "${LLM_ENDPOINT:-https://api.openai.com/v1}")"
    LLM_MODEL="$(prompt_line_page "$(installer_text custom_llm_model)" "$LLM_MODEL")"
    prompt_llm_api_key_value
    return 0
  fi

  LLM_PROVIDER_MODE="preset"
  LLM_PROVIDER="$provider_id"
  LLM_API="${provider_apis[$selected_idx]}"
  LLM_ENDPOINT="${provider_endpoints[$selected_idx]}"
  model_rows=("${(@f)$(llm_model_catalog_rows "$provider_id")}")
  for row in "${model_rows[@]}"; do
    fields=("${(@ps:\t:)row}")
    model_ids+=("${fields[1]}")
    model_names+=("${fields[2]}")
    model_labels+=("${fields[2]} (${fields[1]})")
  done
  if [[ "${#model_ids[@]}" -gt 0 ]]; then
    model_labels+=("$(installer_text custom_input)")
    selected_label="$(prompt_choice "$(installer_text llm_model_prompt)" "${model_labels[1]}" "${model_labels[@]}")"
    if [[ "$selected_label" == "$(installer_text custom_input)" ]]; then
      LLM_MODEL="$(prompt_line_page "$(installer_text custom_llm_model)" "$LLM_MODEL")"
    else
      for (( idx = 1; idx <= ${#model_ids[@]}; idx++ )); do
        if [[ "${model_labels[$idx]}" == "$selected_label" ]]; then
          LLM_MODEL="${model_ids[$idx]}"
          break
        fi
      done
    fi
  else
    LLM_MODEL="$(prompt_line_page "$(installer_text llm_model_id)" "$LLM_MODEL")"
  fi
  prompt_llm_api_key_value
}

detect_external_tool_rows() {
  local tool_path=""
  local seen=";"
  for tool_path in $HOME/.openclaw(N) $HOME/.openclaw-*(N) $HOME/.openclaw_*(N); do
    if [[ "$seen" != *";${tool_path:A};"* ]]; then
      print -r -- "openclaw|OpenClaw|🧭|${tool_path:A}"
      seen="${seen}${tool_path:A};"
    fi
  done
  seen=";"
  for tool_path in $HOME/.claude(N) $HOME/.claude-*(N) $HOME/.claude_*(N); do
    if [[ "$seen" != *";${tool_path:A};"* ]]; then
      print -r -- "claudeCode|Claude Code|🧠|${tool_path:A}"
      seen="${seen}${tool_path:A};"
    fi
  done
  seen=";"
  for tool_path in $HOME/.codex(N) $HOME/.codex-*(N) $HOME/.codex_*(N); do
    if [[ "$seen" != *";${tool_path:A};"* ]]; then
      print -r -- "codex|Codex|🤖|${tool_path:A}"
      seen="${seen}${tool_path:A};"
    fi
  done
  seen=";"
  for tool_path in $HOME/.gemini(N) $HOME/.gemini-*(N) $HOME/.gemini_*(N); do
    if [[ "$seen" != *";${tool_path:A};"* ]]; then
      print -r -- "geminiCli|Gemini CLI|💎|${tool_path:A}"
      seen="${seen}${tool_path:A};"
    fi
  done
  seen=";"
  for tool_path in $HOME/.hermes(N) $HOME/.hermes-*(N) $HOME/.hermes_*(N); do
    if [[ "$seen" != *";${tool_path:A};"* ]]; then
      print -r -- "hermes|Hermes|⚕️|${tool_path:A}"
      seen="${seen}${tool_path:A};"
    fi
  done
}

prompt_external_tools() {
  clear_tty_menu
  print -r -- "$(installer_text detecting_tools)" > /dev/tty
  sleep 0.2

  local rows=("${(@f)$(detect_external_tool_rows)}")
  local keys=()
  local labels=()
  local emojis=()
  local paths=()
  local counts=()
  local selected=()
  local row=""
  local fields=()
  local idx=1
  local cursor=1
  local key=""
  local escape=""
  local manual_key=""
  local manual_name=""
  local manual_path=""
  local chosen=()

  for row in "${rows[@]}"; do
    if [[ -z "$row" ]]; then
      continue
    fi
    fields=("${(@ps:|:)row}")
    if [[ "${#fields[@]}" -lt 4 ]]; then
      continue
    fi
    keys+=("${fields[1]}")
    labels+=("${fields[2]}")
    emojis+=("${fields[3]}")
    paths+=("${fields[4]}")
    selected+=(1)
  done
  keys+=("manual")
  labels+=("manual")
  emojis+=("✍️")
  paths+=("Enter tool name and path manually")
  selected+=(0)

  while true; do
    clear_tty_menu
    print -r -- "$(installer_text detected_tools)" > /dev/tty
    print -r -- "$(installer_text tools_help)" > /dev/tty
    if [[ "${#rows[@]}" -eq 0 ]]; then
      print -r -- "$(installer_text no_tools)" > /dev/tty
    fi
    counts=()
    for (( idx = 1; idx <= ${#keys[@]}; idx++ )); do
      local marker="[ ]"
      local display_label="${labels[$idx]}"
      [[ "${selected[$idx]}" == "1" ]] && marker="[✓]"
      counts[$idx]=1
      local same_seen=0
      local j=1
      for (( j = 1; j <= idx; j++ )); do
        if [[ "${keys[$j]}" == "${keys[$idx]}" ]]; then
          same_seen=$(( same_seen + 1 ))
        fi
      done
      local same_total=0
      for (( j = 1; j <= ${#keys[@]}; j++ )); do
        if [[ "${keys[$j]}" == "${keys[$idx]}" ]]; then
          same_total=$(( same_total + 1 ))
        fi
      done
      if [[ "$same_total" -gt 1 && "${keys[$idx]}" != "manual" ]]; then
        display_label="${display_label} ${same_seen}"
      fi
      if [[ "$idx" == "$cursor" ]]; then
        print -r -- "${TTY_GREEN}  > ${marker} ${emojis[$idx]} ${display_label} - ${paths[$idx]}${TTY_RESET}" > /dev/tty
      else
        print -r -- "    ${marker} ${emojis[$idx]} ${display_label} - ${paths[$idx]}" > /dev/tty
      fi
    done
    IFS= read -rs -k 1 key < /dev/tty || key=""
    if [[ "$key" == $'\e' ]]; then
      IFS= read -rs -k 2 escape < /dev/tty || escape=""
      case "$escape" in
        "[A") cursor=$(( cursor == 1 ? ${#keys[@]} : cursor - 1 )) ;;
        "[B") cursor=$(( cursor == ${#keys[@]} ? 1 : cursor + 1 )) ;;
      esac
    elif [[ "$key" == "k" ]]; then
      cursor=$(( cursor == 1 ? ${#keys[@]} : cursor - 1 ))
    elif [[ "$key" == "j" ]]; then
      cursor=$(( cursor == ${#keys[@]} ? 1 : cursor + 1 ))
    elif [[ "$key" == " " ]]; then
      selected[$cursor]=$(( selected[$cursor] == 1 ? 0 : 1 ))
    elif [[ "$key" == $'\n' || "$key" == $'\r' || -z "$key" ]]; then
      chosen=()
      for (( idx = 1; idx <= ${#keys[@]}; idx++ )); do
        if [[ "${selected[$idx]}" == "1" ]]; then
          if [[ "${keys[$idx]}" == "manual" ]]; then
            manual_name="$(prompt_line_page "$(installer_text manual_tool_name)" "")"
            manual_path="$(prompt_line_page "$(installer_text manual_tool_path)" "")"
            if [[ -n "$manual_name" && -n "$manual_path" ]]; then
              manual_key="manual-${manual_name:l}"
              manual_key="${manual_key// /-}"
              chosen+=("${manual_key}|${manual_name}|${manual_path:A}")
            fi
          else
            chosen+=("${keys[$idx]}|${labels[$idx]}|${paths[$idx]}")
          fi
        fi
      done
      SELECTED_EXTERNAL_TOOLS="${(j:;;:)chosen}"
      return 0
    fi
  done
}

prompt_rag_choice() {
  if [[ "$RAG_SET" == "1" ]]; then
    return 0
  fi

  local selected_label=""
  local rag_not_now_label="$(installer_text rag_not_now)"
  local rag_local_label="$(installer_text rag_local)"
  local rag_cloud_label="$(installer_text rag_cloud)"
  selected_label="$(prompt_choice "$(installer_text rag_choice_prompt)" "$rag_not_now_label" "$rag_not_now_label" "$rag_local_label" "$rag_cloud_label")"
  case "$selected_label" in
    "$rag_local_label")
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="local"
      RAG_EMBEDDING_MODE_SET=1
      ;;
    "$rag_cloud_label")
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_EMBEDDING_MODE_SET=1
      DEPLOY_EMBEDDING_SERVER=0
      ;;
    *)
      ENABLE_RAG=0
      ;;
  esac
  RAG_SET=1
}

wizard_dependency_line() {
  local dep_status="$1"
  local label="$2"
  local detail="$3"
  local marker="~"
  local color="$TTY_YELLOW"
  case "$dep_status" in
    ok)
      marker="✓"
      color="$TTY_BLUE"
      ;;
    error)
      marker="x"
      color="$TTY_RED"
      ;;
    *)
      marker="~"
      color="$TTY_YELLOW"
      ;;
  esac
  print -r -- "  ${color}${marker}${TTY_RESET} ${label}: ${detail}" > /dev/tty
}

wizard_dependency_page() {
  local title="$1"
  local action="$2"
  shift 2
  local row=""
  local fields=()
  local frame=""
  local continue_key=""

  for frame in "/" "-" "\\" "|"; do
    clear_tty_menu
    print -r -- "$title" > /dev/tty
    print -r -- "${TTY_YELLOW}${frame}${TTY_RESET} ${action}" > /dev/tty
    sleep 0.05
  done

  clear_tty_menu
  print -r -- "$title" > /dev/tty
  for row in "$@"; do
    fields=("${(@ps:|:)row}")
    if [[ "${#fields[@]}" -lt 3 ]]; then
      continue
    fi
    wizard_dependency_line "${fields[1]}" "${fields[2]}" "${fields[3]}"
  done
  print -r -- "" > /dev/tty
  print -r -- "$(installer_text press_return)" > /dev/tty
  IFS= read -r continue_key < /dev/tty || true
}

wizard_core_dependency_gate() {
  local rows=()
  local python_probe=""
  local python_status=0
  local static_missing=0
  local asset=""
  local static_assets=(
    "src/dashboard/app/static/index.html"
    "src/dashboard/app/static/css/style.css"
    "src/dashboard/app/static/js/app.js"
  )

  if ensure_python_bin; then
    set +e
    python_probe="$(python_version_probe "$PYTHON_BIN")"
    python_status=$?
    set -e
    if [[ "$python_status" == "0" && -n "$python_probe" ]]; then
      rows+=("ok|Python runtime|${PYTHON_BIN} reports Python ${python_probe}")
    else
      rows+=("error|Python runtime|${PYTHON_BIN} version could not be verified as Python >=3.11")
    fi
    if "$PYTHON_BIN" -c "import venv" >/dev/null 2>&1; then
      rows+=("ok|Python venv|virtual environments are available")
    else
      rows+=("error|Python venv|python -m venv is unavailable")
    fi
  elif [[ "$DRY_RUN" == "1" && "$PYTHON_INSTALL_PLANNED" == "1" ]]; then
    rows+=("pending|Python runtime|managed Python ${PYTHON_STANDALONE_VERSION} will be installed")
    rows+=("pending|Python venv|will be verified after managed Python is available")
  else
    rows+=("error|Python runtime|Python >=3.11 was not found")
    rows+=("error|Python venv|cannot verify venv before Python is available")
  fi

  for asset in "${static_assets[@]}"; do
    if [[ ! -f "${SOURCE_ROOT}/${asset}" ]]; then
      static_missing=$(( static_missing + 1 ))
    fi
  done
  if [[ "$static_missing" == "0" ]]; then
    rows+=("ok|Dashboard static UI|index.html, style.css, and app.js are present")
  else
    rows+=("error|Dashboard static UI|${static_missing} required asset(s) are missing from source")
  fi

  rows+=("pending|Dashboard runtime packages|fastapi, uvicorn, PyYAML, and croniter will be installed and import-checked in the runtime venv")
  rows+=("pending|Dependency remediation|missing allowlisted packages will be installed automatically, then rechecked")
  wizard_dependency_page "$(installer_text core_dependency_title)" "$(installer_text core_dependency_action)" "${rows[@]}"
}

wizard_rag_dependency_gate() {
  if [[ "$ENABLE_RAG" != "1" ]]; then
    return 0
  fi

  local rows=()
  if [[ "$RAG_EMBEDDING_MODE" == "local" ]]; then
    rows+=("ok|Embedding model|${RAG_LOCAL_MODEL} (${RAG_LOCAL_DIMENSION:-unknown} dimensions)")
    rows+=("pending|nova-RAG local packages|sentence-transformers, torch, numpy, and pydantic will be installed and import-checked")
    rows+=("pending|Embedding server|LaunchAgent and background warmup will be prepared for local mode")
  elif [[ "$RAG_EMBEDDING_MODE" == "cloud" ]]; then
    rows+=("ok|Embedding mode|cloud provider ${RAG_CLOUD_PROVIDER}")
    if [[ -n "$RAG_CLOUD_ENDPOINT" && -n "$RAG_CLOUD_MODEL" && -n "$RAG_CLOUD_DIMENSION" ]]; then
      rows+=("ok|Cloud embedding config|endpoint, model, dimension, and key env are configured")
    else
      rows+=("pending|Cloud embedding config|missing fields can be completed in settings before RAG sync")
    fi
    rows+=("ok|nova-RAG local packages|local embedding packages are not required in cloud mode")
  fi
  rows+=("pending|Dependency remediation|missing allowlisted RAG packages will be installed automatically when required")
  wizard_dependency_page "$(installer_text rag_dependency_title)" "$(installer_text rag_dependency_action)" "${rows[@]}"
}

wizard_enabled() {
  case "$WIZARD_MODE" in
    1|yes|true|on)
      return 0
      ;;
    0|no|false|off)
      return 1
      ;;
    auto)
      [[ -t 1 && -r /dev/tty ]]
      return $?
      ;;
    *)
      print -r -- "Invalid NOVA_INSTALL_WIZARD value: ${WIZARD_MODE}" >&2
      exit 2
      ;;
  esac
}

apply_installer_settings_overlay() {
  local log_file=""
  log "Applying installer settings overlay"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Applying runtime settings"
    progress_ok "Applying runtime settings"
    return 0
  fi
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  progress_start "Applying runtime settings"
  if ! {
  NOVA_INSTALL_SOURCE_ROOT="${SOURCE_ROOT}" \
  NOVA_INSTALL_DEPLOY_SOURCE_ROOT="${DEPLOY_SOURCE_ROOT}" \
  NOVA_INSTALL_RUNTIME="${RUNTIME_HOME}" \
  NOVA_INSTALL_UPGRADE="${UPGRADE}" \
  NOVA_INSTALL_DIARY_OUTPUT="${DIARY_OUTPUT_DIR}" \
  NOVA_INSTALL_DIARY_OUTPUT_SET="${DIARY_OUTPUT_SET}" \
  NOVA_INSTALL_REPORTS_OUTPUT="${REPORTS_OUTPUT_DIR}" \
  NOVA_INSTALL_REPORTS_OUTPUT_SET="${REPORTS_OUTPUT_SET}" \
  NOVA_INSTALL_SNAPSHOTS_OUTPUT="${SNAPSHOTS_OUTPUT_DIR}" \
  NOVA_INSTALL_SNAPSHOTS_OUTPUT_SET="${SNAPSHOTS_OUTPUT_SET}" \
  NOVA_INSTALL_ARCHIVES_OUTPUT="${ARCHIVES_OUTPUT_DIR}" \
  NOVA_INSTALL_ARCHIVES_OUTPUT_SET="${ARCHIVES_OUTPUT_SET}" \
  NOVA_INSTALL_SELECTED_EXTERNAL_TOOLS="${SELECTED_EXTERNAL_TOOLS}" \
  NOVA_INSTALL_ENABLE_SKILL_REGISTRATION="${ENABLE_SKILL_REGISTRATION}" \
  NOVA_INSTALL_ENABLE_DASHBOARD="${ENABLE_DASHBOARD}" \
  NOVA_INSTALL_ENABLE_DASHBOARD_SERVER="$([[ "$NO_DASHBOARD_SERVER" == "1" ]] && print 0 || print 1)" \
  NOVA_INSTALL_DASHBOARD_SERVER_SET="${NO_DASHBOARD_SERVER_SET}" \
  NOVA_INSTALL_DASHBOARD_HOST="${DASHBOARD_HOST}" \
  NOVA_INSTALL_DASHBOARD_HOST_SET="${DASHBOARD_HOST_SET}" \
  NOVA_INSTALL_DASHBOARD_PORT="${DASHBOARD_PORT}" \
  NOVA_INSTALL_DASHBOARD_PORT_SET="${DASHBOARD_PORT_SET}" \
  NOVA_INSTALL_ENABLE_NOVA_TASK="${ENABLE_NOVA_TASK}" \
  NOVA_INSTALL_ENABLE_LLM_GENERATION="${ENABLE_LLM_GENERATION}" \
  NOVA_INSTALL_LLM_SET="${LLM_SET}" \
  NOVA_INSTALL_LLM_PROVIDER_MODE="${LLM_PROVIDER_MODE}" \
  NOVA_INSTALL_LLM_PROVIDER="${LLM_PROVIDER}" \
  NOVA_INSTALL_LLM_API="${LLM_API}" \
  NOVA_INSTALL_LLM_ENDPOINT="${LLM_ENDPOINT}" \
  NOVA_INSTALL_LLM_MODEL="${LLM_MODEL}" \
  NOVA_INSTALL_LLM_API_KEY_ENV="${LLM_API_KEY_ENV}" \
  NOVA_INSTALL_LANGUAGE="${INSTALL_LANGUAGE}" \
  NOVA_INSTALL_LANGUAGE_SET="${LANGUAGE_SET}" \
  NOVA_INSTALL_PIPELINE_LANGUAGE_PROFILE="${PIPELINE_LANGUAGE_PROFILE}" \
  NOVA_INSTALL_PIPELINE_ENGLISH_ENABLED="${PIPELINE_ENGLISH_ENABLED}" \
  NOVA_INSTALL_PIPELINE_DIARY_SCHEMA_VERSION="${PIPELINE_DIARY_SCHEMA_VERSION}" \
  NOVA_INSTALL_PIPELINE_PROMPT_PAYLOAD_PROFILE="${PIPELINE_PROMPT_PAYLOAD_PROFILE}" \
  NOVA_INSTALL_RAG_LANGUAGE_PROFILE="${RAG_LANGUAGE_PROFILE}" \
  NOVA_INSTALL_ENABLE_RAG="${ENABLE_RAG}" \
  NOVA_INSTALL_RAG_SET="${RAG_SET}" \
  NOVA_INSTALL_RAG_EMBEDDING_MODE="${RAG_EMBEDDING_MODE}" \
  NOVA_INSTALL_RAG_EMBEDDING_MODE_SET="${RAG_EMBEDDING_MODE_SET}" \
  NOVA_INSTALL_RAG_LOCAL_MODEL="${RAG_LOCAL_MODEL}" \
  NOVA_INSTALL_RAG_LOCAL_MODEL_SET="${RAG_LOCAL_MODEL_SET}" \
  NOVA_INSTALL_RAG_LOCAL_DIMENSION="${RAG_LOCAL_DIMENSION}" \
  NOVA_INSTALL_RAG_CLOUD_PROVIDER="${RAG_CLOUD_PROVIDER}" \
  NOVA_INSTALL_RAG_CLOUD_ENDPOINT="${RAG_CLOUD_ENDPOINT}" \
  NOVA_INSTALL_RAG_CLOUD_MODEL="${RAG_CLOUD_MODEL}" \
  NOVA_INSTALL_RAG_CLOUD_DIMENSION="${RAG_CLOUD_DIMENSION}" \
  NOVA_INSTALL_RAG_CLOUD_API_KEY_ENV="${RAG_CLOUD_API_KEY_ENV}" \
  PYTHONPATH="${SOURCE_ROOT}:${SOURCE_ROOT}/src" \
  "${VENV_PY}" - <<'PY'
import os
from pathlib import Path

from data_foundation.paths import runtime_paths_for_home
from data_foundation.settings import write_settings

runtime = Path(os.environ["NOVA_INSTALL_RUNTIME"]).expanduser()
deploy_source_root = Path(os.environ["NOVA_INSTALL_DEPLOY_SOURCE_ROOT"]).expanduser()
paths = runtime_paths_for_home(runtime)

is_upgrade = os.environ["NOVA_INSTALL_UPGRADE"] == "1"
enable_rag = os.environ["NOVA_INSTALL_ENABLE_RAG"] == "1"
enable_llm = os.environ["NOVA_INSTALL_ENABLE_LLM_GENERATION"] == "1"
enable_skill_registration = os.environ["NOVA_INSTALL_ENABLE_SKILL_REGISTRATION"] == "1"
embedding_mode = os.environ["NOVA_INSTALL_RAG_EMBEDDING_MODE"]
llm_provider_mode = os.environ["NOVA_INSTALL_LLM_PROVIDER_MODE"]
selected_external_tools_raw = os.environ.get("NOVA_INSTALL_SELECTED_EXTERNAL_TOOLS", "")


def flag(name: str) -> bool:
    return os.environ.get(name) == "1"


def first_install_or(flag_name: str) -> bool:
    return (not is_upgrade) or flag(flag_name)


rag_embedding = {
    "mode": embedding_mode,
    "provider": embedding_mode,
    "providerId": "local" if embedding_mode == "local" else os.environ["NOVA_INSTALL_RAG_CLOUD_PROVIDER"],
    "model": os.environ["NOVA_INSTALL_RAG_CLOUD_MODEL"] if embedding_mode == "cloud" else os.environ["NOVA_INSTALL_RAG_LOCAL_MODEL"],
    "device": "auto",
}
if embedding_mode == "cloud":
    dimension = os.environ["NOVA_INSTALL_RAG_CLOUD_DIMENSION"].strip()
    if dimension:
        rag_embedding["dimension"] = int(dimension)
    rag_embedding["endpoint"] = os.environ["NOVA_INSTALL_RAG_CLOUD_ENDPOINT"]
    rag_embedding["apiKeyEnv"] = os.environ["NOVA_INSTALL_RAG_CLOUD_API_KEY_ENV"]
else:
    dimension = os.environ["NOVA_INSTALL_RAG_LOCAL_DIMENSION"].strip()
    if dimension:
        rag_embedding["dimension"] = int(dimension)

update = {
    "general": {
        "locale": os.environ["NOVA_INSTALL_LANGUAGE"],
        "workspaceRoot": str(deploy_source_root),
        "tmpWorkspace": str(runtime / "state" / "tmp"),
    },
    "paths": {
        "install": {
            "workspace": str(deploy_source_root),
            "dashboardApp": str(deploy_source_root / "src" / "dashboard"),
        },
        "runtime": {
            "novaHome": str(runtime),
            "database": str(runtime / "data" / "nova_data.sqlite3"),
        },
        "diary": {},
        "intermediate": {},
        "tasks": {
            "taskBoard": str(runtime / "artifacts" / "tasks" / "TASK_BOARD.md"),
            "legacyTaskDatabase": str(runtime / "data" / "nova_tasks.db"),
        },
        "logsCacheTmp": {
            "logs": str(runtime / "state" / "logs"),
            "cache": str(runtime / "state" / "cache"),
            "tmp": str(runtime / "state" / "tmp"),
            "backups": str(runtime / "state" / "backups"),
        },
    },
    "features": {},
    "dashboard": {
        "projectRoot": str(deploy_source_root),
        "pythonExecutable": str(runtime / ".venv" / "bin" / "python"),
        "appDir": str(deploy_source_root / "src" / "dashboard"),
    },
    "pipeline": {
        "pythonExecutable": str(runtime / ".venv" / "bin" / "python"),
        "workingDirectory": str(deploy_source_root),
    },
}

if first_install_or("NOVA_INSTALL_LANGUAGE_SET"):
    update["general"]["locale"] = os.environ["NOVA_INSTALL_LANGUAGE"]
    update["pipeline"].update(
        {
            "languageProfile": os.environ["NOVA_INSTALL_PIPELINE_LANGUAGE_PROFILE"],
            "englishEnabled": os.environ["NOVA_INSTALL_PIPELINE_ENGLISH_ENABLED"] == "1",
            "diarySchemaVersion": os.environ["NOVA_INSTALL_PIPELINE_DIARY_SCHEMA_VERSION"],
            "promptPayloadProfile": os.environ["NOVA_INSTALL_PIPELINE_PROMPT_PAYLOAD_PROFILE"],
        }
    )
    update.setdefault("rag", {})["languageProfile"] = os.environ["NOVA_INSTALL_RAG_LANGUAGE_PROFILE"]

if first_install_or("NOVA_INSTALL_SNAPSHOTS_OUTPUT_SET"):
    update["paths"]["runtime"]["snapshots"] = os.environ["NOVA_INSTALL_SNAPSHOTS_OUTPUT"]
if first_install_or("NOVA_INSTALL_DIARY_OUTPUT_SET"):
    update["paths"]["diary"]["generatedDiary"] = os.environ["NOVA_INSTALL_DIARY_OUTPUT"]
    update["paths"]["diary"]["legacyDiaryRoot"] = os.environ["NOVA_INSTALL_DIARY_OUTPUT"]
if first_install_or("NOVA_INSTALL_REPORTS_OUTPUT_SET"):
    update["paths"]["diary"]["reports"] = os.environ["NOVA_INSTALL_REPORTS_OUTPUT"]
if first_install_or("NOVA_INSTALL_ARCHIVES_OUTPUT_SET"):
    update["paths"]["intermediate"]["archives"] = os.environ["NOVA_INSTALL_ARCHIVES_OUTPUT"]

if first_install_or("NOVA_INSTALL_DASHBOARD_HOST_SET"):
    update["dashboard"]["host"] = os.environ["NOVA_INSTALL_DASHBOARD_HOST"]
if first_install_or("NOVA_INSTALL_DASHBOARD_PORT_SET"):
    update["dashboard"]["port"] = int(os.environ["NOVA_INSTALL_DASHBOARD_PORT"])
if first_install_or("NOVA_INSTALL_DASHBOARD_SERVER_SET"):
    update["dashboard"]["server"] = {
        "enabled": os.environ["NOVA_INSTALL_ENABLE_DASHBOARD_SERVER"] == "1",
    }

if not is_upgrade:
    update["features"].update(
        {
            "dashboard": os.environ["NOVA_INSTALL_ENABLE_DASHBOARD"] == "1",
            "novaTask": os.environ["NOVA_INSTALL_ENABLE_NOVA_TASK"] == "1",
            "taskAuditSink": os.environ["NOVA_INSTALL_ENABLE_NOVA_TASK"] == "1",
        }
    )
if first_install_or("NOVA_INSTALL_RAG_SET"):
    update["features"].update(
        {
            "rag": enable_rag,
            "embeddingServer": enable_rag,
        }
    )
    update.setdefault("rag", {}).update(
        {
            "enabled": enable_rag,
            "mode": "v2" if enable_rag else "disabled",
            "embedding": rag_embedding,
            "server": {
                "enabled": enable_rag,
            },
        }
    )
if first_install_or("NOVA_INSTALL_LLM_SET"):
    update["features"]["llmGeneration"] = enable_llm

for group_name in ("features",):
    if not update[group_name]:
        update.pop(group_name, None)
for parent, child in (("paths", "diary"), ("paths", "intermediate")):
    if not update[parent][child]:
        update[parent].pop(child, None)

selected_external_tools = []
external_tools_update = {}
for raw_item in [item for item in selected_external_tools_raw.split(";;") if item.strip()]:
    try:
        key, name, path_value = raw_item.split("|", 2)
    except ValueError:
        continue
    item = {"key": key, "name": name, "path": path_value}
    selected_external_tools.append(item)
    if key == "openclaw":
        root = Path(path_value)
        external_tools_update["openclaw"] = {
            "home": str(root),
            "agentsRoot": str(root / "agents"),
            "configPath": str(root / "config.json"),
            "credentialsPath": str(root / "credentials.json"),
            "workspaceRoot": str(root / "workspace"),
            "workspaceCoderRoot": str(root / "workspace-coder"),
            "projectsRoot": str(root / "workspace" / "PROJECTS"),
            "skillsRoot": str(root / "workspace" / "skills"),
            "systemSkillsRoot": str(root / "skills"),
            "memoryRoot": str(root / "memory"),
            "cronJobsPath": str(root / "cron" / "jobs.json"),
            "cronJobsMigratedPath": str(root / "cron" / "jobs.json.migrated"),
            "cronRunsRoot": str(root / "cron" / "runs"),
            "infrastructurePath": str(root / "workspace" / "infrastructure.md"),
            "toolConfigSnapshotPath": str(root / "workspace" / ".dashboard-tool-configs.json"),
        }
    elif key == "claudeCode":
        root = Path(path_value)
        external_tools_update["claudeCode"] = {
            "home": str(root),
            "projectsRoot": str(root / "projects"),
            "skillsRoot": str(root / "skills"),
            "commandsRoot": str(root / "commands"),
            "pluginsRoot": str(root / "plugins"),
            "configPath": str(root / "settings.json"),
        }
    elif key == "codex":
        root = Path(path_value)
        external_tools_update["codex"] = {
            "home": str(root),
            "sessionsRoot": str(root / "sessions"),
            "skillsRoot": str(root / "skills"),
            "configPath": str(root / "config.toml"),
        }
    elif key == "geminiCli":
        root = Path(path_value)
        external_tools_update["geminiCli"] = {
            "home": str(root),
            "chatsRoot": str(root / "tmp" / "ssd" / "chats"),
            "projectsPath": str(root / "projects.json"),
            "skillsRoot": str(root / "skills"),
            "configPath": str(root / "settings.json"),
        }
    elif key == "hermes":
        root = Path(path_value)
        external_tools_update["hermes"] = {
            "home": str(root),
            "stateDbPath": str(root / "state.db"),
            "skillsRoot": str(root / "hermes-agent" / "skills"),
            "optionalSkillsRoot": str(root / "hermes-agent" / "optional-skills"),
            "pluginsRoot": str(root / "hermes-agent" / "plugins"),
            "profilesRoot": str(root / "profiles"),
            "configPath": str(root / "config.yaml"),
        }

if selected_external_tools:
    external_tools_update["installerSelectedTools"] = selected_external_tools

if enable_rag and enable_skill_registration and selected_external_tools:
    external_tools_update["installerV2SkillRegistration"] = {
        "status": "installer-applied",
        "supportedNow": True,
        "purpose": "RAG辅助记忆系统",
        "scope": "Installer apply and Dashboard-managed registration of the Open Nova nova-RAG skill into selected tools' global layer",
        "selectedTools": selected_external_tools,
        "dryRunEndpoint": "GET /api/settings/external-tools/rag-skill-registration/plan",
        "applyEndpoint": "POST /api/settings/external-tools/rag-skill-registration",
        "confirmationTextRequired": "INSTALL OPEN NOVA RAG SKILL",
        "mutationPolicy": "installer writes missing skill files after final install confirmation; exact unmodified generated versions are backed up and upgraded; customized files are preserved unless Dashboard overwrite is explicitly confirmed",
    }

if external_tools_update:
    update["externalTools"] = external_tools_update

if first_install_or("NOVA_INSTALL_LLM_SET") and enable_llm:
    if llm_provider_mode == "preset":
        update["llmProvider"] = {
            "mode": "preset",
            "provider": os.environ["NOVA_INSTALL_LLM_PROVIDER"],
            "presetProvider": os.environ["NOVA_INSTALL_LLM_PROVIDER"],
            "endpoint": os.environ["NOVA_INSTALL_LLM_ENDPOINT"],
            "model": os.environ["NOVA_INSTALL_LLM_MODEL"],
            "api": os.environ["NOVA_INSTALL_LLM_API"],
            "apiKey": "",
            "apiKeyEnv": os.environ["NOVA_INSTALL_LLM_API_KEY_ENV"],
        }
    else:
        update["llmProvider"] = {
            "mode": "custom",
            "provider": "custom",
            "presetProvider": "",
            "endpoint": os.environ["NOVA_INSTALL_LLM_ENDPOINT"],
            "model": os.environ["NOVA_INSTALL_LLM_MODEL"],
            "api": os.environ["NOVA_INSTALL_LLM_API"],
            "apiKey": "",
            "apiKeyEnv": os.environ["NOVA_INSTALL_LLM_API_KEY_ENV"],
        }

write_settings(update, paths)
PY
  } >> "$log_file" 2>&1; then
    progress_fail "Applying runtime settings failed; see ${log_file}"
    return 1
  fi
  progress_ok "Applying runtime settings"
}

store_installer_llm_api_key_secret() {
  local label="Storing LLM API key in secret store"
  local log_file=""
  if [[ "$ENABLE_LLM_GENERATION" != "1" || -z "$LLM_API_KEY_VALUE" ]]; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "$label"
    progress_ok "$label"
    return 0
  fi
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  progress_start "$label"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}: open-nova model key --value-stdin --runtime ${RUNTIME_HOME}" >> "$log_file"
  if ! print -r -- "$LLM_API_KEY_VALUE" | "${VENV_PY}" -m data_foundation.cli \
    model key \
    --value-stdin \
    --runtime "${RUNTIME_HOME}" \
    --json >> "$log_file" 2>&1; then
    progress_fail "${label} failed; see ${log_file}"
    return 1
  fi
  LLM_API_KEY_VALUE=""
  progress_ok "$label"
}

create_desktop_diary_link() {
  if [[ "$CREATE_DESKTOP_DIARY_LINK" != "1" ]]; then
    log "Desktop diary shortcut skipped"
    return 0
  fi
  log "Creating Desktop diary shortcut"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Creating Desktop diary shortcut"
    progress_ok "Creating Desktop diary shortcut"
    return 0
  fi
  if ! mkdir -p "${DESKTOP_DIARY_LINK:h}"; then
    warn "Desktop diary shortcut parent could not be created; continuing without Desktop shortcut: ${DESKTOP_DIARY_LINK:h}"
    return 0
  fi
  if [[ -L "$DESKTOP_DIARY_LINK" ]]; then
    local current_target=""
    current_target="$(readlink "$DESKTOP_DIARY_LINK" || true)"
    if [[ "$current_target" == "$DIARY_OUTPUT_DIR" ]]; then
      return 0
    fi
    warn "Desktop diary shortcut already exists and points elsewhere: ${DESKTOP_DIARY_LINK}"
    return 0
  fi
  if [[ -e "$DESKTOP_DIARY_LINK" ]]; then
    warn "Desktop diary shortcut path already exists; leaving it unchanged: ${DESKTOP_DIARY_LINK}"
    return 0
  fi
  if ! ln -s "$DIARY_OUTPUT_DIR" "$DESKTOP_DIARY_LINK"; then
    warn "Desktop diary shortcut could not be created; continuing without Desktop shortcut: ${DESKTOP_DIARY_LINK}"
    return 0
  fi
}

create_cli_shim() {
  log "Creating open-nova CLI shim"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Creating open-nova CLI shim"
    progress_ok "Creating open-nova CLI shim"
    return 0
  fi
  mkdir -p "${CLI_SHIM:h}"
  local shim_tmp="${CLI_SHIM}.tmp.$$"
  if ! cat > "${shim_tmp}" <<EOF
#!/usr/bin/env zsh
set -euo pipefail
export NOVA_HOME="${RUNTIME_HOME}"
export NOVA_LOCATION_FILE="${LOCATION_FILE}"
export PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src"
export PYTHONDONTWRITEBYTECODE="1"
unset WORKSPACE_DIR DIARY_OUTPUT_DIR TMP_WORKSPACE NOVA_DATA_DB_PATH NOVA_DATA_EXPORT_DIR TASK_DB_PATH
exec "${VENV_PY}" -m data_foundation.cli "\$@"
EOF
  then
    rm -f "${shim_tmp}"
    error "Open Nova CLI shim could not be staged: ${CLI_SHIM}"
    return 1
  fi
  if ! chmod +x "${shim_tmp}" || ! mv -f "${shim_tmp}" "${CLI_SHIM}"; then
    rm -f "${shim_tmp}"
    error "Open Nova CLI shim could not be installed atomically: ${CLI_SHIM}"
    return 1
  fi
  if mkdir -p "${USER_CLI_SHIM:h}" 2>/dev/null; then
    if ! ln -sf "${CLI_SHIM}" "${USER_CLI_SHIM}" 2>/dev/null; then
      warn "User PATH shim could not be linked; runtime CLI remains available at ${CLI_SHIM}"
    fi
  else
    warn "User PATH shim directory could not be created; runtime CLI remains available at ${CLI_SHIM}"
  fi
}

resolve_shell_path_file() {
  if [[ -n "$SHELL_PATH_FILE" ]]; then
    print -r -- "${SHELL_PATH_FILE:A}"
    return 0
  fi
  local shell_name="${SHELL:t}"
  if [[ "$PLATFORM" == "Darwin" || "$shell_name" == "zsh" ]]; then
    print -r -- "${HOME}/.zprofile"
  elif [[ "$shell_name" == "bash" ]]; then
    print -r -- "${HOME}/.bash_profile"
  else
    print -r -- "${HOME}/.profile"
  fi
}

ensure_cli_on_shell_path() {
  if [[ "$ENABLE_SHELL_PATH" != "1" ]]; then
    log "Shell PATH update skipped by --no-shell-path"
    return 0
  fi
  local shim_dir="${USER_CLI_SHIM:h}"
  local profile_path
  local path_expr
  local marker_start="# >>> open-nova installer PATH >>>"
  local marker_end="# <<< open-nova installer PATH <<<"
  profile_path="$(resolve_shell_path_file)"
  if [[ "$shim_dir" == "${HOME}/.local/bin" ]]; then
    path_expr="\$HOME/.local/bin"
  else
    path_expr="${shim_dir}"
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Updating shell PATH"
    progress_ok "Updating shell PATH"
    return 0
  fi
  if [[ -f "$profile_path" ]] && grep -Fq "$marker_start" "$profile_path"; then
    return 0
  fi
  if [[ ":${PATH}:" == *":${shim_dir}:"* ]]; then
    return 0
  fi
  if ! mkdir -p "${profile_path:h}" 2>/dev/null; then
    warn "Shell PATH profile directory could not be created; add ${shim_dir} to PATH manually."
    return 0
  fi
  if ! {
    print -r -- ""
    print -r -- "$marker_start"
    print -r -- "# Added by Open Nova installer so the open-nova CLI resolves in new shells."
    print -r -- "export PATH=\"${path_expr}:\$PATH\""
    print -r -- "$marker_end"
  } >> "$profile_path" 2>/dev/null; then
    warn "Shell PATH profile could not be updated; add ${shim_dir} to PATH manually."
    return 0
  fi
  log "Added ${shim_dir} to shell PATH via ${profile_path}"
}

export_runtime_environment() {
export NOVA_HOME="${RUNTIME_HOME}"
export NOVA_LOCATION_FILE="${LOCATION_FILE}"
export PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src:${DEPLOY_SOURCE_ROOT}/src/dashboard"
}

stage_runtime_source() {
  log "Staging source snapshot for runtime"
  STAGED_RELEASE_ID=""
  STAGED_RELEASE_TARGET=""
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Staging source snapshot"
    progress_ok "Staging source snapshot"
    return 0
  fi
  local app_root="${DEPLOY_SOURCE_ROOT:h}"
  local releases_root="${app_root}/releases"
  local release_id="${UPDATE_TRANSACTION_ID:-$(date +%Y%m%dT%H%M%S)-$$-${RANDOM}}"
  local release_tmp="${releases_root}/.tmp-${release_id}"
  local release_target="${releases_root}/${release_id}"
  local transaction_owned_stage=0
  local stage_command=("${PYTHON_BIN}" "-")
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" && -n "$UPDATE_TRANSACTION_JOURNAL" ]]; then
    release_tmp="$(update_transaction_command reserve-artifact \
      --state "${UPDATE_TRANSACTION_JOURNAL}" \
      --kind source-temp)"
    if [[ "$release_tmp" != "${releases_root}/.tmp-${release_id}" ]]; then
      print -r -- "transaction source reservation returned an unexpected path" >&2
      return 1
    fi
    transaction_owned_stage=1
    stage_command=(
      "${PYTHON_BIN}" "${UPDATE_TRANSACTION_HELPER}"
      run-candidate-command
      --state "${UPDATE_TRANSACTION_JOURNAL}"
      --phase candidate-source-stage
      --
      /usr/bin/env
      -i
      "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
      "USER=open-nova-candidate"
      "LOGNAME=open-nova-candidate"
      "SHELL=/bin/zsh"
      "LC_ALL=C"
      "LANG=C"
      "NOVA_HOME=${UPDATE_VALIDATION_RUNTIME}"
      "NOVA_LOCATION_FILE=${UPDATE_VALIDATION_RUNTIME}/location.json"
      "HOME=${UPDATE_VALIDATION_RUNTIME}/home"
      "TMPDIR=${UPDATE_VALIDATION_RUNTIME}/tmp"
      "XDG_CONFIG_HOME=${UPDATE_VALIDATION_RUNTIME}/xdg"
      "PIP_CONFIG_FILE=/dev/null"
      "PIP_CACHE_DIR=${UPDATE_VALIDATION_RUNTIME}/pip-cache"
      "PYTHONNOUSERSITE=1"
      "OPEN_NOVA_SECRET_BACKEND=memory"
      "PYTHONDONTWRITEBYTECODE=1"
      "${PYTHON_BIN}" "-"
    )
  fi
  mkdir -p "${releases_root}"
  if [[ "$transaction_owned_stage" != "1" ]]; then
    rm -rf "${release_tmp}"
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 "${stage_command[@]}" "${SOURCE_ROOT}" "${release_tmp}" "${DEPLOY_SOURCE_ROOT}" "${release_target}" "${transaction_owned_stage}" <<'PY'
import json
import hashlib
import os
import pwd
import re
import shutil
import subprocess
import sys
import tomllib
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

source = Path(sys.argv[1])
target = Path(sys.argv[2])
deploy_target = Path(sys.argv[3])
release_target = Path(sys.argv[4])
precreated = sys.argv[5] == "1"

allowed_top_level = {
    "advanced",
    "config.py",
    "install",
    "LICENSE",
    "MANIFEST.in",
    "pyproject.toml",
    "src",
}
excluded_names = {
    ".DS_Store",
    ".env",
    ".git",
    ".mypy_cache",
    ".playwright-cli",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "cache",
    "data",
    "dist",
    "htmlcov",
    "location.json",
    "logs",
    "runtime.json",
    "reserved",
    "settings.json",
    "snapshots",
    "state",
    "tmp",
    "venv",
    "wheelhouse",
}
excluded_suffixes = (
    ".db",
    ".egg-info",
    ".log",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
)


def ignore(directory, names):
    ignored = set()
    for name in names:
        if name in excluded_names or name.startswith(".env."):
            ignored.add(name)
            continue
        if any(name.endswith(suffix) for suffix in excluded_suffixes):
            ignored.add(name)
    return ignored


def split_sql_statements(body, version):
    statements = []
    pending = []
    state = "normal"
    index = 0
    while index < len(body):
        character = body[index]
        following = body[index + 1] if index + 1 < len(body) else ""
        if state == "line-comment":
            if character in "\r\n":
                pending.append("\n")
                state = "normal"
            index += 1
            continue
        if state == "block-comment":
            if character == "*" and following == "/":
                pending.append(" ")
                state = "normal"
                index += 2
            else:
                index += 1
            continue
        if state == "normal":
            if character == "-" and following == "-":
                state = "line-comment"
                index += 2
                continue
            if character == "/" and following == "*":
                state = "block-comment"
                index += 2
                continue
            if character in {"'", '"', "`", "["}:
                state = {"'": "single", '"': "double", "`": "backtick", "[": "bracket"}[
                    character
                ]
                pending.append(character)
                index += 1
                continue
            if character == ";":
                text = "".join(pending).strip()
                if text:
                    statements.append(text)
                pending = []
                index += 1
                continue
            pending.append(character)
            index += 1
            continue
        closing = {"single": "'", "double": '"', "backtick": "`", "bracket": "]"}[state]
        pending.append(character)
        if character == closing:
            if following == closing and state != "bracket":
                pending.append(following)
                index += 2
                continue
            state = "normal"
        index += 1
    if state not in {"normal", "line-comment"}:
        raise SystemExit(f"candidate additive migration has unterminated SQL syntax: {version}")
    text = "".join(pending).strip()
    if text:
        statements.append(text)
    return statements


if precreated:
    marker = target / ".open-nova-update-owner"
    if target.is_symlink() or not target.is_dir() or marker.is_symlink() or not marker.is_file():
        raise SystemExit("transaction source reservation is missing or unsafe")
    if any(path != marker for path in target.iterdir()):
        raise SystemExit("transaction source reservation was modified before staging")
else:
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
for name in sorted(allowed_top_level):
    source_path = source / name
    target_path = target / name
    if not source_path.exists():
        continue
    if source_path.is_dir():
        shutil.copytree(source_path, target_path, ignore=ignore, symlinks=True)
    else:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, target_path, follow_symlinks=False)
if any(path.is_symlink() for path in target.rglob("*")):
    raise SystemExit("candidate runtime source payload must not contain symlinks")

def privacy_safe_source_locator(source_path):
    try:
        login_home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
        resolved_source = source_path.resolve()
        relative = resolved_source.relative_to(login_home)
    except (KeyError, OSError, RuntimeError, ValueError):
        return {"kind": "unavailable", "issue": "outside-login-home"}
    components = list(relative.parts)
    if not components or any(not item or item in {".", ".."} or "/" in item or "\\" in item for item in components):
        return {"kind": "unavailable", "issue": "invalid-relative-components"}
    return {"kind": "login-home-relative", "pathComponents": components}

manifest = {
    "schemaVersion": 2,
    "product": "open-nova",
    "sourceLocator": privacy_safe_source_locator(source),
    "deployedSourceLocator": {"kind": "runtime-relative", "pathComponents": ["app", "source"]},
    "releaseLocator": {"kind": "runtime-relative", "pathComponents": ["app", "releases", release_target.name]},
    "deploymentMode": "release-symlink",
    "copiedAt": datetime.now().astimezone().isoformat(),
    "pyprojectVersion": None,
    "git": {
        "available": False,
        "commit": None,
        "branch": None,
        "remote": None,
        "dirty": None,
    },
}

contract_path = target / "src" / "data_foundation" / "migration_compatibility.json"
migrations_root = target / "src" / "data_foundation" / "migrations"
try:
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit("candidate migration compatibility contract is missing or invalid") from exc
records = contract.get("migrations") if isinstance(contract.get("migrations"), list) else []
if (
    contract.get("schemaVersion") != 1
    or contract.get("policy") != "rollback-compatible-additive-only"
    or contract.get("preCommitWriterContract") != "prior-reader-compatible-v1"
    or contract.get("minimumReadableSchema") != "unversioned"
    or not records
):
    raise SystemExit("candidate migration compatibility contract has an unsupported policy")
normalized_records = []
seen_versions = set()
migration_set_digest = hashlib.sha256()
for record in records:
    if not isinstance(record, dict):
        raise SystemExit("candidate migration compatibility record is invalid")
    version = str(record.get("version") or "")
    expected_hash = str(record.get("sha256") or "")
    rollback_class = str(record.get("rollbackClass") or "")
    if (
        not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", version)
        or version in seen_versions
        or not re.fullmatch(r"[0-9a-f]{64}", expected_hash)
        or rollback_class not in {"rollback-compatible-additive", "breaking"}
    ):
        raise SystemExit("candidate migration compatibility record is unsafe")
    migration_path = migrations_root / f"{version}.sql"
    if not migration_path.is_file() or migration_path.is_symlink():
        raise SystemExit(f"candidate migration is missing from its compatibility contract: {version}")
    actual_hash = hashlib.sha256(migration_path.read_bytes()).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(f"candidate migration body changed without a new compatibility version: {version}")
    if rollback_class == "rollback-compatible-additive":
        try:
            body = migration_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SystemExit(f"candidate additive migration is unreadable: {version}") from exc
        statements = split_sql_statements(body, version)
        if not statements:
            raise SystemExit(f"candidate additive migration is empty: {version}")
        for statement in statements:
            create_allowed = re.match(
                r"(?is)^\s*CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX|VIEW)\b",
                statement,
            )
            alter_allowed = re.match(
                r"(?is)^\s*ALTER\s+TABLE\s+(?:\S+)\s+ADD\s+(?:COLUMN\s+)?\S+",
                statement,
            )
            if not create_allowed and not alter_allowed:
                raise SystemExit(
                    f"candidate additive migration contains a prior-reader-unsafe statement: {version}"
                )
    seen_versions.add(version)
    normalized_records.append(
        {"version": version, "sha256": expected_hash, "rollbackClass": rollback_class}
    )
    migration_set_digest.update(version.encode("ascii"))
    migration_set_digest.update(b"\0")
    migration_set_digest.update(expected_hash.encode("ascii"))
    migration_set_digest.update(b"\0")
    migration_set_digest.update(rollback_class.encode("ascii"))
    migration_set_digest.update(b"\n")
migration_entries = list(migrations_root.glob("*.sql"))
if any(not path.is_file() or path.is_symlink() for path in migration_entries):
    raise SystemExit("candidate migration inventory contains an unsafe entry")
actual_versions = {path.stem for path in migration_entries}
if actual_versions != seen_versions or [item["version"] for item in normalized_records] != sorted(seen_versions):
    raise SystemExit("candidate migration set does not exactly match its compatibility contract")
if contract.get("maximumReadableSchema") != normalized_records[-1]["version"]:
    raise SystemExit("candidate migration readable-schema bound does not match its migration set")
manifest["databaseCompatibility"] = {
    "schemaVersion": 1,
    "policy": contract["policy"],
    "preCommitWriterContract": contract["preCommitWriterContract"],
    "minimumReadableSchema": contract["minimumReadableSchema"],
    "maximumReadableSchema": contract["maximumReadableSchema"],
    "migrationSetSha256": migration_set_digest.hexdigest(),
    "migrations": normalized_records,
}
try:
    pyproject = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    manifest["pyprojectVersion"] = (pyproject.get("project") or {}).get("version")
except Exception:
    pass

def git_value(*args):
    return subprocess.check_output(("git", "-C", str(source), *args), text=True, stderr=subprocess.DEVNULL).strip()

def git_optional(*args):
    try:
        value = git_value(*args)
    except Exception:
        return None
    return value or None

def redact_git_remote(value):
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except Exception:
        return None
    if parsed.scheme == "file" or (not parsed.scheme and value.startswith(("/", "~"))):
        return None
    if parsed.scheme in {"https", "ssh"} and parsed.netloc:
        netloc = parsed.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    scp_remote = re.fullmatch(r"(?:[^@/\s]+@)?([^:/\s]+):(.+)", value)
    if scp_remote:
        host, remote_path = scp_remote.groups()
        return f"ssh://{host}/{remote_path.lstrip('/')}"
    return None

try:
    remote = git_optional("config", "--get", "remote.origin.url")
    if remote is None:
        remote_names = git_optional("remote")
        first_remote = (remote_names or "").splitlines()[0] if remote_names else ""
        if first_remote:
            remote = git_optional("remote", "get-url", first_remote)
    manifest["git"].update(
        {
            "available": True,
            "commit": git_value("rev-parse", "HEAD"),
            "branch": git_value("rev-parse", "--abbrev-ref", "HEAD"),
            "remote": redact_git_remote(remote),
            "dirty": bool(git_value("status", "--porcelain")),
        }
    )
except Exception:
    pass
(target / ".open-nova-runtime-source.json").write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)

# Scan the exact copied payload, not the checkout that was used as its source.
# Findings never include candidate values; only path/kind metadata may leave the
# scanner, and a blocked payload is discarded before any source pointer switch.
sys.path.insert(0, str(target / "src"))
from data_foundation.release_clean import repository_clean_deployment_check

clean_result = repository_clean_deployment_check(target)
if clean_result.get("status") != "passed":
    print("staged runtime source payload failed release-clean validation", file=sys.stderr)
    raise SystemExit(9)

payload_files = []
payload_digest = hashlib.sha256()
manifest_path = target / ".open-nova-runtime-source.json"
for payload_path in sorted(target.rglob("*")):
    if (
        payload_path == manifest_path
        or payload_path.name == ".open-nova-update-owner"
        or not (payload_path.is_file() or payload_path.is_symlink())
    ):
        continue
    relative = payload_path.relative_to(target).as_posix()
    if payload_path.is_symlink():
        raise SystemExit("candidate runtime source payload must not contain symlinks")
    content = payload_path.read_bytes()
    size = len(content)
    file_hash = hashlib.sha256(content).hexdigest()
    payload_files.append({"path": relative, "sha256": file_hash, "size": size})
    payload_digest.update(relative.encode("utf-8"))
    payload_digest.update(b"\0")
    payload_digest.update(file_hash.encode("ascii"))
    payload_digest.update(b"\n")

manifest["payload"] = {
    "fileCount": len(payload_files),
    "files": payload_files,
    "sha256": payload_digest.hexdigest(),
}
manifest["cleanScan"] = {
    "status": "passed",
    "scanner": "data_foundation.release_clean.repository_clean_deployment_check",
    "scannedFiles": int(clean_result.get("scannedFiles") or 0),
    "findingCount": 0,
}
manifest_path.write_text(
    json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
  then
    if [[ "$transaction_owned_stage" != "1" ]]; then
      rm -rf "${release_tmp}"
    fi
    print -r -- "runtime source staging failed before source pointer switch" >&2
    return 1
  fi
  if [[ ! -f "${release_tmp}/.open-nova-runtime-source.json" || ! -f "${release_tmp}/pyproject.toml" ]]; then
    if [[ "$transaction_owned_stage" != "1" ]]; then
      rm -rf "${release_tmp}"
    fi
    print -r -- "runtime source release failed validation before switch" >&2
    return 1
  fi
  if ! PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" - "${release_tmp}/.open-nova-runtime-source.json" "${release_id}" <<'PY'
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

manifest_path = Path(sys.argv[1])
expected_release_id = sys.argv[2]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
expected_fields = {
    "schemaVersion", "product", "sourceLocator", "deployedSourceLocator", "releaseLocator",
    "deploymentMode", "copiedAt", "pyprojectVersion", "git", "databaseCompatibility",
    "payload", "cleanScan",
}
if type(manifest.get("schemaVersion")) is not int or manifest.get("schemaVersion") != 2:
    raise SystemExit("staged source manifest has an unsupported privacy schema")
if set(manifest) != expected_fields:
    raise SystemExit("staged source manifest has an invalid exact schema")
if manifest.get("product") != "open-nova" or manifest.get("deploymentMode") != "release-symlink":
    raise SystemExit("staged source manifest has invalid release semantics")
try:
    datetime.fromisoformat(manifest.get("copiedAt"))
except (TypeError, ValueError) as exc:
    raise SystemExit("staged source manifest has an invalid copied timestamp") from exc
version = manifest.get("pyprojectVersion")
if version is not None and (
    not isinstance(version, str)
    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}", version)
):
    raise SystemExit("staged source manifest has an invalid project version")
source_locator = manifest.get("sourceLocator") if isinstance(manifest.get("sourceLocator"), dict) else {}
source_kind = source_locator.get("kind")
source_components = source_locator.get("pathComponents")
if source_kind == "login-home-relative":
    source_valid = set(source_locator) == {"kind", "pathComponents"} and isinstance(source_components, list) and bool(source_components) and all(
        isinstance(item, str)
        and item
        and item not in {".", ".."}
        and "/" not in item
        and "\\" not in item
        and "\0" not in item
        for item in source_components
    )
elif source_kind == "unavailable":
    source_valid = set(source_locator) == {"kind", "issue"} and source_locator.get("issue") in {"outside-login-home", "invalid-relative-components"}
else:
    source_valid = False
if not source_valid:
    raise SystemExit("staged source manifest has an invalid source locator")
for field in ("deployedSourceLocator", "releaseLocator"):
    locator = manifest.get(field) if isinstance(manifest.get(field), dict) else {}
    components = locator.get("pathComponents")
    if (
        set(locator) != {"kind", "pathComponents"}
        or
        locator.get("kind") != "runtime-relative"
        or not isinstance(components, list)
        or not components
        or not all(
            isinstance(item, str)
            and item
            and item not in {".", ".."}
            and "/" not in item
            and "\\" not in item
            and "\0" not in item
            for item in components
        )
    ):
        raise SystemExit("staged source manifest has an invalid runtime locator")
if manifest["deployedSourceLocator"]["pathComponents"] != ["app", "source"]:
    raise SystemExit("staged source manifest has an invalid deployed source locator")
if manifest["releaseLocator"]["pathComponents"] != ["app", "releases", expected_release_id]:
    raise SystemExit("staged source manifest release locator does not match its candidate")
git = manifest.get("git")
if not isinstance(git, dict) or set(git) != {"available", "commit", "branch", "remote", "dirty"}:
    raise SystemExit("staged source manifest has invalid git provenance")
if type(git.get("available")) is not bool:
    raise SystemExit("staged source manifest has invalid git provenance")
if git.get("dirty") is not None and type(git.get("dirty")) is not bool:
    raise SystemExit("staged source manifest has invalid git provenance")
commit = git.get("commit")
if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit)):
    raise SystemExit("staged source manifest has invalid git provenance")
branch = git.get("branch")
if branch is not None and (
    not isinstance(branch, str)
    or not branch
    or branch.startswith(("/", "~/", "file:"))
    or "/Users/" in branch
    or any(character in branch for character in "\0\r\n")
):
    raise SystemExit("staged source manifest has invalid git provenance")
remote = git.get("remote")
if remote is not None:
    if not isinstance(remote, str):
        raise SystemExit("staged source manifest has an unsafe git remote")
    try:
        parsed = urlsplit(remote)
    except (TypeError, ValueError) as exc:
        raise SystemExit("staged source manifest has an unsafe git remote") from exc
    if (
        parsed.scheme not in {"https", "ssh"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("staged source manifest has an unsafe git remote")
clean = manifest.get("cleanScan") if isinstance(manifest.get("cleanScan"), dict) else {}
payload = manifest.get("payload") if isinstance(manifest.get("payload"), dict) else {}
if set(clean) != {"status", "scanner", "scannedFiles", "findingCount"}:
    raise SystemExit("staged source manifest has invalid clean-scan evidence")
if set(payload) != {"fileCount", "files", "sha256"}:
    raise SystemExit("staged source manifest has invalid payload evidence")
if clean.get("status") != "passed" or clean.get("findingCount") != 0:
    raise SystemExit("staged source manifest has no passing clean scan")
if (
    clean.get("scanner") != "data_foundation.release_clean.repository_clean_deployment_check"
    or type(clean.get("scannedFiles")) is not int
    or clean.get("scannedFiles") < 0
    or type(payload.get("fileCount")) is not int
    or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256") or ""))
):
    raise SystemExit("staged source manifest has invalid clean-scan evidence")
if not payload.get("files") or payload.get("fileCount") != len(payload["files"]):
    raise SystemExit("staged source manifest has no complete payload inventory")
expected_paths = set()
aggregate = hashlib.sha256()
for record in payload["files"]:
    if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
        raise SystemExit("staged source payload inventory contains an invalid record")
    relative_text = record.get("path")
    relative = Path(relative_text) if isinstance(relative_text, str) else Path()
    if (
        not relative_text
        or "\0" in relative_text
        or relative_text.startswith(("~/", "file:"))
        or re.match(r"^[A-Za-z]:[\\/]", relative_text)
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
        or relative.as_posix() in expected_paths
    ):
        raise SystemExit("staged source payload inventory contains an unsafe or duplicate path")
    candidate_file = manifest_path.parent / relative
    if candidate_file.is_symlink() or not candidate_file.is_file():
        raise SystemExit("staged source payload file is missing or unsafe")
    content = candidate_file.read_bytes()
    actual_hash = hashlib.sha256(content).hexdigest()
    if (
        actual_hash != record.get("sha256")
        or type(record.get("size")) is not int
        or len(content) != record.get("size")
    ):
        raise SystemExit("staged source payload changed after release-clean scan")
    normalized = relative.as_posix()
    expected_paths.add(normalized)
    aggregate.update(normalized.encode("utf-8"))
    aggregate.update(b"\0")
    aggregate.update(actual_hash.encode("ascii"))
    aggregate.update(b"\n")
candidate_entries = list(manifest_path.parent.rglob("*"))
if any(item.is_symlink() for item in candidate_entries):
    raise SystemExit("staged source payload contains a symlink after release-clean scan")
owner_marker = manifest_path.parent / ".open-nova-update-owner"
actual_paths = {
    item.relative_to(manifest_path.parent).as_posix()
    for item in candidate_entries
    if item not in {manifest_path, owner_marker} and item.is_file()
}
if actual_paths != expected_paths or aggregate.hexdigest() != payload.get("sha256"):
    raise SystemExit("staged source payload file set or aggregate changed after release-clean scan")
compatibility = manifest.get("databaseCompatibility") if isinstance(manifest.get("databaseCompatibility"), dict) else {}
compatibility_fields = {
    "schemaVersion", "policy", "preCommitWriterContract", "minimumReadableSchema",
    "maximumReadableSchema", "migrationSetSha256", "migrations",
}
if set(compatibility) != compatibility_fields:
    raise SystemExit("staged source manifest has invalid migration compatibility evidence")
if (
    type(compatibility.get("schemaVersion")) is not int
    or compatibility.get("schemaVersion") != 1
    or compatibility.get("policy") != "rollback-compatible-additive-only"
    or compatibility.get("preCommitWriterContract") != "prior-reader-compatible-v1"
    or compatibility.get("minimumReadableSchema") != "unversioned"
    or not compatibility.get("migrations")
):
    raise SystemExit("staged source manifest has no migration compatibility contract")
if not re.fullmatch(r"[0-9a-f]{64}", str(compatibility.get("migrationSetSha256") or "")):
    raise SystemExit("staged source manifest has invalid migration compatibility hash")
versions = []
for record in compatibility["migrations"]:
    if (
        not isinstance(record, dict)
        or set(record) != {"version", "sha256", "rollbackClass"}
        or not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", str(record.get("version") or ""))
        or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
        or record.get("rollbackClass") not in {"rollback-compatible-additive", "breaking"}
    ):
        raise SystemExit("staged source manifest has invalid migration compatibility record")
    versions.append(record["version"])
if len(set(versions)) != len(versions) or compatibility.get("maximumReadableSchema") != versions[-1]:
    raise SystemExit("staged source manifest has inconsistent migration compatibility bounds")
PY
  then
    if [[ "$transaction_owned_stage" != "1" ]]; then
      rm -rf "${release_tmp}"
    fi
    print -r -- "runtime source release failed clean-scan manifest validation before switch" >&2
    return 1
  fi
  if [[ "$transaction_owned_stage" == "1" ]]; then
    local promoted_source=""
    promoted_source="$(update_transaction_command promote-source-artifact \
      --state "${UPDATE_TRANSACTION_JOURNAL}")"
    if [[ "$promoted_source" != "$release_target" ]]; then
      print -r -- "transaction source promotion returned an unexpected path" >&2
      return 1
    fi
  else
    mv "${release_tmp}" "${release_target}"
  fi
  STAGED_RELEASE_ID="${release_id}"
  STAGED_RELEASE_TARGET="${release_target}"
}

promote_fresh_runtime_pointer() {
  local candidate="$1"
  local pointer="$2"
  local store_relative="$3"
  "${PYTHON_BIN}" - "${candidate}" "${pointer}" "${store_relative}" <<'PY'
import os
import sys
from pathlib import Path, PurePosixPath

candidate = Path(os.path.abspath(sys.argv[1]))
pointer = Path(os.path.abspath(sys.argv[2]))
store_relative = PurePosixPath(sys.argv[3])
store_parts = store_relative.parts
if (
    not store_parts
    or store_relative.is_absolute()
    or any(part in {"", ".", ".."} for part in store_parts)
):
    raise SystemExit("managed Runtime store path is unsafe")
expected_store = pointer.parent
for component in (None, *store_parts):
    if component is not None:
        expected_store = expected_store / component
    if expected_store.is_symlink() or not expected_store.is_dir():
        raise SystemExit("managed Runtime store is unavailable or unsafe")
if candidate.is_symlink() or not candidate.is_dir() or candidate.parent != expected_store:
    raise SystemExit("managed Runtime candidate is outside its store")
if os.path.lexists(pointer):
    raise SystemExit("managed Runtime pointer appeared concurrently; rerun with --upgrade")
relative = candidate.relative_to(pointer.parent)
if relative.parts != (*store_parts, candidate.name):
    raise SystemExit("managed Runtime candidate has an unexpected relative target")
raw_target = relative.as_posix()
os.symlink(raw_target, pointer)
descriptor = os.open(pointer.parent, os.O_RDONLY)
try:
    os.fsync(descriptor)
finally:
    os.close(descriptor)
try:
    valid = (
        pointer.is_symlink()
        and os.readlink(pointer) == raw_target
        and pointer.resolve(strict=True) == candidate.resolve(strict=True)
    )
except (OSError, RuntimeError):
    valid = False
if not valid:
    raise SystemExit("managed Runtime pointer changed during promotion")
PY
}

promote_staged_runtime_source() {
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Promoting staged source snapshot"
    progress_ok "Promoting staged source snapshot"
    return 0
  fi
  if [[ -z "$STAGED_RELEASE_TARGET" ]]; then
    print -r -- "runtime source promotion requested without a staged release" >&2
    return 1
  fi
  if [[ "${STAGED_RELEASE_TARGET:A}" == "${DEPLOY_SOURCE_ROOT:A}" ]]; then
    return 0
  fi
  local release_target="${STAGED_RELEASE_TARGET}"
  if [[ -e "${DEPLOY_SOURCE_ROOT}" || -L "${DEPLOY_SOURCE_ROOT}" ]]; then
    print -r -- "runtime source already exists; use --upgrade for an existing Open Nova Runtime" >&2
    return 1
  fi
  if ! promote_fresh_runtime_pointer \
    "${release_target}" \
    "${DEPLOY_SOURCE_ROOT}" \
    "releases"
  then
    return 1
  fi
  if [[ ! -f "${DEPLOY_SOURCE_ROOT}/.open-nova-runtime-source.json" ]]; then
    print -r -- "runtime source release switch failed validation; the no-clobber pointer was preserved for operator inspection" >&2
    return 1
  fi
}

deploy_runtime_source() {
  stage_runtime_source
  promote_staged_runtime_source
}

create_fresh_runtime_venv() {
  local generation="${STAGED_RELEASE_ID:-<release-id>}"
  local venv_store="${RUNTIME_HOME}/app/venvs"
  local venv_target="${venv_store}/${generation}"
  if [[ "$DRY_RUN" != "1" && -z "$STAGED_RELEASE_ID" ]]; then
    print -r -- "runtime venv creation requested without a staged source generation" >&2
    return 1
  fi
  if [[ "$DRY_RUN" != "1" && ( -e "$VENV_DIR" || -L "$VENV_DIR" ) ]]; then
    print -r -- "runtime venv pointer already exists; use --upgrade for an existing Open Nova Runtime" >&2
    return 1
  fi
  if [[ "$DRY_RUN" != "1" && ( -e "$venv_target" || -L "$venv_target" ) ]]; then
    print -r -- "runtime venv generation already exists; refusing to overwrite it" >&2
    return 1
  fi
  run_cmd mkdir -p "${venv_store}"
  run_cmd "${PYTHON_BIN}" -m venv "${venv_target}"
  if [[ "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  if [[ ! -f "${venv_target}/bin/python" ]]; then
    print -r -- "runtime venv generation failed validation before pointer promotion" >&2
    return 1
  fi
  if ! promote_fresh_runtime_pointer \
    "${venv_target}" \
    "${VENV_DIR}" \
    "app/venvs"
  then
    print -r -- "runtime venv pointer promotion failed; the staged generation was preserved for operator inspection" >&2
    return 1
  fi
}

format_selected_external_tools() {
  if [[ -z "$SELECTED_EXTERNAL_TOOLS" ]]; then
    print -r -- "none"
    return 0
  fi
  local items=("${(@ps:;;:)SELECTED_EXTERNAL_TOOLS}")
  local item=""
  local fields=()
  local labels=()
  for item in "${items[@]}"; do
    fields=("${(@ps:|:)item}")
    if [[ "${#fields[@]}" -ge 3 ]]; then
      labels+=("${fields[2]}=${fields[3]}")
    fi
  done
  if [[ "${#labels[@]}" -eq 0 ]]; then
    print -r -- "none"
  else
    print -r -- "${(j:, :)labels}"
  fi
}

nearest_existing_parent() {
  local target_path="$1"
  local parent="${target_path:h}"
  while [[ ! -e "$parent" && "$parent" != "/" && "$parent" != "." ]]; do
    parent="${parent:h}"
  done
  print -r -- "$parent"
}

preflight_check() {
  local check_status="$1"
  local severity="$2"
  local check_id="$3"
  local message="$4"
  local log_file=""
  log_file="$(installer_log_file)"
  if [[ -d "${log_file:h}" ]]; then
    print -r -- "preflight ${check_status}: ${check_id}: ${message}" >> "$log_file"
  fi
  if [[ "$check_status" != "ok" && "$severity" == "error" ]]; then
    print -r -- "preflight ${check_status}: ${check_id}: ${message}" >&2
  fi
  if [[ "$check_status" != "ok" && "$severity" == "error" ]]; then
    return 1
  fi
  return 0
}

valid_tcp_port() {
  local value="$1"
  [[ "$value" == <-> ]] && [[ "$value" -ge 1 && "$value" -le 65535 ]]
}

tcp_port_in_use() {
  local port="$1"
  resolve_lsof_bin
  if [[ -z "$LSOF_BIN" ]]; then
    return 2
  fi
  if "$LSOF_BIN" -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

resolve_lsof_bin() {
  if [[ -n "$LSOF_BIN" ]]; then
    return 0
  fi
  LSOF_BIN="$(command -v lsof 2>/dev/null || true)"
  if [[ -z "$LSOF_BIN" && -x "/usr/sbin/lsof" ]]; then
    LSOF_BIN="/usr/sbin/lsof"
  fi
}

resolve_launchctl_bin() {
  if [[ -n "$LAUNCHCTL_BIN" ]]; then
    return 0
  fi
  LAUNCHCTL_BIN="$(command -v launchctl 2>/dev/null || true)"
  if [[ -z "$LAUNCHCTL_BIN" && -x "/bin/launchctl" ]]; then
    LAUNCHCTL_BIN="/bin/launchctl"
  fi
}

resolve_python_bin() {
  if [[ "$PYTHON_SET" == "1" ]]; then
    if command -v "$PYTHON_BIN" >/dev/null 2>&1 || [[ -x "$PYTHON_BIN" ]]; then
      return 0
    fi
    return 1
  fi
  local candidate=""
  local resolved=""
  local first_existing=""
  local seen=" "
  local candidates=()
  local managed_candidates=()
  if [[ -n "$PYTHON_CANDIDATES" ]]; then
    candidates=("${(@z)PYTHON_CANDIDATES}")
  else
    candidates=(
      python3.13
      python3.12
      python3.11
      /opt/homebrew/bin/python3.13
      /opt/homebrew/bin/python3.12
      /opt/homebrew/bin/python3.11
      /usr/local/bin/python3.13
      /usr/local/bin/python3.12
      /usr/local/bin/python3.11
      "$PYTHON_BIN"
      python3
      /opt/homebrew/bin/python3
      /usr/local/bin/python3
      /usr/bin/python3
    )
  fi
  managed_candidates=("${RUNTIME_HOME}"/state/deps/python/*/bin/python3(N))
  candidates+=("${managed_candidates[@]}")
  for candidate in "${candidates[@]}"; do
    resolved="$(resolve_executable "$candidate")"
    if [[ -z "$resolved" || "$seen" == *" ${resolved} "* ]]; then
      continue
    fi
    seen+="${resolved} "
    if [[ -z "$first_existing" ]]; then
      first_existing="$resolved"
    fi
    if python_meets_minimum_version "$resolved"; then
      PYTHON_BIN="$resolved"
      return 0
    fi
  done
  if [[ -n "$first_existing" ]]; then
    PYTHON_BIN="$first_existing"
  fi
  return 1
}

resolve_executable() {
  local candidate="$1"
  local resolved=""
  if [[ -z "$candidate" ]]; then
    return 1
  fi
  if [[ "$candidate" == */* ]]; then
    if [[ -x "$candidate" ]]; then
      print -r -- "$candidate"
      return 0
    fi
    return 1
  fi
  resolved="$(command -v "$candidate" 2>/dev/null || true)"
  if [[ -n "$resolved" ]]; then
    print -r -- "$resolved"
    return 0
  fi
  return 1
}

resolve_curl_bin() {
  local candidate=""
  local resolved=""
  for candidate in "${CURL_BIN:-curl}" /usr/bin/curl; do
    resolved="$(resolve_executable "$candidate" || true)"
    if [[ -n "$resolved" ]]; then
      CURL_BIN="$resolved"
      return 0
    fi
  done
  return 1
}

resolve_tar_bin() {
  local candidate=""
  local resolved=""
  for candidate in "${TAR_BIN:-tar}" /usr/bin/tar /bin/tar; do
    resolved="$(resolve_executable "$candidate" || true)"
    if [[ -n "$resolved" ]]; then
      TAR_BIN="$resolved"
      return 0
    fi
  done
  return 1
}

resolve_shasum_bin() {
  local candidate=""
  local resolved=""
  for candidate in "${SHASUM_BIN:-shasum}" /usr/bin/shasum; do
    resolved="$(resolve_executable "$candidate" || true)"
    if [[ -n "$resolved" ]]; then
      SHASUM_BIN="$resolved"
      return 0
    fi
  done
  return 1
}

resolve_openssl_bin() {
  local candidate=""
  local resolved=""
  for candidate in "${OPENSSL_BIN:-openssl}" /usr/bin/openssl; do
    resolved="$(resolve_executable "$candidate" || true)"
    if [[ -n "$resolved" ]]; then
      OPENSSL_BIN="$resolved"
      return 0
    fi
  done
  return 1
}

python_version_probe() {
  local python="$1"
  "$python" - <<'PY' 2>/dev/null
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
raise SystemExit(0 if sys.version_info >= (3, 11) else 3)
PY
}

python_meets_minimum_version() {
  local python="$1"
  local probe=""
  local probe_status=0
  set +e
  probe="$(python_version_probe "$python")"
  probe_status=$?
  set -e
  [[ "$probe_status" == "0" && -n "$probe" ]]
}

standalone_python_target_arch() {
  local machine="${NOVA_INSTALL_MACHINE:-}"
  if [[ -z "$machine" && -n "$UNAME_BIN" ]]; then
    machine="$("$UNAME_BIN" -m)"
  fi
  case "$machine" in
    arm64|aarch64)
      print -r -- "aarch64"
      ;;
    x86_64|amd64)
      print -r -- "x86_64"
      ;;
    *)
      return 1
      ;;
  esac
}

standalone_python_default_sha256() {
  local target_arch="$1"
  if [[ "$PYTHON_STANDALONE_RELEASE" == "20260623" && "$PYTHON_STANDALONE_VERSION" == "3.13.14" ]]; then
    case "$target_arch" in
      aarch64)
        print -r -- "804c86c8665b18eb0df5070a79d828229018d145baea38a71a5c74c03f9b11d4"
        return 0
        ;;
      x86_64)
        print -r -- "cd0023fb84de358d285c8e116cffd2f433086b943e752955dade521c12e78cab"
        return 0
        ;;
    esac
  fi
  return 1
}

standalone_python_install_dir() {
  local target_arch="$1"
  if [[ -n "$PYTHON_STANDALONE_DIR" ]]; then
    print -r -- "$PYTHON_STANDALONE_DIR"
  else
    print -r -- "${RUNTIME_HOME}/state/deps/python/cpython-${PYTHON_STANDALONE_VERSION}-${target_arch}-apple-darwin"
  fi
}

standalone_python_url() {
  local target_arch="$1"
  if [[ -n "$PYTHON_STANDALONE_URL" ]]; then
    print -r -- "$PYTHON_STANDALONE_URL"
  else
    print -r -- "${PYTHON_STANDALONE_BASE_URL}/cpython-${PYTHON_STANDALONE_VERSION}%2B${PYTHON_STANDALONE_RELEASE}-${target_arch}-apple-darwin-install_only.tar.gz"
  fi
}

standalone_python_sha256() {
  local target_arch="$1"
  if [[ -n "$PYTHON_STANDALONE_SHA256" ]]; then
    print -r -- "${PYTHON_STANDALONE_SHA256#sha256:}"
  else
    standalone_python_default_sha256 "$target_arch"
  fi
}

managed_standalone_python_bin() {
  local target_arch=""
  local install_dir=""
  target_arch="$(standalone_python_target_arch)" || return 1
  install_dir="$(standalone_python_install_dir "$target_arch")"
  if [[ -x "${install_dir}/bin/python3" ]]; then
    print -r -- "${install_dir}/bin/python3"
    return 0
  fi
  return 1
}

sha256_file() {
  local file="$1"
  if resolve_shasum_bin; then
    "$SHASUM_BIN" -a 256 "$file" | awk '{ print $1 }'
    return 0
  fi
  if resolve_openssl_bin; then
    "$OPENSSL_BIN" dgst -sha256 -r "$file" | awk '{ print $1 }'
    return 0
  fi
  return 1
}

verify_sha256_file() {
  local file="$1"
  local expected="$2"
  local actual=""
  if [[ -z "$expected" ]]; then
    warn "No SHA-256 checksum is configured for managed standalone Python; refusing to execute an unverified interpreter."
    return 1
  fi
  actual="$(sha256_file "$file")" || {
    warn "Cannot verify managed standalone Python because neither shasum nor openssl is available."
    return 1
  }
  if [[ "$actual" != "$expected" ]]; then
    warn "Managed standalone Python checksum mismatch: expected ${expected}, got ${actual}"
    return 1
  fi
  return 0
}

install_managed_standalone_python() {
  local target_arch=""
  local install_dir=""
  local python_bin=""
  local url=""
  local expected_sha=""
  local tmp_dir=""
  local extract_dir=""
  local archive=""
  local broken_dir=""

  if [[ "$PYTHON_SET" == "1" || "$PYTHON_AUTO_INSTALL" != "1" ]]; then
    return 1
  fi
  if [[ "$PLATFORM" != "Darwin" ]]; then
    warn "Python >=3.11 was not found. Managed standalone Python auto-install is currently supported on macOS only; rerun with --python /path/to/python3.13."
    return 1
  fi
  target_arch="$(standalone_python_target_arch)" || {
    warn "Python >=3.11 was not found and this macOS CPU architecture is not supported by the managed standalone Python installer."
    return 1
  }
  install_dir="$(standalone_python_install_dir "$target_arch")"
  python_bin="${install_dir}/bin/python3"
  if [[ -x "$python_bin" ]] && python_meets_minimum_version "$python_bin"; then
    PYTHON_BIN="$python_bin"
    return 0
  fi
  url="$(standalone_python_url "$target_arch")"
  expected_sha="$(standalone_python_sha256 "$target_arch" || true)"
  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ "$PYTHON_INSTALL_PLANNED" != "1" ]]; then
      progress_start "Planning managed Python ${PYTHON_STANDALONE_VERSION} install"
      progress_ok "Planning managed Python ${PYTHON_STANDALONE_VERSION} install"
    fi
    PYTHON_INSTALL_PLANNED=1
    return 1
  fi
  if ! resolve_curl_bin; then
    warn "Python >=3.11 was not found and curl is unavailable; cannot download managed standalone Python."
    return 1
  fi
  if ! resolve_tar_bin; then
    warn "Python >=3.11 was not found and tar is unavailable; cannot extract managed standalone Python."
    return 1
  fi

  log "Python >=3.11 not found; installing managed standalone Python ${PYTHON_STANDALONE_VERSION} for ${target_arch}"
  tmp_dir="${RUNTIME_HOME}/state/tmp/python-standalone.$$"
  extract_dir="${tmp_dir}/extract"
  archive="${tmp_dir}/python.tar.gz"
  print -r -- "+ mkdir -p ${extract_dir} ${install_dir:h}"
  /bin/mkdir -p "$extract_dir" "${install_dir:h}"
  print -r -- "+ ${CURL_BIN} -fL --retry 3 --retry-delay 1 -o ${archive} ${url}"
  if ! "$CURL_BIN" -fL --retry 3 --retry-delay 1 -o "$archive" "$url"; then
    /bin/rm -rf "$tmp_dir"
    return 1
  fi
  print -r -- "+ verify sha256 ${archive}"
  if ! verify_sha256_file "$archive" "$expected_sha"; then
    /bin/rm -rf "$tmp_dir"
    return 1
  fi
  print -r -- "+ ${TAR_BIN} -xzf ${archive} -C ${extract_dir}"
  if ! "$TAR_BIN" -xzf "$archive" -C "$extract_dir"; then
    /bin/rm -rf "$tmp_dir"
    return 1
  fi
  if [[ ! -x "${extract_dir}/python/bin/python3" ]]; then
    warn "Managed standalone Python archive did not contain python/bin/python3"
    /bin/rm -rf "$tmp_dir"
    return 1
  fi
  if [[ -e "$install_dir" ]]; then
    broken_dir="${install_dir}.broken.$(/bin/date +%Y%m%d%H%M%S)"
    print -r -- "+ mv ${install_dir} ${broken_dir}"
    /bin/mv "$install_dir" "$broken_dir"
  fi
  print -r -- "+ mv ${extract_dir}/python ${install_dir}"
  /bin/mv "${extract_dir}/python" "$install_dir"
  print -r -- "+ rm -rf ${tmp_dir}"
  /bin/rm -rf "$tmp_dir"
  if python_meets_minimum_version "$python_bin"; then
    PYTHON_BIN="$python_bin"
    return 0
  fi
  warn "Managed standalone Python was installed but did not report Python >=3.11: ${python_bin}"
  return 1
}

maybe_install_python_runtime() {
  if install_managed_standalone_python; then
    return 0
  fi
  return 1
}

ensure_python_bin() {
  if resolve_python_bin; then
    return 0
  fi
  maybe_install_python_runtime || true
  resolve_python_bin
}

dashboard_port_candidates() {
  local candidates=()
  local seen=" "
  local candidate=""
  candidates+=("$DASHBOARD_PORT")
  for candidate in ${(z)DASHBOARD_PORT_CANDIDATES}; do
    candidates+=("$candidate")
  done
  for candidate in "${candidates[@]}"; do
    if valid_tcp_port "$candidate" && [[ "$seen" != *" ${candidate} "* ]]; then
      seen+="${candidate} "
      print -r -- "$candidate"
    fi
  done
}

select_dashboard_port() {
  if [[ "$NO_DASHBOARD_SERVER" == "1" ]]; then
    return 0
  fi
  if ! valid_tcp_port "$DASHBOARD_PORT"; then
    print -r -- "--dashboard-port must be between 1 and 65535" >&2
    exit 2
  fi
  if [[ "$DASHBOARD_PORT_AUTO" != "1" ]]; then
    return 0
  fi
  if [[ "$UPGRADE" == "1" ]]; then
    return 0
  fi
  local candidate=""
  local probe_status=0
  for candidate in $(dashboard_port_candidates); do
    set +e
    tcp_port_in_use "$candidate"
    probe_status=$?
    set -e
    if [[ "$probe_status" == "1" ]]; then
      if [[ "$candidate" != "$DASHBOARD_PORT" ]]; then
        warn "Dashboard port ${DASHBOARD_PORT} is in use; falling back to ${candidate}"
        DASHBOARD_PORT="$candidate"
      fi
      return 0
    fi
    if [[ "$probe_status" == "2" ]]; then
      warn "lsof not found; cannot auto-select Dashboard fallback port. Using ${DASHBOARD_PORT}."
      return 0
    fi
  done
  warn "All Dashboard fallback ports are in use: $(dashboard_port_candidates | tr '\n' ' ')"
  return 0
}

require_fresh_runtime_empty() {
  if [[ "$UPGRADE" == "1" || "$DRY_RUN" == "1" ]]; then
    return 0
  fi
  if [[ -L "${RUNTIME_HOME}/app" || ( -e "${RUNTIME_HOME}/app" && ! -d "${RUNTIME_HOME}/app" ) ]]; then
    print -r -- "existing Open Nova Runtime state requires --upgrade: ${RUNTIME_HOME}" >&2
    return 2
  fi
  local marker=""
  for marker in \
    "${RUNTIME_HOME}/app/source" \
    "${RUNTIME_HOME}/app/releases" \
    "${RUNTIME_HOME}/app/venvs" \
    "${RUNTIME_HOME}/app/update-transactions" \
    "${RUNTIME_HOME}/app/.update-transaction.lock" \
    "${RUNTIME_HOME}/.venv" \
    "${RUNTIME_HOME}/config/runtime.json" \
    "${RUNTIME_HOME}/config/settings.json" \
    "${RUNTIME_HOME}/data/nova_data.sqlite3" \
    "${RUNTIME_HOME}/bin/open-nova"; do
    if [[ -e "$marker" || -L "$marker" ]]; then
      print -r -- "existing Open Nova Runtime state requires --upgrade: ${RUNTIME_HOME}" >&2
      return 2
    fi
  done
  return 0
}

run_installer_preflight() {
  require_fresh_runtime_empty || return 2
  log "Installer preflight/doctor"
  local errors=0
  local python_probe=""
  local python_status=0
  local required_file=""
  local required_files=(
    "LICENSE"
    "pyproject.toml"
    "advanced/cli/open_nova.py"
    "advanced/dashboard/dashboard_launch_agent.py"
    "advanced/dashboard/rag_server_launch_agent.py"
    "advanced/pipeline/run_daily_pipeline.py"
    "advanced/pipeline/run_dashboard_foundation_refresh.py"
    "src/dashboard/app/main.py"
    "src/dashboard/app/static/index.html"
    "src/data_foundation/migrations/0001_initial.sql"
  )
  local parent=""
  local target_path=""
  local dashboard_port="${DASHBOARD_PORT}"

  for required_file in "${required_files[@]}"; do
    if [[ -f "${SOURCE_ROOT}/${required_file}" ]]; then
      preflight_check ok error "source-file" "found ${SOURCE_ROOT}/${required_file}" || errors=$(( errors + 1 ))
    else
      preflight_check error error "source-file" "missing ${SOURCE_ROOT}/${required_file}" || errors=$(( errors + 1 ))
    fi
  done

  if ensure_python_bin; then
    set +e
    python_probe="$(python_version_probe "$PYTHON_BIN")"
    python_status=$?
    set -e
    if [[ "$python_status" == "0" && -n "$python_probe" ]]; then
      preflight_check ok error "python-version" "${PYTHON_BIN} reports Python ${python_probe}" || errors=$(( errors + 1 ))
    elif [[ "$python_status" == "3" ]]; then
      if [[ "$PYTHON_SET" != "1" && "$PYTHON_AUTO_INSTALL" == "1" ]]; then
        maybe_install_python_runtime || true
        set +e
        python_probe="$(python_version_probe "$PYTHON_BIN")"
        python_status=$?
        set -e
      fi
      if [[ "$python_status" == "0" && -n "$python_probe" ]]; then
        preflight_check ok error "python-version" "${PYTHON_BIN} reports Python ${python_probe}" || errors=$(( errors + 1 ))
      elif [[ "$DRY_RUN" == "1" && "$PYTHON_INSTALL_PLANNED" == "1" ]]; then
        preflight_check warn warn "python-bootstrap" "Python >=3.11 not found; dry-run would install managed standalone Python ${PYTHON_STANDALONE_VERSION}" || true
      else
        preflight_check error error "python-version" "${PYTHON_BIN} reports Python ${python_probe}; Python >=3.11 is required" || errors=$(( errors + 1 ))
      fi
    else
      preflight_check warn warn "python-version" "${PYTHON_BIN} is executable but version could not be verified before venv creation" || true
    fi
    if "$PYTHON_BIN" -c "import venv" >/dev/null 2>&1
    then
      preflight_check ok error "python-venv" "${PYTHON_BIN} can create virtual environments" || errors=$(( errors + 1 ))
    else
      preflight_check error error "python-venv" "${PYTHON_BIN} cannot run python -m venv" || errors=$(( errors + 1 ))
    fi
  else
    if [[ "$DRY_RUN" == "1" && "$PYTHON_INSTALL_PLANNED" == "1" ]]; then
      preflight_check warn warn "python-bootstrap" "Python >=3.11 not found; dry-run would install managed standalone Python ${PYTHON_STANDALONE_VERSION}" || true
    elif [[ "$DRY_RUN" == "1" && "$PYTHON_SET" != "1" ]]; then
      preflight_check warn warn "python-command" "Python executable not found during dry-run preview: ${PYTHON_BIN}" || true
    else
      preflight_check error error "python-command" "Python executable not found: ${PYTHON_BIN}" || errors=$(( errors + 1 ))
    fi
  fi

  for target_path in "$RUNTIME_HOME" "$DIARY_OUTPUT_DIR" "$REPORTS_OUTPUT_DIR" "$SNAPSHOTS_OUTPUT_DIR" "$ARCHIVES_OUTPUT_DIR" "${LOCATION_FILE:h}"; do
    parent="$(nearest_existing_parent "$target_path")"
    if [[ -n "$parent" && -w "$parent" ]]; then
      preflight_check ok error "writable-target" "${target_path} can be created under ${parent}" || errors=$(( errors + 1 ))
    else
      preflight_check error error "writable-target" "${target_path} cannot be created; nearest parent is not writable: ${parent:-unknown}" || errors=$(( errors + 1 ))
    fi
  done

  if [[ "$CREATE_DESKTOP_DIARY_LINK" == "1" ]]; then
    parent="$(nearest_existing_parent "$DESKTOP_DIARY_LINK")"
    if [[ -n "$parent" && -w "$parent" ]]; then
      preflight_check ok warn "desktop-shortcut" "${DESKTOP_DIARY_LINK} can be created under ${parent}" || true
    else
      preflight_check warn warn "desktop-shortcut" "${DESKTOP_DIARY_LINK} may not be creatable; installer will continue without core failure" || true
    fi
  fi

  if [[ "$PLATFORM" == "Darwin" && ( "$NO_SCHEDULER" != "1" || "$NO_DASHBOARD_SERVER" != "1" ) ]]; then
    resolve_launchctl_bin
    if [[ -n "$LAUNCHCTL_BIN" ]]; then
      preflight_check ok warn "launchctl" "${LAUNCHCTL_BIN} is available" || true
      local uid=""
      uid="$("$ID_BIN" -u 2>/dev/null || print -r -- "")"
      if [[ -n "$uid" ]] && "$LAUNCHCTL_BIN" print "gui/${uid}" >/dev/null 2>&1; then
        preflight_check ok warn "launchagent-domain" "gui/${uid} launchd domain is available" || true
      else
        preflight_check warn warn "launchagent-domain" "launchd gui domain is not available; service registration may be skipped or fail" || true
      fi
    else
      preflight_check warn warn "launchctl" "launchctl not found; managed service registration may be skipped or fail" || true
    fi
    parent="$(nearest_existing_parent "$HOME/Library/LaunchAgents")"
    if [[ -n "$parent" && -w "$parent" ]]; then
      preflight_check ok warn "launchagents-writable" "$HOME/Library/LaunchAgents can be created under ${parent}" || true
    else
      preflight_check warn warn "launchagents-writable" "$HOME/Library/LaunchAgents may not be writable; scheduler/SSE service registration may fail" || true
    fi
  fi

  if [[ "$NO_DASHBOARD_SERVER" != "1" ]]; then
    if ! valid_tcp_port "$dashboard_port"; then
      preflight_check error error "dashboard-port" "TCP port ${dashboard_port} is invalid" || errors=$(( errors + 1 ))
    else
      local port_probe_status=0
      resolve_lsof_bin
      set +e
      tcp_port_in_use "$dashboard_port"
      port_probe_status=$?
      set -e
      case "$port_probe_status" in
        0)
          if [[ "$UPGRADE" == "1" ]]; then
            preflight_check warn warn "dashboard-port" "TCP port ${dashboard_port} is already in use; upgrade will replace/restart the managed SSE service on this port" || true
          elif [[ "$DASHBOARD_PORT_AUTO" == "1" ]]; then
            preflight_check warn warn "dashboard-port" "TCP port ${dashboard_port} is already in use after fallback selection; SSE server registration may fail" || true
          else
            preflight_check error error "dashboard-port" "TCP port ${dashboard_port} is already in use and --no-dashboard-port-auto is set" || errors=$(( errors + 1 ))
          fi
          ;;
        1)
          preflight_check ok warn "dashboard-port" "TCP port ${dashboard_port} appears available" || true
          ;;
        *)
          preflight_check warn warn "dashboard-port" "lsof not found; cannot preflight TCP port ${dashboard_port}" || true
          ;;
      esac
    fi
  fi

  preflight_check ok warn "pip-network" "pip network access is deferred to dependency installation; failures will be reported with the pip command" || true

  if [[ "$errors" -gt 0 ]]; then
    print -r -- "Installer preflight failed with ${errors} error(s)." >&2
    exit 2
  fi
}

run_post_install_doctor() {
  log "Post-install doctor"
  run_json_cmd "Runtime status doctor" \
    "${VENV_PY}" -m data_foundation.cli \
    onboarding runtime-status \
    --runtime "${RUNTIME_HOME}" \
    --json
  run_optional_json_cmd "Installer doctor" \
    "${VENV_PY}" -m data_foundation.cli \
    doctor --installer \
    --runtime "${RUNTIME_HOME}" \
    --json
  run_optional_json_cmd "Pipeline doctor" \
    "${VENV_PY}" -m data_foundation.cli \
    doctor --pipeline \
    --runtime "${RUNTIME_HOME}" \
    --json
  run_optional_json_cmd "Scheduler doctor" \
    "${VENV_PY}" -m data_foundation.cli \
    doctor --scheduler \
    --runtime "${RUNTIME_HOME}" \
    --json
  if [[ "$ENABLE_RAG" == "1" ]]; then
    run_optional_json_cmd "nova-RAG doctor" \
      "${VENV_PY}" -m data_foundation.cli \
      doctor --rag \
      --runtime "${RUNTIME_HOME}" \
      --json
  fi
}

cleanup_runtime_source_artifacts() {
  if [[ "$DRY_RUN" == "1" || ! -e "${DEPLOY_SOURCE_ROOT}" ]]; then
    return 0
  fi
  progress_start "Cleaning runtime source artifacts"
  rm -rf "${DEPLOY_SOURCE_ROOT}/build" "${DEPLOY_SOURCE_ROOT}/dist"
  find -H "${DEPLOY_SOURCE_ROOT}" \
    \( -type d -name "__pycache__" -o -type d -name "*.egg-info" \) \
    -prune -exec rm -rf {} + 2>/dev/null || true
  progress_ok "Cleaning runtime source artifacts"
}

run_runtime_dependency_check() {
  local missing_file="$1"
  local dependency_nova_home="${RUNTIME_HOME}"
  local dependency_location_file="${LOCATION_FILE}"
  local -a check_command
  check_command=("${VENV_PY}" -)
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" ]]; then
    check_command=(
      "${PYTHON_BIN}" "${UPDATE_TRANSACTION_HELPER}"
      run-candidate-command
      --state "${UPDATE_TRANSACTION_JOURNAL}"
      --phase candidate-dependency-check
      --
      /usr/bin/env
      -i
      "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
      "USER=open-nova-candidate"
      "LOGNAME=open-nova-candidate"
      "SHELL=/bin/zsh"
      "LC_ALL=C"
      "LANG=C"
      "NOVA_INSTALL_DEPLOY_SOURCE_ROOT=${DEPLOY_SOURCE_ROOT}"
      "NOVA_INSTALL_ENABLE_RAG=${ENABLE_RAG}"
      "NOVA_INSTALL_RAG_EMBEDDING_MODE=${RAG_EMBEDDING_MODE}"
      "NOVA_INSTALL_MISSING_DEPENDENCIES_FILE=${missing_file}"
      "NOVA_HOME=${UPDATE_VALIDATION_RUNTIME}"
      "NOVA_LOCATION_FILE=${UPDATE_VALIDATION_RUNTIME}/location.json"
      "PYTHONPATH=${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src:${DEPLOY_SOURCE_ROOT}/src/dashboard"
      "HOME=${UPDATE_VALIDATION_RUNTIME}/home"
      "TMPDIR=${UPDATE_VALIDATION_RUNTIME}/tmp"
      "XDG_CONFIG_HOME=${UPDATE_VALIDATION_RUNTIME}/xdg"
      "PIP_CONFIG_FILE=/dev/null"
      "PIP_CACHE_DIR=${UPDATE_VALIDATION_RUNTIME}/pip-cache"
      "PYTHONNOUSERSITE=1"
      "OPEN_NOVA_SECRET_BACKEND=memory"
      "PYTHONDONTWRITEBYTECODE=1"
      "${VENV_PY}" -
    )
  fi
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" ]]; then
    dependency_nova_home="${UPDATE_VALIDATION_RUNTIME}"
    dependency_location_file="${UPDATE_VALIDATION_RUNTIME}/location.json"
  fi
  rm -f "${missing_file}"
  NOVA_INSTALL_DEPLOY_SOURCE_ROOT="${DEPLOY_SOURCE_ROOT}" \
  NOVA_INSTALL_ENABLE_RAG="${ENABLE_RAG}" \
  NOVA_INSTALL_RAG_EMBEDDING_MODE="${RAG_EMBEDDING_MODE}" \
  NOVA_INSTALL_MISSING_DEPENDENCIES_FILE="${missing_file}" \
  NOVA_HOME="${dependency_nova_home}" \
  NOVA_LOCATION_FILE="${dependency_location_file}" \
  PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src:${DEPLOY_SOURCE_ROOT}/src/dashboard" \
  PYTHONDONTWRITEBYTECODE=1 \
    "${check_command[@]}" <<'PY'
import importlib
import os
import sys
from pathlib import Path

source_root = Path(os.environ["NOVA_INSTALL_DEPLOY_SOURCE_ROOT"])
missing_file = Path(os.environ["NOVA_INSTALL_MISSING_DEPENDENCIES_FILE"])
required_static = [
    source_root / "src" / "dashboard" / "app" / "static" / "index.html",
    source_root / "src" / "dashboard" / "app" / "static" / "css" / "style.css",
    source_root / "src" / "dashboard" / "app" / "static" / "js" / "app.js",
]
dashboard_checks = [
    ("fastapi", "fastapi>=0.110,<1", "Dashboard API"),
    ("uvicorn", "uvicorn>=0.29,<1", "Dashboard server"),
    ("yaml", "PyYAML>=6,<7", "Dashboard settings YAML"),
    ("croniter", "croniter>=2,<7", "Dashboard scheduler"),
]
rag_checks = []
if os.environ.get("NOVA_INSTALL_ENABLE_RAG") == "1":
    rag_checks = [
        ("numpy", "numpy>=1.26,<3", "nova-RAG vectors"),
        ("pydantic", "pydantic>=2,<3", "nova-RAG API schema"),
    ]
    if os.environ.get("NOVA_INSTALL_RAG_EMBEDDING_MODE") == "local":
        rag_checks[0:0] = [
            ("sentence_transformers", "sentence-transformers>=3,<6", "nova-RAG local embeddings"),
            ("torch", "torch>=2,<3", "nova-RAG local embeddings"),
        ]
checks = dashboard_checks + rag_checks

errors = []
missing_packages = []
for path in required_static:
    if not path.is_file():
        errors.append(f"missing Dashboard static asset: {path}")

for module_name, package_spec, purpose in checks:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        errors.append(f"{purpose} dependency import failed: {module_name}: {exc}")
        missing_packages.append(package_spec)

try:
    importlib.import_module("app.main")
except Exception as exc:
    errors.append(f"Dashboard application import failed: app.main: {exc}")

if errors:
    if missing_packages:
        missing_file.parent.mkdir(parents=True, exist_ok=True)
        missing_file.write_text("\n".join(dict.fromkeys(missing_packages)) + "\n", encoding="utf-8")
    for item in errors:
        print(f"dependency gate error: {item}", file=sys.stderr)
    raise SystemExit(1)

print("dependency gate ok: Dashboard static assets: index.html, style.css, app.js")
print("dependency gate ok: Dashboard dependencies: fastapi, uvicorn, PyYAML, croniter")
if rag_checks:
    print("dependency gate ok: nova-RAG local dependencies: sentence-transformers, torch, numpy, pydantic")
PY
}

run_runtime_dependency_gate() {
  local label="Verifying runtime dependency gate"
  local log_file=""
  log "Verifying runtime Dashboard dependency gate"
  log "Dependency gate: Dashboard dependencies (fastapi, uvicorn, PyYAML, croniter) and static UI assets"
  if [[ "$ENABLE_RAG" == "1" && "$RAG_EMBEDDING_MODE" == "local" ]]; then
    log "Dependency gate: nova-RAG local dependencies (sentence-transformers, torch, numpy, pydantic)"
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    if [[ "$SUMMARY_ONLY" == "1" ]]; then
      return 0
    fi
    progress_start "$label"
    progress_ok "$label"
    return 0
  fi
  local missing_file="${RUNTIME_HOME}/state/tmp/installer-dependency-gate-missing.txt"
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" && -n "$UPDATE_TRANSACTION_DIR" ]]; then
    missing_file="${UPDATE_TRANSACTION_DIR}/candidate-dependency-gate-missing.txt"
  fi
  local missing_packages=()
  mkdir -p "${missing_file:h}"
  log_file="$(installer_log_file)"
  mkdir -p "${log_file:h}"
  progress_start "$label"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}" >> "$log_file"
  if run_runtime_dependency_check "${missing_file}" >> "$log_file" 2>&1; then
    progress_ok "$label"
    return 0
  fi
  if [[ ! -s "${missing_file}" ]]; then
    progress_fail "${label} failed; see ${log_file}"
    return 1
  fi
  while IFS= read -r package_spec; do
    if [[ -n "${package_spec}" ]]; then
      missing_packages+=("${package_spec}")
    fi
  done < "${missing_file}"
  if (( ${#missing_packages[@]} == 0 )); then
    progress_fail "${label} failed; see ${log_file}"
    return 1
  fi
  log "Installing missing runtime dependencies detected by dependency gate: ${missing_packages[*]}"
  progress_fail "${label} found missing packages; installing remediation"
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" && "$SOURCE_ONLY" != "1" ]]; then
    run_update_candidate_cmd candidate-dependency-remediation \
      "${VENV_PY}" -m pip install "${missing_packages[@]}"
  else
    run_cmd "${VENV_PY}" -m pip install "${missing_packages[@]}"
  fi
  progress_start "$label"
  if run_runtime_dependency_check "${missing_file}" >> "$log_file" 2>&1; then
    progress_ok "$label"
    return 0
  fi
  progress_fail "${label} failed after remediation; see ${log_file}"
  return 1
}

run_dashboard_service_launch_agent_apply() {
  if [[ "$UPGRADE" == "1" ]]; then
    run_json_cmd "SSE server LaunchAgent service registration" \
      "${VENV_PY}" -c 'import json; from app.services import launcher; print(json.dumps(launcher.install_dashboard_launch_agent({"confirmationText": launcher.DASHBOARD_INSTALL_CONFIRMATION}), ensure_ascii=False, indent=2))'
  else
    run_optional_json_cmd "SSE server LaunchAgent service registration" \
      "${VENV_PY}" -c 'import json; from app.services import launcher; print(json.dumps(launcher.install_dashboard_launch_agent({"confirmationText": launcher.DASHBOARD_INSTALL_CONFIRMATION}), ensure_ascii=False, indent=2))'
  fi
}

run_rag_service_launch_agent_apply() {
  if [[ "$UPGRADE" == "1" ]]; then
    run_json_cmd "nova-RAG server LaunchAgent service registration" \
      "${VENV_PY}" -c 'import json; from app.services import launcher; print(json.dumps(launcher.install_rag_launch_agent({"confirmationText": launcher.RAG_INSTALL_CONFIRMATION}), ensure_ascii=False, indent=2))'
  else
    run_optional_json_cmd "nova-RAG server LaunchAgent service registration" \
      "${VENV_PY}" -c 'import json; from app.services import launcher; print(json.dumps(launcher.install_rag_launch_agent({"confirmationText": launcher.RAG_INSTALL_CONFIRMATION}), ensure_ascii=False, indent=2))'
  fi
}

run_external_rag_skill_registration_apply() {
  local label="Registering nova-RAG skill for selected external tools"
  local log_file=""
  if [[ "$ENABLE_RAG" != "1" || "$ENABLE_SKILL_REGISTRATION" != "1" ]]; then
    return 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "$label"
    progress_ok "$label"
    return 0
  fi
  log_file="$(installer_log_file)"
  progress_start "$label"
  print -r -- "" >> "$log_file"
  print -r -- "## ${label}" >> "$log_file"
  if NOVA_HOME="${RUNTIME_HOME}" \
    NOVA_LOCATION_FILE="${LOCATION_FILE}" \
    PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src:${DEPLOY_SOURCE_ROOT}/src/dashboard" \
    "${VENV_PY}" - >> "$log_file" 2>&1 <<'PY'
import json

from app.services.external_rag_skill_registration import (
    CONFIRMATION_TEXT,
    DEFAULT_TARGETS,
    queue_rag_skill_registration,
)
from data_foundation.paths import load_paths
from data_foundation.settings import read_settings, write_settings

settings = read_settings(load_paths(), redact_secrets=True)
external = settings.get("externalTools") if isinstance(settings.get("externalTools"), dict) else {}
preference = external.get("installerV2SkillRegistration") if isinstance(external.get("installerV2SkillRegistration"), dict) else {}
selected = preference.get("selectedTools") if isinstance(preference.get("selectedTools"), list) else external.get("installerSelectedTools")
selected = selected if isinstance(selected, list) else []
tools = []
for item in selected:
    key = str(item.get("key") or "") if isinstance(item, dict) else str(item)
    if key in DEFAULT_TARGETS and key not in tools:
        tools.append(key)
if not tools:
    print(json.dumps({"accepted": True, "status": "skipped", "reason": "no-supported-installer-selected-tools"}))
else:
    result = queue_rag_skill_registration({
        "tools": tools,
        "dryRun": False,
        "overwrite": False,
        "confirmationText": CONFIRMATION_TEXT,
    }, requested_by="installer-v2")
    write_settings({"externalTools": {"installerV2SkillRegistration": {
        "status": "installer-applied",
        "supportedNow": True,
    }}})
    print(json.dumps(result, ensure_ascii=False, indent=2))
PY
  then
    progress_ok "$label"
  else
    progress_fail "${label} failed; see ${log_file}"
    warn "Open Nova installation completed, but external nova-RAG skill registration failed; retry from Dashboard Settings."
  fi
}

maybe_fail_update_phase() {
  local phase="$1"
  if [[ "$UPDATE_TEST_MODE" != "1" ]]; then
    return 0
  fi
  if [[ -n "$UPDATE_TEST_HOOK" ]]; then
    if [[ "$UPDATE_TEST_HOOK" != /* || ! -x "$UPDATE_TEST_HOOK" ]]; then
      print -r -- "NOVA_INSTALL_TEST_HOOK must be an absolute executable path" >&2
      return 2
    fi
    "$UPDATE_TEST_HOOK" "$phase"
  fi
  if [[ -z "$UPDATE_TEST_FAIL_PHASE" ]]; then
    return 0
  fi
  if [[ "$UPDATE_TEST_FAIL_PHASE" == *[^a-z0-9-]* ]]; then
    print -r -- "NOVA_INSTALL_TEST_FAIL_PHASE must be a stable lowercase phase id" >&2
    return 2
  fi
  if [[ "$UPDATE_TEST_FAIL_PHASE" == "$phase" ]]; then
    print -r -- "synthetic update failure at phase ${phase}" >&2
    return 97
  fi
}

update_transaction_command() {
  "${PYTHON_BIN}" "${UPDATE_TRANSACTION_HELPER}" "$@"
}

run_update_candidate_cmd() {
  local phase="$1"
  shift
  run_cmd \
    "${PYTHON_BIN}" "${UPDATE_TRANSACTION_HELPER}" \
    run-candidate-command \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --phase "$phase" \
    -- /usr/bin/env -i \
    "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
    "USER=open-nova-candidate" \
    "LOGNAME=open-nova-candidate" \
    "SHELL=/bin/zsh" \
    "LC_ALL=C" \
    "LANG=C" \
    "NOVA_HOME=${UPDATE_VALIDATION_RUNTIME}" \
    "NOVA_LOCATION_FILE=${UPDATE_VALIDATION_RUNTIME}/location.json" \
    "HOME=${UPDATE_VALIDATION_RUNTIME}/home" \
    "TMPDIR=${UPDATE_VALIDATION_RUNTIME}/tmp" \
    "XDG_CONFIG_HOME=${UPDATE_VALIDATION_RUNTIME}/xdg" \
    "PIP_CONFIG_FILE=/dev/null" \
    "PIP_CACHE_DIR=${UPDATE_VALIDATION_RUNTIME}/pip-cache" \
    "PYTHONNOUSERSITE=1" \
    "OPEN_NOVA_SECRET_BACKEND=memory" \
    "PYTHONDONTWRITEBYTECODE=1" \
    "$@"
}

prepare_update_validation_runtime() {
  local reserved=""
  reserved="$(update_transaction_command reserve-artifact \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --kind validation-runtime)"
  if [[ "$reserved" != "$UPDATE_VALIDATION_RUNTIME" ]]; then
    print -r -- "transaction validation Runtime reservation returned an unexpected path" >&2
    return 1
  fi
  mkdir -p \
    "${UPDATE_VALIDATION_RUNTIME}/home" \
    "${UPDATE_VALIDATION_RUNTIME}/tmp" \
    "${UPDATE_VALIDATION_RUNTIME}/xdg" \
    "${UPDATE_VALIDATION_RUNTIME}/pip-cache"
}

update_exit_handler() {
  local original_rc="$1"
  local rollback_rc=0
  trap - ZERR INT TERM HUP
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" && "$UPDATE_COMMITTED" != "1" && "$UPDATE_ROLLBACK_RUNNING" != "1" ]]; then
    UPDATE_ROLLBACK_RUNNING=1
    set +e
    update_transaction_command rollback --state "${UPDATE_TRANSACTION_JOURNAL}"
    rollback_rc=$?
    set -e
    if [[ "$rollback_rc" -ne 0 ]]; then
      print -r -- "Open Nova update rollback was incomplete; inspect the preserved transaction journal: ${UPDATE_TRANSACTION_JOURNAL}" >&2
      original_rc=70
    else
      print -r -- "Open Nova update failed; the prior source, venv, protected control files, and service state were restored. Live SQLite databases passed integrity checks and were not rewound." >&2
    fi
  fi
  exit "$original_rc"
}

begin_update_transaction() {
  local uid="0"
  local launchctl_path="/usr/bin/true"
  local mode="upgrade"
  UPDATE_TRANSACTION_ID="$(date +%Y%m%dT%H%M%S)-$$-${RANDOM}"
  if [[ "$SOURCE_ONLY" == "1" ]]; then
    mode="source-only"
  fi
  if [[ "$PLATFORM" == "Darwin" ]]; then
    resolve_launchctl_bin
    if [[ -z "$LAUNCHCTL_BIN" ]]; then
      print -r -- "launchctl not found; guarded update cannot capture managed service state" >&2
      return 1
    fi
    launchctl_path="$LAUNCHCTL_BIN"
    uid="$("$ID_BIN" -u 2>/dev/null || print -r -- "")"
    if [[ -z "$uid" ]]; then
      print -r -- "current uid could not be determined; guarded update cannot capture managed service state" >&2
      return 1
    fi
  fi
  update_transaction_command recover --runtime "${RUNTIME_HOME}"
  UPDATE_TRANSACTION_JOURNAL="$(update_transaction_command begin \
    --runtime "${RUNTIME_HOME}" \
    --home "${HOME}" \
    --source-pointer "${DEPLOY_SOURCE_ROOT}" \
    --venv-pointer "${VENV_DIR}" \
    --mode "${mode}" \
    --tx-id "${UPDATE_TRANSACTION_ID}" \
    --owner-pid "$$" \
    --platform "${PLATFORM}" \
    --launchctl "${launchctl_path}" \
    --uid "${uid}")"
  UPDATE_TRANSACTION_DIR="${UPDATE_TRANSACTION_JOURNAL:h}"
  UPDATE_VALIDATION_RUNTIME="${UPDATE_TRANSACTION_DIR}/candidate-runtime"
  UPDATE_TRANSACTION_ACTIVE=1
}

record_update_candidate() {
  local kind="$1"
  local candidate="$2"
  update_transaction_command record-candidate \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --kind "$kind" \
    --candidate "$candidate"
}

stage_update_candidate_venv() {
  local active_source="${DEPLOY_SOURCE_ROOT}"
  local active_venv_dir="${VENV_DIR}"
  local active_venv_py="${VENV_PY}"
  local candidate_spec="${STAGED_RELEASE_TARGET}"
  local candidate_root="${RUNTIME_HOME}/app/venvs"
  UPDATE_STAGED_VENV="$(update_transaction_command reserve-artifact \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --kind venv)"
  if [[ "$UPDATE_STAGED_VENV" != "${candidate_root}/${UPDATE_TRANSACTION_ID}" ]]; then
    print -r -- "transaction venv reservation returned an unexpected path" >&2
    return 1
  fi
  if [[ "${#INSTALL_EXTRAS[@]}" -gt 0 ]]; then
    candidate_spec="${candidate_spec}[${(j:,:)INSTALL_EXTRAS}]"
  fi
  run_update_candidate_cmd candidate-venv-create \
    "${PYTHON_BIN}" -m venv "${UPDATE_STAGED_VENV}"
  VENV_DIR="${UPDATE_STAGED_VENV}"
  VENV_PY="${UPDATE_STAGED_VENV}/bin/python"
  DEPLOY_SOURCE_ROOT="${STAGED_RELEASE_TARGET}"
  run_update_candidate_cmd candidate-pip-upgrade \
    /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "${VENV_PY}" -m pip install --upgrade pip
  run_update_candidate_cmd candidate-pip-install \
    /usr/bin/env PYTHONDONTWRITEBYTECODE=1 "${VENV_PY}" -m pip install "${candidate_spec}"
  run_runtime_dependency_gate
  DEPLOY_SOURCE_ROOT="${active_source}"
  VENV_DIR="${active_venv_dir}"
  VENV_PY="${active_venv_py}"
  record_update_candidate venv "${UPDATE_STAGED_VENV}"
  maybe_fail_update_phase candidate-venv
}

run_source_only_candidate_gate() {
  local active_source="${DEPLOY_SOURCE_ROOT}"
  local missing_file="${UPDATE_TRANSACTION_DIR}/source-only-dependency-gate-missing.txt"
  local log_file="$(installer_log_file)"
  if ! PYTHONDONTWRITEBYTECODE=1 "${PYTHON_BIN}" - "${RUNTIME_HOME}" "${VENV_DIR}" <<'PY'
import os
import sys
from pathlib import Path

runtime = Path(os.path.abspath(sys.argv[1]))
pointer = Path(os.path.abspath(sys.argv[2]))
store = runtime / "app" / "venvs"
try:
    if pointer != runtime / ".venv" or not pointer.is_symlink():
        raise ValueError
    raw_target = Path(os.readlink(pointer))
    if raw_target.is_absolute() or raw_target.parts[:2] != ("app", "venvs") or len(raw_target.parts) != 3:
        raise ValueError
    if any(part in {"", ".", ".."} for part in raw_target.parts):
        raise ValueError
    lexical_target = pointer.parent / raw_target
    if lexical_target.is_symlink() or not lexical_target.is_dir() or lexical_target.parent != store:
        raise ValueError
    if store.is_symlink() or lexical_target.resolve(strict=True).parent != store.resolve(strict=True):
        raise ValueError
    if not (pointer / "bin" / "python").is_file():
        raise ValueError
except (OSError, RuntimeError, ValueError):
    raise SystemExit("source-only update requires a relative managed Runtime venv pointer; run a full upgrade first")
PY
  then
    return 1
  fi
  if [[ ! -x "$VENV_PY" ]]; then
    print -r -- "source-only update requires the existing runtime venv: ${VENV_PY}" >&2
    return 1
  fi
  DEPLOY_SOURCE_ROOT="${STAGED_RELEASE_TARGET}"
  mkdir -p "${log_file:h}"
  progress_start "Verifying source-only candidate against existing dependencies"
  if ! run_runtime_dependency_check "$missing_file" >> "$log_file" 2>&1; then
    DEPLOY_SOURCE_ROOT="${active_source}"
    progress_fail "Source-only candidate requires dependency changes; use a full upgrade"
    return 1
  fi
  DEPLOY_SOURCE_ROOT="${active_source}"
  progress_ok "Verifying source-only candidate against existing dependencies"
}

run_update_candidate_doctor() {
  log "Atomic upgrade candidate doctor"
  run_json_cmd "Candidate installer doctor" \
    "${PYTHON_BIN}" "${UPDATE_TRANSACTION_HELPER}" \
    run-candidate-command \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --phase candidate-doctor \
    -- \
    /usr/bin/env -i \
      PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin \
      USER=open-nova-candidate \
      LOGNAME=open-nova-candidate \
      SHELL=/bin/zsh \
      LC_ALL=C \
      LANG=C \
      NOVA_HOME="${RUNTIME_HOME}" \
      NOVA_LOCATION_FILE="${LOCATION_FILE}" \
      HOME="${UPDATE_VALIDATION_RUNTIME}/home" \
      TMPDIR="${UPDATE_VALIDATION_RUNTIME}/tmp" \
      XDG_CONFIG_HOME="${UPDATE_VALIDATION_RUNTIME}/xdg" \
      PIP_CONFIG_FILE=/dev/null \
      PIP_CACHE_DIR="${UPDATE_VALIDATION_RUNTIME}/pip-cache" \
      PYTHONNOUSERSITE=1 \
      OPEN_NOVA_SECRET_BACKEND=memory \
      PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src:${DEPLOY_SOURCE_ROOT}/src/dashboard" \
      PYTHONDONTWRITEBYTECODE=1 \
      "${VENV_PY}" -m data_foundation.cli \
      doctor --installer \
      --runtime "${RUNTIME_HOME}" \
      --json
}

clean_staged_candidate_build_artifacts() {
  if [[ -z "$STAGED_RELEASE_TARGET" || ! -d "$STAGED_RELEASE_TARGET" ]]; then
    return 0
  fi
  if [[ "$UPDATE_TRANSACTION_ACTIVE" == "1" ]]; then
    update_transaction_command clean-source-build-artifacts \
      --state "${UPDATE_TRANSACTION_JOURNAL}"
    return 0
  fi
  # The clean staged payload excludes these paths. Any occurrence here was
  # produced by candidate wheel/import validation and is transaction-owned.
  rm -rf "${STAGED_RELEASE_TARGET}/build" "${STAGED_RELEASE_TARGET}/dist"
  find -P "${STAGED_RELEASE_TARGET}" \
    \( -type d -name "__pycache__" -o -type d -name "*.egg-info" \) \
    -prune -exec rm -rf {} + 2>/dev/null || true
}

capture_update_mutable_state() {
  local shell_profile=""
  shell_profile="$(resolve_shell_path_file)"
  update_transaction_command capture-mutable \
    --state "${UPDATE_TRANSACTION_JOURNAL}" \
    --location "${LOCATION_FILE}" \
    --cli-shim "${CLI_SHIM}" \
    --user-cli-shim "${USER_CLI_SHIM}" \
    --desktop-link "${DESKTOP_DIARY_LINK}" \
    --shell-profile "${shell_profile}"
  UPDATE_MUTABLE_STATE_CAPTURED=1
}

commit_update_transaction() {
  update_transaction_command commit --state "${UPDATE_TRANSACTION_JOURNAL}"
  UPDATE_COMMITTED=1
  UPDATE_TRANSACTION_ACTIVE=0
}

run_guarded_update_transaction() {
  if [[ "$DRY_RUN" == "1" ]]; then
    stage_runtime_source
    if [[ "$SOURCE_ONLY" != "1" ]]; then
      run_cmd "${PYTHON_BIN}" -m venv "${RUNTIME_HOME}/app/venvs/<candidate-id>"
      run_cmd "${RUNTIME_HOME}/app/venvs/<candidate-id>/bin/python" -m pip install "${STAGED_RELEASE_TARGET:-${DEPLOY_SOURCE_ROOT}}"
      run_runtime_dependency_gate
    fi
    return 0
  fi
  if [[ -n "$LLM_API_KEY_VALUE" ]]; then
    print -r -- "credential rotation is not part of an atomic upgrade; update the model key after the upgrade succeeds" >&2
    return 2
  fi
  begin_update_transaction
  # ZERR and signal traps are scoped to this outer transaction driver. This
  # avoids changing normal installer control flow while still covering every
  # non-zero post-begin phase under zsh ERR_EXIT semantics.
  trap 'update_exit_handler $?' ZERR
  trap 'update_exit_handler 130' INT
  trap 'update_exit_handler 143' TERM
  trap 'update_exit_handler 129' HUP
  prepare_update_validation_runtime
  maybe_fail_update_phase prior-captured
  stage_runtime_source
  record_update_candidate source "${STAGED_RELEASE_TARGET}"
  update_transaction_command verify-migration-compatibility --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase migration-compatibility-verified
  if [[ "$SOURCE_ONLY" == "1" ]]; then
    run_source_only_candidate_gate
  else
    stage_update_candidate_venv
  fi
  clean_staged_candidate_build_artifacts
  update_transaction_command cleanup-validation-runtime --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase source-staged
  maybe_fail_update_phase payload-scanned
  update_transaction_command stop --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase services-stopped
  capture_update_mutable_state
  update_transaction_command normalize-service-plists --state "${UPDATE_TRANSACTION_JOURNAL}"
  update_transaction_command promote --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase source-promoted
  if [[ "$SOURCE_ONLY" != "1" ]]; then
    maybe_fail_update_phase venv-promoted
  fi
  update_transaction_command restore-services --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase services-restored
  if [[ "$SOURCE_ONLY" != "1" ]]; then
    maybe_fail_update_phase candidate-doctor-started
    run_update_candidate_doctor
    maybe_fail_update_phase candidate-doctor-passed
  fi
  update_transaction_command verify --state "${UPDATE_TRANSACTION_JOURNAL}"
  maybe_fail_update_phase candidate-verified
  commit_update_transaction
}

print_useful_commands() {
  print -r -- "$(installer_text useful_commands)"
  print -r -- "  open-nova onboarding runtime-status --runtime \"${RUNTIME_HOME}\""
  print -r -- "  open-nova doctor --installer --runtime \"${RUNTIME_HOME}\""
  print -r -- "  open-nova doctor --pipeline --runtime \"${RUNTIME_HOME}\""
  print -r -- "  open-nova doctor --scheduler --runtime \"${RUNTIME_HOME}\""
  if [[ "$ENABLE_RAG" == "1" ]]; then
    print -r -- "  open-nova doctor --rag --runtime \"${RUNTIME_HOME}\""
  fi
  print -r -- "  open-nova onboarding rollback-plan --runtime \"${RUNTIME_HOME}\""
  if [[ "$NO_DASHBOARD_SERVER" != "1" ]]; then
    print -r -- "  open-nova dashboard restart"
  fi
}

summary_line() {
  local line_status="$1"
  local label="$2"
  local detail="$3"
  local mark="[ok]"
  local color="$TTY_BLUE"
  case "$line_status" in
    ok)
      mark="✓"
      color="$TTY_BLUE"
      ;;
    warn)
      mark="!"
      color="$TTY_YELLOW"
      ;;
    error)
      mark="x"
      color="$TTY_RED"
      ;;
  esac
  print -r -- "  ${color}${mark}${TTY_RESET} ${label}: ${detail}"
}

effective_dashboard_url() {
  if [[ -f "${RUNTIME_HOME}/config/settings.json" ]]; then
    local python_for_settings="${VENV_PY:-$PYTHON_BIN}"
    if [[ -x "$python_for_settings" ]]; then
      local configured_url=""
      configured_url="$("$python_for_settings" - "${RUNTIME_HOME}/config/settings.json" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
dashboard = settings.get("dashboard") if isinstance(settings.get("dashboard"), dict) else {}
host = str(dashboard.get("host") or "127.0.0.1")
port = int(dashboard.get("port") or 3036)
print(f"http://{host}:{port}/dashboard")
PY
)"
      if [[ -n "$configured_url" ]]; then
        print -r -- "$configured_url"
        return 0
      fi
    fi
  fi
  print -r -- "http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/dashboard"
}

effective_llm_summary() {
  if [[ -f "${RUNTIME_HOME}/config/settings.json" ]]; then
    local python_for_settings="${VENV_PY:-$PYTHON_BIN}"
    if [[ -x "$python_for_settings" ]]; then
      local configured_summary=""
      configured_summary="$("$python_for_settings" - "${RUNTIME_HOME}/config/settings.json" <<'PY' 2>/dev/null || true
import json
import re
import sys
from pathlib import Path

settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
features = settings.get("features") if isinstance(settings.get("features"), dict) else {}
if features.get("llmGeneration") is False:
    print("warn\tdisabled")
    raise SystemExit
provider = settings.get("llmProvider") if isinstance(settings.get("llmProvider"), dict) else {}
mode = str(provider.get("mode") or "custom")
provider_id = str(provider.get("provider") or provider.get("presetProvider") or "custom")
model = str(provider.get("model") or "")
endpoint = str(provider.get("endpoint") or "")
api_key_env = str(provider.get("apiKeyEnv") or "LLM_API_KEY")
safe_key_env = api_key_env if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", api_key_env) else "<redacted-invalid-env>"
secret_ref = provider.get("secretRef") if isinstance(provider.get("secretRef"), dict) else {}
secret_backend = str(secret_ref.get("backend") or "").strip()
secret_suffix = f"; key store {secret_backend}" if secret_backend else f"; key env {safe_key_env}"
if endpoint and model:
    status = "warn" if secret_backend == "memory" else "ok"
    if secret_backend == "memory":
        secret_suffix += " (not pipeline-visible)"
    print(f"{status}\t{mode}/{provider_id}; model {model}{secret_suffix}")
else:
    print(f"warn\t{mode}/{provider_id}; model {model or 'unset'}; endpoint {endpoint or 'unset'}; key env {safe_key_env}")
PY
)"
      if [[ -n "$configured_summary" ]]; then
        print -r -- "$configured_summary"
        return 0
      fi
    fi
  fi
  if [[ "$ENABLE_LLM_GENERATION" != "1" ]]; then
    print -r -- "warn	disabled"
  elif [[ -n "$LLM_ENDPOINT" && -n "$LLM_MODEL" ]]; then
    print -r -- "ok	${LLM_PROVIDER_MODE}/${LLM_PROVIDER}; model ${LLM_MODEL}; key env $(safe_env_var_label "$LLM_API_KEY_ENV")"
  else
    print -r -- "warn	${LLM_PROVIDER_MODE}/${LLM_PROVIDER}; model ${LLM_MODEL:-unset}; endpoint ${LLM_ENDPOINT:-unset}; key env $(safe_env_var_label "$LLM_API_KEY_ENV")"
  fi
}

effective_external_tools_summary() {
  if [[ -f "${RUNTIME_HOME}/config/settings.json" ]]; then
    local python_for_settings="${VENV_PY:-$PYTHON_BIN}"
    if [[ -x "$python_for_settings" ]]; then
      local configured_tools=""
      configured_tools="$("$python_for_settings" - "${RUNTIME_HOME}/config/settings.json" <<'PY' 2>/dev/null || true
import json
import sys
from pathlib import Path

settings = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
external = settings.get("externalTools") if isinstance(settings.get("externalTools"), dict) else {}
selected = external.get("installerSelectedTools") if isinstance(external.get("installerSelectedTools"), list) else []
items = []
for item in selected:
    if not isinstance(item, dict):
        continue
    name = str(item.get("name") or item.get("key") or "").strip()
    path = str(item.get("path") or "").strip()
    if name and path:
        items.append(f"{name}={path}")
if not items:
    fallback_labels = {
        "openclaw": "OpenClaw",
        "claudeCode": "Claude Code",
        "codex": "Codex",
        "geminiCli": "Gemini CLI",
        "hermes": "Hermes",
    }
    for key, label in fallback_labels.items():
        value = external.get(key)
        if isinstance(value, dict) and value.get("home"):
            items.append(f"{label}={value['home']}")
print(", ".join(items) if items else "none")
PY
)"
      if [[ -n "$configured_tools" ]]; then
        print -r -- "$configured_tools"
        return 0
      fi
    fi
  fi
  format_selected_external_tools
}

print_install_summary() {
  local dashboard_detail=""
  local scheduler_detail=""
  local llm_detail=""
  local llm_status=""
  local rag_detail=""
  local dependency_detail=""

  print -r -- ""
  print -r -- "$(installer_text install_summary)"
  summary_line ok "CLI" "$([[ "$DRY_RUN" == "1" ]] && print "planned at ${CLI_SHIM}" || print "installed at ${CLI_SHIM}")"
  summary_line ok "foundation" "runtime source ${DEPLOY_SOURCE_ROOT}"
  summary_line ok "settings" "runtime ${RUNTIME_HOME}; location pointer ${LOCATION_FILE}"
  summary_line ok "diary artifacts" "${DIARY_OUTPUT_DIR}"

  if [[ "$NO_DASHBOARD_SERVER" == "1" ]]; then
    dashboard_detail="Dashboard UI installed; server service skipped"
    summary_line warn "Dashboard" "$dashboard_detail"
  else
    dashboard_detail="server enabled at $(effective_dashboard_url)"
    summary_line ok "Dashboard" "$dashboard_detail"
  fi

  if [[ "$PLATFORM" == "Darwin" && "$NO_SCHEDULER" != "1" ]]; then
    scheduler_detail="managed launchd jobs enabled"
    summary_line ok "scheduler" "$scheduler_detail"
  elif [[ "$NO_SCHEDULER" == "1" ]]; then
    scheduler_detail="disabled by option"
    summary_line warn "scheduler" "$scheduler_detail"
  else
    scheduler_detail="skipped on ${PLATFORM}"
    summary_line warn "scheduler" "$scheduler_detail"
  fi

  summary_line ok "Nova-Task" "$([[ "$ENABLE_NOVA_TASK" == "1" ]] && print enabled || print disabled)"

  IFS=$'\t' read -r llm_status llm_detail <<<"$(effective_llm_summary)"
  summary_line "${llm_status:-warn}" "LLM generation" "${llm_detail:-unknown}"

  if [[ "$ENABLE_RAG" != "1" ]]; then
    summary_line warn "nova-RAG" "not installed now"
  elif [[ "$RAG_EMBEDDING_MODE" == "local" ]]; then
    rag_detail="local embeddings; model ${RAG_LOCAL_MODEL}; dimension ${RAG_LOCAL_DIMENSION:-unset}"
    summary_line ok "nova-RAG" "$rag_detail"
  else
    rag_detail="cloud embeddings; provider ${RAG_CLOUD_PROVIDER}; model ${RAG_CLOUD_MODEL:-unset}; dimension ${RAG_CLOUD_DIMENSION:-unset}"
    summary_line ok "nova-RAG" "$rag_detail"
  fi

  summary_line ok "external tools" "$(effective_external_tools_summary)"
  if [[ "$DRY_RUN" != "1" ]]; then
    summary_line ok "installer log" "${INSTALLER_LOG_FILE}"
  fi

  dependency_detail="Dashboard API dependencies and static assets"
  if [[ "$ENABLE_RAG" == "1" && "$RAG_EMBEDDING_MODE" == "local" ]]; then
    dependency_detail="${dependency_detail}; nova-RAG local embedding dependencies"
  fi
  summary_line ok "dependency gate" "$([[ "$DRY_RUN" == "1" ]] && print "planned: ${dependency_detail}" || print "verified: ${dependency_detail}")"
}

run_wizard() {
  if [[ ! -r /dev/tty ]]; then
    print -r -- "Interactive wizard requires /dev/tty. Use --no-wizard for non-interactive runs." >&2
    exit 2
  fi
  local default_diary_output=""
  local default_reports_output=""
  local default_snapshots_output=""
  local default_archives_output=""

  if ! prompt_yes_no "$(installer_text welcome)" "yes"; then
    log "$(installer_text welcome_cancelled)"
    exit 0
  fi
  WIZARD_CONFIRMED=1
  if [[ "$DRY_RUN" == "1" ]]; then
    SUMMARY_ONLY=1
  fi

  if [[ "$LANGUAGE_SET" != "1" ]]; then
    prompt_language_profile
  else
    apply_language_profile
  fi

  wizard_core_dependency_gate
  if [[ "$ENABLE_LLM_GENERATION" == "1" && "$LLM_SET" != "1" ]]; then
    prompt_llm_provider_from_catalog
  fi
  prompt_external_tools
  prompt_rag_choice

  ENABLE_DASHBOARD=1
  if [[ "$PLATFORM" == "Darwin" && "$NO_SCHEDULER_SET" != "1" ]]; then
    NO_SCHEDULER=0
  elif [[ "$PLATFORM" != "Darwin" ]]; then
    NO_SCHEDULER=1
  fi
  ENABLE_NOVA_TASK=1

  default_diary_output="${RUNTIME_HOME}/artifacts/diary"
  default_reports_output="${RUNTIME_HOME}/artifacts/reports"
  default_snapshots_output="${RUNTIME_HOME}/snapshots"
  default_archives_output="${RUNTIME_HOME}/sources/archives"
  DIARY_OUTPUT_DIR="${DIARY_OUTPUT_DIR:-$default_diary_output}"
  REPORTS_OUTPUT_DIR="${REPORTS_OUTPUT_DIR:-$default_reports_output}"
  SNAPSHOTS_OUTPUT_DIR="${SNAPSHOTS_OUTPUT_DIR:-$default_snapshots_output}"
  ARCHIVES_OUTPUT_DIR="${ARCHIVES_OUTPUT_DIR:-$default_archives_output}"

  if [[ "$ENABLE_RAG" == "1" ]]; then
    if [[ "$RAG_EMBEDDING_MODE" == "local" ]]; then
      prompt_rag_local_model
      if [[ "$EMBEDDING_SERVER_SET" != "1" ]]; then
        DEPLOY_EMBEDDING_SERVER=1
      fi
    elif [[ "$RAG_EMBEDDING_MODE" == "cloud" ]]; then
      DEPLOY_EMBEDDING_SERVER=0
      RAG_CLOUD_PROVIDER="$(prompt_line "$(installer_text cloud_provider)" "$RAG_CLOUD_PROVIDER")"
      RAG_CLOUD_ENDPOINT="$(prompt_line "$(installer_text cloud_endpoint)" "$RAG_CLOUD_ENDPOINT")"
      RAG_CLOUD_MODEL="$(prompt_line "$(installer_text cloud_model)" "$RAG_CLOUD_MODEL")"
      RAG_CLOUD_DIMENSION="$(prompt_line "$(installer_text cloud_dimension)" "$RAG_CLOUD_DIMENSION")"
      RAG_CLOUD_API_KEY_ENV="$(prompt_line "$(installer_text cloud_key_env)" "$RAG_CLOUD_API_KEY_ENV")"
    fi
    wizard_rag_dependency_gate
    if [[ -n "$SELECTED_EXTERNAL_TOOLS" ]]; then
      ENABLE_SKILL_REGISTRATION=1
    fi
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      RUNTIME_HOME="$2"
      RUNTIME_SET=1
      shift 2
      ;;
    --diary-output)
      DIARY_OUTPUT_DIR="$2"
      DIARY_OUTPUT_SET=1
      shift 2
      ;;
    --desktop-diary-link)
      DESKTOP_DIARY_LINK="$2"
      CREATE_DESKTOP_DIARY_LINK=1
      DESKTOP_DIARY_LINK_SET=1
      shift 2
      ;;
    --no-desktop-diary-link)
      CREATE_DESKTOP_DIARY_LINK=0
      DESKTOP_DIARY_LINK_SET=1
      shift
      ;;
    --no-shell-path)
      ENABLE_SHELL_PATH=0
      SHELL_PATH_SET=1
      shift
      ;;
    --shell-path-file)
      SHELL_PATH_FILE="$2"
      SHELL_PATH_SET=1
      shift 2
      ;;
    --reports-output)
      REPORTS_OUTPUT_DIR="$2"
      REPORTS_OUTPUT_SET=1
      shift 2
      ;;
    --snapshots-output)
      SNAPSHOTS_OUTPUT_DIR="$2"
      SNAPSHOTS_OUTPUT_SET=1
      shift 2
      ;;
    --archives-output)
      ARCHIVES_OUTPUT_DIR="$2"
      ARCHIVES_OUTPUT_SET=1
      shift 2
      ;;
    --source-root)
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --python)
      PYTHON_BIN="$2"
      PYTHON_SET=1
      shift 2
      ;;
    --no-python-auto-install)
      PYTHON_AUTO_INSTALL=0
      shift
      ;;
    --no-scheduler)
      NO_SCHEDULER=1
      NO_SCHEDULER_SET=1
      shift
      ;;
    --no-dashboard)
      print -r -- "--no-dashboard is no longer supported because Dashboard is required. Use --no-dashboard-server to skip service installation." >&2
      exit 2
      ;;
    --no-dashboard-server)
      NO_DASHBOARD_SERVER=1
      NO_DASHBOARD_SERVER_SET=1
      shift
      ;;
    --dashboard-port)
      DASHBOARD_PORT="$2"
      DASHBOARD_PORT_SET=1
      shift 2
      ;;
    --dashboard-host)
      DASHBOARD_HOST="$2"
      DASHBOARD_HOST_SET=1
      shift 2
      ;;
    --dashboard-port-auto)
      DASHBOARD_PORT_AUTO=1
      shift
      ;;
    --no-dashboard-port-auto)
      DASHBOARD_PORT_AUTO=0
      shift
      ;;
    --enable-rag)
      ENABLE_RAG=1
      RAG_SET=1
      shift
      ;;
    --register-rag-skills)
      ENABLE_SKILL_REGISTRATION=1
      shift
      ;;
    --enable-dev-test)
      ENABLE_DEV_TEST=1
      shift
      ;;
    --rag-embedding-mode)
      RAG_EMBEDDING_MODE="$2"
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-local-model)
      RAG_LOCAL_MODEL="$2"
      RAG_LOCAL_DIMENSION="$(rag_local_model_dimension "$RAG_LOCAL_MODEL")"
      RAG_LOCAL_MODEL_SET=1
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="local"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-local-dimension)
      RAG_LOCAL_DIMENSION="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="local"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --deploy-embedding-server)
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="local"
      DEPLOY_EMBEDDING_SERVER=1
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      EMBEDDING_SERVER_SET=1
      shift
      ;;
    --no-deploy-embedding-server)
      DEPLOY_EMBEDDING_SERVER=0
      EMBEDDING_SERVER_SET=1
      shift
      ;;
    --llm-provider)
      LLM_PROVIDER="$2"
      LLM_PROVIDER_MODE="preset"
      LLM_SET=1
      shift 2
      ;;
    --llm-endpoint)
      LLM_ENDPOINT="$2"
      LLM_SET=1
      shift 2
      ;;
    --llm-model)
      LLM_MODEL="$2"
      LLM_SET=1
      shift 2
      ;;
    --llm-api-key-env)
      LLM_API_KEY_ENV="$2"
      LLM_SET=1
      shift 2
      ;;
    --rag-cloud-provider)
      RAG_CLOUD_PROVIDER="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-cloud-endpoint)
      RAG_CLOUD_ENDPOINT="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-cloud-model)
      RAG_CLOUD_MODEL="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-cloud-dimension)
      RAG_CLOUD_DIMENSION="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --rag-cloud-api-key-env)
      RAG_CLOUD_API_KEY_ENV="$2"
      ENABLE_RAG=1
      RAG_EMBEDDING_MODE="cloud"
      RAG_SET=1
      RAG_EMBEDDING_MODE_SET=1
      shift 2
      ;;
    --language)
      INSTALL_LANGUAGE="$2"
      LANGUAGE_SET=1
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --summary-only)
      SUMMARY_ONLY=1
      shift
      ;;
    --upgrade)
      UPGRADE=1
      shift
      ;;
    --source-only|--sync-runtime-source)
      SOURCE_ONLY=1
      UPGRADE=1
      WIZARD_MODE=0
      shift
      ;;
    --wizard)
      WIZARD_MODE=1
      shift
      ;;
    --no-wizard)
      WIZARD_MODE=0
      shift
      ;;
    --yes)
      YES=1
      WIZARD_MODE=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      print -r -- "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

SOURCE_ROOT="${SOURCE_ROOT:A}"
if [[ ! -f "${SOURCE_ROOT}/pyproject.toml" ]]; then
  print -r -- "pyproject.toml not found under source root: ${SOURCE_ROOT}" >&2
  exit 2
fi
RUNTIME_HOME="${RUNTIME_HOME:A}"
require_fresh_runtime_empty || exit 2
resolve_python_bin || true

if wizard_enabled; then
  run_wizard
fi
apply_language_profile
LANGUAGE_SELECTED=1
if ! validate_llm_api_key_env; then
  exit 2
fi

RUNTIME_HOME="${RUNTIME_HOME:A}"
DIARY_OUTPUT_DIR="${DIARY_OUTPUT_DIR:-${RUNTIME_HOME}/artifacts/diary}"
DESKTOP_DIARY_LINK="${DESKTOP_DIARY_LINK:A}"
REPORTS_OUTPUT_DIR="${REPORTS_OUTPUT_DIR:-${RUNTIME_HOME}/artifacts/reports}"
SNAPSHOTS_OUTPUT_DIR="${SNAPSHOTS_OUTPUT_DIR:-${RUNTIME_HOME}/snapshots}"
ARCHIVES_OUTPUT_DIR="${ARCHIVES_OUTPUT_DIR:-${RUNTIME_HOME}/sources/archives}"
DIARY_OUTPUT_DIR="${DIARY_OUTPUT_DIR:A}"
REPORTS_OUTPUT_DIR="${REPORTS_OUTPUT_DIR:A}"
SNAPSHOTS_OUTPUT_DIR="${SNAPSHOTS_OUTPUT_DIR:A}"
ARCHIVES_OUTPUT_DIR="${ARCHIVES_OUTPUT_DIR:A}"
VENV_DIR="${RUNTIME_HOME}/.venv"
VENV_PY="${VENV_DIR}/bin/python"
CLI_SHIM="${RUNTIME_HOME}/bin/open-nova"
DEPLOY_SOURCE_ROOT="${RUNTIME_HOME}/app/source"
INSTALLER_LOG_FILE="${RUNTIME_HOME}/state/logs/installer-v2.log"
LOCATION_FILE="${NOVA_LOCATION_FILE:-$HOME/.config/open-nova/location.json}"
if [[ "$UPGRADE" == "1" && ! -d "$RUNTIME_HOME" && "$DRY_RUN" != "1" ]]; then
  print -r -- "--upgrade requires an existing runtime: ${RUNTIME_HOME}" >&2
  exit 2
fi
select_dashboard_port
INSTALL_SPEC="${DEPLOY_SOURCE_ROOT}"
INSTALL_EXTRAS=()
INSTALL_EXTRAS+=("dashboard")
if [[ "$ENABLE_RAG" == "1" ]]; then
  INSTALL_EXTRAS+=("rag-server")
fi
if [[ "$ENABLE_RAG" == "1" && "$RAG_EMBEDDING_MODE" == "local" ]]; then
  INSTALL_EXTRAS+=("rag-local")
  if [[ "$EMBEDDING_SERVER_SET" != "1" ]]; then
    DEPLOY_EMBEDDING_SERVER=1
  fi
fi
if [[ "$ENABLE_DEV_TEST" == "1" ]]; then
  INSTALL_EXTRAS+=("dev-test")
fi
if [[ "${#INSTALL_EXTRAS[@]}" -gt 0 ]]; then
  INSTALL_SPEC="${DEPLOY_SOURCE_ROOT}[${(j:,:)INSTALL_EXTRAS}]"
fi

case "$RAG_EMBEDDING_MODE" in
  local|cloud) ;;
  *)
    print -r -- "--rag-embedding-mode must be local or cloud" >&2
    exit 2
    ;;
esac

require_fresh_runtime_empty || exit 2
log "Open Nova installer v2"
print_installer_data_notice
log "mode: $([[ "$SOURCE_ONLY" == "1" ]] && print source-only || ([[ "$UPGRADE" == "1" ]] && print upgrade || print install))"
log "source root: ${SOURCE_ROOT}"
log "runtime: ${RUNTIME_HOME}"
log "language: ${INSTALL_LANGUAGE} (pipeline=${PIPELINE_LANGUAGE_PROFILE}, diarySchema=${PIPELINE_DIARY_SCHEMA_VERSION}, promptPayload=${PIPELINE_PROMPT_PAYLOAD_PROFILE})"
log "deployed runtime source: ${DEPLOY_SOURCE_ROOT}"
log "generated diary output: ${DIARY_OUTPUT_DIR}"
log "Desktop diary shortcut: $([[ "$CREATE_DESKTOP_DIARY_LINK" == "1" ]] && print "${DESKTOP_DIARY_LINK}" || print disabled)"
log "selected external tools: $(format_selected_external_tools)"
if [[ "$ENABLE_RAG" == "1" && "$ENABLE_SKILL_REGISTRATION" == "1" ]]; then
  log "skill registration: installer writes missing nova-RAG skills for selected external tools; existing skills are preserved"
fi
log "reports output: ${REPORTS_OUTPUT_DIR}"
log "snapshots output: ${SNAPSHOTS_OUTPUT_DIR}"
log "archives/intermediate output: ${ARCHIVES_OUTPUT_DIR}"
log "location pointer: ${LOCATION_FILE}"
log "install dependency spec: ${INSTALL_SPEC}"
log "dashboard UI: $([[ "$ENABLE_DASHBOARD" == "1" ]] && print enabled || print disabled)"
log "SSE server: $([[ "$NO_DASHBOARD_SERVER" == "1" ]] && print disabled || print enabled)"
if [[ "$NO_DASHBOARD_SERVER" != "1" ]]; then
  log "Dashboard URL: http://${DASHBOARD_HOST}:${DASHBOARD_PORT}/dashboard"
fi
log "scheduler: $([[ "$NO_SCHEDULER" == "1" ]] && print disabled || print default)"
log "Nova-Task: $([[ "$ENABLE_NOVA_TASK" == "1" ]] && print enabled || print disabled)"
log "dev-test: $([[ "$ENABLE_DEV_TEST" == "1" ]] && print enabled || print disabled)"
log "LLM generation: $([[ "$ENABLE_LLM_GENERATION" == "1" ]] && print enabled || print disabled)"
if [[ "$ENABLE_LLM_GENERATION" == "1" ]]; then
  log "LLM provider: ${LLM_PROVIDER_MODE}/${LLM_PROVIDER}; model: ${LLM_MODEL:-unset}; endpoint: ${LLM_ENDPOINT:-unset}; api key env: $(safe_env_var_label "$LLM_API_KEY_ENV")"
fi
log "nova-RAG: $([[ "$ENABLE_RAG" == "1" ]] && print enabled || print disabled)"
if [[ "$ENABLE_RAG" == "1" ]]; then
  log "nova-RAG embedding mode: ${RAG_EMBEDDING_MODE}"
  if [[ "$RAG_EMBEDDING_MODE" == "local" ]]; then
    log "nova-RAG local embedding: model=${RAG_LOCAL_MODEL}; dimension=${RAG_LOCAL_DIMENSION:-unset}"
  elif [[ "$RAG_EMBEDDING_MODE" == "cloud" ]]; then
    log "nova-RAG cloud embedding: provider=${RAG_CLOUD_PROVIDER}; model=${RAG_CLOUD_MODEL:-unset}; endpoint=${RAG_CLOUD_ENDPOINT:-unset}; dimension=${RAG_CLOUD_DIMENSION:-unset}; api key env=$(safe_env_var_label "$RAG_CLOUD_API_KEY_ENV")"
  fi
fi

if [[ "$NO_DASHBOARD_SERVER" == "1" ]]; then
  warn "SSE server disabled: only the Dashboard UI realtime overview and task board pages will be unavailable."
  warn "Static snapshot pages such as AI Assets, other Dashboard pages, and Nova-Task remain available."
fi

run_installer_preflight

if [[ "$DRY_RUN" == "1" ]]; then
  log "dry-run only; no files will be written and no commands will be executed"
elif wizard_enabled && [[ "$YES" != "1" && "$WIZARD_CONFIRMED" != "1" ]]; then
  if [[ "$UPGRADE" == "1" ]]; then
    if ! prompt_yes_no "$(installer_text proceed_upgrade)" "no"; then
      log "$(installer_text upgrade_cancelled)"
      exit 0
    fi
  elif ! prompt_yes_no "$(installer_text proceed_install)" "no"; then
    log "$(installer_text install_cancelled)"
    exit 0
  fi
fi

if [[ "$UPGRADE" == "1" ]]; then
  run_guarded_update_transaction
  create_cli_shim
  if [[ "$SOURCE_ONLY" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
      log "source-only dry-run complete; no source pointer, settings, dependencies, LaunchAgents, or RAG manifests were changed"
    else
      log "Open Nova runtime source-only sync complete; the prior managed service state was restored"
      log "Settings, dependencies, Keychain references, and user data were not changed; legacy Python LaunchAgents may receive cache-suppression environment metadata"
    fi
    exit 0
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    COMPLETION_TEXT="Open Nova installer v2 upgrade dry-run complete."
  else
    COMPLETION_TEXT="Open Nova installer v2 atomic upgrade complete."
    log "Upgrade preserved Settings, runtime manifest, location pointer, live SQLite state without rewind, service loaded/running state, and configured Dashboard port; legacy Python LaunchAgents were transactionally normalized when required"
    log "Credential rotation, external Skill registration, and background embedding deployment were not performed inside the update transaction"
  fi
  print_install_summary
  print -r -- ""
  print -r -- "${COMPLETION_TEXT}"
  print -r -- ""
  print_useful_commands
  exit 0
fi

run_cmd mkdir -p "${RUNTIME_HOME}"
run_cmd mkdir -p "${DIARY_OUTPUT_DIR}" "${REPORTS_OUTPUT_DIR}" "${SNAPSHOTS_OUTPUT_DIR}" "${ARCHIVES_OUTPUT_DIR}"
create_desktop_diary_link
deploy_runtime_source
create_fresh_runtime_venv
run_cmd "${VENV_PY}" -m pip install --upgrade pip
run_cmd "${VENV_PY}" -m pip install "${INSTALL_SPEC}"
run_runtime_dependency_gate
create_cli_shim
ensure_cli_on_shell_path

log "Applying runtime bootstrap and active runtime pointer"
export_runtime_environment
runtime_apply_args=(
  -m data_foundation.cli
  onboarding runtime-apply
  --runtime "${RUNTIME_HOME}"
  --select-active-runtime
  --confirmation-text "APPLY OPEN NOVA ONBOARDING"
  --json
)
if [[ "$UPGRADE" != "1" || "$LANGUAGE_SET" == "1" ]]; then
  runtime_apply_args+=(--language "${INSTALL_LANGUAGE}")
fi
run_json_cmd "Runtime bootstrap apply" "${VENV_PY}" "${runtime_apply_args[@]}"
apply_installer_settings_overlay
run_external_rag_skill_registration_apply
store_installer_llm_api_key_secret

if [[ "$PLATFORM" == "Darwin" && "$NO_SCHEDULER" != "1" ]]; then
  log "Registering managed Open Nova scheduler LaunchAgents"
  if [[ "$UPGRADE" == "1" ]]; then
    run_json_cmd "Scheduler LaunchAgent plist write" \
      "${VENV_PY}" -m data_foundation.cli \
      onboarding apply \
      --scheduler-plist-apply \
      --runtime "${RUNTIME_HOME}" \
      --confirmation-text "WRITE OPEN NOVA LAUNCHAGENTS" \
      --json
    run_json_cmd "Scheduler LaunchAgent registration" \
      "${VENV_PY}" -m data_foundation.cli \
      onboarding apply \
      --scheduler-register-apply \
      --runtime "${RUNTIME_HOME}" \
      --confirmation-text "REGISTER OPEN NOVA SCHEDULER" \
      --json
  else
    run_optional_json_cmd "Scheduler LaunchAgent plist write" \
      "${VENV_PY}" -m data_foundation.cli \
      onboarding apply \
      --scheduler-plist-apply \
      --runtime "${RUNTIME_HOME}" \
      --confirmation-text "WRITE OPEN NOVA LAUNCHAGENTS" \
      --json
    run_optional_json_cmd "Scheduler LaunchAgent registration" \
      "${VENV_PY}" -m data_foundation.cli \
      onboarding apply \
      --scheduler-register-apply \
      --runtime "${RUNTIME_HOME}" \
      --confirmation-text "REGISTER OPEN NOVA SCHEDULER" \
      --json
  fi
elif [[ "$NO_SCHEDULER" == "1" ]]; then
  log "Scheduler registration skipped by --no-scheduler"
else
  log "Scheduler registration skipped on unsupported platform: ${PLATFORM}"
fi

if [[ "$NO_DASHBOARD_SERVER" != "1" ]]; then
  if [[ "$PLATFORM" == "Darwin" ]]; then
    log "Installing SSE server LaunchAgent service"
    run_dashboard_service_launch_agent_apply
  else
    log "SSE server service registration skipped on unsupported platform: ${PLATFORM}"
  fi
fi

if [[ "$ENABLE_RAG" == "1" ]]; then
  if [[ "$PLATFORM" == "Darwin" ]]; then
    log "Installing nova-RAG server LaunchAgent service"
    run_rag_service_launch_agent_apply
  else
    log "nova-RAG server LaunchAgent registration skipped on unsupported platform: ${PLATFORM}"
  fi
fi

if [[ "$DEPLOY_EMBEDDING_SERVER" == "1" && "$PLATFORM" == "Darwin" && "$ENABLE_RAG" == "1" ]]; then
  log "nova-RAG embedding server lifecycle is managed by its LaunchAgent; direct background start skipped"
  progress_start "nova-RAG LaunchAgent owns the embedding server; direct background start skipped"
  progress_ok "nova-RAG LaunchAgent owns the embedding server; direct background start skipped"
elif [[ "$DEPLOY_EMBEDDING_SERVER" == "1" ]]; then
  JOB_DIR="${RUNTIME_HOME}/state/jobs"
  JOB_SCRIPT="${JOB_DIR}/deploy-embedding-server.sh"
  JOB_LOG="${RUNTIME_HOME}/state/logs/embedding-server-deploy.log"
  log "Queueing background embedding server deployment"
  if [[ "$DRY_RUN" == "1" ]]; then
    progress_start "Queueing background embedding server deployment"
    progress_ok "Queueing background embedding server deployment"
  else
    mkdir -p "${JOB_DIR}" "${RUNTIME_HOME}/state/logs"
    cat > "${JOB_SCRIPT}" <<EOF
#!/usr/bin/env zsh
set -euo pipefail
export NOVA_HOME="${RUNTIME_HOME}"
export NOVA_LOCATION_FILE="${LOCATION_FILE}"
export PYTHONPATH="${DEPLOY_SOURCE_ROOT}:${DEPLOY_SOURCE_ROOT}/src"
"${VENV_PY}" - <<'PY'
from agentic_rag.rag_server_lifecycle import start_rag_server
print(start_rag_server(requested_by="installer-v2", wait_timeout_seconds=1.0))
PY
EOF
    chmod +x "${JOB_SCRIPT}"
    nohup "${JOB_SCRIPT}" > "${JOB_LOG}" 2>&1 &
    print -r -- "$!" > "${JOB_DIR}/deploy-embedding-server.pid"
  fi
fi

run_post_install_doctor
cleanup_runtime_source_artifacts

if [[ "$DRY_RUN" == "1" ]]; then
  COMPLETION_TEXT="Open Nova installer v2 dry-run complete."
elif [[ "$UPGRADE" == "1" ]]; then
  COMPLETION_TEXT="Open Nova installer v2 upgrade complete."
else
  COMPLETION_TEXT="Open Nova installer v2 complete."
fi

if [[ "$SUMMARY_ONLY" == "1" && -t 1 && -r /dev/tty ]]; then
  clear_tty_menu
fi

print_install_summary

print -r -- ""
print -r -- "${COMPLETION_TEXT}"
print -r -- ""
print_useful_commands
