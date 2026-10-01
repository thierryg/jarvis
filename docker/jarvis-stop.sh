#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/jarvis-stop.sh
# Purpose : Stop the local Docker test (containers removed, data, models and images kept)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Stops and removes the containers and the network of the "jarvis-local" Compose project.
# The data volume (database, models, snapshots), the image jarvis:local and docker/.local/
# are kept, so docker/jarvis-start.sh restarts in seconds. docker/jarvis-clean.sh frees them.
#
# Usage:
#   docker/jarvis-stop.sh [--timeout S] [--help]
#     --timeout S   seconds given to each container to stop gracefully (default 20)
#
# Prerequisites: Docker Engine with the Compose v2 plugin.
#
# Exit codes:
#   0  stopped (or already stopped)
#   1  docker compose failed
#   2  usage error
#   4  prerequisite missing
#   7  Docker daemon unreachable

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/jarvis-docker-common.sh"

TIMEOUT=20

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --timeout) TIMEOUT="${2:?--timeout needs seconds}"; shift ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || die "--timeout needs a number of seconds" "$E_USAGE"
}

# main ARGS...: graceful stop of the whole project.
main() {
  parse_args "$@"
  require_docker
  if ! project_running; then
    log_ok "the local test is not running"
    exit "$E_OK"
  fi
  log_step "Stopping $PROJECT (graceful, ${TIMEOUT} s per container)"
  compose down --timeout "$TIMEOUT"
  log_ok "stopped; data, models and image kept (docker/jarvis-start.sh restarts, docker/jarvis-clean.sh frees them)"
}

main "$@"
