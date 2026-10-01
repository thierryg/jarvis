# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/web/probe.py
# Purpose : Camera stream test (ffprobe) for the settings page: codec, resolution, rate, advice
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Stream probe used by ``POST /api/camera/probe``.

``ffprobe`` opens the RTSP URL over TCP (the transport Jarvis itself uses), reads the stream
headers and reports the video codec, resolution, frame rate and profile, plus the audio codec.
The result is turned into Jarvis-specific advice: H.265 cannot be decoded by the Haswell GPU,
a low resolution limits face recognition, a high rate wastes CPU. Credentials never appear in
the returned text: they are masked in ffprobe's error messages.

The module has no dependency beyond the standard library (the API process does not load OpenCV).
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from fractions import Fraction

# user:password@ in any URL, and ffprobe's own echo of the URL.
_CREDENTIALS = re.compile(r"(?i)(rtsps?|https?)://[^@/\s]+@")


def mask_credentials(text: str) -> str:
    """Replace ``user:password@`` in every URL of ``text`` by ``***@``."""
    return _CREDENTIALS.sub(lambda m: f"{m.group(1)}://***@", text or "")


def _rate(value: str | None) -> float | None:
    """Convert an ffprobe rational such as ``"25/1"`` or ``"30000/1001"`` to frames per second."""
    try:
        r = Fraction(value or "")
        return round(float(r), 2) if r > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def advice(video: dict | None) -> list[str]:
    """Jarvis-specific warnings for a probed video stream (pure function, unit-tested).

    Args:
        video: ``{"codec", "width", "height", "fps", "profile"}`` or ``None`` when no video.

    Returns:
        Human-readable warnings (US English), empty when the stream suits Jarvis.
    """
    if not video:
        return ["No video stream found at this URL."]
    out = []
    codec = (video.get("codec") or "").lower()
    if codec in ("hevc", "h265"):
        out.append("H.265/HEVC: the Intel HD 4600 cannot decode it in hardware, the CPU will; select H.264 "
                   "for this stream in the camera settings.")
    elif codec not in ("h264",):
        out.append(f"Codec {codec or 'unknown'}: H.264 is recommended.")
    width = video.get("width") or 0
    if width and width < 1280:
        out.append(f"{width} px wide: faces will be small; use at least 1280 px (720p) for recognition, "
                   "1920 px (1080p) or more for faces beyond 3 m.")
    if width and width > 2688:
        out.append(f"{width} px wide: more than 4 MP costs CPU on a Haswell mini-PC; prefer a 2-4 MP stream.")
    fps = video.get("fps") or 0
    if fps and fps > 15:
        out.append(f"{fps:g} fps: Jarvis analyzes about 6 frames per second; 10-15 fps is enough and saves bandwidth.")
    return out


async def probe(url: str, timeout_s: float = 15.0) -> dict:
    """Probe an RTSP stream with ffprobe (over TCP, like the core).

    Args:
        url: Full URL, credentials included (they are never returned).
        timeout_s: Overall limit for ffprobe.

    Returns:
        ``{"ok": bool, "elapsed_s": float, "video": {...} | None, "audio": {...} | None,
        "warnings": [...], "error": str | None}``.
    """
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"ok": False, "elapsed_s": 0.0, "video": None, "audio": None, "warnings": [],
                "error": "ffprobe is not installed (apt install ffmpeg)"}
    cmd = [ffprobe, "-v", "error", "-rtsp_transport", "tcp", "-timeout", str(int(timeout_s * 1_000_000)),
           "-show_entries", "stream=codec_type,codec_name,profile,width,height,avg_frame_rate,r_frame_rate",
           "-of", "json", url]
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                                                start_new_session=True)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout_s + 5)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return {"ok": False, "elapsed_s": round(time.monotonic() - start, 2), "video": None, "audio": None,
                "warnings": [], "error": f"no answer within {timeout_s:g} s (address, port, firewall?)"}
    elapsed = round(time.monotonic() - start, 2)
    if proc.returncode != 0:
        msg = mask_credentials(err.decode(errors="replace")).strip().splitlines()
        text = msg[-1] if msg else f"ffprobe exited with code {proc.returncode}"
        if "401" in text or "Unauthorized" in text:
            text += " (check Secrets > Camera account / password)"
        return {"ok": False, "elapsed_s": elapsed, "video": None, "audio": None, "warnings": [], "error": text}
    streams = json.loads(out or b"{}").get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = None if v is None else {
        "codec": v.get("codec_name"), "profile": v.get("profile"), "width": v.get("width"), "height": v.get("height"),
        "fps": _rate(v.get("avg_frame_rate")) or _rate(v.get("r_frame_rate")),
    }
    audio = None if a is None else {"codec": a.get("codec_name")}
    return {"ok": video is not None, "elapsed_s": elapsed, "video": video, "audio": audio,
            "warnings": advice(video), "error": None if video else "no video stream"}
