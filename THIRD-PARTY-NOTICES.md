<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : THIRD-PARTY-NOTICES.md
Purpose : Scope of the 0BSD license and licenses of the third-party components and AI models
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Third-party notices

## Scope of the Jarvis license

The Jarvis source code, scripts, configuration, documentation and artwork in this repository are
released under the **BSD Zero Clause License (0BSD)** ([LICENSE](LICENSE)): anyone may use, copy,
modify and distribute them, for any purpose, with or without fee, with **no condition at all** (not
even keeping the copyright notice).

0BSD was chosen because it is:
- the least restrictive license approved by the OSI;
- valid in every jurisdiction, including those (such as France) where an author cannot fully waive
  their rights, which public-domain dedications like the Unlicense or CC0 attempt to do.

**The 0BSD grant covers only the files written for Jarvis.** The components that Jarvis downloads
and runs keep their own licenses. They are **not** relicensed, and some of them are more
restrictive than 0BSD.

## Components with notable conditions

| Component | License | What it implies |
|---|---|---|
| Ultralytics YOLO11 (library and `yolo11n` weights) | **AGPL-3.0** | a modified version offered to users over a network must publish its source; Ultralytics sells an enterprise license for closed products |
| InsightFace pretrained models (`buffalo_s`) | **non-commercial research only** | commercial use needs a license from InsightFace; the `insightface` library code itself is MIT |
| Picovoice Porcupine (optional wake word) | proprietary, access key required | free tier with usage limits |
| SpeechBrain ECAPA voxceleb model (optional) | Apache-2.0 | attribution and notice |
| Vosk French model, openWakeWord, Piper voices | Apache-2.0 / MIT / per-voice licenses | check each voice's model card |
| Anubis (anti-bot proxy) | MIT | notice kept in the Debian package |
| fast-alpr, fast-plate-ocr, open-image-models (license plates, optional) | MIT (code) | the licence of the downloaded model weights (YOLOv9 plate detector, CCT OCR) is not stated on the project pages: check it before any commercial use |
| FFmpeg (system package) | LGPL-2.1+ / GPL depending on the build | Ubuntu build, distributed by Ubuntu |

The exact list, with the versions, suppliers and licenses of every Python package, AI model and
system component, is the **CycloneDX SBOM**: `docs/sbom/jarvis-sbom.cdx.json` and
`docs/sbom/jarvis-sbom.csv` (regenerate them with `make sbom`).

## Practical rule

- **Personal or home use** of Jarvis as shipped is compatible with every license above.
- **A commercial product or service built on Jarvis** must review at least the AGPL-3.0 (Ultralytics)
  and the non-commercial InsightFace model terms. One option is to replace these two components with
  permissively licensed alternatives.
