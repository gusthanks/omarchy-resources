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
//   gif                           the Transfers tab with the mock transfers
//                                 progressing, then the batch paused and
//                                 resumed: one png per frame -> $OUT/0000.png
//                                 ... + $OUT/fps (dev/run.sh makes the GIF)
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
    activeTab: shell.mode === "gif" ? 4 : Math.max(0, shell.tabs.indexOf(shell.mode))
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
      shell.baseTransfers = JSON.stringify(transferData)
    }
  }

  // ---------------------------------------------------------------- gif mode
  // The mock transfers advanced by `t` simulated seconds, deterministic so the
  // GIF does not change between runs. While `paused`, the whole batch is
  // stopped (as after "Pause batch"): no speed, no ETA, time stands still.
  property string baseTransfers: ""
  readonly property int gifFrames: 96
  readonly property int gifFps: 10
  readonly property int gifPauseFrom: 52
  readonly property int gifPauseTo: 72
  readonly property real gifSecondsPerFrame: 2

  function gifState(f) {
    var paused = f >= gifPauseFrom && f < gifPauseTo
    var running = Math.min(f, gifPauseFrom) + Math.max(0, f - gifPauseTo)
    var t = running * gifSecondsPerFrame
    var d = JSON.parse(baseTransfers)
    var videos = ["VID_0193.MOV", "VID_0194.MOV", "VID_0195.MOV", "VID_0196.MOV", "VID_0197.MOV", "VID_0198.MOV"]
    var aggMoved = 0, aggTotal = 0, aggSpeed = 0, moved = 0
    for (var i = 0; i < d.transfers.length; i++) {
      var x = d.transfers[i]
      var speed = x.active_speed * (1 + 0.06 * Math.sin(f * 0.7 + i * 2))
      var gained = x.active_speed * t
      moved += gained
      x.total_bytes += gained
      x.elapsed_sec += t
      x.elapsed_str = Model.formatDuration(x.elapsed_sec)
      x.overall_pct = Math.round(x.total_bytes / x.overall_total_bytes * 1000) / 10
      x.active_speed = paused ? 0 : speed
      x.overall_eta = paused ? "" : Model.formatDuration((x.overall_total_bytes - x.total_bytes) / speed)
      if (paused) x.state = "paused"
      if (i === 0) {
        // rsync: a run of large videos, one after the other
        var fileSize = 2400000000
        var into = gained + 900000000
        var k = Math.floor(into / fileSize)
        var pos = into - k * fileSize
        x.active_file = { name: videos[k % videos.length], size: fileSize, pos: pos,
                          pct: Math.round(pos / fileSize * 1000) / 10, writing: false }
        x.file_eta = paused ? "" : Model.formatDuration((fileSize - pos) / speed)
      } else if (x.active_file) {
        var fsz = x.active_file.size
        var fpos = Math.min(fsz, x.active_file.pos + gained)
        x.active_file.pos = fpos
        x.active_file.pct = Math.round(fpos / fsz * 1000) / 10
        x.file_eta = paused ? "" : Model.formatDuration((fsz - fpos) / speed)
      }
      if (x.batch) {
        x.batch.elapsed_sec += t
        x.batch.elapsed_str = Model.formatDuration(x.batch.elapsed_sec)
        if (paused) x.batch.state = "paused"
      }
      aggMoved += x.total_bytes
      aggTotal += x.overall_total_bytes
      aggSpeed += x.active_speed
    }
    for (var b = 0; b < d.batches.length; b++) {
      var bt = d.batches[b]
      bt.total_bytes += moved
      bt.elapsed_sec += t
      bt.elapsed_str = Model.formatDuration(bt.elapsed_sec)
      bt.active_speed = aggSpeed
      if (paused) bt.state = "paused"
    }
    d.agg_total_bytes = aggMoved
    d.agg_overall_total_bytes = aggTotal
    d.agg_overall_pct = Math.round(aggMoved / aggTotal * 1000) / 10
    d.agg_speed = aggSpeed
    return d
  }

  property int gifFrame: 0
  function pad(n) { var s = String(n); while (s.length < 4) s = "0" + s; return s }
  function gifNext() {
    if (gifFrame >= gifFrames) {
      gifFpsFile.setText(String(gifFps) + "\n")
      Qt.callLater(Qt.quit)
      return
    }
    mon.transferData = gifState(gifFrame)
    var f = gifFrame
    gifFrame++
    // let the bindings settle before the grab
    Qt.callLater(function() { shell.save(shell.stage, shell.out + "/" + pad(f) + ".png", 1, function() { gifGap.restart() }) })
  }
  Timer { id: gifGap; interval: 40; onTriggered: shell.gifNext() }
  FileView { id: gifFpsFile; path: shell.mode === "gif" ? shell.out + "/fps" : ""; printErrors: false }

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
      // The GIF shows the top of the Transfers tab: the summary card and the
      // first transfer, a fixed size for every frame.
      height: shell.mode === "gif" ? Style.space(574) : view.implicitHeight + pad * 2
      clip: shell.mode === "gif"
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
    onTriggered: shell.mode === "gif"
      ? shell.gifNext()
      : shell.save(shell.stage, shell.out, 2, function() { Qt.quit() })
  }
}
