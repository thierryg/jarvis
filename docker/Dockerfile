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
# Same platform and layout as the target (Ansible "jarvis" role, scripts/jarvis-install.sh):
# Ubuntu Server 26.04 LTS, Python 3.11 installed by uv in /opt/jarvis/python, virtualenv
# /opt/jarvis/venv. The uv version is jarvis_uv_version of deploy/ansible/group_vars/all/main.yml.
FROM ghcr.io/astral-sh/uv:0.12.21 AS uv

FROM ubuntu:26.04

# System libraries: compiler toolchain (native wheels), ffmpeg (RTSP decoding), PortAudio/ALSA
# (audio I/O), OpenGL/GLib/OpenMP runtimes (OpenCV, ONNX Runtime), CA bundle (model downloads).
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates ffmpeg libportaudio2 libasound2-plugins libgl1 \
      libglib2.0-0t64 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/jarvis/python UV_NO_CACHE=1 UV_NO_PROGRESS=1 \
    VIRTUAL_ENV=/opt/jarvis/venv PATH=/opt/jarvis/venv/bin:$PATH
RUN uv venv --python 3.11 "$VIRTUAL_ENV"

# CPU-only PyTorch first (avoids pulling the multi-GB CUDA wheels), then insightface, which must be
# built against numpy<2 without build isolation.
RUN uv pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
 && uv pip install "numpy<2" cython \
 && uv pip install --no-build-isolation insightface==0.7.3

# Install the jarvis package with the requested extras (default: core).
WORKDIR /src
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
ARG EXTRAS=core
RUN uv pip install ".[${EXTRAS}]"

# Packaging tools: the setuptools pulled in above and the pip/setuptools bundled with the uv
# interpreter carry known CVEs (vendored wheel, jaraco.context, urllib3, msgpack). uv does every
# install, so pip is removed from the interpreter and setuptools/wheel are kept current; this keeps
# the Trivy scan of the CI "container" job clean.
RUN uv pip install --upgrade setuptools wheel \
 && interp="$(ls -d /opt/jarvis/python/cpython-3.11*/bin/python3.11)" \
 && uv pip uninstall --python "$interp" --break-system-packages pip \
 && uv pip install --python "$interp" --break-system-packages --upgrade setuptools

# Run as an unprivileged system user, same layout as the bare-metal install.
RUN useradd --system --home-dir /var/lib/jarvis jarvis \
 && install -d -o jarvis /var/lib/jarvis /var/lib/jarvis/.config /run/jarvis
ENV HOME=/var/lib/jarvis YOLO_CONFIG_DIR=/var/lib/jarvis/.config/Ultralytics \
    JARVIS_CONFIG=/etc/jarvis/config.yaml PYTHONUNBUFFERED=1
USER jarvis
ENTRYPOINT ["jarvis"]
CMD ["core"]
