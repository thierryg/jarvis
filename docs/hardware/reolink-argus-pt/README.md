<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/hardware/reolink-argus-pt/README.md
Purpose : Reolink Argus PT (5 MP battery Wi-Fi pan/tilt camera): documents, suitability, integration
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Reolink Argus PT (5 MP, dual-band Wi-Fi): official documentation

The owner's camera is sold on Amazon under ASIN **B09PG7FMKH** (https://www.amazon.fr/dp/B09PG7FMKH): "Reolink 5MP Camera Exterieur sans Fil Solaire, 360° Suivi Auto, **Argus PT**" with the 3 W solar panel. Reolink's support site names this revision **"Argus PT (5/2.4GHz)"**, to tell it apart from the older **Argus PT 2K** (2560x1440, 4 MP, 6000 mAh battery) and from the 4K **Argus PT Ultra** (https://support.reolink.com/c/argus-pt-2k-argus-pt-5-2-4-ghz/).

The documents were downloaded on 2026-09-30 from Reolink's official sites and CDNs (reolink.com, home-cdn.reolink.us, m.reolink.com, and the Reolink bucket `reolink-storage` linked from the reolink.com product page). Each file was checked as a valid PDF with `file`.

| File | Contents | Version / date | Size | Source |
|---|---|---|---|---|
| `argus-pt_specifications_fr.pdf` | Specification sheet (FR), 5 MP model, 4 p. | Linked from the current reolink.com product page | 353 088 B | https://reolink-storage.s3.us-east-1.amazonaws.com/website/shop/public/specs/argus-pt-pro-1784725239764.pdf (linked from https://reolink.com/product/argus-pt/) |
| `argus-pt_quick_start_guide_2025_multi.pdf` | "Reolink Argus PT Operational Instructions", EN/DE/FR/IT/ES, item no. B430, ref. 58.03.008.0375, 13 p. | PDF created 2025-09-18 | 2 114 147 B | https://m.reolink.com/files/docs/specs/QSG/reolink-argus-pt-new/EN-Reolink-Argus-PT-New.pdf |
| `argus-pt_argus-pt-pro_quick_start_guide_2022-04_multi.pdf` | "Reolink Argus PT / Argus PT Pro Operational Instruction", EN/DE/FR/IT/ES, 39 p. | April 2022 | 5 811 947 B | https://home-cdn.reolink.us/wp-content/uploads/2022/04/070142401649295760.7602.pdf |
| `battery-camera_user_manual_2021-09_en.pdf` | "Reolink Wireless Battery-powered Camera User Manual" (Argus family, includes the Argus PT pan/tilt sections), 115 p. | Sept 2021 (QSG1_A) | 8 564 135 B | https://home-cdn.reolink.us/wp-content/assets/multiple-languages/manual/Reolink_Wireless_Battery_powered_Camera_User_Manual.pdf |

Notes:
- The only static specification PDF linked from reolink.com is in **French** (the file name says `argus-pt-pro`, but its content is the 5 MP Argus PT). The English specifications are on the web page https://reolink.com/us/product/argus-pt/ (no PDF link found there).
- The user manual is generic for the whole Argus battery-camera family (2021). It does not document RTSP or ONVIF, because the camera does not support them on its own (see below).
- The 2025 quick start guide mentions "Item No. B430"; it was not possible to confirm that B430 is exactly the Amazon revision. Both guides are kept.

## Key specifications

Sources: specification sheet (FR PDF), https://reolink.com/us/product/argus-pt/, https://reolink.com/product/argus-pt/.

| Item | Value |
|---|---|
| Type | **Battery-powered Wi-Fi pan/tilt camera** (not PoE, no RJ45), outdoor IP64 |
| Sensor / resolution | 1/2.7" CMOS, **2880x1616** (US page) / 2880x1620 (FR sheet), 5 MP **@ 15 fps max.** |
| Frame rate | Main stream 10-15 fps (default 15), sub stream 10-15 fps (default 15) |
| Bit rate | Main 1024-4096 kbit/s (default 3072), sub 64-672 kbit/s (default 672) |
| Codec (recording) | H.265 (per the sheet) |
| Lens / FOV | Fixed lens, **90° horizontal**, 47° vertical, 110° diagonal. **No optical zoom** in the specifications |
| Pan / tilt | **355° pan, 140° tilt**, 32 presets, built-in auto-tracking (Reolink app only) |
| Night vision | IR up to 10 m (2 x 850 nm LEDs, IR-cut filter), color night vision with 2 x 6500 K spotlights |
| Audio | Two-way audio, built-in microphone and speaker; siren |
| Detection | PIR (up to 10 m, 120° horizontal), person / vehicle / pet detection |
| Wi-Fi | IEEE 802.11 a/b/g/n, **dual band 2.4 GHz / 5 GHz**, WPA/WPA2 |
| Power | **21.6 Wh rechargeable battery**, USB-C charging port, 3 W Reolink solar panel. No PoE, no 12 V DC input |
| Storage | microSD up to 128 GB (FAT32), optional Reolink Cloud |
| Protocols (sheet) | SSL, TCP/IP, UDP, UPnP, SMTP, NTP, **P2P**. **No RTSP, no ONVIF, no HTTP API** listed |
| Environment / size | -10 °C to +55 °C, 20-85 % RH, Ø98 x 122 mm, 477 g with battery |

### RTSP / ONVIF support: the decisive point

- Reolink states that its battery-powered cameras, when used standalone, **do not support RTSP, RTMP, ONVIF** and the related protocols used by third-party software (https://support.reolink.com/articles/360004441753-Can-Reolink-Battery-Powered-Cameras-Work-with-3rd-Party-Software/).
- The compatibility list marks the whole Argus series with **"Must connect to Reolink Home Hub"** for CGI/RTSP/ONVIF (https://support.reolink.com/articles/900000617826-Which-Reolink-Products-Support-CGI-RTSP-ONVIF/).
- The **Argus PT** is listed as compatible with the **Reolink Home Hub series**. Some hardware versions need a firmware upgrade first ("Released, upgradable"), others are "Factory-supported" (https://support.reolink.com/articles/32379509281561-Reolink-Home-Hub-Compatibility/).
- Through a Home Hub, the stream of a battery camera is served **by the Hub**, with two hard limits (https://support.reolink.com/articles/900000630706-Introduction-to-RTSP/):
  - each preview session lasts **at most 5 minutes**, then the camera goes back to sleep and the RTSP connection is closed;
  - the camera must wake up for each request, so Reolink recommends an RTSP request timeout of **at least 20 s**.
- On Reolink devices, RTSP/ONVIF/HTTP/RTMP are **disabled by default on some models** and must be enabled first. For a camera behind a Home Hub, the ports are set **on the Home Hub**, not on the camera (https://support.reolink.com/articles/900000621783-How-to-Configure-Reolink-Ports-Settings/).

## Relevance to Jarvis

**Verdict: not suitable as the Jarvis camera on its own. Usable only as a degraded, experimental source through a Reolink Home Hub (extra purchase), with PTZ disabled in Jarvis.** A wired (PoE) or mains-powered Wi-Fi Reolink camera that supports RTSP/ONVIF standalone, or one of the ONVIF PTZ cameras already documented here (Axis, Hikvision, Dahua), is the right choice.

Reasons:

1. **No stream without a Home Hub.** Jarvis reads `camera.rtsp_url` with OpenCV/FFmpeg. The Argus PT alone exposes no RTSP URL, so Jarvis cannot use it at all. Buying a Home Hub (or Home Hub Pro/Mini) is mandatory.
2. **Jarvis needs a continuous stream; the camera is designed to sleep.** Jarvis decodes the stream 24/7 to run YOLO and InsightFace. Through the Hub, the session is cut every 5 minutes and each reconnection needs a wake-up of up to ~20 s. The Jarvis reader (`jarvis/vision/camera.py`) sets `OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|stimeout;5000000` (5 s socket timeout, set with `setdefault`, so it can be overridden in the environment) and reconnects with exponential backoff: it recovers, but there are regular blind gaps, precisely when a visitor arrives.
3. **Battery life.** Continuous streaming keeps the radio and the encoder on permanently. A 21.6 Wh battery and a 3 W panel are sized for motion-triggered clips, not for 24/7 streaming (Reolink quotes 1-4 weeks per charge in normal event-driven use). Permanent USB-C power was not verified as a supported operating mode by Reolink.
4. **Pan/tilt is not usable by Jarvis.** Jarvis drives PTZ through ONVIF (`jarvis/vision/ptz.py`, `OnvifPTZ`). The camera has no ONVIF on its own; whether the Home Hub exposes ONVIF PTZ for a battery camera is **not documented** by Reolink. The built-in auto-tracking only works in the Reolink ecosystem. Set `ptz.enabled: false`: `build_ptz()` then returns `NullPTZ`, a no-op driver, and the tracker is disabled (`self.enabled = not isinstance(ptz, NullPTZ)`), so Jarvis works as with a fixed camera (detection and recognition only, no subject tracking, no home preset).
5. **Pixels on the face.** Face recognition needs about **80-112 px of face width** (the code accepts faces from `faces.min_face_px: 40`, but recognition quality drops sharply below ~80 px). With 2880 px over 90° horizontal, the scene width at distance *d* is about 2·*d*, and a 16 cm wide face spans about **230 / d px** on the main stream:

   | Distance | Main stream (2880 px) | Sub stream (640 px, assumed) |
   |---|---|---|
   | 1.0 m | ~230 px | ~51 px |
   | 2.0 m | ~115 px | ~26 px |
   | 3.0 m | ~77 px | ~17 px |
   | 5.0 m | ~46 px | ~10 px |

   Recognition therefore works only within about **2-3 m on the main stream**, and there is no optical zoom to compensate. The sub stream resolution of this model is not documented (640x360 is typical for Reolink battery cameras: check with `ffprobe`); if confirmed, it is **too small for face recognition** and only fits person detection.
6. **Wi-Fi.** Wi-Fi adds jitter, packet loss and latency compared with a cable (the PTZ comment in `config/config.example.yaml` already assumes ~0.5 s of RTSP latency on a wired camera). Prefer 5 GHz with a strong signal (RSSI better than about -65 dBm) close to the access point; 2.4 GHz at the gate/door is often congested.
7. **Codec.** The Intel HD 4600 (Haswell) VAAPI decodes **H.264 but not H.265/HEVC**. Reolink states that the RTSP video of its 2, 4 and 5 MP cameras is H.264 (https://support.reolink.com/hc/en-us/articles/900000638523-What-s-the-Format-of-the-RTSP-Video-Audio-that-Reolink-Cameras-Use/), while the Argus PT sheet lists H.265 for recording. Check the codec actually served by the Hub with `ffprobe`. If it is H.265, set `camera.hw_accel: false` (CPU decoding of 5 MP at 15 fps is heavy on this CPU) or do not use this camera.

## Integration guide (camera and Home Hub on the same LAN as Jarvis)

This guide assumes a Reolink **Home Hub** on the same LAN as the Jarvis mini-PC. Without a Hub, stop here: the camera cannot be integrated.

### 1. Initial setup and firmware

1. Charge the camera fully over USB-C, insert a microSD card if wanted, switch it on.
2. Connect the Home Hub to the LAN with its Ethernet cable and add it in the **Reolink app** (or Reolink Client on a PC). Set a **strong admin password** on the Hub (Reolink advises against special characters in passwords used in RTSP URLs: https://support.reolink.com/articles/360007010473-How-to-Live-View-Reolink-Cameras-via-VLC-Media-Player/; prefer a long alphanumeric password).
3. **Update the camera firmware first** (required for the hardware versions marked "Released, upgradable"): see https://support.reolink.com/articles/6735628255001-Where-to-Find-Firmware-for-Battery-powered-Cameras/. Then update the Home Hub firmware.
4. Add the Argus PT to the Home Hub in the app, on the 5 GHz band if the signal allows it.
5. **Dedicated user for Jarvis**: in the Hub settings, create a user (for example `jarvis`) with the lowest privilege level available (view only). Whether the Home Hub user system offers a view-only role on your firmware was not verified; if not, use a separate account rather than `admin`.

### 2. Enable RTSP (and ONVIF) on the Home Hub

1. Reolink app: **Home Hub > Settings > Network > Advanced (Advanced Network Settings) > Server Settings**, then enable **RTSP** (default port 554). Enable **ONVIF** (default port 8000) only if you want to test PTZ (see step 7). Reolink Web Client / Reolink Client: **Network > Advanced > Server Settings > Set Up**. The exact labels vary with the app/firmware version (https://support.reolink.com/articles/900000621783-How-to-Configure-Reolink-Ports-Settings/).
2. Do the settings **on the Hub, not on the camera** (the camera has no such menu).
3. Stream settings (camera display/stream settings in the app): main stream at the maximum resolution and **15 fps**, sub stream at 15 fps. Choose **H.264** if the menu offers an encoding choice.

### 3. RTSP URLs

Reolink's official format (https://support.reolink.com/articles/900000630706-Introduction-to-RTSP/):

```
rtsp://<user>:<password>@<hub-ip>:554/Preview_<channel>_<main|sub>
```

The channel is the camera's position on the Hub (`01` for the first camera, `02`, ...):

```
rtsp://<hub-ip>:554/Preview_01_main     # 5 MP, face recognition
rtsp://<hub-ip>:554/Preview_01_sub      # low resolution, detection only
```

The older `h264Preview_01_main` / `h264Preview_01_sub` paths are widely quoted by the community for Reolink cameras, but Reolink's current support articles only document `Preview_XX_main|sub`. Use the latter.

Test from the mini-PC (the long timeout lets the camera wake up; `-timeout` is in microseconds on recent FFmpeg, `-stimeout` on older builds):

```bash
ffprobe -hide_banner -rtsp_transport tcp -timeout 30000000 \
  -show_entries stream=codec_name,width,height,avg_frame_rate -of compact \
  "rtsp://jarvis:PASSWORD@192.168.1.40:554/Preview_01_main"
```

Expected: `codec_name=h264` and `width=2880`. Check VAAPI H.264 support on the mini-PC with `vainfo | grep -i h264`.

### 4. Network: DHCP reservation, privacy

1. Create a **DHCP reservation** on the router for the **Home Hub** (the RTSP address) and for the camera.
2. Privacy: in the app, disable **push notifications**, **Reolink Cloud** upload, email alerts and, if your firmware offers it, the **UID/P2P** remote access. The option names differ between firmware versions and were not verified for this model.
3. Optionally **block Internet access** for the Hub and the camera on the router (firewall rule by IP/MAC). The P2P remote viewing and cloud features then stop working; RTSP on the LAN is not affected. Keep Internet open temporarily for firmware updates, or update from a downloaded file.

### 5. Jarvis configuration

Keys from `config/config.example.yaml` and `jarvis/config/settings.py`. Credentials are **never** written in the URL: `rtsp_url_with_credentials()` injects `secrets.camera_username` / `secrets.camera_password` (URL-encoded) only when `camera.rtsp_url` has no `user:password@` part. A URL that already contains `@` is used unchanged.

`/etc/jarvis/config.yaml`:

```yaml
secrets:
  camera_username: ${CAMERA_USER:-}       # Hub account created for Jarvis
  camera_password: ${CAMERA_PASSWORD:-}

camera:
  # Home Hub, channel 01, main stream (5 MP) for face recognition.
  rtsp_url: "rtsp://192.168.1.40:554/Preview_01_main"
  hw_accel: auto            # VAAPI only if the stream is H.264; set false if ffprobe reports hevc
  reconnect_delay_s: 2.0

ptz:
  enabled: false            # no ONVIF PTZ on the Argus PT: NullPTZ, tracking disabled
```

Equivalent environment variables (`/etc/jarvis/jarvis.env`, format `JARVIS__<SECTION>__<KEY>`):

```bash
JARVIS__CAMERA__RTSP_URL=rtsp://192.168.1.40:554/Preview_01_main
JARVIS__PTZ__ENABLED=false
JARVIS__SECRETS__CAMERA_USERNAME=jarvis
JARVIS__SECRETS__CAMERA_PASSWORD=long-random-password
# Optional: longer socket timeout for the camera wake-up (default is 5 s)
OPENCV_FFMPEG_CAPTURE_OPTIONS=rtsp_transport;tcp|stimeout;20000000
```

Ansible (`deploy/ansible/group_vars/all/main.yml` for non-secret values, `vault.yml` encrypted with `ansible-vault` for the secrets):

```yaml
# group_vars/all/main.yml (or inventory host vars)
jarvis_camera_host: "192.168.1.40"        # Home Hub address: the RTSP port is probed (warning only)
jarvis_env:
  JARVIS__CAMERA__RTSP_URL: "rtsp://192.168.1.40:554/Preview_01_main"
  JARVIS__PTZ__ENABLED: "false"

# group_vars/all/vault.yml (ansible-vault encrypt)
jarvis_secrets:
  JARVIS__SECRETS__CAMERA_USERNAME: "jarvis"
  JARVIS__SECRETS__CAMERA_PASSWORD: "long-random-password"
```

The secrets can also be entered in the write-only **Settings > Secrets** page of the Jarvis web UI.

### 6. Firewall

The Jarvis nftables firewall (`deploy/ansible/roles/firewall/templates/nftables.conf.j2`) has an **output policy `accept`**: the outgoing RTSP connection from the mini-PC to the Hub (TCP 554) works without any change. The camera and the Hub never need inbound access to Jarvis; do not open any port for them.

### 7. PTZ (optional, unverified)

If the Hub exposes ONVIF (port 8000) and you want to test pan/tilt, set `ptz.enabled: true`, `ptz.host: <hub-ip>`, `ptz.port: 8000` and use a tool such as ONVIF Device Manager first. Reolink does not document ONVIF PTZ for battery cameras behind a Home Hub; if it does not work, keep `ptz.enabled: false`. The `OnvifPTZ` driver retries failed connections with a 5 s to 120 s backoff, which would only produce log noise.

### 8. Checks and troubleshooting

| Symptom | Likely cause | Action |
|---|---|---|
| No RTSP at all on the camera's own IP | Standalone battery camera: RTSP not supported | Use the **Home Hub** address and `Preview_XX_*` path |
| `Connection refused` on port 554 of the Hub | RTSP disabled (default on some firmwares) | Enable RTSP in Hub > Network > Advanced > Server Settings |
| `401 Unauthorized` | Wrong user/password, or special characters | Check `secrets.camera_username/password`; use an alphanumeric password; do not put credentials in `rtsp_url` twice |
| Timeout on the first connection | Camera asleep, wake-up up to ~20 s | Retry; raise the FFmpeg timeout (`-timeout 30000000` in ffprobe, `stimeout` in `OPENCV_FFMPEG_CAPTURE_OPTIONS`) |
| Stream cut every ~5 minutes | Home Hub limit for battery cameras | By design; Jarvis reconnects automatically. Not fixable: use a mains-powered camera for 24/7 analysis |
| Black or green image, decoder errors, high CPU | H.265 stream, not decoded by HD 4600 VAAPI | Check `ffprobe` codec; choose H.264 if offered, else `camera.hw_accel: false` |
| Black image at night | IR/spotlight settings, or camera in sleep | Check night mode in the app; check the battery level |
| Frequent drops, frozen frames | Weak Wi-Fi, 2.4 GHz congestion | Move to 5 GHz, add an access point near the camera, check RSSI in the app |
| Battery empties in a few days | Continuous streaming | Expected with Jarvis; this camera is not designed for 24/7 streaming |
| Faces detected but never recognized | Too few pixels on the face | Use the main stream, keep the recognition zone within ~2 m |
| PTZ errors in the logs | ONVIF PTZ not available | `ptz.enabled: false` |
