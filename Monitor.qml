import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import "Model.js" as Model

// Data, sampling and actions for the Resource Monitor. Panel.qml owns one for
// the bar; dev/Harness.qml makes one with `live: false` and fills it with mock
// data. ResourcesView.qml reads and drives everything through this object.
Item {
  id: root

  // Set by Panel.qml.
  property QtObject bar: null
  property bool opened: false
  property string barMode: "compact"
  // false: never reads /proc, never runs the collectors or any command (dev harness).
  property bool live: true

  signal closeRequested()
  signal cycleModeRequested()

  Commons.Translations {
    id: tr
    directory: Qt.resolvedUrl("i18n")
  }
  function t(key, fallback) { return tr.t(key, fallback) }

  // ---- Live metrics state ----
  property real cpuPct: 0
  property real cpuUserPct: 0
  property real cpuSysPct: 0
  property real cpuIowaitPct: 0
  property var cpuCores: []
  property int procsRunning: 0
  property int procsBlocked: 0
  property int ctxtRate: 0
  property var prevCpuRaw: null

  property real memTotalBytes: 0
  property real memUsedBytes: 0
  property real memAvailBytes: 0
  property real memCachedBytes: 0
  property real memBuffersBytes: 0
  property real memPct: 0
  property real swapTotalBytes: 0
  property real swapUsedBytes: 0
  property real swapPct: 0

  property real load1: 0
  property real load5: 0
  property real load15: 0
  property int runningThreads: 0
  property int totalThreads: 0
  property string uptimeFormatted: "0m"

  property string netIface: "net"
  property real netRxRateBps: 0
  property real netTxRateBps: 0
  property real netTotalRxBytes: 0
  property real netTotalTxBytes: 0
  property var prevNetRaw: null

  property real diskReadRateBps: 0
  property real diskWriteRateBps: 0
  property real diskTotalReadBytes: 0
  property real diskTotalWriteBytes: 0
  property var prevDiskRaw: null

  property int cpuTempC: 0
  property double lastSampleMs: 0

  // ---- Rolling 60-point histories (2 minutes) ----
  property var cpuHistory: []
  property var memHistory: []
  property var netRxHistory: []
  property var netTxHistory: []
  property var diskReadHistory: []
  property var diskWriteHistory: []
  property int historyRevision: 0

  // ---- Detailed panel state (updated while panel is open) ----
  property var details: ({
    cpuModel: "Processor",
    kernel: "Linux",
    gpuCurMhz: 0,
    gpuMaxMhz: 0,
    gpuBoostMhz: 0,
    pkgTempC: 0,
    coreFreqs: ({}),
    avgFreqMhz: 0,
    sensors: [],
    disks: [],
    topCpuProcs: [],
    topMemProcs: []
  })

  // ---- Panel interaction state ----
  property int activeTab: 0 // 0=Overview, 1=Processes, 2=Disks, 3=Sensors, 4=Transfers
  property string procSort: "cpu" // "cpu" | "mem"
  property int selectedProcIndex: 0
  property string killFeedbackText: ""

  // ---- Transfers state ----
  property var transferData: ({
    count: 0,
    agg_read_speed: 0,
    agg_write_speed: 0,
    agg_speed: 0,
    transfers: [],
    recent_completed: [],
    batches: [],
    events: []
  })
  readonly property var transferList: transferData.transfers || []
  readonly property var recentTransferList: transferData.recent_completed || []
  readonly property int transferCount: transferData.count || 0
  // count = every listed item; transfer_count / check_count split copies from
  // dry runs, listings and checksum verifications (older collectors: count).
  readonly property int transferOnlyCount: transferData.transfer_count !== undefined ? transferData.transfer_count : transferCount
  readonly property int checkCount: transferData.check_count || 0
  readonly property real aggTransferSpeed: transferData.agg_speed || 0
  readonly property bool hasActiveTransfers: transferCount > 0
  property int selectedTransferIndex: 0
  property string transferStatusMessage: ""
  // Collector events already notified (by id), so a repeated payload never
  // notifies twice. The collector only reports; the notification text is
  // translated here.
  property var notifiedEventIds: ({})
  property var notifiedEventOrder: []

  readonly property string pluginDir: Qt.resolvedUrl(".").toString().replace(/^file:\/\//, "")
  readonly property string collectorScript: pluginDir + "/collect-details.sh"
  readonly property string transferCollectorScript: pluginDir + "/collect-transfers.py"

  readonly property int effectiveTempC: details.pkgTempC > 0 ? details.pkgTempC : cpuTempC
  readonly property bool urgentState: cpuPct >= 90 || memPct >= 90 || effectiveTempC >= 85

  readonly property color fgColor: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accentColor: Commons.Color.accent
  readonly property color urgentColor: bar ? bar.urgent : Commons.Color.urgent

  function fmtNum(val, dec) {
    return Commons.I18n.formatNumber(val, dec)
  }

  function modeDisplayName(m) {
    if (m === "detailed") return tr.t("mode.detailed")
    if (m === "full") return tr.t("mode.full")
    if (m === "icon") return tr.t("mode.icon")
    return tr.t("mode.compact")
  }

  function close() { root.closeRequested() }
  function cycleBarMode() { root.cycleModeRequested() }

  function refreshAll() {
    if (!root.live) return
    sampleFastMetrics()
    refreshDetails()
    pollTransfers()
  }

  function openBtop() {
    root.close()
    if (!root.live) return
    if (root.bar) {
      root.bar.run("omarchy-launch-or-focus-tui btop")
    } else {
      Quickshell.execDetached(["omarchy-launch-or-focus-tui", "btop"])
    }
  }

  function currentProcList() {
    var list = procSort === "mem" ? details.topMemProcs : details.topCpuProcs
    return Array.isArray(list) ? list : []
  }

  function killProcess(pid, name) {
    var numPid = parseInt(pid, 10)
    if (!root.live || !numPid || numPid <= 1 || killProc.running) return
    killFeedbackText = tr.t("proc.killed").arg(String(numPid)).arg(Model.neutralizeMarkup(name, 24))
    feedbackClearTimer.restart()
    killProc.command = ["kill", "-15", String(numPid)]
    killProc.running = true
  }

  // ---- Zero-spawn kernel /proc readers ----
  FileView {
    id: statFile
    path: root.live ? "/proc/stat" : ""
    printErrors: false
  }
  FileView {
    id: memFile
    path: root.live ? "/proc/meminfo" : ""
    printErrors: false
  }
  FileView {
    id: loadFile
    path: root.live ? "/proc/loadavg" : ""
    printErrors: false
  }
  FileView {
    id: uptimeFile
    path: root.live ? "/proc/uptime" : ""
    printErrors: false
  }
  FileView {
    id: netFile
    path: root.live ? "/proc/net/dev" : ""
    printErrors: false
  }
  FileView {
    id: diskstatsFile
    path: root.live ? "/proc/diskstats" : ""
    printErrors: false
  }
  FileView {
    id: tempZone6File
    path: root.live ? "/sys/class/thermal/thermal_zone6/temp" : ""
    printErrors: false
  }
  FileView {
    id: tempZone4File
    path: root.live ? "/sys/class/thermal/thermal_zone4/temp" : ""
    printErrors: false
  }
  FileView {
    id: tempZone0File
    path: root.live ? "/sys/class/thermal/thermal_zone0/temp" : ""
    printErrors: false
  }

  function sampleFastMetrics() {
    if (!root.live) return
    statFile.reload()
    memFile.reload()
    loadFile.reload()
    uptimeFile.reload()
    netFile.reload()
    diskstatsFile.reload()
    tempZone6File.reload()
    tempZone4File.reload()
    tempZone0File.reload()

    var nowMs = Date.now()
    var elapsedSec = lastSampleMs > 0 ? Math.max(0.2, (nowMs - lastSampleMs) / 1000.0) : 1.0
    lastSampleMs = nowMs

    var cpuParsed = Model.parseProcStat(statFile.text(), prevCpuRaw, elapsedSec)
    prevCpuRaw = cpuParsed.rawState
    cpuPct = cpuParsed.cpuPct
    cpuUserPct = cpuParsed.userPct
    cpuSysPct = cpuParsed.sysPct
    cpuIowaitPct = cpuParsed.iowaitPct
    cpuCores = cpuParsed.cores
    procsRunning = cpuParsed.procsRunning
    procsBlocked = cpuParsed.procsBlocked
    if (cpuParsed.ctxtRate > 0) ctxtRate = cpuParsed.ctxtRate

    var memParsed = Model.parseProcMeminfo(memFile.text())
    memTotalBytes = memParsed.totalBytes
    memUsedBytes = memParsed.usedBytes
    memAvailBytes = memParsed.availBytes
    memCachedBytes = memParsed.cachedBytes
    memBuffersBytes = memParsed.buffersBytes
    memPct = memParsed.pct
    swapTotalBytes = memParsed.swapTotalBytes
    swapUsedBytes = memParsed.swapUsedBytes
    swapPct = memParsed.swapPct

    var loadParsed = Model.parseProcLoadavg(loadFile.text())
    load1 = loadParsed.load1
    load5 = loadParsed.load5
    load15 = loadParsed.load15
    runningThreads = loadParsed.runningThreads
    totalThreads = loadParsed.totalThreads

    var upParsed = Model.parseProcUptime(uptimeFile.text())
    uptimeFormatted = upParsed.formatted

    var netParsed = Model.parseProcNetDev(netFile.text(), prevNetRaw, elapsedSec)
    prevNetRaw = netParsed.rawState
    netIface = netParsed.iface
    netRxRateBps = netParsed.rxRateBps
    netTxRateBps = netParsed.txRateBps
    netTotalRxBytes = netParsed.totalRxBytes
    netTotalTxBytes = netParsed.totalTxBytes

    var diskParsed = Model.parseProcDiskstats(diskstatsFile.text(), prevDiskRaw, elapsedSec)
    prevDiskRaw = diskParsed.rawState
    diskReadRateBps = diskParsed.readRateBps
    diskWriteRateBps = diskParsed.writeRateBps
    diskTotalReadBytes = diskParsed.totalReadBytes
    diskTotalWriteBytes = diskParsed.totalWriteBytes

    var rawTemp = parseInt(tempZone6File.text().trim(), 10)
      || parseInt(tempZone4File.text().trim(), 10)
      || parseInt(tempZone0File.text().trim(), 10)
      || 0
    if (rawTemp > 1000) cpuTempC = Math.round(rawTemp / 1000)

    cpuHistory = Model.pushHistory(cpuHistory, cpuPct, 60)
    memHistory = Model.pushHistory(memHistory, memPct, 60)
    netRxHistory = Model.pushHistory(netRxHistory, netRxRateBps, 60)
    netTxHistory = Model.pushHistory(netTxHistory, netTxRateBps, 60)
    diskReadHistory = Model.pushHistory(diskReadHistory, diskReadRateBps, 60)
    diskWriteHistory = Model.pushHistory(diskWriteHistory, diskWriteRateBps, 60)
    historyRevision = (historyRevision + 1) % 1000000
  }

  function refreshDetails() {
    if (root.live && !detailsProc.running) {
      detailsProc.running = true
    }
  }

  Process {
    id: detailsProc
    command: ["bash", root.collectorScript]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text && text.length > 0) {
          root.details = Model.parseDetailsOutput(text)
        }
      }
    }
  }

  Process {
    id: killProc
    onExited: root.refreshDetails()
  }

  function pollTransfers() {
    if (root.live && !transferPollProc.running) {
      transferPollProc.running = true
    }
  }

  Process {
    id: transferPollProc
    command: ["python3", root.transferCollectorScript]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        if (text && text.length > 0) {
          var parsed = Model.parseJsonSafely(text, null)
          if (parsed && typeof parsed === "object") {
            root.transferData = parsed
            root.notifyTransferEvents(parsed.events)
          }
        }
      }
    }
  }

  function notifyTransferEvents(events) {
    if (!root.live || !Array.isArray(events)) return
    for (var i = 0; i < events.length; i++) {
      var ev = events[i]
      if (!ev || !ev.notify || !ev.id || root.notifiedEventIds[ev.id]) continue
      root.notifiedEventIds[ev.id] = true
      root.notifiedEventOrder.push(ev.id)
      while (root.notifiedEventOrder.length > 50) delete root.notifiedEventIds[root.notifiedEventOrder.shift()]
      var n = Model.notificationFor(ev, root.t, root.fmtNum)
      if (n) Quickshell.execDetached(["notify-send", "--app-name=" + tr.t("notify.appName"), "-i", "drive-harddisk", "--", n.title, n.body])
    }
  }

  Process {
    id: transferActionProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.pollTransfers()
      }
    }
  }

  function runTransferAction(flag, arg) {
    if (!root.live) return
    transferActionProc.command = ["python3", root.transferCollectorScript, flag, String(arg)]
    transferActionProc.running = true
  }

  function pauseTransfer(pid) { runTransferAction("--pause", pid) }
  function resumeTransfer(pid) { runTransferAction("--resume", pid) }
  function cancelTransfer(pid) { runTransferAction("--cancel", pid) }
  function openTransferPath(path) { if (path) runTransferAction("--open", path) }
  // Disks on the Disks tab open in the default file manager. iPhone/iPad
  // entries (fstype "afc") have no local mount point, so they are not clickable.
  function canOpenDisk(disk) {
    return Boolean(disk && disk.mount && disk.fstype !== "afc" && Model.diskOpenPath(disk.mount))
  }
  function openDiskFolder(disk) {
    if (!canOpenDisk(disk)) return
    var target = Model.diskOpenPath(disk.mount)
    root.close()
    if (root.live) Quickshell.execDetached(["xdg-open", target])
  }
  function copyTransferText(str) {
    if (str) {
      runTransferAction("--copy", str)
      root.transferStatusMessage = tr.t("transfers.copied")
      transferStatusTimer.restart()
    }
  }

  Timer {
    id: transferStatusTimer
    interval: 2000
    onTriggered: root.transferStatusMessage = ""
  }

  Timer {
    id: transferSampleTimer
    interval: root.opened ? 1500 : 3500
    running: root.live
    repeat: true
    triggeredOnStart: true
    onTriggered: root.pollTransfers()
  }

  Timer {
    id: feedbackClearTimer
    interval: 3500
    onTriggered: root.killFeedbackText = ""
  }

  Timer {
    id: fastSampleTimer
    interval: root.opened ? 1500 : 2500
    running: root.live
    repeat: true
    triggeredOnStart: true
    onTriggered: root.sampleFastMetrics()
  }

  Timer {
    id: detailsSampleTimer
    interval: 2500
    running: root.live && root.opened
    repeat: true
    onTriggered: root.refreshDetails()
  }
}
