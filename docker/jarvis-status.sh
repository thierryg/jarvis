#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/jarvis-status.sh
# Purpose : State of the local Docker test: containers, health, resources, web UI, certificate
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Reports on the "jarvis-local" Compose project started by docker/jarvis-start.sh:
#   - containers: state, health, uptime, published ports;
#   - resources: CPU, memory, network and block I/O of each container (docker stats);
#   - image and volumes: size of jarvis:local, data and run volumes;
#   - web UI: HTTP code of https://localhost:<port>/api/ui through nginx (certificate verified
#     against the local CA), HTTP -> HTTPS redirect;
#   - core: status of the core seen by the API (camera, analysis rate, tracks, voice);
#   - certificate: validity and remaining days (scripts/jarvis-cert.sh status);
#   - optionally the last log lines of every service (--logs N).
#
# Usage:
#   docker/jarvis-status.sh [--logs N] [--json] [--help]
#     --logs N   also print the last N log lines of each service
#     --json     machine-readable summary (docker compose ps --format json + checks)
#
# Prerequisites: Docker Engine with the Compose v2 plugin, curl.
#
# Exit codes:
#   0  every service is running (and healthy when it has a health check), web UI answers
#   2  usage error
#   4  prerequisite missing
#   7  not started, a service is stopped/unhealthy, or the web UI does not answer
#   8  running, with warnings (e.g. core unhealthy while the models download, certificate expiring)

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/jarvis-docker-common.sh"

LOGS=0
JSON=0

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --logs) LOGS="${2:?--logs needs a number}"; shift ;;
      --json) JSON=1 ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$LOGS" =~ ^[0-9]+$ ]] || die "--logs needs a number" "$E_USAGE"
}

# row LABEL VALUE: aligned "label value" line.
row() { printf '  %s%-16s%s %s\n' "$C_DIM" "$1" "$C_RESET" "$2"; }

# state_color TEXT: green for running/healthy/200, red for exited/unhealthy, yellow otherwise.
state_color() {
  case "$1" in
    running | healthy | 200 | 301 | VALID | ok) printf '%s%s%s' "$C_GREEN" "$1" "$C_RESET" ;;
    exited | dead | unhealthy | down | EXPIRED | REVOKED | INVALID | 000) printf '%s%s%s' "$C_RED" "$1" "$C_RESET" ;;
    *) printf '%s%s%s' "$C_YELLOW" "$1" "$C_RESET" ;;
  esac
}

# main ARGS...: collect every check, print it, compute the verdict.
main() {
  parse_args "$@"
  require_docker
  require_cmd curl
  if ! project_running; then
    ((JSON)) && { echo '{"running": false}'; exit "$E_STATE"; }
    log_warn "the local test is not started: docker/jarvis-start.sh"
    exit "$E_STATE"
  fi
  local verdict=$E_OK svc state health line
  local -a services=(core api nginx)

  # Containers: one line per service (state, health, uptime).
  local -A STATE=() HEALTH=()
  for svc in "${services[@]}"; do
    line="$(docker inspect --format '{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}|{{.State.StartedAt}}' \
      "$(compose ps -a -q "$svc" 2>/dev/null | head -1)" 2>/dev/null || echo "down|-|")"
    IFS='|' read -r state health _ <<<"$line"
    STATE[$svc]="${state:-down}"
    HEALTH[$svc]="${health:--}"
    if [[ "${STATE[$svc]}" != running ]]; then verdict=$E_STATE
    elif [[ "${HEALTH[$svc]}" == unhealthy || "${HEALTH[$svc]}" == starting ]]; then ((verdict == E_OK)) && verdict=$E_WARN
    fi
  done

  # Web UI through nginx, certificate verified against the local CA; HTTP redirect.
  local ca="$LOCAL_DIR/tls/ca/jarvis-ca.crt" ui redirect
  ui="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 --cacert "$ca" "$(https_url)api/ui" 2>/dev/null || true)"
  redirect="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://localhost:${JARVIS_HTTP_PORT}/" 2>/dev/null || true)"
  [[ "$ui" == 200 ]] || verdict=$E_STATE

  # Core status as seen by the API (from inside the API container: no session needed there).
  local core
  core="$(compose exec -T api python -c "
from jarvis.config.settings import load_settings
from jarvis.core.control import ControlClient
try:
    s = ControlClient(load_settings().control.socket_path).call('status')
    print(f\"camera {'connected' if s.get('camera_connected') else 'disconnected'} · {s.get('vision_fps', 0)} fps · \"
          f\"{s.get('tracks', 0)} people · {s.get('vehicles', 0)} vehicles · voice {s.get('voice_state', '?')}\")
except Exception as exc:
    print(f'unreachable ({exc.__class__.__name__})')" 2>/dev/null || echo "unknown")"

  # Certificate of the local test.
  local cert=""
  if [[ -r "$LOCAL_DIR/tls/jarvis.crt" ]]; then
    cert="$(JARVIS_COLOR=never "$REPO_ROOT/scripts/jarvis-cert.sh" --tls-dir "$LOCAL_DIR/tls" status --json --no-revocation 2>/dev/null |
      sed -n 's/.*"state":"\([^"]*\)".*"days_left":\([-0-9]*\).*/\1 · \2 days left/p' || true)"
  fi

  if ((JSON)); then
    printf '{"running": true, "services": {'
    local first=1
    for svc in "${services[@]}"; do
      ((first)) || printf ', '
      first=0
      printf '"%s": {"state": "%s", "health": "%s"}' "$svc" "${STATE[$svc]}" "${HEALTH[$svc]}"
    done
    printf '}, "web_ui_http": "%s", "http_redirect": "%s", "url": "%s", "exit_code": %d}\n' "$ui" "$redirect" "$(https_url)" "$verdict"
    exit "$verdict"
  fi

  printf '\n%sJarvis local test%s  %s· jarvis-home %s · project %s%s\n' "$C_BOLD" "$C_RESET" "$C_DIM" "$(jarvis_version)" "$PROJECT" "$C_RESET"
  printf '\n%sContainers%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  for svc in "${services[@]}"; do
    row "$svc" "$(state_color "${STATE[$svc]}")  health $(state_color "${HEALTH[$svc]}")"
  done
  printf '\n%sResources%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  # shellcheck disable=SC2046  # one ID per word
  docker stats --no-stream --format '  {{.Name}}\t CPU {{.CPUPerc}}\t MEM {{.MemUsage}}\t NET {{.NetIO}}\t DISK {{.BlockIO}}' \
    $(compose ps -q) 2>/dev/null | column -t -s $'\t' || true
  printf '\n%sImage and volumes%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  row "image" "$(docker image inspect --format '{{.Id}}' "$IMAGE" >/dev/null 2>&1 &&
    docker image ls "$IMAGE" --format '{{.Repository}}:{{.Tag}} · {{.Size}} · built {{.CreatedSince}}' || echo "not built")"
  local vol
  for vol in data run; do
    row "volume $vol" "$(docker volume inspect "${PROJECT}_$vol" --format '{{.Mountpoint}}' 2>/dev/null || echo "absent")"
  done
  printf '\n%sChecks%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  row "web UI" "$(https_url) → HTTP $(state_color "${ui:-000}")"
  row "HTTP → HTTPS" "http://localhost:${JARVIS_HTTP_PORT}/ → HTTP $(state_color "${redirect:-000}")"
  row "core" "$core"
  row "certificate" "${cert:-missing (docker/jarvis-start.sh creates it)}"
  if ((LOGS > 0)); then
    printf '\n%sLast %d log lines%s\n' "$C_CYAN$C_BOLD" "$LOGS" "$C_RESET"
    compose logs --no-color --tail "$LOGS" 2>/dev/null | sed 's/^/  /'
  fi
  echo
  case "$verdict" in
    "$E_OK") log_ok "all services up" ;;
    "$E_WARN") log_warn "running with warnings (a health check is starting or failing: docker/jarvis-status.sh --logs 50)" ;;
    *) log_error "not fully up: docker/jarvis-status.sh --logs 50" ;;
  esac
  exit "$verdict"
}

main "$@"
