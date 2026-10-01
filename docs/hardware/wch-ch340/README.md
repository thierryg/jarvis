<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/wch-ch340/README.md
Purpose : WCH CH340 and LCUS-4 USB relay board: datasheets, product pages, serial protocol
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# WCH CH340 and LCUS USB relay board: documentation

Documents downloaded on 2026-09-30. The PDFs were checked as valid with `file`.

| File | Contents | Source |
|---|---|---|
| `ch340ds1_datasheet_v1D_en.pdf` | "The DataSheet of CH340 (the first)", English DataSheet, **Version 1D**, 7 p. | Mouser copy (WCH file), **retrieved through the Wayback Machine archive**: https://web.archive.org/web/2025id_/https://www.mouser.com/datasheet/2/306/CH340DS1-2487512.pdf |
| `ch340ds1_datasheet_v3D_zh.pdf` | "CH340 手册（一）" (CH340DS1 datasheet in Chinese), **version 3D**, 10 p. | LCSC distributor (official WCH distributor): https://datasheet.lcsc.com/datasheet/pdf/a3b2ca2c7638da2635df3f5b1d90bcc0.pdf?productCode=C14267 (page https://www.lcsc.com/datasheet/lcsc_datasheet_2305301024_WCH-Jiangsu-Qin-Heng-CH340G_C14267.pdf) |
| `lcus-4_product_page_chinalctech.html` | LCUS-4 product page from the manufacturer LC Technology (saved HTML): specifications, terminal block, protocol | http://www.chinalctech.com/cpzx/32.html |
| `lcus-1_product_page_chinalctech.html` | LCUS-1 product page from the same manufacturer (saved HTML): description of the frame format | http://www.chinalctech.com/m/view.php?aid=131 |

Notes:
- **Official WCH datasheet not retrieved directly**: the page https://www.wch-ic.com/downloads/CH340DS1_PDF.html (CH340DS1.PDF, version 3.4, 318 KB, published on 2025-03-12 according to the site API) is a JavaScript application. Its download API answers "请刷新页面后再重试" ("refresh the page and try again") to curl requests. **Download it from a browser** to get the current English version (3.4). The English version 1D saved here is old. The Chinese version 3D is more recent.
- The direct Mouser page (https://www.mouser.com/datasheet/2/306/CH340DS1-2487512.pdf) blocks curl, hence the Wayback archive.
- LC Technology publishes **no PDF** for the LCUS boards. The HTML product pages are the only manufacturer documentation found.

## Key points for the project

### CH340 (source: datasheet v1D EN)
- **5 V or 3.3 V** supply. At 5 V, VCC receives 5 V and the V3 pin is decoupled with 4700 pF or 0.01 µF. At 3.3 V, V3 is tied to VCC, and the other circuits connected to the CH340 must not exceed 3.3 V.
- Supported modem signals: RTS, DTR, DCD, RI, DSR and **CTS#** (input). The datasheet mentions **internal pull-up** resistors on several inputs, without giving their value in version 1D.
- Linux driver: `ch341`, built into the kernel, device `/dev/ttyUSBx` (general knowledge, not stated in the datasheet).

### LCUS-4 relay board (source: LC Technology product page)
- 8-bit MCU + **CH340**. 4 relays **5 V, 10 A / 250 V AC, 10 A / 30 V DC**. Terminals per relay: **COM, NC, NO**. Overcurrent protection and flyback diode.
- 5 V supply over USB or external (jumper "Usb_P" / "Ext_P"). The manufacturer recommends an **external supply beyond 2 active relays**. In the Ext_P position without an external supply, the LED lights up but the relay does not pull in.
- **Serial link: 9600 bps** by default.
- **4-byte frame** (LCUS-1 page): `Data(1)` = start marker (default **0xA0**), `Data(2)` = address (channel number, 0x01 = 1st relay), `Data(3)` = operation (**0x00 = off, 0x01 = on**), `Data(4)` = check byte.
- Commands published for the LCUS-4: `A0 01 01 A2` / `A0 01 00 A1` (channel 1 on/off), `A0 02 01 A3` / `A0 02 00 A2`, `A0 03 01 A4` / `A0 03 00 A3`, `A0 04 01 A5` / `A0 04 00 A4`.
- **Check byte**: the manufacturer does not give the formula. All the examples satisfy `Data(4) = (0xA0 + channel + state) & 0xFF`, i.e. the **sum of the first three bytes modulo 256** (inferred from the examples).
- **Status read**: send `FF`. The board returns text such as `CH1:ON CH2:ON CH3:OFF CH4:OFF`.
- For the Novomatic 200: use the **COM/NO** contact of one relay as a short pulse (on then off, duration to be chosen, for example a few hundred ms), never held (see `../novoferm-novomatic-200/README.md`).
