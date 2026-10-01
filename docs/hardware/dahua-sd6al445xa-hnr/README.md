<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/dahua-sd6al445xa-hnr/README.md
Purpose : Dahua SD6AL445XA-HNR PTZ camera (4 MP): official documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Dahua SD6AL445XA-HNR: official documentation

Official Dahua documents downloaded on 2026-09-30 from the links on the product page (https://www.dahuasecurity.com/products/PTZ-Cameras/WizMind-Series/SD6A65F/4MP/SD6AL445XA-HNR), hosted on `materialfile.dahuasecurity.com`. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `sd6al445xa-hnr_datasheet_20200605_en.pdf` | Datasheet DH-SD6AL445XA-HNR (EN), Rev 001.001, 2020-06-05, 4 p. | https://materialfile.dahuasecurity.com/uploads/cpq/38837/datasheet/SD6AL445XA-HNR_datasheet_20200605.pdf |
| `network-speed-dome_web3.0_user_manual_v2.0.2_en.pdf` | Network Speed Dome & PTZ Camera Web 3.0 User's Manual (EN), V2.0.2, June 2020, 187 p. (generic web interface manual) | https://materialfile.dahuasecurity.com/uploads/cpq/14367/user_manual/Dahua_Network_Speed_Dome__26_PTZ_Camera_Web_3.0_User_27s_Manual_V2.0.2.pdf |
| `network-speed-dome_installation_manual_v1.0.0_en.pdf` | Network Speed Dome & PTZ Camera Installation Manual (EN), V1.0.0, July 2020, 28 p. | https://materialfile.dahuasecurity.com/uploads/cpq/14367/user_manual/Network_Speed_Dome__26_PTZ_Camera_Installation_Manual_V1.0.0.pdf |
| `sd6al445xa-hnr_dimensions_en.pdf` | Dimension drawing, 1 p. | https://materialfile.dahuasecurity.com/uploads/cpq/3547/drawings/SD6AL445XA-HNR_DIMENSIONS.pdf |

Notes:
- No French documentation is offered on the product page.
- The user and installation manuals are generic (speed dome / PTZ range). The datasheet is model-specific.

## Key points for the project

Source: datasheet dated 2020-06-05.

- **Resolution: 4 MP, 2560 x 1440** max., 1/2.8" CMOS sensor, 45x optical zoom. The "Dahua 4 MP" assumption is **confirmed**.
- **Power: 36 V DC / 2.23 A or Hi-PoE**. Basic consumption 20 W, **max. 36 W** (IR and PTZ active at minimum voltage). The datasheet mentions **neither IEEE 802.3at nor 802.3bt**: "Hi-PoE" is a Dahua proprietary name. A standard PoE+ 802.3at switch (30 W) may not be enough. Plan for the Dahua Hi-PoE injector, an 802.3bt switch whose compatibility must be verified, or the 36 V power supply sold as an accessory.
- **Network**: RJ-45 **10/100Base-T** only.
- **ONVIF**: "ONVIF Profile S&G&T ; CGI".
- I/O: alarms 7 inputs / 2 outputs, 1 RS-485 (1200-115200 bps), 1 audio input.
