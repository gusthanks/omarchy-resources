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

## Test in isolation

Run the plugin's QML in a **separate** Quickshell instance that never touches the running
shell: make a throwaway config dir with symlinks to Omarchy's libraries and this repo, plus
a small `shell.qml` that shows the component you are working on.

```bash
cfg="$XDG_RUNTIME_DIR/gusthx.resources-dev"; rm -rf "$cfg"; mkdir -p "$cfg"
ln -s /usr/share/omarchy/shell/Commons "$cfg/Commons"
ln -s /usr/share/omarchy/shell/Ui "$cfg/Ui"
ln -s "$PWD" "$cfg/plugin"
# write $cfg/shell.qml: ShellRoot { PanelWindow { ... import "plugin" as P ... } }
quickshell -n -p "$cfg/shell.qml"
```

For screenshots, draw on a background layer (`WlrLayershell.layer: WlrLayer.Background`,
`keyboardFocus: None`) and save with `item.grabToImage(r => r.saveToFile(path))`, then
`Qt.quit()`. A full working example is `dev/` in
[omarchy-agents-pet](https://github.com/gusthanks/omarchy-agents-pet).

Also run `omarchy plugin validate .`. What can't be rendered in isolation (live data in
the real bar) is checked by the user after deploying, not by you on their live shell.

## Deploy once, at the end, and restart the bar

When ALL the requested work is finished, tested in isolation and committed, update the
installed copy once and then run `omarchy restart shell` once. The shell's hot reload is
not reliable for plugins (the old version can stay on screen), so do not tell the user
something is done before the restart ran. Never deploy or restart in the middle of a task,
and never use `omarchy refresh shell` (it resets the bar layout).
