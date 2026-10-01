# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : docker/Dockerfile
# Purpose : Container image for the Jarvis core and API services
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
# Single image shared by the core and api services (only the command differs).
# Debian 13 "trixie" (current Debian stable; Debian 12 standard security support ended 2026-07-11).
FROM python:3.14-slim-trixie

# System libraries: compiler toolchain (native wheels), ffmpeg (RTSP decoding), PortAudio/ALSA
# (audio I/O), OpenGL/GLib/OpenMP runtimes (OpenCV, ONNX Runtime).
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ffmpeg libportaudio2 libasound2-plugins libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# CPU-only PyTorch first (avoids pulling the multi-GB CUDA wheels), then insightface, which must be
# built against numpy<2 without build isolation.
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir "numpy<2" cython \
 && pip install --no-cache-dir --no-build-isolation insightface==0.7.3

# Install the jarvis package with the requested extras (default: core).
WORKDIR /src
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
ARG EXTRAS=core
RUN pip install --no-cache-dir ".[${EXTRAS}]"

# Run as an unprivileged system user, same layout as the bare-metal install.
RUN useradd --system --home-dir /var/lib/jarvis jarvis && install -d -o jarvis /var/lib/jarvis /run/jarvis
ENV HOME=/var/lib/jarvis YOLO_CONFIG_DIR=/var/lib/jarvis/.config/Ultralytics \
    JARVIS_CONFIG=/etc/jarvis/config.yaml PYTHONUNBUFFERED=1
USER jarvis
ENTRYPOINT ["jarvis"]
CMD ["core"]
