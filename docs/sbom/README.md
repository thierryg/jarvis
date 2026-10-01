<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/sbom/README.md
Purpose : Software Bill of Materials: files, contents, regeneration and usage
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# SBOM (Software Bill of Materials)

| File | Contents |
|---|---|
| `jarvis-sbom.cdx.json` | **CycloneDX 1.6** SBOM (JSON), validated against the official schema in strict mode |
| `jarvis-sbom.csv` | Same inventory as a table: type, name, version, license, purl or source |

## Contents

- **Python packages** from the virtualenv actually installed: every dependency, including transitive ones. For each: version, license, `pkg:pypi/…` purl, project links. Source: `cyclonedx-py environment`.
- **AI models** (type `machine-learning-model`): YOLO11n, InsightFace buffalo_s, Vosk small-fr, Piper siwis voice, openWakeWord, and ECAPA when speaker verification is enabled. For each: supplier, license verified at the source, distribution URL and **SHA-256 digest of every file**.
- **System components**: CPython, SQLite, FFmpeg, nginx and the OS, with the versions detected on the machine.
- **Application** `jarvis-home` as `metadata.component`, with its dependency graph.

## Regenerating

```bash
./scripts/jarvis-regen-sbom.sh             # SBOM + validation + PDF (docs/diagrams)
./scripts/jarvis-regen-sbom.sh --no-pdf    # SBOM + validation only
JARVIS_VENV=.venv JARVIS_MODELS=./models ./scripts/jarvis-regen-sbom.sh   # another installation
```

The main script is `scripts/jarvis-gen-sbom.py`, which can be used on its own (`--help`).

The SBOM shipped here was produced on the test environment. Regenerate it on the installed machine after every `jarvis-install.sh` run or dependency update.

## Using it

- Vulnerabilities: `grype sbom:docs/sbom/jarvis-sbom.cdx.json` or `trivy sbom docs/sbom/jarvis-sbom.cdx.json`.
- Continuous monitoring: import into [OWASP Dependency-Track](https://dependencytrack.org/).
- Licenses: the per-family summary and the list of licenses to watch (copyleft, non-commercial, undeclared) are in `docs/diagrams/jarvis-bom.pdf`.
