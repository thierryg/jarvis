<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/README.md
Purpose : Index of the hardware module manuals and datasheets, gaps, checked assumptions
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Jarvis hardware documentation

Index of the manuals and datasheets of the hardware modules. Each subfolder contains a `README.md` that lists the files, their version, their source and the key points for the project. The documents were downloaded on 2026-09-29 (M73) and 2026-09-30 (other modules).

| Folder | Module | Role in the project | Documents |
|---|---|---|---|
| [`lenovo-thinkcentre-m73/`](lenovo-thinkcentre-m73/README.md) | Lenovo ThinkCentre M73 (Tiny, SFF, Tower) | Host mini-PC | User Guides EN/FR, HMM, PSREF, safety guide |
| [`axis-q6075-e/`](axis-q6075-e/README.md) | Axis Q6075-E | ONVIF PTZ camera (1080p) | Datasheet EN/FR, installation guide, user manual, dimension drawing, end of life notice |
| [`axis-q6078-e/`](axis-q6078-e/README.md) | Axis Q6078-E | ONVIF PTZ camera (4K) | Datasheet EN/FR, installation guide, user manual, dimension drawing |
| [`hikvision-ds-2df8c842ixs-ael/`](hikvision-ds-2df8c842ixs-ael/README.md) | Hikvision DS-2DF8C842IXS-AEL(T5) | ONVIF PTZ camera (8 MP) | Datasheet (third-party copy, to be replaced), generic user manual |
| [`dahua-sd6al445xa-hnr/`](dahua-sd6al445xa-hnr/README.md) | Dahua SD6AL445XA-HNR | ONVIF PTZ camera (4 MP) | Datasheet, Web 3.0 user manual, installation manual, dimension drawing |
| [`reolink-argus-pt/`](reolink-argus-pt/README.md) | Reolink Argus PT (5 MP, battery, dual-band Wi-Fi) | Owner's camera; no RTSP/ONVIF without a Reolink Home Hub, not suitable standalone | Specification sheet (FR), quick start guides (2022, 2025), battery camera user manual |
| [`novoferm-novomatic-200/`](novoferm-novomatic-200/README.md) | Novoferm Novomatic 200 | Garage door operator | FR installation and operating manual (2 revisions), technical description |
| [`seeed-respeaker-usb-mic-array-v2/`](seeed-respeaker-usb-mic-array-v2/README.md) | Seeed ReSpeaker USB Mic Array v2.0 (XMOS XVF-3000) | USB microphone | Product brief, schematic, dimension drawing, XVF3000 datasheet and brief |
| [`ftdi-ft232rl/`](ftdi-ft232rl/README.md) | FTDI FT232R | USB-to-serial interface (door sensor on CTS) | Datasheet DS_FT232R v2.16 |
| [`wch-ch340/`](wch-ch340/README.md) | WCH CH340 + LCUS-4 relay board | USB relay board | CH340 datasheet EN (1D) and ZH (3D), LCUS product pages (protocol) |
| [`diodes-pam8403/`](diodes-pam8403/README.md) | Diodes PAM8403 | Class-D audio amplifier | Datasheet |
| [`realtek-rtl8153/`](realtek-rtl8153/README.md) | Realtek RTL8153 | USB 3.0 to Gigabit adapter | No public document (link to the product page) |
| [`asix-ax88179/`](asix-ax88179/README.md) | ASIX AX88179A / AX88179B | USB 3.0 to Gigabit adapter | Product briefs, product introductions (datasheet restricted to MyASIX) |
| [`wago-221/`](wago-221/README.md) | Wago 221 series (221-412, -413, -415) | Lever terminal blocks | Datasheets |

## Missing documents or documents to replace

- **Hikvision**: the official datasheet is blocked by an anti-bot challenge on www.hikvision.com. The current copy comes from SourceSecurity.com and must be replaced by downloading it from a browser. No model-specific Quick Start Guide.
- **WCH CH340**: the current English version (3.4) of the datasheet must be downloaded from a browser on wch-ic.com.
- **Novomatic 200**: the voltage present on the "external pulse generator" input (terminal F) is not documented and must be measured.
- **Realtek RTL8153**: no public datasheet.
- **ASIX**: the full datasheet requires a MyASIX account.
- **Wago**: no French datasheet available as a static PDF.

## Project assumptions checked against the documentation

| Assumption | Verdict | Source |
|---|---|---|
| Axis Q6075-E is 1080p | Confirmed (1920x1080 max.) | Q6075-E datasheet |
| Dahua SD6AL445XA-HNR is 4 MP | Confirmed (2560x1440) | Dahua datasheet |
| FT232R CTS# has an internal pull-up | Confirmed: 200 kΩ to VCCIO (weak) | DS_FT232R v2.16 |
| Novomatic push-button input is a dry contact | Confirmed: "boutons-poussoirs et sorties de relais sans potentiel" (potential-free push-buttons and relay outputs) | Manual WN 923001 |
| … at low voltage | **Not documented**: to be measured | Manual WN 923001 |
| (Point of attention) Is PoE+ 802.3at enough for the cameras? | **No**: Axis 51 W max. (802.3bt / High PoE 60 W), Hikvision 802.3bt 51 W max., Dahua Hi-PoE 36 W max. | Datasheets |
