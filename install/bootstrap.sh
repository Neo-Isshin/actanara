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
Open Nova installer bootstrap

Usage:
  install/bootstrap.sh [bootstrap-options] [-- installer-options]

Bootstrap options:
  --source-root PATH       Use an existing local checkout.
  --source-url URL         Clone source from URL when no local checkout is supplied.
                          Default: https://github.com/Neo-Isshin/open-nova.git
  --ref COMMIT            Checkout an explicit full 40/64-hex commit after cloning/fetching.
                          With the default remote, omitting --ref selects the latest stable release.
  --cache-root PATH        Source acquisition cache root. Default: ~/.cache/open-nova/installer
  --git PATH              Git binary. Default: git
  --offline               Forbid source network access and require a local source or a cached explicit commit.
  --dry-run               Print source acquisition and installer plan without writes.
  -h, --help              Show this help.

Installer options after -- are forwarded to install/install.sh.
EOF
}

log() {
  print -r -- "==> $*"
}

run_cmd() {
  print -r -- "+ $*"
  if [[ "$DRY_RUN" != "1" ]]; then
    "$@"
  fi
}

git_exec() {
  if [[ "$OFFLINE" == "1" ]]; then
    # A cached partial clone may otherwise fetch a missing promisor object from
    # inside checkout/archive even though bootstrap never invokes `git fetch`.
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
  print -r -- "+ ${GIT_BIN} $*"
  if [[ "$DRY_RUN" != "1" ]]; then
    git_exec "$@"
  fi
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
    print -r -- "Resolved source object is not the required commit ${ref}; refusing mutable HEAD fallback." >&2
    return 2
  fi
  # `git archive` forces every tree/blob needed by the installer sparse payload
  # to be read before sparse-checkout, checkout, reset, or installer execution.
  # git_exec forbids both lazy fetch and every transport, so a partial-cache
  # miss fails closed here without a permitted source-network transport.
  if ! git_exec -C "${root}" archive --format=tar "${ref}" -- "${OFFLINE_SOURCE_PATHS[@]}" > /dev/null; then
    print -r -- "Offline source cache is incomplete for commit ${ref}; reconnect and refresh this installer cache before retrying offline." >&2
    return 2
  fi
}

require_value() {
  local option="$1"
  local value="${2:-}"
  if [[ -z "$value" || "$value" == --* ]]; then
    print -r -- "${option} requires a value" >&2
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
  local index=1
  local arg=""
  local value=""
  while (( index <= ${#INSTALL_ARGS[@]} )); do
    arg="${INSTALL_ARGS[$index]}"
    case "$arg" in
      --upgrade|--source-only|--sync-runtime-source)
        INSTALLER_UPGRADE=1
        ;;
      --runtime)
        if (( index >= ${#INSTALL_ARGS[@]} )); then
          print -r -- "--runtime requires a value" >&2
          return 2
        fi
        index=$(( index + 1 ))
        value="${INSTALL_ARGS[$index]}"
        if [[ -z "$value" || "$value" == --* ]]; then
          print -r -- "--runtime requires a path value" >&2
          return 2
        fi
        INSTALLER_RUNTIME="$value"
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
  print -rn -- "${candidate:A}"
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

reject_existing_runtime_candidate() {
  local label="$1"
  local raw_candidate="$2"
  local candidate=""
  if ! candidate="$(normalize_runtime_candidate "$raw_candidate")"; then
    print -r -- "Cannot safely resolve ${label} Runtime path; use the existing Runtime's open-nova update command." >&2
    return 2
  fi
  if runtime_has_open_nova_marker "$candidate"; then
    print -r -- "Existing Open Nova Runtime detected at ${candidate}; fresh install refused." >&2
    print -r -- "Use the existing Runtime's 'open-nova update --apply' command (and 'open-nova doctor' first if needed)." >&2
    return 2
  fi
}

guard_fresh_install_before_source_writes() {
  parse_installer_safety_args || return $?
  if [[ "$INSTALLER_UPGRADE" == "1" ]]; then
    return 0
  fi

  reject_existing_runtime_candidate "target" "$INSTALLER_RUNTIME" || return $?
  if [[ -n "${NOVA_HOME:-}" ]]; then
    reject_existing_runtime_candidate "NOVA_HOME" "$NOVA_HOME" || return $?
  fi
  reject_existing_runtime_candidate "default" "$HOME/.open-nova" || return $?

  local location_file="${NOVA_LOCATION_FILE:-$HOME/.config/open-nova/location.json}"
  local location_payload=""
  local pointed_runtime=""
  if ! location_file="$(normalize_runtime_candidate "$location_file")"; then
    print -r -- "Cannot safely resolve the Open Nova Runtime pointer path; fresh install refused." >&2
    return 2
  fi
  if [[ -e "$location_file" || -L "$location_file" ]]; then
    if [[ ! -f "$location_file" || -L "$location_file" ]]; then
      print -r -- "Cannot safely parse Open Nova Runtime pointer: ${location_file}; fresh install refused." >&2
      print -r -- "Use the existing Runtime's 'open-nova update' or 'open-nova doctor' command." >&2
      return 2
    fi
    local location_size=""
    location_size="$(/usr/bin/wc -c < "$location_file" | /usr/bin/tr -d '[:space:]')" || return 2
    if [[ ! "$location_size" =~ "^[0-9]+$" ]] || (( location_size > 65536 )); then
      print -r -- "Cannot safely parse Open Nova Runtime pointer: ${location_file}; fresh install refused." >&2
      return 2
    fi
    location_payload="$(<"$location_file")" || return 2
    if ! pointed_runtime="$(extract_json_string "$location_payload" "novaHome")"; then
      print -r -- "Cannot safely parse Open Nova Runtime pointer: ${location_file}; fresh install refused." >&2
      print -r -- "Use the existing Runtime's 'open-nova update' or 'open-nova doctor' command." >&2
      return 2
    fi
    if [[ "$pointed_runtime" != /* || "$pointed_runtime" == *$'\n'* || "$pointed_runtime" == *$'\r'* ]]; then
      print -r -- "Runtime pointer novaHome is not a safe absolute path: ${location_file}; fresh install refused." >&2
      return 2
    fi
    reject_existing_runtime_candidate "location pointer" "$pointed_runtime" || return $?
  fi

  local launch_agent=""
  for launch_agent in "$HOME"/Library/LaunchAgents/com.open-nova*.plist(N); do
    if [[ -e "$launch_agent" || -L "$launch_agent" ]]; then
      print -r -- "Existing Open Nova LaunchAgent detected; fresh install refused: ${launch_agent}" >&2
      print -r -- "Use the existing Runtime's 'open-nova update' command after 'open-nova doctor'." >&2
      return 2
    fi
  done
}

resolve_latest_stable_commit() {
  if ! command -v "$CURL_BIN" >/dev/null 2>&1 && [[ ! -x "$CURL_BIN" ]]; then
    print -r -- "curl binary not found: ${CURL_BIN}" >&2
    return 2
  fi
  local payload=""
  if ! payload="$("$CURL_BIN" -fsSL --proto '=https' --tlsv1.2 --connect-timeout 10 --max-time 30 "$DEFAULT_LATEST_RELEASE_API")"; then
    print -r -- "No stable Open Nova release could be read from the default GitHub Release API; refusing mutable HEAD." >&2
    return 2
  fi
  local release_tag=""
  local release_metadata=""
  local release_name_status=""
  local release_draft=""
  local release_prerelease=""
  if ! release_metadata="$(parse_latest_release_json "$payload")"; then
    print -r -- "The default GitHub latest-release response is invalid; refusing mutable HEAD." >&2
    return 2
  fi
  IFS=$'\t' read -r release_tag release_draft release_prerelease release_name_status <<< "$release_metadata"
  if [[ "$release_draft" != "false" || "$release_prerelease" != "false" ]]; then
    print -r -- "The default GitHub Release is draft or prerelease; a stable release is required." >&2
    return 2
  fi
  # GitHub immutable Releases may still have their human-facing title and
  # notes edited after publication.  Treat an explicit WITHDRAWN title as a
  # distribution stop even though GitHub's /releases/latest endpoint continues
  # to classify the object as published, non-draft, and non-prerelease.
  if [[ "$release_name_status" == "withdrawn" ]]; then
    print -r -- "The default GitHub Release is explicitly withdrawn; refusing source acquisition." >&2
    return 2
  fi
  if [[ ! "$release_tag" =~ "^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$" ]] \
    || [[ "$release_tag" == *..* || "$release_tag" == *'@{'* || "$release_tag" == *. ]]; then
    print -r -- "The default GitHub Release returned an unsafe tag; refusing source acquisition." >&2
    return 2
  fi

  local remote_rows=""
  if ! remote_rows="$("$GIT_BIN" ls-remote --tags "$DEFAULT_SOURCE_URL" "refs/tags/${release_tag}" "refs/tags/${release_tag}^{}")"; then
    print -r -- "Unable to resolve stable release tag ${release_tag} from the default GitHub remote." >&2
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
    print -r -- "Stable release tag ${release_tag} did not peel to a full commit; refusing mutable HEAD." >&2
    return 2
  fi
  log "Selected latest stable release tag: ${release_tag} (${resolved_commit})" >&2
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

if [[ -z "$SOURCE_ROOT" && -z "$SOURCE_URL" ]]; then
  checkout_candidate="${BOOTSTRAP_DIR:h}"
  # Only a bootstrap executed from a real file may infer its adjacent checkout.
  # Hosted/stdin execution must not silently adopt an unrelated current working tree.
  if [[ -f "$0" && -f "${checkout_candidate}/pyproject.toml" && -x "${checkout_candidate}/install/install.sh" ]]; then
    SOURCE_ROOT="$checkout_candidate"
  fi
fi

if [[ -n "$SOURCE_ROOT" && -n "$SOURCE_REF" ]]; then
  print -r -- "--source-root and --ref cannot be combined; refusing to checkout or mutate a user-provided source tree." >&2
  exit 2
fi

# This guard intentionally runs before cache directory creation, clone, fetch,
# sparse-checkout, or any other installer-owned source write.
guard_fresh_install_before_source_writes || exit $?

if [[ -z "$SOURCE_ROOT" ]]; then
  if [[ -z "$SOURCE_URL" ]]; then
    SOURCE_URL="$DEFAULT_SOURCE_URL"
  fi
  if ! command -v "$GIT_BIN" >/dev/null 2>&1 && [[ ! -x "$GIT_BIN" ]]; then
    print -r -- "Git binary not found: ${GIT_BIN}" >&2
    exit 2
  fi
  if [[ -z "$SOURCE_REF" ]]; then
    if [[ "$OFFLINE" == "1" ]]; then
      print -r -- "Offline source acquisition requires --source-root or an explicit full --ref already present in the installer cache." >&2
      exit 2
    fi
    if [[ "$SOURCE_URL" != "$DEFAULT_SOURCE_URL" ]]; then
      print -r -- "A custom --source-url requires an explicit full 40/64-hex --ref commit; refusing mutable HEAD." >&2
      exit 2
    fi
    SOURCE_REF="$(resolve_latest_stable_commit)" || exit $?
  elif ! is_full_commit_id "$SOURCE_REF"; then
    print -r -- "Remote --ref must be a full 40/64-hex commit; branches, tags, and abbreviated commits are refused." >&2
    exit 2
  else
    SOURCE_REF="${SOURCE_REF:l}"
  fi
  cache_root="${DEFAULT_CACHE_ROOT:A}"
  SOURCE_ROOT="${cache_root}/source"
  CACHE_SOURCE=1
  if [[ -d "${SOURCE_ROOT}/.git" ]]; then
    existing_source_url="$(git_exec -C "${SOURCE_ROOT}" remote get-url origin 2>/dev/null || true)"
    if [[ "$existing_source_url" != "$SOURCE_URL" ]]; then
      print -r -- "Installer cache origin does not match requested source URL; choose a different --cache-root." >&2
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
    fi
  else
    if [[ "$OFFLINE" == "1" ]]; then
      print -r -- "Offline source cache is missing: ${SOURCE_ROOT}" >&2
      exit 2
    fi
    log "Cloning source: ${SOURCE_URL}"
    run_cmd mkdir -p "${cache_root}"
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
      print -r -- "Resolved source object is not the required commit ${SOURCE_REF}; refusing mutable HEAD fallback." >&2
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
  print -r -- "install/install.sh not found under source root: ${SOURCE_ROOT}" >&2
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
  print -r -- "zsh not found; cannot run installer" >&2
  exit 2
fi
print -r -- "+ ${ZSH_BIN} ${SOURCE_ROOT}/install/install.sh --source-root ${SOURCE_ROOT} ${INSTALL_ARGS[*]}"
if [[ "$DRY_RUN" == "1" ]]; then
  if [[ -f "${SOURCE_ROOT}/install/install.sh" ]]; then
    "$ZSH_BIN" "${SOURCE_ROOT}/install/install.sh" --source-root "${SOURCE_ROOT}" "${INSTALL_ARGS[@]}"
  fi
else
  "$ZSH_BIN" "${SOURCE_ROOT}/install/install.sh" --source-root "${SOURCE_ROOT}" "${INSTALL_ARGS[@]}"
fi
fi
