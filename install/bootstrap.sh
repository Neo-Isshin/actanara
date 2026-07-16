#!/usr/bin/env zsh
# Keep the hosted bootstrap inside one compound command. If a streamed download
# is truncated, zsh cannot parse the closing `fi` and executes no prefix.
if true; then
set -euo pipefail

BOOTSTRAP_DIR="${0:A:h}"
DEFAULT_CACHE_ROOT="${NOVA_INSTALL_CACHE_ROOT:-$HOME/.cache/open-nova/installer}"
DEFAULT_SOURCE_URL="https://github.com/Neo-Isshin/open-nova.git"
DEFAULT_LATEST_RELEASE_API="https://api.github.com/repos/Neo-Isshin/open-nova/releases/latest"
SOURCE_ROOT="${NOVA_INSTALL_SOURCE_ROOT:-}"
SOURCE_URL="${NOVA_INSTALL_SOURCE_URL:-}"
SOURCE_REF="${NOVA_INSTALL_REF:-}"
GIT_BIN="${NOVA_INSTALL_GIT:-git}"
CURL_BIN="${NOVA_INSTALL_CURL:-curl}"
PLUTIL_BIN="${NOVA_INSTALL_PLUTIL:-/usr/bin/plutil}"
ZSH_BIN="${NOVA_INSTALL_ZSH:-${ZSH_VERSION:+/bin/zsh}}"
DRY_RUN=0
OFFLINE=0
INSTALL_ARGS=()
CACHE_SOURCE=0
BOOTSTRAP_LOG_FILE=""
BOOTSTRAP_LANGUAGE="${NOVA_INSTALL_LANGUAGE:-zh-CN}"
SPARSE_PATHS=(
  "/pyproject.toml"
  "/MANIFEST.in"
  "/LICENSE"
  "/config.py"
  "/install"
  "/advanced"
  "/src"
)
OFFLINE_SOURCE_PATHS=(
  "pyproject.toml"
  "MANIFEST.in"
  "LICENSE"
  "config.py"
  "install"
  "advanced"
  "src"
)

usage() {
  cat <<'EOF'
Open Nova setup

Usage:
  install/bootstrap.sh [bootstrap-options] [-- installer-options]

Preparation options:
  --source-root PATH       Use an existing local copy of Open Nova.
  --source-url URL         Download Open Nova from this URL.
                          Default: https://github.com/Neo-Isshin/open-nova.git
  --ref VERSION           Use an exact 40- or 64-character version ID.
                          Omit it to use the latest stable release.
  --cache-root PATH        Download cache folder. Default: ~/.cache/open-nova/installer
  --git PATH              Git executable. Default: git
  --offline               Use local or previously downloaded files only.
  --dry-run               Preview preparation and setup without writing files.
  -h, --help              Show this help.

Options after -- are passed to the main setup.
EOF
}

bootstrap_text() {
  local key="$1"
  case "$BOOTSTRAP_LANGUAGE" in
    en|en-US|en_US)
      case "$key" in
        preparing_folder) print -r -- "Preparing the download folder" ;;
        downloading) print -r -- "Downloading Open Nova" ;;
        checking_updates) print -r -- "Checking the selected Open Nova version" ;;
        preparing_files) print -r -- "Preparing installation files" ;;
        latest_ready) print -r -- "Latest stable version selected" ;;
        cache_ready) print -r -- "Previously downloaded files are ready" ;;
        existing_ready) print -r -- "Existing Open Nova data will be kept and updated" ;;
        starting) print -r -- "Starting Open Nova setup" ;;
        starting_update) print -r -- "Starting Open Nova update" ;;
        step_failed) print -r -- "Could not prepare Open Nova. Run again with NOVA_INSTALL_VERBOSE=1 for details." ;;
        options_conflict) print -r -- "Some preparation options cannot be used together. Run with --help." ;;
        runtime_incomplete) print -r -- "This Open Nova folder is incomplete and cannot be updated safely." ;;
        service_unmatched) print -r -- "An Open Nova background service exists, but its installation folder could not be found." ;;
        location_invalid) print -r -- "The saved Open Nova location could not be read safely." ;;
        version_unavailable) print -r -- "The selected Open Nova version could not be verified." ;;
        version_invalid) print -r -- "Choose an exact Open Nova version ID." ;;
        offline_missing) print -r -- "The required Open Nova files are not available offline." ;;
        cache_mismatch) print -r -- "This download folder belongs to a different Open Nova source. Choose another folder." ;;
        git_missing) print -r -- "Git is required to download Open Nova." ;;
        files_missing) print -r -- "Required Open Nova installation files are missing." ;;
        shell_missing) print -r -- "zsh is required to start Open Nova setup." ;;
        option_value_missing) print -r -- "One setup option is missing its value. Run with --help." ;;
        *) print -r -- "$key" ;;
      esac
      ;;
    *)
      case "$key" in
        preparing_folder) print -r -- "准备下载文件夹" ;;
        downloading) print -r -- "下载 Open Nova" ;;
        checking_updates) print -r -- "检查所选 Open Nova 版本" ;;
        preparing_files) print -r -- "准备安装文件" ;;
        latest_ready) print -r -- "已选择最新稳定版本" ;;
        cache_ready) print -r -- "已准备此前下载的文件" ;;
        existing_ready) print -r -- "已保留现有 Open Nova 数据，将直接更新" ;;
        starting) print -r -- "启动 Open Nova 安装" ;;
        starting_update) print -r -- "启动 Open Nova 更新" ;;
        step_failed) print -r -- "未能准备 Open Nova，可设置 NOVA_INSTALL_VERBOSE=1 后重试以查看详情。" ;;
        options_conflict) print -r -- "部分准备选项不能同时使用，请通过 --help 查看用法。" ;;
        runtime_incomplete) print -r -- "此 Open Nova 文件夹不完整，无法安全更新。" ;;
        service_unmatched) print -r -- "检测到 Open Nova 后台服务，但未找到对应的安装文件夹。" ;;
        location_invalid) print -r -- "无法安全读取已保存的 Open Nova 位置。" ;;
        version_unavailable) print -r -- "未能确认所选 Open Nova 版本。" ;;
        version_invalid) print -r -- "请选择完整、准确的 Open Nova 版本 ID。" ;;
        offline_missing) print -r -- "离线状态下缺少所需 Open Nova 文件。" ;;
        cache_mismatch) print -r -- "此下载文件夹属于其他 Open Nova 来源，请选择另一文件夹。" ;;
        git_missing) print -r -- "下载 Open Nova 需要 Git。" ;;
        files_missing) print -r -- "缺少 Open Nova 安装所需文件。" ;;
        shell_missing) print -r -- "启动 Open Nova 安装需要 zsh。" ;;
        option_value_missing) print -r -- "一个安装选项缺少内容，请通过 --help 查看用法。" ;;
        *) print -r -- "$key" ;;
      esac
      ;;
  esac
}

bootstrap_command_label() {
  case "$*" in
    mkdir\ -p*) bootstrap_text preparing_folder ;;
    *" clone "*|clone\ *) bootstrap_text downloading ;;
    *" fetch "*|fetch\ *) bootstrap_text checking_updates ;;
    *) bootstrap_text preparing_files ;;
  esac
}

bootstrap_ok() {
  print -r -- "  ✓ $*"
}

bootstrap_fail() {
  print -r -- "  ✕ $(bootstrap_text step_failed)" >&2
}

bootstrap_problem() {
  local key="$1"
  local technical_message="$2"
  if [[ -n "$BOOTSTRAP_LOG_FILE" && -d "${BOOTSTRAP_LOG_FILE:h}" ]]; then
    print -r -- "ERROR: ${technical_message}" >> "$BOOTSTRAP_LOG_FILE"
  fi
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "ERROR: ${technical_message}" >&2
  else
    print -r -- "  ✕ $(bootstrap_text "$key")" >&2
  fi
}

prepare_bootstrap_log() {
  local cache_root="$1"
  /bin/chmod 700 "$cache_root"
  : > "$BOOTSTRAP_LOG_FILE"
  /bin/chmod 600 "$BOOTSTRAP_LOG_FILE"
}

log() {
  if [[ -n "$BOOTSTRAP_LOG_FILE" && -d "${BOOTSTRAP_LOG_FILE:h}" ]]; then
    print -r -- "==> $*" >> "$BOOTSTRAP_LOG_FILE"
  fi
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "==> $*"
  fi
}

run_cmd() {
  local label="$(bootstrap_command_label "$@")"
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "+ ${label}"
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    bootstrap_ok "$label"
    return 0
  fi
  local rc=0
  if [[ -n "$BOOTSTRAP_LOG_FILE" && -d "${BOOTSTRAP_LOG_FILE:h}" ]]; then
    print -r -- "## ${label}" >> "$BOOTSTRAP_LOG_FILE"
    "$@" >/dev/null 2>&1 || {
      rc=$?
      print -r -- "ERROR: step exited with status ${rc}" >> "$BOOTSTRAP_LOG_FILE"
      bootstrap_fail
      return "$rc"
    }
  else
    "$@" >/dev/null 2>&1 || {
      rc=$?
      bootstrap_fail
      return "$rc"
    }
  fi
  bootstrap_ok "$label"
}

git_exec() {
  if [[ "$OFFLINE" == "1" ]]; then
    # A cached partial clone may otherwise fetch a missing promisor object from
    # inside object inspection or checkout even though bootstrap never invokes
    # `git fetch`.
    # Git >=2.45 honors the no-lazy-fetch request; the empty protocol allowlist
    # is an independent older-Git-compatible transport barrier. Hooks and
    # user/system Git configuration are also excluded.
    GIT_NO_LAZY_FETCH=1 \
    GIT_ALLOW_PROTOCOL= \
    GIT_TERMINAL_PROMPT=0 \
    GIT_CONFIG_NOSYSTEM=1 \
    GIT_CONFIG_GLOBAL=/dev/null \
      "${GIT_BIN}" \
        -c protocol.allow=never \
        -c core.hooksPath=/dev/null \
        "$@"
    return $?
  fi
  "${GIT_BIN}" "$@"
}

run_git_cmd() {
  local label="$(bootstrap_command_label "$@")"
  if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
    print -r -- "+ ${label}"
  fi
  if [[ "$DRY_RUN" == "1" ]]; then
    bootstrap_ok "$label"
    return 0
  fi
  local rc=0
  if [[ -n "$BOOTSTRAP_LOG_FILE" && -d "${BOOTSTRAP_LOG_FILE:h}" ]]; then
    print -r -- "## ${label}" >> "$BOOTSTRAP_LOG_FILE"
    git_exec "$@" >/dev/null 2>&1 || {
      rc=$?
      print -r -- "ERROR: download step exited with status ${rc}" >> "$BOOTSTRAP_LOG_FILE"
      bootstrap_fail
      return "$rc"
    }
  else
    git_exec "$@" >/dev/null 2>&1 || {
      rc=$?
      bootstrap_fail
      return "$rc"
    }
  fi
  bootstrap_ok "$label"
}

configure_sparse_checkout() {
  local root="$1"
  if [[ "$CACHE_SOURCE" != "1" ]]; then
    return 0
  fi
  log "Configuring installer source sparse checkout"
  run_git_cmd -C "${root}" sparse-checkout init --no-cone
  run_git_cmd -C "${root}" sparse-checkout set "${SPARSE_PATHS[@]}"
}

installer_arg_present() {
  local expected="$1"
  local argument=""
  for argument in "${INSTALL_ARGS[@]}"; do
    if [[ "$argument" == "$expected" ]]; then
      return 0
    fi
  done
  return 1
}

verify_offline_source_cache() {
  local root="$1"
  local ref="$2"
  local resolved_object=""
  resolved_object="$(git_exec -C "${root}" rev-parse --verify "${ref}^{commit}" 2>/dev/null || true)"
  resolved_object="${resolved_object:l}"
  if [[ "$resolved_object" != "$ref" ]]; then
    bootstrap_problem version_unavailable "resolved source object does not match required commit ${ref}"
    return 2
  fi

  # `git archive` is intentionally not used here: on a promisor/partial clone,
  # Git may demand unrelated public-source blobs even when every object in the
  # installer sparse payload is already cached. Inspect only the selected tree
  # entries and then prove that each referenced blob is locally readable.
  # git_exec forbids lazy fetch and every transport for every probe.
  local required_path=""
  for required_path in "${OFFLINE_SOURCE_PATHS[@]}"; do
    if ! git_exec -C "${root}" cat-file -e "${ref}:${required_path}" 2>/dev/null; then
      bootstrap_problem offline_missing "offline source cache is incomplete for commit ${ref}"
      return 2
    fi
  done

  local inventory_file=""
  inventory_file="$(mktemp "${TMPDIR:-/tmp}/open-nova-offline-cache.XXXXXXXX")" || {
    bootstrap_problem offline_missing "unable to create a private offline source inventory"
    return 2
  }
  if ! git_exec -C "${root}" ls-tree -r -z --full-tree "${ref}" -- "${OFFLINE_SOURCE_PATHS[@]}" > "${inventory_file}"; then
    rm -f "${inventory_file}"
    bootstrap_problem offline_missing "offline source cache inventory is incomplete for commit ${ref}"
    return 2
  fi

  local row=""
  local metadata=""
  local file_mode=""
  local remainder=""
  local object_type=""
  local object_id=""
  local object_count=0
  local inventory_invalid=0
  while IFS= read -r -d $'\0' row; do
    metadata="${row%%$'\t'*}"
    if [[ "$metadata" == "$row" ]]; then
      inventory_invalid=1
      break
    fi
    file_mode="${metadata%% *}"
    remainder="${metadata#* }"
    object_type="${remainder%% *}"
    object_id="${remainder##* }"
    case "${file_mode}:${object_type}" in
      100644:blob|100755:blob|120000:blob)
        ;;
      *)
        inventory_invalid=1
        break
        ;;
    esac
    if ! is_full_commit_id "${object_id}" \
      || ! git_exec -C "${root}" cat-file blob "${object_id}" > /dev/null 2>&1; then
      inventory_invalid=1
      break
    fi
    object_count=$((object_count + 1))
  done < "${inventory_file}"
  rm -f "${inventory_file}"
  if [[ "$inventory_invalid" == "1" || "$object_count" -eq 0 ]]; then
    bootstrap_problem offline_missing "offline source cache content is incomplete for commit ${ref}"
    return 2
  fi
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    bootstrap_problem option_value_missing "${option} requires a value"
    exit 2
  fi
}

is_full_commit_id() {
  local value="$1"
  [[ "$value" =~ "^[[:xdigit:]]{40}$|^[[:xdigit:]]{64}$" ]]
}

extract_json_string() {
  local payload="$1"
  local field="$2"
  local value=""
  if [[ -x "$PLUTIL_BIN" ]]; then
    if value="$(print -rn -- "$payload" | "$PLUTIL_BIN" -extract "$field" raw -o - - 2>/dev/null)"; then
      [[ -n "$value" ]] || return 1
      print -rn -- "$value"
      return 0
    fi
  fi

  # Safe dependency-free fallback: accept one plain JSON string only. Escaped
  # values are deliberately rejected rather than decoded by shell code.
  local flattened=""
  local occurrences=""
  flattened="$(print -rn -- "$payload" | /usr/bin/tr '\r\n' '  ' 2>/dev/null)" || return 1
  occurrences="$(print -rn -- "$flattened" | /usr/bin/grep -o "\"${field}\"[[:space:]]*:" 2>/dev/null | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')" || return 1
  [[ "$occurrences" == "1" ]] || return 1
  value="$(print -rn -- "$flattened" | /usr/bin/sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\\([^\"\\\\]*\\)\".*/\\1/p")" || return 1
  [[ -n "$value" ]] || return 1
  print -rn -- "$value"
}

extract_json_boolean() {
  local payload="$1"
  local field="$2"
  local value=""
  if [[ -x "$PLUTIL_BIN" ]]; then
    if value="$(print -rn -- "$payload" | "$PLUTIL_BIN" -extract "$field" raw -o - - 2>/dev/null)"; then
      [[ "$value" == "true" || "$value" == "false" ]] || return 1
      print -rn -- "$value"
      return 0
    fi
  fi

  local flattened=""
  local occurrences=""
  flattened="$(print -rn -- "$payload" | /usr/bin/tr '\r\n' '  ' 2>/dev/null)" || return 1
  occurrences="$(print -rn -- "$flattened" | /usr/bin/grep -o "\"${field}\"[[:space:]]*:" 2>/dev/null | /usr/bin/wc -l | /usr/bin/tr -d '[:space:]')" || return 1
  [[ "$occurrences" == "1" ]] || return 1
  value="$(print -rn -- "$flattened" | /usr/bin/sed -n "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\\(true\\|false\\).*/\\1/p")" || return 1
  [[ "$value" == "true" || "$value" == "false" ]] || return 1
  print -rn -- "$value"
}

parse_latest_release_json() {
  local payload="$1"
  print -rn -- "$payload" | /usr/bin/awk '
function fail() { failed = 1; return 0 }
function skip_ws(    c) {
  while (cursor <= length(input)) {
    c = substr(input, cursor, 1)
    if (c != " " && c != "\t" && c != "\r" && c != "\n") break
    cursor++
  }
}
function hex_digit(c,    position) {
  position = index("0123456789abcdef", tolower(c))
  return position == 0 ? -1 : position - 1
}
function parse_string(    c, escape, code, digit, index_value, decoded) {
  if (substr(input, cursor, 1) != "\"") return fail()
  cursor++
  decoded = ""
  while (cursor <= length(input)) {
    c = substr(input, cursor, 1)
    cursor++
    if (c == "\"") {
      parsed_string = decoded
      return 1
    }
    if (c == "\\") {
      if (cursor > length(input)) return fail()
      escape = substr(input, cursor, 1)
      cursor++
      if (escape == "u") {
        if (cursor + 3 > length(input)) return fail()
        code = 0
        for (index_value = 0; index_value < 4; index_value++) {
          digit = hex_digit(substr(input, cursor + index_value, 1))
          if (digit < 0) return fail()
          code = code * 16 + digit
        }
        cursor += 4
        decoded = decoded (code <= 127 ? sprintf("%c", code) : "?")
      } else if (index("\"\\/bfnrt", escape) > 0) {
        if (escape == "\"" || escape == "\\" || escape == "/") {
          decoded = decoded escape
        } else {
          decoded = decoded " "
        }
      } else {
        return fail()
      }
    } else {
      if (c ~ /[[:cntrl:]]/) return fail()
      decoded = decoded c
    }
  }
  return fail()
}
function consume_literal(word) {
  if (substr(input, cursor, length(word)) != word) return fail()
  cursor += length(word)
  return 1
}
function parse_number(    remainder) {
  remainder = substr(input, cursor)
  if (match(remainder, /^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?/) != 1) return fail()
  cursor += RLENGTH
  return 1
}
function parse_array(depth,    c) {
  cursor++
  skip_ws()
  if (substr(input, cursor, 1) == "]") { cursor++; return 1 }
  while (!failed) {
    if (!parse_value(depth + 1)) return 0
    skip_ws()
    c = substr(input, cursor, 1)
    if (c == "]") { cursor++; return 1 }
    if (c != ",") return fail()
    cursor++
    skip_ws()
  }
  return 0
}
function parse_object(depth,    c, key) {
  cursor++
  skip_ws()
  if (substr(input, cursor, 1) == "}") { cursor++; return 1 }
  while (!failed) {
    if (!parse_string()) return 0
    key = parsed_string
    skip_ws()
    if (substr(input, cursor, 1) != ":") return fail()
    cursor++
    skip_ws()
    if (depth == 1 && key == "name") {
      name_count++
      if (name_count != 1) return fail()
      c = substr(input, cursor, 1)
      if (c == "\"") {
        if (!parse_string()) return 0
        name_kind = "string"
        name_value = parsed_string
      } else if (substr(input, cursor, 4) == "null") {
        if (!consume_literal("null")) return 0
        name_kind = "null"
      } else {
        return fail()
      }
    } else if (depth == 1 && key == "tag_name") {
      tag_count++
      if (tag_count != 1 || substr(input, cursor, 1) != "\"") return fail()
      if (!parse_string() || parsed_string == "") return fail()
      tag_value = parsed_string
    } else if (depth == 1 && (key == "draft" || key == "prerelease")) {
      if (key == "draft") {
        draft_count++
        if (draft_count != 1) return fail()
      } else {
        prerelease_count++
        if (prerelease_count != 1) return fail()
      }
      if (substr(input, cursor, 4) == "true") {
        if (!consume_literal("true")) return 0
        boolean_value = "true"
      } else if (substr(input, cursor, 5) == "false") {
        if (!consume_literal("false")) return 0
        boolean_value = "false"
      } else {
        return fail()
      }
      if (key == "draft") draft_value = boolean_value
      else prerelease_value = boolean_value
    } else if (!parse_value(depth + 1)) {
      return 0
    }
    skip_ws()
    c = substr(input, cursor, 1)
    if (c == "}") { cursor++; return 1 }
    if (c != ",") return fail()
    cursor++
    skip_ws()
  }
  return 0
}
function parse_value(depth,    c) {
  skip_ws()
  c = substr(input, cursor, 1)
  if (c == "{") return parse_object(depth)
  if (c == "[") return parse_array(depth)
  if (c == "\"") return parse_string()
  if (c == "t") return consume_literal("true")
  if (c == "f") return consume_literal("false")
  if (c == "n") return consume_literal("null")
  return parse_number()
}
{ input = input (NR == 1 ? "" : "\n") $0 }
END {
  cursor = 1
  skip_ws()
  if (substr(input, cursor, 1) != "{" || !parse_object(1)) failed = 1
  skip_ws()
  if (cursor <= length(input)) failed = 1
  if (tag_count != 1 || draft_count != 1 || prerelease_count != 1) failed = 1
  if (failed) exit 2
  if (name_count == 0 || name_kind == "null") name_status = "absent"
  else if (tolower(name_value) ~ /withdrawn/) name_status = "withdrawn"
  else name_status = "ok"
  printf "%s\t%s\t%s\t%s\n", tag_value, draft_value, prerelease_value, name_status
}'
}

parse_installer_safety_args() {
  INSTALLER_UPGRADE=0
  INSTALLER_RUNTIME="${NOVA_INSTALL_RUNTIME:-$HOME/.open-nova}"
  INSTALLER_RUNTIME_ARG=0
  INSTALLER_RUNTIME_ENV=0
  INSTALLER_YES=0
  if [[ -n "${NOVA_INSTALL_RUNTIME:-}" ]]; then
    INSTALLER_RUNTIME_ENV=1
  fi
  local index=1
  local arg=""
  local value=""
  while (( index <= ${#INSTALL_ARGS[@]} )); do
    arg="${INSTALL_ARGS[$index]}"
    case "$arg" in
      --upgrade|--source-only|--sync-runtime-source)
        INSTALLER_UPGRADE=1
        ;;
      --yes)
        INSTALLER_YES=1
        ;;
      --runtime)
        if (( index >= ${#INSTALL_ARGS[@]} )); then
          bootstrap_problem option_value_missing "--runtime requires a value"
          return 2
        fi
        index=$(( index + 1 ))
        value="${INSTALL_ARGS[$index]}"
        if [[ -z "$value" || "$value" == --* ]]; then
          bootstrap_problem option_value_missing "--runtime requires a path value"
          return 2
        fi
        INSTALLER_RUNTIME="$value"
        INSTALLER_RUNTIME_ARG=1
        ;;
      --diary-output|--desktop-diary-link|--shell-path-file|--reports-output|--snapshots-output|--archives-output|--source-root|--python|--dashboard-port|--dashboard-host|--rag-embedding-mode|--rag-local-model|--rag-local-dimension|--llm-provider|--llm-endpoint|--llm-model|--llm-api-key-env|--rag-cloud-provider|--rag-cloud-endpoint|--rag-cloud-model|--rag-cloud-dimension|--rag-cloud-api-key-env|--language)
        # A token consumed as another option's value must never be mistaken for
        # --upgrade and used to bypass the fresh-install guard.
        if (( index < ${#INSTALL_ARGS[@]} )); then
          index=$(( index + 1 ))
        fi
        ;;
    esac
    index=$(( index + 1 ))
  done
}

normalize_runtime_candidate() {
  local candidate="$1"
  if [[ "$candidate" == "~" ]]; then
    candidate="$HOME"
  elif [[ "$candidate" == "~/"* ]]; then
    candidate="${HOME}/${candidate#\~/}"
  elif [[ "$candidate" == "~"* ]]; then
    return 1
  fi
  print -rn -- "${candidate:a}"
}

canonical_source_url() {
  local value="$1"
  case "$value" in
    "https://github.com/Neo-Isshin/open-nova"|"https://github.com/Neo-Isshin/open-nova/"|"https://github.com/Neo-Isshin/open-nova.git"|"https://github.com/Neo-Isshin/open-nova.git/")
      print -rn -- "$DEFAULT_SOURCE_URL"
      ;;
    *)
      print -rn -- "$value"
      ;;
  esac
}

source_urls_match() {
  local existing="$1"
  local requested="$2"
  [[ "$(canonical_source_url "$existing")" == "$(canonical_source_url "$requested")" ]]
}

runtime_has_open_nova_marker() {
  local candidate="$1"
  local marker=""
  for marker in \
    "${candidate}/app/source" \
    "${candidate}/.venv" \
    "${candidate}/config/runtime.json" \
    "${candidate}/config/settings.json" \
    "${candidate}/data/nova_data.sqlite3" \
    "${candidate}/bin/open-nova"; do
    if [[ -e "$marker" || -L "$marker" ]]; then
      return 0
    fi
  done
  return 1
}

runtime_is_updateable_open_nova() {
  local candidate="$1"
  local source_pointer="${candidate}/app/source"
  local source_raw=""
  local generation=""
  local source_target=""
  local manifest=""
  local manifest_payload=""
  local manifest_size=""
  local product=""
  local deployment_mode=""
  [[ -d "$candidate" && ! -L "$candidate" ]] || return 1
  [[ -d "${candidate}/config" && ! -L "${candidate}/config" ]] || return 1
  [[ -d "${candidate}/app" && ! -L "${candidate}/app" ]] || return 1
  [[ -d "${candidate}/app/releases" && ! -L "${candidate}/app/releases" ]] || return 1
  [[ -f "${candidate}/config/settings.json" && ! -L "${candidate}/config/settings.json" ]] || return 1
  [[ -f "${candidate}/config/runtime.json" && ! -L "${candidate}/config/runtime.json" ]] || return 1
  [[ -L "$source_pointer" ]] || return 1
  source_raw="$(/usr/bin/readlink "$source_pointer" 2>/dev/null)" || return 1
  [[ "$source_raw" == releases/* ]] || return 1
  generation="${source_raw#releases/}"
  [[ -n "$generation" && "$generation" != "." && "$generation" != ".." && "$generation" != */* ]] || return 1
  source_target="${candidate}/app/releases/${generation}"
  [[ -d "$source_target" && ! -L "$source_target" ]] || return 1
  manifest="${source_target}/.open-nova-runtime-source.json"
  [[ -f "$manifest" && ! -L "$manifest" ]] || return 1
  manifest_size="$(/usr/bin/wc -c < "$manifest" | /usr/bin/tr -d '[:space:]')" || return 1
  [[ "$manifest_size" =~ "^[0-9]+$" ]] || return 1
  (( manifest_size > 0 && manifest_size <= 1048576 )) || return 1
  manifest_payload="$(<"$manifest")" || return 1
  product="$(extract_json_string "$manifest_payload" "product")" || return 1
  deployment_mode="$(extract_json_string "$manifest_payload" "deploymentMode")" || return 1
  [[ "$product" == "open-nova" && "$deployment_mode" == "release-symlink" ]]
}

read_saved_runtime_location() {
  local location_file="${NOVA_LOCATION_FILE:-$HOME/.config/open-nova/location.json}"
  local location_payload=""
  local pointed_runtime=""
  if ! location_file="$(normalize_runtime_candidate "$location_file")"; then
    bootstrap_problem location_invalid "cannot safely resolve the saved Open Nova location"
    return 2
  fi
  if [[ -e "$location_file" || -L "$location_file" ]]; then
    if [[ ! -f "$location_file" || -L "$location_file" ]]; then
      bootstrap_problem location_invalid "saved Open Nova location is not a regular file: ${location_file}"
      return 2
    fi
    local location_size=""
    location_size="$(/usr/bin/wc -c < "$location_file" | /usr/bin/tr -d '[:space:]')" || return 2
    if [[ ! "$location_size" =~ "^[0-9]+$" ]] || (( location_size > 65536 )); then
      bootstrap_problem location_invalid "saved Open Nova location is invalid: ${location_file}"
      return 2
    fi
    location_payload="$(<"$location_file")" || return 2
    if ! pointed_runtime="$(extract_json_string "$location_payload" "novaHome")"; then
      bootstrap_problem location_invalid "saved Open Nova location could not be parsed: ${location_file}"
      return 2
    fi
    if [[ "$pointed_runtime" != /* || "$pointed_runtime" == *$'\n'* || "$pointed_runtime" == *$'\r'* ]]; then
      bootstrap_problem location_invalid "saved Open Nova location is not an absolute path: ${location_file}"
      return 2
    fi
  fi
  print -rn -- "$pointed_runtime"
}

select_installer_mode_before_source_writes() {
  parse_installer_safety_args || return $?
  local selected_runtime="$INSTALLER_RUNTIME"
  local pointed_runtime=""
  local normalized_runtime=""

  if [[ "$INSTALLER_RUNTIME_ARG" != "1" && "$INSTALLER_RUNTIME_ENV" != "1" ]]; then
    if [[ -n "${NOVA_HOME:-}" ]]; then
      selected_runtime="$NOVA_HOME"
    else
      pointed_runtime="$(read_saved_runtime_location)" || return $?
      if [[ -n "$pointed_runtime" ]]; then
        selected_runtime="$pointed_runtime"
      fi
    fi
  fi
  if ! normalized_runtime="$(normalize_runtime_candidate "$selected_runtime")"; then
    bootstrap_problem location_invalid "cannot safely resolve the selected Open Nova folder"
    return 2
  fi
  if [[ "$INSTALLER_RUNTIME_ARG" != "1" ]]; then
    INSTALL_ARGS+=(--runtime "$normalized_runtime")
  fi

  if [[ "$INSTALLER_UPGRADE" == "1" ]]; then
    return 0
  fi
  if runtime_has_open_nova_marker "$normalized_runtime"; then
    if ! runtime_is_updateable_open_nova "$normalized_runtime"; then
      bootstrap_problem runtime_incomplete "existing Open Nova state is incomplete at ${normalized_runtime}"
      return 2
    fi
    INSTALL_ARGS+=(--upgrade)
    if [[ "$INSTALLER_YES" != "1" ]]; then
      INSTALL_ARGS+=(--yes)
      INSTALLER_YES=1
    fi
    INSTALLER_UPGRADE=1
    bootstrap_ok "$(bootstrap_text existing_ready)"
    return 0
  fi

  local launch_agent=""
  for launch_agent in "$HOME"/Library/LaunchAgents/com.open-nova*.plist(N); do
    if [[ -e "$launch_agent" || -L "$launch_agent" ]]; then
      bootstrap_problem service_unmatched "existing Open Nova background service has no selected Runtime: ${launch_agent}"
      return 2
    fi
  done
}

resolve_latest_stable_commit() {
  if ! command -v "$CURL_BIN" >/dev/null 2>&1 && [[ ! -x "$CURL_BIN" ]]; then
    bootstrap_problem version_unavailable "curl executable not found: ${CURL_BIN}"
    return 2
  fi
  local payload=""
  if ! payload="$("$CURL_BIN" -fsSL --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 "$DEFAULT_LATEST_RELEASE_API")"; then
    bootstrap_problem version_unavailable "latest stable Open Nova release could not be read"
    return 2
  fi
  local release_tag=""
  local release_metadata=""
  local release_name_status=""
  local release_draft=""
  local release_prerelease=""
  if ! release_metadata="$(parse_latest_release_json "$payload")"; then
    bootstrap_problem version_unavailable "latest stable Open Nova release response is invalid"
    return 2
  fi
  IFS=$'\t' read -r release_tag release_draft release_prerelease release_name_status <<< "$release_metadata"
  if [[ "$release_draft" != "false" || "$release_prerelease" != "false" ]]; then
    bootstrap_problem version_unavailable "latest Open Nova release is not stable"
    return 2
  fi
  # GitHub immutable Releases may still have their human-facing title and
  # notes edited after publication.  Treat an explicit WITHDRAWN title as a
  # distribution stop even though GitHub's /releases/latest endpoint continues
  # to classify the object as published, non-draft, and non-prerelease.
  if [[ "$release_name_status" == "withdrawn" ]]; then
    bootstrap_problem version_unavailable "latest Open Nova release was withdrawn"
    return 2
  fi
  if [[ ! "$release_tag" =~ "^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$" ]] \
    || [[ "$release_tag" == *..* || "$release_tag" == *'@{'* || "$release_tag" == *. ]]; then
    bootstrap_problem version_unavailable "latest Open Nova release returned an invalid version tag"
    return 2
  fi

  local remote_rows=""
  if ! remote_rows="$("$GIT_BIN" ls-remote --tags "$DEFAULT_SOURCE_URL" "refs/tags/${release_tag}" "refs/tags/${release_tag}^{}")"; then
    bootstrap_problem version_unavailable "stable release tag ${release_tag} could not be resolved"
    return 2
  fi
  local direct_commit=""
  local peeled_commit=""
  local object_id=""
  local ref_name=""
  while IFS=$'\t' read -r object_id ref_name; do
    if [[ "$ref_name" == "refs/tags/${release_tag}^{}" ]] && is_full_commit_id "$object_id"; then
      peeled_commit="${object_id:l}"
    elif [[ "$ref_name" == "refs/tags/${release_tag}" ]] && is_full_commit_id "$object_id"; then
      direct_commit="${object_id:l}"
    fi
  done <<< "$remote_rows"
  local resolved_commit="${peeled_commit:-$direct_commit}"
  if [[ -z "$resolved_commit" ]]; then
    bootstrap_problem version_unavailable "stable release tag ${release_tag} did not resolve to an exact version"
    return 2
  fi
  log "Selected latest stable release tag: ${release_tag} (${resolved_commit})" >&2
  bootstrap_ok "$(bootstrap_text latest_ready)" >&2
  print -rn -- "$resolved_commit"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source-root)
      require_value "$1" "${2:-}"
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --source-url)
      require_value "$1" "${2:-}"
      SOURCE_URL="$2"
      shift 2
      ;;
    --ref)
      require_value "$1" "${2:-}"
      SOURCE_REF="$2"
      shift 2
      ;;
    --cache-root)
      require_value "$1" "${2:-}"
      DEFAULT_CACHE_ROOT="$2"
      shift 2
      ;;
    --git)
      require_value "$1" "${2:-}"
      GIT_BIN="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      INSTALL_ARGS+=("--dry-run")
      shift
      ;;
    --offline)
      OFFLINE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        INSTALL_ARGS+=("$1")
        shift
      done
      ;;
    *)
      INSTALL_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ "$OFFLINE" == "1" ]] && ! installer_arg_present "--offline"; then
  INSTALL_ARGS+=("--offline")
fi

for (( bootstrap_index = 1; bootstrap_index <= ${#INSTALL_ARGS[@]}; bootstrap_index++ )); do
  if [[ "${INSTALL_ARGS[$bootstrap_index]}" == "--language" ]] && (( bootstrap_index < ${#INSTALL_ARGS[@]} )); then
    BOOTSTRAP_LANGUAGE="${INSTALL_ARGS[$((bootstrap_index + 1))]}"
    break
  fi
done

if [[ -z "$SOURCE_ROOT" && -z "$SOURCE_URL" ]]; then
  checkout_candidate="${BOOTSTRAP_DIR:h}"
  # Only a bootstrap executed from a real file may infer its adjacent checkout.
  # Hosted/stdin execution must not silently adopt an unrelated current working tree.
  if [[ -f "$0" && -f "${checkout_candidate}/pyproject.toml" && -x "${checkout_candidate}/install/install.sh" ]]; then
    SOURCE_ROOT="$checkout_candidate"
  fi
fi

if [[ -n "$SOURCE_ROOT" && -n "$SOURCE_REF" ]]; then
  bootstrap_problem options_conflict "--source-root and --ref cannot be combined"
  exit 2
fi

# Runtime selection intentionally runs before cache directory creation, clone,
# fetch, sparse-checkout, or any other installer-owned source write.
select_installer_mode_before_source_writes || exit $?

if [[ -z "$SOURCE_ROOT" ]]; then
  if [[ -z "$SOURCE_URL" ]]; then
    SOURCE_URL="$DEFAULT_SOURCE_URL"
  fi
  if ! command -v "$GIT_BIN" >/dev/null 2>&1 && [[ ! -x "$GIT_BIN" ]]; then
    bootstrap_problem git_missing "Git executable not found: ${GIT_BIN}"
    exit 2
  fi
  if [[ -z "$SOURCE_REF" ]]; then
    if [[ "$OFFLINE" == "1" ]]; then
      bootstrap_problem offline_missing "offline setup requires a local source or exact cached version"
      exit 2
    fi
    if [[ "$SOURCE_URL" != "$DEFAULT_SOURCE_URL" ]]; then
      bootstrap_problem version_invalid "custom source URL requires an exact version ID"
      exit 2
    fi
    SOURCE_REF="$(resolve_latest_stable_commit)" || exit $?
  elif ! is_full_commit_id "$SOURCE_REF"; then
    bootstrap_problem version_invalid "remote version must be a full 40- or 64-character commit ID"
    exit 2
  else
    SOURCE_REF="${SOURCE_REF:l}"
  fi
  cache_root="${DEFAULT_CACHE_ROOT:A}"
  SOURCE_ROOT="${cache_root}/source"
  BOOTSTRAP_LOG_FILE="${cache_root}/bootstrap.log"
  CACHE_SOURCE=1
  if [[ "$DRY_RUN" != "1" && -d "$cache_root" ]]; then
    prepare_bootstrap_log "$cache_root"
  fi
  if [[ -d "${SOURCE_ROOT}/.git" ]]; then
    existing_source_url="$(git_exec -C "${SOURCE_ROOT}" remote get-url origin 2>/dev/null || true)"
    if ! source_urls_match "$existing_source_url" "$SOURCE_URL"; then
      bootstrap_problem cache_mismatch "download cache source does not match requested source"
      exit 2
    fi
    log "Updating existing source checkout: ${SOURCE_ROOT}"
    if [[ "$OFFLINE" != "1" ]]; then
      configure_sparse_checkout "${SOURCE_ROOT}"
      run_git_cmd -C "${SOURCE_ROOT}" fetch --all --tags --force
    else
      verify_offline_source_cache "${SOURCE_ROOT}" "${SOURCE_REF}" || exit $?
      configure_sparse_checkout "${SOURCE_ROOT}"
      log "Offline mode: using the existing installer-owned source cache without fetch"
      bootstrap_ok "$(bootstrap_text cache_ready)"
    fi
  else
    if [[ "$OFFLINE" == "1" ]]; then
      bootstrap_problem offline_missing "offline source cache is missing: ${SOURCE_ROOT}"
      exit 2
    fi
    log "Downloading the selected Open Nova version"
    run_cmd mkdir -p "${cache_root}"
    if [[ "$DRY_RUN" != "1" ]]; then
      prepare_bootstrap_log "$cache_root"
    fi
    run_git_cmd clone --filter=blob:none --sparse --no-checkout "${SOURCE_URL}" "${SOURCE_ROOT}"
    configure_sparse_checkout "${SOURCE_ROOT}"
  fi
fi

SOURCE_ROOT="${SOURCE_ROOT:A}"
SELECTED_REF=""
if [[ "$CACHE_SOURCE" == "1" ]]; then
  SELECTED_REF="$SOURCE_REF"
  if [[ "$DRY_RUN" != "1" ]]; then
    resolved_object="$(git_exec -C "${SOURCE_ROOT}" rev-parse --verify "${SOURCE_REF}^{commit}" 2>/dev/null || true)"
    resolved_object="${resolved_object:l}"
    if [[ "$resolved_object" != "$SOURCE_REF" ]]; then
      bootstrap_problem version_unavailable "resolved source object does not match required commit ${SOURCE_REF}"
      exit 2
    fi
  fi
  log "Selecting immutable source commit: ${SOURCE_REF}"
  run_git_cmd -C "${SOURCE_ROOT}" checkout --detach "${SOURCE_REF}"
fi

if [[ "$CACHE_SOURCE" == "1" ]]; then
  selected_ref="$SELECTED_REF"
  log "Resetting installer-owned source checkout to ${selected_ref}"
  run_git_cmd -C "${SOURCE_ROOT}" reset --hard "${selected_ref}"
  log "Cleaning ignored artifacts from installer-owned source checkout"
  run_git_cmd -C "${SOURCE_ROOT}" clean -fdX
fi

if [[ "$DRY_RUN" == "1" && ! -f "${SOURCE_ROOT}/install/install.sh" ]]; then
  log "Dry-run assumes cloned source will contain install/install.sh"
elif [[ ! -f "${SOURCE_ROOT}/install/install.sh" ]]; then
  bootstrap_problem files_missing "main setup script not found under source root: ${SOURCE_ROOT}"
  exit 2
fi

log "Running installer from source root: ${SOURCE_ROOT}"
if [[ -z "$ZSH_BIN" || ! -x "$ZSH_BIN" ]]; then
  ZSH_BIN="$(command -v zsh 2>/dev/null || true)"
fi
if [[ -z "$ZSH_BIN" && -x "/bin/zsh" ]]; then
  ZSH_BIN="/bin/zsh"
fi
if [[ -z "$ZSH_BIN" ]]; then
  bootstrap_problem shell_missing "zsh executable not found"
  exit 2
fi
bootstrap_start_key="starting"
if [[ "$INSTALLER_UPGRADE" == "1" ]]; then
  bootstrap_start_key="starting_update"
fi
if [[ "${NOVA_INSTALL_VERBOSE:-0}" == "1" ]]; then
  print -r -- "+ $(bootstrap_text "$bootstrap_start_key")"
fi
bootstrap_ok "$(bootstrap_text "$bootstrap_start_key")"
if [[ "$DRY_RUN" == "1" ]]; then
  if [[ -f "${SOURCE_ROOT}/install/install.sh" ]]; then
    "$ZSH_BIN" "${SOURCE_ROOT}/install/install.sh" --source-root "${SOURCE_ROOT}" "${INSTALL_ARGS[@]}"
  fi
else
  "$ZSH_BIN" "${SOURCE_ROOT}/install/install.sh" --source-root "${SOURCE_ROOT}" "${INSTALL_ARGS[@]}"
fi
fi
