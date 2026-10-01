<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/seeed-respeaker-usb-mic-array-v2/README.md
Purpose : Seeed ReSpeaker USB Mic Array v2.0 (XMOS XVF-3000): official documents and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Seeed ReSpeaker USB Mic Array v2.0: official documentation

Official documents downloaded on 2026-09-30 from the links of the Seeed Studio wiki (https://wiki.seeedstudio.com/ReSpeaker_Mic_Array_v2.0/), hosted on `files.seeedstudio.com`. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `respeaker-mic-array-v2.0_product_brief_en.pdf` | Product Brief ReSpeaker Mic Array v2.0 (EN), © 2008-2018 Seeed, 3 p. | https://files.seeedstudio.com/wiki/ReSpeaker_Mic_Array_V2/res/ReSpeaker%20MicArray%20v2.0%20Product%20Brief.pdf |
| `respeaker-mic-array-v2.0_dimensions_en.pdf` | Mechanical drawing "RESPEAKER MIC v2.0", 1 p. | https://files.seeedstudio.com/wiki/ReSpeaker_Mic_Array_V2/res/RESPEAKER%20MIC%20v2.0.pdf |
| `respeaker-mic-array-v2.0.1_schematic_en.pdf` | Schematic v2.0.1 (PDF extracted from the Eagle archive `ReSpeakerMicArrayv2.0.1Schematic.zip`, dated 2020-03-23), 1 p. | https://files.seeedstudio.com/products/107990053/ReSpeakerMicArrayv2.0.1Schematic.zip |
| `xvf3000-3100-tq128_datasheet_1.0_en.pdf` | XMOS XVF3000/XVF3100-TQ128 datasheet, doc. X011274, 2017-10-10, 78 p. | https://files.seeedstudio.com/wiki/ReSpeaker_Mic_Array_V2/res/XVF3000-3100-TQ128-Datasheet_1.0.pdf |
| `xvf3000-3100_product_brief_1.4_en.pdf` | XMOS XVF3000/XVF3100 product brief v1.4, 2 p. | https://files.seeedstudio.com/wiki/ReSpeaker_Mic_Array_V2/res/XVF3000-3100-product-brief_1.4.pdf |

Notes:
- The wiki itself (HTML page) remains the reference for the firmware images, the `dfu.py` tool, the DSP parameters (`tuning.py`) and the Python examples. It was not archived here.
- The XMOS documents are the ones Seeed redistributes on its wiki. No French documentation exists.
- Minor discrepancy between sources: SNR of 63 dB in the product brief, 61 dB on the wiki.

## Key points for the project

Sources: Seeed wiki (Specification, Update Firmware and usb.core.find sections), product brief.

- **Chip**: XMOS **XVF-3000** (AEC, beamforming, dereverberation, noise suppression, gain control, VAD, DoA). 4 ST **MP34DT01TR-M** digital MEMS microphones. WM8960 codec for the 3.5 mm jack output.
- **USB Audio Class 1.0 (UAC 1.0)**: no driver needed on Linux. It shows up as an ALSA sound card.
- **Maximum sample rate: 16 kHz** (wiki).
- **Two firmware images**: `1_channel_firmware.bin` (1 processed channel for ASR) and `6_channels_firmware.bin` (factory firmware: channel 0 = processed ASR audio, channels 1-4 = raw microphones, channel 5 = merged playback). Updated over **USB DFU** (`dfu.py`).
- **USB identifiers**: VID `0x2886`, PID `0x0018`, used by the udev rules.
- **Power**: 5 V DC over micro-USB or through the expansion header. About 180 mA with the LEDs on, 170 mA with them off.
- 12 programmable RGB LEDs. Diameter 70 mm.
