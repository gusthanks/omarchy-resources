# 📊 omarchy-resources

> **Resource Monitor & Background Transfers** — Complete system telemetry and live file transfer monitor for the [Omarchy](https://omarchy.org) status bar.

[![Omarchy Plugin](https://img.shields.io/badge/omarchy-plugin-blue?style=flat-square)](https://omarchy.org)
[![Version](https://img.shields.io/badge/version-1.0.0-emerald?style=flat-square)](manifest.json)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Hyprland-purple?style=flat-square)](https://wiki.hypr.land)

`omarchy-resources` is a high-performance, modular system monitor widget for Omarchy Linux (Hyprland / Quickshell). It brings together real-time hardware telemetry, live interactive process management, storage stats, hardware sensors, and background file transfer tracking under a unified, elegant popup interface.

---

## ✨ Features

- **📊 Tab 1 — System Overview:**
  - Real-time CPU usage (User, System, IO-wait) with 60-sample historical sparkline graphs.
  - Memory consumption (RAM Used, Available, Cached/Buffers, Swap/ZRAM).
  - Live Network traffic (Download / Upload speed and rolling bandwidth history).
  - Real-time Disk I/O read/write transfer rates.
- **⚡ Tab 2 — Interactive Process Manager:**
  - Top active processes sorted by CPU or Memory usage.
  - Sort switcher (`s` key).
  - Interactive process termination (`SIGTERM`) via `x` or `Delete`.
  - Direct integration shortcut to open `btop` in terminal (`b` key).
- **💾 Tab 3 — Disks & Storage:**
  - Automatic detection of physical storage drives and mount points.
  - Free space progress tracks and visual indicators.
- **🌡️ Tab 4 — Thermal Sensors & Hardware:**
  - Hardware thermal zone monitoring (CPU Package, Core temps, Fan RPM, Battery power/voltage).
  - System specs: CPU model, Kernel version, GPU clock frequency, Context switch rate, and Uptime.
- **🚀 Tab 5 — Background File Transfers (Integrated):**
  - Live detection of CLI and background file operations (`rsync`, `rclone`, `dd`, `git clone`, `curl`, `wget`, `cp`, `tar`).
  - Deep `/proc/<pid>/fd` inspection: displays the exact file currently being transferred, progress percentage, ETA, and overall sync progress.
  - Interactive process controls: Pause (`p`), Resume, Cancel (`x`), Open destination folder in Nautilus (`o`), and Copy command (`c`).
  - Ignores persistent daemons and cloud mounts (e.g. `rclone mount`) to avoid false positives.
- **🎨 100% Native Omarchy Design:**
  - Fully dynamic colors following the active Omarchy theme live.
  - Multiple status bar display modes: `compact`, `detailed`, `full`, or `icon` (cycled with Right Click or `m`).
  - Responsive keyboard shortcuts (`1` to `5` to switch tabs, `Esc` to close).

---

## 📦 Installation

Install directly with the Omarchy CLI:

```bash
omarchy plugin add https://github.com/gusthanks/omarchy-resources --enable
```

Then reload the shell:

```bash
omarchy restart shell
```

---

## 🛠️ Keyboard Shortcuts

When the panel is open:

| Key | Action |
|:---:|:---|
| `1` | Switch to **Overview** |
| `2` | Switch to **Processes** |
| `3` | Switch to **Disks** |
| `4` | Switch to **Sensors & Hardware** |
| `5` | Switch to **Transfers** |
| `↑` / `↓` | Select process (Tab 2) or transfer (Tab 5) |
| `s` | Toggle Process sorting (CPU / Memory) |
| `x` | Terminate selected process (SIGTERM) or cancel transfer |
| `p` | Pause / Resume selected transfer |
| `o` | Open transfer destination in file manager |
| `c` | Copy transfer command to clipboard |
| `b` | Launch `btop` system monitor |
| `m` | Cycle status bar mode (`compact` / `detailed` / `full` / `icon`) |
| `r` | Force refresh telemetry |
| `Esc` | Close panel |

---

## 🧪 Testing

The data parsers and telemetry formatters are covered by automated unit tests using Vitest:

```bash
npm test
```

---

## 📄 License

MIT © [gusthx](https://github.com/gusthanks)
