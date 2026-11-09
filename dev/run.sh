#!/usr/bin/env bash
# Run the Resource Monitor dev harness in its OWN Quickshell instance.
# It never touches the live Omarchy shell or the installed plugin, so the bar
# is not reloaded or restarted while you test or take screenshots. All data
# comes from dev/mock/: no /proc sampling, no collectors, no commands run.
#
#   dev/run.sh preview      the popup content in a window, to play with (mock data)
#   dev/run.sh overview     docs/overview.png    (tab 1: CPU, memory, network, disk I/O, transfers)
#   dev/run.sh processes    docs/processes.png   (tab 2)
#   dev/run.sh disks        docs/disks.png       (tab 3, with an iPhone)
#   dev/run.sh sensors      docs/sensors.png     (tab 4)
#   dev/run.sh transfers    docs/transfers.png   (tab 5: rsync + an iPhone backup)
#   dev/run.sh docs         all of the captures above, plus preview.png
#   dev/run.sh check        compile every plugin type, Panel.qml included
#                           (declared, never instantiated); run before deploy
#   dev/run.sh test         Model.js, catalog and collector tests (node --test and
#                           python3 unittest, no packages to install)
#
# LANGUAGE picks the shell language for the run (default en), e.g.
# LANGUAGE=pt-BR dev/run.sh overview. Captures render offscreen (HEADLESS=1,
# the default), so nothing shows on the desktop; HEADLESS=0 uses a background
# layer surface instead. DEV_SCALE stands in for the monitor scale (1.6).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export OMARCHY_PATH="${OMARCHY_PATH:-/usr/share/omarchy}"
omarchy_shell="$OMARCHY_PATH/shell"
export HEADLESS="${HEADLESS:-1}"

# A throwaway Quickshell config: Omarchy's Commons/Ui libraries, this repo as
# "resources", and the harness as shell.qml. The language file is pinned here
# instead of reading ~/.config/omarchy/language.
cfg="${XDG_RUNTIME_DIR:-/tmp}/gusthx-resources-dev"
rm -rf "$cfg"
mkdir -p "$cfg"
ln -s "$omarchy_shell/Commons" "$cfg/Commons"
ln -s "$omarchy_shell/Ui" "$cfg/Ui"
ln -s "$repo" "$cfg/resources"
cp "$repo/dev/Harness.qml" "$cfg/shell.qml"
cp "$repo/dev/LayerStage.qml" "$cfg/"
lang="${LANGUAGE:-en}"; lang="${lang%%:*}"
printf '%s\n' "$lang" > "$cfg/language"
export OMARCHY_LANGUAGE_FILE="$cfg/language"

suffix=""; [ "$lang" != en ] && suffix="-$lang"

render() { # mode out
  local log="$cfg/$1.log"
  local platform=()
  [ "$HEADLESS" = 1 ] && platform=(env QT_QPA_PLATFORM=offscreen QT_SCALE_FACTOR="${DEV_SCALE:-1.6}")
  rm -f "$2"
  MODE="$1" OUT="$2" timeout 30 "${platform[@]}" quickshell -n -p "$cfg/shell.qml" >"$log" 2>&1 || true
  local noise='qmlscanner|portal|polkit|QT_QPA_PLATFORM|actually running wayland|window masks|--- WARNING ---|No PanelWindow backend|toplevel-mapping'
  if grep -aE 'WARN|ERROR' "$log" | grep -vE "$noise" | grep -q .; then
    grep -aE 'WARN|ERROR' "$log" | grep -vE "$noise" >&2
  fi
  [ -f "$2" ] && echo "${2#"$repo"/}" || { echo "no image written for $1 (log: $log)" >&2; return 1; }
}

tabs="overview processes disks sensors transfers"
mkdir -p "$repo/docs"
case "${1:-preview}" in
  preview) MODE=preview quickshell -n -p "$cfg/shell.qml" ;;
  overview|processes|disks|sensors|transfers) render "$1" "$repo/docs/$1$suffix.png" ;;
  docs)
    for m in $tabs; do "$0" "$m"; done
    # The store/README preview is the overview capture.
    [ -z "$suffix" ] && cp "$repo/docs/overview.png" "$repo/preview.png" && echo preview.png ;;
  test)
    node --test "$repo/dev/tests/"
    python3 -m unittest discover -s "$repo/dev/tests" -p 'test_*.py' ;;
  check)
    # Compile-only: types are declared inside Components that never get created,
    # so no window opens. Needs the Wayland backend (Panel uses PanelWindow).
    files=$(cd "$repo" && ls *.qml | sed 's/\.qml$//')
    { echo 'import QtQuick'; echo 'import Quickshell'; echo 'import "resources" as R'; echo 'ShellRoot {'
      i=0; for f in $files; do echo "  Component { id: c$i; R.$f {} }"; i=$((i+1)); done
      echo '  Component.onCompleted: {'
      i=0; for f in $files; do echo "    console.warn(\"CHECK $f\", c$i.status === Component.Ready ? \"OK\" : c$i.errorString())"; i=$((i+1)); done
      echo '    Qt.quit()'; echo '  }'; echo '}'; } > "$cfg/shell.qml"
    timeout 20 quickshell -n -p "$cfg/shell.qml" >"$cfg/check.log" 2>&1 || true
    if grep -q 'Failed to load configuration' "$cfg/check.log"; then grep -aE 'ERROR' "$cfg/check.log" >&2; exit 1; fi
    grep -a 'CHECK ' "$cfg/check.log" | sed 's/^.*CHECK /CHECK /'
    grep -a 'CHECK ' "$cfg/check.log" | grep -q . || { echo "check produced no output (log: $cfg/check.log)" >&2; exit 1; }
    ! grep -a 'CHECK ' "$cfg/check.log" | grep -vq ' OK$' ;;
  *) sed -n '2,25p' "$0"; exit 1 ;;
esac
