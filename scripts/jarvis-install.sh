#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/jarvis-install.sh
# Purpose : Bare-metal installer for Ubuntu Server 26.04 LTS (packages, venv, units, TLS, models)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Installs Jarvis on Ubuntu Server 26.04 LTS (24.04 still accepted), x86_64,
# Intel Haswell CPU or newer: system packages, the "jarvis" system user and its
# directories, a Python 3.11 virtualenv (uv) under /opt/jarvis, configuration
# files, the TLS certificate (jarvis-cert, local CA by default), tmpfiles/udev
# rules, systemd units and timers, the nginx site, the commands in the PATH and
# the AI models (download + OpenVINO export).
# Idempotent: existing configuration, certificate and virtualenv are kept.
# For a fully automated and hardened deployment, prefer deploy/ansible.
#
# Usage (from the repository root):
#   sudo ./scripts/jarvis-install.sh [--with-speaker] [--with-monitoring] [--no-console]
#                             [--skip-models] [--cert-name NAME] [--yes] [--help]
#
# Environment variables (same meaning as the options, kept for compatibility):
#   WITH_SPEAKER=0|1     install the "speaker" extra (speaker verification). Default: 0.
#   WITH_MONITORING=0|1  install prometheus-node-exporter (left disabled; enabled from
#                        the web UI under Settings > Monitoring). Default: 0.
#   WITH_CONSOLE=0|1     enable the local tty1 console (jarvis-console.service). Default: 1.
#   SKIP_MODELS=0|1      do not download/export the AI models. Default: 0.
#
# Prerequisites:
#   - Ubuntu Server 26.04 LTS x86_64, run as root (sudo); CPU with AVX2 strongly recommended.
#   - Internet access (apt, astral.sh for uv, PyPI, download.pytorch.org, model downloads).
#
# Exit codes:
#   0  success
#   1  a command failed (the failing command and line are printed)
#   2  usage error
#   3  not run as root
#   4  missing prerequisite (source tree incomplete, unsupported architecture)
#   5  unsupported operating system
#   130 interrupted

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/jarvis-common.sh
. "$SCRIPT_DIR/lib/jarvis-common.sh"

# --- install layout -------------------------------------------------------------
SRC="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly SRC
readonly PREFIX=/opt/jarvis
readonly VENV=$PREFIX/venv
readonly DATA=/var/lib/jarvis
readonly ETC=/etc/jarvis
readonly LIBDIR=/usr/local/lib/jarvis

WITH_SPEAKER="${WITH_SPEAKER:-0}"
WITH_MONITORING="${WITH_MONITORING:-0}"
WITH_CONSOLE="${WITH_CONSOLE:-1}"
SKIP_MODELS="${SKIP_MODELS:-0}"
CERT_NAMES=()

usage() { sed -n '/^# Usage/,/^# Prerequisites:/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options (override the environment variables).
parse_args() {
  while (($#)); do
    case "$1" in
      --with-speaker) WITH_SPEAKER=1 ;;
      --with-monitoring) WITH_MONITORING=1 ;;
      --no-console) WITH_CONSOLE=0 ;;
      --skip-models) SKIP_MODELS=1 ;;
      --cert-name) CERT_NAMES+=(--name "${2:?--cert-name needs a value}"); shift ;;
      --yes | -y) export ASSUME_YES=1 ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
}

# preflight: privileges, OS, architecture, source tree, CPU capability warnings.
preflight() {
  log_step "Preflight checks (jarvis-home $(jarvis_version))"
  require_root
  [[ "$(uname -m)" == x86_64 ]] || die "unsupported architecture $(uname -m): x86_64 required" "$E_DEPS"
  require_file "$SRC/pyproject.toml" "$SRC/jarvis/VERSION" "$SRC/deploy/nginx/jarvis.conf"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}:${VERSION_ID:-}" in
    ubuntu:26.04) log_ok "Ubuntu Server ${VERSION_ID} LTS" ;;
    ubuntu:24.04) log_warn "Ubuntu 24.04: supported, but 26.04 LTS is the reference platform" ;;
    *) die "unsupported OS ${PRETTY_NAME:-unknown}: Ubuntu Server 26.04 LTS required" "$E_CONFIG" ;;
  esac
  grep -q avx2 /proc/cpuinfo || log_warn "CPU without AVX2 (Celeron/Pentium?): inference will be very slow"
  local cores
  cores=$(lscpu -p=CORE | grep -v '^#' | sort -u | wc -l)
  ((cores >= 4)) || log_warn "only $cores physical cores: set detector.imgsz: 416 and vision.process_fps: 4"
}

# install_packages: build toolchain, ffmpeg, nginx, audio and OpenCV runtime libraries.
install_packages() {
  log_step "System packages"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q --no-install-recommends \
    build-essential python3-dev ffmpeg nginx openssl curl ca-certificates rsync \
    libportaudio2 libasound2-plugins alsa-utils libgl1 libglib2.0-0 libgomp1
}

# create_user: unprivileged system account and its data/log/backup/config directories.
create_user() {
  log_step "System user and directories"
  id jarvis &>/dev/null || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin jarvis
  # audio: mic/speaker; dialout: USB-serial relay and door sensor; plugdev: HID relay; video/render: VAAPI
  local g
  for g in audio dialout plugdev video render; do
    getent group "$g" >/dev/null && usermod -aG "$g" jarvis
  done
  install -d -o jarvis -g jarvis -m 750 "$DATA" "$DATA"/{models,faces,unknown,voice,sightings,tmp,monitoring} /var/log/jarvis
  install -d -o jarvis -g jarvis -m 700 /var/backups/jarvis
  install -d -o root -g jarvis -m 750 "$ETC"
  log_ok "user jarvis ready"
}

# install_python: uv-managed Python 3.11 virtualenv with CPU-only torch and the jarvis package.
install_python() {
  log_step "Python 3.11 environment (uv)"
  command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  install -d "$PREFIX"
  rsync -a --delete --exclude .git --exclude '__pycache__' --exclude '.venv' "$SRC/" "$PREFIX/src/"
  export UV_PYTHON_INSTALL_DIR=$PREFIX/python
  [[ -x $VENV/bin/python ]] || uv venv --python 3.11 "$VENV"
  local -a pip=(uv pip install --python "$VENV/bin/python")
  # CPU-only torch first (otherwise ultralytics pulls in the ~2.5 GB CUDA build).
  "${pip[@]}" torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
  # insightface is built from source and needs numpy/cython at build time.
  "${pip[@]}" "numpy<2" cython
  "${pip[@]}" --no-build-isolation insightface==0.7.3
  local extras=core
  is_true "$WITH_SPEAKER" && extras=core,speaker
  "${pip[@]}" "jarvis-home[$extras] @ file://$PREFIX/src"
  log_ok "jarvis-home $("$VENV/bin/python" -c 'import jarvis; print(jarvis.__version__)') installed in $VENV"
}

# install_config: example configuration and secrets, only when absent.
install_config() {
  log_step "Configuration"
  [[ -f $ETC/config.yaml ]] || install -o root -g jarvis -m 640 "$SRC/config/config.example.yaml" "$ETC/config.yaml"
  [[ -f $ETC/jarvis.env ]] || install -o root -g jarvis -m 640 "$SRC/deploy/jarvis.env.example" "$ETC/jarvis.env"
  log_ok "$ETC/config.yaml and $ETC/jarvis.env in place"
}

# install_commands: shared library and commands in the PATH (jarvis, jarvis-console, jarvis-cert, jarvis-hwinfo).
install_commands() {
  log_step "Commands in the PATH"
  install -d -m 755 "$LIBDIR"
  install -m 644 "$SRC/scripts/lib/jarvis-common.sh" "$LIBDIR/jarvis-common.sh"
  install -m 755 "$SRC/deploy/bin/jarvis" /usr/local/bin/jarvis
  install -m 755 "$SRC/deploy/bin/jarvis-console" /usr/local/bin/jarvis-console
  install -m 755 "$SRC/scripts/jarvis-cert.sh" /usr/local/sbin/jarvis-cert
  install -m 755 "$SRC/scripts/jarvis-hwinfo.sh" /usr/local/bin/jarvis-hwinfo
  log_ok "jarvis, jarvis-console, jarvis-cert, jarvis-hwinfo installed"
}

# install_tls: local-CA certificate on first install; legacy self-signed ones are migrated.
install_tls() {
  log_step "TLS certificate"
  if [[ -f $ETC/tls/jarvis.crt ]]; then
    log_info "existing certificate kept (migrated to the local CA at renewal if self-signed)"
  else
    JARVIS_CERT_NO_RELOAD=1 /usr/local/sbin/jarvis-cert issue --mode local "${CERT_NAMES[@]}"
  fi
  /usr/local/sbin/jarvis-cert status --no-revocation || log_warn "certificate status needs attention (see above)"
}

# install_system_units: tmpfiles, udev rules and systemd units/timers.
install_system_units() {
  log_step "tmpfiles, udev and systemd units"
  install -m 644 "$SRC/deploy/tmpfiles/jarvis.conf" /etc/tmpfiles.d/jarvis.conf
  systemd-tmpfiles --create /etc/tmpfiles.d/jarvis.conf
  install -m 644 "$SRC/deploy/udev/99-jarvis.rules" /etc/udev/rules.d/99-jarvis.rules
  udevadm control --reload && udevadm trigger
  install -m 644 "$SRC"/deploy/systemd/jarvis-*.service "$SRC"/deploy/systemd/jarvis-*.timer \
    "$SRC"/deploy/systemd/jarvis.target "$SRC"/deploy/systemd/jarvis-monitoring.path /etc/systemd/system/
  systemctl daemon-reload
}

# install_nginx: TLS reverse proxy site (the distribution's default site is removed).
install_nginx() {
  log_step "nginx"
  install -d -m 755 /var/www/letsencrypt
  install -m 644 "$SRC/deploy/nginx/jarvis.conf" /etc/nginx/sites-available/jarvis.conf
  # Host-specific snippets (upstream, server names): installed once, then kept.
  local f
  for f in jarvis-upstream.conf jarvis-server-name.conf; do
    [[ -f /etc/nginx/snippets/$f ]] || install -D -m 644 "$SRC/deploy/nginx/snippets/$f" "/etc/nginx/snippets/$f"
  done
  ln -sf /etc/nginx/sites-available/jarvis.conf /etc/nginx/sites-enabled/jarvis.conf
  rm -f /etc/nginx/sites-enabled/default
  nginx -t -q || die "nginx configuration test failed" "$E_CONFIG"
  systemctl reload-or-restart nginx
  log_ok "nginx site enabled"
}

# install_models: AI models fetched and exported as the jarvis user, with the secrets of jarvis.env.
install_models() {
  if is_true "$SKIP_MODELS"; then log_warn "models skipped (--skip-models): run 'jarvis setup-models' later"; return 0; fi
  log_step "Models (download + OpenVINO export, takes a few minutes)"
  sudo -u jarvis env HOME="$DATA" YOLO_CONFIG_DIR="$DATA/.config/Ultralytics" JARVIS_CONFIG="$ETC/config.yaml" \
    bash -c "set -a; source $ETC/jarvis.env; exec $VENV/bin/jarvis setup-models"
}

# enable_services: start at boot (not started now), optional monitoring and console.
enable_services() {
  log_step "Services"
  systemctl enable jarvis.target jarvis-core.service jarvis-api.service jarvis-backup.timer \
    jarvis-monitoring.path jarvis-cert-renew.timer
  systemctl start jarvis-backup.timer jarvis-cert-renew.timer
  if is_true "$WITH_MONITORING"; then
    apt-get install -y -q --no-install-recommends prometheus-node-exporter
    systemctl disable --now prometheus-node-exporter   # enabled on demand from the web UI
    log_info "Promtail/Alloy: add the apt.grafana.com repository, then 'apt install alloy' (see the manual)"
  fi
  # pfSense-style local console on the mini-PC screen (tty1).
  if is_true "$WITH_CONSOLE"; then systemctl enable jarvis-console.service; fi
  log_ok "units enabled"
}

# summary: next steps for the operator.
summary() {
  local ip
  ip="$(hostname -I | awk '{print $1}')"
  cat <<MSG

${C_GREEN}${C_BOLD}Installation complete${C_RESET} (jarvis-home $(jarvis_version)). Next steps:
  1. Edit $ETC/config.yaml and $ETC/jarvis.env (RTSP URL, credentials, relay)
  2. Test the hardware  : jarvis test-hardware
  3. Check the config   : jarvis check-config
  4. Start              : sudo systemctl start jarvis.target   (start at boot already enabled)
  5. Monitor            : systemctl status jarvis.target ; journalctl -u jarvis-core -f
  6. Trust the local CA : jarvis-cert export-ca --out jarvis-ca.crt (import it on the clients)
  7. Open https://$ip/ and log in as admin / admin: a new password is required at first login
     (reset it at any time from the local console: jarvis-console, option 3)
MSG
}

# main ARGS...: run every installation step in order.
main() {
  parse_args "$@"
  # Installed code must be world-readable for the jarvis account, whatever the login.defs
  # UMASK of the admin session (027 on a hardened host); secrets get explicit modes.
  umask 022
  preflight
  install_packages
  create_user
  install_python
  install_config
  install_commands
  install_tls
  install_system_units
  install_nginx
  install_models
  enable_services
  summary
}

main "$@"
