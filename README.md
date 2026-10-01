<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : README.md
Purpose : Project overview, documentation map, installation paths, tools and commands
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Jarvis: PTZ camera, face recognition and voice control for the garage

A 100 % on-premises system running on a Linux mini-PC:
- a PTZ camera tracks people (YOLO11n + ByteTrack) and recognizes faces (InsightFace);
- an offline voice assistant answers the "hey jarvis" wake word (openWakeWord, Vosk, Piper), only during the
  recognition window after an authorized person was recognized (green LED on), and only for « Jarvis… ouvre /
  ferme la porte du garage »;
- optional license plate recognition (fast-alpr, offline, international plates): a registered plate on an
  approaching vehicle opens the garage, and the garage can optionally close once the vehicle has left;
- a USB relay drives the Novoferm Novomatic 200 garage door, only after an authorized person (or a registered
  plate) has been recognized; every decision is traced in the event log.

Nothing is sent to the cloud.

The software version has a single source: [`jarvis/VERSION`](jarvis/VERSION) (`make version`, `jarvis version`).

## Documentation

| Document | Contents |
|---|---|
| [`INSTALL.md`](INSTALL.md) | Step-by-step installation: Ubuntu Server 26.04 LTS USB key made with balenaEtcher, BIOS, OS install, SSH key, Ansible deployment, first steps, up to a verified deployment |
| [`REQUIREMENTS.md`](REQUIREMENTS.md) | Hardware and software requirements (which Ubuntu to install, minimum sizing) |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history (Keep a Changelog, Semantic Versioning) |
| [`AGENTS.md`](AGENTS.md) | Project memory and standing rules for AI coding agents (`CLAUDE.md` and `GEMINI.md` point to it) |
| [`deploy/README.md`](deploy/README.md) | Map of the deployment artifacts: systemd, nginx, Anubis, TLS, udev, monitoring, commands |
| [`deploy/ansible/README.md`](deploy/ansible/README.md) | Ansible deployment (IaC): inventory, variables, phases, preflight and postflight checks |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Processes, threads, vision and voice pipelines, decision, security, GDPR, storage |
| [`docs/SOFTWARE.md`](docs/SOFTWARE.md) | Software documentation |
| [`docs/diagrams/jarvis-network-flows.pdf`](docs/diagrams/jarvis-network-flows.pdf) | Network flow diagrams and matrix (nginx, Anubis, MQTT, syslog, monitoring, NTP, ACME), nftables rules, camera configuration |
| [`docs/diagrams/jarvis-bom.pdf`](docs/diagrams/jarvis-bom.pdf) | Hardware bill of materials (indicative prices), camera choice, software licenses |
| [`docs/diagrams/jarvis-interconnection.pdf`](docs/diagrams/jarvis-interconnection.pdf) | Wiring of mini-PC / camera / audio / relay / door / sensor, port allocation |
| [`docs/manuals/jarvis-software-documentation.pdf`](docs/manuals/jarvis-software-documentation.pdf) | Software documentation (PDF edition of `docs/SOFTWARE.md`) |
| [`docs/CAMERA-SELECTION.md`](docs/CAMERA-SELECTION.md) | Camera selection guide: RTSP/H.264/ONVIF PTZ requirements, face pixel density, models with official links |
| [`docs/hardware/`](docs/hardware/README.md) | Manuals and datasheets of every hardware module, with key points for the project |
| [`docs/hardware/lenovo-thinkcentre-m73/`](docs/hardware/lenovo-thinkcentre-m73/README.md) | Official Lenovo manuals and key points of the ThinkCentre M73 |
| [`docs/sbom/`](docs/sbom/README.md) | Software Bill of Materials (CycloneDX 1.6 + CSV), regeneration and vulnerability scanning |

The PDFs (US English, contents with page numbers, glossary, bibliography, index) are regenerated with `make pdf` (`uv run --no-project --with markdown python3 docs/diagrams/src/build.py`). This requires Graphviz (`dot`), poppler-utils (`pdftotext`) and Chrome or Chromium 131+.

## Reference hardware

- **Lenovo ThinkCentre M73 Tiny**, **4-core CPU with AVX2**: i5-4590T, i5-4460T, i7-4785T or i7-4765T.
  - The i5-4570T has only 2 cores: it works, but set `detector.imgsz: 416` and `vision.process_fps: 4`.
  - Celeron and Pentium CPUs are excluded: they lack AVX2.
- 8 GB of DDR3 SO-DIMM and an SSD.
- **Everything runs on the CPU**: the Tiny has no PCIe slot, hence no graphics card.
- **ONVIF Profile S** PTZ camera on an isolated network, through a USB 3.0 → Gigabit adapter and a PoE injector.
- **ReSpeaker USB Mic Array v2.0** microphone, paired with a small amplifier and a weatherproof speaker.
- **LCUS-4** USB relay board (CH340) and a reed contact on an **FT232RL TTL** adapter.

Details and quantities are in the BOM PDF. Check a candidate machine with `scripts/jarvis-hwinfo.sh` (see [Tools](#tools)).

## Try it locally (Docker)

To try Jarvis on a workstation, with nothing installed on the host but Docker (Engine + Compose v2):

```bash
docker/jarvis-start.sh                    # build the image, first start downloads the models, then https://localhost:8443/
docker/jarvis-start.sh --rtsp rtsp://192.168.1.30:554/stream1   # analyze a real camera (credentials in docker/.local/jarvis.env)
docker/jarvis-status.sh                   # containers, health, resources, web UI, core, certificate (--logs 50, --json)
docker/jarvis-stop.sh                     # stop (data, models and image kept)
docker/jarvis-clean.sh                    # stop if running and remove the image (--volumes: data too, --purge: everything)
```

- **Login:** `admin` / `admin`, with a new password required at once.
- **Host files:** everything host-specific (config, environment, certificate from a private local CA)
  is generated once in `docker/.local/`, which is ignored by git.
- **Hardware:** the relay and LEDs are simulated (pulses appear in the logs), and the voice
  assistant is off (no microphone in a container).
- **Network:** only `127.0.0.1` is published.
- **Make targets:** `make docker-start`, `docker-status`, `docker-stop`, `docker-clean`.
- **Prerequisites:** the scripts check Docker and print the exact fix when something is missing.
  Ubuntu's `docker.io` needs `sudo apt install docker-compose-v2` (Docker's `docker-ce`:
  `docker-compose-plugin`). On a permission error on `/var/run/docker.sock`, run
  `sudo usermod -aG docker $USER`, then `newgrp docker` or log in again (the membership only
  applies to new sessions). buildx is optional: without it the classic builder is used.

## Installation

The full procedure, from the Ubuntu Server 26.04 LTS USB key (written with balenaEtcher) to a verified deployment, is in [`INSTALL.md`](INSTALL.md). Check the requirements first in [`REQUIREMENTS.md`](REQUIREMENTS.md).

### Option 1: Ansible (recommended)

From the administration workstation, once Ubuntu Server 26.04 LTS is installed on the mini-PC and SSH key access works:

```bash
cd deploy/ansible
cp inventory/hosts.example.yml inventory/hosts.yml   # set ansible_host and ansible_user
./jarvis-deploy.sh deploy
```

`./jarvis-deploy.sh ping` and `./jarvis-deploy.sh preflight` check the target without changing anything; `./jarvis-deploy.sh postflight` re-runs the final checks only. The deployment is idempotent and also applies hardening, the nftables firewall, fail2ban, the SSH banner, TLS and the MOTD. See [`deploy/ansible/README.md`](deploy/ansible/README.md) and [`INSTALL.md`](INSTALL.md).

### Option 2: shell installer (manual alternative)

Directly on the mini-PC (Ubuntu Server 26.04 LTS; 24.04 still accepted). It does not apply the hardening, firewall, fail2ban, banner or MOTD. Options: `sudo ./scripts/jarvis-install.sh --help`.

```bash
sudo ./scripts/jarvis-install.sh                 # --with-speaker (or WITH_SPEAKER=1) for speaker verification
sudoedit /etc/jarvis/config.yaml          # RTSP URL, PTZ host, relay, sensor
sudoedit /etc/jarvis/jarvis.env           # CAMERA_USER, CAMERA_PASSWORD
sudo -u jarvis JARVIS_CONFIG=/etc/jarvis/config.yaml /opt/jarvis/venv/bin/jarvis create-user admin
sudo -u jarvis JARVIS_CONFIG=/etc/jarvis/config.yaml /opt/jarvis/venv/bin/jarvis test-hardware
sudo systemctl enable --now jarvis-core jarvis-api
```

The script installs Python 3.11 with `uv`, the systemd units, nginx (TLS), the udev rules, the commands in the PATH, then downloads the models.

### Option 3: manual Python environment

Python **3.11** is required: some wheels are not yet available for 3.12.

```bash
sudo apt install ffmpeg libportaudio2 libgl1 libglib2.0-0 libgomp1 build-essential
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install "numpy>=1.26,<2" cython                  # insightface is built against it
uv pip install --no-build-isolation -r requirements.txt
uv pip install --no-deps -e .                           # "jarvis" command
```

`requirements.txt` uses the PyTorch **CPU** index. Without it, pip downloads the CUDA build (about 2.5 GB). The optional dependencies (SpeechBrain, Porcupine, gpiod) are commented out in it.

### Option 4: Docker

```bash
cd docker && docker compose up -d --build
docker compose run --rm core setup-models
docker compose run --rm api create-user admin
```

### First sign-in

Open `https://jarvis.local/` (or the mini-PC IP address) and sign in with **`admin` / `admin`**. A **password change is forced** at the first sign-in. If the password is lost: `jarvis-console`, option 3. With the local CA, trust its certificate first (`sudo jarvis-cert export-ca`, see [`INSTALL.md`](INSTALL.md)).

## Tools

| Tool | Role |
|---|---|
| `jarvis` | Administration shell (`help`); `jarvis <command>` runs a single command as the `jarvis` service account |
| `jarvis-console` | Local appliance console (menu), admin password reset |
| `jarvis-cert` | TLS certificate manager: local CA or Let's Encrypt; issue, renew, revoke; `jarvis-cert status` shows creation, expiry, remaining time and revocation |
| `jarvis-motd` | Redisplays the SSH dashboard (JARVIS banner, live metrics, hardware support, services) |
| `scripts/jarvis-hwinfo.sh` | Hardware inventory (system, CPU, NPU, GPU, memory, storage, network, USB, audio, sensors) ending with a Jarvis suitability verdict (`--json`, `--bench`, `--help`) |
| `make version` / `make bump PART=minor` | Print the version / bump it in `jarvis/VERSION` and open the `CHANGELOG.md` entry (`PART=patch\|minor\|major\|X.Y.Z`) |

## Commands

| Command | Role |
|---|---|
| `jarvis core` | Vision, voice, decision, hardware (`jarvis-core` service) |
| `jarvis api` | Web interface and REST API (`jarvis-api` service, behind nginx) |
| `jarvis setup-models` | Downloads and exports the models (the only step that needs Internet access) |
| `jarvis create-user <name>` | Creates or resets a web account |
| `jarvis enroll-face --first-name X [--can-open-garage] photo.jpg…` | Enrolls a person from the command line |
| `jarvis say "text"` | Tests speech synthesis |
| `jarvis test-hardware [--pulse]` | Tests the LEDs and the sensor; `--pulse` also triggers the garage door |

The configuration is read from `$JARVIS_CONFIG`, or by default from `/etc/jarvis/config.yaml`. See `config/config.example.yaml`.

## Tests

```bash
uv venv --python 3.11 .venv-dev && uv pip install --python .venv-dev/bin/python -r requirements-dev.txt
PYTHONPATH=. .venv-dev/bin/python -m pytest
```

These tests cover the logic: decision, identity voting, command parser, relay protocol, PTZ controller, API and authentication. They need neither the models nor the hardware.

## Disclaimer

This is a **convenience** system, not a certified access control system. There is no liveness detection: a photo can fool the face part. Enable `speaker.enabled` and do not expose port 443 to the Internet. The limitations are detailed in chapter 6 of `docs/ARCHITECTURE.md`.

## License

Jarvis is released under the **BSD Zero Clause License (0BSD)**, see [LICENSE](LICENSE): use, copy,
modify and distribute it for any purpose, with no condition. The third-party libraries and AI models
it downloads keep their own licenses. The notable ones are Ultralytics YOLO (AGPL-3.0) and the
InsightFace pretrained models (non-commercial research only); see
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) and the SBOM in `docs/sbom/`.
