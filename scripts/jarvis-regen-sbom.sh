#!/usr/bin/env bash
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/jarvis-regen-sbom.sh
# Purpose : Regenerate and validate the CycloneDX SBOM, then rebuild the PDFs
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Regenerates the Jarvis CycloneDX 1.6 SBOM (docs/sbom/jarvis-sbom.cdx.json
# plus a CSV summary), validates it against the strict CycloneDX 1.6 JSON
# schema, then rebuilds the PDFs under docs/diagrams (the BOM PDF embeds the
# SBOM summary).
#
# Usage:
#   ./scripts/jarvis-regen-sbom.sh [--no-pdf]
#     --no-pdf  skip the PDF rebuild
#
# Environment variables (defaults match an install done by scripts/jarvis-install.sh):
#   JARVIS_VENV=/opt/jarvis/venv           virtualenv whose packages are inventoried
#   JARVIS_MODELS=/var/lib/jarvis/models   AI model directory to inventory
#
# Prerequisites: uv (uvx); for the PDFs: graphviz (dot) and Chrome or Chromium.
#
# Exit codes:
#   0  success (a missing models dir or missing PDF tools only prints a warning)
#   1  a command failed (the failing command and line are printed)
#   2  usage error
#   4  prerequisite missing (virtualenv, uv, python3)
#   5  the generated SBOM does not validate against the CycloneDX 1.6 schema
#   130 interrupted
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/jarvis-common.sh
. "$SCRIPT_DIR/lib/jarvis-common.sh"

ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
VENV="${JARVIS_VENV:-/opt/jarvis/venv}"
MODELS="${JARVIS_MODELS:-/var/lib/jarvis/models}"
OUT="$ROOT/docs/sbom/jarvis-sbom.cdx.json"
PDF=1

usage() { sed -n '/^# Usage:/,/^# Environment variables/p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# parse_args ARGS...: command-line options.
parse_args() {
  while (($#)); do
    case "$1" in
      --no-pdf) PDF=0 ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) die "unknown option: $1 (see --help)" "$E_USAGE" ;;
    esac
    shift
  done
}

# check_prerequisites: virtualenv to inventory, uv for the validator, models directory (optional).
check_prerequisites() {
  [[ -x "$VENV/bin/python" ]] || die "virtualenv not found: $VENV (set JARVIS_VENV)" "$E_DEPS"
  require_cmd uv python3
  [[ -d "$MODELS" ]] || log_warn "$MODELS not found: AI models will not be inventoried"
}

# generate_sbom: CycloneDX JSON + CSV summary.
generate_sbom() {
  log_step "Generating the SBOM (jarvis-home $(jarvis_version))"
  python3 "$ROOT/scripts/jarvis-gen-sbom.py" --python "$VENV/bin/python" --models "$MODELS" --out "$OUT"
}

# validate_sbom: strict CycloneDX 1.6 schema validation in a throwaway uv environment.
validate_sbom() {
  log_step "Validating (strict CycloneDX 1.6 schema)"
  uv run -q --no-project --with "cyclonedx-python-lib[json-validation]" python - "$OUT" <<'PY' ||
import sys
from cyclonedx.schema import SchemaVersion
from cyclonedx.validation.json import JsonStrictValidator
err = JsonStrictValidator(SchemaVersion.V1_6).validate_str(open(sys.argv[1], encoding="utf-8").read())
if err:
    sys.exit(f"Invalid SBOM: {err}")
PY
    die "SBOM failed schema validation" "$E_CONFIG"
  log_ok "SBOM is valid"
}

# rebuild_pdfs: rebuild the PDFs (the BOM PDF embeds the SBOM summary) when the tools exist.
rebuild_pdfs() {
  ((PDF)) || return 0
  if command -v dot >/dev/null && command -v pdftotext >/dev/null && { command -v google-chrome || command -v chromium || command -v chromium-browser; } >/dev/null; then
    log_step "Rebuilding the PDFs"
    python3 "$ROOT/docs/diagrams/src/build.py"
  else
    log_warn "PDFs not rebuilt: install graphviz, poppler-utils and Chrome/Chromium, or rerun with --no-pdf"
  fi
}

# main ARGS...: generate, validate, then rebuild the documentation.
main() {
  parse_args "$@"
  check_prerequisites
  generate_sbom
  validate_sbom
  rebuild_pdfs
  log_ok "done: $OUT and ${OUT%.cdx.json}.csv"
  log_info "vulnerability scan: grype sbom:$OUT   (or: trivy sbom $OUT)"
}

main "$@"
