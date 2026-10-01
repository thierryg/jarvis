# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/provisioning/models.py
# Purpose : One-time download/export of every AI model used by Jarvis
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Model download and preparation (run once, with Internet access).

Afterwards everything runs fully offline. Each step is idempotent: it is
skipped when its target already exists (or, for library-managed caches, lets
the library reuse what is on disk).
"""

from __future__ import annotations

import logging
import os
import shutil
import urllib.request
import zipfile
from pathlib import Path

from jarvis.config.settings import Settings

log = logging.getLogger(__name__)

VOSK_URL = "https://alphacephei.com/vosk/models/{name}.zip"
PIPER_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/{lang}/{locale}/{voice}/{quality}/{file}"


def _download(url: str, dest: Path) -> None:
    """Download ``url`` to ``dest`` atomically (via a ``.part`` file then rename).

    Args:
        url: Source URL.
        dest: Destination file (parent directories are created).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s", url)
    tmp = dest.with_suffix(dest.suffix + ".part")
    # url is built from the https:// constants of this module (Vosk, Piper), never from user input.
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310  # nosec B310
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def setup_vosk(settings: Settings) -> None:
    """Download and unpack the Vosk model named after ``stt.vosk_model_path``'s last component.

    Args:
        settings: Application settings.
    """
    target = Path(settings.stt.vosk_model_path)
    if target.exists():
        return
    archive = target.parent / f"{target.name}.zip"
    _download(VOSK_URL.format(name=target.name), archive)
    with zipfile.ZipFile(archive) as z:
        z.extractall(target.parent)
    archive.unlink()


def setup_piper(settings: Settings) -> None:
    """Download the Piper voice (``.onnx`` + ``.onnx.json``) from the rhasspy/piper-voices repo.

    The Hugging Face path is derived from the file name
    ``<locale>-<voice>-<quality>.onnx`` (e.g. ``fr_FR-siwis-medium.onnx``).

    Args:
        settings: Application settings.
    """
    model = Path(settings.tts.piper_model)
    if model.exists():
        return
    voice_file = model.name                        # fr_FR-siwis-medium.onnx
    locale, voice, quality = voice_file.removesuffix(".onnx").split("-")
    for f in (voice_file, voice_file + ".json"):
        _download(PIPER_URL.format(lang=locale.split("_")[0], locale=locale, voice=voice, quality=quality, file=f),
                  model.parent / f)


def setup_yolo(settings: Settings) -> None:
    """Fetch the YOLO weights and export them to OpenVINO at ``detector.model_path``.

    The base model name is the target directory name without the
    ``_openvino_model`` suffix (e.g. ``yolo11n``).

    Args:
        settings: Application settings.
    """
    target = Path(settings.detector.model_path)
    if target.exists():
        return
    from jarvis.vision import ensure_yolo_config_dir

    ensure_yolo_config_dir()
    from ultralytics import YOLO

    base = target.name.removesuffix("_openvino_model")  # yolo11n
    target.parent.mkdir(parents=True, exist_ok=True)
    # Ultralytics downloads the .pt and writes the export into the CWD.
    cwd = os.getcwd()
    os.chdir(target.parent)
    try:
        # export() returns a path relative to the CWD: resolve it before changing back.
        exported = Path(YOLO(f"{base}.pt").export(format="openvino", imgsz=settings.detector.imgsz,
                                                  half=False)).resolve()
    finally:
        os.chdir(cwd)
    if exported.resolve() != target.resolve():
        shutil.move(str(exported), target)


def setup_insightface(settings: Settings) -> None:
    """Fetch the InsightFace model pack by instantiating the face engine.

    Args:
        settings: Application settings.
    """
    from jarvis.vision.faces import FaceEngine

    FaceEngine(settings.faces)  # downloads the buffalo_* pack into model_root on first call


def setup_openwakeword(settings: Settings) -> None:
    """Download the openWakeWord feature models (and the named wake word model, if not a custom file).

    Args:
        settings: Application settings.
    """
    if settings.wakeword.engine != "openwakeword":
        return
    from openwakeword.utils import download_models

    # Use the data directory: site-packages is read-only under systemd.
    names = [] if settings.wakeword.oww_model.endswith(".onnx") else [settings.wakeword.oww_model]
    download_models(names, target_directory=settings.wakeword.oww_dir)


def setup_speechbrain(settings: Settings) -> None:
    """Fetch the ECAPA speaker model by instantiating the verifier (if speaker ID is enabled).

    Args:
        settings: Application settings.
    """
    if not settings.speaker.enabled:
        return
    from jarvis.voice.speaker import SpeakerVerifier

    SpeakerVerifier(settings.speaker)


def setup_plates(settings: Settings) -> None:
    """Download the license plate models (fast-alpr detector + OCR) into the account cache.

    fast-alpr downloads its ONNX files on first use into ``~/.cache/open-image-models`` and
    ``~/.cache/fast-plate-ocr`` (``/var/lib/jarvis/.cache`` for the ``jarvis`` account): one read
    on a blank image does it now, so the appliance never needs the Internet at run time. Skipped
    when plate recognition is disabled.

    Args:
        settings: Application settings.
    """
    if not settings.plates.enabled:
        log.info("License plate recognition disabled: models not downloaded")
        return
    import numpy as np

    from jarvis.vision.plates import PlateEngine

    PlateEngine(settings.plates).read(np.zeros((384, 640, 3), np.uint8))
    log.info("License plate models ready (%s, %s)", settings.plates.detector_model, settings.plates.ocr_model)


def setup_models(settings: Settings) -> None:
    """Run every provisioning step in order.

    Args:
        settings: Application settings.
    """
    for step in (setup_vosk, setup_piper, setup_yolo, setup_insightface, setup_openwakeword, setup_speechbrain,
                 setup_plates):
        log.info("== %s", step.__name__)
        step(settings)
    log.info("Models ready.")
