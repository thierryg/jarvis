<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/ftdi-ft232rl/README.md
Purpose : FTDI FT232R USB UART (door sensor on CTS#): official datasheet and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# FTDI FT232R (FT232RL / FT232RQ): official documentation

Official FTDI document downloaded on 2026-09-30. It was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `ds_ft232r_v2.16_en.pdf` | FT232R USB UART IC Datasheet (DS_FT232R), **Version 2.16**, doc. FT_000053, clearance FTDI# 38, PDF dated 2020-05-21, 40 p. | Official FTDI file https://ftdichip.com/wp-content/uploads/2020/08/DS_FT232R.pdf, **retrieved through the Wayback Machine archive**: https://web.archive.org/web/2023id_/https://ftdichip.com/wp-content/uploads/2020/08/DS_FT232R.pdf |

Notes:
- ftdichip.com is protected by a Cloudflare challenge ("Just a moment...") that blocks curl, even with a browser User-Agent. The Mouser copy also returns a block page. The saved file is the archived copy of the official FTDI PDF (metadata: title "FT232R", version 2.16).
- No French version.

## Key points for the project

Source: DS_FT232R v2.16, tables 3.1/3.4 (pinout), note 3, tables 5.3 to 5.10 (I/O characteristics) and section 6.

- **CTS#**: pin 11 on SSOP-28 (FT232RL), pin 8 on QFN-32 (FT232RQ). "Clear To Send Control Input / Handshake Signal" input, **active low**.
- **Internal pull-up: yes, 200 kΩ to VCCIO**: "When used in Input Mode, the input pins are pulled to VCCIO via internal 200kΩ resistors" and "Only input pins have an internal 200KΩ pull-up resistor to VCCIO". The "CTS with internal pull-up" assumption is **confirmed**. This pull-up is **weak**. With a long cable to the door sensor, an external pull-up (a few kΩ to VCCIO) and filtering remain prudent (recommendation, not a datasheet requirement).
- EEPROM option: the inputs can be programmed to be **gently pulled low during USB suspend** (PWREN# = 1). Take this into account if the PC puts the USB port into suspend.
- **Input switching threshold (Vin): 1.0 to 1.5 V (typ. 1.2 V)**, with 20 to 30 mV of hysteresis. The threshold is the same for VCCIO from 1.8 V to 5 V (tables 5.3 to 5.10). **Dry contact to GND** = CTS# at 0 = active level.
- **Maximum input voltage** (absolute maximum ratings): -0.5 V to VCC + 0.5 V. No external signal may exceed this range.
- **Output levels** (standard drive): at VCCIO = 5 V, Voh 3.2 to 4.9 V and Vol 0.3 to 0.6 V (2 mA). At VCCIO = 3.3 V, Voh 2.2 to 3.2 V and Vol 0.3 to 0.5 V.
- **VCCIO** sets the logic levels of all UART/CBUS I/Os (1.8 V to 5 V). The module used must state whether VCCIO is tied to 5 V or to 3V3OUT (output of the internal 3.3 V regulator).
- VCC min. 4.0 V with the internal oscillator. 3.3 V operation is only possible with an external oscillator.
