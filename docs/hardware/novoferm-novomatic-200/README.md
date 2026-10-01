<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/novoferm-novomatic-200/README.md
Purpose : Novoferm Novomatic 200 garage door operator: manuals, terminals, pulse logic
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Novoferm Novomatic 200: documentation

Documents downloaded on 2026-09-30. Each file was checked as a valid PDF with `file`.

| File | Contents | Source |
|---|---|---|
| `novomatic-200_notice_montage_utilisation_fr.pdf` | Notice de pose, d'emploi et d'entretien (FR installation, operating and maintenance manual), ref. **WN 923001 01/12** (FT-751-23 B), 14 p. Wiring diagram in figures 13 to 13d (p. 5), French text from p. 7 ("sans potentiel" instruction on p. 8) | Official Novoferm France file, **retrieved through the Wayback Machine archive**: https://web.archive.org/web/2024id_/https://www.novoferm.fr/fileadmin/novoferm_fr/Dateien/PHOTOS/Habitat/Notices_de_pose/Anciennes_notices_de_pose/Novomatic/FT-751-23_B_Novomatic_200_Francais.pdf |
| `novomatic-200_notice_montage_utilisation_fr_revA_lapeyre.pdf` | Same manual, revision **WN 923001-02-6-50 04/12** (FT_751_23_A), 14 p. | Copy hosted by the retailer Lapeyre: https://statics.lapeyre.fr/img/catalogue/APC0001/484/914/AST484914.pdf |
| `novomatic-200-led_descriptif_fr.pdf` | Descriptif technique Novomatic 200 LED (FR technical description), 1 p. | https://www.novoferm.fr/fileadmin/novoferm_fr/Dateien/PHOTOS/Habitat/Descriptif_type/Descriptif_Novomatic_200_LED.pdf |

Notes:
- The official URL of the manual on novoferm.fr ("Anciennes notices de pose" section) **now redirects to a product page** (HTTP 301). The file is no longer online at Novoferm. The copy archived by the Wayback Machine is the original file from novoferm.fr.
- Both revisions (01/12 and 04/12) show the same wiring diagram. Check the reference printed on the manual shipped with the operator or on the rating plate (side face of the operator head).
- Not found: detailed electrical schematic of the control board (terminal voltages). The manual does not give the voltage present on the control terminals.

## Key points for the project

Source: manual WN 923001 01/12, figures 13 to 13d and the "Notice de pose" (installation) chapter, § 13.

- **Control terminal block** (fig. 13, under the cover): terminals **H, G, F, E**, from left to right.
  - **E**: external antenna (shield on the adjacent terminal).
  - **F**: **"Raccordement pour impulseur externe"** (connection for an external pulse generator, fig. 13b, e.g. key switch or keypad). This is the **push-button** input, on a pair of terminals.
  - **G**: **STOP A input** (e.g. wicket door contact, fig. 13c). Opening it stops the movement or prevents starting in both directions.
  - **H**: **STOP B input** (LS 2 one-way photoelectric cell, fig. 13d). Opening it prevents closing.
- **Contact type**: the manual requires: "Ne connecter aucune ligne sous tension et ne raccorder que des **boutons-poussoirs et des sorties de relais sans potentiel**" (do not connect any live line; connect only potential-free push-buttons and relay outputs). The "push-button is a dry contact" assumption is **confirmed**. A relay NO contact (LCUS board) wired to the F pair is suitable.
- **Voltage on the terminals: not documented**. The manual gives no voltage for input F. The "low voltage" nature is therefore **not confirmed by the documentation** and must be measured with a multimeter, with the operator unplugged and then plugged back in, before any wiring.
- Wire gauge given in fig. 13a: **2 x AWG 22** for pulse generators and accessories (compatible with Wago 221 terminals, 0.14 to 4 mm²).
- **Step-by-step control logic** ("Fonctionnement standard", standard operation): 1st pulse = start toward the programmed end position. Pulse during travel = stop. Next pulse = reverse direction. A single input is therefore used to open, stop and close. The software must know the actual door state (door sensor).
- **Short pulse mandatory**: "Une brève impulsion suffit" (a short pulse is enough). The diagnostic display reports "Impulsion continue au niveau de l'entrée DÉPART… La porte n'accepte plus aucune impulsion de départ" (continuous pulse on the START input… the door no longer accepts any start pulse). **The relay must be released after a short pulse** and never left latched.
- Power: 230 V / 50 Hz, 2P+E plug. Standby 4 W, 160 W max. in operation. Duty cycle 2 min. IP22. 0.4 W LED lighting that switches off after about 90 s (factory setting).
- The supplied remote control uses a 23A 12 V alkaline battery.
