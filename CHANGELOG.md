# Changelog

## 1.2.0 (2026-11-09)

### Transfers

- Paths are shown in full; relative ones are resolved against the command's working
  directory.
- `tar`: the real source and destination, instead of the option bundle and the first
  operand.
- A folder source is sized once in the background (`du`), so folder copies get overall
  progress and an ETA. The size is kept in the runtime directory and removed when the
  transfer ends.
- A file being written no longer shows as 100% of its own size.
- A card at the top of the tab adds up every active transfer (speed, bytes moved, overall
  progress).
- **Batches**: a transfer started by a script (`bash backup.sh`, a Python script, `make`)
  is shown as a step of that batch, with the script's path, how long it has been running
  and how much it has moved. **Pause batch**, **Resume batch** and **Cancel batch** signal
  the script and everything under it, script first; cancelling one transfer only ends that
  step and the script moves on.
- **Uploads and downloads**: `rclone` to a remote and `rsync`/`scp`/`sftp` to another host
  are listed, marked ↑ or ↓. Progress comes from the local side and speed from the network
  side, so cached files and network-bound uploads count. `rclone` was never listed before
  (its own name matched the mount/config filter), and its option values are no longer
  taken for paths.

### Docs and packaging

- `docs/demo.gif`, rendered from mock data by the new `dev/run.sh gif`: a backup script's
  rsync progressing, the batch paused and resumed.
- `preview.png` moved to `docs/preview.png`.
- README reorganized (demo, uninstall, layout, changes) and translated to Brazilian
  Portuguese in `README.pt-BR.md`.
- The manifest declares `"license": "MIT"` and the author "Gustavo Costa (gusthx)".

## 1.1.0 (2026-11-09)

- The panel is split: `Panel.qml` (bar label, popup, IPC), `Monitor.qml` (data and
  actions) and `ResourcesView.qml` (the popup's content).
- iPhone/iPad storage on the Disks tab and `idevicebackup2` backups on the Transfers tab,
  with progress estimated from the device's used space (marked ≈).
- The collectors share one runtime-directory rule (`$XDG_RUNTIME_DIR`, then a private
  `/tmp` folder); the transfer state is written there with mode 0600, no longer to a
  shared `/dev/shm` file.
- Disks open with `xdg-open`; iPhone/iPad entries are not clickable.
- Layout fixes: right-aligned speeds and times, Overview transfers showing 0 B/s,
  keyboard selection scrolling to the right row; every label goes through i18n.
- Dev harness (`dev/run.sh`: check, test, offscreen captures from mock data) and
  `dev/deploy.sh`; tests on `node --test` and `python3 unittest`, no packages.
- README in English with screenshots from mock data and a privacy section.

## 1.0.0 (2026-11-08)

- First release, as a monitor for background file transfers (speed, progress, ETA, pause,
  resume, cancel, open, copy command), then merged into a full resource monitor: CPU,
  memory, network, disk I/O, processes, disks and sensors in one bar widget, with the
  transfers on their own tab.
