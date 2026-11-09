import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "resources" as R
import "resources/Model.js" as Model

// Isolated dev harness. Runs in its own Quickshell instance (see dev/run.sh),
// never inside the live Omarchy shell, so testing and screenshots do not
// reload or restart the user's bar. All data is MOCK (dev/mock/): the Monitor
// runs with `live: false`, so it never reads /proc, never runs the collectors
// and never kills, pauses or opens anything.
//
// MODE (env):
//   preview                       floating window with the popup, to play with
//   overview processes disks
//   sensors transfers             that tab of the popup -> $OUT (png)
ShellRoot {
  id: shell

  readonly property string mode: Quickshell.env("MODE") || "preview"
  readonly property string out: Quickshell.env("OUT") || ""
  readonly property bool capturing: mode !== "preview"
  readonly property bool headless: Quickshell.env("HEADLESS") === "1"
  readonly property var tabs: ["overview", "processes", "disks", "sensors", "transfers"]
  property Item stage: null

  function save(item, path, scale, done) {
    item.grabToImage(function(r) {
      r.saveToFile(path)
      if (done) done()
    }, Qt.size(item.width * scale, item.height * scale))
  }

  // A smooth, repeatable series for the sparklines (no randomness, so
  // captures do not change between runs).
  function series(base, swing, period, phase, n) {
    var out = []
    for (var i = 0; i < n; i++) {
      var v = base + swing * Math.sin((i + phase) / period) + swing * 0.35 * Math.sin((i + phase) * 0.9)
      out.push(Math.max(0, v))
    }
    return out
  }

  FileView { id: detailsFile; path: Qt.resolvedUrl("resources/dev/mock/details.txt"); blockLoading: true }
  FileView { id: transfersFile; path: Qt.resolvedUrl("resources/dev/mock/transfers.json"); blockLoading: true }

  R.Monitor {
    id: mon
    live: false
    activeTab: Math.max(0, shell.tabs.indexOf(shell.mode))
    selectedProcIndex: shell.mode === "processes" ? 1 : 0

    Component.onCompleted: {
      var n = 60
      cpuPct = 34; cpuUserPct = 24; cpuSysPct = 8; cpuIowaitPct = 2
      cpuCores = [
        { id: 0, pct: 52 }, { id: 1, pct: 41 }, { id: 2, pct: 28 }, { id: 3, pct: 63 },
        { id: 4, pct: 19 }, { id: 5, pct: 33 }, { id: 6, pct: 12 }, { id: 7, pct: 24 }
      ]
      memTotalBytes = 16 * 1073741824
      memUsedBytes = 9.4 * 1073741824
      memAvailBytes = memTotalBytes - memUsedBytes
      memCachedBytes = 4.1 * 1073741824
      memBuffersBytes = 0.3 * 1073741824
      memPct = memUsedBytes / memTotalBytes * 100
      swapTotalBytes = 8 * 1073741824
      swapUsedBytes = 0.6 * 1073741824
      swapPct = swapUsedBytes / swapTotalBytes * 100
      load1 = 2.14; load5 = 1.87; load15 = 1.52
      runningThreads = 3; totalThreads = 1412
      uptimeFormatted = "3h 42m"
      ctxtRate = 18420
      netIface = "wlan0"
      netRxRateBps = 2.4 * 1048576; netTxRateBps = 310 * 1024
      netTotalRxBytes = 8.7 * 1073741824; netTotalTxBytes = 1.2 * 1073741824
      diskReadRateBps = 92 * 1048576; diskWriteRateBps = 99 * 1048576
      diskTotalReadBytes = 61.3 * 1073741824; diskTotalWriteBytes = 48.9 * 1073741824
      cpuTempC = 54
      cpuHistory = shell.series(32, 12, 6, 0, n)
      memHistory = shell.series(58, 2, 15, 3, n)
      netRxHistory = shell.series(1.8 * 1048576, 1.1 * 1048576, 5, 1, n)
      netTxHistory = shell.series(0.3 * 1048576, 0.2 * 1048576, 4, 7, n)
      diskReadHistory = shell.series(80 * 1048576, 25 * 1048576, 7, 2, n)
      diskWriteHistory = shell.series(85 * 1048576, 22 * 1048576, 6, 5, n)
      historyRevision = 1
      details = Model.parseDetailsOutput(detailsFile.text())
      transferData = Model.parseJsonSafely(transfersFile.text(), transferData)
    }
  }

  Component {
    id: stageComponent
    Rectangle {
      id: frame
      readonly property int pad: Style.spacing.popupPadding
      color: Commons.Color.background
      border.width: Math.max(1, Style.space(2))
      border.color: Commons.Color.popups.border
      radius: Style.cornerRadius
      width: Style.space(450) + pad * 2
      height: view.implicitHeight + pad * 2
      Component.onCompleted: shell.stage = frame

      R.ResourcesView {
        id: view
        x: frame.pad
        y: frame.pad
        width: Style.space(450)
        height: implicitHeight
        monitor: mon
        focus: true
      }
    }
  }

  // HEADLESS=1 (the dev/run.sh default): Qt's offscreen platform, nothing
  // reaches the desktop. Otherwise a background-layer surface below every
  // window. LayerStage is its own file: offscreen has no PanelWindow backend.
  LazyLoader {
    active: shell.capturing && !shell.headless
    source: "LayerStage.qml"
    onItemChanged: if (item) item.content = stageComponent
  }

  FloatingWindow {
    visible: !shell.capturing || shell.headless
    implicitWidth: offLoader.item ? offLoader.item.width : 10
    implicitHeight: offLoader.item ? offLoader.item.height : 10
    color: "transparent"
    Loader { id: offLoader; active: !shell.capturing || shell.headless; sourceComponent: stageComponent }
  }

  Timer {
    running: shell.capturing
    interval: 1500
    onTriggered: shell.save(shell.stage, shell.out, 2, function() { Qt.quit() })
  }
}
