#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/jarvis-start.sh
# Purpose : Build the Docker images and start Jarvis locally (core + API + nginx over HTTPS)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Tries Jarvis on a workstation, with nothing installed on the host but Docker:
#   1. prepares docker/.local/ once (kept afterwards, edit it freely):
#      config.yaml (from config/config.example.yaml), jarvis.env (simulated relay/LEDs,
#      voice off, API reachable by nginx), nginx.conf (redirect to the published HTTPS
#      port) and a TLS certificate signed by a private "Jarvis Local CA" for localhost;
#   2. builds the image (first build: 10-20 min, PyTorch CPU + insightface compilation);
#   3. downloads the AI models into the data volume on the first start (a few minutes);
#   4. starts the Compose project "jarvis-local" and waits until the web UI answers;
#   5. prints the URL, the CA to trust and the first login (admin / admin).
# Only 127.0.0.1 is published: the local test is not reachable from the network.
#
# Usage:
#   docker/jarvis-start.sh [--rtsp URL] [--no-build] [--rebuild] [--skip-models] [--api-only]
#                   [--https-port N] [--http-port N] [--help]
#     --rtsp URL       camera stream to analyze (rtsp://host:554/path, without credentials:
#                      put them in docker/.local/jarvis.env as JARVIS__SECRETS__CAMERA_*)
#     --no-build       use the existing jarvis:local image
#     --rebuild        rebuild without the Docker cache
#     --skip-models    do not download the models (the core cannot start without them)
#     --api-only       start the web API and nginx only (UI tour, core shown as unreachable)
#     --https-port N   published HTTPS port (default 8443, env JARVIS_HTTPS_PORT)
#     --http-port N    published HTTP port (default 8080, env JARVIS_HTTP_PORT)
#
# Environment: JARVIS_DOCKER_SUBNET=10.250.250.0/24 forces the subnet of the project network
# (by default Docker picks one; when its address pools are exhausted, a free /24 is chosen).
#
# Prerequisites: Docker Engine with the Compose v2 plugin, openssl (certificate), about 8 GB
# of disk (image ~4 GB + models); Internet access for the build and the first model download.
# buildx is optional (without it the classic builder is used). Each missing piece is reported with
# the command that fixes it: package to install (docker.io / docker-ce flavors), daemon to start,
# docker group to join, or 'newgrp docker' when the membership is newer than the login session.
#
# Exit codes:
#   0  Jarvis is up (URL printed)
#   1  a command failed (build, compose)
#   2  usage error
#   4  prerequisite missing (docker, compose, openssl)
#   7  Docker daemon unreachable, or the web UI did not come up in time
#   130 interrupted

. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/jarvis-docker-common.sh"

RTSP=""
BUILD=1
NO_CACHE=0
MODELS=1
API_ONLY=0

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --rtsp) RTSP="${2:?--rtsp needs a URL}"; shift ;;
      --no-build) BUILD=0 ;;
      --rebuild) NO_CACHE=1 ;;
      --skip-models) MODELS=0 ;;
      --api-only) API_ONLY=1; MODELS=0 ;;
      --https-port) JARVIS_HTTPS_PORT="${2:?--https-port needs a number}"; shift ;;
      --http-port) JARVIS_HTTP_PORT="${2:?--http-port needs a number}"; shift ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$JARVIS_HTTPS_PORT" =~ ^[0-9]+$ && "$JARVIS_HTTP_PORT" =~ ^[0-9]+$ ]] || die "ports must be numbers" "$E_USAGE"
  if [[ -n "$RTSP" && ! "$RTSP" =~ ^rtsps?://[^@/[:space:]]+(/.*)?$ ]]; then
    die "--rtsp: rtsp://host[:port]/path expected, without user:password (see --help)" "$E_USAGE"
  fi
}

# set_env KEY VALUE: add or replace one line of docker/.local/jarvis.env.
set_env() {
  local file="$LOCAL_DIR/jarvis.env"
  if grep -q "^$1=" "$file" 2>/dev/null; then
    sed -i "s|^$1=.*|$1=$2|" "$file"
  else
    printf '%s=%s\n' "$1" "$2" >>"$file"
  fi
}

# prepare_local: generate the local configuration, environment, nginx site and certificate once.
prepare_local() {
  log_step "Local configuration (docker/.local/)"
  install -d -m 700 "$LOCAL_DIR"
  if [[ ! -f "$LOCAL_DIR/config.yaml" ]]; then
    install -m 644 "$REPO_ROOT/config/config.example.yaml" "$LOCAL_DIR/config.yaml"
    log_ok "config.yaml created from config/config.example.yaml"
  fi
  if [[ ! -f "$LOCAL_DIR/jarvis.env" ]]; then
    cat >"$LOCAL_DIR/jarvis.env" <<'EOF'
# Jarvis local test (docker/jarvis-start.sh): JARVIS__SECTION__KEY overrides of config.yaml.
# The API listens on the Compose network and trusts nginx's forwarded headers.
JARVIS__API__HOST=0.0.0.0
JARVIS__API__FORWARDED_ALLOW_IPS=*
# No hardware in a container: simulated relay and LEDs (pulses appear in the logs), no door
# sensor, no microphone/speaker, so the voice assistant is off.
JARVIS__HARDWARE__GARAGE__BACKEND=mock
JARVIS__HARDWARE__LED_GREEN__BACKEND=mock
JARVIS__HARDWARE__LED_RED__BACKEND=mock
JARVIS__HARDWARE__DOOR_SENSOR__BACKEND=none
JARVIS__AUDIO__ENABLED=false
# No PTZ unless you point Jarvis at a real ONVIF camera.
JARVIS__PTZ__ENABLED=false
# Camera account (write it here, never in the URL):
# JARVIS__SECRETS__CAMERA_USERNAME=jarvis
# JARVIS__SECRETS__CAMERA_PASSWORD=change-me
EOF
    chmod 600 "$LOCAL_DIR/jarvis.env"
    log_ok "jarvis.env created (simulated hardware, voice off)"
  fi
  [[ -n "$RTSP" ]] && { set_env JARVIS__CAMERA__RTSP_URL "$RTSP"; log_ok "camera stream: $RTSP"; }
  # nginx: same site as docker/nginx-docker.conf, but HTTP redirects to the published HTTPS port.
  sed "s|return 301 https://\$host\$request_uri;|return 301 https://\$host:${JARVIS_HTTPS_PORT}\$request_uri;|" \
    "$DOCKER_DIR/nginx-docker.conf" >"$LOCAL_DIR/nginx.conf"
  if [[ ! -f "$LOCAL_DIR/tls/jarvis.crt" ]]; then
    require_cmd openssl
    JARVIS_COLOR=never "$REPO_ROOT/scripts/jarvis-cert.sh" --tls-dir "$LOCAL_DIR/tls" --no-reload --yes \
      issue --mode local --name localhost --name jarvis.local --ip 127.0.0.1 >/dev/null
    chmod 644 "$LOCAL_DIR/tls/jarvis.key"   # read by the nginx container; local test only
    log_ok "certificate issued by the Jarvis Local CA (docker/.local/tls/ca/jarvis-ca.crt)"
  fi
}

# free_subnet: first candidate /24 overlapping neither a local route nor a Docker network.
free_subnet() {
  local used
  local -a nets
  mapfile -t nets < <(docker network ls -q)
  used="$( { ip -4 route 2>/dev/null | awk '$1 ~ /\// { print $1 }'
             docker network inspect "${nets[@]}" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null | tr ' ' '\n'
           } | grep -E '^[0-9.]+/[0-9]+$' || true)"
  python3 - "$used" <<'PY'
import ipaddress, sys
used = [ipaddress.ip_network(u, strict=False) for u in sys.argv[1].split()]
for cand in ("10.250.250.0/24", "10.251.251.0/24", "172.31.250.0/24", "192.168.250.0/24", "10.99.99.0/24"):
    net = ipaddress.ip_network(cand)
    if not any(net.overlaps(u) for u in used):
        print(cand)
        break
PY
}

# ensure_network: create the project network once (see JARVIS_DOCKER_SUBNET in the header).
ensure_network() {
  docker network inspect "$NETWORK" >/dev/null 2>&1 && return 0
  local -a create=(docker network create --label "com.docker.compose.project=$PROJECT")
  if [[ -n "${JARVIS_DOCKER_SUBNET:-}" ]]; then
    "${create[@]}" --subnet "$JARVIS_DOCKER_SUBNET" "$NETWORK" >/dev/null
    log_ok "network $NETWORK created ($JARVIS_DOCKER_SUBNET, from JARVIS_DOCKER_SUBNET)"
    return 0
  fi
  if "${create[@]}" "$NETWORK" >/dev/null 2>&1; then
    log_ok "network $NETWORK created (Docker default pools)"
    return 0
  fi
  # Typical on managed workstations: default-address-pools of daemon.json fully used.
  local subnet
  subnet="$(free_subnet)"
  [[ -n "$subnet" ]] || die "no free subnet for the Docker network: set JARVIS_DOCKER_SUBNET=a.b.c.0/24" "$E_STATE"
  "${create[@]}" --subnet "$subnet" "$NETWORK" >/dev/null
  log_ok "network $NETWORK created on $subnet (the Docker address pools are exhausted)"
}

# build_image: build jarvis:local (cached layers make later builds fast).
build_image() {
  ((BUILD)) || { log_info "build skipped (--no-build)"; return 0; }
  log_step "Building the image $IMAGE (first time: 10-20 min)"
  # Compose >= 2.39 builds through Bake, which needs buildx; without it, Compose warns and
  # falls back. Choose the classic builder explicitly and say how to get buildx.
  if [[ -z "${COMPOSE_BAKE:-}" ]] && ! docker buildx version >/dev/null 2>&1; then
    export COMPOSE_BAKE=false
    log_info "buildx not installed: classic builder (optional: $(docker_pkg_hint buildx))"
  fi
  local -a args=(build)
  ((NO_CACHE)) && args+=(--no-cache)
  compose "${args[@]}" core
}

# ensure_models: download and export the AI models into the data volume on the first start.
ensure_models() {
  ((MODELS)) || { log_warn "models not downloaded: the core will not start until 'docker/jarvis-start.sh' runs without --skip-models"; return 0; }
  if compose run --rm --no-deps --entrypoint test core -d /var/lib/jarvis/models/yolo11n_openvino_model 2>/dev/null; then
    log_ok "AI models already in the data volume"
    return 0
  fi
  log_step "Downloading and exporting the AI models (first start, a few minutes)"
  compose run --rm --no-deps core setup-models
}

# wait_ready: poll the web UI through nginx until it answers (or time out).
wait_ready() {
  local url i code
  url="$(https_url)api/ui"
  log_step "Waiting for $url"
  for i in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --cacert "$LOCAL_DIR/tls/ca/jarvis-ca.crt" "$url" 2>/dev/null || true)"
    [[ "$code" == 200 ]] && { log_ok "web UI up"; return 0; }
    sleep 3
    ((i % 10 == 0)) && log_info "still starting… ($((i * 3)) s)"
  done
  compose ps
  die "the web UI did not answer within 180 s: run docker/jarvis-status.sh --logs 50" "$E_STATE"
}

# summary: what to do next.
summary() {
  local camera="${RTSP:-$(sed -n 's/^JARVIS__CAMERA__RTSP_URL=//p' "$LOCAL_DIR/jarvis.env" | head -1)}"
  cat <<MSG

${C_GREEN}${C_BOLD}Jarvis is running locally${C_RESET} (jarvis-home $(jarvis_version), project $PROJECT)
  Web UI        : $(https_url)
  First login   : admin / admin (a new password is required at once)
  Trust the CA  : docker/.local/tls/ca/jarvis-ca.crt (or accept the browser warning)
  Camera        : ${camera:-not set (Settings > Camera stream in the UI, or --rtsp rtsp://…)}
  Hardware      : simulated relay and LEDs (see the logs), voice off
  Next          : docker/jarvis-status.sh   docker/jarvis-stop.sh   docker/jarvis-clean.sh
MSG
}

# main ARGS...: prepare, build, models, start, wait, summary.
main() {
  parse_args "$@"
  require_docker
  require_cmd curl sed
  log_info "jarvis-home $(jarvis_version) · local Docker test"
  prepare_local
  build_image
  ensure_network
  ensure_models
  log_step "Starting the containers"
  if ((API_ONLY)); then
    compose up -d --no-deps api nginx
  else
    compose up -d
  fi
  wait_ready
  summary
}

main "$@"
