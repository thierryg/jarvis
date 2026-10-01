<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/hikvision-ds-2df8c842ixs-ael/README.md
Purpose : Hikvision DS-2DF8C842IXS-AEL(T5) PTZ camera (8 MP): documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Hikvision DS-2DF8C842IXS-AEL(T5): documentation

Documents downloaded on 2026-09-30. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `ds-2df8c842ixs-aelt5_datasheet_v5.7.1_en.pdf` | Datasheet DS-2DF8C842IXS-AEL(T5) (EN), created 2022-01-22, 7 p. Matches the official file "Datasheet-of-DS-2DF8C842IXS-AELT5_V5.7.1_20220121.pdf" | Copy hosted by SourceSecurity.com: https://www.sourcesecurity.com/datasheets/hikvision-ds-2df8c842ixs-ael-t5-ip-dome-camera/co-3425-ga/ds-2df8c842ixs-ael.pdf (see notes) |
| `network-speed-dome_user_manual_g3_v5.5.23_en.pdf` | Network Speed Dome User Manual (EN), ref. UD18928B-C, G3 platform, firmware 5.5.23, 2022-12-23, 103 p. Generic manual for Hikvision network speed domes | https://assets.hikvision.com/prd/public/all/doc/m000032084/UD18928B-C_Network-Speed-Dome_User-Manual_G3,-5.5.23_20221223.PDF |

Notes:
- **www.hikvision.com blocks automated downloads** (TencentEdgeOne JavaScript challenge). The official datasheet (https://www.hikvision.com/content/dam/hikvision/products/S000000001/S000000002/S000000011/S000000024/OFR000036/M000050188/Data_Sheet/Datasheet-of-DS-2DF8C842IXS-AELT5_V5.7.1_20220121.pdf) could not be retrieved directly, nor through the Wayback Machine archive.
- The saved datasheet comes from **SourceSecurity.com**, a professional security directory, not a distributor. Its metadata (creation date 2022-01-22, product title) matches the official V5.7.1 version of 2022-01-21, but byte-for-byte identity could not be verified. **To be replaced** by the official file, downloaded from a browser on the product page: https://www.hikvision.com/en/products/IP-Products/PTZ-Cameras/Ultra-Series/ds-2df8c842ixs-ael-t5-/
- The user manual comes from the official `assets.hikvision.com` CDN. It is a generic "Network Speed Dome" manual. It does not list models, so its exact applicability to the T5 (firmware 5.7.x) is **not confirmed**. The web interface menus may differ slightly.
- Not found as a direct download: a model-specific Quick Start Guide. The QSGs found are on www.hikvision.com, which is blocked. No French documentation found.

## Key points for the project

Source: datasheet V5.7.1.

- **Resolution: 8 MP, 3840 x 2160** max., 1/1.2" progressive CMOS sensor, 42x optical zoom (7.5-315 mm), IR up to 500 m.
- **Power**: 24 V AC (max. 62 W, including 12 W IR and 8 W heater) **or IEEE 802.3bt PoE (max. 51 W, including 12 W IR and 8 W heater)**. The datasheet does not give a class number. 51 W corresponds to class 6 of the 802.3bt standard (inference). An 802.3bt switch or injector is required; PoE+ 802.3at is not enough.
- **ONVIF**: "Open Network Video Interface (Profile S, Profile G, Profile T)". Also ISAPI, ISUP and the Hikvision SDK.
- I/O: 7 alarm inputs, 2 alarm outputs, RS-485 (Pelco-P/D, Hikvision), audio input and output.
- Weight about 9.6 kg, IP67. Operating temperature -40 °C to 70 °C.
- The user manual contains a "10.9 Set ONVIF" section: the procedure requires checking "Enable ONVIF" and then **adding a dedicated ONVIF user** in the web interface before any ONVIF access.
