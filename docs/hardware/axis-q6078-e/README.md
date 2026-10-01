<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/axis-q6078-e/README.md
Purpose : Axis Q6078-E PTZ camera (4K): official documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Axis Q6078-E: official documentation

> **Status (checked on axis.com, 2026-09-30):** the Q6078-E is **discontinued**; Axis names the
> **Q6088-E** as its replacement, and hardware/RMA support runs until 2031-11-21. See
> [docs/CAMERA-SELECTION.md](../../CAMERA-SELECTION.md).

Official Axis Communications documents downloaded on 2026-09-30 from the product support page (https://www.axis.com/products/axis-q6078-e/support) and the help.axis.com portal. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `q6078-e_datasheet_en.pdf` | Datasheet (EN), ref. T10163369/EN/M28.2/202609, 4 p. | https://www.axis.com/dam/public/e1/f6/c8/datasheet-axis-q6078-e-ptz-camera-en-US-555514.pdf |
| `q6078-e_datasheet_fr.pdf` | Fiche technique (FR datasheet), ref. T10163369_fr/FR/M27.2/202511, 5 p. (one revision older than the EN) | https://www.axis.com/dam/public/b6/cb/36/datasheet-axis-q6078-e-ptz-camera-fr-FR-512205.pdf |
| `q6078-e_installation_guide_en.pdf` | Installation Guide, multilingual (EN first), Ver. M3.3, December 2022, 24 p. | https://www.axis.com/dam/public/67/04/dd/axis-q6078-e-ptz-camera--installation-guide-en-US-386940.pdf |
| `q6078-e_user_manual_en.pdf` | User Manual, ref. T10164156, © 2021-2026, 32 p. | https://help.axis.com/download/um_q6078_e_T10164156_2410.pdf |
| `q6078-e_dimension_drawing_en.pdf` | Dimension drawing, 2 p. | https://www.axis.com/dam/public/04/c5/8d/dimension-drawing-axis-q6078-e-ptz-network-camera-en-US-396129.pdf |

Notes:
- Axis publishes neither a user manual nor an installation guide in French. Only the datasheet exists in FR (revision M27.2; the EN one is M28.2).
- The installation guide is multilingual and includes a French section.

## Key points for the project

Sources: EN/FR datasheet, installation guide, user manual.

- **Resolution: 4K 2160p (3840x2160) maximum**, 1/2.5" RGB CMOS sensor. Up to 25/30 fps in 4K and 50/60 fps in the other resolutions.
- **Power: "High PoE" PoE**, 1-port Axis High PoE 60 W SFP midspan supplied (100-240 V AC, max. 66.1 W). Camera power consumption: **typical 16 W, max. 51 W**. The datasheet gives neither a class nor an 802.3 standard. In practice, 51 W requires an 802.3bt source or the Axis midspan (inference).
- Documentation inconsistency: the "Connectors" section of the user manual mentions "Power over Ethernet Plus (PoE+)", which contradicts the datasheet (51 W max.). Rely on the datasheet.
- **Network**: RJ45 **10BASE-T/100BASE-TX** only, IP66/IP67 push-pull connector supplied.
- **ONVIF**: Profile G, Profile S and Profile T.
- Operating temperature with the 60 W midspan: -50 °C to 50 °C.
