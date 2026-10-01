<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/CAMERA-SELECTION.md
Purpose : Camera selection guide: requirements, RTSP/ONVIF/PTZ constraints, models with official links
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Camera selection guide

This guide explains how to choose a camera that works with Jarvis on the reference host (Lenovo ThinkCentre M73 Tiny, Intel Haswell CPU with HD Graphics 4600, Ubuntu Server 26.04). It lists the requirements that come from the code, the camera types, a short list of models with their official manufacturer links, and the configuration and commissioning steps.

**All vendor data was checked on 2026-09-30** on the manufacturers' own web sites and datasheets. A value marked **n/c** (not confirmed) could not be seen on an official page or datasheet: check it before buying. Vendors change product lines and firmware often, so re-check the product page on the day you order.

## Contents

1. [Purpose and how to use this guide](#1-purpose-and-how-to-use-this-guide)
2. [Requirements](#2-requirements)
3. [Pixels on the face](#3-pixels-on-the-face)
4. [Camera types compared](#4-camera-types-compared)
5. [Models](#5-models)
6. [RTSP URL formats by vendor](#6-rtsp-url-formats-by-vendor)
7. [Jarvis configuration mapping](#7-jarvis-configuration-mapping)
8. [Commissioning checklist and troubleshooting](#8-commissioning-checklist-and-troubleshooting)
   - [8b. License plate reading (optional)](#8b-license-plate-reading-optional)
9. [Sources](#9-sources)

## 1. Purpose and how to use this guide

Jarvis reads **one** camera: an RTSP video stream for person detection and face recognition, and, optionally, an ONVIF PTZ service to follow and zoom on the visitor. Any camera that meets the mandatory requirements of section 2 works. A PTZ camera with optical zoom gives the best recognition range; a fixed camera also works, without tracking and zoom.

Before buying a camera, validate it against this short checklist (each point is detailed in section 2):

- [ ] It serves a **local RTSP stream** (no vendor cloud, hub or app needed to get the stream).
- [ ] At least one stream (main or sub) can be encoded in **H.264**.
- [ ] It offers **2 to 4 MP** (1080p or more) at a frame rate you can set to **15 fps or less**.
- [ ] It is **ONVIF Profile S or T** conformant; for tracking, the ONVIF **PTZ service** supports `ContinuousMove`, `Stop` and presets (`GotoPreset`).
- [ ] It is **wired Ethernet**, ideally powered by **PoE** with a known class and maximum power.
- [ ] Outdoor rating (**IP66** or better, **IK10** if it can be reached by hand), **IR** night vision, **WDR** for a backlit doorway.
- [ ] Local user accounts, and cloud / P2P features that can be **disabled**.
- [ ] The number of **pixels on a face** at the recognition distance is at least 80 px (section 3).

## 2. Requirements

### 2.1 Summary table

| Level | Requirement | Rationale | Code / document reference |
|---|---|---|---|
| Mandatory | Local RTSP stream, reachable over TCP | Jarvis opens the stream with OpenCV + FFmpeg and forces RTSP over TCP with a 5 s socket timeout | `jarvis/vision/camera.py` (`OPENCV_FFMPEG_CAPTURE_OPTIONS = "rtsp_transport;tcp\|stimeout;5000000"`) |
| Mandatory | Continuous stream (camera never sleeps) | Jarvis analyzes the stream 24/7; a lost stream is only reopened after a backoff that starts at `camera.reconnect_delay_s` (2 s) | `jarvis/vision/camera.py`, `CameraConfig` in `jarvis/config/settings.py` |
| Mandatory | Wired or mains-powered camera, not battery | Battery cameras sleep between events and often have no standalone RTSP | `docs/hardware/reolink-argus-pt/README.md` |
| Strongly recommended | H.264 on the stream Jarvis reads | The Intel HD 4600 (Haswell, Gen7.5) VAAPI decoder handles H.264 only; H.265/HEVC is decoded by the CPU | `jarvis/vision/camera.py` (`# VAAPI (H.264 only on the Intel HD 4600 GPU)`), `jarvis/core/sysinfo.py` (`camera.hw_accel` = VAAPI reports H264) |
| Strongly recommended | 1080p or 2-4 MP, 15 fps or less | Jarvis analyzes about 6 frames/s (`vision.process_fps: 6.0`); more pixels or frames only cost decoding time. Decoding 4K H.265 continuously saturates a Haswell-class CPU | `VisionConfig`, `CameraConfig` comment in `jarvis/config/settings.py` |
| Strongly recommended | ONVIF Profile S or T with the PTZ service (`GetProfiles`, `ContinuousMove` pan/tilt **and** zoom, `Stop`, `GotoPreset`) | This is the only PTZ interface Jarvis drives; vendor-proprietary PTZ (app or cloud only) is not usable | `jarvis/vision/ptz.py` (`OnvifPTZ`, onvif-zeep-async) |
| Strongly recommended | Optical zoom (PTZ) | Keeps enough pixels on a face at 3-8 m (section 3); the tracker zooms toward `ptz.target_height_ratio: 0.55` | `jarvis/vision/ptz.py`, `PTZConfig` |
| Strongly recommended | Wired Ethernet on the dedicated camera network, PoE | Stable latency and no Wi-Fi drops; the camera sits on 192.168.50.0/24 behind the USB 3.0 Gigabit adapter of the mini-PC | `REQUIREMENTS.md` section 2.2, `config/config.example.yaml` (`ptz.host`) |
| Recommended | PoE class matched to the camera | Outdoor PTZ cameras with heater and IR need IEEE 802.3bt (up to 51 W for the BOM models); an 802.3at (30 W) switch is not enough for them | `REQUIREMENTS.md`, `docs/hardware/README.md` |
| Recommended | IP66 or better, IK10 if within reach | Outdoor doorway or gate | Datasheets (section 5) |
| Recommended | IR night vision covering the recognition distance | Night visitors; under IR the image is grayscale: recognition still works but with lower accuracy | Section 2.4 |
| Recommended | WDR (true WDR, 120 dB or more) | A doorway is often backlit: without WDR the face is a dark silhouette | Section 2.4 |
| Recommended | Local accounts, ability to disable cloud / P2P, separate low-privilege user | Jarvis is 100 % on-premises; credentials are stored as Jarvis secrets | `SecretsConfig` in `jarvis/config/settings.py` |
| Nice to have | ONVIF presets you can name, a "sound" preset | `ptz.home_preset` (default `"1"`) and optional `ptz.sound_preset` | `PTZConfig` |
| Nice to have | Third stream, Profile T, H.264 High profile selectable | Keep a high-resolution stream for recording and a small one for analysis | Section 6 |

### 2.2 Video stream and decoding

- `jarvis/vision/camera.py` opens `camera.rtsp_url` with `cv2.VideoCapture(url, cv2.CAP_FFMPEG, params)` and keeps a one-frame buffer, so only the freshest frame is analyzed.
- The environment variable `OPENCV_FFMPEG_CAPTURE_OPTIONS` is set with `setdefault` to `rtsp_transport;tcp|stimeout;5000000`: **RTSP is always carried over TCP** and a socket that stays silent for **5 s** is dropped. The camera must therefore accept RTSP over TCP (interleaved), which every ONVIF Profile S camera does.
- On loss, the thread reconnects with an exponential backoff starting at `camera.reconnect_delay_s` (default 2.0 s).
- `camera.hw_accel` (default `"auto"`) enables VAAPI hardware decoding. In `"auto"` mode, `jarvis/core/sysinfo.py` turns it on only when `vainfo` reports an H264 decode profile. On the HD 4600 (Haswell, Gen7.5, `i965` driver) VAAPI decodes **H.264 only**: an H.265/HEVC stream is decoded by the CPU, which competes with YOLO and InsightFace.
- `CameraConfig` in `jarvis/config/settings.py` notes that continuously decoding 4K H.265 saturates a Haswell-class CPU and recommends the camera's sub stream (720p H.264) for analysis. The default is `rtsp_url: "rtsp://192.168.50.64:554/stream2"`.
- `vision.process_fps` defaults to **6.0** analyses per second (`VisionConfig`); the automatic performance mode lowers it to 4 on a 2-core CPU and raises it to 10 with an accelerator or 6+ cores (`jarvis/core/sysinfo.py`).

**Requirement:** an **H.264** stream (main or sub), **1080p or 2-4 MP, 15 fps or less**. Choose which stream Jarvis reads from the pixel budget of section 3: the sub stream is enough when the PTZ zooms on the visitor; a fixed camera usually needs the main stream in H.264.

### 2.3 PTZ over ONVIF

`jarvis/vision/ptz.py` implements `OnvifPTZ` with **onvif-zeep-async** on its own asyncio loop. It uses:

| ONVIF call | Service | Use in Jarvis |
|---|---|---|
| `GetProfiles` | Media | Picks the media profile token `ptz.profile_index` (default 0) |
| `ContinuousMove` | PTZ | Proportional tracking: pan/tilt velocity **and** zoom velocity in one request |
| `Stop` | PTZ | Stops pan, tilt and zoom (`PanTilt: true, Zoom: true`) |
| `GotoPreset` | PTZ | Returns to `ptz.home_preset` (default `"1"`) after `ptz.return_home_after_s` (30 s) without a target, and goes to `ptz.sound_preset` on a sound trigger |

**Requirement for tracking:** ONVIF **Profile S** (or T) with the **PTZ service** supporting `ContinuousMove` (pan/tilt and zoom), `Stop` and presets. PTZ that is only available in a vendor app, cloud or proprietary SDK does not work with Jarvis. The ONVIF port is `ptz.port` (default 80; some vendors use another port, see section 6).

With `ptz.enabled: false`, `build_ptz()` returns `NullPTZ` and the tracker disables itself (`self.enabled = not isinstance(ptz, NullPTZ)`): **a fixed camera works**, with detection and recognition only, and no tracking, zoom or home preset.

### 2.4 Other constraints

- **Network.** Wired Ethernet is strongly preferred. The reference design puts the camera on a **dedicated camera network, 192.168.50.0/24**, on a **USB 3.0 to Gigabit adapter** (ASIX AX88179 or Realtek RTL8153) of the M73 Tiny, which has a single RJ45 port (`REQUIREMENTS.md`). The ONVIF PTZ comment in `config/config.example.yaml` assumes about 0.5 s of RTSP latency on a wired camera; Wi-Fi adds jitter and drops.
- **Power (PoE).** IEEE 802.3af (Type 1, up to 15.4 W at the source), 802.3at (Type 2, PoE+, 30 W at the source) and 802.3bt (Types 3-4, 60-90 W at the source). Outdoor PTZ domes with heaters and IR illuminators draw far more than fixed cameras: the BOM PTZ cameras need **up to 51 W** (802.3bt or a vendor High PoE / Hi-PoE injector, or a DC/AC supply). Check the maximum power in the datasheet, not the typical power, and size the switch power budget accordingly.
- **Weatherproofing.** IP66 (jets of water) or IP67 (immersion) for outdoor use; IK10 when the camera can be reached by hand.
- **Night vision.** Built-in IR must cover the recognition distance. Under IR the image is **grayscale**: InsightFace still matches faces, but accuracy is lower than in color, and enrollment photos are in color. Several high-end PTZ models have **no built-in IR** and rely on external illuminators (section 5).
- **WDR.** A doorway usually has a bright background and a shaded face; true WDR (120 dB or more) or a vendor HDR mode keeps the face exposed.
- **Privacy and security.** Local credentials; a dedicated low-privilege user for Jarvis (and, on Hikvision, a dedicated ONVIF user); cloud, P2P and UPnP disabled; RTSP enabled locally **without** a vendor hub or cloud.
- **Not suitable:** battery or Wi-Fi-only cloud cameras. Example: the owner's **Reolink Argus PT** has no standalone RTSP/ONVIF, needs a Reolink Home Hub, and the hub closes the session after 5 minutes (see `docs/hardware/reolink-argus-pt/README.md`).

## 3. Pixels on the face

### 3.1 Why it matters

`FaceConfig` in `jarvis/config/settings.py` sets `min_face_px: 40` (a smaller face is ignored) and `det_size: 320` (input size of the face detector). 40 px is enough to **detect** a face, but **recognition** quality needs more pixels. Aim for:

- **≥ 80 px** across the face at the recognition distance: acceptable;
- **≥ 112 px**: good (the face crop given to the recognition model is 112 × 112 px, so fewer pixels are upscaled).

### 3.2 Formula

A human face is about **16 cm** wide. For a camera with a horizontal field of view HFOV and an image width of W pixels, at distance d (meters):

```
scene width at d (m)   = 2 × d × tan(HFOV / 2)
px_on_face             = W × 0.16 / (2 × d × tan(HFOV / 2))
```

### 3.3 Worked examples

Horizontal pixels on a 16 cm face. W = 1280 (720p sub stream), 1920 (2 MP), 2560 (4 MP, 2560 × 1440) and 3840 (8 MP). **Bold** = at least 112 px (good), plain = 80-111 px (acceptable), *italic* = below 80 px (too small for reliable recognition).

| HFOV | Stream | 2 m | 3 m | 5 m | 8 m |
|---|---|---|---|---|---|
| 90° (wide fixed lens) | 720p (1280) | *51* | *34* | *20* | *13* |
| 90° | 2 MP (1920) | *77* | *51* | *31* | *19* |
| 90° | 4 MP (2560) | 102 | *68* | *41* | *26* |
| 90° | 8 MP (3840) | **154** | 102 | *61* | *38* |
| 60° (standard / light zoom) | 720p (1280) | 89 | *59* | *35* | *22* |
| 60° | 2 MP (1920) | **133** | 89 | *53* | *33* |
| 60° | 4 MP (2560) | **177** | **118** | *71* | *44* |
| 60° | 8 MP (3840) | **266** | **177** | 106 | *67* |
| 30° (zoomed PTZ / telephoto) | 720p (1280) | **191** | **127** | *76* | *48* |
| 30° | 2 MP (1920) | **287** | **191** | **115** | *72* |
| 30° | 4 MP (2560) | **382** | **255** | **153** | 96 |
| 30° | 8 MP (3840) | **573** | **382** | **229** | **143** |

### 3.4 What this means

- A **wide fixed camera** (90° or more) only recognizes faces within **about 2-3 m**, even in 4-8 MP. Mount it close to where the visitor stops (door, intercom), at face height, and read the **main stream in H.264**.
- **Optical zoom** narrows the field of view without losing pixels: going from 60° to 30° roughly doubles the pixels on the face. This is why a **PTZ camera with optical zoom** is recommended: Jarvis detects the person on a wide view, then `ContinuousMove` zooms until the person fills about 55 % of the frame height (`ptz.target_height_ratio: 0.55`). At that framing a 720p or 1080p sub stream already gives well over 112 px on the face, so the CPU-friendly sub stream is enough.
- **Digital zoom** does not add information: it only crops and upscales.
- The HFOV of a zoom lens varies between its wide and tele ends: take both values from the datasheet and compute the pixels at your real distances.

## 4. Camera types compared

| Type | Pros for Jarvis | Cons for Jarvis | Verdict |
|---|---|---|---|
| **Outdoor PTZ dome with optical zoom** (20-45x) | Tracking and zoom over ONVIF, face recognition at 5-10 m, usually IP66/67 and IK10, often built-in IR | Price, weight, power (802.3bt or a vendor High PoE injector up to 51 W), some high-end models have no built-in IR | **Recommended** |
| **Mid-range / entry PTZ** (4-25x, PoE+) | Cheaper, 802.3af/at power, ONVIF PTZ on most professional brands | Shorter zoom, smaller sensor, weaker IR; consumer brands may not document ONVIF PTZ | **Recommended** to **Suitable**, depending on documented ONVIF PTZ |
| **Pan/tilt without optical zoom** (consumer "PT" cameras) | Cheap, can point at the door | Fixed wide lens: recognition only within 2-3 m (section 3); zoom velocity is ignored | **Suitable** at short range |
| **Fixed bullet / turret / dome** | Cheap, low power (802.3af), simple, reliable | No tracking or zoom (`ptz.enabled: false`); must be mounted close to the visitor | **Fixed-only** |
| **Dual-lens trackers** (wide + tele lens, in-camera auto-tracking) | Wide overview plus a tele view | Tracking runs in the vendor firmware/app; ONVIF may expose only one lens and PTZ control over ONVIF is often not documented | **Suitable** with caveats; check ONVIF PTZ on the device |
| **Battery / Wi-Fi-only cloud cameras** | Easy to install | Sleep between events, no standalone RTSP/ONVIF, cloud-dependent | **Not suitable** |

## 5. Models

Column legend: **ONVIF PTZ** = PTZ control available over ONVIF (yes when the vendor lists ONVIF Profile S/T on a PTZ camera; n/c when the vendor does not state it). **Power** is the maximum from the datasheet. All links point to the manufacturer's own site; datasheet links are in section 9. Checked on 2026-09-30.

### 5.1 Outdoor PTZ domes with optical zoom (BOM and high-end)

| Vendor | Model | Type | Sensor / resolution | Optical zoom | Codecs (H.264?) | ONVIF profiles | ONVIF PTZ | Power | IP / IK | IR range | Official link | Jarvis verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Axis | **Q6078-E** (BOM) | Outdoor PTZ | 1/2.5" CMOS, 3840 × 2160 (4K) | 20x | H.264 (Baseline/Main/High), H.265, MJPEG | G, S, T | Yes | Axis High PoE, 60 W midspan included; typical 16 W, max 51 W; IEEE class n/c | IP66/IP67, IK10 | None built in (T90 illuminators) | [axis.com](https://www.axis.com/products/axis-q6078-e) | **Recommended** (BOM). **Discontinued**: Axis states it is replaced by the **Q6088-E**; hardware/RMA support until 2031-11-21 |
| Axis | **Q6088-E** | Outdoor PTZ | 1/2" CMOS, 3840 × 2160 (4K) | 34x | H.264 (Baseline/Main/High), H.265, AV1, MJPEG | G, M, S, T | Yes | IEEE 802.3bt Class 6, max 51 W (power-optimized mode on 802.3at Class 4, max 25.5 W); 90 W midspan included | IP66/IP67, IK10 | None built in (T90D illuminators) | [axis.com](https://www.axis.com/products/axis-q6088-e) | **Recommended**. Official successor of the Q6078-E; use a 1080p/720p H.264 stream for analysis |
| Axis | **Q6075-E** (BOM) | Outdoor PTZ | 1/2.8" CMOS, 1920 × 1080 | 40x | H.264 (Baseline/Main/High), H.265, MJPEG | G, S, T | Yes | Axis High PoE, 60 W midspan; typical 14 W, max 51 W; installation guide: IEEE 802.3bt | IP66/IP67, IK10 | None built in | [axis.com](https://www.axis.com/products/axis-q6075-e) | **Recommended** if already owned. **End of life** (last order 2026-05-10, RMA until 2032-05-10); Axis states it is replaced by the **Q6086-E** |
| Axis | **Q6086-E** | Outdoor PTZ | 1/2" CMOS, 2688 × 1512 (4 MP) | 34x | H.264 (Baseline/Main/High), H.265, AV1, MJPEG | G, M, S, T | Yes | IEEE 802.3bt Class 6, max 51 W (low-power profile on 802.3at Class 4, max 25.5 W); 90 W midspan included except "NM" variant | IP66/IP67, IK10 | None built in | [axis.com](https://www.axis.com/products/axis-q6086-e) | **Recommended**. Official successor of the Q6075-E; 4 MP is a good match for the Haswell host |
| Hikvision | **DS-2DF8C842IXS-AEL(T5)** (BOM) | Outdoor PTZ | n/c (official page not readable) | n/c | n/c | n/c | n/c | n/c | n/c | n/c | [hikvision.com](https://www.hikvision.com/en/products/IP-Products/PTZ-Cameras/Ultra-Series/ds-2df8c842ixs-ael-t5-/) | **Suitable** (BOM). The official page and datasheet are behind an anti-bot challenge and could not be read on 2026-09-30; current status n/c. The project copy of datasheet V5.7.1 (third-party hosted, see `docs/hardware/hikvision-ds-2df8c842ixs-ael/README.md`) gives 8 MP, 42x, ONVIF S/G/T, 802.3bt max 51 W, IR 500 m, IP67: confirm from a browser. 8 MP main stream is likely H.265-heavy: read the H.264 sub stream |
| Dahua | **SD6AL445XA-HNR** (BOM) | Outdoor PTZ (WizMind, laser) | 1/2.8" CMOS, 2560 × 1440 (4 MP) | 45x | H.264 (B/M/H), H.265, MJPEG | S, G, T | Yes | 36 V DC / 2.23 A or Dahua Hi-PoE; max 36 W; IEEE standard/class n/c | IP67 / IK n/c | 550 m (IR + laser) | [dahuasecurity.com](https://www.dahuasecurity.com/products/PTZ-Cameras/WizMind-Series/SD6A65F/4MP/SD6AL445XA-HNR) | **Suitable** if already owned. The product page carries a **Discontinued** tag; successor n/c. Hi-PoE is proprietary: use the Dahua injector or the 36 V supply |
| Hanwha Vision | **XNP-6400RW** | Outdoor PTZ, wiper, gyro stabilization | 1/2.8" CMOS, 1920 × 1080 | 40x | H.264 (Baseline/Main/High), H.265, MJPEG | S, G, T | Yes | IEEE 802.3bt Type 3 Class 6, max 42 W; injector included | IP66, IK10 (product page) | 200 m | [hanwhavision.com](https://www.hanwhavision.com/us/products/product-details/xnp-6400rw) | **Recommended**. 1080p and 150 dB WDR are a good match for a backlit doorway |
| Bosch | **AUTODOME IP starlight 5100i IR** (NDP-5523-Z30L) | Outdoor PTZ, wiper | 1/1.8" CMOS; stream max 2560 × 1440 | 30x | H.264, H.265, M-JPEG | S, G, T | Yes | 24 VAC or IEEE 802.3bt Type 3; max 39.4 W | IP66, IK10 (window and wiper excluded) | 320 m (detection) | [boschsecurity.com](https://commerce.boschsecurity.com/nlexp/en/AUTODOME-IP-starlight-5100i-IR/p/F.01U.359.951/) | **Suitable**. Status n/c; the datasheet read is the pt-BR edition |

### 5.2 Mid-range and entry PTZ

| Vendor | Model | Type | Sensor / resolution | Optical zoom | Codecs (H.264?) | ONVIF profiles | ONVIF PTZ | Power | IP / IK | IR range | Official link | Jarvis verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Axis | **P5655-E** | Outdoor PTZ | 1/2.8" CMOS, 1920 × 1080 | 32x | H.264 (Baseline/Main/High), H.265, MJPEG | G, M, S, T | Yes | IEEE 802.3at Type 2 Class 4, max 19 W; or 20-28 V DC / 20-24 V AC | IP66, IK10 | None built in | [axis.com](https://www.axis.com/products/axis-p5655-e) | **Recommended**. 32x at 1080p on a standard PoE+ switch; add an illuminator for night |
| Axis | **M5526-E** | Indoor/outdoor mini PTZ | 1/3" CMOS, 2688 × 1512 | 10x | H.264 (Baseline/Main/High), H.265, MJPEG | G, M, S, T | Yes | IEEE 802.3af/at Type 1 Class 3, max 12.95 W; or 20-28 V DC | IP66, IK09 | None built in | [axis.com](https://www.axis.com/products/axis-m5526-e) | **Recommended** for short doorways (2-5 m); lowest power of the ONVIF PTZ list |
| Hikvision | **DS-2DE4425IWG-E** | Outdoor PTZ (DarkFighter, AcuSense) | 1/2.8" CMOS, 2560 × 1440 | 25x | H.264, H.264+, H.265, H.265+ (main); H.264/H.265/MJPEG (sub) | S, G, T | Yes | 12 VDC max 18 W, or PoE IEEE 802.3at (class n/c) | IP67 / IK n/c | 100 m | [hikvision datasheet](https://assets.hikvision.com/prd/public/all/doc/m000124062/DS-2DE4425IWG-E_B_Datasheet_20250220_.pdf) | **Recommended**. Specs from the official datasheet dated 2025-02-20 (assets.hikvision.com); product page not readable, status n/c |
| Dahua | **SD49425DB-HNY** | Outdoor PTZ (WizSense) | 1/2.8" CMOS, 2560 × 1440 | 25x | H.264 (B/M/H), H.265, Smart H.264+/H.265+, MJPEG (sub) | S, G, T | Yes | 12 VDC 3 A or PoE+ IEEE 802.3at; max 21 W | IP66 / IK n/c | 100 m | [dahuasecurity.com](https://www.dahuasecurity.com/products/network-products/ptz-cameras/wizsense-series/sd4/sd49425db-hny) | **Recommended**. No discontinuation tag on 2026-09-30 (SD49425XB-HNR, SD49425GB-HNR and SD5A425XA-HNR are tagged discontinued) |
| Uniview | **IPC6424SR-X25-VF-B** | Outdoor PTZ dome (LightHunter) | 1/2.8" CMOS, 2688 × 1520 (sub 1920 × 1080) | 25x | H.264, H.265, Ultra 265, MJPEG | S, G, T | Yes | 12 VDC 3 A or PoE+ IEEE 802.3at; max 21 W | IP67, IK10 | 100 m | [uniview.com](https://www.uniview.com/Products/PTZ_Cameras/Prime_Series/Prime_Series/IPC6424SR-X25-VF-B/) | **Recommended**. 1080p sub stream is a good analysis stream |
| Annke | **CZ425X** | Outdoor PTZ | 1/2.8" CMOS, 2560 × 1440 | 25x | H.264, H.264+, H.265, H.265+ | n/c ("Support ONVIF") | n/c | 12 V DC 3.33 A or PoE IEEE 802.3at; wattage n/c | IP66, IK10 | 50 m | [annke.com](https://www.annke.com/products/cz500-ultra) | **Suitable**. Test ONVIF `ContinuousMove` with zoom before relying on tracking; shown "out of stock" on 2026-09-30 |
| Reolink | **E1 Outdoor PoE** | Outdoor pan/tilt, motorized lens | 1/2.8" CMOS, 3840 × 2160 | 3x (2.8-8 mm) | H.264, H.265 | ONVIF listed, profiles n/c | n/c | PoE IEEE 802.3af or 12 V DC 1 A | IP65 | 12 m | [reolink.com](https://reolink.com/product/e1-outdoor-poe/) | **Suitable**. Reolink lists it as RTSP/ONVIF standalone; ONVIF PTZ not documented per model; auto-tracking is in the Reolink firmware only. 8 MP RTSP main stream is H.265 (Reolink): read the H.264 sub stream |
| Reolink | **TrackMix PoE** | Dual-lens pan/tilt tracker | 1/2.65" + 1/2.8" CMOS, 3840 × 2160 | n/c (6x "hybrid" zoom, two lenses) | H.264, H.265 | n/c (covered by the "PoE cameras" row) | n/c | PoE IEEE 802.3af or 12 V DC 2 A, under 12 W | IP65 | 30 m | [reolink.com](https://reolink.com/product/reolink-trackmix-poe/) | **Suitable** with caveats. Tracking is in-camera; Reolink notes that via ONVIF only channel 1 is added. ONVIF PTZ n/c |
| Reolink | **RLC-823A** | Outdoor PTZ, floodlights | 1/2.8" CMOS, 3840 × 2160 | 5x | H.265 listed on the page (sub stream H.264 per Reolink RTSP article) | n/c | n/c | PoE IEEE 802.3at or 12 V DC 2 A, under 24 W | IP66 | 60 m | [reolink.com](https://reolink.com/product/rlc-823a/) | **Suitable** if already owned. The product page states it is **no longer sold**; no successor named |
| TP-Link | **VIGI C540** | Outdoor pan/tilt | 1/3" CMOS, 2560 × 1440 | None (4 mm fixed) | H.264, H.264+, H.265, H.265+ (main); H.264/H.265 (sub) | S | n/c on the product page (VIGI FAQ lists PTZ control among ONVIF features) | 12 V DC max 14 W, or PoE 802.3af/at Class 4 | IP66 | 30 m | [vigi.com](https://www.vigi.com/uk/business-networking/vigi-network-camera/vigi-c540/) | **Suitable** at short range (no optical zoom: zoom commands have no effect) |
| TP-Link | **VIGI C540V** | Outdoor dual-lens pan/tilt | 1/3" CMOS, 2560 × 1440 | 3x "Mixed Zoom" (two lenses, 4-12 mm, no motorized parts) | H.264, H.264+, H.265, H.265+ | S | n/c | PoE 802.3at Class 4 or 12 V DC; max 14 W | IP66 | 30 m | [vigi.com](https://www.vigi.com/us/business-networking/vigi-network-camera/vigi-c540v/) | **Suitable** with caveats: lens switching is not a continuous ONVIF zoom (n/c) |
| TP-Link | **Tapo C520WS** | Outdoor pan/tilt, Wi-Fi + 10/100 RJ45 | 1/3" CMOS, 2560 × 1440 | None (3.2 mm; 12x digital) | H.264 | S (Tapo FAQ) | Yes per the Tapo FAQ (Profile S includes PTZ); not stated per model | 9 V DC adapter only (no PoE) | IP66 | 29.9 m (IR) | [tapo.com](https://www.tapo.com/us/product/smart-camera/tapo-c520ws/) | **Suitable** at short range, **wired** only. Needs a Tapo camera account; ONVIF port 2020; Tapo says only two of Tapo Care, SD recording and ONVIF/NVR can run at once |

### 5.3 Fixed cameras (no tracking, `ptz.enabled: false`)

| Vendor | Model | Type | Sensor / resolution | Optical zoom | Codecs (H.264?) | ONVIF profiles | ONVIF PTZ | Power | IP / IK | IR range | Official link | Jarvis verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Axis | **M3215-LVE** | Outdoor dome | 1/2.9" CMOS, 1920 × 1080, 101° HFOV | None | H.264 (Baseline/Main/High), H.265, MJPEG | G, M, S, T | No (fixed) | IEEE 802.3af/at Type 1 Class 3, max 10.4 W | IP66, IK10 | 30 m or more | [axis.com](https://www.axis.com/products/axis-m3215-lve) | **Fixed-only**. At 101°, recognition within about 2 m: mount close to the door |
| Axis | **M2036-LE** | Outdoor bullet | 1/2.7" CMOS, 2304 × 1728 (4 MP), 130° HFOV | None | H.264 (Main/High), H.265 | G, M, S, T | No (fixed) | IEEE 802.3af/at Type 1 (Class 3 per page), max 12.95 W | IP66/IP67, IK08 | 20 m or more | [axis.com](https://www.axis.com/products/axis-m2036-le) | **Fixed-only**. Very wide lens: short recognition range |
| Hikvision | **DS-2CD2143G2-I** | Outdoor dome (AcuSense) | 1/3" CMOS, 2688 × 1520 | None (2.8 or 4 mm) | H.264, H.264+, H.265, H.265+ | S, G | No (fixed) | 12 VDC max 5 W or PoE 802.3af Class 3, max 6.5 W | IP67, IK10 | 30 m | [hikvision datasheet](https://assets.hikvision.com/prd/normal/all/doc/sm000058940/DS-2CD2143G2-I_Datasheet_20251021.pdf) | **Fixed-only**. Specs from the official datasheet dated 2025-10-21; product page not readable, status n/c. Prefer the 4 mm lens |
| Dahua | **IPC-HFW2441S-S** | Outdoor bullet (WizSense) | 1/2.9" CMOS, 2688 × 1520 | None | H.264 (B/M/H), H.265, Smart H.264+/H.265+ | S, G, T | No (fixed) | 12 VDC or PoE 802.3af; max 5.1 W (PoE) | IP67 / IK n/c | 30 m | [dahuasecurity.com](https://www.dahuasecurity.com/products/All-Products/Network-Cameras/WizSense-Series/2-Series/4MP/IPC-HFW2441S-S) | **Fixed-only** |
| Uniview | **IPC2124LE-ADF28(40)KM-H** | Outdoor mini bullet | 1/3" CMOS, 2688 × 1520 | None (2.8 or 4 mm) | H.264, H.265, Ultra 265, MJPEG | S, G, T | No (fixed) | 12 VDC or PoE 802.3af, max 5.6 W | IP67 / IK n/c | 50 m | [uniview.com](https://www.uniview.com/Products/Cameras/Easy/IPC2124LE-ADF28(40)KM-H/) | **Fixed-only**. Status n/c |
| Hanwha Vision | **XNO-6080R** | Outdoor bullet, motorized varifocal | 1/2.8" CMOS, 1920 × 1080 | 4.3x (2.8-12 mm, set at installation) | H.264, H.265, MJPEG | S, G, T | No (fixed) | PoE 802.3af Class 3 max 12.95 W, 12 VDC or 24 VAC | IP66/IP67, IK10 | 50 m | [hanwhavision.com](https://www.hanwhavision.com/us/products/product-details/xno-6080r) | **Fixed-only**. The varifocal lens lets you narrow the view on the doorway (section 3) |
| Reolink | **RLC-510A** | Outdoor bullet | 1/2.7" CMOS, 2560 × 1920 (5 MP), 80° HFOV | None (4 mm) | H.264 | Covered by the "RLC Series" RTSP/ONVIF row; profiles n/c | No (fixed) | PoE 802.3af or 12 V DC 1 A, under 12 W | IP67 | 30 m | [reolink.com](https://reolink.com/product/rlc-510a/) | **Fixed-only**. H.264 main stream per Reolink (2/4/5 MP models) |
| Reolink | **RLC-810A** | Outdoor bullet | 1/2.7" CMOS, 3840 × 2160 (8 MP) | None (2.8, 4 or 6 mm) | H.265 on the page; RTSP main H.265 / sub H.264 (Reolink) | Covered by the "RLC Series" row; profiles n/c | No (fixed) | PoE 802.3af or 12 V DC 1 A, under 12 W | IP67 | 30 m | [reolink.com](https://reolink.com/product/rlc-810a/) | **Fixed-only**. The 8 MP main stream is H.265 (CPU decoding); the H.264 sub stream is low resolution |
| TP-Link | **VIGI C340** | Outdoor bullet | 1/3" CMOS, 2560 × 1440 | None (2.8, 4 or 6 mm) | H.264, H.264+, H.265, H.265+ (main); H.264/H.265 (sub) | ONVIF listed; profile n/c on the page (VIGI FAQ: S, T, G) | No (fixed) | PoE 802.3af/at or 12 V DC, max 7.5 W | IP66 | 30 m | [vigi.com](https://www.vigi.com/us/business-networking/vigi-network-camera/vigi-c340/) | **Fixed-only** |

### 5.4 Not suitable

| Vendor | Model / ecosystem | Why | Official link | Jarvis verdict |
|---|---|---|---|---|
| Reolink | **Argus PT** (owner's camera; battery, dual-band Wi-Fi, 5 MP, 355°/140° pan/tilt, no optical zoom, IP64) | Reolink states that battery cameras do not support RTSP/ONVIF standalone; the Argus series "must connect to Reolink Home Hub", and the hub limits a session to 5 minutes | [reolink.com](https://reolink.com/product/argus-pt/), [support article](https://support.reolink.com/articles/900000617826-Which-Reolink-Products-Support-CGI-RTSP-ONVIF/) | **Not suitable** (experimental only through a Home Hub, `ptz.enabled: false`; see `docs/hardware/reolink-argus-pt/README.md`) |
| Ring | Ring cameras and doorbells | No local RTSP/ONVIF documented by the vendor. Ring's ONVIF article covers third-party cameras added to Ring Edge and states that "ONVIF-compatible cameras are not made by Ring" | [ring.com](https://ring.com/support/articles/snp6q/Using-Your-ONVIF-Compatible-Camera-with-Ring-Edge) | **Not suitable** |
| Google | Nest cameras and doorbells | Streams are served through the cloud Device Access API (RTSP only on legacy models, WebRTC on newer ones, 5-minute sessions); no local RTSP/ONVIF documented | [developers.google.com](https://developers.google.com/nest/device-access/traits/device/camera-live-stream) | **Not suitable** |
| Arlo | Arlo cameras | No local RTSP/ONVIF documented by the vendor | [arlo.com](https://www.arlo.com/) | **Not suitable** |
| Blink | Blink cameras | No local RTSP/ONVIF documented by the vendor; the documented local option is Sync Module 2 USB storage | [blinkforhome.com](https://support.blinkforhome.com/en_US/using-your-sync-module/sync-module-2-local-storage-operation) | **Not suitable** |

### 5.5 Shortlist

- **Best recognition range, BOM continuity:** Axis Q6086-E (4 MP) or Q6088-E (4K), the official successors of the Q6075-E and Q6078-E. Plan an 802.3bt source (or the supplied midspan) and an external IR illuminator.
- **Best value PTZ:** Hikvision DS-2DE4425IWG-E, Dahua SD49425DB-HNY or Uniview IPC6424SR-X25-VF-B (4 MP, 25x, PoE+ 802.3at, IR 100 m).
- **Standard PoE+ switch, no heater budget:** Axis P5655-E (32x, max 19 W).
- **Short doorway, low budget:** Axis M5526-E (PTZ) or a fixed 4 MP camera with a 4 mm lens mounted within 2-3 m of the visitor.

## 6. RTSP URL formats by vendor

Only formats documented by the vendors are listed. Credentials are **not** written in `camera.rtsp_url`: Jarvis injects them (section 7).

| Vendor | Main stream | Sub stream | Notes | Official source |
|---|---|---|---|---|
| Axis | `rtsp://<ip>/axis-media/media.amp?videocodec=h264&resolution=1920x1080` | `rtsp://<ip>/axis-media/media.amp?videocodec=h264&resolution=1280x720` | Syntax `rtsp://<servername>/axis-media/media.amp[?<parameter>=<value>[&...]]`. Parameters: `videocodec=h264\|h265\|jpeg\|av1` (default h264 for RTSP), `resolution`, `fps` (0 = max), `camera` (channel, default 1), `h264profile=baseline\|main\|high`, `streamprofile=<name>`. Axis has no fixed "sub stream": the resolution parameter selects it | [VAPIX video streaming](https://developer.axis.com/vapix/network-video/video-streaming/), [VAPIX URL options](https://developer.axis.com/vapix/network-video/parameter-management/image-api/#url-options) |
| Hikvision | `rtsp://<ip>:554/Streaming/Channels/101` | `rtsp://<ip>:554/Streaming/Channels/102` | `<channel><stream>`: 01 = main, 02 = sub (03 = third stream on the ISAPI form `/ISAPI/Streaming/channels/<ID>`, ID = channel × 100 + stream type). Enable ONVIF and add a dedicated ONVIF user for PTZ | [How do I get my RTSP stream](https://supportusa.hikvision.com/support/solutions/articles/17000129064-how-do-i-get-my-rtsp-stream-), [RTSP format example](https://supportusa.hikvision.com/support/solutions/articles/17000129022-do-you-have-an-example-showing-the-format-for-getting-a-rtsp-stream-from-a-camera-), [ISAPI guide](http://enpinfo.hikvision.com/unzip/20201110210551_77443_doc/GUID-515FF2B5-5E01-4F03-8B81-4CA5BD621965.html) |
| Dahua | `rtsp://<ip>:554/cam/realmonitor?channel=1&subtype=0` | `rtsp://<ip>:554/cam/realmonitor?channel=1&subtype=1` | Default port 554; channel starts at 1; subtype 0 = main, 1 = sub | [Dahua Network Camera Web 5.0 Operation Manual V1.0.6](https://material.dahuasecurity.com/uploads/cpq/DOR/PUM0001826/Dahua-Network-Camera-Web-5.0_Operation-Manual_V1.0.6.pdf) |
| Amcrest | n/c | n/c | Amcrest documents RTSP in its support center, but the page could not be read on 2026-09-30 (anti-bot challenge). Many Amcrest models use the Dahua `/cam/realmonitor` path: confirm the `subtype` mapping with `ffprobe` on the device | [Accessing Amcrest Products Using RTSP](https://support.amcrest.com/hc/en-us/articles/360052688931-Accessing-Amcrest-Products-Using-RTSP) |
| Reolink | `rtsp://<ip>:554/Preview_01_main` | `rtsp://<ip>:554/Preview_01_sub` | Channel starts at 01. RTSP/ONVIF may be disabled by default (Settings > Network > Advanced > Server Settings); ONVIF port 8000. 8 MP cameras: main H.265, sub H.264; 2/4/5 MP cameras: H.264 | [Introduction to RTSP](https://support.reolink.com/articles/900000630706-Introduction-to-RTSP/), [RTSP video/audio format](https://support.reolink.com/hc/en-us/articles/900000638523-What-s-the-Format-of-the-RTSP-Video-Audio-that-Reolink-Cameras-Use/), [Introduction to ONVIF](https://support.reolink.com/hc/en-us/articles/360008718893-Introduction-to-ONVIF-Protocol/) |
| TP-Link VIGI | `rtsp://<ip>:554/stream1` | `rtsp://<ip>:554/stream2` | ONVIF Profile S/T/G, ONVIF port 80 (2020 on older firmware) | [VIGI third-party integration FAQ](https://www.tp-link.com/us/support/faq/4201/), [VIGI RTSP FAQ](https://www.tp-link.com/us/support/faq/3718/) |
| TP-Link Tapo | `rtsp://<ip>:554/stream1` | `rtsp://<ip>:554/stream2` | Requires a **camera account** created in the Tapo app (not the TP-Link ID); ONVIF Profile S on port 2020; battery models generally not supported | [Tapo RTSP/ONVIF FAQ](https://www.tp-link.com/us/support/faq/2680/), [Tapo ONVIF/RTSP common questions](https://www.tp-link.com/us/support/faq/4465/) |
| Uniview | `rtsp://<ip>:554/media/video1` | `rtsp://<ip>:554/media/video2` | Third stream `/media/video3`; `:554` is optional; `/unicast/c<N>/s0/live` is the **NVR** form | [How to Get a Uniview Camera's RTSP Stream (V1.1)](https://global.uniview.com/res/202310/26/20231026_1890310_How%20to%20Get%20a%20Uniview%20Camera's%20RTSP%20Stream_974039_168459_0.pdf), [How to Get the URLs for Uniview IPC and NVR](https://global.uniview.com/res/202310/26/20231026_1890323_How%20to%20Get%20the%20URLs%20for%20Uniview%20IPC%20and%20NVR_975509_168459_0.pdf) |
| Hanwha Vision | `rtsp://<ip>:554/profile2/media.smp` | `rtsp://<ip>:554/profile<N>/media.smp` (the profile you configured with H.264 at a lower resolution) | Default profiles: profile1 = MJPEG, profile2 = H.264; default port 554. The support article returned HTTP 403 to automated access: the format was taken from the vendor's indexed article text, confirm it in a browser | [Camera - RTSP URL](https://support.hanwhavision.com/hc/en-001/articles/47782445700243-Camera-RTSP-URL) |

### 6.1 Testing a stream

From the mini-PC (camera network), check that the stream opens over TCP and report its codec, size and frame rate:

```bash
ffprobe -hide_banner -rtsp_transport tcp -timeout 5000000 \
  -show_entries stream=codec_name,profile,width,height,avg_frame_rate -of compact \
  "rtsp://jarvis:PASSWORD@192.168.50.64:554/Streaming/Channels/102"
```

- `-timeout` is in microseconds on recent FFmpeg releases (older builds use `-stimeout`); 5 s matches Jarvis.
- Expected: `codec_name=h264`, and the resolution and frame rate you configured.
- Check that the GPU decodes H.264: `vainfo | grep -i h264` (VAProfileH264... entries).
- Quote the URL: `&` in Dahua URLs is a shell operator. URL-encode special characters of the password (`@` = `%40`, `:` = `%3A`), or prefer an alphanumeric password.

### 6.2 Selecting H.264 and the sub stream

- **Axis:** add `videocodec=h264&resolution=1280x720` (or 1920x1080) and optionally `fps=15`, or create a stream profile in the web interface and use `streamprofile=<name>`.
- **Hikvision / Dahua / Uniview / Hanwha / VIGI:** in the web interface, *Video / Audio > Video* (labels vary), set the **Sub Stream** (or third stream) to **H.264**, 1280 × 720 or 1920 × 1080, 10-15 fps, and **disable** H.264+/H.265+ "smart" codecs on that stream if the image stutters (they lengthen the GOP).
- **Reolink:** 8 MP models serve H.265 on the main stream; use `Preview_01_sub` (H.264) or a 4-5 MP model.
- With a **PTZ**, read the sub stream: the zoom keeps the face large (section 3.4). With a **fixed** camera, read the main stream in H.264 if the sub stream gives fewer than 80 px on the face.

## 7. Jarvis configuration mapping

| Setting | Default | Meaning |
|---|---|---|
| `camera.rtsp_url` | `rtsp://192.168.50.64:554/stream2` | Stream URL **without credentials** (section 6) |
| `camera.hw_accel` | `auto` | VAAPI decoding; `auto` enables it when `vainfo` reports H.264 decode; set `false` for an H.265 stream |
| `camera.reconnect_delay_s` | `2.0` | Initial reconnection delay (exponential backoff) |
| `secrets.camera_username` / `secrets.camera_password` | empty | Camera account; injected into the RTSP URL and used for ONVIF |
| `ptz.enabled` | `true` | `false` for a fixed camera (`NullPTZ`, no tracking) |
| `ptz.host` / `ptz.port` | `192.168.50.64` / `80` | ONVIF endpoint (port 8000 on Reolink, 2020 on Tapo, 80 on VIGI) |
| `ptz.username` / `ptz.password` | empty | Empty = taken from `secrets.camera_username` (or `admin`) / `secrets.camera_password` |
| `ptz.profile_index` | `0` | Index of the ONVIF media profile carrying the PTZ configuration |
| `ptz.home_preset` | `"1"` | ONVIF preset token of the entrance view; `null` disables the return home |
| `ptz.sound_preset` | `null` | Optional preset for the sound trigger (`audio.sound_trigger_rms`) |
| `ptz.zoom_enabled` / `ptz.target_height_ratio` | `true` / `0.55` | Zoom toward the target; set `zoom_enabled: false` on a pan/tilt camera without optical zoom |

**Credentials.** `rtsp_url_with_credentials()` in `jarvis/config/settings.py` returns `camera.rtsp_url` unchanged when the URL already contains `@` or when no secret is set; otherwise it inserts `secrets.camera_username:secrets.camera_password@`, both URL-encoded with `quote(..., safe='')`. The same function prepares the URL passed to `RtspCamera`, so the credentials never appear in a settings dump. The PTZ credentials fall back to the same secrets.

Example `/etc/jarvis/config.yaml` for a Hikvision PTZ on the camera network:

```yaml
secrets:
  camera_username: ${CAMERA_USER:-}
  camera_password: ${CAMERA_PASSWORD:-}

camera:
  rtsp_url: "rtsp://192.168.50.64:554/Streaming/Channels/102"   # H.264 sub stream
  hw_accel: auto

ptz:
  enabled: true
  host: 192.168.50.64
  port: 80
  profile_index: 0
  home_preset: "1"
```

Fixed camera: keep the `camera` section and set `ptz.enabled: false`.

**Ansible** (`deploy/ansible/group_vars/all/main.yml`):

- `jarvis_camera_host`: optional camera address; the preflight role probes its **RTSP port 554** and only prints a warning when it is unreachable (`deploy/ansible/roles/preflight/tasks/main.yml`).
- `jarvis_env`: non-secret settings written to `/etc/jarvis/jarvis.env`, e.g. `JARVIS__CAMERA__RTSP_URL`, `JARVIS__PTZ__HOST`, `JARVIS__PTZ__ENABLED`.
- `jarvis_secrets`: secrets written to `/etc/jarvis/jarvis.env`, defined in the encrypted `vault.yml`, e.g. `JARVIS__SECRETS__CAMERA_USERNAME`, `JARVIS__SECRETS__CAMERA_PASSWORD`.

```yaml
# group_vars/all/main.yml (or inventory host vars)
jarvis_camera_host: "192.168.50.64"
jarvis_env:
  JARVIS__CAMERA__RTSP_URL: "rtsp://192.168.50.64:554/Streaming/Channels/102"
  JARVIS__PTZ__HOST: "192.168.50.64"

# group_vars/all/vault.yml (ansible-vault encrypt)
jarvis_secrets:
  JARVIS__SECRETS__CAMERA_USERNAME: "jarvis"
  JARVIS__SECRETS__CAMERA_PASSWORD: "long-random-password"
```

The secrets can also be set from the write-only secrets page of the web UI.

## 8. Commissioning checklist and troubleshooting

### 8.1 Pre-purchase checklist

- [ ] Official product page and datasheet read on the day of the order; product not tagged discontinued (or successor identified).
- [ ] Datasheet lists **H.264** and **ONVIF Profile S or T**; for a PTZ, PTZ over ONVIF confirmed (vendor statement or a test with ONVIF Device Manager on a demo unit).
- [ ] Pixels on the face computed at your real distance with the lens HFOV (section 3): ≥ 80 px, ideally ≥ 112 px.
- [ ] **Maximum** power and PoE type known; switch or injector sized for it (802.3bt / vendor High PoE for outdoor PTZ with heater), cable run under 100 m.
- [ ] IP66/IP67, IK10 if within reach; operating temperature for your climate; IR range or external illuminator.
- [ ] RTSP works **without** vendor cloud, hub or app.

### 8.2 Commissioning checklist

1. Connect the camera to the camera network (192.168.50.0/24), give it a static address (for example 192.168.50.64) and update its firmware from the vendor site.
2. Change the default password; create a **dedicated user** for Jarvis (on Hikvision, enable ONVIF and add an ONVIF user); disable cloud, P2P and UPnP.
3. Enable RTSP and ONVIF (some vendors disable them by default); set the time server (NTP).
4. Configure the analysis stream: **H.264**, 720p or 1080p, 10-15 fps (section 6.2).
5. For a PTZ: store preset **1** on the entrance view (it must match `ptz.home_preset`), and optionally a sound preset.
6. Test with `ffprobe -rtsp_transport tcp` (section 6.1) and, for PTZ, with an ONVIF client (move, zoom, stop, go to preset).
7. Set `camera.rtsp_url`, `ptz.*` and the secrets (section 7), restart Jarvis and check the logs for `RTSP stream connected` and `ONVIF PTZ connected`.
8. Walk to the door by day and by night and check the live preview: the face must be sharp and at least 80 px wide at the recognition point.

### 8.3 Troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| Log `RTSP stream unavailable, retrying` | Wrong URL, RTSP disabled, wrong network or VLAN | Test with `ffprobe`; check the path in section 6; check that the mini-PC has an address on 192.168.50.0/24 |
| `401 Unauthorized` | Wrong credentials, credentials in the URL twice, special characters | Put the credentials only in `secrets.*`; use an alphanumeric password |
| Stream drops every few seconds | 5 s socket timeout reached (camera stalls, Wi-Fi, overloaded PoE) | Use a cable; check the PoE budget; the timeout can be raised with `OPENCV_FFMPEG_CAPTURE_OPTIONS` in the environment |
| High CPU load, low analysis rate | H.265 stream or 4K/8 MP decoded by the CPU | Switch the analysis stream to H.264 720p/1080p; check `vainfo`; keep `camera.hw_accel: auto` |
| Smeared or gray frames | Packet loss (UDP) or smart codec GOP | Jarvis already forces TCP; disable H.264+/H.265+ on the analysis stream |
| Faces detected but rarely recognized | Too few pixels on the face, backlight, IR at night | Section 3; enable WDR; move the home preset closer or zoom in; enroll more photos |
| Log `ONVIF connection failed` | ONVIF disabled, wrong port, no ONVIF user | Enable ONVIF, create the ONVIF user, set `ptz.port` (80, 8000, 2020...) |
| Camera moves but never zooms | No optical zoom, or zoom not exposed over ONVIF | Set `ptz.zoom_enabled: false` |
| Camera does not return to the entrance view | Preset token differs from `ptz.home_preset` | Read the preset tokens with an ONVIF client; set `ptz.home_preset` to the right token |
| Wrong media profile (no PTZ) | `ptz.profile_index` points to a profile without a PTZ configuration | Try `profile_index: 1`, or check the profiles with an ONVIF client |
| Tracking oscillates | RTSP latency | Lower `ptz.max_speed` and the pan/tilt gains; use the sub stream (lower latency) |
| Camera reboots when IR or heater turns on | PoE budget too small | Use 802.3bt / the vendor injector, or the DC/AC supply |


## 8b. License plate reading (optional)

Plate recognition (`plates.enabled`, see `docs/SOFTWARE.md` §17) reads the plates of the vehicles
seen by the same stream. Its needs differ from face recognition:

| Requirement | Value | Why |
|---|---|---|
| Plate width in the image | ≥ 100–130 px at the reading distance | the OCR needs about 10 px per character |
| Viewing angle to the plate | ≤ 30° horizontal and vertical | perspective distorts the characters |
| Shutter / motion | short exposure (≤ 1/500 s) for moving vehicles | motion blur kills the read |
| Night | IR illumination; plates are retro-reflective | headlights otherwise blind the sensor |
| Stream | H.264, ≥ 1080p, the same as for faces | one stream feeds both analyses |

A camera placed and zoomed for faces at the door is often too high, or aimed too steeply, for
plates. Typical options:
- **PTZ (single camera):** a PTZ whose home preset frames the driveway.
- **Two cameras (not wired in yet):** a second, fixed "ANPR-style" camera aimed along the
  driveway at plate height. Jarvis reads one stream today, so this would need a second Jarvis
  camera source, a possible evolution.

## 9. Sources

All links were checked on **2026-09-30**. "Not readable" means the page exists but blocked automated access on that date.

**Axis**
- AXIS Q6078-E: https://www.axis.com/products/axis-q6078-e, datasheet https://www.axis.com/dam/public/e1/f6/c8/datasheet-axis-q6078-e-ptz-camera-en-US-555514.pdf
- AXIS Q6088-E: https://www.axis.com/products/axis-q6088-e, datasheet https://www.axis.com/dam/public/c9/39/a9/datasheet-axis-q6088-e-ptz-camera-en-US-555448.pdf
- AXIS Q6075-E: https://www.axis.com/products/axis-q6075-e, datasheet https://www.axis.com/dam/public/ea/af/5a/datasheet-axis-q6075-e-ptz-network-camera-en-US-506806.pdf, discontinuation statement https://www.axis.com/dam/public/permalink/258039/product-discontinuation-statement-axis-q6075,-axis-q6075-epdf-en-US_258039.pdf, installation guide https://www.axis.com/dam/public/9d/b3/fb/axis-q60-e-series--installation-guide-en-US-476747.pdf
- AXIS Q6086-E: https://www.axis.com/products/axis-q6086-e, datasheet https://www.axis.com/dam/public/36/91/a2/datasheet-axis-q6086-e-ptz-camera-en-US-555447.pdf
- AXIS P5655-E: https://www.axis.com/products/axis-p5655-e, datasheet https://www.axis.com/dam/public/5a/02/07/datasheet-axis-p5655%E2%80%93e-ptz-network-camera-en-US-555535.pdf
- AXIS M5526-E: https://www.axis.com/products/axis-m5526-e, datasheet https://www.axis.com/dam/public/e6/db/d3/datasheet-axis-m5526-e-ptz-camera-en-US-555377.pdf
- AXIS M3215-LVE: https://www.axis.com/products/axis-m3215-lve, datasheet https://www.axis.com/dam/public/69/3c/f0/datasheet-axis-m3215-lve-dome-camera-en-US-506716.pdf
- AXIS M2036-LE: https://www.axis.com/products/axis-m2036-le, datasheet https://www.axis.com/dam/public/bc/50/1b/datasheet-axis-m2036-le-bullet-camera-en-US-555403.pdf
- VAPIX RTSP: https://developer.axis.com/vapix/network-video/video-streaming/, URL options https://developer.axis.com/vapix/network-video/parameter-management/image-api/#url-options

**Hikvision**
- DS-2DF8C842IXS-AEL(T5): https://www.hikvision.com/en/products/IP-Products/PTZ-Cameras/Ultra-Series/ds-2df8c842ixs-ael-t5-/ (not readable)
- DS-2DE4425IWG-E datasheet (2025-02-20): https://assets.hikvision.com/prd/public/all/doc/m000124062/DS-2DE4425IWG-E_B_Datasheet_20250220_.pdf
- DS-2CD2143G2-I datasheet (2025-10-21): https://assets.hikvision.com/prd/normal/all/doc/sm000058940/DS-2CD2143G2-I_Datasheet_20251021.pdf
- RTSP: https://supportusa.hikvision.com/support/solutions/articles/17000129064-how-do-i-get-my-rtsp-stream-, https://supportusa.hikvision.com/support/solutions/articles/17000129022-do-you-have-an-example-showing-the-format-for-getting-a-rtsp-stream-from-a-camera-, http://enpinfo.hikvision.com/unzip/20201110210551_77443_doc/GUID-515FF2B5-5E01-4F03-8B81-4CA5BD621965.html

**Dahua**
- SD6AL445XA-HNR: https://www.dahuasecurity.com/products/PTZ-Cameras/WizMind-Series/SD6A65F/4MP/SD6AL445XA-HNR
- SD49425DB-HNY: https://www.dahuasecurity.com/products/network-products/ptz-cameras/wizsense-series/sd4/sd49425db-hny
- IPC-HFW2441S-S: https://www.dahuasecurity.com/products/All-Products/Network-Cameras/WizSense-Series/2-Series/4MP/IPC-HFW2441S-S
- RTSP (Web 5.0 manual): https://material.dahuasecurity.com/uploads/cpq/DOR/PUM0001826/Dahua-Network-Camera-Web-5.0_Operation-Manual_V1.0.6.pdf

**Amcrest**
- RTSP: https://support.amcrest.com/hc/en-us/articles/360052688931-Accessing-Amcrest-Products-Using-RTSP (not readable)

**Uniview**
- IPC6424SR-X25-VF-B: https://www.uniview.com/Products/PTZ_Cameras/Prime_Series/Prime_Series/IPC6424SR-X25-VF-B/, datasheet https://global.uniview.com/res/202212/21/20221221_1864433_UNV%20%E3%80%90Datasheet%E3%80%91%20IPC6424SR-X25-VF-B%204MP%2025x%20LightHunter%20Network%20PTZ%20Dome%20Camera%20Datasheet%20V1.0-EN_958453_168459_0.pdf
- IPC2124LE-ADF28(40)KM-H: https://www.uniview.com/Products/Cameras/Easy/IPC2124LE-ADF28(40)KM-H/, datasheet https://global.uniview.com/vn/res/202406/04/20240604_1903139_UNV%20IPC2124LE-ADF28(40)KM-H%204MP%20HD%20Mini%20IR%20Fixed%20Bullet%20Network%20Camera_992163_168459_0.pdf
- RTSP: https://global.uniview.com/res/202310/26/20231026_1890310_How%20to%20Get%20a%20Uniview%20Camera's%20RTSP%20Stream_974039_168459_0.pdf, https://global.uniview.com/res/202310/26/20231026_1890323_How%20to%20Get%20the%20URLs%20for%20Uniview%20IPC%20and%20NVR_975509_168459_0.pdf

**Hanwha Vision**
- XNP-6400RW: https://www.hanwhavision.com/us/products/product-details/xnp-6400rw (datasheet linked from the page, hosted on the vendor's Azure storage: https://hvsgmpprdstorage.blob.core.windows.net/pim/XNP-6400RW/DataSheet_XNP-6400RW_20260921_EN_132717.pdf)
- XNO-6080R: https://www.hanwhavision.com/us/products/product-details/xno-6080r
- RTSP: https://support.hanwhavision.com/hc/en-001/articles/47782445700243-Camera-RTSP-URL (not readable)

**Bosch**
- AUTODOME IP starlight 5100i IR: https://commerce.boschsecurity.com/nlexp/en/AUTODOME-IP-starlight-5100i-IR/p/F.01U.359.951/, datasheet NDP-5523-Z30L (pt-BR, V8) https://cdn.commerce.boschsecurity.com/public/documents/NDP_5523_Z30L_Data_sheet_ptBR_83037267083.pdf

**Annke**
- CZ425X: https://www.annke.com/products/cz500-ultra

**Reolink**
- E1 Outdoor PoE: https://reolink.com/product/e1-outdoor-poe/
- TrackMix PoE: https://reolink.com/product/reolink-trackmix-poe/
- RLC-823A: https://reolink.com/product/rlc-823a/
- RLC-510A: https://reolink.com/product/rlc-510a/
- RLC-810A: https://reolink.com/product/rlc-810a/
- Argus PT: https://reolink.com/product/argus-pt/
- CGI/RTSP/ONVIF support: https://support.reolink.com/articles/900000617826-Which-Reolink-Products-Support-CGI-RTSP-ONVIF/
- RTSP: https://support.reolink.com/articles/900000630706-Introduction-to-RTSP/, codec https://support.reolink.com/hc/en-us/articles/900000638523-What-s-the-Format-of-the-RTSP-Video-Audio-that-Reolink-Cameras-Use/
- ONVIF: https://support.reolink.com/hc/en-us/articles/360008718893-Introduction-to-ONVIF-Protocol/
- Battery cameras and third-party software: https://support.reolink.com/articles/360004441753-Can-Reolink-Battery-Powered-Cameras-Work-with-3rd-Party-Software/
- TrackMix via ONVIF (Synology): https://support.reolink.com/articles/360004124293-How-to-Add-Reolink-Cameras-to-Synology-Surveillance-Station/

**TP-Link (VIGI and Tapo)**
- VIGI C540: https://www.vigi.com/uk/business-networking/vigi-network-camera/vigi-c540/
- VIGI C540V: https://www.vigi.com/us/business-networking/vigi-network-camera/vigi-c540v/
- VIGI C340: https://www.vigi.com/us/business-networking/vigi-network-camera/vigi-c340/
- Tapo C520WS: https://www.tapo.com/us/product/smart-camera/tapo-c520ws/
- VIGI FAQ: https://www.tp-link.com/us/support/faq/4201/, https://www.tp-link.com/us/support/faq/3718/
- Tapo FAQ: https://www.tp-link.com/us/support/faq/2680/, https://www.tp-link.com/us/support/faq/4465/

**Cloud ecosystems**
- Ring: https://ring.com/support/articles/snp6q/Using-Your-ONVIF-Compatible-Camera-with-Ring-Edge
- Google Nest: https://developers.google.com/nest/device-access/traits/device/camera-live-stream
- Arlo: https://www.arlo.com/
- Blink: https://support.blinkforhome.com/en_US/using-your-sync-module/sync-module-2-local-storage-operation

**Project files**
- `jarvis/vision/camera.py`, `jarvis/vision/ptz.py`, `jarvis/config/settings.py`, `jarvis/core/sysinfo.py`, `config/config.example.yaml`, `deploy/ansible/group_vars/all/main.yml`, `deploy/ansible/roles/preflight/tasks/main.yml`, `REQUIREMENTS.md`, `docs/hardware/README.md` and the camera folders in `docs/hardware/`.
