<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/axis-q6075-e/README.md
Purpose : Axis Q6075-E PTZ camera (1080p): official documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Axis Q6075-E: official documentation

Official Axis Communications documents downloaded on 2026-09-30 from the product support page (https://www.axis.com/products/axis-q6075-e/support) and the help.axis.com portal. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `q6075-e_datasheet_en.pdf` | Datasheet (EN), ref. T10139891/EN/M39.2/202511, 5 p. | https://www.axis.com/dam/public/ea/af/5a/datasheet-axis-q6075-e-ptz-network-camera-en-US-506806.pdf |
| `q6075-e_datasheet_fr.pdf` | Fiche technique (FR datasheet), ref. T10139891_fr/FR/M39.2/202511, 5 p. | https://www.axis.com/dam/public/c8/45/50/datasheet-axis-q6075-e-ptz-network-camera-fr-FR-511580.pdf |
| `q60-e_installation_guide_en.pdf` | Installation Guide AXIS Q60-E Series (Q6074-E / Q6075-E), multilingual (EN first), © 2019-2025, 26 p. | https://www.axis.com/dam/public/9d/b3/fb/axis-q60-e-series--installation-guide-en-US-476747.pdf |
| `q60-e_user_manual_en.pdf` | User Manual AXIS Q60-E Series (Q6074-E / Q6075-E), ref. T10142949, © 2019-2026, 34 p. | https://help.axis.com/download/um_d201_s_xpt_q6075_T10142949_2404.pdf |
| `q6075-e_dimension_drawing_en.pdf` | Dimension drawing, 2 p. | https://www.axis.com/dam/public/cc/77/3e/dimension-drawing-axis-q6075-e-ptz-network-camera-en-US-396127.pdf |
| `q6075-e_discontinuation_statement_en.pdf` | Product discontinuation statement (Q6075 / Q6075-E), 1 p. | https://www.axis.com/dam/public/permalink/258039/product-discontinuation-statement-axis-q6075,-axis-q6075-epdf-en-US_258039.pdf |

Notes:
- Axis publishes neither an installation manual nor a user manual in French for this model. Only the datasheet exists in FR. The installation guide is multilingual and includes a French section.
- The user manual covers the whole Q60-E series (Q6074-E and Q6075-E).
- **End-of-life product**: last order date May 10, 2026, hardware service/RMA until May 10, 2032. Official replacement: AXIS Q6086-E (discontinuation statement).

## Key points for the project

Sources: EN/FR datasheet, installation guide, user manual.

- **Resolution: HDTV 1080p (1920x1080) maximum**, 1/2.8" CMOS sensor, 40x optical zoom, up to 50/60 fps at 1080p. The "Q6075-E is 1080p" assumption is **confirmed**.
- **Power: "High PoE" PoE**, Axis High PoE 60 W SFP midspan supplied (100-240 V AC, max. 66.1 W). Camera power consumption: **typical 14 W, max. 51 W** (datasheet).
- **PoE standard**: the compliance table of the installation guide states **IEEE 802.3bt, 50-57 V DC, max. 51 W**. The datasheet does not give a class number. 51 W at the powered device corresponds to class 6 of the 802.3bt standard (inference, not written in the Axis documents). A PoE+ 802.3at switch (30 W) is not enough.
- Documentation inconsistency: the "Connectors" section of the user manual mentions "Power over Ethernet Plus (PoE+)", which contradicts the datasheet (High PoE 60 W, 51 W max.). Rely on the datasheet and the installation guide.
- **Network**: RJ45 **10BASE-T/100BASE-TX** only (no Gigabit), IP66/IP67 push-pull RJ45 connector supplied.
- **ONVIF**: Profile G, Profile S and Profile T. VAPIX API. PTZ can be driven over ONVIF.
- Operating temperature with the 60 W midspan: -50 °C to 50 °C.
