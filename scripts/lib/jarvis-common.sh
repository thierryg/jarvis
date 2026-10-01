#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/lib/jarvis-common.sh
# Purpose : Shared bash library: colors, logging, error handling, exit codes, checks
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Sourced (never executed) by every Jarvis shell script:
#
#   SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=lib/jarvis-common.sh
#   . "$SCRIPT_DIR/lib/jarvis-common.sh"
#
# It provides:
#   * strict mode (set -Eeuo pipefail) and an ERR trap that reports the failing
#     command, file and line, then exits with E_RUNTIME (or the command status);
#   * colorized log helpers (log_info, log_ok, log_warn, log_error, log_step,
#     log_debug), disabled automatically when stderr is not a terminal, when
#     NO_COLOR is set (https://no-color.org) or with JARVIS_COLOR=never;
#   * die MESSAGE [CODE]: log an error and exit with a documented code;
#   * requirement checks: require_root, require_cmd, require_file;
#   * cleanup registration (on_exit) run by the EXIT trap in LIFO order;
#   * small utilities: confirm, jarvis_version, is_true.
#
# Standard exit codes (shared by every script, documented in their headers):
#   0  E_OK        success
#   1  E_RUNTIME   unexpected runtime failure (a command failed)
#   2  E_USAGE     invalid arguments / usage error
#   3  E_PRIV      insufficient privileges (root required)
#   4  E_DEPS      missing prerequisite (command, package, file)
#   5  E_CONFIG    invalid configuration or input data
#   6  E_NETWORK   network / remote service failure
#   7  E_STATE     unexpected system state (check failed, resource busy)
#   8  E_WARN      completed with warnings (e.g. certificate close to expiry)
#   130            interrupted (SIGINT)
#
# Environment variables:
#   NO_COLOR / JARVIS_COLOR=always|never|auto   color control
#   JARVIS_DEBUG=1                              enable log_debug output and xtrace

# Guard against double sourcing.
[[ -n "${_JARVIS_COMMON_SH:-}" ]] && return 0
readonly _JARVIS_COMMON_SH=1

set -Eeuo pipefail
shopt -s inherit_errexit 2>/dev/null || true

# --- exit codes ---------------------------------------------------------------
# Used by the scripts that source this library.
# shellcheck disable=SC2034
readonly E_OK=0 E_RUNTIME=1 E_USAGE=2 E_PRIV=3 E_DEPS=4 E_CONFIG=5 E_NETWORK=6 E_STATE=7 E_WARN=8

# Name of the calling script, used as the log prefix.
SCRIPT_NAME="$(basename -- "${0}")"
# Root of the repository (scripts/lib/.. /..), valid when run from a checkout.
JARVIS_REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"

# --- colors -------------------------------------------------------------------
# setup_colors: define the color variables according to the terminal and the
# NO_COLOR / JARVIS_COLOR conventions. Called once at source time.
setup_colors() {
  local mode="${JARVIS_COLOR:-auto}"
  if [[ "$mode" == "never" || -n "${NO_COLOR:-}" ]] || { [[ "$mode" == "auto" ]] && [[ ! -t 2 ]]; }; then
    C_RESET="" C_BOLD="" C_DIM="" C_RED="" C_GREEN="" C_YELLOW="" C_BLUE="" C_CYAN=""
  else
    C_RESET=$'\e[0m' C_BOLD=$'\e[1m' C_DIM=$'\e[2m' C_RED=$'\e[31m' C_GREEN=$'\e[32m'
    C_YELLOW=$'\e[33m' C_BLUE=$'\e[34m' C_CYAN=$'\e[36m'
  fi
  export C_RESET C_BOLD C_DIM C_RED C_GREEN C_YELLOW C_BLUE C_CYAN
}
setup_colors

# --- logging (all on stderr, so stdout stays usable for data) ----------------
# _log LEVEL COLOR MESSAGE...: internal formatter "HH:MM:SS LEVEL script: message".
_log() {
  local level="$1" color="$2"
  shift 2
  printf '%s%s%s %s%-5s%s %s%s:%s %s\n' "$C_DIM" "$(date +%H:%M:%S)" "$C_RESET" \
    "$color$C_BOLD" "$level" "$C_RESET" "$C_DIM" "$SCRIPT_NAME" "$C_RESET" "$*" >&2
}
log_info()  { _log INFO "$C_BLUE" "$@"; }
log_ok()    { _log OK "$C_GREEN" "$@"; }
log_warn()  { _log WARN "$C_YELLOW" "$@"; }
log_error() { _log ERROR "$C_RED" "$@"; }
log_debug() { [[ "${JARVIS_DEBUG:-0}" == 1 ]] && _log DEBUG "$C_DIM" "$@"; return 0; }
# log_step MESSAGE: highlighted section title.
log_step()  { printf '\n%s==>%s %s%s%s\n' "$C_CYAN$C_BOLD" "$C_RESET" "$C_BOLD" "$*" "$C_RESET" >&2; }

# die MESSAGE [CODE]: log an error and exit (default code E_RUNTIME).
die() {
  local msg="$1" code="${2:-$E_RUNTIME}"
  log_error "$msg"
  exit "$code"
}

# --- traps --------------------------------------------------------------------
_JARVIS_CLEANUPS=()
# on_exit COMMAND: register a cleanup command run at exit (last registered first).
on_exit() { _JARVIS_CLEANUPS+=("$1"); }

# _jarvis_exit_trap: run the registered cleanups. The ERR trap is removed first so that a
# deliberate non-zero exit (die, verdict codes) is not reported again as a failed command;
# the script keeps the status given to "exit".
_jarvis_exit_trap() {
  local i
  trap - ERR
  set +e
  for ((i = ${#_JARVIS_CLEANUPS[@]} - 1; i >= 0; i--)); do
    eval "${_JARVIS_CLEANUPS[i]}"
  done
  return 0
}

# _jarvis_err_trap: report the failing command with its location, then exit with its status.
_jarvis_err_trap() {
  local status=$? cmd="${BASH_COMMAND}" line="${BASH_LINENO[0]}" src="${BASH_SOURCE[1]:-$SCRIPT_NAME}"
  log_error "command failed (status ${status}) at $(basename -- "$src"):${line}: ${cmd}"
  exit "$status"
}

trap _jarvis_exit_trap EXIT
trap _jarvis_err_trap ERR
trap 'log_warn "interrupted"; exit 130' INT TERM
[[ "${JARVIS_DEBUG:-0}" == 1 ]] && set -x

# --- requirement checks ------------------------------------------------------
# require_root: exit with E_PRIV unless running as uid 0.
require_root() { [[ "${EUID}" -eq 0 ]] || die "this command must be run as root (use sudo)" "$E_PRIV"; }

# require_cmd CMD...: exit with E_DEPS if one of the commands is not in PATH.
require_cmd() {
  local c missing=()
  for c in "$@"; do command -v "$c" >/dev/null 2>&1 || missing+=("$c"); done
  ((${#missing[@]} == 0)) || die "missing required command(s): ${missing[*]}" "$E_DEPS"
}

# require_file PATH...: exit with E_DEPS if a file does not exist or is unreadable.
require_file() {
  local f
  for f in "$@"; do [[ -r "$f" ]] || die "required file not found or unreadable: $f" "$E_DEPS"; done
}

# --- utilities ---------------------------------------------------------------
# is_true VALUE: success for 1/yes/true/on (case-insensitive).
is_true() { [[ "${1,,}" =~ ^(1|y|yes|true|on)$ ]]; }

# confirm PROMPT: ask a yes/no question on the terminal; ASSUME_YES=1 answers yes.
confirm() {
  is_true "${ASSUME_YES:-0}" && return 0
  local answer
  read -r -p "${C_YELLOW}?${C_RESET} $1 [y/N] " answer
  is_true "$answer"
}

# jarvis_version: print the software version (single source jarvis/VERSION).
jarvis_version() {
  local f
  for f in "$JARVIS_REPO_ROOT/jarvis/VERSION" /opt/jarvis/src/jarvis/VERSION; do
    [[ -r "$f" ]] && { tr -d '[:space:]' <"$f"; echo; return 0; }
  done
  echo "unknown"
}
