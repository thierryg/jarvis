<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/lenovo-thinkcentre-m73/README.md
Purpose : Lenovo ThinkCentre M73 (Tiny, SFF, Tower): official manuals and key points
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Lenovo ThinkCentre M73: official documentation

Official Lenovo documents downloaded on 2026-09-29. Each file was checked as a valid PDF with `file`.

| File | Contents | Form factor | Machine types | Source |
|---|---|---|---|---|
| `m73_tiny_ug_en.pdf` | User Guide (EN), 5th ed. May 2016, 162 p. | Tiny | 10AX, 10AY, 10DK, 10DL, 10DM, 10DN | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_tiny_ug_en.pdf |
| `m73_tiny_ug_fr.pdf` | Guide d'utilisation (FR user guide), 168 p. | Tiny | same | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_tiny_ug_fr.pdf |
| `m73_sff_ug_en.pdf` | User Guide (EN), 7th ed. May 2016, 158 p. | SFF | 10B4, 10B5, 10B6, 10B7, 10HL, 10HM | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_sff_ug_en.pdf |
| `m73_sff_ug_fr.pdf` | Guide d'utilisation (FR user guide), 164 p. | SFF | same | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_sff_ug_fr.pdf |
| `m73_tower_ug_en.pdf` | User Guide (EN), 7th ed. May 2016, 146 p. | Tower | 10B0, 10B1, 10B2, 10B3, 10HJ, 10HK | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_tower_ug_en.pdf |
| `m73_tower_ug_fr.pdf` | Guide d'utilisation (FR user guide), 154 p. | Tower | same | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_tower_ug_fr.pdf |
| `m73_hmm.pdf` | Hardware Maintenance Manual (EN), 7th ed. August 2015, 256 p. | Tiny + SFF + Tower | 10B0-10B7, 10AX, 10AY, 10DK-10DN, 10HJ-10HM | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_hmm.pdf |
| `ThinkCentre_M73_Tiny_PSREF_Spec.pdf` | PSREF sheet (platform specifications) | Tiny | - | https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M73_Tiny/ThinkCentre_M73_Tiny_Spec.PDF |
| `ThinkCentre_M73_SFF_PSREF_Spec.pdf` | PSREF sheet | SFF | - | https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M73_SFF/ThinkCentre_M73_SFF_Spec.PDF |
| `ThinkCentre_M73_Tower_PSREF_Spec.pdf` | PSREF sheet | Tower | - | https://psref.lenovo.com/syspool/Sys/PDF/ThinkCentre/ThinkCentre_M73_Tower/ThinkCentre_M73_Tower_Spec.PDF |
| `m73_swsg_en.pdf` | Safety, Warranty, and Setup Guide (EN), 2 p. | All | - | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_swsg_en.pdf |
| `m73_swsg_fr.pdf` | Consignes de sécurité, garantie et guide de configuration (FR safety, warranty and setup guide), 2 p. | All | - | https://download.lenovo.com/pccbbs/thinkcentre_pdf/m73_swsg_fr.pdf |

Notes:
- There is no separate BIOS/UEFI PDF. BIOS configuration is described in the "Using the Setup Utility" chapter of each User Guide (F1 at power-on, F12 for the boot menu) and in the HMM.
- Machine types 10AA-10AD and 10AU-10AW do not appear in any of these M73 documents. They probably belong to other models (M73z all-in-one, M83/M93...). Check the MT/TYPE label of the machine.
- The M73**p** (10K9-10KC, Skylake generation) is a different product, not covered here.

## Key points for the project

Sources: PSREF sheets and the "Features" chapter of the User Guides.

### Common to all form factors
- **Chipset**: Intel H81 (Haswell, LGA1150 socket). No full vPro/AMT support ("Intel Standard Manageability" only).
- **CPU** (Haswell, 4th generation): Celeron G18xx, Pentium G32xx/G34xx, Core i3-4130/4150, i5-4570/4590, i7-4770. The low-power "T" variants (35 W) are used in the Tiny. Instruction set: **AVX2 + FMA3** (useful for ONNX Runtime, llama.cpp, OpenVINO). No AVX-512 or AVX-VNNI. Celeron/Pentium have **neither AVX nor AVX2**, so avoid them for inference.
- **Integrated GPU**: Intel HD 4400/4600 (VGA + DisplayPort outputs, dual display). Of little use for AI (OpenVINO GPU usable but limited).
- **RAM**: **16 GB max.**, DDR3-1600 (PC3-12800), **2 slots** (dual channel). The controller drops to 1333 MHz with CPUs that only support that frequency.
- **Network**: Gigabit Ethernet (Wake on LAN, PXE).
- **OS**: Linux listed as "certified/tested" (depending on the machine type).

### Tiny (10AX, 10AY, 10DK-10DN)
- CPU: Celeron G1820T/G1840T, Pentium G3220T/G3240T/G3420T, i3-4130T/4150T, i5-4460T/4570T/4590T, i7-4765T/4785T.
- RAM: 2 x 204-pin DDR3 **SODIMM**, 16 GB max.
- **No PCIe slot** for a graphics card. Only **1 half-size mini PCI Express slot** (Wi-Fi). An accelerator is only possible over USB (Coral USB, Intel NCS2, etc.) or possibly through this mini-PCIe slot (Coral mini-PCIe, compatibility not guaranteed).
- Power: **65 W external power adapter**.
- USB: **2 x USB 3.0 on the front** (1 of them always powered) and **3 x USB 2.0 on the rear**.
- Audio: front panel with **1 x 3.5 mm microphone jack** and **1 x 3.5 mm headset/microphone combo jack**. Realtek ALC283 codec and 1.5 W internal speaker. **No line-in**.
- Serial/COM: **optional**. An optional rear port takes either a **serial port** or a USB 2.0 port depending on the model. An optional external I/O box (over USB) adds 4 x USB 2.0, 1 serial port and 2 x PS/2.
- Storage: 1 x 2.5" SATA 6 Gb/s bay. M.2 SSD on some models according to the PSREF.

### SFF (10B4-10B7, 10HL, 10HM)
- CPU: Celeron G1820, Pentium G3220/G3240/G3420/G3440, i3-4130/4150, i5-4570/4590, i7-4770.
- RAM: 2 x 240-pin DDR3 DIMM (UDIMM), 16 GB max.
- **PCIe** (all **low-profile**): slot 1 **PCIe 2.0 x16 (40 W max.)**, slots 2-3 PCIe 2.0 x1. This requires a low-profile card **with no auxiliary power connector and ≤ 40 W**.
- Power: **240 W** (auto-sensing, 85 %). No 6/8-pin PCIe connector available.
- USB: **2 x USB 3.0 on the rear**, 2 x USB 2.0 on the front, 2 x USB 2.0 on the rear (+2 optional USB 2.0).
- Audio: front panel with microphone and headphone (3.5 mm). Rear panel with **line-in, line-out and microphone** (3.5 mm). Realtek ALC662 codec.
- Serial/COM: **yes, 1 DB9 serial port as standard** (+1 optional second one), optional parallel port, PS/2 keyboard and mouse.

### Tower (10B0-10B3, 10HJ, 10HK)
- CPU: same range as the SFF, plus i5-4670 and i7-4790.
- RAM: 2 x DDR3 DIMM (UDIMM), 16 GB max.
- **PCIe** (**full height**, half length): slot 1 **PCIe 2.0 x16 (60 W max.)**, slots 2-3 PCIe 2.0 x1. Card length is limited (half-length, about 17 cm).
- Power: **280 W** (auto-sensing or manual switch). The User Guide also mentions a **450 W** option on some models. Check for an auxiliary PCIe connector before installing a GPU > 60 W.
- USB: **2 x USB 3.0 on the rear**, 2 x USB 2.0 on the front, 2 x USB 2.0 on the rear.
- Audio: front panel with microphone and headphone. Rear panel with **line-in, line-out and microphone** (3.5 mm). Realtek ALC662 codec.
- Serial/COM: **yes, 1 DB9 serial port as standard** (+1 optional), optional parallel port, PS/2.

### Conclusion for vision and voice
- For a GPU: only the **SFF** (low-profile, ≤ 40 W with no auxiliary power) or the **Tower** (≤ 60 W from the slot) are suitable. The link is **PCIe 2.0**, which limits bandwidth but remains acceptable for inference.
- The **Tiny** can only take a USB 3.0 accelerator (front) or a mini-PCIe one. Its i5/i7 "T" CPU with AVX2 allows light CPU inference (Whisper tiny/base, ONNX/OpenVINO face detection).
- RAM is capped at 16 GB DDR3: this is a limiting factor for local LLMs.
- For the microphone: 3.5 mm jack on every model. A true line-in only exists on the SFF and the Tower. A USB microphone or webcam remains the simplest solution.
