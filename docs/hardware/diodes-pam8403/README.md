<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/diodes-pam8403/README.md
Purpose : Diodes PAM8403 class-D audio amplifier: official datasheet and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Diodes PAM8403: official documentation

Official Diodes Incorporated document downloaded on 2026-09-30. It was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `pam8403_datasheet_en.pdf` | PAM8403 "Filterless 3W Class-D Stereo Audio Amplifier", doc. **DS36439 Rev. 2-3**, 11 p. | https://www.diodes.com/assets/Datasheets/PAM8403.pdf |

Notes:
- No French version.

## Key points for the project

Source: datasheet DS36439 Rev. 2-3.

- **Filterless stereo class-D** amplifier: **3 W per channel at 10 % THD into 4 Ω at 5 V** (3.2 W typ. in the characteristics table). At 3.6 V, output power drops to 1.6 W into 4 Ω.
- **Supply: 2.5 V to 5.5 V** (absolute maximum rating: 6.0 V). It can therefore be powered from USB 5 V.
- Audio input voltage: -0.3 V to VDD + 0.3 V (absolute maximum rating).
- **Active-low MUTE** pin (pin 5). Separate PVDD (4, 13) and VDD (6) pins for power and analog.
- Electrical characteristics are given at 25 °C, VDD = 5 V, gain = 24 dB, RL = 8 Ω, unless otherwise noted.
