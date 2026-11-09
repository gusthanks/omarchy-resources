# Notes for AI agents (and humans) working on this plugin

## Never work on the live plugin

The copy installed at `~/.config/omarchy/plugins/gusthx.resources/` is the one the user's Omarchy
shell is running. Saving a file there hot-reloads the plugin in the user's bar, and
`omarchy restart shell` restarts the whole bar. Doing that over and over while developing
is disruptive.

- **Edit only this repository** (the working copy in `~/Work/`).
- **Do not** edit, copy into, or `git` anything under `~/.config/omarchy/plugins/`.
- **Do not** run `omarchy restart shell`, `omarchy refresh shell`, `omarchy theme set`,
  or IPC calls (`omarchy-shell gusthx.resources ...`) to test a change.
- **Do not** take screenshots of the user's desktop (`grim`) to check your work.

## Test and capture in isolation

`dev/run.sh` starts a **separate** Quickshell instance with Omarchy's `Commons`/`Ui`
libraries, this repo and mock data (`dev/mock/`). The harness runs `Monitor.qml` with
`live: false`, so it never reads `/proc`, runs the collectors or any command. Captures
render offscreen (Qt's offscreen platform) and save images straight to files.

```bash
dev/run.sh check     # compile every type, Panel.qml included (deploy runs this too)
dev/run.sh test      # node --test + python3 unittest, no packages
dev/run.sh docs      # docs/*.png and preview.png from mock data
dev/run.sh preview   # the popup in a window, mock data
```

Also run `omarchy plugin validate` on a clean checkout. What the harness cannot render (the
real bar with live data) is checked by the user after deploying, not by you on their live
shell.

## Deploy once, at the end, and restart the bar

When ALL the requested work is finished, tested in isolation and committed, run
`dev/deploy.sh` once: it validates and compiles HEAD, installs it into the live plugin
(backing up a plain-copy folder to `~/Work/backups/` first) and runs `omarchy restart shell`
once. The shell's hot reload is
not reliable for plugins (the old version can stay on screen), so do not tell the user
something is done before the restart ran. Never deploy or restart in the middle of a task,
and never use `omarchy refresh shell` (it resets the bar layout).

## Other conventions

- Repo root = plugin root (the layout `omarchy plugin add <url>` expects).
- Strings go through the plugin's own catalog in `i18n/` (`Commons.Translations`); keep
  every language file on the same keys (the tests check it).
- `collect-details.sh` and `collect-transfers.py` share the runtime-directory rule
  (`runtime_dir`); change both together (a test compares them).
- Screenshots in `docs/` come from mock data only, never from the real machine.
