#!/usr/bin/env python3
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/jarvis-gen-sbom.py
# Purpose : Generate the CycloneDX 1.6 SBOM (Python packages, AI models, system)
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Generate the Jarvis CycloneDX 1.6 SBOM: Python packages + AI models + system components.

1. ``cyclonedx-py environment`` inventories the virtualenv (packages, versions, licenses, purl);
2. this script adds the application itself, the AI models (SHA-256 digests of the actual files,
   source and license) and the system components in use (Python, FFmpeg, nginx, SQLite);
3. it also writes a human-readable CSV summary.

Usage (on the installed machine):
    python3 scripts/jarvis-gen-sbom.py --python /opt/jarvis/venv/bin/python \
        --models /var/lib/jarvis/models --out docs/sbom/jarvis-sbom.cdx.json
Prerequisites: ``uvx`` (uv), or ``cyclonedx-py`` on the PATH (pip package cyclonedx-bom).
Exit status: 0 on success; non-zero if cyclonedx-py is unavailable or fails.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
import tomllib
import uuid
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Models: directory/file relative to the models directory -> metadata verified against the upstream source.
MODELS = [
    {"name": "yolo11n", "version": "11 (export OpenVINO FP32)", "path": "yolo11n_openvino_model",
     "supplier": "Ultralytics", "license": "AGPL-3.0-only",
     "url": "https://github.com/ultralytics/ultralytics", "task": "person detection"},
    {"name": "insightface-buffalo_s", "version": "buffalo_s (insightface 0.7)", "path": "insightface/models/buffalo_s",
     "supplier": "InsightFace (deepinsight)", "license_name": "InsightFace pretrained models: non-commercial research only",
     "url": "https://github.com/deepinsight/insightface/tree/master/python-package",
     "task": "face detection (SCRFD) and face embeddings (ArcFace 512-d)"},
    {"name": "vosk-model-small-fr", "version": "0.22", "path": "vosk-model-small-fr-0.22",
     "supplier": "Alpha Cephei", "license": "Apache-2.0", "url": "https://alphacephei.com/vosk/models",
     "task": "speech recognition (French)"},
    {"name": "piper-fr_FR-siwis-medium", "version": "medium", "path": "piper",
     "supplier": "rhasspy / Open Home Foundation", "license": "CC-BY-4.0",
     "url": "https://huggingface.co/rhasspy/piper-voices/tree/main/fr/fr_FR/siwis/medium",
     "task": "text-to-speech (license of the SIWIS dataset)"},
    {"name": "openwakeword-hey_jarvis", "version": "0.1", "path": "openwakeword",
     "supplier": "openWakeWord (dscripka)", "license": "CC-BY-NC-SA-4.0",
     "url": "https://github.com/dscripka/openWakeWord", "task": "wake word + melspectrogram + embedding"},
    {"name": "speechbrain-spkrec-ecapa-voxceleb", "version": "1.0", "path": "spkrec-ecapa-voxceleb",
     "supplier": "SpeechBrain", "license": "Apache-2.0",
     "url": "https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb",
     "task": "speaker verification (speaker.enabled option)"},
]

# File extensions hashed as model artifacts (weights, graphs, configs).
MODEL_EXT = {".onnx", ".bin", ".xml", ".mdl", ".fst", ".mat", ".ie", ".dubm", ".ckpt", ".pt", ".json", ".yaml"}


def run(cmd: list[str]) -> str:
    """Run a command and return stdout + stderr, or "" if it cannot be run."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return r.stdout + r.stderr  # nginx -v writes to stderr
    except (OSError, subprocess.SubprocessError):
        return ""


def cyclonedx_env(python: str, dest: Path) -> dict:
    """Inventory the virtualenv of ``python`` with cyclonedx-py and return the parsed BOM."""
    if shutil.which("cyclonedx-py"):
        base = ["cyclonedx-py"]
    elif shutil.which("uvx"):
        base = ["uvx", "--from", "cyclonedx-bom", "cyclonedx-py"]
    else:
        raise SystemExit("cyclonedx-py not found: install uv, or run \"pip install cyclonedx-bom\"")
    subprocess.run(base + ["environment", "--spec-version", "1.6", "--output-format", "JSON",
                           "--output-file", str(dest), python], check=True)
    return json.loads(dest.read_text())


def sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in 1 MiB blocks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def model_component(meta: dict, models_dir: Path) -> dict | None:
    """Build the machine-learning-model component (one hashed file per sub-component), or None if absent."""
    base = models_dir / meta["path"]
    if not base.exists():
        return None
    files = [base] if base.is_file() else sorted(p for p in base.rglob("*") if p.is_file() and p.suffix in MODEL_EXT)
    lic = {"license": {"id": meta["license"]}} if "license" in meta else {"license": {"name": meta["license_name"]}}
    comp = {
        "type": "machine-learning-model",
        "bom-ref": f"model:{meta['name']}",
        "name": meta["name"],
        "version": meta["version"],
        "supplier": {"name": meta["supplier"]},
        "description": meta["task"],
        "licenses": [lic],
        "externalReferences": [{"type": "distribution", "url": meta["url"]}],
        "properties": [{"name": "jarvis:models_relpath", "value": meta["path"]},
                       {"name": "jarvis:size_bytes", "value": str(sum(p.stat().st_size for p in files))}],
        "components": [
            {"type": "file", "bom-ref": f"model:{meta['name']}:{p.relative_to(models_dir)}",
             "name": str(p.relative_to(models_dir)), "hashes": [{"alg": "SHA-256", "content": sha256(p)}]}
            for p in files
        ],
    }
    return comp


def system_components(python: str) -> list[dict]:
    """Detect CPython, SQLite, FFmpeg, nginx and the OS actually present on this machine."""
    comps = []
    py = run([python, "-c", "import sys, sqlite3; print(sys.version.split()[0], sqlite3.sqlite_version)"]).split()
    if len(py) >= 2:
        comps.append({"type": "platform", "bom-ref": "sys:cpython", "name": "cpython", "version": py[0],
                      "licenses": [{"license": {"id": "PSF-2.0"}}],
                      "purl": f"pkg:generic/python@{py[0]}"})
        comps.append({"type": "library", "bom-ref": "sys:sqlite", "name": "sqlite", "version": py[1],
                      "licenses": [{"license": {"name": "Public Domain"}}], "purl": f"pkg:generic/sqlite@{py[1]}"})
    if m := re.search(r"ffmpeg version (\S+)", run(["ffmpeg", "-version"])):
        comps.append({"type": "application", "bom-ref": "sys:ffmpeg", "name": "ffmpeg", "version": m.group(1),
                      "licenses": [{"license": {"id": "LGPL-2.1-or-later"}}],
                      "description": "audio decoding of voice enrollment files (voice.load_audio_file)"})
    if m := re.search(r"nginx/(\S+)", run(["nginx", "-v"])):
        comps.append({"type": "application", "bom-ref": "sys:nginx", "name": "nginx", "version": m.group(1),
                      "licenses": [{"license": {"id": "BSD-2-Clause"}}], "description": "TLS termination"})
    osr = Path("/etc/os-release")
    if osr.exists():
        info = dict(line.split("=", 1) for line in osr.read_text().splitlines() if "=" in line)
        comps.append({"type": "operating-system", "bom-ref": "sys:os", "name": info.get("ID", "linux").strip('"'),
                      "version": info.get("VERSION_ID", "").strip('"')})
    return comps


def license_label(comp: dict) -> str:
    """Flatten a component's licenses into one label for the CSV.

    Components without license data get "UNDECLARED" (docs/diagrams/src/build.py classifies it
    as "undeclared" in the BOM PDF).
    """
    out = []
    for lic in comp.get("licenses") or []:
        if "expression" in lic:
            out.append(lic["expression"])
        else:
            d = lic.get("license", {})
            out.append(d.get("id") or d.get("name", "").replace("License :: OSI Approved :: ", ""))
    return " ; ".join(out) or "UNDECLARED"


def main() -> None:
    """Parse arguments, build the enriched BOM and write the JSON and CSV outputs."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default="/opt/jarvis/venv/bin/python")
    ap.add_argument("--models", default="/var/lib/jarvis/models")
    ap.add_argument("--extra-models", action="append", default=[],
                    help="additional directory to search (e.g. a separate openWakeWord directory)")
    ap.add_argument("--out", default=str(ROOT / "docs/sbom/jarvis-sbom.cdx.json"))
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    bom = cyclonedx_env(args.python, out.with_suffix(".env.tmp"))
    out.with_suffix(".env.tmp").unlink()

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    # The version is dynamic in pyproject.toml: its single source is jarvis/VERSION.
    project["version"] = (ROOT / "jarvis" / "VERSION").read_text(encoding="utf-8").strip()
    bom["serialNumber"] = f"urn:uuid:{uuid.uuid4()}"
    bom["metadata"]["timestamp"] = datetime.now(UTC).isoformat(timespec="seconds")
    bom["metadata"]["component"] = {
        "type": "application", "bom-ref": "jarvis-home", "name": project["name"], "version": project["version"],
        "description": project["description"], "purl": f"pkg:pypi/{project['name']}@{project['version']}",
        # License of the Jarvis code itself (pyproject.toml); components keep their own.
        "licenses": [{"license": {"id": project.get("license", {}).get("text", "0BSD")}}],
    }
    # The project itself (installed in editable mode) is the application described in metadata:
    # drop it from the components, along with its file:// purl, which would leak a local path.
    own = {c["bom-ref"] for c in bom["components"] if c["name"] == project["name"]}
    bom["components"] = [c for c in bom["components"] if c["bom-ref"] not in own]
    bom["dependencies"] = [d for d in bom.get("dependencies", []) if d["ref"] not in own]
    for d in bom["dependencies"]:
        d["dependsOn"] = [r for r in d.get("dependsOn", []) if r not in own]
    py_refs = [c["bom-ref"] for c in bom["components"]]

    # AI models: first matching directory wins (--models, then each --extra-models).
    models = []
    for meta in MODELS:
        comp = None
        for d in [args.models, *args.extra_models]:
            comp = comp or model_component(meta, Path(d))
        if comp:
            models.append(comp)
    system = system_components(args.python)
    bom["components"] += models + system
    bom.setdefault("dependencies", []).append(
        {"ref": "jarvis-home", "dependsOn": py_refs + [m["bom-ref"] for m in models] + [s["bom-ref"] for s in system]})
    out.write_text(json.dumps(bom, indent=2, ensure_ascii=False) + "\n")

    # Human-readable CSV, also read by docs/diagrams/src/build.py ("name" and "license" columns)
    # to render the SBOM chapter of the BOM PDF.
    with out.with_name("jarvis-sbom.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["type", "name", "version", "license", "purl / source"])
        for c in sorted(bom["components"], key=lambda c: (c["type"], c["name"].lower())):
            src = c.get("purl") or next((r["url"] for r in c.get("externalReferences", [])
                                         if r["type"] == "distribution"), "")
            w.writerow([c["type"], c["name"], c.get("version", ""), license_label(c), src])
    print(f"SBOM: {out} ({len(py_refs)} Python packages, {len(models)} models, {len(system)} system components)")


if __name__ == "__main__":
    main()
