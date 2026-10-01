<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : REQUIREMENTS.md
Purpose : Hardware and software requirements (which Ubuntu to install, minimum sizing)
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Hardware and software requirements

This document lists everything to gather **before** the installation described in
[INSTALL.md](INSTALL.md).
- **Detailed bill of materials** (part numbers, where to buy): `docs/diagrams/jarvis-bom.pdf`.
- **Module manuals:** `docs/hardware/`.
- **Check an existing machine:** `scripts/jarvis-hwinfo.sh` inventories it and gives a Jarvis suitability verdict.

## 1. Operating system

| Item | Choice |
|---|---|
| **Distribution** | **Ubuntu Server 26.04 LTS** ("Resolute Raccoon"), **Server** edition, no desktop environment |
| Image | the latest point release, e.g. `ubuntu-26.04.1-live-server-amd64.iso` (x86_64 / amd64), from https://ubuntu.com/download/server |
| Verification | SHA-256 checksums published at https://releases.ubuntu.com/26.04/ (`SHA256SUMS` + `SHA256SUMS.gpg` signature) |
| Kernel | the 26.04 GA kernel (the HWE kernel brings nothing on this hardware) |
| Also accepted | Ubuntu Server 24.04 LTS with `jarvis_allow_ubuntu_2404: true` (not recommended) |
| **Not supported** | Ubuntu Desktop, Debian, interim (non-LTS) releases such as 26.10, ARM, 32-bit |

### Support lifecycle (official dates)

| Release | Released | End of standard security support | Extended support |
|---|---|---|---|
| **Ubuntu 26.04 LTS** (reference) | April 2026 | **May 2031** | Ubuntu Pro / ESM: May 2036 · Legacy add-on: May 2041 |
| Ubuntu 24.04 LTS | April 2024 | May 2029 | Ubuntu Pro / ESM: May 2034 · Legacy add-on: May 2039 |
| Debian 13 "trixie" (current Debian stable) | 2025-08-09 | 2028-08-09 | LTS: 2030-06-30 · ELTS: 2035-06-30 |
| Debian 12 "bookworm" (oldstable) | 2023-06-10 | **ended 2026-07-11** | LTS: 2028-06-30 · ELTS: 2033-06-30 |

Sources: https://ubuntu.com/about/release-cycle and https://www.debian.org/releases/ (checked 2026-09-30).

**Why Ubuntu Server 26.04 LTS and not Debian 13?**
- **Longer support:** standard security support runs until May 2031 (2028 for Debian 13), and up to 2036/2041 with Ubuntu Pro, at no cost for personal use.
- **Recent packages:** OpenSSH 10.2 with the post-quantum ML-KEM key exchange, OpenSSL 3.5, systemd 259, and current Intel drivers.
- **Reference platform:** it is the platform Jarvis is tested on, and the Ansible playbook refuses any other system.

Debian 12 is no longer an option: its standard security support ended on 2026-07-11.

### Answers in the Ubuntu Server installer

| Screen | Answer |
|---|---|
| Installation type | **Ubuntu Server** (not "minimized") |
| Network | DHCP on the built-in Ethernet port (LAN); reserve the address in your router afterwards |
| Proxy / mirror | defaults (unless on a corporate network) |
| Storage | entire disk, **LVM**; LUKS encryption is possible but then a passphrase is required at every boot (the appliance stays down until it is typed) |
| Profile | server name `jarvis`, an administrator account (e.g. `admin`) with a strong password |
| Ubuntu Pro | optional (Livepatch, support up to 2036) |
| **SSH** | **tick "Install OpenSSH server"**, optionally import your SSH key (GitHub/Launchpad) |
| Featured snaps | **none** |

## 2. Hardware

### 2.1 Host (reference)

| Item | Reference | Minimum | Recommended |
|---|---|---|---|
| Mini-PC | **Lenovo ThinkCentre M73 Tiny** | — | — |
| CPU | 4th generation Intel Core (Haswell), **AVX2 required in practice** | i3-4130T (2 cores / 4 threads) | **i5-4590T / i5-4690T (4 cores)**; the i5-4570T only has 2 cores |
| Memory | DDR3L SO-DIMM | 4 GB | **8 GB** (2 × 4 GB, dual channel) |
| Storage | 2.5" SATA SSD | 64 GB | **128 to 256 GB** (FIFO video recordings) |
| Network | 1 × built-in Gigabit Ethernet | LAN | + **USB 3.0 to Gigabit adapter** (ASIX AX88179 or Realtek RTL8153) for the dedicated camera network |
| Power | Lenovo 65 W power brick | — | a UPS is recommended |
| Firmware | — | UEFI | **After Power Loss = Power On**; Secure Boot enabled; supervisor password set |

**M73 Tiny constraints:**
- no PCIe slot and no DB9 serial port;
- a single RJ45 port;
- 2 × USB 3.0 at the front and 3 × USB 2.0 at the back.

The Intel HD Graphics iGPU decodes the camera video (VAAPI, `i965` driver on Haswell). All inference runs on the CPU (OpenVINO); the start-up hardware detection picks the best settings automatically.

### 2.2 Peripherals

| Function | Module | Connection |
|---|---|---|
| PTZ ONVIF camera | see [docs/CAMERA-SELECTION.md](docs/CAMERA-SELECTION.md) for the full list with official links. Current Axis models: Q6088-E (successor of the discontinued Q6078-E) and Q6086-E (successor of the end-of-life Q6075-E); also Dahua SD6AL445XA-HNR (now marked discontinued by Dahua) and Hikvision DS-2DF8C842IXS-AEL | dedicated camera network (192.168.50.0/24) through the USB Gigabit adapter; **PoE 802.3bt power (≥ 51 W)**: Hi-PoE injector or 36 V DC supply |
| Microphone | Seeed ReSpeaker USB Mic Array v2.0 | USB |
| Speaker | PAM8403 amplifier + 3 W speaker, or a USB speaker | jack / USB |
| Door relay | LCUS-1/4 USB relay board (CH340) | USB; dry contact on **terminal F** (external push-button input) of the Novomatic 200, **never 230 V** |
| Door sensor (optional) | dry contact + FTDI FT232RL (CTS ↔ GND) | USB; **an external pull-up resistor is advised** (the FT232R internal pull-up, about 200 kΩ, is weak) |
| Terminal blocks | Wago 221 | — |

The wiring (diagrams, wire gauges, checks) is described in `docs/diagrams/jarvis-interconnection.pdf`.

### 2.3 Network

- **Address:** a fixed or DHCP-reserved IPv4 address on the LAN for the mini-PC.
- **Internet access during the installation only**, for:
  - `archive.ubuntu.com`;
  - `pypi.org` and `files.pythonhosted.org`;
  - `download.pytorch.org`;
  - `github.com` and `objects.githubusercontent.com` (uv, Python, Anubis, AI models);
  - `huggingface.co` and `alphacephei.com` (models).

  Afterwards Jarvis runs **100 % offline**, except for the security updates.
- **No port open from the Internet.** For remote access, use a VPN (WireGuard, Tailscale). Even the Let's Encrypt mode needs no inbound port: it uses the DNS-01 challenge.

## 3. Administration workstation (Ansible deployment)

| Item | Requirement |
|---|---|
| OS | Linux or macOS (Windows: WSL2 with Ubuntu) |
| Tools | `git`, `ssh`, `rsync`, and **uv** (https://docs.astral.sh/uv/) or ansible-core ≥ 2.16. `jarvis-deploy.sh` falls back to `uvx` when Ansible is missing or too old |
| SSH key | ed25519 (`ssh-keygen -t ed25519`), copied to the mini-PC (`ssh-copy-id`) |
| Installation USB key | 4 GB minimum, written with **balenaEtcher** (https://etcher.balena.io) |
| Web clients | a recent browser (Firefox, Chrome, Edge, Safari). In local-certificate mode, import the Jarvis local CA once (see INSTALL.md) |

## 4. Disk space summary

| Component | Disk space |
|---|---|
| Ubuntu Server | ≈ 5 GB |
| Python environment (CPU PyTorch, OpenVINO, insightface…) | ≈ 3 GB |
| AI models (YOLO11n OpenVINO, buffalo_s, Vosk FR, Piper, openWakeWord, ECAPA) | ≈ 0.5 GB |
| Video recordings and time-lapse | configurable (FIFO quota in Settings) |
| **Free space required by the preflight** | **15 GB** |
