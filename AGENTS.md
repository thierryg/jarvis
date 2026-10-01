<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : AGENTS.md
Purpose : Project memory and standing rules for AI coding agents (Claude, Gemini, Codex, Copilot...)
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# AGENTS.md — project memory for AI agents

This is the canonical instruction file for every AI coding agent working on this repository.
`CLAUDE.md` and `GEMINI.md` point here. Read it fully before any change. The rules below are
**standing orders** from the project owner: apply them without being asked again.

## 1. What Jarvis is

Jarvis is a 100 % on-premises smart gatekeeper running on a Lenovo ThinkCentre M73 **Tiny**
(Intel Haswell, no PCIe, one RJ45 port).

| Function | How |
|---|---|
| Camera | PTZ ONVIF camera; people tracked with YOLO11n (OpenVINO) + ByteTrack |
| Face recognition | InsightFace (SCRFD detection, ArcFace embeddings) |
| Voice | Offline commands: openWakeWord, Vosk, ECAPA speaker verification; Piper for speech synthesis |
| Garage door | USB relay on the Novoferm Novomatic 200 dry contact (terminal F) |

**Processes:**
- privileged `jarvis-core` (systemd `Type=notify`);
- unprivileged FastAPI web API (`jarvis-api`, 127.0.0.1:8000) behind nginx TLS and Anubis;
- the two talk over a Unix control socket (`/run/jarvis/core.sock`);
- SQLite WAL database (`/var/lib/jarvis`).

**Administration:** framework-free SPA in 12 languages, cmd2 shell (`jarvis`), local console
(`jarvis-console`), certificate manager (`jarvis-cert`).

## 2. Layout

| Path | Content |
|---|---|
| `jarvis/cli/` | cmd2 administration shell (`jarvis`) |
| `jarvis/config/` | settings models, catalog of about 104 hot-reloadable parameters |
| `jarvis/core/` | service, control socket, events, decision engine, logs, monitoring, sysinfo |
| `jarvis/vision/` | camera, PTZ, pipeline, faces, recorder |
| `jarvis/voice/` | assistant, audio, commands, speaker, TTS, wake word |
| `jarvis/hardware/`, `jarvis/storage/`, `jarvis/integrations/` | relay/door sensor, SQLite with migrations, MQTT/Home Assistant |
| `jarvis/web/` | FastAPI app and `static/` (index.html, app.js, style.css, `i18n/*.json`, `img/`) |
| `jarvis/VERSION` | **single source of the version** |
| `deploy/` | systemd, nginx (+ snippets), Anubis, udev, tmpfiles, PATH commands, monitoring, **ansible/** — see `deploy/README.md` |
| `scripts/` | `jarvis-install.sh`, `jarvis-cert.sh`, `jarvis-bump-version.py`, `jarvis-gen-sbom.py`, `jarvis-regen-sbom.sh`, `lib/jarvis-common.sh` |
| `docs/` | architecture and software docs, PDFs (`diagrams/src/build.py`, outputs in `diagrams/` and `manuals/`), SBOM, hardware manuals |
| `tests/` | pytest suite (no model or hardware required) |

## 3. Standing rules (mandatory)

### Language and style
- **Code in US English:** file, function, class and variable names, and **all comments and
  docstrings** in Python, bash, Makefile, HTML, JS, CSS, YAML and Jinja.
- **Every Markdown file (`*.md`, existing and future) is written in technical US English.**
  This replaces the earlier convention of French prose docs. The only French in the
  repository is user-facing content that is French on purpose: the `fr` UI locale, the French
  voice commands and voice, and the published titles of French books in the PDF bibliographies.
  The PDFs are in US English (`docs/diagrams/src/build.py`).
- **Header** at the top of every source file:
  - the Jarvis banner;
  - `File`, `Purpose`, `Author : Thierry Gayet <thierry.gayet@labworks.fr>`;
  - `Project : jarvis-home (version: jarvis/VERSION)` and
    `Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD`.
  - Document every module, class, function and method.
- **Match the surrounding code:** comment density, naming and idioms.

### Bash scripts (existing and new)
- **Naming:** every bash script is named `jarvis-XXXX.sh` (and every Python script of `scripts/` `jarvis-XXXX.py`), sourced libraries included
  (`scripts/lib/jarvis-common.sh`, `docker/jarvis-docker-common.sh`). Installed commands keep
  their command names (`jarvis`, `jarvis-console`, `jarvis-cert`, `jarvis-motd`); the
  update-motd hook `10-jarvis` needs its numeric prefix.
- **Structure:** source `scripts/lib/jarvis-common.sh` (strict mode, ERR/EXIT traps), use
  documented functions, and end with `main "$@"`.
- **Output:** colorized logging (`log_info/ok/warn/error/step`) that honors `NO_COLOR`;
  errors go through `die MSG CODE`.
- **Exit codes**, documented in the header: 0 OK, 1 runtime, 2 usage, 3 privileges,
  4 prerequisites, 5 configuration, 6 network, 7 state, 8 warnings.
- **Arguments:** `--help`; an unknown option is exit code 2.
- **Quality:** idempotent; `make shellcheck` has zero warnings.

### Versioning
- **Single source:** the version lives only in `jarvis/VERSION` (SemVer).
- **Bump with every delivered batch:** `make bump PART=patch|minor|major`, then fill in the
  `CHANGELOG.md` entry (Keep a Changelog).

### Documentation is always regenerated
- **Every change updates ALL affected Markdown docs:** `README.md`, `deploy/README.md`,
  `deploy/ansible/README.md`, `docs/ARCHITECTURE.md`, the software doc, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `SECURITY.md` and this file.
- **Rebuild the PDFs** (`make pdf`) when their sources change, and the SBOM (`make sbom`) when
  dependencies change.
- **A change is not done while a doc is stale.**
- **PDF manuals:**
  - author "Thierry Gayet <thierry.gayet@labworks.fr>";
  - professional industrial / military technical-manual style;
  - table of contents, French technical glossary, French and English bibliography, detailed index.

### Web UI
- **Locales:** US English by default, plus fr, es, nl, de, it, ru, zh, id, ko, ja, th. Every
  new string gets a key in **all** `static/i18n/*.json` files. `en-US.json` is the reference: it
  holds every key, and the other locales translate exactly the same key set.
- **CSP-safe:** no inline script or style (the CSSOM `element.style` is allowed).
- **Assets:** images and icons live in `jarvis/web/static/img/`.

### License
- **Project license:** Jarvis is **0BSD** (`LICENSE`). Every new file carries the SPDX line in its header.
- **Third parties:** they keep their own licenses; `THIRD-PARTY-NOTICES.md` and the SBOM document
  them. Never copy third-party code into the repository without recording its license there.

### Security (never break)
- **Never commit** secrets, tokens, TLS or Anubis keys, `jarvis.env` or biometric data
  (faces, voice prints, recordings).
- **Secrets** are write-only in the API and UI, masked in the audit log, and resolved by
  precedence: parameter > environment `JARVIS__SECTION__KEY` > `config.yaml` > default.
- **Audit every administrative action with diffs** (SHA-256 hash chain).
- **Never expose port 443 on the Internet** (use a VPN). Let's Encrypt uses DNS-01 by default.
- **Default web account:** `admin` / `admin` must be changed at first login.
- **Hardware:** the relay only switches the Novomatic dry contact (terminal F), never 230 V.
- **Git:** commit or push only when the owner asks.

### Validation before saying "done"
Run `make check` (ruff, bandit, ShellCheck, pytest, pip-audit) and, for Ansible,
`deploy/ansible/jarvis-deploy.sh lint`. Report real results, failures included.

## 4. Useful commands

```bash
make venv test lint sast shellcheck        # development
make version / make bump PART=minor        # version
make pdf / make sbom                       # documentation and SBOM
docker/jarvis-start.sh | jarvis-status.sh | jarvis-stop.sh | jarvis-clean.sh   # local Docker test
sudo ./scripts/jarvis-install.sh --help           # shell installer
deploy/ansible/jarvis-deploy.sh --help            # Ansible deployment (Ubuntu Server 26.04 LTS)
jarvis-cert status                         # TLS certificate (on the target)
```
