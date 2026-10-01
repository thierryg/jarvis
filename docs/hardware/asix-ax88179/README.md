<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/asix-ax88179/README.md
Purpose : ASIX AX88179A/B USB 3.0 to Gigabit Ethernet: public documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# ASIX AX88179 (AX88179A / AX88179B): public documentation

Official ASIX documents downloaded on 2026-09-30 from the AX88179A and AX88179B product pages ("Download Technical Documentation" section). Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `ax88179a_product_brief_en.pdf` | AX88179A Product Brief, 2024-01-17, 1 p. | https://www.asix.com.tw/en/support/download/file/1973 (page https://www.asix.com.tw/en/product/USBEthernet/Super-Speed_USB_Ethernet/AX88179A) |
| `ax88179a_product_introduction_rev1.05_en.pdf` | AX88179A Product Introduction, revision 1.05, 2024-05-13, 12 p. | https://www.asix.com.tw/en/support/download/file/1753 |
| `ax88179b_product_brief_en.pdf` | AX88179B Product Brief, 2024-05-13, 1 p. | https://www.asix.com.tw/en/support/download/file/1749 (page https://www.asix.com.tw/en/product/USBEthernet/Super-Speed_USB_Ethernet/AX88179B) |
| `ax88179b_product_introduction_rev1.02_en.pdf` | AX88179B Product Introduction, revision 1.02, 2024-05-13, 12 p. | https://www.asix.com.tw/en/support/download/file/1750 |

Notes:
- **Full datasheet not public**: the ASIX FAQ states that a **MyASIX** account is required to download the datasheet, the reference schematic and the PCB files. The documents above are the only ones available without an account.
- The product page of the **original AX88179** (no suffix) no longer responds on asix.com.tw. The site search only returns the AX88179A and the AX88179B. Check which chip the adapter actually uses (with `lsusb`, or `ethtool -i` for the loaded driver). The A/B documents do not necessarily apply to an original AX88179.

## Key points for the project

Source: AX88179A/B product briefs.

- **USB 3.2 Gen 1 to Gigabit Ethernet** controller with an integrated 10/100/1000 PHY.
- AX88179A/B: supported by the in-box drivers of Linux, Android and Chrome OS, and by the **native Linux/macOS CDC-NCM driver** ("driverless, Plug & Play"). They support CDC-NCM and CDC-ECM.
- IEEE 802.3az (EEE), Auto-MDIX, Wake-on-LAN (Magic Packet), IPv4/IPv6 checksum offload. The AX88179B adds IEEE 1588v2 / 802.1AS.
