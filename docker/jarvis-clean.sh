#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/jarvis-clean.sh
# Purpose : Stop the local Docker test if running and free its images (optionally data and config)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Frees what docker/jarvis-start.sh created, in three levels:
#   default     stop the project if it runs, remove its containers, network and the image
#               jarvis:local (plus the dangling build layers it left)
#   --volumes   also delete the data volume: database, AI models (a new start downloads them
#               again), snapshots, recordings — asks for confirmation
#   --purge     everything above, plus docker/.local/ (config, env, certificate and local CA:
#               browsers that trusted the CA will warn again) and the nginx image
# The shared python base image is never removed (other projects may use it).
#
# Usage:
#   docker/jarvis-clean.sh [--volumes] [--purge] [--yes] [--help]
#     --volumes   delete the data volume too (asks for confirmation)
#     --purge     delete volumes, docker/.local/ and the nginx image (asks for confirmation)
#     --yes       do not ask for confirmation (env ASSUME_YES=1)
#
# Prerequisites: Docker Engine with the Compose v2 plugin.
#
# Exit codes:
#   0  cleaned (or nothing to clean)
#   1  a docker command failed
#   2  usage error, or confirmation declined
#   4  prerequisite missing
#   7  Docker daemon unreachable

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/jarvis-docker-common.sh"

VOLUMES=0
PURGE=0

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --volumes) VOLUMES=1 ;;
      --purge) PURGE=1; VOLUMES=1 ;;
      --yes | -y) export ASSUME_YES=1 ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
}

# human_size BYTES: size of an image in a readable unit.
human_size() { awk -v b="${1:-0}" 'BEGIN { split("B KB MB GB", u); i = 1; while (b >= 1000 && i < 4) { b /= 1000; i++ } printf "%.1f %s", b, u[i] }'; }

# main ARGS...: stop, then remove images, volumes and local files as requested.
main() {
  parse_args "$@"
  require_docker
  if ((VOLUMES)); then
    confirm "Delete the Jarvis local data (database, models, snapshots)$( ((PURGE)) && echo ', docker/.local/ (config, certificate, CA)')?" ||
      die "aborted: nothing deleted" "$E_USAGE"
  fi
  local freed=0 size
  size="$(docker image inspect --format '{{.Size}}' "$IMAGE" 2>/dev/null || echo 0)"
  log_step "Cleaning $PROJECT"
  local -a down=(down --remove-orphans --rmi local --timeout 20)
  ((VOLUMES)) && down+=(--volumes)
  if project_running || docker image inspect "$IMAGE" >/dev/null 2>&1; then
    compose "${down[@]}"
    log_ok "containers and network removed$( ((VOLUMES)) && echo ', data volume deleted')"
  else
    log_info "nothing running"
    ((VOLUMES)) && docker volume rm "${PROJECT}_data" "${PROJECT}_run" >/dev/null 2>&1 || true
  fi
  if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker image rm "$IMAGE" >/dev/null
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network rm "$NETWORK" >/dev/null && log_ok "network $NETWORK removed"
  fi
  freed=$((freed + size))
  # Dangling layers left by rebuilds of jarvis:local (only untagged images of this build).
  docker image prune --force --filter "label=com.docker.compose.project=$PROJECT" >/dev/null 2>&1 || true
  if ((PURGE)); then
    docker image rm nginx:1.27-alpine >/dev/null 2>&1 && log_ok "nginx image removed" || true
    rm -rf -- "$LOCAL_DIR"
    log_ok "docker/.local/ removed (config, environment, certificate and local CA)"
  fi
  log_ok "image $IMAGE removed ($(human_size "$freed") freed)"
  ((VOLUMES)) || log_info "data and models kept in the volume ${PROJECT}_data (--volumes deletes them)"
}

main "$@"
