#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/jarvis-docker-common.sh
# Purpose : Shared helpers of the local Docker scripts (start, status, stop, clean)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Sourced (never executed) by docker/jarvis-start.sh, jarvis-status.sh, jarvis-stop.sh and jarvis-clean.sh. Loads the
# shared bash library (colors, logging, error handling, exit codes) and defines:
#   DOCKER_DIR, REPO_ROOT, LOCAL_DIR, COMPOSE_FILE, PROJECT   paths and names
#   compose ARGS...        docker compose on the local project
#   require_docker         docker CLI, Compose v2 and a reachable daemon (E_DEPS / E_STATE),
#                          with the exact fix printed (package to install, group, session)
#   project_running        success when at least one container of the project exists
#   https_url              URL of the web UI (https://localhost:<port>/)

DOCKER_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$DOCKER_DIR/.." && pwd)"
# shellcheck source=../scripts/lib/jarvis-common.sh
. "$REPO_ROOT/scripts/lib/jarvis-common.sh"

# Used by the scripts that source this file.
# shellcheck disable=SC2034
readonly LOCAL_DIR="$DOCKER_DIR/.local"
readonly COMPOSE_FILE="$DOCKER_DIR/docker-compose.local.yml"
readonly PROJECT="jarvis-local"
# shellcheck disable=SC2034
readonly IMAGE="jarvis:local"
# shellcheck disable=SC2034
readonly NETWORK="jarvis-local-net"
export JARVIS_HTTPS_PORT="${JARVIS_HTTPS_PORT:-8443}"
export JARVIS_HTTP_PORT="${JARVIS_HTTP_PORT:-8080}"

# compose ARGS...: docker compose on the local project file.
compose() { docker compose -f "$COMPOSE_FILE" -p "$PROJECT" "$@"; }

# docker_pkg_hint KIND: install command for "engine", "compose" or "buildx" on this distribution.
# Ubuntu/Debian packages differ between the distribution's docker.io and Docker's docker-ce:
# the flavor is the package owning the docker binary (stale docker-ce packages left by a
# release upgrade do not count).
docker_pkg_hint() {
  local ce=0 bin
  bin="$(command -v docker 2>/dev/null || true)"
  [[ -n "$bin" ]] && command -v dpkg >/dev/null 2>&1 &&
    dpkg -S "$(readlink -f "$bin")" 2>/dev/null | grep -q '^docker-ce' && ce=1
  if command -v apt-get >/dev/null 2>&1; then
    case "$1:$ce" in
      engine:*) echo "sudo apt install docker.io" ;;
      compose:1) echo "sudo apt install docker-compose-plugin" ;;
      compose:0) echo "sudo apt install docker-compose-v2" ;;
      buildx:1) echo "sudo apt install docker-buildx-plugin" ;;
      buildx:0) echo "sudo apt install docker-buildx" ;;
    esac
  elif command -v dnf >/dev/null 2>&1; then
    case "$1" in
      engine) echo "sudo dnf install docker-ce (https://docs.docker.com/engine/install/)" ;;
      compose) echo "sudo dnf install docker-compose-plugin" ;;
      buildx) echo "sudo dnf install docker-buildx-plugin" ;;
    esac
  else
    echo "see https://docs.docker.com/engine/install/"
  fi
}

# docker_socket: path of the daemon's Unix socket (DOCKER_HOST=unix://… or the default).
docker_socket() {
  local host="${DOCKER_HOST:-unix:///var/run/docker.sock}"
  [[ "$host" == unix://* ]] && echo "${host#unix://}"
  return 0
}

# diagnose_daemon: explain why the Docker daemon is unreachable, then exit E_STATE.
# Distinguishes a stopped daemon, a user outside the docker group, and a membership that the
# current login session does not have yet (usermod only applies to new sessions).
diagnose_daemon() {
  local sock user err
  sock="$(docker_socket)"
  user="$(id -un)"
  err="$(docker info 2>&1 >/dev/null | grep -m1 -iE 'error|denied|cannot' || true)"
  [[ -n "$err" ]] && log_debug "docker info: $err"
  if [[ -n "$sock" && ! -S "$sock" ]]; then
    log_info "no Docker socket at $sock: start the daemon with"
    log_info "  sudo systemctl enable --now docker.socket docker.service"
    die "the Docker daemon is not running" "$E_STATE"
  fi
  if [[ -n "$sock" && ! -w "$sock" ]]; then
    local group owner
    group="$(stat -c %G "$sock" 2>/dev/null || echo docker)"
    owner="$(stat -c %U "$sock" 2>/dev/null || echo root)"
    if [[ "$owner" == "$user" ]]; then
      log_info "$sock belongs to $user but is not writable: check its mode (ls -l $sock)"
    elif id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
      log_info "$user is in the '$group' group, but this login session started before it was added:"
      log_info "  newgrp $group      (this shell only), or log out and back in (all sessions)"
    else
      log_info "$sock belongs to group '$group' and $user is not a member:"
      log_info "  sudo usermod -aG $group $user && newgrp $group"
      log_info "  (members of '$group' are root-equivalent on this host)"
    fi
    die "permission denied on the Docker socket $sock" "$E_STATE"
  fi
  die "cannot reach the Docker daemon${err:+: $err}" "$E_STATE"
}

# require_docker: docker CLI with the Compose v2 plugin, and a daemon we may talk to.
require_docker() {
  command -v docker >/dev/null 2>&1 || die "docker is missing: $(docker_pkg_hint engine)" "$E_DEPS"
  docker compose version >/dev/null 2>&1 ||
    die "Docker Compose v2 is missing (docker compose plugin): $(docker_pkg_hint compose)" "$E_DEPS"
  docker info >/dev/null 2>&1 || diagnose_daemon
}

# project_running: success when containers of the project exist (running or not).
project_running() { [[ -n "$(compose ps -a -q 2>/dev/null)" ]]; }

# https_url: address of the web UI.
https_url() { echo "https://localhost:${JARVIS_HTTPS_PORT}/"; }
