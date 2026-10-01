<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : CHANGELOG.md
Purpose : Release history (Keep a Changelog 1.1.0, Semantic Versioning 2.0.0)
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

The version has a single source, `jarvis/VERSION`. Bump it with
`make bump PART=patch|minor|major` (`scripts/jarvis-bump-version.py`): this moves the
`[Unreleased]` section below under a dated heading. Never edit the version anywhere else.

## [Unreleased]

## [0.8.1] - 2026-10-01

### Changed
- **Local Docker test:**
  - `docker/jarvis-start.sh` (and status/stop/clean) explains each Docker failure with the
    command that fixes it: the Compose/buildx package for the installed flavor (`docker.io`:
    `docker-compose-v2`, `docker-buildx`; `docker-ce`: `docker-compose-plugin`,
    `docker-buildx-plugin`), a stopped daemon, a user outside the `docker` group, or a group
    membership newer than the login session (`newgrp docker`).
  - Without buildx, the image is built with the classic builder (`COMPOSE_BAKE=false`) instead
    of a Compose warning.
  - The summary shows "Camera: not set (…)" instead of an empty line.
- **`.gitignore` rewritten** for every file type of the repository: Python tool caches and
  reports, secrets (keys, certificates, keystores, vault, tokens), biometric and runtime data
  (faces, voice prints, snapshots, recordings, audio/video, embeddings, databases), AI models,
  archives, backups and **crash dumps** (`core.<pid>`, which hold process memory), editors and OS.

### Fixed
- `deploy/anubis/jarvis.env`, the non-secret Anubis template read by the Ansible `anubis` role,
  was ignored by the `*.env` rule and missing from clones: it is now explicitly kept. Its header
  carries the 0BSD SPDX line.
- Ultralytics no longer falls back to `/tmp/Ultralytics` ("user config directory is not
  writable") on a fresh data volume: `$YOLO_CONFIG_DIR` is created before YOLO is imported.

## [0.8.0] - 2026-09-30

### Added
- **Local Docker test:**
  - `docker/jarvis-start.sh` builds the image and starts core + API + nginx over HTTPS on
    `localhost:8443`, using a self-contained `docker/docker-compose.local.yml` (config,
    environment and local-CA certificate generated in `docker/.local/`). The models are
    downloaded on the first start, the hardware is simulated, voice is off, and only `127.0.0.1`
    is published.
  - `docker/jarvis-status.sh` reports containers, health, resources, the image and volumes,
    the web UI and HTTP redirect, the core status and the certificate (`--logs`, `--json`).
  - `docker/jarvis-stop.sh` stops the project and keeps the data.
  - `docker/jarvis-clean.sh` stops the project if needed and frees the image; `--volumes` and
    `--purge` remove more.
  - `make docker-start|docker-status|docker-stop|docker-clean`.
- `api.forwarded_allow_ips`: the proxy addresses uvicorn trusts (127.0.0.1 by default, `*` in Docker).

### Changed
- **Script names:** every bash script is named `jarvis-XXXX.sh`:
  - `scripts/install.sh` → `scripts/jarvis-install.sh`;
  - `scripts/regen-sbom.sh` → `scripts/jarvis-regen-sbom.sh`;
  - `scripts/lib/common.sh` → `scripts/lib/jarvis-common.sh`, installed as
    `/usr/local/lib/jarvis/jarvis-common.sh`;
  - `deploy/ansible/deploy.sh` → `deploy/ansible/jarvis-deploy.sh`;
  - `deploy/ansible/tests/run-container-test.sh` → `deploy/ansible/tests/jarvis-container-test.sh`.

- **Python script names:** the Python scripts of `scripts/` follow the same rule:
  - `scripts/gen-sbom.py` → `scripts/jarvis-gen-sbom.py`;
  - `scripts/bump-version.py` → `scripts/jarvis-bump-version.py`.

  Every reference is updated, and ShellCheck (Makefile, CI) now covers every script.
- The Docker image is based on Debian 13 "trixie" (`python:3.11-slim-trixie`).

### Fixed
- **Docker network:** `docker/jarvis-start.sh` creates the project network itself. When the
  daemon's `default-address-pools` are exhausted (common on managed workstations), it picks a
  free /24 that overlaps no local route and no Docker network; `JARVIS_DOCKER_SUBNET` forces one.
- `docker/docker-compose.yml`: the API used `JARVIS_API_HOST`, which is ignored, so it listened
  on 127.0.0.1 inside its container and nginx could not reach it. It now uses
  `JARVIS__API__HOST` and trusts nginx's forwarded headers.

## [0.7.0] - 2026-09-30

### Added
- **License plate recognition (ANPR)**, optional and off by default (`plates.enabled`):
  - **Reading:** vehicles (car, motorcycle, bus, truck) are tracked with the persons, and their
    plates are read by fast-alpr (YOLOv9 plate detector + international CCT OCR, ONNX, offline,
    with country prediction). A plate is confirmed after identical reads, and its direction
    (approaching / leaving) comes from the vehicle box.
  - **Opening:** a registered plate (enabled, not expired, linked person allowed at that time)
    on an approaching vehicle opens the garage, **only when the door sensor reads "closed"**.
  - **Closing (opt-in, DANGER):** once the known vehicle has left, the garage closes after the
    scene stayed clear for `close_delay_s`, only when the sensor reads "open". An arrival
    cancels a pending close.
  - **Tracing:** every read, decision and refusal (with its reason) is in the event log, the
    `plate_reads` table (vehicle snapshot, action) and the service log.
  - **Vehicles page:** plate registry (add, enable/disable, link to a person, expiry, delete;
    audited with diffs) and read history (date, time window, plate, status filters).
  - **Settings:** License plates group; `storage.plate_read_retention_days` for GDPR.
  - **Models:** `jarvis setup-models` downloads the plate models once, and the core runs offline.
  - **Tests:** `tests/test_plates.py` (23 tests: normalization, voting, direction, every decision
    rule, API).
- The "vehicle" recording trigger.

### Changed
- **PDF documentation in US English** (`docs/diagrams/src/build.py`, `backmatter.py`): technical-manual
  layout (document number, record of revisions, numbered chapters, DANGER / CAUTION / NOTE callouts,
  running header and "Page n of N" footer), contents with page numbers (two-pass render with
  `pdftotext`), glossary, bibliography (English and French books with verified ISBNs, standards) and
  index with page numbers in every PDF.
- Renamed outputs: `docs/diagrams/jarvis-network-flows.pdf`, `docs/diagrams/jarvis-interconnection.pdf`,
  `docs/manuals/jarvis-software-documentation.pdf` (directory `docs/manuels/` renamed `docs/manuals/`,
  screenshots moved to `docs/manuals/screenshots/` with English names).
- Network flows: nginx → Anubis → API path, MQTT, remote syslog, Loki/Prometheus/node_exporter, NTP,
  Let's Encrypt ACME / DNS-01 flows (F13 to F24); ruleset aligned with the Ansible firewall role.
- BOM / interconnection: PTZ power (802.3bt / Hi-PoE, 36 V DC, 24 V AC; PoE+ not enough), Axis
  Q6075-E end of life (replacement Q6086-E), Reolink Argus PT "not suitable without a Reolink Home
  Hub", external pull-up on the FT232R door-sensor input, Novomatic terminal F.
- `scripts/gen-sbom.py`: English CSV columns (`name`, `license`) and `UNDECLARED` fallback.

## [0.6.0] - 2026-09-30

### Changed
- **Recognition window and voice commands:**
  - **Window:** when an authorized person is recognized, the green LED stays on for
    `decision.auth_window_s` (Settings > Decision and access, now **30 s** by default, formerly
    60 s) and turns off at its end.
  - **Voice:** accepted only during that window (`decision.voice_only_after_recognition`, on by
    default). Outside it the microphone is ignored; a new recognition extends the window.
  - **Wake word required:** every command must start with the **"Jarvis"** wake word; listening
    without it after the greeting is disabled (`listen_after_recognition_s` = 0, removed from
    the UI).
  - **Grammar:** with `commands.strict` (default), only « ouvre la porte du garage » and
    « ferme la porte du garage » are accepted; there is no keyword fallback and no cancel phrase.
  - **Tracing:** `voice_window_opened` events; window open/close messages in the service log;
    the Live page shows the seconds left.
  - **Existing installations:** `/etc/jarvis/config.yaml` keeps its old phrases and window until
    edited (see `config/config.example.yaml`).

## [0.5.0] - 2026-09-30

### Added
- **Docs:** `docs/CAMERA-SELECTION.md`, a camera selection guide (requirements, face pixel
  density, RTSP/ONVIF PTZ constraints, 30 models with official links checked on 2026-09-30).
- **Settings > Camera stream** (new first group):
  - the RTSP stream URL and the reconnection delay; ONVIF address, port and media profile in
    the PTZ group;
  - the URL is validated, and credentials in the URL are refused: they belong to the secrets;
  - a live preview of the annotated stream (boxes, track numbers, names), with analysis
    indicators;
  - "Test the stream" (`POST /api/camera/probe`, ffprobe over TCP): codec, resolution, rate,
    audio and Jarvis advice (H.265, resolution, rate); password masked; audited.
- **Logs page:**
  - search in `/var/log/jarvis/jarvis.log` (`GET /api/logs`): text or regular expression,
    minimum level, process, date-time range, rotated files;
  - live tail over Server-Sent Events (`GET /api/logs/stream`), with tracebacks kept with
    their entry, highlighted hits and download;
  - secrets masked server-side; nginx streams it without buffering.
- **Tests:** `tests/test_camera_logs.py` (11 tests); the browser check grows to 33 checks.

### Fixed
- **MJPEG preview:** a still image (frozen camera, reconnection) was never displayed, because
  browsers only paint a part when the next boundary arrives. The boundary now follows each
  image, and the image is re-sent every 2 s.
- The MQTT test broker now sets its own event loop, so it no longer depends on test order.
- Axis Q6078-E marked as discontinued (successor Q6088-E) in REQUIREMENTS.md and its hardware notes.

## [0.4.0] - 2026-09-30

### Added
- **License:** Jarvis is released under the **BSD Zero Clause License (0BSD)**, the least
  restrictive OSI-approved license (`LICENSE`).
  - Every header carries `SPDX-License-Identifier: 0BSD`, and so do the pyproject license and
    classifier and the SBOM application license.
  - `THIRD-PARTY-NOTICES.md` lists the components that keep their own terms (Ultralytics YOLO
    AGPL-3.0, InsightFace models non-commercial…).
- **Web UI, password change dialog:**
  - forced mode after the initial `admin` / `admin` sign-in (shell locked, Escape ignored,
    sign-out as the only other way out);
  - voluntary mode from the user menu;
  - live policy checklist mirroring the server;
  - handling of any 403 "Password change required".
- **Web UI images:** favicon (SVG + ICO), Apple touch icon, web app manifest and `theme-color`;
  `login-bg.svg` as the login hero background; `<meta name="robots" content="noindex…">`.
- `docs/hardware/reolink-argus-pt/`: the owner's Reolink Argus PT (official PDFs), suitability
  analysis and integration guide through a Reolink Home Hub.
- **Tests:**
  - `tests/test_web_ui.py`: i18n completeness against `en-US.json`, assets, CSP-safe markup,
    sprite consistency, password rules mirrored from the server;
  - `tests/browser/ui_check.py` and `make ui-check`: 22 real-browser checks with Playwright and Chrome.

### Changed
- `en-US.json` is the single i18n reference: it is complete (528 keys), and `_source.en.json` is removed.

### Fixed
- **Blank page:**
  - opened as a local file, or when a translation table fails to load, the boot no longer
    aborts: the login view is shown with an explanation;
  - the first visit no longer shows "session expired";
  - an unreachable API shows a notice.
- The access-rule editor showed the next day as the "valid until" date west of UTC (UTC `toISOString()`); it now uses the local date.

## [0.3.0] - 2026-09-30

### Added
- **Ansible deployment** (`deploy/ansible/`) of a fresh Ubuntu Server 26.04 LTS.
  - **Roles, in order:** preflight, base, hardening, firewall, ssh, jarvis, tls, anubis, nginx,
    fail2ban, models, monitoring, motd, services and postflight.
  - **Wrapper:** `deploy.sh` runs deps, ping, preflight, check, deploy, postflight and lint, with
    a `uvx` fallback when Ansible is missing or too old.
  - **Lint:** clean under ansible-lint (production profile) and yamllint.
  - **Preflight** (blocking, before any change):
    - OS, architecture, systemd and cgroup v2;
    - RAM, CPU and disk;
    - sudo and SSH lockout protection: key present, `AllowUsers`, controller address inside the
      admin networks;
    - TLS settings, Internet access, ports 80/443, clock and camera.
  - **Postflight:**
    - services active and enabled;
    - ports listening, with the API and Anubis on loopback only;
    - HTTP → HTTPS 301; TLS 1.2/1.3 only; security headers;
    - robots.txt, and AI/search bots answered 403; Anubis challenge for browsers;
    - certificate valid and served, renewal timer, renewal drill;
    - default admin/admin login with a forced password change;
    - nftables, fail2ban, sshd, sysctl, AppArmor and the MOTD.
  - **TLS mode per host:** `jarvis_tls_mode: local` (private CA) or `letsencrypt` (public domain,
    DNS-01), with example inventories for both.
  - **Hardening:** sysctl, forbidden kernel modules, no core dumps, journald, auditd, AppArmor,
    unattended-upgrades, sudo, pwquality, umask, cron.
  - **Network protection:**
    - nftables default-deny;
    - fail2ban jails `sshd`, `jarvis-login`, `nginx-botsearch`, `recidive`;
    - sshd keys only, with ML-KEM/NTRU/X25519 key exchange;
    - legal banner before login.
  - **End-to-end test** (`tests/run-container-test.sh`): Ubuntu 26.04 systemd container over SSH.
- **SSH MOTD** (`deploy/motd/`): JARVIS ASCII art with neofetch, or fastfetch when neofetch is
  not packaged. It shows:
  - CPU (current/max/free), memory, disk I/O, network in/out and GPU;
  - the hardware support detected by the core;
  - per-service state and CPU/GPU/memory/IO/network consumption.
- `scripts/jarvis-hwinfo.sh` (`jarvis-hwinfo`), a hardware inventory:
  - covers system, CPU (ISA extensions), NPU, GPU (VAAPI, OpenCL, Vulkan, OpenVINO, ONNX),
    memory, storage (TRIM, SMART), network, USB (Jarvis modules recognized), audio and sensors;
  - `--json` output, optional quick IO benchmark, and a Jarvis suitability verdict.
- The core publishes `/run/jarvis/capabilities.json` (hardware detection and auto-tuning).
- **systemd resource accounting** (CPU/memory/IO/IP) on the Jarvis and Anubis units.
- **Anubis integration in nginx:**
  - front upstream through per-host snippets;
  - `robots.txt` `Disallow: /`, `X-Robots-Tag`, `Cache-Control: no-store`;
  - AI/search bot user agents answered 403;
  - stream and `/metrics` bypass Anubis;
  - dedicated logs for fail2ban.
- **Anubis `DynamicUser` drop-in:** signing key passed with `LoadCredential`, full sandboxing.
- **New docs:**
  - `INSTALL.md`: from the Ubuntu 26.04 USB key made with balenaEtcher to a verified deployment;
  - `REQUIREMENTS.md`: hardware, software, official Ubuntu and Debian support dates;
  - `deploy/README.md`, `deploy/ansible/README.md`.
- AI agent memory: `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`.

### Changed
- Every Markdown document is now written in technical US English; `docs/LOGICIEL.md` became
  `docs/SOFTWARE.md`.
- `jarvis-cert issue --mode letsencrypt` always forces a new issuance, which makes switching
  from staging to production possible.
- `install.sh`: `umask 022` for the installed code, `jarvis-hwinfo` installed, nginx snippets.

### Fixed
Found by the end-to-end container test:
- The hardened `UMASK 027` also applied to the sudo sessions, which made the virtualenv unreadable
  for the `jarvis` account. uv now runs under `umask 022`, a permission check repairs older
  installations, and `install.sh` forces `umask 022`.
- `/run/jarvis` was missing after a failed run (a lost handler notification): it is now an
  idempotent task, and `force_handlers` is enabled.
- The fail2ban `jarvis-login` filter now matches after fail2ban strips the timestamp.
- The Anubis signing key is readable by its `DynamicUser` service through `LoadCredential`,
  instead of loosening the key permissions.
- The ASCII art moved to `/usr/local/share/jarvis`, readable by every administrator. A
  non-root `jarvis-motd` now says the security section needs root.
- `jarvis_version()` in `common.sh` read the wrong path on installed hosts.
- Re-runs are idempotent: the only change reported is the renewal drill, which is expected.
- `build.py` strips the Markdown file-header comment before rendering the PDF.

## [0.2.0] - 2026-09-30

### Added
- Central version file `jarvis/VERSION`: read by `jarvis.__version__` and the pyproject
  dynamic metadata. It is shown in the web UI (authenticated users only), the shell, the
  console, MQTT discovery, Prometheus `jarvis_info`, the SBOM, the PDF covers and
  `jarvis-cert --version`.
- `scripts/bump-version.py` and `make version` / `make bump`: SemVer validation, no
  downgrade, CHANGELOG roll-over.
- `jarvis-cert` (`scripts/jarvis-cert.sh`), a TLS certificate manager:
  - **local** mode: a private "Jarvis Local CA" (ECDSA P-384, name-constrained to local names
    and private IPv4) signs 397-day ECDSA P-256 server certificates; real revocation database
    and CRL; `export-ca` explains how to trust the CA on clients;
  - **letsencrypt** mode: certbot with DNS-01 by default (no inbound port exposed), with
    HTTP-01 as an option, staging, and a deploy hook to the stable nginx paths;
  - `status`: creation and expiry dates, remaining time, lifetime used, revocation (local
    DB, signed CRL, OCSP fallback), chain, key/certificate match, served-certificate check
    (`--remote`), and `--json`. Exit codes 0 valid / 7 unusable / 8 expiring;
  - `renew` (automatic migration of the legacy self-signed certificate), `revoke`;
  - `jarvis-cert-renew.service` / `.timer`: runs twice a day and renews 30 days before
    expiry.
- `scripts/lib/common.sh`, the shared bash library: strict mode, ERR/EXIT traps, colorized
  logging (honors `NO_COLOR`), documented exit codes, requirement checks.
- `.shellcheckrc`; ShellCheck now covers `scripts/lib` and `deploy/bin` (Makefile, CI).
- nginx: ACME HTTP-01 webroot location on port 80 (everything else still redirects to HTTPS).
- Anubis bot policy (`deploy/anubis/`): denies AI crawlers and search engines, challenges
  browsers, and lets API clients and Prometheus through (validated with Anubis 1.27.0).
- Default web account `admin` / `admin` with a mandatory password change at first login;
  reset from the local console.
- Commands in the PATH: `jarvis`, `jarvis-console`, `jarvis-cert`.

### Changed
- `install.sh`, `regen-sbom.sh`, `deploy/bin/jarvis` and `deploy/bin/jarvis-console`
  rewritten with functions, colors, option parsing and documented exit codes.
- `install.sh` targets Ubuntu Server 26.04 LTS (24.04 is still accepted with a warning).
- Source headers no longer repeat the version number (`Project : jarvis-home (version: jarvis/VERSION)`).
- The TLS private key is now `root:root 0600` (only nginx reads it).

### Removed
- `scripts/gen-cert.sh`: replaced by `jarvis-cert issue --mode local`.

## [0.1.0] - 2026-09-29

### Added
- **Recognition:**
  - PTZ ONVIF camera tracking (YOLO11n OpenVINO + ByteTrack);
  - InsightFace face recognition with voting, clustering of unknown faces, adaptive
    enrollment and face search.
- **Voice:** offline voice commands (openWakeWord, Vosk, ECAPA speaker verification) and
  Piper TTS.
- **Garage door:** relay control of the Novoferm Novomatic 200 through the dry contact on
  terminal F.
- **Web UI and API:**
  - FastAPI web API behind nginx TLS;
  - framework-free UI in 12 languages;
  - sessions with idle timeout;
  - audit trail with diffs and a SHA-256 hash chain;
  - search by date and daily time range.
- **Settings:** detailed catalog applied hot; write-only secrets; precedence
  parameter > environment > file.
- **Recording:** event-triggered clips with FIFO rotation, time-lapse viewer and MP4
  export.
- **Performance:** hardware capability detection with automatic CPU/GPU tuning; resource
  views.
- **Integrations and monitoring:**
  - MQTT bridge with Home Assistant discovery;
  - rotating log file, remote syslog, Prometheus metrics, Promtail/Alloy templates.
- **Administration:**
  - cmd2 administration shell and pfSense-style local console;
  - hardened systemd units (`jarvis.target`, notify/watchdog, backup timer).
- **DevSecOps:** ruff, bandit, pip-audit, gitleaks, ShellCheck, Trivy, CodeQL, Dependabot,
  pre-commit, and a CycloneDX 1.6 SBOM.

[Unreleased]: https://example.invalid/jarvis-home/compare/v0.8.1...HEAD
[0.8.1]: https://example.invalid/jarvis-home/compare/v0.8.0...v0.8.1
[0.8.0]: https://example.invalid/jarvis-home/compare/v0.7.0...v0.8.0
[0.7.0]: https://example.invalid/jarvis-home/compare/v0.6.0...v0.7.0
[0.6.0]: https://example.invalid/jarvis-home/compare/v0.5.0...v0.6.0
[0.5.0]: https://example.invalid/jarvis-home/compare/v0.4.0...v0.5.0
[0.4.0]: https://example.invalid/jarvis-home/compare/v0.3.0...v0.4.0
[0.3.0]: https://example.invalid/jarvis-home/compare/v0.2.0...v0.3.0
[0.2.0]: https://example.invalid/jarvis-home/compare/v0.1.0...v0.2.0
[0.1.0]: https://example.invalid/jarvis-home/releases/tag/v0.1.0
