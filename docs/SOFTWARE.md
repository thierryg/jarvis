<!--
=============================================================================
Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
-----------------------------------------------------------------------------
File    : docs/SOFTWARE.md
Purpose : Detailed software documentation: algorithms, parameters, API, data, operations
Author  : Thierry Gayet <thierry.gayet@labworks.fr>
Project : jarvis-home (version: jarvis/VERSION)
Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
=============================================================================
-->
# Jarvis: detailed software documentation

This document describes the software end to end. For each part, it explains what it does, how it does it, with which algorithms and parameters, and how it is administered.
- `docs/ARCHITECTURE.md` gives the big picture and justifies the design choices.
- `docs/diagrams/*.pdf` cover the network, the hardware and the wiring.
- `docs/sbom/` contains the software inventory.

Software version: see the central file `jarvis/VERSION` (command `jarvis version`). References of the form `file.py` point into the `jarvis/` package.

---

## Contents

1. [What the system does](#1-what-the-system-does)
2. [Software architecture](#2-software-architecture)
3. [Vision: person detection and tracking, PTZ](#3-vision-person-detection-and-tracking-ptz)
4. [Face recognition and identification](#4-face-recognition-and-identification)
5. [Sighting traceability](#5-sighting-traceability)
6. [Voice: wake word, speech recognition, speaker, speech synthesis](#6-voice-wake-word-speech-recognition-speaker-speech-synthesis)
7. [Decision engine and access rules](#7-decision-engine-and-access-rules)
8. [Hardware: relay, indicator LEDs, door sensor](#8-hardware-relay-indicator-leds-door-sensor)
9. [Administration](#9-administration)
10. [REST API reference](#10-rest-api-reference)
11. [Data and storage](#11-data-and-storage)
12. [Event log and notifications](#12-event-log-and-notifications)
13. [Configuration reference](#13-configuration-reference)
14. [Operations, performance, tests](#14-operations-performance-tests)
15. [Known limitations](#15-known-limitations)
16. [Deployment and operations tooling](#16-deployment-and-operations-tooling)
17. [License plate recognition and vehicle automation](#17-license-plate-recognition-and-vehicle-automation)
18. [Settings tabs and simulation](#18-settings-tabs-and-simulation)

---

## 1. What the system does

Jarvis is a **100 % local smart gatekeeper**. No data ever leaves the house. It does four things:

1. **It spots people and follows them** with a PTZ camera: the camera pans and zooms to keep the person in the center of the frame.
2. **It identifies faces** and records every sighting (date, time, identity or "unknown", photo). Returning unknown visitors are grouped automatically.
3. **It talks**, offline: wake word "hey jarvis", commands « ouvre / ferme le garage » ("open / close the garage"), spoken replies.
4. **It opens the garage door**, and only when every condition is met: an authorized face seen less than 60 s ago, access rules satisfied, matching voice (optional), consistent door state.

### Typical scenarios

| Situation | What happens |
|---|---|
| A resident arrives | The camera follows and recognizes them in under a second. The green LED lights for the **recognition window** (30 s by default) and « Bonjour Alice » is spoken (once a day). During the window only: « **Jarvis**… ouvre la porte du garage »: pulse on the relay and « J'ouvre le garage ». The LED turns off at the end of the window, and the microphone is ignored again. |
| A contractor comes outside their hours | They are recognized and greeted, but the authorization window stays closed. The `access_denied_schedule` event is logged and notified. Their voice command is refused. |
| A stranger rings | Red LED, "unknown" sighting recorded with their photo. If they have been here before, they join the "unknown #12" cluster. Webhook sent to Home Assistant. |
| A flagged person (watchlist) | Red LED, no greeting, `watchlist_seen` alert (log and webhook). Access is **never** granted. |
| The administrator, from the couch | Watches the annotated video stream, the history filtered by date or by person, runs a search by photo, exports to CSV, enrolls a person, sets time windows. |

---

## 2. Software architecture

### 2.1 Processes and isolation

```
                ┌──────────────────────── mini-PC (Ubuntu) ────────────────────────┐
 Browser    ──HTTPS──▶ nginx :443 ──HTTP──▶ jarvis-api (FastAPI, 127.0.0.1:8000)          │
                │                               │  ▲            │                       │
                │                   Unix socket │  │ frame.jpg  │ SQLite (WAL)          │
                │               /run/jarvis/core.sock          ▼                       │
                │                               ▼  │   /var/lib/jarvis/jarvis.db       │
 Camera ◀─RTSP/ONVIF─▶ jarvis-core (vision, voice, decision, hardware) ──▶ USB relay  │
 Mic/spkr ◀────────────┘                                                            │
                └──────────────────────────────────────────────────────────────────┘
```

When Anubis is deployed (§16.3), it sits between nginx and the API: nginx :443 → Anubis 127.0.0.1:8923 → API 127.0.0.1:8000.

| Process | systemd unit | User | Access | Hardening |
|---|---|---|---|---|
| `jarvis core` | `jarvis-core.service` | `jarvis` (+ groups `audio`, `dialout`, `plugdev`) | camera (network), mic/speaker, relay, sensor, models | `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`, `ReadWritePaths=/var/lib/jarvis /run/jarvis`, `Nice=-5`, `OMP_NUM_THREADS=3` |
| `jarvis api` | `jarvis-api.service` | `jarvis` | no device | Same hardening, plus `PrivateDevices=yes`, empty `CapabilityBoundingSet=`, `SystemCallFilter=@system-service`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6` |
| nginx | `nginx.service` | `www-data` | ports 80/443 | TLS 1.2/1.3, HSTS, strict CSP, `limit_req` on `/api/login` |
| Anubis (optional) | `anubis@jarvis.service` | dynamic user (`DynamicUser=yes`) | loopback only (127.0.0.1:8923) | drop-in `10-jarvis.conf`: `PrivateDevices=yes`, empty `CapabilityBoundingSet=`, `SystemCallFilter=@system-service`, signing key passed as a systemd credential |

**Principle of least privilege**: the API is exposed to the network but cannot actuate anything by itself. Every hardware action, and every computation that needs a model, goes through a **closed list of commands** on the core socket.

### 2.2 Python modules

The package is split into subpackages by concern (paths relative to `jarvis/`).

| Module | Role | Main classes / functions |
|---|---|---|
| `__init__.py` | package root; `__version__` read from `jarvis/VERSION` | `__version__`, `VERSION_FILE` |
| `cli/__init__.py`, `cli/shell.py` | `jarvis` entry point: cmd2 administration shell or a single command | `main`, `JarvisShell` |
| `cli/tasks.py` | operational tasks shared by the shell and the local console | `run_core`, `run_api`, `CheckResult`, `BackupResult` |
| `console.py` | pfSense-style local appliance console (tty1) | `Console` |
| `config/settings.py` | YAML configuration and environment overrides, validated with pydantic | `Settings` and its sections, `load_settings`, `resolve_credentials` |
| `config/catalog.py` | catalog of the settings editable from the web UI (hot reload) | `Param`, `apply_overrides`, `validate_changes`, `diff` |
| `core/service.py` | the `core` process: assembles camera, vision, voice, decision, hardware, control socket | `Core` |
| `core/control.py` | API → core control channel (Unix socket, one JSON request per line) | `ControlServer`, `ControlClient`, `CoreUnavailable` |
| `core/decision.py`, `core/access.py` | GO / NO_GO decision engine; per-person access rules | `DecisionEngine`, `access_allowed` |
| `core/events.py` | events exchanged between the perception pipelines and the decision engine | `FaceRecognized`, `UnknownFaceSeen`, `VoiceCommand`, `ManualGarage`, `Intent` |
| `core/logs.py` | journald console, `/var/log/jarvis/jarvis.log` with rotation, remote syslog | `configure`, `rotate_if_needed` |
| `core/monitoring.py` | Prometheus metrics; node_exporter / Promtail / Alloy agent control | `render_metrics`, `apply` |
| `core/notify.py` | non-blocking outgoing webhook | `WebhookNotifier` |
| `core/sdnotify.py` | dependency-free systemd notification protocol | `notify`, `watchdog_interval_s` |
| `core/sysinfo.py` | hardware capability detection, auto-tuning, live resource metrics, `capabilities.json` | `detect_capabilities`, `apply_auto_performance`, `ResourceSampler`, `write_capabilities_snapshot` |
| `vision/camera.py` | RTSP frame acquisition | `RtspCamera` |
| `vision/ptz.py` | ONVIF PTZ control and tracking controller | `OnvifPTZ`, `NullPTZ`, `PTZTracker`, `build_ptz` |
| `vision/pipeline.py` | vision pipeline: detection, tracking, unknown faces, sightings, adaptive enrollment | `PersonDetector`, `UnknownStore`, `SightingRecorder`, `AdaptiveEnroller`, `PreviewWriter` |
| `vision/faces.py` | InsightFace detection (SCRFD) and embeddings (ArcFace), gallery, voting | `FaceEngine`, `EmbeddingGallery`, `IdentityResolver` |
| `vision/recorder.py` | event-triggered clips and time-lapse capture, FIFO quota | `Recorder`, `enforce_quota`, `timelapse_frames` |
| `voice/assistant.py` | voice assistant loop | `VoiceAssistant` |
| `voice/wakeword.py` | wake word: openWakeWord or Porcupine | `OpenWakeWord`, `PorcupineWakeWord`, `build_wakeword` |
| `voice/commands.py` | pure command parsing | `CommandParser`, `normalize` |
| `voice/speaker.py`, `voice/tts.py`, `voice/audio.py` | ECAPA speaker verification; Piper speech synthesis; audio helpers | `SpeakerVerifier`, `PiperTTS`, `Framer` |
| `hardware/devices.py` | outputs (garage relay, LEDs) and door sensor input | `SerialLcusOutput`, `HidDcttechOutput`, `GpiodOutput`, `MockOutput`, `DoorSensor` |
| `integrations/mqtt.py` | MQTT bridge, Home Assistant discovery | `MqttBridge`, `discovery_messages` |
| `storage/database.py` | SQLite persistence (WAL), migrations, audit hash chain | `Database`, `Person`, `event_hash` |
| `provisioning/models.py` | model download and export | `setup_yolo`, `setup_insightface`, `setup_vosk`, `setup_piper`, `setup_openwakeword`, `setup_speechbrain` |
| `web/app.py` | REST API (FastAPI) behind nginx and Anubis | `create_app`, `LoginRateLimiter` |
| `web/static/` | framework-free UI: `index.html`, `app.js`, `style.css`, `i18n/*.json` (12 locales), `img/` | — |

### 2.3 Core process threads

The core is **multi-threaded** rather than multi-process. The heavy computations (FFmpeg, OpenVINO, ONNX Runtime, Kaldi) run in native code and release the GIL. Each library manages its own thread pool, and the models are loaded into memory only once.

| Thread | Class | Rate | Inputs → outputs |
|---|---|---|---|
| `camera` | `RtspCamera` | stream rate | RTSP → latest frame + sequence number (condition variable) |
| `vision` | `VisionPipeline` | `vision.process_fps` (6/s) | frame → tracks, PTZ commands, face events, sightings, `frame.jpg` |
| `ptz-onvif` | `OnvifPTZ` (asyncio loop) | on demand | latest pending command → ONVIF SOAP |
| `voice` | `VoiceAssistant` | 80 ms blocks | microphone → `VoiceCommand` |
| `tts` | `PiperTTS` | queue | text → cached WAV → speaker |
| `decision` | `DecisionEngine` | event queue | events → LEDs, relay, voice, log, notifications |
| `notify` | `WebhookNotifier` | queue (100 max) | event → HTTP POST (if `webhook_url`) |
| `control` | `ControlServer` | on demand | JSON over socket → handler → JSON |
| `maintenance` | `Core._maintenance` | every 6 h | retention purge (GDPR) |
| LED timer | `threading.Timer` | one-shot | turns the indicator LEDs off after `led_on_s` |

**Graceful shutdown** (`Core.shutdown`, triggered by SIGTERM or SIGINT):
1. components are stopped;
2. `join(5 s)` on `vision`, `camera` and `voice`: the core never exits in the middle of a native inference (that case used to cause a `terminate called…`);
3. ONVIF is closed;
4. the relay is returned to rest;
5. `core_stopped` event.

### 2.4 Inter-process communication

**Control socket** (`control.py`): `/run/jarvis/core.sock`, mode `0660` (umask `007`), group `jarvis`. Every request and every response fits on **one JSON line**. The server is a `ThreadingUnixStreamServer`, and the client has a 30 s timeout.

| Command | Parameters | Response | Used by |
|---|---|---|---|
| `status` | — | `camera_connected`, `vision_fps`, `tracks`, `target_id`, `known_embeddings`, `voice_state`, `door`, `authorized_persons` | Live tab |
| `reload_faces` | — | `known_embeddings` | after any change to faces or persons |
| `reload_voices` | — | — | after a change to voice profiles |
| `enroll_face` | `person_id`, `image_path` | `face_id`, `det_score` | adding a photo |
| `embed_face` | `image_path` | `embedding[512]`, `det_score` | search by face (nothing is stored) |
| `enroll_voice` | `person_id`, `audio_path` | `profile_id` | adding a voice |
| `garage_pulse` | `actor` | — | "Garage pulse" button |
| `ptz_home` | — | — | "PTZ: home position" button |
| `say` | `text` (200 characters max) | — | "Make Jarvis speak" field |

Every response contains `ok: true|false`, plus `error` on failure. An unknown command is rejected.

**Preview frame**: `/run/jarvis/frame.jpg` on tmpfs. The core writes it to `frame.tmp`, then replaces it atomically (`os.replace`). The API reads it back at `preview_fps` and streams it as MJPEG. The RTSP stream is therefore **decoded only once**.

**SQLite in WAL mode**: shared by both processes. One connection is opened per operation, with `busy_timeout=5000` and `foreign_keys=ON`. WAL mode allows concurrent reads while one process is writing.

### 2.5 Internal events (`events.py`)

```
FaceRecognized(track_id, person_id, first_name, score, ts, sighting_id)
UnknownFaceSeen(track_id, unknown_id|None, ts, cluster_id, sighting_id)
VoiceCommand(text, intent|None, speaker_person_id, speaker_score, via_wakeword, ts)
ManualGarage(actor, ts)
Intent = OPEN_GARAGE | CLOSE_GARAGE | CANCEL
```

The vision and voice pipelines **produce** these events into a single `queue.Queue`. The `DecisionEngine` is the **only consumer**, which serializes all decisions.

### 2.6 End-to-end sequence: from face to door opening

```
Camera   RtspCamera   VisionPipeline        IdentityResolver   DecisionEngine        VoiceAssistant   PiperTTS   Relay
  │ RTSP ──▶│ latest() ──▶│ YOLO+ByteTrack         │                  │                    │              │        │
  │         │             │ select_target → PTZ    │                  │                    │              │        │
  │         │             │ SCRFD+ArcFace ────────▶│ vote 1/3         │                    │              │        │
  │         │             │ (0.4 s later) ────────▶│ vote 2/3         │                    │              │        │
  │         │             │ ─────────────────────▶ │ vote 3/3 → known │                    │              │        │
  │         │             │ sighting "known" (SQLite + photo)         │                    │              │        │
  │         │             │ FaceRecognized ───────────────────────────▶│ access_allowed?    │              │        │
  │         │             │                        │                  │ green LED, window  │              │        │
  │         │             │                        │                  │ 60 s; say() ──────────────────────▶│ « Bonjour Alice »
  │         │             │                        │                  │ open_listening(10) ▶│ (waits for  │        │
  │         │             │                        │                  │                    │ end of TTS)  │        │
  │         │             │                        │                  │                    │ Vosk: « ouvre le garage »     │
  │         │             │                        │                  │◀── VoiceCommand ───│              │        │
  │         │             │                        │                  │ checks ① to ④ → pulse_garage() ─────────────▶│ 500 ms
  │         │             │                        │                  │ say() ────────────────────────────▶│ « J'ouvre le garage »
```

---

## 3. Vision: person detection and tracking, PTZ

### 3.1 Acquisition (`RtspCamera`)

- OpenCV with the FFmpeg backend. Options `rtsp_transport=tcp` and `stimeout=5 s` (variable `OPENCV_FFMPEG_CAPTURE_OPTIONS`), `CAP_PROP_BUFFERSIZE=1`.
- The thread reads **continuously** and keeps only **the latest frame**, with a sequence number. Consumers call `latest(after_seq)`, which waits on a `threading.Condition` until a newer frame arrives. FFmpeg buffer latency therefore never accumulates.
- **Reconnection**: if opening fails, the delay doubles at each attempt (from `reconnect_delay_s` = 2 s up to 30 s). After a disconnection, a 1 s pause prevents a tight loop.
- `hw_accel: true` requests VAAPI decoding (`VIDEO_ACCELERATION_ANY`). On the Intel HD 4600, this only works with H.264.
- Use the **720p H.264 substream**: decoding 4K H.265 would saturate a Haswell CPU.

### 3.2 Person detection (`PersonDetector`)

- Ultralytics **YOLO11n** model, exported to **OpenVINO IR FP32** by `jarvis setup-models` (`yolo11n.pt` → `yolo11n_openvino_model/`).
- Call: `model.track(frame, persist=True, classes=[0], conf=0.45, imgsz=480, tracker="bytetrack.yaml")`. COCO class 0 is "person". The frame is *letterboxed* to 480 px on the long side. Detections below `conf` are discarded.
- Output: a list of `Track(id, box xyxy, conf)` in source-frame pixels.

### 3.3 Multi-object tracking (ByteTrack)

ByteTrack is built into Ultralytics.
- A **Kalman filter** predicts the position of each track.
- Association is done **in two passes** by IoU: first the high-confidence detections, then the low-confidence ones (often partially occluded people), matched to the tracks left orphaned.
- There is no re-identification network to run. This is the right option for a single camera on a modest CPU.
- On the Jarvis side, a track absent for more than `TRACK_GRACE_S = 5 s` is forgotten, and the voting state of the `IdentityResolver` is purged with it (`prune`).

### 3.4 Target selection (`select_target`)

1. If the previous target is still present, it is kept. This stability prevents the camera from swinging back and forth between two people.
2. Otherwise, the best score `0.6 × min(4 × relative area, 1) + 0.4 × centrality` wins, with `centrality = 1 − min(2 × |cx − 0.5|, 1)`. In other words: the closest and most central person.

### 3.5 PTZ control (`compute_command`, `PTZTracker`, `OnvifPTZ`)

**Proportional controller**, speeds normalized to [−1, 1]:

```
ex = cx / W × 2 − 1                                   (−1 left … +1 right)
ey = (y1 + 0.3 × h) / H × 2 − 1                       (aim at 30 % from the top of the box: the face)
pan  = 0 if |ex| < deadzone, else clamp(pan_gain × ex, max_speed)
tilt = 0 if |ey| < deadzone, else clamp(−tilt_gain × ey, max_speed)   (ONVIF: y+ = up)
zoom: ratio = h / H ; err = target_height_ratio − ratio
       box touching an edge and ratio > 0.3          → zoom = −zoom_gain × 0.5 (safety zoom-out)
       |err| > 0.1 and (err < 0 or target centered)  → zoom = clamp(zoom_gain × err, max_speed)
```

- **Dead zone** of 12 %, **speed capped** at 0.5 and **low gains**: RTSP latency (0.3 to 1 s) would make a more aggressive controller oscillate.
- Zooming in only happens when the target is **centered**.
- **Anti-flooding**: a command is sent only if it changes, and at most every `command_interval_s` (0.25 s). A null command sends a `Stop`.
- **Return to preset** `home_preset` after `return_home_after_s` (30 s) without a target.
- **Sound trigger** (optional): `sound_trigger_rms > 0` points the camera at `sound_preset` on a noise, if no target has been tracked for 5 s.
- **ONVIF**: `onvif-zeep-async` runs in its own asyncio loop (thread `ptz-onvif`). Commands are **coalesced**: only the most recent one is sent, and vision never waits on the network. Calls used: `ContinuousMove`, `Stop` and `GotoPreset` on profile `profile_index`. On connection failure, retry with a delay growing from 5 s to 120 s.
- **Fixed camera** (`ptz.enabled: false`): tracking is disabled (`PTZTracker.enabled = False`) and the camera is never "moving". Before this fix, face recognition never ran without PTZ.

### 3.6 Annotated preview (`PreviewWriter`)

Rate `preview_fps` (5/s). The frame is annotated with:
- green boxes for known people (with their first name), red for unknown ones, yellow for people not yet identified;
- a thick outline for the target;
- track number and clock.

It is then resized to `preview_width` (960 px), encoded as JPEG quality 75 and written atomically.

---

## 4. Face recognition and identification

### 4.1 Search region

The face is searched only in the **upper half of the "person" box** (`face_region`), widened by 10 % on the sides and 5 % upward, over 55 % of the height. This is far cheaper than analyzing the whole frame, and the face is attached to the track without ambiguity. A region smaller than 48 px is ignored.

**Budget**: at most `max_faces_per_frame` (2) faces per frame, starting with the target. Each track is rechecked at most every `recheck_interval_s` (0.4 s). **Nothing is analyzed during a PTZ movement**, because the frame is blurred.

### 4.2 Detection and encoding (`FaceEngine`, InsightFace `buffalo_s`)

- **SCRFD-500M** detects faces and their 5 landmarks, with a `det_size` input of 320 × 320, on ONNX Runtime CPU.
- **Alignment**: similarity transform to the 112 × 112 ArcFace template, from the 5 landmarks.
- **ArcFace MobileFaceNet** produces a **512-dimensional embedding**, L2-normalized (`normed_embedding`).
- **Quality filters** (`quality_ok`): shortest side ≥ `min_face_px` (40 px), detection score ≥ `min_det_score` (0.6), sharpness ≥ `min_sharpness` (30). Sharpness is the **variance of the Laplacian** in grayscale.
- `best_face` keeps the largest face that passes the filters. For enrollment from a photo, the image is first downscaled to 960 px and the filters are lifted.
- A lock (`threading.Lock`) serializes access to the ONNX session between the vision thread and the control commands.

### 4.3 Matching (`EmbeddingGallery`)

- The gallery is an in-memory `N × 512` matrix (float32, normalized rows) along with the vector of `person_id`s. A person can have several photos, hence several rows.
- **Cosine similarity** is a plain dot product, `sims = M · e`. The best candidate is the `argmax`. The computation is instantaneous up to thousands of embeddings.
- ArcFace threshold `match_threshold`:

| Threshold | Behavior |
|---|---|
| 0.40 | permissive: more recognitions, and more confusions |
| **0.45** | default: a good compromise for a household |
| 0.55 | strict: more "not recognized" in profile view or under IR |

- The gallery is **hot-reloaded** (`reload_faces`) after any addition or deletion, without restarting the core.

### 4.4 Identification by voting (`IdentityResolver`)

A single blurred frame must **never** trigger an action. The resolver therefore keeps a per-track state:

```
for each observation (track t, best candidate p, score s, embedding e, capture c, quality q):
    if t is already decided: ignore
    if s ≥ match_threshold:
        votes[t][p] += 1 ; remember the best observation (s, q, e, c) for p
        if votes[t][p] ≥ votes_required (3): decide KNOWN(p) with the best observation
    else:
        misses[t] += 1 ; remember the highest-quality observation
        if misses[t] ≥ unknown_after_observations (6) and no vote: decide UNKNOWN
```

- The decision is **made once per track**. A person standing still in front of the camera therefore generates a single sighting.
- Quality of a capture: `q = det_score × min(width / 100 px, 1) × min(sharpness / 100, 1)`, between 0 and 1.
- After a gallery reload, all tracks are re-evaluated (`reset`).

### 4.5 Unknown visitors: deduplication and clustering (`UnknownStore`)

When a track is declared unknown, its **best capture** is processed as follows:

1. **Nearest-neighbor search** among all stored unknowns (in-memory cache reloaded from SQLite).
2. **Deduplication**: if the similarity is ≥ `unknown_dedupe_similarity` (0.5) with an unknown seen less than `unknown_dedupe_window_s` (1 h) ago, nothing is stored. It is the same visit. The sighting itself is still recorded and attached to the cluster.
3. **Clustering** (incremental *leader* clustering): if the similarity is ≥ `unknown_cluster_similarity` (0.5), the new unknown **joins the cluster** of its nearest neighbor. Otherwise, it founds a new cluster (`cluster_id = id`).
4. The capture is stored in `unknown/YYYYMMDD/<uuid>.jpg`, with its embedding.

**Labeling a cluster** (Unknowns tab) turns all of its visits at once into known faces of the chosen or newly created person. The embeddings are reused without recomputation. The cluster is also **identified retroactively**: its sightings switch to the `labeled` status, with the `person_id` (§5).

### 4.6 Adaptive learning (`AdaptiveEnroller`)

Appearance changes: haircut, beard, glasses, aging, lighting. When a person is recognized **with certainty**, their best capture enriches their gallery, provided that **all** of the following conditions are met:

| Condition | Parameter | Default | Reason |
|---|---|---|---|
| Feature enabled | `adaptive_enabled` | `true` | — |
| Score well above the threshold | `adaptive_min_score` | 0.60 (threshold 0.45) | avoid learning a confusion |
| Good-quality capture | `adaptive_min_quality` | 0.35 | no blur, no tiny face |
| Genuinely new capture | `adaptive_novelty_max_similarity` | 0.85 | no point stacking duplicates |
| Per-person cap | `adaptive_max_per_person` | 5 | limits drift and poisoning |
| Not on the watchlist | — | — | — |

The addition carries `source = "auto"`. It is marked "auto" in the interface, removable in one click, and logged (`face_auto_enrolled`). The gallery is reloaded immediately. **Residual risk**: a confusion with a score ≥ 0.6 would be learned. This is unlikely with ArcFace, where different people generally stay below 0.3. For strict control, disable the feature.

### 4.7 Search by face

`POST /api/search/face`:
1. The uploaded photo is re-encoded, oriented according to its EXIF data, then written to `data/tmp/`.
2. The core computes its embedding (`embed_face`), and the file is **deleted immediately**.
3. The API compares the embedding with those of **all stored sightings** and returns those above `search_threshold` (0.45), sorted by similarity.

Use case: "When did this person come by?", from a photo of a neighbor, a delivery driver or a screenshot. The search is logged (`face_search`) with its author.

---

## 5. Sighting traceability

Every identification decision becomes a row of the `sightings` table:

| Field | Content |
|---|---|
| `ts` | **date and time** (Unix timestamp, displayed in local time) |
| `status` | `known` (recognized) · `unknown` · `watchlist` (watched person) · `labeled` (unknown identified afterward) |
| `person_id` | identity (empty for an unknown) |
| `score` | ArcFace similarity of the best observation |
| `quality` | capture quality (§4.4) |
| `image_path` | **face photo** (`sightings/YYYYMMDD/<uuid>.jpg`, JPEG quality 85) |
| `embedding` | 512 floats (used by the search by face) |
| `track_id` | ByteTrack track number |
| `unknown_id`, `cluster_id` | stored unknown and visitor cluster |

**Browsing**: **Sightings** tab.
- Filters by period (from / to), person and status, with pagination.
- Table: date, time, thumbnail, identity, status, score, track.
- 14-day histogram (recognized in green, unknown or watched in red).
- **CSV export**: `;` separator, UTF-8 BOM for Excel, columns `id;date;time;status;person;score;quality;track;unknown_group`. Every export is logged.

**Retention**: `sighting_retention_days` (90 days), photos included. Deleting a person also erases all of their sightings (right to erasure).

---

## 6. Voice: wake word, speech recognition, speaker, speech synthesis

### 6.1 Audio capture

- `sounddevice.RawInputStream`: 16 kHz, mono, int16, blocks of **1,280 samples (80 ms)**, the native size of openWakeWord.
- The PortAudio callback pushes blocks into a queue of 200 blocks (16 s). When it is full, **audio is dropped rather than accumulated**: better to lose a bit of sound than to add latency.
- On a device error, the stream is restarted after 5 s.

### 6.2 Half-duplex

- The `PiperTTS.speaking` flag is raised **as soon as a sentence is queued**, then lowered 0.3 s after playback ends (reverberation tail).
- While it is raised, the microphone is ignored and the wake-word state is reset. Jarvis therefore never hears itself.
- The ReSpeaker adds its hardware echo cancellation.

### 6.3 Wake word

- **openWakeWord** (default, "hey jarvis") is an ONNX chain of three models:
  1. mel spectrogram;
  2. Google's audio embedding extractor (pre-trained, frozen);
  3. small `hey_jarvis_v0.1` classifier.

  A score ≥ `threshold` (0.5) triggers wake-up. `Framer` re-slices the stream into frames of the size required by the engine.
- **Porcupine** (optional, "jarvis" alone) requires a Picovoice key. Sensitivity `porcupine_sensitivity`.
- After detection: 880 Hz beep (150 ms, Hann window), audio queue flush, then listening for `listen_timeout_s` (6 s).

**Recognition window** (`decision.auth_window_s`, 30 s by default, set in Settings > Decision and access):
- **Opening:** when an authorized person is recognized, the decision engine lights the green LED
  for the whole window and calls `VoiceAssistant.activate(window)`. The event is
  `voice_window_opened`, with the person and the duration.
- **Standby:** outside the window, with `decision.voice_only_after_recognition` (default), the
  microphone is **ignored**: no wake word detection at all. The status shows `voice_state = sleeping`.
- **Extension:** a new recognition extends the window (it never shortens it); the LED timer restarts.
- **Expiry:** the LED turns off, the assistant goes back to sleep, and the service log records
  "Voice commands disabled: recognition window closed".
- **Wake word required:** every command must start with the **"Jarvis" token**, i.e. the wake
  word ("hey jarvis" with openWakeWord, "jarvis" with Porcupine). Listening without it after the
  greeting (`listen_after_recognition_s`) is 0, i.e. disabled.

### 6.4 Speech recognition: Vosk with a restricted grammar

- Model `vosk-model-small-fr-0.22` (Kaldi, about 41 MB), `KaldiRecognizer(model, 16000, grammar)`.
- The **grammar** is the JSON list of the configured phrases, normalized, plus `"[unk]"`. By default (`commands.strict`) it holds exactly **two** phrases: « ouvre la porte du garage » and « ferme la porte du garage ». The decoder can produce **only** these phrases. Everything else comes out as `[unk]`. This is what makes recognition very robust to noise, far more than free dictation.
- Loop: every block goes through `AcceptWaveform`. At each end of utterance (`Result`):
  - phrase understood: stop;
  - phrase not understood after the wake word: stop as well, and Jarvis replies « Je n'ai pas compris » ("I did not understand");
  - noise during direct listening: keep listening.
- When the time limit is reached: `FinalResult`.

### 6.5 Command parsing (`CommandParser`)

1. **Normalization**: lowercase, NFKD decomposition, removal of accents and apostrophes, collapsed whitespace (« Fermé l'accès » → « ferme l acces »).
2. **Exact match** against the configured phrases.
3. **Strict mode** (`commands.strict`, default): nothing else is accepted. « ouvre le garage » or « ferme la porte » are refused, as is anything that is not one of the two commands.
4. **Keyword fallback** (only with `commands.strict: false`): presence of `garage` or `porte` ("door"), plus a word starting with `ouvr` (*ouvrir*, open) or `ferm` (*fermer*, close).
5. Otherwise: `None`, command not understood.

### 6.6 Speaker verification (option `speaker.enabled`)

- **SpeechBrain ECAPA-TDNN** (`spkrec-ecapa-voxceleb`): normalized 192-dimensional embedding, on CPU.
- **Enrollment**: 10 s read aloud in the browser (`MediaRecorder`, WebM/Opus). The recording is converted by FFmpeg to float32 16 kHz mono (3 s minimum), then stored in `voice_profiles`.
- **Identification**: the command utterance (`max_utterance_s` = 6 s at most, 0.5 s at least) is compared with the voice gallery. Below `threshold` (0.35), the speaker is not identified. A short command (1 to 2 s) yields a less reliable voiceprint, hence this compromise threshold.
- Verification **does not detect a replayed** recording. It complements the face, it does not replace it.

### 6.7 Speech synthesis (Piper)

- **Piper** (VITS network exported to ONNX), voice `fr_FR-siwis-medium`, 22.05 kHz, offline. Compatible with the piper-tts 1.2 API (`synthesize`) and 1.3 onward (`synthesize_wav`).
- **Disk cache**: key `SHA-1(model | text)` → `tts-cache/<key>.wav`. Each sentence is synthesized only once, and greetings become instantaneous.
- Playback through `sounddevice.play` on `audio.output_device`. FIFO queue: sentences never overlap.

---

## 7. Decision engine and access rules

### 7.1 Event handling (`DecisionEngine`)

| Event | Actions |
|---|---|
| `FaceRecognized`, person **on the watchlist** | Red LED for `led_on_s`, `watchlist_seen` event, notification, optional `watchlist_message`. **No greeting and no authorization.** |
| `FaceRecognized`, regular person | `face_recognized` event; green LED for the whole recognition window. If `can_open_garage` **and** `access_allowed(person, now)`: the person enters the **recognition window** (`auth_window_s` = 30 s) and the voice commands are enabled for it (`voice_window_opened`). Otherwise, `access_denied_schedule` with the reason, and notification. Greeting once a day (`greetings` table). |
| `UnknownFaceSeen` | Red LED, `face_unknown` event (unknown, cluster, sighting), notification, optional `unknown_message`. |
| `VoiceCommand` without intent | « Je n'ai pas compris. » (`voice_not_understood`) |
| `VoiceCommand` CANCEL | « D'accord. » (`voice_cancel`) |
| `VoiceCommand` OPEN / CLOSE | Checks ① to ④ below, then pulse. |
| `ManualGarage` (web) | Direct pulse, logged with the user. |

**Checks before a voice-triggered pulse**:
1. at least one authorized person in the recognition window (30 s by default), otherwise « Accès refusé. Je ne vous ai pas reconnu. » ("Access denied. I did not recognize you.", `no_authorized_face`);
2. if `speaker.enabled`: the identified voice must belong to a person in the window, otherwise « Voix non reconnue » ("Voice not recognized", `speaker_mismatch`). Without voice verification, the command is attributed to the most recently recognized person;
3. door sensor: "open" on a door that is already open yields « Le garage est déjà ouvert. » ("The garage is already open."), and vice versa;
4. hardware debounce: `cooldown_s` (5 s) minimum between two pulses, otherwise `garage_pulse_rejected`.

Every refusal lights the red LED, plays a voice message and creates the `voice_denied` event with its reason, followed by a notification.

### 7.2 Per-person access rules (`access.py`)

| Field | Format | Effect |
|---|---|---|
| `can_open_garage` | boolean | base condition |
| `watchlist` | boolean | forbids any access (absolute priority) |
| `valid_until` | timestamp | access **expires** after this date (guest, contractor, rental) |
| `access_days` | ISO digits `1` to `7` (Monday = 1) | allowed days. Empty = every day |
| `access_start`, `access_end` | `HH:MM` | time range [start, end[, which **may cross midnight** (22:00 → 06:00) |

`access_allowed(person, date)` returns `(allowed, reason)`. Possible reasons: `ok`, `not_authorized`, `watchlist`, `expired`, `day_not_allowed`, `outside_hours`. The function is pure, tested by 12 edge cases (`tests/test_features.py`).

---

## 8. Hardware: relay, indicator LEDs, door sensor

| Backend (`hardware.*.backend`) | Hardware | Protocol |
|---|---|---|
| `serial_lcus` | CH340 USB relay board "LCUS-1/2/4" | Serial 9,600 baud, frame `A0 <channel> <state> <checksum>` (checksum = `(A0 + channel + state) & FF`). A single port shared by all channels (`_SharedSerial`), protected by a lock. |
| `hid_dcttech` | "USBRelay" USB HID relay (16c0:05df) | *Feature report* `[0, FF (on) or FD (off), channel, 0…]` |
| `gpiod` | Linux GPIO (libgpiod v2): Raspberry Pi, FT232H, MCP2221 | output line, optional `active_low` |
| `mock` | none | logs only (development, automatic fallback when opening fails) |

- **Garage pulse** (`Hardware.pulse_garage`): the contact is closed for `pulse_ms` (500 ms), then opening is **guaranteed** by a `finally`. Lock and `cooldown_s`. The return value is `False` when the pulse is refused.
- **Indicator LEDs** (`indicate`): turns one color on and the other off. A timer turns them off after `led_on_s`, and a new indication cancels the previous one.
- **Door sensor**:
  - `serial_cts` reads the CTS pin of a USB-serial adapter. With an FT232RL TTL adapter, the reed switch is wired between CTS and GND. With an RS-232 port, between DTR and CTS;
  - `gpiod` reads a GPIO line with pull-up;
  - `active_means_closed` sets the logic (true = contact closed ↔ door closed).
- **Shutdown**: all outputs return to rest.
- Stable device names via udev (`deploy/udev/99-jarvis.rules`): `/dev/jarvis-relay` (1a86:7523) and `/dev/jarvis-door` (0403:6001).

---

## 9. Administration

### 9.1 Access and interface security

- **Accounts**: `jarvis create-user <name>` (12 characters minimum). Password hashed with **Argon2id**, with automatic rehashing when the parameters change.
- **Default account**: when the database holds no account, the API creates the factory account **`admin` / `admin`** at startup (`Database.ensure_default_admin`, event `default_admin_created`) with a **forced password change**. As long as `must_change_password` is set, every route returns 403 "Password change required" except those of `PASSWORD_CHANGE_ALLOWED` (`/api/me`, `/api/logout`, `/api/account/password`). `POST /api/account/password` checks the current password (401 otherwise) and enforces the policy: at least 12 characters, at least 5 different characters, different from the current password, and not the account name nor `admin`, `password` or `jarvis` (422 otherwise). The account's other sessions are revoked and the change is audited (`password_changed`, `password_change_failed`). `jarvis reset-admin [--yes]` restores `admin`/`admin` with the forced change and closes its web sessions (`admin_factory_reset`).
- **Password dialog (web UI)**:
  - **Forced mode:** after a sign-in with the initial password, the application shell stays
    visible but locked (blurred) behind a modal dialog. Escape is ignored and the secondary
    button signs out; no page is loaded and no polling starts until the change succeeds.
  - **Any 403 "Password change required":** the dialog reopens.
  - **Voluntary mode:** the user menu has a "Change password" entry.
  - **Live checklist:** it mirrors the server policy (12 characters, 5 distinct characters, no
    default word, different from the current password, confirmation), and the submit button is
    enabled only when every rule is met. The server stays the authority: its 401/422 messages
    are shown.
  - **After the change:** a toast reports the other sessions that were signed out.
- **Boot robustness (web UI)**: the page never stays blank.
  - **Missing translation table:** the English texts of `index.html` are used.
  - **Page opened as a local file (`file://`):** the login view explains how to reach the appliance
    and disables the form.
  - **API not answering:** the login view shows "service not answering".
  - **First visit (401 on `/api/me`):** the login view appears without any "session expired" notice.
- **Localization**: `static/i18n/en-US.json` is the single reference (every key). The 11 other
  locales (fr-FR, es-ES, nl-NL, de-DE, it-IT, ru-RU, zh-CN, id-ID, ko-KR, ja-JP, th-TH) must have
  exactly the same keys and placeholders, which `tests/test_web_ui.py` checks.
- **Images** (`static/img/`): favicon (SVG, ICO), Apple touch icon, PWA icons (`manifest.webmanifest`),
  `login-bg.svg` (login hero background), standalone copies of the icon sprite and of the hero
  illustration. The page also sends `<meta name="robots" content="noindex…">`.
- **Session**: random 256-bit token (`secrets.token_urlsafe(32)`). The database stores only its **SHA-256**. Cookie `jarvis_session` with `HttpOnly`, `Secure`, `SameSite=Strict`, lifetime `session_hours` (12 h).
- **Anti-CSRF**: the `X-Jarvis: 1` header is mandatory on every request other than GET or HEAD. A third-party form cannot add it.
- **Anti-brute-force**: at most 5 failures per IP over 5 minutes (`LoginRateLimiter`), plus nginx `limit_req` (10 per minute, burst of 5). The IP is read from the `X-Real-IP` header supplied by nginx.
- **Media**: photos are served only by `/api/media`, which requires a session, accepts only the `faces/`, `unknown/` and `sightings/` directories (after symlink resolution) and sends `Cache-Control: private`.
- Every administrative action is **logged with its author**.

### 9.2 Web interface tabs

| Tab | Features |
|---|---|
| **Live** | Annotated MJPEG stream (stopped when leaving the tab), core status refreshed every 3 s (camera, frames analyzed per second, tracks, enrolled faces, voice, door, people in the authorization window). Buttons: garage pulse (with confirmation), PTZ to preset, make Jarvis speak. |
| **Sightings** | Filterable timestamped history, 14-day histogram, CSV export, **search by face**. |
| **People** | Creation, right to open the garage, **watchlist**, **access rules** (days, hours, expiration), photos (multiple upload, deletion, "auto" mark), voice profile (10 s recording, removal), last sighting, full deletion. |
| **Unknowns** | **Clustered** visitors with their number of visits and sightings, and the dates. Association with an existing or new person (**retroactive identification**), deletion. |
| **Log** | All events (§12), pagination into the past, colors for openings and refusals. |
| **Vehicles** | Plate registry and read history (§17.3). |
| **Logs** | Service log with text search, level filter and live tail (secrets masked). |
| **Settings** | Every hot-reloadable parameter, in **domain tabs** with one **sub-tab per group**; camera stream test and annotated preview; **simulation** of the camera and the voice (§18). |

### 9.3 Common procedures

**Add a resident**
1. People tab: first name, last name, check "Can open the garage", then Add.
2. **+ Photos**: 3 to 5 sharp photos, face frontal and three-quarter, with and without glasses, in daylight. Photos are re-encoded on import (EXIF and GPS stripped), downscaled to 1,600 px, then encoded by the core. A photo without a detected face is rejected.
3. Optionally, **+ Voice**: read the suggested sentence for 10 s, in a quiet place (HTTPS is required for the microphone).
4. In the Live tab, check that the person's box shows their first name.

**Temporary access (contractor, guest)**: create the person and check "Can open the garage". Under "Access rules", choose the days and the time range (for example Mon to Fri, 08:00 to 18:00), and an "Until" date, then Save. Outside the range, the person is recognized and greeted, but is not granted access.

**Revoke access**: uncheck "Can open the garage"; this takes effect immediately for subsequent recognitions. An authorization window that is already open expires after 60 s at most.

**Flag a person**: check "Watchlist". A photo is needed to recognize them. The simplest way is to label their cluster in the Unknowns tab.

**Identify a visitor afterward**: in the Unknowns tab, on the cluster card, choose "Associate with…" a person or "+ New person…". All of their visits become photos of that person, and their past sightings are attributed.

**Find a person's visits**: Sightings tab, "person" filter. If they are not enrolled: **Search by face** with a photo.

**Delete a person** (right to erasure): Delete button. The following are erased: the record, the face embeddings and photos, the voice profiles and their recordings, the **sightings and their photos**, the greetings. The core reloads its galleries. The log keeps the `person_deleted` event, without any biometric data.

**Export the history**: Sightings tab, apply filters, then Export CSV.

### 9.4 Command line

| Command | Role |
|---|---|
| `jarvis core` / `jarvis api` | services (started by systemd) |
| `jarvis setup-models` | downloads Vosk, Piper, YOLO11n (and its OpenVINO export), InsightFace, openWakeWord and ECAPA if enabled. This is the **only** step that needs Internet access. |
| `jarvis create-user <name>` | creates or resets an account (password asked twice) |
| `jarvis reset-admin [--yes]` | factory reset of the `admin` web account (§9.1) |
| `jarvis enroll-face --first-name X [--last-name Y] [--can-open-garage] photo…` | offline enrollment, bypassing the core (initial installation) |
| `jarvis say "text"` | tests speech synthesis and the speaker |
| `jarvis test-hardware [--pulse]` | reads the sensor, lights each LED for 2 s; `--pulse` also actuates the garage |
| `jarvis version` | prints the software version (`jarvis/VERSION`), Python and platform |
| option `-c file.yaml` | alternative configuration (otherwise `$JARVIS_CONFIG`, then `/etc/jarvis/config.yaml`, then `config/config.yaml`) |

---

## 10. REST API reference

Prefix `/api`. Every route requires a session, except `POST /login`. Every method other than GET requires the `X-Jarvis: 1` header. Responses are JSON, except CSV, MJPEG and media. The OpenAPI schema is deliberately disabled.

| Method | Route | Body / parameters | Response |
|---|---|---|---|
| POST | `/login` | `{username, password}` | `{username}`, session cookie · 401 · 429 |
| POST | `/logout` | — | `{}` |
| GET | `/me` | — | `{username, idle_timeout_minutes, language, must_change_password, version}` (the version is disclosed only to authenticated users) |
| POST | `/account/password` | `{current_password, new_password}` | `{other_sessions_revoked}` · 401 wrong current password · 422 policy not met |
| GET | `/persons` | — | list: id, first name, last name, `can_open_garage`, `watchlist`, access rules, `face_count`, `auto_face_count`, `voice_count`, `last_seen` |
| POST | `/persons` | `{first_name, last_name?, can_open_garage?, watchlist?, access_days?, access_start?, access_end?, valid_until?}` | `{id}` (201) · 422 on invalid format |
| PATCH | `/persons/{id}` | same fields, optional, plus `clear_valid_until` | `{}` and core reload |
| DELETE | `/persons/{id}` | — | `{}` (full erasure) |
| GET | `/persons/{id}/faces` | — | photos: id, `image_path`, `source` (`upload`, `cli`, `labeled`, `auto`) |
| POST | `/persons/{id}/faces` | multipart `file` (image, 15 MB max) | `{face_id}` (201) · 415 · 422 if no face · 503 if the core is down |
| DELETE | `/faces/{id}` | — | `{}` |
| POST | `/persons/{id}/voice` | multipart `file` (WAV, WebM, OGG, MP3, M4A, FLAC) | `{profile_id}` (201) |
| DELETE | `/persons/{id}/voice` | — | `{}` |
| GET | `/unknowns` | — | individual unknowns |
| GET | `/unknowns/clusters` | — | clusters: `cluster_id`, `faces`, `sightings`, `first_seen`, `last_seen`, `image_path` |
| POST | `/unknowns/{id}/label` | `{person_id}` or `{new_person:{…}}` | `{person_id, face_id, sightings_relabeled}` |
| POST | `/unknowns/clusters/{cid}/label` | same | `{person_id, faces, sightings_relabeled}` |
| DELETE | `/unknowns/{id}` · `/unknowns/clusters/{cid}` | — | `{}` · `{deleted}` |
| GET | `/sightings` | `start`, `end` (YYYY-MM-DD, local time, inclusive bounds), `person_id`, `status`, `limit` (1,000 max), `before_id` | sightings with first and last name |
| GET | `/sightings.csv` | same filters | CSV file as an attachment |
| GET | `/sightings/stats` | `days` (366 max) | `[{day, status, n}]` |
| POST | `/search/face` | multipart `file`, `limit` (500 max) | sightings with `similarity`, sorted |
| GET | `/media` | `path` | image (allowed directories only) |
| GET | `/events` | `limit`, `type`, `before_id` | events |
| GET | `/status` | — | core status (see `status`, §2.4) |
| POST | `/garage/pulse` · `/ptz/home` · `/say` | — · — · `{text}` | `{}` |
| GET | `/stream.mjpg` | — | `multipart/x-mixed-replace`, 5 frames/s |
| GET | `/settings` | — | `{groups: [{name, domain, params}], domains}` (§18.1) |
| GET | `/simulation` | — | `{files: [{name, kind, size}], camera_simulation, max_upload_mb}` |
| POST | `/simulation/files` | multipart `file` (video, photo or recording; `simulation.max_upload_mb`) | `{name, kind, size}` · 413 · 415 |
| DELETE | `/simulation/files/{name}` | — | `{}` |
| POST | `/simulation/camera` | `{file}` (`null` = back to the camera) | `{camera_simulation}` · 422 not a video or photo |
| POST | `/simulation/face` | `{person_id}` | `{person, window_s, events, said}` · 404 |
| POST | `/simulation/voice` | `{text}` or `{file}` | `{wake_word, stt, transcript, intent, outcome, events, said}` |

---

## 11. Data and storage

### 11.1 SQLite schema (`db.py`)

| Table | Columns | Notes |
|---|---|---|
| `persons` | id, first_name, last_name, can_open_garage, created_at, **watchlist, access_days, access_start, access_end, valid_until** | |
| `face_embeddings` | id, person_id → persons (CASCADE), embedding (BLOB 512 × float32), image_path, source, created_at | `source`: upload, cli, labeled, auto |
| `voice_profiles` | id, person_id (CASCADE), embedding (192 × float32), audio_path, created_at | |
| `unknown_faces` | id, embedding, image_path, det_score, created_at, **cluster_id** | index on cluster_id |
| `sightings` | id, ts, track_id, status, person_id (SET NULL), score, quality, image_path, embedding, unknown_id, cluster_id | indexes on ts, person_id and cluster_id |
| `events` | id, ts, type, actor, person_id, details (JSON) | index on ts |
| `greetings` | person_id (CASCADE), day | primary key (person_id, day) |
| `users` | id, username (UNIQUE), password_hash, created_at | |
| `sessions` | token_hash (SHA-256), user_id (CASCADE), expires_at | |

**Migrations**: at startup, `Database.init()` runs the schema (`CREATE … IF NOT EXISTS`). It then adds the columns missing from previous versions (`MIGRATIONS` list, via `ALTER TABLE ADD COLUMN`), then creates the indexes. The operation is idempotent and tested against a database with the first schema.

### 11.2 Directory layout

```
/etc/jarvis/config.yaml          configuration (640 root:jarvis)
/etc/jarvis/jarvis.env           secrets: CAMERA_USER, CAMERA_PASSWORD, PICOVOICE_ACCESS_KEY
/etc/jarvis/tls/                 TLS certificate and key
/opt/jarvis/venv                 Python 3.11 and dependencies (read-only at runtime)
/var/lib/jarvis/jarvis.db        SQLite database (plus -wal, -shm)
/var/lib/jarvis/faces/<id>/      enrollment photos, including auto_*.jpg (adaptive learning)
/var/lib/jarvis/unknown/<date>/  unknown faces
/var/lib/jarvis/sightings/<date>/ sighting captures
/var/lib/jarvis/voice/<id>/      voice enrollment recordings
/var/lib/jarvis/tmp/             search photos (deleted immediately)
/var/lib/jarvis/models/          YOLO OpenVINO, InsightFace, Vosk, Piper, openWakeWord, ECAPA
/var/lib/jarvis/tts-cache/       synthesized sentences
/run/jarvis/                     core.sock, frame.jpg, capabilities.json (tmpfs, 0770 jarvis)
```

### 11.3 Retention (`maintenance` thread, every 6 h)

| Data | Duration | Parameter |
|---|---|---|
| Unknown faces (images included) | 30 days | `storage.unknown_retention_days` |
| Sightings (photos included) | 90 days | `storage.sighting_retention_days` |
| Event log, greetings | 180 days | `storage.event_retention_days` |
| Expired sessions | immediate | — |

---

## 12. Event log and notifications

### 12.1 Event types (`events` table, Log tab)

| Domain | Types |
|---|---|
| System | `core_started`, `core_stopped`, `user_created`, `default_admin_created` |
| Vision | `face_recognized`, `face_unknown`, `watchlist_seen`, `access_denied_schedule`, `face_auto_enrolled` |
| Voice | `voice_not_understood`, `voice_cancel`, `voice_denied` (reasons `no_authorized_face`, `speaker_mismatch`) |
| Garage | `garage_pulse` (actor, person, intent, source, door state before), `garage_pulse_rejected`, `garage_pulse_simulated` (decided during a simulation, relay not driven) |
| Web: simulation | `simulation_file_added`, `simulation_file_deleted`, `simulation_camera`, `simulation_face`, `simulation_voice` |
| Web: session | `login`, `login_failed` (with IP), `logout`, `password_changed`, `password_change_failed`, `admin_factory_reset` |
| Web: people | `person_created`, `person_updated`, `person_deleted`, `face_added`, `face_deleted`, `voice_added`, `voice_deleted` |
| Web: unknowns | `unknown_labeled`, `unknown_deleted`, `cluster_labeled`, `cluster_deleted` |
| Web: traceability | `sightings_exported`, `face_search` |
| Web: actions | `ptz_home`, `say` |

Everything is also written to **journald** (`journalctl -u jarvis-core -f`).

### 12.2 Webhook notifications (`notify.py`)

If `notifications.webhook_url` is set, every event listed in `notifications.events` is sent as a JSON `POST`:

```json
{"source": "jarvis", "type": "face_unknown", "ts": 1790000000.1,
 "datetime": "2026-09-30T09:27:55", "unknown_id": 12, "cluster_id": 4, "sighting_id": 57}
```

- Emitted types: `face_unknown`, `watchlist_seen`, `access_denied_schedule`, `voice_denied`, `garage_pulse`.
- Sent from a dedicated thread, with a 100-message queue and a `timeout_s` timeout. A network error is only logged: **the core is never blocked**.
- **Home Assistant**: an automation with a *Webhook* trigger (`/api/webhook/<id>`), then a mobile notification or turning on a light.
- **ntfy**: `webhook_url: https://ntfy.lan/jarvis`.
- **Node-RED / n8n**: incoming HTTP node.
- F12 network flow: see `docs/diagrams/jarvis-network-flows.pdf`.

---

## 13. Configuration reference

File: `/etc/jarvis/config.yaml`, validated by pydantic. An unknown key is ignored; an invalid type makes startup fail. Strings accept `${VAR}` and `${VAR:-default}`, read from the environment (`jarvis.env`). Commented example: `config/config.example.yaml`.

| Key | Default | Effect |
|---|---|---|
| `log_level` | `INFO` | logging level |
| **camera** | | |
| `rtsp_url` | `rtsp://user:pass@192.168.50.64:554/stream2` | analyzed stream (720p substream recommended) |
| `hw_accel` | `false` | VAAPI decoding |
| `reconnect_delay_s` | `2.0` | first reconnection delay |
| **ptz** | | |
| `enabled` | `true` | `false` = fixed camera |
| `host`, `port`, `username`, `password`, `profile_index` | `192.168.50.64`, `80`, `admin`, empty, `0` | ONVIF access |
| `home_preset`, `return_home_after_s` | `"1"`, `30` | return to the home position |
| `sound_preset` | `null` | preset targeted by the sound trigger |
| `pan_gain`, `tilt_gain`, `zoom_gain` | `0.6`, `0.6`, `0.5` | controller gains |
| `deadzone`, `max_speed` | `0.12`, `0.5` | dead zone, maximum speed |
| `invert_tilt`, `zoom_enabled`, `target_height_ratio` | `false`, `true`, `0.55` | tilt direction, zoom, target size of the person |
| `command_interval_s` | `0.25` | maximum command rate |
| **detector** | | |
| `model_path`, `imgsz`, `conf`, `tracker` | `…/yolo11n_openvino_model`, `480`, `0.45`, `bytetrack.yaml` | detection and tracking (416 on a 2-core CPU) |
| **faces** | | |
| `model_name`, `model_root`, `providers`, `det_size` | `buffalo_s`, `…/insightface`, `[CPUExecutionProvider]`, `320` | face models |
| `match_threshold` | `0.45` | recognition threshold |
| `min_face_px`, `min_det_score`, `min_sharpness` | `40`, `0.6`, `30` | quality filters |
| `votes_required`, `unknown_after_observations` | `3`, `6` | per-track voting |
| `recheck_interval_s`, `max_faces_per_frame` | `0.4`, `2` | CPU budget |
| `unknown_dedupe_similarity`, `unknown_dedupe_window_s` | `0.5`, `3600` | unknown deduplication |
| `unknown_cluster_similarity` | `0.5` | visitor clustering |
| `adaptive_enabled`, `adaptive_min_score`, `adaptive_min_quality`, `adaptive_max_per_person`, `adaptive_novelty_max_similarity` | `true`, `0.6`, `0.35`, `5`, `0.85` | adaptive learning |
| `search_threshold` | `0.45` | search by face |
| **vision** | | |
| `process_fps`, `preview_fps`, `preview_width`, `frame_path` | `6`, `5`, `960`, `/run/jarvis/frame.jpg` | rates, preview |
| **decision** | | |
| `led_on_s`, `greeting`, `auth_window_s`, `voice_only_after_recognition`, `listen_after_recognition_s` | `5`, `Bonjour {first_name}`, `30`, `true`, `0` | recognition window: green LED + voice; other LED signals |
| `unknown_message`, `watchlist_message` | `null`, `null` | optional sentences |
| **audio** | | |
| `enabled`, `input_device`, `output_device`, `sample_rate`, `sound_trigger_rms` | `true`, `null`, `null`, `16000`, `0` | devices (`python -m sounddevice` lists them) |
| **wakeword** | | |
| `engine`, `oww_model`, `oww_dir`, `threshold` | `openwakeword`, `hey_jarvis_v0.1`, `…/openwakeword`, `0.5` | wake word |
| `porcupine_access_key`, `porcupine_keyword`, `porcupine_sensitivity` | empty, `jarvis`, `0.6` | Porcupine option |
| **stt** | | |
| `vosk_model_path`, `listen_timeout_s`, `max_utterance_s` | `…/vosk-model-small-fr-0.22`, `6`, `6` | speech recognition |
| **speaker** | | |
| `enabled`, `threshold`, `model_dir` | `false`, `0.35`, `…/spkrec-ecapa-voxceleb` | speaker verification |
| **tts** | | |
| `piper_model`, `cache_dir` | `…/fr_FR-siwis-medium.onnx`, `…/tts-cache` | speech synthesis |
| **commands** | | |
| `open_phrases`, `close_phrases`, `cancel_phrases` | 3, 3 and 2 phrases | Vosk grammar and intents |
| **hardware** | | |
| `garage`, `led_green`, `led_red`: `backend`, `device`, `channel`, `active_low` | `mock`, `null`, 1/2/3, `false` | outputs |
| `door_sensor`: `backend`, `device`, `line`, `active_means_closed` | `none`, `null`, `0`, `true` | door sensor |
| `pulse_ms`, `cooldown_s` | `500`, `5` | garage pulse |
| **storage** | | |
| `data_dir` | `/var/lib/jarvis` | data root |
| `unknown_retention_days`, `sighting_retention_days`, `event_retention_days` | `30`, `90`, `180` | GDPR retention |
| **simulation** | | |
| `loop`, `drive_relay`, `require_window`, `max_upload_mb` | `true`, `false` (DANGER), `true`, `100` | camera and voice simulation (§18) |
| **api** | | |
| `host`, `port`, `session_hours`, `cookie_secure`, `max_upload_mb` | `127.0.0.1`, `8000`, `12`, `true`, `15` | web server |
| **notifications** | | |
| `webhook_url`, `events`, `timeout_s` | `null`, 5 types, `5` | webhooks |
| **control** | | |
| `socket_path` | `/run/jarvis/core.sock` | API → core socket |

---

## 14. Operations, performance, tests

### 14.1 Operations

```bash
systemctl status jarvis-core jarvis-api          # service status
journalctl -u jarvis-core -f                     # live log
sudo systemctl restart jarvis-core               # after editing config.yaml
sudo -u jarvis sqlite3 /var/lib/jarvis/jarvis.db ".backup /root/jarvis-$(date +%F).db"   # online backup
./scripts/jarvis-regen-sbom.sh                          # up-to-date software inventory (SBOM)
```

To back up: `/etc/jarvis/` and `/var/lib/jarvis/`, excluding `models/` and `tts-cache/`, which are regenerated.

### 14.2 Measured and estimated performance

| Stage | Cost |
|---|---|
| YOLO11n OpenVINO + ByteTrack, imgsz 480 | 60 to 100 ms per frame on a Haswell i5 (89 ms measured on a loaded development PC) |
| SCRFD-500M + ArcFace per face region | 20 to 60 ms (59 ms measured) |
| H.264 720p decoding at 15 frames/s | about 10 % of one core |
| openWakeWord · Vosk (while listening) | about 5 % · about 20 % of one core |
| Piper medium | about 0.3 s of compute per second of speech, then cached |
| Full pipeline (test video, 6 people) | 10 to 15 frames/s analyzed on the development workstation |

Default target: 6 analyses per second. On an i5-4570T (2 cores): `vision.process_fps: 4` and `detector.imgsz: 416`.

### 14.3 Automated tests

201 tests (`pytest`), none of which needs a model or hardware. Main files:

| File | Tests | Scope |
|---|---|---|
| `tests/test_logic.py` | 35 | configuration, command grammar and parsing, relay protocols, PTZ controller, target selection, gallery, voting, decision rules |
| `tests/test_features.py` | 28 | access rules, migrations, sightings, clustering, watchlist, schedules, notifications, adaptive learning, CSV, statistics, face search, retroactive labeling, `threading.Thread` attribute guard |
| `tests/test_admin.py` | 13 | settings catalog and hot reload, audit with diffs and hash chain, sessions, idle timeout |
| `tests/test_api.py` | 9 | authentication, login rate limiting, CSRF, person and face lifecycle, control socket |
| `tests/test_secrets.py` | 7 | precedence parameter > environment > file, write-only secrets, token migration, webhook bearer |
| `tests/test_cli.py` | 6 | cmd2 shell commands and exit codes |
| `tests/test_scripts.py` | 6 | `jarvis-hwinfo` JSON/verdict, `jarvis-motd`, SSH banner, `jarvis-deploy.sh`, `jarvis-common.sh` error handling |
| `tests/test_recording.py` | 5 | pre-roll/post-roll clips, FIFO quota, time-lapse, Range requests |
| `tests/test_sysinfo.py` | 5 | hardware detection, auto-tuning, capabilities snapshot |
| `tests/test_version_cert.py` | 5 | single version source, `/api/me` version, bump rules, `jarvis-cert` local lifecycle and input validation |
| `tests/test_default_admin.py` | 3 | default admin/admin, forced password change, password policy |
| `tests/test_simulation.py` | 13 | domain of every settings group, wake word token, voice chain with fallback, simulated pulses never drive the relay (unless `drive_relay`), camera simulation marks events, upload validation (size, type, path traversal) |
| `tests/test_mqtt.py` | 2 | Home Assistant discovery, MQTT bridge against a real broker |

The Ansible deployment has its own end-to-end test (`deploy/ansible/tests/jarvis-container-test.sh`): an
Ubuntu 26.04 systemd container reached over SSH, full deployment with postflight checks, then an
idempotence re-run.

Lightweight environment: `pip install -r requirements-dev.txt`, then `PYTHONPATH=. pytest`. No model or hardware required.

The complete chain has also been validated **with the real models**, on a test video containing several faces:
1. detection, tracking, unknown faces, clustering, sightings with photo;
2. labeling of a cluster through the API and retroactive identification;
3. recognition on the next pass;
4. CSV export and search by face;
5. walkthrough of the interface in Chrome (Playwright), without any JavaScript error.

---

## 15. Known limitations

- **No liveness detection** (anti-spoofing): a good-quality photo can fool the face part. Countermeasures: voting over several frames, voice verification (`speaker.enabled`), the short recognition window (30 s), IR at night (most screens do not show up under IR). Possible evolution: a dedicated anti-spoofing model. A lightweight heuristic was deliberately ruled out: it would give a false sense of security.
- **Speaker verification does not detect a replayed** recording.
- **Adaptive learning**: a confusion with a score ≥ 0.6 would be learned (cap of 5, additions visible and removable).
- **Incremental clustering**: arrival order influences the clusters. Two clusters of the same person can coexist. Simply label both of them as the same person.
- **Door without sensor**: "open" on an open door closes it again (pulse input of the Novomatic).
- **Convenience system**, not certified access control: keep a second lock for access to the house.
- **GDPR**: strictly domestic use, camera limited to the property, visitors informed, consent of household members (see `docs/ARCHITECTURE.md` §6).

---

## 16. Deployment and operations tooling

### 16.1 Central version file (`jarvis/VERSION`)

- `jarvis/VERSION` is the **single source of truth** for the version (Semantic Versioning `MAJOR.MINOR.PATCH[-pre]`). `jarvis/__init__.py` reads it into `__version__`, and `pyproject.toml` declares `dynamic = ["version"]` with `[tool.setuptools.dynamic] version = {file = "jarvis/VERSION"}`.
- Consumers: `jarvis version` in the shell, `GET /api/me` (`"version"`, authenticated users only), the FastAPI app, `jarvis-cert --version` (via `jarvis_version` in `scripts/lib/jarvis-common.sh`), and every file header (`Project : jarvis-home (version: jarvis/VERSION)`).
- **Never edit it by hand**: `scripts/jarvis-bump-version.py patch|minor|major|X.Y.Z` (or `make bump PART=patch|minor|major|X.Y.Z`) validates SemVer, refuses to go backwards and moves the `[Unreleased]` section of `CHANGELOG.md` under a dated `[x.y.z]` heading. Options `--show` and `--dry-run`. Exit codes: 0 success, 2 usage error or invalid version, 4 VERSION or CHANGELOG missing, 5 version not greater than the current one.

### 16.2 TLS manager (`scripts/jarvis-cert.sh`, installed as `/usr/local/sbin/jarvis-cert`)

It manages the certificate served by nginx at the stable paths `/etc/jarvis/tls/jarvis.crt` (certificate + chain) and `/etc/jarvis/tls/jarvis.key`, in one of two modes remembered in `/etc/jarvis/tls/jarvis-cert.conf`:

| Mode | Use | Mechanism |
|---|---|---|
| `local` (default of the installers) | LAN only, no public domain | Private "Jarvis Local CA" (ECDSA P-384, 10 years, name-constrained to local names and private IPv4 ranges) signing a server certificate (ECDSA P-256, 397 days) for `.local`/`.lan`/`.home.arpa` names and LAN IPs. Import the CA once on client devices (`export-ca`). Revocation uses a real CA database and CRL. |
| `letsencrypt` | public domain name | certbot obtains an ECDSA certificate. The default **DNS-01** challenge needs **no inbound port** open on the Internet (Jarvis stays LAN-only); HTTP-01 (`--challenge http`) requires port 80 reachable from the Internet (nginx serves `/.well-known/acme-challenge/` from `/var/www/letsencrypt`). |

| Subcommand | Role |
|---|---|
| `issue --mode local [--name NAME]... [--ip IPV4]... [--days N] [--new-ca]` | issue a certificate signed by the local CA |
| `issue --mode letsencrypt --domain FQDN --email ADDR [--challenge dns\|http] [--dns-plugin NAME --dns-credentials FILE] [--staging] [--install-deps]` | obtain a Let's Encrypt certificate |
| `renew [--force] [--days-before N]` | renew when close to expiry (default 30 days) |
| `status [--cert FILE] [--warn-days N] [--remote HOST[:PORT]] [--json] [--no-revocation]` | dates, remaining time, revocation, chain (read-only, no root needed) |
| `revoke [--reason unspecified\|keyCompromise\|superseded\|cessationOfOperation] [--yes]` | revoke the current certificate |
| `export-ca [--out FILE]` | export the local CA certificate for the clients |

Global options: `--tls-dir DIR` (env `JARVIS_TLS_DIR`), `--no-reload` (env `JARVIS_CERT_NO_RELOAD=1`), `--yes` (env `ASSUME_YES=1`), `--no-color`. Root is required for any change in `/etc/jarvis/tls`.

Exit codes: 0 success / certificate valid, 1 runtime failure, 2 usage error, 3 root required, 4 missing prerequisite, 5 invalid configuration or option, 6 network failure (ACME, CRL/OCSP), 7 certificate unusable (expired, not yet valid, revoked, key mismatch, bad chain), 8 valid but expiring within `--warn-days` (default 30), 130 interrupted.

**Automatic renewal**: `deploy/systemd/jarvis-cert-renew.timer` fires twice a day (04:17 and 16:17, `RandomizedDelaySec=1h`, `Persistent=true`) and starts the oneshot `jarvis-cert-renew.service`, which runs `jarvis-cert renew --days-before 30` then `jarvis-cert status --warn-days 7`: an invalid result fails the unit (visible in `systemctl --failed` and in the MOTD).

### 16.3 Web front end: nginx and Anubis

- `deploy/nginx/jarvis.conf` is identical on every host. The two host-specific values live in generated snippets (templates in `deploy/nginx/snippets/`): `/etc/nginx/snippets/jarvis-upstream.conf` (`upstream jarvis_front`: the API `127.0.0.1:8000` by default, Anubis `127.0.0.1:8923` when deployed by Ansible) and `/etc/nginx/snippets/jarvis-server-name.conf` (`server_name`, must match the certificate names).
- Path: client → nginx :443 → `jarvis_front` → API. `/api/stream.mjpg` (no buffering, 1 h read timeout) and `/metrics` go straight to the API; `/api/login` is rate-limited (`limit_req`, 429) and then goes through `jarvis_front`.
- **Anubis** (`anubis@jarvis.service`, files in `deploy/anubis/`): anti-bot proxy with a proof-of-work challenge (`DIFFICULTY=4`), listening on `127.0.0.1:8923` and forwarding to `http://127.0.0.1:8000`. Its policy `jarvis.botPolicies.yaml` denies pathological clients, AI crawlers and agents, headless browsers, search engines and link-preview bots (HTTP 403), allows `/metrics`, challenges browser-like clients once (cookie), and passes other non-browser clients through (the API still requires a session or a token). The Ed25519 signing key `/etc/anubis/jarvis.key` is handed over as a systemd credential by the drop-in `anubis@jarvis.service.d/10-jarvis.conf`.
- **No indexing**: nginx serves `/robots.txt` itself (`User-agent: *` / `Disallow: /`), adds `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noimageindex, notranslate` to every response, and a `map` on `$http_user_agent` (`$jarvis_denied_bot`) returns **403** to AI crawlers/agents (GPTBot, ClaudeBot, CCBot, PerplexityBot, Bytespider…) and search-engine robots (Googlebot, Bingbot…) before they reach Anubis.

### 16.4 Capability snapshot and SSH MOTD

- At startup, the core detects the hardware (`detect_capabilities`), applies the automatic performance choices (`apply_auto_performance`), then `write_capabilities_snapshot` (`jarvis/core/sysinfo.py`) publishes `/run/jarvis/capabilities.json` next to the control socket: `{"ts", "capabilities", "performance"}` (CPU model and flags, GPUs, OpenVINO/ONNX devices, VAAPI codecs). The file is written atomically, world-readable (mode 0644, no secret), and a failure is only logged.
- `deploy/motd/jarvis-motd` (installed as `/usr/local/bin/jarvis-motd`, called at every SSH login by the update-motd hook `/etc/update-motd.d/10-jarvis`, capped at 5 s) prints the banner, live metrics, the hardware support read from `capabilities.json` (or probed directly), the state and resource usage of the Jarvis units, the TLS certificate and the security status. It always exits 0 (2 on a usage error): a MOTD must never block a login.

### 16.5 Hardware inventory (`scripts/jarvis-hwinfo.sh`)

`jarvis-hwinfo [--section NAME]... [--json] [--bench] [--no-color]` inventories the machine before or after installation (no Jarvis component needed): system, CPU (ISA extensions such as AVX2, AVX-512, VNNI), NPU, GPU (VAAPI, OpenCL, Vulkan, OpenVINO/ONNX devices), memory, storage (SMART, optional 256 MiB sequential benchmark), network, USB (recognized Jarvis modules: ReSpeaker, CH340 relay, FT232R…), audio and sensors. It ends with a suitability verdict against `REQUIREMENTS.md`. Exit codes: 0 requirements met, 2 usage error, 8 machine below a Jarvis requirement.

### 16.6 Shared bash library (`scripts/lib/jarvis-common.sh`)

Sourced (never executed) by every Jarvis shell script, installed as `/usr/local/lib/jarvis/jarvis-common.sh`. It provides strict mode (`set -Eeuo pipefail`) with an ERR trap reporting the failing command, file and line; colorized logging (`log_info`, `log_ok`, `log_warn`, `log_error`, `log_step`, `log_debug`), disabled when stderr is not a terminal, with `NO_COLOR` or `JARVIS_COLOR=never`; `die MESSAGE [CODE]`; `require_root`, `require_cmd`, `require_file`; LIFO cleanup registration (`on_exit`); `confirm`, `jarvis_version`, `is_true`. Standard exit codes shared by all scripts: 0 `E_OK`, 1 `E_RUNTIME`, 2 `E_USAGE`, 3 `E_PRIV`, 4 `E_DEPS`, 5 `E_CONFIG`, 6 `E_NETWORK`, 7 `E_STATE`, 8 `E_WARN`, 130 interrupted. `JARVIS_DEBUG=1` enables `log_debug` and xtrace.

### 16.7 Ansible deployment (`deploy/ansible/`)

The reference deployment of a host is the Ansible playbook in `deploy/ansible/` (`site.yml`, `jarvis-deploy.sh`), which installs Jarvis, nginx, Anubis, TLS (`jarvis_tls_mode: local` by default, `letsencrypt` with the DNS-01 challenge by default), the firewall, fail2ban, hardening, the MOTD and monitoring. The step-by-step procedure is in `INSTALL.md`, and the playbook reference (variables, TLS modes, roles, troubleshooting) is in `deploy/ansible/README.md`.

## 17. License plate recognition and vehicle automation

Disabled by default (`plates.enabled`). When it is enabled, Jarvis reads the license plates of
the vehicles in view and can open, and optionally close, the garage for registered plates.
Every read and every decision is traced in the event log (`plate_*` events, garage pulses with
`source: plate`), in the `plate_reads` table (with the vehicle snapshot) and in the service log.

### 17.1 Pipeline

1. **Detection:** the person detector also tracks the COCO vehicle classes car, motorcycle, bus
   and truck (`plates.vehicle_classes`), with the same ByteTrack instance. Persons keep driving
   the PTZ and the face recognition, and vehicles go to the plate reader. The preview draws
   vehicles in orange, with their plate and direction.
2. **Reading:** `PlateReader` (`jarvis/vision/plates.py`) handles one read per vision cycle at
   most.
   - **Which vehicle:** the largest vehicle that is due (`read_interval_s`) and at least
     `min_vehicle_px` wide.
   - **How:** its crop goes to fast-alpr, a YOLOv9 plate detector followed by the CCT "global"
     OCR, which also predicts the country or region. It runs on ONNX Runtime on the CPU, in about
     30 ms per read on a recent CPU, and several times that on the Haswell.
3. **Confirmation:** the plate is confirmed after `votes_required` identical normalized reads
   (default 2), each with a mean character confidence of at least `min_confidence` (default 0.80).
   Only the confirming read keeps its snapshot, stored under `/var/lib/jarvis/plates/YYYYMMDD/`.
4. **Direction:** it comes from the vehicle box.
   - Area growth of at least `approach_growth` (×1.15) over 2 s means **approaching**.
   - Shrinkage to `leave_shrink` (×0.85) or less means **leaving**.
   - A vehicle unseen for 3 s is **gone**.

**Normalization** (`jarvis/core/plates.py`): the plate goes through Unicode NFKC and upper case,
and only letters and digits of any script are kept. "AB-123-CD", "ab 123 cd" and "AB·123·CD" are
the same plate. Comparison is exact: there is no O/0 or I/1 tolerance, which would widen the set
of plates that open the garage.

### 17.2 Decisions (`DecisionEngine._on_vehicle`)

| Situation | Conditions | Action and trace |
|---|---|---|
| Plate confirmed | — | `plate_reads` row + `plate_read` event (status known / unknown / disabled / expired); notification `plate_recognized`, or `plate_unknown` / `plate_refused` (`unknown_notify`) |
| Known vehicle **approaching** | `auto_open`; plate enabled and not expired; the linked person (if any) has `can_open_garage` and is inside their access rules; **door sensor = closed** | pulse (`garage_pulse`, `source: plate`), read action `opened`; any pending close is cancelled (`garage_close_cancelled`) |
| Door open already | — | no pulse (it would **close** the single-button motor): `plate_open_refused` reason `already_open` |
| Door state unknown | no door sensor | no pulse: `plate_open_refused` reason `door_state_unknown` |
| Known vehicle **leaving**, then **gone** | `auto_close` (opt-in) | `garage_close_scheduled`; the close runs once the scene (no person, no vehicle) stayed clear for `close_delay_s`, and any presence restarts the countdown |
| Pending close due | **door sensor = open** | pulse, read action `closed`, `garage_closed_vehicle_left`; otherwise `plate_close_refused` (`already_closed`, `door_state_unknown`, or `scene_never_clear` after `close_give_up_s`) |

Every refusal is logged once per vehicle and reason. The other reasons are `plate_unknown`,
`plate_disabled`, `plate_expired`, `person_not_allowed`, `schedule_<reason>`,
`auto_open_disabled` and `auto_close_disabled`.

> **DANGER — automatic closing.** An unattended closing door can injure a person or an animal, or
> damage a vehicle. `plates.auto_close` is therefore off by default. Enable it only when:
> - the Novomatic obstacle detection (force limitation) is tested and working;
> - a safety photocell is fitted across the opening;
> - the camera covers the whole door area, because the "clear scene" check only sees what the
>   camera sees.

### 17.3 Registry, API and interface

- **Tables:** `plates` holds the plate (normalized, unique), display text, country, label,
  optional linked person, enabled flag and expiration date. `plate_reads` holds the confirmed
  reads.
- **API:**
  - `GET/POST /api/plates`, `PATCH/DELETE /api/plates/{id}` (audited: `plate_added`,
    `plate_updated` with the before/after diff, `plate_deleted`);
  - `GET /api/plate-reads` (date range, daily time window, plate fragment, status).
- **Vehicles page:** the registry (add, enable or disable, delete) and the read history, with the
  snapshot, direction and action.
- **Settings:** the License plates group; retention in Data retention (GDPR),
  `plate_read_retention_days`.
- **Hot changes:** registry changes apply immediately, because the decision engine reads the
  registry at each read. Enabling or disabling the feature requires a core restart.

### 17.4 Models and limits

- **Models:** `jarvis setup-models` downloads the two ONNX models once, when `plates.enabled` is
  true. They go into `/var/lib/jarvis/.cache/open-image-models` and `…/fast-plate-ocr`, so plate
  reading then runs **offline**.
  - **Libraries:** fast-alpr, fast-plate-ocr and open-image-models are MIT-licensed.
  - **Model weights:** their licence is not stated on the project pages (see
    `THIRD-PARTY-NOTICES.md`).
- **Camera placement:** plates need pixels. Aim for a plate at least **100–130 px wide** in the
  image, at the reading distance.
  - **Viewing angle:** at most about 30° horizontal and vertical.
  - **At night:** IR, and a short shutter to avoid motion blur.
  - **Second camera:** a camera placed for faces (height, angle) is often poorly placed for
    plates; a dedicated fixed camera may be needed (see `docs/CAMERA-SELECTION.md`).
- **Spoofing:** a plate is not a secret, and anyone can copy one. Plate-based opening is a
  convenience, not authentication.
  - Limit each registered plate with an expiration date and the linked person's access rules.
  - Keep `unknown_notify` on.
  - Review the plate reads regularly.

---

## 18. Settings tabs and simulation

### 18.1 Tabbed settings

The Settings page has two navigation levels. Each **domain tab** groups the parameter groups of
one domain; each group is a **sub-tab**. The mapping is `DOMAINS` in `jarvis/config/catalog.py`,
and a test checks that every group belongs to exactly one domain.

| Domain tab | Sub-tabs (groups) |
|---|---|
| Camera & vision | Camera stream, PTZ camera, Person detection, Performance |
| Recognition | Face recognition, Unknown visitors, Adaptive learning, Search by face, License plates |
| Access & voice | Decision and access, Voice |
| Recording | Video recording, Time-lapse |
| Integrations | Smart home (MQTT), Notifications, Monitoring (Grafana) |
| System | Web interface, Logging, Data retention (GDPR), Secrets |
| Simulation | Camera simulation, Voice simulation |

- **Unsaved edits:** a dot marks each domain tab that holds unsaved edits. The save bar applies
  them all at once, whatever the tab.
- **Remembered tab:** the last opened sub-tab is kept in the browser (`localStorage`,
  `jarvis.settings.tab`).
- **Cards:** the camera card (stream test, annotated preview) follows Camera stream, the
  monitoring card follows Monitoring, and the simulation cards follow their sub-tabs.

### 18.2 Camera simulation

Plays an uploaded file **instead of the RTSP stream**, through the whole real pipeline: person
detection, tracking, face recognition, plates, preview and recordings.

- **Inputs:** a video (`.mp4`, `.mkv`, `.mov`, `.avi`, `.m4v`; `.webm` counts as a recording) is paced at its own frame
  rate and looped if `simulation.loop` is on; a photo (`.jpg`, `.png`…) is repeated at 5 frames/s.
- **Switching:** `RtspCamera.simulate(path)` switches the source without restarting the core;
  "Back to the camera stream" (or `{file: null}`) returns to RTSP.
- **Not saved:** the simulation is not persisted. A core restart goes back to the camera.
- **Files:** uploads go to `storage.simulation_dir` (`/var/lib/jarvis/simulation`). The API
  accepts a bare file name only (no path), checks the extension and caps the size
  (`simulation.max_upload_mb`, 100 MB; nginx allows 100 MB on this route only).

### 18.3 Voice simulation

Tests the voice chain **without a microphone** (`jarvis/voice/simulate.py`):

1. **Recognize a person:** `simulate_face` injects a `FaceRecognized` event for the chosen
   person. As with a real recognition, it opens the recognition window (green LED, greeting).
2. **Say a command**, typed or recorded:
   - **typed:** the text must start with the wake word ("Jarvis, ouvre la porte du garage").
     The command part is synthesized by Piper, then transcribed by Vosk with the live restricted
     grammar. Without the Piper or Vosk model, the text is parsed directly (the result says so).
   - **recording:** an uploaded audio file (any format FFmpeg reads) goes through the configured
     wake word engine, then Vosk transcribes what follows the wake word.
3. **Result:** each stage is shown (wake word heard, speech recognition, transcript, command,
   outcome), with the decisions logged by the engine and what Jarvis would have said.

The live rules apply: no wake word → ignored; with `simulation.require_window` (default) and
`decision.voice_only_after_recognition`, a command outside a recognition window is ignored
("microphone asleep"). Turn `require_window` off to test the voice chain alone.

### 18.4 Safety: the relay

> **A garage pulse decided during a simulation does not drive the relay.** Simulated faces and
> voice commands carry `simulated=True`, and every event produced while the camera plays a file
> is also treated as simulated. The decision engine logs `garage_pulse_simulated` (with the
> intent and the person) and lights the green LED, but sends nothing to the relay.
>
> `simulation.drive_relay` (DANGER, off by default) lifts this guard for an end-to-end test
> with the real door. Turn it back off afterwards.

Every simulation action is audited with its author (`simulation_*` events).

