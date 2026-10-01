<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/realtek-rtl8153/README.md
Purpose : Realtek RTL8153 USB 3.0 to Gigabit Ethernet: documentation search and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Realtek RTL8153: documentation

Search carried out on 2026-09-30.

| File | Contents | Source |
|---|---|---|
| (none) | Not found: public RTL8153 datasheet on the manufacturer's website | - |

Notes:
- Realtek publishes **no public datasheet** for the RTL8153. Only the RTL8153B-VB-CG product page exists: https://www.realtek.com/Product/Index?id=4078 (description and feature list, with no downloadable document).
- Copies of the datasheet circulate on third-party sites (datasheet aggregators, board manufacturers' sites). They were not kept, for lack of an official source or a recognized distributor.

## Key points for the project

Source: Realtek RTL8153B-VB-CG product page.

- **10/100/1000M Ethernet controller for USB 3.0** (integrated MAC + PHY + USB 3.0/2.0/1.1 controller + memory), 40-pin QFN package.
- Wake-On-LAN, IEEE 802.3az (EEE), ARP/NS offload, embedded OTP instead of an external EEPROM.
- On Linux, this controller is handled by the `r8152` driver (general knowledge, not stated on the product page).
