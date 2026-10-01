#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : deploy/ansible/tests/jarvis-container-test.sh
# Purpose : End-to-end test of the playbook against an Ubuntu 26.04 systemd container over SSH
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Builds tests/Dockerfile.ubuntu2604, starts it (systemd as PID 1, sshd on 127.0.0.1:PORT),
# creates a throw-away SSH key and inventory, then runs "jarvis-deploy.sh deploy" and, unless
# --once, a second time to prove idempotence (no task may report "changed" on re-run
# except the documented always-changed checks). Kernel, udev and AppArmor tasks are skipped
# in a container (the playbook detects it); everything else runs for real.
#
# Usage:
#   tests/jarvis-container-test.sh [--models] [--once] [--keep] [--port N] [--work DIR]
#     --models   also download/export the AI models and start jarvis-core (slow)
#     --once     skip the idempotence re-run
#     --keep     leave the container running at the end (inspect with: ssh -p PORT ...)
#     --port N   host port for SSH (default 2222)
#     --work DIR working directory for the key, inventory and caches (default: mktemp)
#
# Prerequisites: docker (privileged containers), ssh, rsync, uv or ansible-core >= 2.16.
#
# Exit codes:
#   0  deployment and postflight passed (and the re-run was idempotent)
#   1  the deployment, the postflight or the idempotence check failed
#   2  usage error
#   4  prerequisite missing (docker, ssh, rsync)
#   7  the container did not start or SSH never answered
#   130 interrupted

TESTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../scripts/lib/jarvis-common.sh
. "$TESTS_DIR/../../../scripts/lib/jarvis-common.sh"

readonly IMAGE=jarvis-ubuntu2604-test
readonly NAME=jarvis-ansible-test
PORT=2222
WORK=""
MODELS=0
ONCE=0
KEEP=0

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --models) MODELS=1 ;;
      --once) ONCE=1 ;;
      --keep) KEEP=1 ;;
      --port) PORT="${2:?--port needs a value}"; shift ;;
      --work) WORK="${2:?--work needs a directory}"; shift ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
  [[ "$PORT" =~ ^[0-9]+$ ]] || die "--port must be a number" "$E_USAGE"
}

# prepare: working directory, SSH key and inventory.
prepare() {
  require_cmd docker ssh ssh-keygen rsync
  if [[ -z "$WORK" ]]; then WORK="$(mktemp -d)"; fi
  mkdir -p "$WORK/uv-cache"
  WORK="$(readlink -f -- "$WORK")"
  [[ -f "$WORK/id_ed25519" ]] || ssh-keygen -q -t ed25519 -N '' -C jarvis-ansible-test -f "$WORK/id_ed25519"
  cat >"$WORK/hosts.yml" <<INV
all:
  children:
    jarvis:
      hosts:
        jarvis-test:
          ansible_host: 127.0.0.1
          ansible_port: $PORT
          ansible_user: admin
          ansible_ssh_private_key_file: $WORK/id_ed25519
          ansible_ssh_common_args: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o IdentityAgent=none"
          jarvis_install_models: $( ((MODELS)) && echo true || echo false)
          jarvis_upgrade_packages: false
INV
  log_ok "work directory: $WORK"
}

# start_container: build the image and boot it with systemd.
start_container() {
  log_step "Building $IMAGE"
  docker build -q -t "$IMAGE" -f "$TESTS_DIR/Dockerfile.ubuntu2604" "$TESTS_DIR" >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  ((KEEP)) || on_exit "docker rm -f $NAME >/dev/null 2>&1"
  log_step "Starting $NAME (systemd, ssh on 127.0.0.1:$PORT)"
  docker run -d --name "$NAME" --hostname jarvis-test --privileged --cgroupns=private \
    --tmpfs /run --tmpfs /run/lock -p "127.0.0.1:$PORT:22" \
    -v "$WORK/uv-cache:/var/cache/jarvis/uv" "$IMAGE" >/dev/null
  docker exec -i "$NAME" bash -c "install -m 600 -o admin -g admin /dev/stdin /home/admin/.ssh/authorized_keys" <"$WORK/id_ed25519.pub"
  local i
  for i in $(seq 1 30); do
    ssh -q -p "$PORT" -i "$WORK/id_ed25519" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o IdentitiesOnly=yes -o IdentityAgent=none \
      -o ConnectTimeout=2 admin@127.0.0.1 true 2>/dev/null && { log_ok "SSH ready"; return 0; }
    sleep 1
  done
  die "SSH did not answer on 127.0.0.1:$PORT" "$E_STATE"
}

# deploy_once LOG: run the full deployment, keeping the output in LOG.
deploy_once() {
  local log="$1"
  "$TESTS_DIR/../jarvis-deploy.sh" --no-color -i "$WORK/hosts.yml" deploy </dev/null 2>&1 | tee "$log"
  return "${PIPESTATUS[0]}"
}

# main ARGS...: prepare, boot, deploy, re-deploy, report.
main() {
  parse_args "$@"
  prepare
  start_container
  log_step "Deployment #1"
  deploy_once "$WORK/deploy-1.log" || die "deployment #1 failed (log: $WORK/deploy-1.log)" "$E_RUNTIME"
  if ((!ONCE)); then
    log_step "Deployment #2 (idempotence)"
    deploy_once "$WORK/deploy-2.log" || die "deployment #2 failed (log: $WORK/deploy-2.log)" "$E_RUNTIME"
    local changed
    changed="$(sed -n 's/.*changed=\([0-9]*\).*/\1/p' "$WORK/deploy-2.log" | tail -1)"
    log_info "re-run: changed=$changed (only the always-changed renewal drill is expected)"
  fi
  log_ok "end-to-end test passed (logs in $WORK)"
  ((KEEP)) && log_info "container kept: ssh -p $PORT -i $WORK/id_ed25519 admin@127.0.0.1"
  return 0
}

main "$@"
