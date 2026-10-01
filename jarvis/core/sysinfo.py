# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : jarvis/core/sysinfo.py
# Purpose : Hardware capability detection, automatic performance tuning and live resource metrics
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
"""Hardware capability detection and live resource metrics.

At start-up the core calls :func:`detect_capabilities` then :func:`apply_auto_performance`:

- **CPU**: model, physical/logical cores, frequency, and the instruction sets that matter for
  inference (SSE4.2, AVX, AVX2, FMA, AVX-512, AVX-512 VNNI, AVX-VNNI, AMX);
- **GPU**: DRM cards (Intel / NVIDIA / AMD by PCI vendor id), OpenVINO devices (``CPU``, ``GPU``,
  ``NPU`` — the OpenVINO GPU plugin only lists Gen9+ Intel GPUs, so a Haswell HD 4600 is correctly
  absent), ONNX Runtime execution providers (CUDA, OpenVINO, CPU), NVIDIA GPUs (``nvidia-smi``) and
  VAAPI H.264/HEVC decode support (``vainfo``);
- **Auto mode** (``performance.mode: auto``) then picks the best YOLO device, face-recognition
  providers, hardware video decoding, detector input size and analysis rate — but only for the
  settings the user did not set explicitly (file, environment or web parameter always win).

:class:`ResourceSampler` computes the live figures shown by the console and the shell: CPU
current/max/free, RAM, per-interface network throughput, disk I/O, GPU frequency/usage and temperatures.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

CPU_FLAGS = ("sse4_2", "avx", "avx2", "fma", "avx512f", "avx512_vnni", "avx_vnni", "amx_tile", "f16c")
PCI_VENDORS = {"0x8086": "Intel", "0x10de": "NVIDIA", "0x1002": "AMD"}


def _run(cmd: list[str], timeout: float = 5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


# --------------------------------------------------------------------------------- detection

def cpu_info() -> dict:
    """CPU model, core counts, max frequency and inference-relevant instruction sets."""
    model, flags = "", set()
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            if line.startswith("model name") and not model:
                model = line.split(":", 1)[1].strip()
            elif line.startswith("flags") and not flags:
                flags = set(line.split(":", 1)[1].split())
    except OSError:
        pass
    physical = logical = os.cpu_count() or 1
    max_mhz = None
    try:
        import psutil

        physical = psutil.cpu_count(logical=False) or logical
        freq = psutil.cpu_freq()
        max_mhz = round(freq.max) if freq and freq.max else None
    except Exception as exc:  # psutil missing or no cpufreq driver: keep the os.cpu_count() figures
        log.debug("psutil CPU details unavailable: %s", exc)
    return {"model": model or platform.processor() or platform.machine(), "physical_cores": physical, "logical_cpus": logical,
            "max_mhz": max_mhz, "flags": [f for f in CPU_FLAGS if f in flags]}


def gpu_cards() -> list[dict]:
    """GPUs seen by the kernel (DRM), with vendor, PCI id, driver and render node."""
    cards = []
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        dev = card / "device"
        try:
            vendor = (dev / "vendor").read_text().strip()
            device = (dev / "device").read_text().strip()
            driver = os.path.basename(os.readlink(dev / "driver")) if (dev / "driver").exists() else ""
        except OSError:
            continue
        render = next((f"/dev/dri/{p.name}" for p in (dev / "drm").glob("renderD*")), None) if (dev / "drm").exists() else None
        cards.append({"card": card.name, "vendor": PCI_VENDORS.get(vendor, vendor), "pci_id": f"{vendor[2:]}:{device[2:]}",
                      "driver": driver, "render_node": render})
    return cards


def openvino_devices() -> dict[str, str]:
    """OpenVINO inference devices (``CPU``, ``GPU``, ``GPU.1``, ``NPU``…) with their full names."""
    try:
        import openvino as ov

        core = ov.Core()
        return {d: core.get_property(d, "FULL_DEVICE_NAME") for d in core.available_devices}
    except Exception:
        return {}


def onnx_providers() -> list[str]:
    """ONNX Runtime execution providers available in this environment."""
    try:
        import onnxruntime

        return list(onnxruntime.get_available_providers())
    except Exception:
        return []


def nvidia_gpus() -> list[dict]:
    out = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"]) \
        if shutil.which("nvidia-smi") else ""
    gpus = []
    for line in out.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) != 3 or not parts[1].isdigit():  # "No devices were found", driver errors…
            continue
        gpus.append({"name": parts[0], "memory_mb": int(parts[1]), "driver": parts[2]})
    return gpus


def vaapi_decode() -> list[str]:
    """Hardware decode profiles reported by ``vainfo`` (e.g. H264, HEVC), empty when unavailable."""
    if not (shutil.which("vainfo") and any(Path("/dev/dri").glob("renderD*"))):
        return []
    out = _run(["vainfo"], timeout=8)
    codecs = set()
    for line in out.splitlines():
        if "VAEntrypointVLD" in line:
            prof = line.split(":")[0].strip()
            for codec in ("H264", "HEVC", "VP9", "AV1", "MPEG2", "JPEG"):
                if codec in prof:
                    codecs.add(codec)
    return sorted(codecs)


def detect_capabilities() -> dict:
    """Full hardware inventory used by the auto mode, the status and the admin tools."""
    try:
        import psutil

        mem_total = psutil.virtual_memory().total
    except Exception:
        mem_total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return {"cpu": cpu_info(), "memory_bytes": mem_total, "gpus": gpu_cards(), "openvino_devices": openvino_devices(),
            "onnx_providers": onnx_providers(), "nvidia": nvidia_gpus(), "vaapi_decode": vaapi_decode(),
            "kernel": platform.release(), "python": platform.python_version()}


# --------------------------------------------------------------------------------- auto tuning

def choose(caps: dict) -> dict:
    """Best settings for the detected hardware (pure function, unit-tested).

    Returns:
        ``{setting key: value}`` plus a ``_why`` list of human-readable reasons.
    """
    why, out = [], {}
    ov = caps.get("openvino_devices", {})
    providers = caps.get("onnx_providers", [])
    cores = caps.get("cpu", {}).get("physical_cores") or 1
    flags = set(caps.get("cpu", {}).get("flags", []))

    if "GPU" in ov:
        out["detector.device"] = "intel:gpu"
        why.append(f"YOLO on the Intel GPU via OpenVINO ({ov['GPU']})")
    elif "NPU" in ov:
        out["detector.device"] = "intel:npu"
        why.append(f"YOLO on the NPU via OpenVINO ({ov['NPU']})")
    else:
        out["detector.device"] = "intel:cpu"
        why.append("YOLO on the CPU via OpenVINO" + (" (AVX2)" if "avx2" in flags else " (no AVX2: slow)"))

    if "CUDAExecutionProvider" in providers:
        out["faces.providers"] = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        why.append("face models on the NVIDIA GPU (ONNX Runtime CUDA)")
    elif "OpenVINOExecutionProvider" in providers and "GPU" in ov:
        out["faces.providers"] = ["OpenVINOExecutionProvider", "CPUExecutionProvider"]
        why.append("face models on the Intel GPU (ONNX Runtime OpenVINO)")
    else:
        out["faces.providers"] = ["CPUExecutionProvider"]
        why.append("face models on the CPU (ONNX Runtime)")

    out["camera.hw_accel"] = "H264" in caps.get("vaapi_decode", [])
    why.append("H.264 decoding by VAAPI" if out["camera.hw_accel"] else "H.264 decoding by the CPU (no VAAPI H.264)")

    if out["detector.device"] == "intel:cpu" and cores <= 2:
        out["detector.imgsz"], out["vision.process_fps"] = 416, 4.0
        why.append(f"{cores} physical cores: reduced detector input (416) and 4 analyses/s")
    elif out["detector.device"] != "intel:cpu" or cores >= 6:
        out["vision.process_fps"] = 10.0
        why.append("accelerator or ≥ 6 cores: 10 analyses/s")
    out["_threads"] = max(1, cores - 1)
    why.append(f"{out['_threads']} inference threads (physical cores − 1)")
    out["_why"] = why
    return out


def apply_auto_performance(settings, caps: dict, user_keys: set[str]) -> dict:
    """Apply :func:`choose` to ``settings`` for every key the user did not set explicitly.

    Args:
        settings: Settings to adjust in place.
        caps: Result of :func:`detect_capabilities`.
        user_keys: Dotted keys set by the file, the environment or a web/CLI parameter.

    Returns:
        ``{"applied": {key: value}, "kept": [keys left to the user], "why": [...]}``.
    """
    from jarvis.config.catalog import get_path, set_path

    picks = choose(caps)
    report = {"applied": {}, "kept": [], "why": picks.pop("_why")}
    threads = picks.pop("_threads")

    def resolve_auto_values() -> None:
        # Values left at "auto" can never reach the inference libraries: resolve them in both modes.
        if settings.camera.hw_accel == "auto":
            settings.camera.hw_accel = picks["camera.hw_accel"]
        if settings.detector.device == "auto":
            settings.detector.device = picks["detector.device"]
        if settings.faces.providers == ["auto"]:
            settings.faces.providers = picks["faces.providers"]

    if settings.performance.mode != "auto":
        resolve_auto_values()
        report["why"] = ["performance.mode is manual: only the settings left at 'auto' were resolved"]
        return report
    for key, value in picks.items():
        if key in user_keys:
            report["kept"].append(key)
            continue
        if get_path(settings, key) != value:
            set_path(settings, key, value)
            report["applied"][key] = value
    if "OMP_NUM_THREADS" not in os.environ:  # a value from the systemd unit or the operator wins
        os.environ["OMP_NUM_THREADS"] = str(threads)
        report["applied"]["OMP_NUM_THREADS"] = threads
    resolve_auto_values()  # keys the user explicitly set to "auto"
    return report


# --------------------------------------------------------------------------------- live metrics

class ResourceSampler:
    """Differential sampler of CPU, memory, network, disk and GPU usage (``psutil`` based).

    Call :meth:`sample` periodically; rates are computed against the previous call and ``max``
    values are tracked since the sampler was created.
    """

    def __init__(self):
        import psutil

        self.ps = psutil
        self._net = psutil.net_io_counters(pernic=True)
        self._disk = psutil.disk_io_counters()
        self._t = time.monotonic()
        self.cpu_max = 0.0
        psutil.cpu_percent(percpu=True)  # prime the counters

    def sample(self) -> dict:
        ps = self.ps
        now = time.monotonic()
        dt = max(now - self._t, 1e-3)
        per_cpu = ps.cpu_percent(percpu=True)
        cpu = sum(per_cpu) / max(len(per_cpu), 1)
        self.cpu_max = max(self.cpu_max, cpu)
        freq = ps.cpu_freq()
        vm, sw = ps.virtual_memory(), ps.swap_memory()
        net_now = ps.net_io_counters(pernic=True)
        net = {}
        for nic, c in net_now.items():
            p = self._net.get(nic)
            if nic == "lo" or p is None:
                continue
            net[nic] = {"rx_bps": (c.bytes_recv - p.bytes_recv) * 8 / dt, "tx_bps": (c.bytes_sent - p.bytes_sent) * 8 / dt,
                        "rx_total": c.bytes_recv, "tx_total": c.bytes_sent}
        disk_now = ps.disk_io_counters()
        disk = {}
        if disk_now and self._disk:
            disk = {"read_Bps": (disk_now.read_bytes - self._disk.read_bytes) / dt,
                    "write_Bps": (disk_now.write_bytes - self._disk.write_bytes) / dt,
                    "read_iops": (disk_now.read_count - self._disk.read_count) / dt,
                    "write_iops": (disk_now.write_count - self._disk.write_count) / dt}
        self._net, self._disk, self._t = net_now, disk_now, now
        temps = {}
        try:
            for name, entries in (ps.sensors_temperatures() or {}).items():
                if entries:
                    temps[name] = max(e.current for e in entries)
        except (AttributeError, OSError):
            pass
        return {"cpu": {"current": round(cpu, 1), "max": round(self.cpu_max, 1), "free": round(100 - cpu, 1),
                        "per_cpu": per_cpu, "load_avg": os.getloadavg(),
                        "mhz": round(freq.current) if freq else None, "max_mhz": round(freq.max) if freq and freq.max else None},
                "memory": {"total": vm.total, "used": vm.total - vm.available, "free": vm.available, "percent": vm.percent,
                           "swap_used": sw.used, "swap_total": sw.total},
                "net": net, "disk": disk, "gpu": gpu_usage(), "temperatures": temps,
                "disk_usage": {"/": ps.disk_usage("/")._asdict()}}


def gpu_usage() -> list[dict]:
    """Live GPU figures: NVIDIA via ``nvidia-smi``; Intel i915 via its sysfs frequencies."""
    out = []
    if shutil.which("nvidia-smi"):
        q = _run(["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                  "--format=csv,noheader,nounits"])
        for line in q.strip().splitlines():
            parts = [x.strip() for x in line.split(",")]
            try:
                name, util, used, total, temp = parts
                out.append({"name": name, "util_percent": float(util), "mem_used_mb": int(used),
                            "mem_total_mb": int(total), "temp_c": float(temp)})
            except ValueError:
                continue  # not a data line (no GPU, driver error)
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]")):
        cur, mx = card / "gt_cur_freq_mhz", card / "gt_max_freq_mhz"
        if cur.exists() and mx.exists():
            try:
                c, m = int(cur.read_text()), int(mx.read_text())
                # i915 exposes no utilization counter without root/perf: the frequency ratio is a proxy.
                out.append({"name": f"Intel iGPU ({card.name})", "freq_mhz": c, "max_freq_mhz": m,
                            "freq_ratio_percent": round(100 * c / m, 1) if m else None})
            except (OSError, ValueError):
                pass
    return out


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n:.0f} B"
        n /= 1024
    return f"{n:.1f} TB"


def human_bits(n: float) -> str:
    for unit in ("bit/s", "kbit/s", "Mbit/s", "Gbit/s"):
        if abs(n) < 1000 or unit == "Gbit/s":
            return f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} Gbit/s"


def render_resources(m: dict, caps: dict | None = None) -> list[tuple[str, str, str]]:
    """Rows (section, metric, value) of a resource sample, shared by the console and the shell."""
    rows = []
    c = m["cpu"]
    rows += [("CPU", "load current / max / free", f"{c['current']:.1f} % / {c['max']:.1f} % / {c['free']:.1f} %"),
             ("CPU", "load average 1 / 5 / 15 min", " / ".join(f"{x:.2f}" for x in c["load_avg"])),
             ("CPU", "frequency current / max", f"{c['mhz'] or '?'} / {c['max_mhz'] or '?'} MHz"),
             ("CPU", "per logical CPU", " ".join(f"{x:.0f}" for x in c["per_cpu"]) + " %")]
    mem = m["memory"]
    rows += [("RAM", "total / used / free", f"{human_bytes(mem['total'])} / {human_bytes(mem['used'])} / "
                                            f"{human_bytes(mem['free'])} ({mem['percent']:.0f} % used)"),
             ("RAM", "swap used / total", f"{human_bytes(mem['swap_used'])} / {human_bytes(mem['swap_total'])}")]
    for nic, n in m["net"].items():
        rows.append(("Network", nic, f"in {human_bits(n['rx_bps'])} · out {human_bits(n['tx_bps'])} "
                                     f"(total {human_bytes(n['rx_total'])} / {human_bytes(n['tx_total'])})"))
    d = m["disk"]
    if d:
        rows.append(("Disk I/O", "read / write", f"{human_bytes(d['read_Bps'])}/s ({d['read_iops']:.0f} IOPS) / "
                                                 f"{human_bytes(d['write_Bps'])}/s ({d['write_iops']:.0f} IOPS)"))
    root = m["disk_usage"]["/"]
    rows.append(("Disk", "/ used / free", f"{human_bytes(root['used'])} / {human_bytes(root['free'])} ({root['percent']:.0f} %)"))
    for g in m["gpu"]:
        if "util_percent" in g:
            rows.append(("GPU", g["name"], f"{g['util_percent']:.0f} % · VRAM {g['mem_used_mb']}/{g['mem_total_mb']} MB · {g['temp_c']:.0f} °C"))
        else:
            rows.append(("GPU", g["name"], f"{g['freq_mhz']} / {g['max_freq_mhz']} MHz (frequency ratio {g['freq_ratio_percent']} %)"))
    for name, temp in m["temperatures"].items():
        rows.append(("Temperature", name, f"{temp:.0f} °C"))
    if caps:
        rows += render_capabilities(caps)
    return rows


def write_capabilities_snapshot(path: str | Path, caps: dict, performance: dict) -> None:
    """Publish the start-up hardware detection for other tools (SSH MOTD, monitoring).

    The file is written atomically (temporary file + rename), world-readable (it holds no
    secret: CPU model and flags, GPUs, OpenVINO/ONNX devices, VAAPI codecs and the automatic
    performance choices). Failures are logged, never raised: this is informational only.

    Args:
        path: Destination, normally ``/run/jarvis/capabilities.json`` (next to the control socket).
        caps: Result of :func:`detect_capabilities`.
        performance: Result of :func:`apply_auto_performance`.
    """
    path = Path(path)
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ts": time.time(), "capabilities": caps, "performance": performance},
                                  default=str, indent=1), encoding="utf-8")
        tmp.chmod(0o644)
        tmp.replace(path)
    except OSError as exc:
        log.warning("Cannot write the capabilities snapshot %s: %s", path, exc)


def render_capabilities(caps: dict) -> list[tuple[str, str, str]]:
    """Rows (section, item, value) describing the CPU and GPU support detected."""
    cpu = caps["cpu"]
    rows = [("CPU support", cpu["model"], f"{cpu['physical_cores']} cores / {cpu['logical_cpus']} threads, "
                                          f"max {cpu['max_mhz'] or '?'} MHz"),
            ("CPU support", "instruction sets", ", ".join(cpu["flags"]) or "none of AVX/AVX2/AVX-512")]
    for g in caps["gpus"]:
        rows.append(("GPU support", f"{g['vendor']} {g['pci_id']}", f"driver {g['driver'] or '-'}, {g['render_node'] or 'no render node'}"))
    rows.append(("GPU support", "OpenVINO devices", ", ".join(f"{k} ({v})" for k, v in caps["openvino_devices"].items()) or "-"))
    rows.append(("GPU support", "ONNX Runtime providers", ", ".join(caps["onnx_providers"]) or "-"))
    rows.append(("GPU support", "VAAPI decoding", ", ".join(caps["vaapi_decode"]) or "not available"))
    for n in caps["nvidia"]:
        rows.append(("GPU support", n["name"], f"{n['memory_mb']} MB, driver {n['driver']}"))
    return rows
