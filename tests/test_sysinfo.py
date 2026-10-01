# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : tests/test_sysinfo.py
# Purpose : Hardware detection driven auto-tuning and resource metrics
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""The auto mode picks the best devices for simulated hardware profiles and never overrides a
setting chosen by the operator."""

import pytest

from jarvis.config.settings import Settings
from jarvis.core.sysinfo import ResourceSampler, apply_auto_performance, choose, render_resources

HASWELL_TINY = {"cpu": {"physical_cores": 4, "flags": ["avx", "avx2", "fma"]}, "openvino_devices": {"CPU": "i5-4590T"},
                "onnx_providers": ["CPUExecutionProvider"], "vaapi_decode": ["H264", "MPEG2"]}
I5_4570T = {**HASWELL_TINY, "cpu": {"physical_cores": 2, "flags": ["avx2"]}}
RECENT_IGPU = {"cpu": {"physical_cores": 6, "flags": ["avx2", "avx_vnni"]},
               "openvino_devices": {"CPU": "i5-1240P", "GPU": "Intel Iris Xe"},
               "onnx_providers": ["OpenVINOExecutionProvider", "CPUExecutionProvider"], "vaapi_decode": ["H264", "HEVC"]}
NVIDIA = {"cpu": {"physical_cores": 8, "flags": ["avx2"]}, "openvino_devices": {"CPU": "x"},
          "onnx_providers": ["CUDAExecutionProvider", "CPUExecutionProvider"], "vaapi_decode": []}


def test_choices_per_profile():
    c = choose(HASWELL_TINY)
    assert c["detector.device"] == "intel:cpu" and c["faces.providers"] == ["CPUExecutionProvider"]
    assert c["camera.hw_accel"] is True and c["_threads"] == 3
    c = choose(I5_4570T)
    assert c["detector.imgsz"] == 416 and c["vision.process_fps"] == 4.0
    c = choose(RECENT_IGPU)
    assert c["detector.device"] == "intel:gpu" and c["faces.providers"][0] == "OpenVINOExecutionProvider"
    assert c["vision.process_fps"] == 10.0
    c = choose(NVIDIA)
    assert c["faces.providers"][0] == "CUDAExecutionProvider" and c["camera.hw_accel"] is False


def test_auto_never_overrides_the_operator(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "2")
    s = Settings()
    s.vision.process_fps = 6.0
    r = apply_auto_performance(s, I5_4570T, user_keys={"vision.process_fps"})
    assert s.vision.process_fps == 6.0 and "vision.process_fps" in r["kept"]
    assert s.detector.imgsz == 416 and s.detector.device == "intel:cpu" and s.camera.hw_accel is True
    assert "OMP_NUM_THREADS" not in r["applied"]                      # the unit's value wins


def test_manual_mode_changes_nothing_but_resolves_auto_values():
    s = Settings()
    s.performance.mode = "manual"
    r = apply_auto_performance(s, RECENT_IGPU, user_keys=set())
    assert r["applied"] == {} and s.detector.imgsz == 480
    assert s.detector.device == "intel:gpu" and s.faces.providers != ["auto"]   # "auto" still resolved


def test_resource_sample_renders():
    psutil = pytest.importorskip("psutil")
    assert psutil
    m = ResourceSampler().sample()
    assert 0 <= m["cpu"]["current"] <= 100 and m["memory"]["total"] > 0
    rows = render_resources(m)
    assert any(r[0] == "CPU" for r in rows) and any(r[0] == "RAM" for r in rows)


def test_capabilities_snapshot_is_atomic_and_readable(tmp_path):
    import json
    import stat

    from jarvis.core.sysinfo import write_capabilities_snapshot

    target = tmp_path / "capabilities.json"
    write_capabilities_snapshot(target, {"cpu": {"model": "i5-4570T", "flags": ["avx2"]}}, {"why": ["x"]})
    data = json.loads(target.read_text())
    assert data["capabilities"]["cpu"]["flags"] == ["avx2"] and data["performance"]["why"] == ["x"]
    assert stat.S_IMODE(target.stat().st_mode) == 0o644 and not (tmp_path / "capabilities.tmp").exists()
    write_capabilities_snapshot(tmp_path / "missing" / "c.json", {}, {})     # logged, never raised
