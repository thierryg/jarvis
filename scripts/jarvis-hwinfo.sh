#!/usr/bin/env bash
# The section functions are dispatched indirectly ("section_$name"), invisible to ShellCheck.
# shellcheck disable=SC2329
# =============================================================================
# Jarvis - Local smart gatekeeper (PTZ camera, face & voice recognition)
# -----------------------------------------------------------------------------
# File    : scripts/jarvis-hwinfo.sh
# Purpose : Hardware inventory: system, CPU, NPU, GPU, memory, storage/IO, network, USB, audio
# Author  : Thierry Gayet <thierry.gayet@labworks.fr>
# Project : jarvis-home (version: jarvis/VERSION)
# Copyright (c) 2026 Thierry Gayet. SPDX-License-Identifier: 0BSD
# =============================================================================
#
# Detects every hardware capability relevant to Jarvis, before or after the installation
# (no Jarvis component needed), and ends with a suitability verdict against the Jarvis
# requirements (REQUIREMENTS.md). Sections:
#   system   vendor, model, firmware (UEFI, Secure Boot), OS, kernel, virtualization
#   cpu      model, topology, frequencies, caches, ISA extensions (AVX2, AVX-512, VNNI,
#            AMX, AES, SHA), virtualization, microcode, vulnerabilities
#   npu      Intel NPU (intel_vpu), AMD XDNA, Google Coral (USB/PCIe), Hailo
#   gpu      DRM cards (driver, PCI id, frequencies, render node), VAAPI profiles,
#            OpenCL, Vulkan, NVIDIA, OpenVINO and ONNX Runtime devices (if installed)
#   memory   total/available/swap, DIMM slots, type, speed, ECC (dmidecode, root)
#   storage  disks (model, size, SSD/HDD, bus, TRIM, scheduler), SMART health,
#            filesystems, live I/O rates; optional quick sequential benchmark
#   network  interfaces (driver, bus, link speed, MAC, MTU, addresses), route, DNS
#   usb      USB devices, Jarvis modules recognized (ReSpeaker, CH340 relay, FT232R,
#            AX88179/RTL8153 NIC, Coral)
#   audio    capture and playback devices; video devices (/dev/video*)
#   sensors  temperatures (thermal zones, hwmon)
#
# Usage:
#   jarvis-hwinfo [--section NAME]... [--json] [--bench] [--no-color] [--help]
#     --section NAME  only this section (repeatable): system cpu npu gpu memory storage
#                     network usb audio sensors
#     --json          machine-readable output (one JSON object) instead of the report
#     --bench         also run a quick sequential write/read test (256 MiB in /var/tmp)
#
# Prerequisites: bash >= 4.4, Linux /proc and /sys. Optional, for more detail: lscpu,
# lspci (pciutils), lsusb (usbutils), lsblk, dmidecode and smartctl (root), vainfo,
# clinfo, vulkaninfo, nvidia-smi, ethtool, arecord/aplay, mokutil, python3.
#
# Exit codes:
#   0  inventory done, the machine meets the Jarvis requirements
#   2  usage error
#   8  inventory done, but the machine is below a Jarvis requirement (see the verdict)

_self="$(readlink -f -- "${BASH_SOURCE[0]}")"
for _lib in "$(dirname -- "$_self")/lib/jarvis-common.sh" /usr/local/lib/jarvis/jarvis-common.sh; do
  # shellcheck source=lib/jarvis-common.sh
  [[ -r "$_lib" ]] && { . "$_lib"; break; }
done
[[ -n "${_JARVIS_COMMON_SH:-}" ]] || { echo "jarvis-hwinfo: jarvis-common.sh library not found" >&2; exit 4; }
# An inventory degrades gracefully: a failing probe only leaves its field empty.
set +e
trap - ERR

readonly ALL_SECTIONS=(system cpu npu gpu memory storage network usb audio sensors)
SECTIONS=()
JSON=0
BENCH=0
# Collected results: "section<TAB>key<TAB>value" lines (report and JSON share them).
RESULTS=""
VERDICT=()

usage() { sed -n '/^# Usage:/,/^# Prerequisites:/p' "$_self" | sed -e 's/^# \{0,1\}//' -e '$d'; }

# --- output -------------------------------------------------------------------------------
# section TITLE: start a report section.
section() { CURRENT="$1"; ((JSON)) || printf '\n%s%s%s\n' "$C_CYAN$C_BOLD" "${1^^}" "$C_RESET"; }
# put KEY VALUE: record a result and print it (empty values are skipped).
put() {
  local key="$1" value="$2"
  [[ -n "$value" ]] || return 0
  RESULTS+="$CURRENT"$'\t'"$key"$'\t'"${value//$'\n'/ | }"$'\n'
  ((JSON)) || printf '  %s%-18s%s %s\n' "$C_DIM" "$key" "$C_RESET" "$value"
}
# have CMD: success when CMD is available.
have() { command -v "$1" >/dev/null 2>&1; }
# rd FILE: first line of a sysfs/procfs file, empty when unreadable.
rd() { [[ -r "$1" ]] && head -n1 "$1" 2>/dev/null | tr -d '\0'; }
# hbytes N: human-readable size (1024 base).
hbytes() {
  awk -v b="${1:-0}" 'BEGIN { split("B KiB MiB GiB TiB PiB", u); i = 1
    while (b >= 1024 && i < 6) { b /= 1024; i++ }
    printf (i == 1 ? "%d %s" : "%.1f %s"), b, u[i] }'
}
# verdict LEVEL MESSAGE: add a line to the final assessment (ok, warn, fail).
verdict() { VERDICT+=("$1|$2"); }

# --- sections ---------------------------------------------------------------------------------
section_system() {
  section system
  local d=/sys/class/dmi/id
  put "Vendor" "$(rd "$d"/sys_vendor)"
  put "Product" "$(rd "$d"/product_name) $(rd "$d"/product_version)"
  put "Board" "$(rd "$d"/board_vendor) $(rd "$d"/board_name)"
  put "Firmware" "$(rd "$d"/bios_vendor) $(rd "$d"/bios_version) ($(rd "$d"/bios_date))"
  if [[ -d /sys/firmware/efi ]]; then
    local sb=""
    have mokutil && sb="$(mokutil --sb-state 2>/dev/null | head -1)"
    put "Boot" "UEFI${sb:+ · $sb}"
  else
    put "Boot" "legacy BIOS"
  fi
  # shellcheck disable=SC1091
  put "OS" "$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME")"
  put "Kernel" "$(uname -srm)"
  put "Virtualization" "$(systemd-detect-virt 2>/dev/null)"
  put "Uptime" "$(uptime -p 2>/dev/null)"
}

section_cpu() {
  section cpu
  local model sockets cores threads flags f
  model="$(grep -m1 '^model name' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//')"
  threads="$(grep -c '^processor' /proc/cpuinfo)"
  cores="$(awk -F: '/^physical id/ { p = $2 } /^core id/ { print p ":" $2 }' /proc/cpuinfo | sort -u | wc -l)"
  sockets="$(awk -F: '/^physical id/ { print $2 }' /proc/cpuinfo | sort -u | wc -l)"
  ((cores > 0)) || cores="$threads"
  put "Model" "$model"
  put "Topology" "${sockets:-1} socket(s) · $cores cores · $threads threads"
  local base max cur gov
  base="$(rd /sys/devices/system/cpu/cpu0/cpufreq/base_frequency)"
  max="$(rd /sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq)"
  cur="$(awk -F': ' '/^cpu MHz/ { s += $2; n++ } END { if (n) printf "%d", s / n }' /proc/cpuinfo)"
  gov="$(rd /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor)"
  put "Frequency" "${base:+base $((base / 1000)) MHz · }${max:+max $((max / 1000)) MHz · }now ${cur:-?} MHz${gov:+ · governor $gov}"
  if have lscpu; then
    put "Caches" "$(lscpu 2>/dev/null | awk -F: '/^L[123][di]? cache/ { gsub(/^ +/, "", $2); printf "%s %s  ", $1, $2 }' | sed 's/ cache//g')"
  fi
  flags=" $(grep -m1 '^flags' /proc/cpuinfo | cut -d: -f2) "
  local -a simd=() crypto=() virt=()
  for f in sse4_2 avx avx2 fma f16c avx512f avx512bw avx512vl avx512_vnni avx512_bf16 avx_vnni amx_tile amx_int8 amx_bf16; do
    [[ "$flags" == *" $f "* ]] && simd+=("$f")
  done
  for f in aes sha_ni pclmulqdq rdrand rdseed; do [[ "$flags" == *" $f "* ]] && crypto+=("$f"); done
  for f in vmx svm; do [[ "$flags" == *" $f "* ]] && virt+=("$f"); done
  put "SIMD / AI" "${simd[*]:-none}"
  put "Crypto" "${crypto[*]:-none}"
  put "Virtualization" "${virt[*]:-none}"
  put "Microcode" "$(grep -m1 '^microcode' /proc/cpuinfo | cut -d: -f2 | sed 's/^ *//')"
  local vuln
  vuln="$(grep -l -v -e '^Not affected' -e '^Mitigation' /sys/devices/system/cpu/vulnerabilities/* 2>/dev/null | xargs -r -n1 basename | tr '\n' ' ')"
  put "Vulnerable" "${vuln:-none (all mitigated or not affected)}"
  # Jarvis: AVX2 for OpenVINO/ONNX speed, 4 cores for vision + voice in parallel.
  if [[ "$flags" == *" avx2 "* ]]; then verdict ok "CPU has AVX2"; else verdict fail "CPU without AVX2: inference too slow for Jarvis"; fi
  if ((cores >= 4)); then verdict ok "$cores physical cores"; else verdict warn "$cores physical cores: lower detector.imgsz and vision.process_fps"; fi
}

section_npu() {
  section npu
  local found=0 dev drv
  for dev in /sys/class/accel/accel*; do
    [[ -e "$dev" ]] || continue
    drv="$(basename "$(readlink -f "$dev/device/driver")" 2>/dev/null)"
    put "$(basename "$dev")" "driver ${drv:-?} · /dev/accel/$(basename "$dev")$([[ "$drv" == intel_vpu ]] && echo ' (Intel NPU, OpenVINO "NPU")')$([[ "$drv" == amdxdna ]] && echo ' (AMD XDNA / Ryzen AI)')"
    found=1
  done
  [[ -e /dev/apex_0 ]] && { put "Coral PCIe" "/dev/apex_0 (Edge TPU)"; found=1; }
  [[ -e /dev/hailo0 ]] && { put "Hailo" "/dev/hailo0"; found=1; }
  if have lsusb && lsusb 2>/dev/null | grep -qiE '1a6e:089a|18d1:9302'; then put "Coral USB" "Edge TPU accelerator"; found=1; fi
  ((found)) || put "Accelerator" "none (inference runs on the CPU/GPU)"
}

section_gpu() {
  section gpu
  local card drv pci name cur min max rp0 node
  for card in /sys/class/drm/card[0-9]; do
    [[ -e "$card/device" ]] || continue
    drv="$(basename "$(readlink -f "$card/device/driver")" 2>/dev/null)"
    pci="$(basename "$(readlink -f "$card/device")")"
    name=""
    have lspci && name="$(lspci -s "$pci" 2>/dev/null | cut -d: -f3- | sed 's/^ *//')"
    cur="$(rd "$card/gt_act_freq_mhz")"; [[ -n "$cur" ]] || cur="$(rd "$card/gt_cur_freq_mhz")"
    min="$(rd "$card/gt_min_freq_mhz")"; max="$(rd "$card/gt_max_freq_mhz")"; rp0="$(rd "$card/gt_RP0_freq_mhz")"
    node="$(find "$card/device/drm" -maxdepth 1 -name 'renderD*' -printf '/dev/dri/%f' 2>/dev/null | head -1)"
    put "$(basename "$card")" "${name:-$pci} · driver ${drv:-?}${node:+ · $node}${cur:+ · ${cur}/${rp0:-$max} MHz (min ${min:-?})}"
  done
  if have vainfo; then
    local va
    va="$(vainfo 2>/dev/null | awk '/VAProfile/ { sub(/:.*/, ""); gsub(/[ \t]/, ""); sub(/VAProfile/, ""); print }' | sort -u | tr '\n' ' ')"
    put "VAAPI" "${va:-no profile (driver missing? i965-va-driver for Haswell, intel-media-va-driver for Gen8+)}"
    if [[ -n "$va" ]]; then verdict ok "VAAPI hardware video decoding"; else verdict warn "no VAAPI decoding: the CPU decodes the camera stream"; fi
  else
    put "VAAPI" "vainfo not installed (apt install vainfo)"
  fi
  have clinfo && put "OpenCL" "$(clinfo -l 2>/dev/null | sed -n 's/.*Device #[0-9]*: //p' | tr '\n' ';' | sed 's/;$//')"
  have vulkaninfo && put "Vulkan" "$(vulkaninfo --summary 2>/dev/null | sed -n 's/.*deviceName *= *//p' | tr '\n' ';' | sed 's/;$//')"
  if have nvidia-smi; then
    put "NVIDIA" "$(nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>/dev/null | tr '\n' ';' | sed 's/;$//')"
  fi
  # OpenVINO / ONNX Runtime devices, from the Jarvis virtualenv when present.
  local py=/opt/jarvis/venv/bin/python
  [[ -x "$py" ]] || py="$(command -v python3)"
  if [[ -n "$py" ]]; then
    put "OpenVINO" "$(timeout 30 "$py" -c 'import openvino as ov; c = ov.Core(); print(", ".join(f"{d} ({c.get_property(d, \"FULL_DEVICE_NAME\")})" for d in c.available_devices))' 2>/dev/null)"
    put "ONNX Runtime" "$(timeout 30 "$py" -c 'import onnxruntime as o; print(", ".join(o.get_available_providers()))' 2>/dev/null)"
  fi
}

section_memory() {
  section memory
  local total avail swap
  total="$(awk '/^MemTotal/ { print $2 * 1024 }' /proc/meminfo)"
  avail="$(awk '/^MemAvailable/ { print $2 * 1024 }' /proc/meminfo)"
  swap="$(awk '/^SwapTotal/ { print $2 * 1024 }' /proc/meminfo)"
  put "Total" "$(hbytes "$total") (available $(hbytes "$avail"))"
  put "Swap" "$(hbytes "$swap")"
  if have dmidecode && [[ $EUID -eq 0 ]]; then
    put "Slots" "$(dmidecode -t memory 2>/dev/null | awk -F': ' '
      /^Memory Device/ { dev = 1 } dev && /^\tSize:/ { size = $2 } dev && /^\tType:/ { type = $2 }
      dev && /^\tConfigured Memory Speed:/ { speed = $2 } dev && /^\tLocator:/ { loc = $2 }
      dev && /^$/ { if (size != "") printf "%s: %s %s %s; ", loc, size, type, speed; dev = 0; size = type = speed = loc = "" }' | sed 's/; $//')"
    put "ECC" "$(dmidecode -t memory 2>/dev/null | awk -F': ' '/Error Correction Type/ { print $2; exit }')"
  else
    put "Slots" "run as root for the DIMM details (dmidecode)"
  fi
  local gib=$((total / 1024 / 1024 / 1024))
  if ((total >= 7 * 1024 * 1024 * 1024)); then verdict ok "$(hbytes "$total") RAM"
  elif ((total >= 3500 * 1024 * 1024)); then verdict warn "$(hbytes "$total") RAM: works, 8 GiB recommended"
  else verdict fail "$(hbytes "$total") RAM: below the 4 GiB minimum"; fi
  : "$gib"
}

section_storage() {
  section storage
  if have lsblk; then
    local name model size rota tran disc
    while read -r name size rota tran model; do
      disc="$(rd "/sys/block/$name/queue/discard_max_bytes")"
      put "$name" "${model:-?} · $size · $([[ "$rota" == 0 ]] && echo SSD || echo HDD) · ${tran:-?} · TRIM $([[ "${disc:-0}" -gt 0 ]] && echo yes || echo no) · scheduler $(sed -n 's/.*\[\(.*\)\].*/\1/p' "/sys/block/$name/queue/scheduler" 2>/dev/null)"
      if have smartctl && [[ $EUID -eq 0 ]]; then
        put "$name SMART" "$(smartctl -H "/dev/$name" 2>/dev/null | sed -n 's/.*overall-health self-assessment test result: //p; s/^SMART Health Status: //p')"
      fi
    done < <(lsblk -d -n -o NAME,SIZE,ROTA,TRAN,MODEL -e 7,11 2>/dev/null)
  fi
  local fs
  for fs in / /var /var/lib/jarvis; do
    [[ -d "$fs" ]] || continue
    put "fs $fs" "$(df -h --output=source,fstype,size,used,avail,pcent "$fs" 2>/dev/null | tail -1 | awk '{ printf "%s %s · %s used of %s (%s) · %s free", $1, $2, $4, $3, $6, $5 }')"
  done
  # Live I/O over one second, physical disks only.
  local r0 w0 r1 w1
  read -r r0 w0 < <(awk '$3 ~ /^(sd[a-z]+|nvme[0-9]+n[0-9]+|vd[a-z]+|mmcblk[0-9]+)$/ { r += $6; w += $10 } END { print r * 512, w * 512 }' /proc/diskstats)
  sleep 1
  read -r r1 w1 < <(awk '$3 ~ /^(sd[a-z]+|nvme[0-9]+n[0-9]+|vd[a-z]+|mmcblk[0-9]+)$/ { r += $6; w += $10 } END { print r * 512, w * 512 }' /proc/diskstats)
  put "I/O now" "read $(hbytes $((r1 - r0)))/s · write $(hbytes $((w1 - w0)))/s"
  ((BENCH)) && bench_disk
  local free
  free="$(df -B1 --output=avail /var 2>/dev/null | tail -1)"
  if ((free >= 15 * 1024 * 1024 * 1024)); then verdict ok "$(hbytes "$free") free on /var"
  else verdict fail "$(hbytes "$free") free on /var: 15 GiB required"; fi
}

# bench_disk: 256 MiB sequential write then read with direct I/O (no page cache) in /var/tmp.
bench_disk() {
  local f w r
  f="$(mktemp /var/tmp/jarvis-bench.XXXXXX)"
  w="$(dd if=/dev/zero of="$f" bs=4M count=64 oflag=direct conv=fsync 2>&1 | awk -F', ' '/copied/ { print $NF }')"
  r="$(dd if="$f" of=/dev/null bs=4M iflag=direct 2>&1 | awk -F', ' '/copied/ { print $NF }')"
  rm -f -- "$f"
  put "Benchmark" "sequential write ${w:-?} · read ${r:-?} (256 MiB, direct I/O, /var/tmp)"
}

section_network() {
  section network
  local dev path drv bus speed state mac mtu addr
  for path in /sys/class/net/*; do
    dev="$(basename "$path")"
    [[ "$dev" == lo || "$dev" =~ ^(veth|docker|br-|virbr|vnet) ]] && continue
    drv="$(basename "$(readlink -f "$path/device/driver")" 2>/dev/null)"
    bus="virtual"
    [[ -e "$path/device" ]] && bus="$(readlink -f "$path/device" | grep -q '/usb' && echo USB || echo PCI)"
    [[ -d "$path/wireless" ]] && bus+=" wifi"
    state="$(rd "$path/operstate")"
    speed="$(rd "$path/speed")"; [[ "${speed:-0}" -gt 0 ]] 2>/dev/null || speed=""
    mac="$(rd "$path/address")"; mtu="$(rd "$path/mtu")"
    addr="$(ip -br addr show dev "$dev" 2>/dev/null | awk '{ $1 = $2 = ""; print }' | sed 's/^ *//')"
    put "$dev" "${drv:-?} · $bus · $state${speed:+ · ${speed} Mb/s} · $mac · mtu $mtu${addr:+ · $addr}"
  done
  put "Default route" "$(ip -4 route show default 2>/dev/null | awk '{ print $3 " via " $5; exit }')"
  put "DNS" "$(awk '/^nameserver/ { printf "%s ", $2 }' /etc/resolv.conf 2>/dev/null)"
  local wired
  wired="$(for p in /sys/class/net/*; do [[ -e $p/device && ! -d $p/wireless ]] && echo x; done | wc -l)"
  if ((wired >= 2)); then verdict ok "$wired wired interfaces (LAN + camera network)"
  else verdict warn "$wired wired interface: add a USB 3.0 Gigabit adapter for the dedicated camera network"; fi
}

section_usb() {
  section usb
  if ! have lsusb; then put "USB" "lsusb not installed (apt install usbutils)"; return 0; fi
  local line id
  declare -A known=(
    [2886:0018]="ReSpeaker USB Mic Array v2.0 (microphone)"
    [1a86:7523]="CH340 USB-serial (LCUS relay board)"
    [0403:6001]="FTDI FT232R (door sensor on CTS)"
    [0b95:1790]="ASIX AX88179 USB 3.0 Gigabit (camera network)"
    [0bda:8153]="Realtek RTL8153 USB 3.0 Gigabit (camera network)"
    [1a6e:089a]="Google Coral Edge TPU (bootloader)"
    [18d1:9302]="Google Coral Edge TPU"
    [16c0:05df]="USB HID relay board"
  )
  while read -r line; do
    id="$(awk '{ print $6 }' <<<"$line")"
    if [[ -n "${known[$id]:-}" ]]; then
      put "$id" "${C_GREEN}${known[$id]}${C_RESET}"
    elif ! grep -qi 'root hub' <<<"$line"; then
      put "$id" "$(cut -d' ' -f7- <<<"$line")"
    fi
  done < <(lsusb 2>/dev/null)
  if have lsusb && lsusb -t >/dev/null 2>&1; then
    put "USB 3.x ports" "$(lsusb -t 2>/dev/null | grep -c '5000M\|10000M\|20000M') root/device link(s) at 5 Gb/s or more"
  fi
}

section_audio() {
  section audio
  if have arecord; then
    put "Capture" "$(arecord -l 2>/dev/null | sed -n 's/^card \([0-9]*\): [^[]*\[\([^]]*\)\].*device \([0-9]*\).*/hw:\1,\3 \2/p' | tr '\n' ';' | sed 's/;$//')"
    put "Playback" "$(aplay -l 2>/dev/null | sed -n 's/^card \([0-9]*\): [^[]*\[\([^]]*\)\].*device \([0-9]*\).*/hw:\1,\3 \2/p' | tr '\n' ';' | sed 's/;$//')"
  else
    put "ALSA" "alsa-utils not installed"
  fi
  put "Video devices" "$(find /dev -maxdepth 1 -name 'video*' 2>/dev/null | sort | tr '\n' ' ')"
}

section_sensors() {
  section sensors
  local z t type h name
  for z in /sys/class/thermal/thermal_zone*; do
    [[ -r "$z/temp" ]] || continue
    t="$(rd "$z/temp")"; type="$(rd "$z/type")"
    [[ -n "$t" ]] && put "$type" "$((t / 1000)) °C"
  done
  for h in /sys/class/hwmon/hwmon*; do
    name="$(rd "$h/name")"
    t="$(rd "$h/temp1_input")"
    [[ -n "$t" && "$name" =~ ^(coretemp|k10temp|nvme|drivetemp|acpitz)$ ]] && put "$name" "$((t / 1000)) °C"
  done
  return 0
}

# --- final report --------------------------------------------------------------------------
# print_verdict: Jarvis suitability; returns E_WARN when a requirement is not met.
print_verdict() {
  local v level msg worst=0
  ((JSON)) || printf '\n%sJARVIS SUITABILITY%s\n' "$C_CYAN$C_BOLD" "$C_RESET"
  for v in "${VERDICT[@]}"; do
    level="${v%%|*}"; msg="${v#*|}"
    case "$level" in
      ok) ((JSON)) || printf '  %s✔%s %s\n' "$C_GREEN" "$C_RESET" "$msg" ;;
      warn) ((JSON)) || printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$msg" ;;
      fail) ((JSON)) || printf '  %s✘%s %s\n' "$C_RED" "$C_RESET" "$msg"; worst=1 ;;
    esac
  done
  ((JSON)) || echo
  return $((worst ? E_WARN : E_OK))
}

# print_json: every recorded value as {"section": {"key": "value"}, "verdict": [...]}.
print_json() {
  local verdicts
  verdicts="$(printf '%s\n' "${VERDICT[@]}")"
  RESULTS="$RESULTS" VERDICTS="$verdicts" python3 - <<'PY'
import json, os, re
out = {}
strip = re.compile(r"\x1b\[[0-9;]*m")
for line in os.environ["RESULTS"].splitlines():
    if line.count("\t") >= 2:
        section, key, value = line.split("\t", 2)
        out.setdefault(section, {})[key] = strip.sub("", value)
out["verdict"] = [dict(zip(("level", "message"), v.split("|", 1))) for v in os.environ["VERDICTS"].splitlines() if "|" in v]
print(json.dumps(out, indent=2, ensure_ascii=False))
PY
}

# main ARGS...: options, selected sections, verdict.
main() {
  while (($#)); do
    case "$1" in
      --section)
        [[ " ${ALL_SECTIONS[*]} " == *" ${2:-} "* ]] || { log_error "unknown section: ${2:-} (${ALL_SECTIONS[*]})"; exit "$E_USAGE"; }
        SECTIONS+=("$2"); shift ;;
      --json) JSON=1 ;;
      --bench) BENCH=1 ;;
      --no-color) export JARVIS_COLOR=never; setup_colors ;;
      -h | --help) usage; exit "$E_OK" ;;
      *) log_error "unknown option: $1 (see --help)"; exit "$E_USAGE" ;;
    esac
    shift
  done
  ((${#SECTIONS[@]})) || SECTIONS=("${ALL_SECTIONS[@]}")
  ((JSON)) && { JARVIS_COLOR=never; setup_colors; }
  ((JSON)) || printf '%sJarvis hardware inventory%s · %s · %s\n' "$C_BOLD" "$C_RESET" "$(hostname)" "$(date '+%Y-%m-%d %H:%M:%S %Z')"
  local s
  for s in "${SECTIONS[@]}"; do "section_$s"; done
  local rc=0
  print_verdict || rc=$?
  ((JSON)) && print_json
  exit "$rc"
}

main "$@"
