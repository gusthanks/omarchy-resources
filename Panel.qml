import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

Panel {
  id: root
  moduleName: "gusthx.resources"
  ipcTarget: "gusthx.resources"
  manageIpc: false

  Commons.Translations {
    id: tr
    directory: Qt.resolvedUrl("i18n")
  }

  // ---- Inline bar settings (persisted in shell.json) ----
  readonly property var barModes: ["compact", "detailed", "full", "icon"]
  readonly property string barMode: {
    var m = String(setting("barMode", "compact"))
    return barModes.indexOf(m) >= 0 ? m : "compact"
  }

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
    recent_completed: []
  })
  readonly property var transferList: transferData.transfers || []
  readonly property var recentTransferList: transferData.recent_completed || []
  readonly property int transferCount: transferData.count || 0
  readonly property real aggTransferSpeed: transferData.agg_speed || 0
  readonly property bool hasActiveTransfers: transferCount > 0
  property int selectedTransferIndex: 0
  property string transferStatusMessage: ""

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

  readonly property string barDisplayText: {
    if (button.vertical || barMode === "icon") {
      return "󰍛"
    }
    var cStr = fmtNum(Math.round(cpuPct)) + "%"
    var mStr = fmtNum(Math.round(memPct)) + "%"
    if (barMode === "detailed") {
      var tPart = effectiveTempC > 0 ? (" · " + fmtNum(effectiveTempC) + "°C") : ""
      var mShort = Model.formatShortBytes(memUsedBytes, fmtNum)
      return "󰍛 " + cStr + tPart + "  󰘚 " + mShort
    }
    if (barMode === "full") {
      var nRate = Model.formatShortRate(netRxRateBps + netTxRateBps, fmtNum)
      return "󰍛 " + cStr + "  󰘚 " + mStr + "  󰇚 " + nRate
    }
    return "󰍛 " + cStr + "  󰘚 " + mStr
  }

  readonly property string barTooltip: {
    var cStr = fmtNum(Math.round(cpuPct))
    var mStr = fmtNum(Math.round(memPct))
    var mBytes = Model.formatBytes(memUsedBytes, 1, fmtNum) + " / " + Model.formatBytes(memTotalBytes, 0, fmtNum)
    var mName = modeDisplayName(barMode)
    return Model.neutralizeMarkup(
      tr.t("bar.tooltip").arg(cStr).arg(mStr).arg(mBytes).arg(mName),
      260
    )
  }

  readonly property real openPanelIndicatorWidth: !button.vertical && barMode !== "icon"
    ? Math.max(Style.space(14), Math.round(button.labelWidth * 0.7))
    : 0

  function cycleBarMode() {
    var idx = barModes.indexOf(barMode)
    var nextMode = barModes[(idx + 1) % barModes.length]
    root.settings = Object.assign({}, root.settings, { barMode: nextMode })
    if (root.bar && root.bar.shell) {
      root.bar.shell.updateEntryInline(root.moduleName, root.settings)
    }
  }

  function openBtop() {
    root.close()
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
    if (!numPid || numPid <= 1 || killProc.running) return
    killFeedbackText = tr.t("proc.killed").arg(String(numPid)).arg(Model.neutralizeMarkup(name, 24))
    feedbackClearTimer.restart()
    killProc.command = ["kill", "-15", String(numPid)]
    killProc.running = true
  }

  // ---- Zero-spawn kernel /proc readers ----
  FileView {
    id: statFile
    path: "/proc/stat"
    printErrors: false
  }
  FileView {
    id: memFile
    path: "/proc/meminfo"
    printErrors: false
  }
  FileView {
    id: loadFile
    path: "/proc/loadavg"
    printErrors: false
  }
  FileView {
    id: uptimeFile
    path: "/proc/uptime"
    printErrors: false
  }
  FileView {
    id: netFile
    path: "/proc/net/dev"
    printErrors: false
  }
  FileView {
    id: diskstatsFile
    path: "/proc/diskstats"
    printErrors: false
  }
  FileView {
    id: tempZone6File
    path: "/sys/class/thermal/thermal_zone6/temp"
    printErrors: false
  }
  FileView {
    id: tempZone4File
    path: "/sys/class/thermal/thermal_zone4/temp"
    printErrors: false
  }
  FileView {
    id: tempZone0File
    path: "/sys/class/thermal/thermal_zone0/temp"
    printErrors: false
  }

  function sampleFastMetrics() {
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
    if (!detailsProc.running) {
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
    if (!transferPollProc.running) {
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
          }
        }
      }
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
    transferActionProc.command = ["python3", root.transferCollectorScript, flag, String(arg)]
    transferActionProc.running = true
  }

  function pauseTransfer(pid) { runTransferAction("--pause", pid) }
  function resumeTransfer(pid) { runTransferAction("--resume", pid) }
  function cancelTransfer(pid) { runTransferAction("--cancel", pid) }
  function openTransferPath(path) { if (path) runTransferAction("--open", path) }
  function openDiskFolder(mountPath) {
    if (!mountPath) return
    var target = String(mountPath).trim()
    if (target.indexOf("·") >= 0 || target.indexOf("•") >= 0) {
      var parts = target.split(/[·•]/).map(function(s) { return s.trim() })
      target = parts.indexOf("/home") >= 0 ? "/home" : parts[0]
    }
    root.close()
    Quickshell.execDetached(["nautilus", target])
  }
  function copyTransferText(str) {
    if (str) {
      runTransferAction("--copy", str)
      root.transferStatusMessage = "Copiado!"
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
    running: true
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
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.sampleFastMetrics()
  }

  Timer {
    id: detailsSampleTimer
    interval: 2500
    running: root.opened
    repeat: true
    onTriggered: root.refreshDetails()
  }

  function resetTabScroll() {
    var c = panel.loadedContent
    if (c && typeof c.resetScroll === "function") {
      c.resetScroll()
    }
  }

  function scrollBy(delta) {
    var c = panel.loadedContent
    if (c && typeof c.scrollBy === "function") {
      c.scrollBy(delta)
    }
  }

  onActiveTabChanged: root.resetTabScroll()

  onOpenedChanged: {
    if (opened) {
      sampleFastMetrics()
      refreshDetails()
      pollTransfers()
      root.resetTabScroll()
    }
  }

  ShellIpc {
    target: "gusthx.resources"

    function open() { root.open() }
    function close() { root.close() }
    function show() { root.open() }
    function hide() { root.close() }
    function toggle() { root.toggle() }
    function cycleMode() { root.cycleBarMode() }
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // ---- Bar Widget Button ----
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barDisplayText
    active: root.urgentState
    tooltipText: root.barTooltip
    onPressed: function(b) {
      if (b === Qt.RightButton) root.cycleBarMode()
      else if (b === Qt.MiddleButton) root.openBtop()
      else root.toggle()
    }
  }

  // ---- Popup Panel ----
  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: panel.loadedContent
    contentWidth: panel.fittedContentWidth(Style.space(450))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight)

    content: PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      implicitHeight: headerColumn.implicitHeight + Style.space(12) + tabContentColumn.implicitHeight

      function resetScroll() {
        if (tabScrollView && tabScrollView.contentItem) {
          tabScrollView.contentItem.contentY = 0
        }
      }

      function scrollBy(delta) {
        if (tabScrollView && tabScrollView.contentItem) {
          var flick = tabScrollView.contentItem
          var maxScroll = Math.max(0, flick.contentHeight - tabScrollView.height)
          flick.contentY = Math.max(0, Math.min(maxScroll, flick.contentY + delta))
        }
      }

      function ensureVisible(topY, itemH) {
        if (tabScrollView && tabScrollView.contentItem) {
          var flick = tabScrollView.contentItem
          var itemBottom = topY + itemH
          if (itemBottom > flick.contentY + tabScrollView.height) {
            flick.contentY = Math.max(0, itemBottom - tabScrollView.height + Style.space(8))
          } else if (topY < flick.contentY) {
            flick.contentY = Math.max(0, topY - Style.space(8))
          }
        }
      }

      onCloseRequested: root.close()
      onTabRequested: function(direction) {
        root.activeTab = (root.activeTab + direction + 5) % 5
      }
      onMoveRequested: function(dx, dy) {
        if (dx !== 0) {
          root.activeTab = (root.activeTab + dx + 5) % 5
        } else if (dy !== 0 && root.activeTab === 1) {
          var list = root.currentProcList()
          if (list.length > 0) {
            root.selectedProcIndex = Math.max(0, Math.min(list.length - 1, root.selectedProcIndex + dy))
            keyCatcher.ensureVisible(Style.space(80) + root.selectedProcIndex * Style.space(36), Style.space(32))
          }
        } else if (dy !== 0 && root.activeTab === 4) {
          if (root.transferList.length > 0) {
            root.selectedTransferIndex = Math.max(0, Math.min(root.transferList.length - 1, root.selectedTransferIndex + dy))
            keyCatcher.ensureVisible(Style.space(40) + root.selectedTransferIndex * Style.space(80), Style.space(70))
          }
        } else if (dy !== 0) {
          keyCatcher.scrollBy(dy * Style.space(60))
        }
      }
      onDeleteRequested: {
        if (root.activeTab === 1) {
          var list = root.currentProcList()
          if (root.selectedProcIndex >= 0 && root.selectedProcIndex < list.length) {
            var p = list[root.selectedProcIndex]
            root.killProcess(p.pid, p.name)
          }
        } else if (root.activeTab === 4 && root.transferList.length > 0) {
          var tObj = root.transferList[root.selectedTransferIndex] || root.transferList[0]
          if (tObj) root.cancelTransfer(tObj.pid)
        }
      }
      onTextKey: function(t) {
        if (t === "1") root.activeTab = 0
        else if (t === "2") root.activeTab = 1
        else if (t === "3") root.activeTab = 2
        else if (t === "4") root.activeTab = 3
        else if (t === "5") root.activeTab = 4
        else if (t === "b" || t === "B") root.openBtop()
        else if (t === "m" || t === "M") root.cycleBarMode()
        else if ((t === "s" || t === "S") && root.activeTab === 1) {
          root.procSort = root.procSort === "cpu" ? "mem" : "cpu"
        } else if (root.activeTab === 4 && root.transferList.length > 0) {
          var curT = root.transferList[root.selectedTransferIndex] || root.transferList[0]
          if (t === "p" || t === "P") {
            if (curT.state === "paused") root.resumeTransfer(curT.pid)
            else root.pauseTransfer(curT.pid)
          } else if (t === "o" || t === "O") {
            root.openTransferPath(curT.destination || curT.source)
          } else if (t === "c" || t === "C") {
            root.copyTransferText(curT.command)
          } else if (t === "x" || t === "X") {
            root.cancelTransfer(curT.pid)
          }
        } else if (t === "r" || t === "R") {
          root.sampleFastMetrics()
          root.refreshDetails()
          root.pollTransfers()
        }
      }

      Keys.onPressed: function(event) {
        if (event.key === Qt.Key_PageDown) {
          keyCatcher.scrollBy(Style.space(220))
          event.accepted = true
        } else if (event.key === Qt.Key_PageUp) {
          keyCatcher.scrollBy(-Style.space(220))
          event.accepted = true
        } else if (event.key === Qt.Key_Home) {
          keyCatcher.resetScroll()
          event.accepted = true
        } else if (event.key === Qt.Key_End) {
          if (tabScrollView && tabScrollView.contentItem) {
            var flick = tabScrollView.contentItem
            flick.contentY = Math.max(0, flick.contentHeight - tabScrollView.height)
            event.accepted = true
          }
        }
      }

      Column {
        id: headerColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(12)

        // ==================== HERO HEADER ====================
        Item {
          width: parent.width
          implicitHeight: Math.max(heroLeft.implicitHeight, heroActions.implicitHeight)

          Row {
            id: heroLeft
            anchors.left: parent.left
            anchors.right: heroActions.left
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(12)

            PlainText {
              text: "󰍛"
              color: root.urgentState ? root.urgentColor : root.fgColor
              font.pixelSize: Style.font.display
              anchors.verticalCenter: parent.verticalCenter
            }

            Column {
              width: Math.max(Style.space(120), parent.width - Style.space(44))
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              PlainText {
                text: tr.t("title")
                font.pixelSize: Style.font.title
                font.bold: true
                width: parent.width
              }

              PlainText {
                text: tr.t("subtitle")
                  .arg(root.uptimeFormatted)
                  .arg(root.fmtNum(root.load1, 2) + " / " + root.fmtNum(root.load5, 2))
                color: Qt.darker(root.fgColor, 1.4)
                font.pixelSize: Style.font.caption
                font.bold: true
                width: parent.width
              }
            }
          }

          Row {
            id: heroActions
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(6)

            // Live CPU Temp Pill
            Rectangle {
              visible: root.effectiveTempC > 0
              anchors.verticalCenter: parent.verticalCenter
              radius: Style.cornerRadius > 0 ? Math.min(Style.space(6), Style.cornerRadius) : 0
              color: root.effectiveTempC >= 80
                ? Qt.rgba(root.urgentColor.r, root.urgentColor.g, root.urgentColor.b, 0.22)
                : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
              border.width: 1
              border.color: root.effectiveTempC >= 80
                ? root.urgentColor
                : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.2)
              implicitWidth: tempLabel.implicitWidth + Style.space(14)
              implicitHeight: Style.space(26)

              PlainText {
                id: tempLabel
                anchors.centerIn: parent
                text: "󰔏 " + root.fmtNum(root.effectiveTempC) + "°C"
                color: root.effectiveTempC >= 80 ? root.urgentColor : root.fgColor
                font.pixelSize: Style.font.bodySmall
                font.bold: true
              }
            }

            Button {
              iconText: "󰒓"
              iconSize: Style.font.body
              tooltipText: Model.neutralizeMarkup(
                tr.t("mode.buttonTooltip").arg(root.modeDisplayName(root.barMode)),
                120
              )
              foreground: root.fgColor
              fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
              horizontalPadding: Style.space(8)
              verticalPadding: Style.space(5)
              bordered: true
              onClicked: root.cycleBarMode()
            }

            Button {
              iconText: "󰆍"
              text: "btop"
              fontSize: Style.font.caption
              iconSize: Style.font.bodySmall
              tooltipText: Model.neutralizeMarkup(tr.t("btop.buttonTooltip"), 120)
              foreground: root.fgColor
              fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
              horizontalPadding: Style.space(8)
              verticalPadding: Style.space(5)
              bordered: true
              onClicked: root.openBtop()
            }
          }
        }

        // ==================== TAB SWITCHER ====================
        Row {
          id: tabRow
          width: parent.width
          spacing: Style.space(4)
          readonly property real iconTabWidth: Style.space(38)
          readonly property real remWidth: width - iconTabWidth - spacing * 4

          TabButton {
            width: tabRow.iconTabWidth
            iconText: "󰕮"
            iconSize: Style.font.body
            tooltipText: tr.t("tab.overview")
            active: root.activeTab === 0
            onClicked: root.activeTab = 0
          }
          TabButton {
            width: Math.round(tabRow.remWidth * 0.28)
            iconText: "󰒋"
            text: tr.t("tab.processes")
            active: root.activeTab === 1
            onClicked: root.activeTab = 1
          }
          TabButton {
            width: Math.round(tabRow.remWidth * 0.20)
            iconText: "󰋊"
            text: tr.t("tab.disks")
            active: root.activeTab === 2
            onClicked: root.activeTab = 2
          }
          TabButton {
            width: Math.round(tabRow.remWidth * 0.24)
            iconText: "󰔏"
            text: tr.t("tab.sensors")
            active: root.activeTab === 3
            onClicked: root.activeTab = 3
          }
          TabButton {
            width: tabRow.remWidth - Math.round(tabRow.remWidth * 0.28) - Math.round(tabRow.remWidth * 0.20) - Math.round(tabRow.remWidth * 0.24)
            iconText: "\uf021"
            text: root.hasActiveTransfers ? (tr.t("tab.transfers") + " (" + root.transferCount + ")") : tr.t("tab.transfers")
            active: root.activeTab === 4
            onClicked: root.activeTab = 4
          }
        }

        PanelSeparator { foreground: root.fgColor }
      }

      ScrollView {
        id: tabScrollView
        anchors.top: headerColumn.bottom
        anchors.topMargin: Style.space(12)
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: tabContentColumn.implicitHeight > height ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff
        Binding {
          target: tabScrollView.contentItem
          property: "interactive"
          value: tabContentColumn.implicitHeight > tabScrollView.height
        }

        Column {
          id: tabContentColumn
          width: tabScrollView.availableWidth - (tabContentColumn.implicitHeight > tabScrollView.height ? Style.space(4) : 0)
          spacing: Style.space(12)

          // ==================== TAB 0: OVERVIEW ====================
          Column {
            visible: root.activeTab === 0
            width: parent.width
            spacing: Style.space(12)

          // ---- CPU Card ----
          SurfaceCard {
            Column {
              width: parent.width
              spacing: Style.space(8)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Column {
                  width: parent.width - cpuBigPct.implicitWidth - Style.space(8)
                  spacing: Style.space(2)

                  PlainText {
                    text: "󰻠  " + tr.t("cpu.title")
                    color: Qt.darker(root.fgColor, 1.35)
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    width: parent.width
                  }

                  PlainText {
                    text: root.details.cpuModel
                    font.pixelSize: Style.font.bodySmall
                    width: parent.width
                  }
                }

                PlainText {
                  id: cpuBigPct
                  text: root.fmtNum(Math.round(root.cpuPct)) + "%"
                  color: root.cpuPct >= 85 ? root.urgentColor : root.fgColor
                  font.pixelSize: Style.font.heading
                  font.bold: true
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              SparkGraph {
                width: parent.width
                height: Style.space(44)
                values: root.cpuHistory
                maxValue: 100
                revision: root.historyRevision
                lineColor: root.cpuPct >= 85 ? root.urgentColor : root.accentColor
              }

              // Per-core bars (grid of 2 columns)
              Grid {
                visible: root.cpuCores.length > 0
                width: parent.width
                columns: 2
                columnSpacing: Style.space(10)
                rowSpacing: Style.space(6)

                Repeater {
                  model: root.cpuCores
                  Item {
                    required property var modelData
                    required property int index
                    width: (parent.width - Style.space(10)) / 2
                    implicitHeight: coreCol.implicitHeight

                    Column {
                      id: coreCol
                      width: parent.width
                      spacing: Style.space(3)

                      Row {
                        width: parent.width
                        PlainText {
                          text: tr.t("cpu.core").arg(root.fmtNum(modelData.id))
                            + (root.details.coreFreqs[modelData.id]
                              ? " · " + root.fmtNum(root.details.coreFreqs[modelData.id]) + " MHz"
                              : "")
                          color: Qt.darker(root.fgColor, 1.3)
                          font.pixelSize: Style.font.caption
                          width: parent.width - coreVal.implicitWidth
                        }
                        PlainText {
                          id: coreVal
                          text: root.fmtNum(Math.round(modelData.pct)) + "%"
                          font.pixelSize: Style.font.caption
                          font.bold: true
                        }
                      }

                      ProgressBarTrack {
                        width: parent.width
                        height: Style.space(5)
                        fraction: modelData.pct / 100.0
                        fillColor: modelData.pct >= 85 ? root.urgentColor : root.fgColor
                      }
                    }
                  }
                }
              }

              // CPU breakdown footer (User / Sys / IOwait / Freq)
              Row {
                width: parent.width
                spacing: Style.space(10)

                MiniStatPill {
                  width: (parent.width - Style.space(20)) / 3
                  label: "User / Sys"
                  value: root.fmtNum(Math.round(root.cpuUserPct)) + "% / " + root.fmtNum(Math.round(root.cpuSysPct)) + "%"
                }
                MiniStatPill {
                  width: (parent.width - Style.space(20)) / 3
                  label: tr.t("cpu.freq")
                  value: root.details.avgFreqMhz > 0
                    ? root.fmtNum(root.details.avgFreqMhz / 1000.0, 2) + " GHz"
                    : "—"
                }
                MiniStatPill {
                  width: (parent.width - Style.space(20)) / 3
                  label: tr.t("cpu.threads")
                  value: root.fmtNum(root.runningThreads) + " / " + root.fmtNum(root.totalThreads)
                }
              }
            }
          }

          // ---- Memory & Swap Card ----
          SurfaceCard {
            Column {
              width: parent.width
              spacing: Style.space(8)

              Row {
                width: parent.width
                spacing: Style.space(8)

                Column {
                  width: parent.width - memBigPct.implicitWidth - Style.space(8)
                  spacing: Style.space(2)

                  PlainText {
                    text: "󰘚  " + tr.t("mem.title")
                    color: Qt.darker(root.fgColor, 1.35)
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    width: parent.width
                  }

                  PlainText {
                    text: Model.formatBytes(root.memUsedBytes, 1, root.fmtNum)
                      + " / " + Model.formatBytes(root.memTotalBytes, 1, root.fmtNum)
                    font.pixelSize: Style.font.bodySmall
                    width: parent.width
                  }
                }

                PlainText {
                  id: memBigPct
                  text: root.fmtNum(Math.round(root.memPct)) + "%"
                  color: root.memPct >= 88 ? root.urgentColor : root.fgColor
                  font.pixelSize: Style.font.heading
                  font.bold: true
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              SparkGraph {
                width: parent.width
                height: Style.space(38)
                values: root.memHistory
                maxValue: 100
                revision: root.historyRevision
                lineColor: root.memPct >= 88 ? root.urgentColor : root.fgColor
              }

              Row {
                width: parent.width
                spacing: Style.space(8)

                MiniStatPill {
                  width: (parent.width - Style.space(16)) / 3
                  label: tr.t("mem.available")
                  value: Model.formatBytes(root.memAvailBytes, 1, root.fmtNum)
                }
                MiniStatPill {
                  width: (parent.width - Style.space(16)) / 3
                  label: tr.t("mem.cached")
                  value: Model.formatBytes(root.memCachedBytes + root.memBuffersBytes, 1, root.fmtNum)
                }
                MiniStatPill {
                  width: (parent.width - Style.space(16)) / 3
                  label: tr.t("mem.swap")
                  value: root.swapTotalBytes > 0
                    ? Model.formatBytes(root.swapUsedBytes, 1, root.fmtNum) + " (" + root.fmtNum(Math.round(root.swapPct)) + "%)"
                    : "0 B"
                }
              }
            }
          }

          // ---- Network & Disk I/O Dual Row ----
          Row {
            width: parent.width
            spacing: Style.space(10)

            // Network Live Card
            SurfaceCard {
              width: (parent.width - Style.space(10)) / 2

              Column {
                width: parent.width
                spacing: Style.space(6)

                Row {
                  width: parent.width
                  PlainText {
                    text: "󰖩  " + tr.t("net.title")
                    color: Qt.darker(root.fgColor, 1.35)
                    font.pixelSize: Style.font.caption
                    font.bold: true
                    width: parent.width - netIfaceBadge.implicitWidth
                  }
                  PlainText {
                    id: netIfaceBadge
                    text: root.netIface
                    color: Qt.darker(root.fgColor, 1.4)
                    font.pixelSize: Style.font.caption
                  }
                }

                Row {
                  width: parent.width
                  PlainText {
                    text: "↓ " + Model.formatRate(root.netRxRateBps, root.fmtNum)
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    width: parent.width / 2
                  }
                  PlainText {
                    text: "↑ " + Model.formatRate(root.netTxRateBps, root.fmtNum)
                    color: Qt.darker(root.fgColor, 1.25)
                    font.pixelSize: Style.font.bodySmall
                    horizontalAlignment: Text.AlignRight
                    width: parent.width / 2
                  }
                }

                SparkGraph {
                  width: parent.width
                  height: Style.space(32)
                  values: root.netRxHistory
                  secondaryValues: root.netTxHistory
                  maxValue: 0
                  revision: root.historyRevision
                  lineColor: root.accentColor
                }

                PlainText {
                  text: tr.t("net.total")
                    .arg(Model.formatShortBytes(root.netTotalRxBytes, root.fmtNum))
                    .arg(Model.formatShortBytes(root.netTotalTxBytes, root.fmtNum))
                  color: Qt.darker(root.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                  width: parent.width
                }
              }
            }

            // Disk I/O Live Card
            SurfaceCard {
              width: (parent.width - Style.space(10)) / 2

              Column {
                width: parent.width
                spacing: Style.space(6)

                PlainText {
                  text: "󰋊  " + tr.t("io.title")
                  color: Qt.darker(root.fgColor, 1.35)
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  width: parent.width
                }

                Row {
                  width: parent.width
                  PlainText {
                    text: "R " + Model.formatRate(root.diskReadRateBps, root.fmtNum)
                    font.pixelSize: Style.font.bodySmall
                    font.bold: true
                    width: parent.width / 2
                  }
                  PlainText {
                    text: "W " + Model.formatRate(root.diskWriteRateBps, root.fmtNum)
                    color: Qt.darker(root.fgColor, 1.25)
                    font.pixelSize: Style.font.bodySmall
                    horizontalAlignment: Text.AlignRight
                    width: parent.width / 2
                  }
                }

                SparkGraph {
                  width: parent.width
                  height: Style.space(32)
                  values: root.diskReadHistory
                  secondaryValues: root.diskWriteHistory
                  maxValue: 0
                  revision: root.historyRevision
                  lineColor: root.fgColor
                }

                PlainText {
                  text: tr.t("io.total")
                    .arg(Model.formatShortBytes(root.diskTotalReadBytes, root.fmtNum))
                    .arg(Model.formatShortBytes(root.diskTotalWriteBytes, root.fmtNum))
                  color: Qt.darker(root.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                  width: parent.width
                }
              }
            }
          }

          // ---- Transfers Overview Card ----
          SurfaceCard {
            width: parent.width
            clickable: true
            onClicked: root.activeTab = 4

            Column {
              width: parent.width
              spacing: Style.space(8)

              Row {
                width: parent.width
                spacing: Style.space(8)

                PlainText {
                  text: "\uf021  " + tr.t("tab.transfers").toUpperCase()
                  color: Qt.darker(root.fgColor, 1.35)
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  width: parent.width - (root.hasActiveTransfers ? speedBadge.implicitWidth : idleBadge.implicitWidth) - Style.space(8)
                  anchors.verticalCenter: parent.verticalCenter
                }

                // Speed badge when active
                Rectangle {
                  id: speedBadge
                  visible: root.hasActiveTransfers
                  radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
                  color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.16)
                  border.width: 1
                  border.color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.3)
                  implicitWidth: speedText.implicitWidth + Style.space(12)
                  implicitHeight: Style.space(20)
                  anchors.verticalCenter: parent.verticalCenter

                  PlainText {
                    id: speedText
                    anchors.centerIn: parent
                    text: Model.formatSpeed(root.aggTransferSpeed)
                    color: root.accentColor
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }

                // Status when idle
                PlainText {
                  id: idleBadge
                  visible: !root.hasActiveTransfers
                  text: tr.t("transfers.idle")
                  color: Qt.darker(root.fgColor, 1.45)
                  font.pixelSize: Style.font.caption
                  anchors.verticalCenter: parent.verticalCenter
                }
              }

              // Active transfers list in Overview
              Repeater {
                model: root.transferList

                Column {
                  required property var modelData
                  width: parent.width
                  spacing: Style.space(4)

                  Row {
                    width: parent.width
                    spacing: Style.space(6)

                    PlainText {
                      text: Model.toolIcon(modelData.tool)
                      color: root.accentColor
                      font.pixelSize: Style.font.bodySmall
                      anchors.verticalCenter: parent.verticalCenter
                    }

                    PlainText {
                      text: Model.toolDisplayTitle(modelData.tool)
                      font.bold: true
                      font.pixelSize: Style.font.caption
                      anchors.verticalCenter: parent.verticalCenter
                    }

                    PlainText {
                      text: "PID " + modelData.pid
                      color: Qt.darker(root.fgColor, 1.45)
                      font.pixelSize: Style.font.caption
                      anchors.verticalCenter: parent.verticalCenter
                    }

                    Item { Layout.fillWidth: true; width: 1 }

                    PlainText {
                      text: Model.formatSpeed(modelData.speed)
                      color: root.accentColor
                      font.bold: true
                      font.pixelSize: Style.font.caption
                      anchors.verticalCenter: parent.verticalCenter
                    }
                  }

                  PlainText {
                    visible: Boolean(modelData.destination || modelData.source)
                    text: modelData.destination
                      ? (Model.shortenPath(modelData.source) + " → " + Model.shortenPath(modelData.destination))
                      : Model.shortenPath(modelData.source)
                    color: Qt.darker(root.fgColor, 1.35)
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideMiddle
                    width: parent.width
                  }

                  ProgressBarTrack {
                    visible: modelData.progress_pct !== null && modelData.progress_pct !== undefined
                    width: parent.width
                    height: Style.space(5)
                    fraction: Math.max(0, Math.min(1, (modelData.progress_pct || 0) / 100.0))
                    fillColor: root.accentColor
                  }
                }
              }

              // If idle but has recent completed transfers: show latest completed
              Row {
                visible: !root.hasActiveTransfers && root.recentTransferList.length > 0
                width: parent.width
                spacing: Style.space(6)

                PlainText {
                  text: "\uf00c"
                  color: root.accentColor
                  font.pixelSize: Style.font.caption
                }

                PlainText {
                  text: root.recentTransferList[0]
                    ? (Model.toolDisplayTitle(root.recentTransferList[0].tool) + " (" + (root.recentTransferList[0].total_bytes ? Model.formatBytes(root.recentTransferList[0].total_bytes) : "") + " em " + (root.recentTransferList[0].elapsed_str || "") + ")")
                    : ""
                  color: Qt.darker(root.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideRight
                  width: parent.width - Style.space(24)
                }
              }

              // Link to Transfers tab
              Row {
                anchors.horizontalCenter: parent.horizontalCenter
                spacing: Style.space(4)

                PlainText {
                  text: tr.t("tab.transfers") + " →"
                  color: Qt.darker(root.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }

        // ==================== TAB 1: PROCESSES ====================
        Column {
          visible: root.activeTab === 1
          width: parent.width
          spacing: Style.space(10)

          Row {
            width: parent.width
            spacing: Style.space(6)

            Button {
              width: (parent.width - Style.space(6)) / 2
              iconText: "󰻠"
              text: tr.t("proc.sortByCpu")
              fontSize: Style.font.bodySmall
              iconSize: Style.font.body
              foreground: root.fgColor
              fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
              bordered: true
              active: root.procSort === "cpu"
              onClicked: {
                root.procSort = "cpu"
                root.selectedProcIndex = 0
              }
            }

            Button {
              width: (parent.width - Style.space(6)) / 2
              iconText: "󰘚"
              text: tr.t("proc.sortByMem")
              fontSize: Style.font.bodySmall
              iconSize: Style.font.body
              foreground: root.fgColor
              fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
              bordered: true
              active: root.procSort === "mem"
              onClicked: {
                root.procSort = "mem"
                root.selectedProcIndex = 0
              }
            }
          }

          // Feedback toast when SIGTERM is sent
          Rectangle {
            visible: root.killFeedbackText !== ""
            width: parent.width
            implicitHeight: Style.space(28)
            radius: Style.cornerRadius
            color: Qt.rgba(root.urgentColor.r, root.urgentColor.g, root.urgentColor.b, 0.16)
            border.width: 1
            border.color: root.urgentColor

            PlainText {
              anchors.centerIn: parent
              text: "󰄬 " + root.killFeedbackText
              color: root.fgColor
              font.pixelSize: Style.font.caption
              font.bold: true
            }
          }

          // Process rows
          Column {
            width: parent.width
            spacing: Style.space(4)

            Repeater {
              model: root.currentProcList()

              Rectangle {
                id: procRow
                required property var modelData
                required property int index

                readonly property bool isSelected: root.selectedProcIndex === index || rowMouse.containsMouse
                readonly property real barFraction: root.procSort === "mem"
                  ? Math.min(1, modelData.mem / 25.0)
                  : Math.min(1, modelData.cpu / 100.0)

                width: parent.width
                implicitHeight: Style.space(32)
                radius: Style.cornerRadius
                color: isSelected
                  ? Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.1)
                  : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.04)
                border.width: isSelected ? 1 : 0
                border.color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.25)

                // Subtle proportional fill bar behind the process row
                Rectangle {
                  anchors.left: parent.left
                  anchors.top: parent.top
                  anchors.bottom: parent.bottom
                  width: Math.max(0, parent.width * procRow.barFraction)
                  radius: parent.radius
                  color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.1)
                }

                MouseArea {
                  id: rowMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  onEntered: root.selectedProcIndex = procRow.index
                  onClicked: root.selectedProcIndex = procRow.index
                }

                Row {
                  anchors.fill: parent
                  anchors.leftMargin: Style.space(10)
                  anchors.rightMargin: Style.space(8)
                  spacing: Style.space(8)

                  PlainText {
                    text: String(modelData.pid)
                    color: Qt.darker(root.fgColor, 1.45)
                    font.pixelSize: Style.font.caption
                    width: Style.space(46)
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  PlainText {
                    text: modelData.name
                    font.pixelSize: Style.font.bodySmall
                    font.bold: procRow.isSelected
                    width: parent.width - Style.space(185)
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  PlainText {
                    text: root.fmtNum(modelData.cpu, 1) + "%"
                    color: modelData.cpu >= 50 ? root.urgentColor : root.fgColor
                    font.pixelSize: Style.font.caption
                    font.bold: root.procSort === "cpu"
                    horizontalAlignment: Text.AlignRight
                    width: Style.space(44)
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  PlainText {
                    text: Model.formatShortBytes(modelData.rssBytes, root.fmtNum)
                    color: Qt.darker(root.fgColor, root.procSort === "mem" ? 1.0 : 1.35)
                    font.pixelSize: Style.font.caption
                    font.bold: root.procSort === "mem"
                    horizontalAlignment: Text.AlignRight
                    width: Style.space(48)
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  // Kill (SIGTERM) button
                  Rectangle {
                    width: Style.space(22)
                    height: Style.space(22)
                    anchors.verticalCenter: parent.verticalCenter
                    radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
                    color: killMouse.containsMouse
                      ? Qt.rgba(root.urgentColor.r, root.urgentColor.g, root.urgentColor.b, 0.28)
                      : "transparent"

                    PlainText {
                      anchors.centerIn: parent
                      text: "󰅖"
                      color: killMouse.containsMouse ? root.urgentColor : Qt.darker(root.fgColor, 1.4)
                      font.pixelSize: Style.font.caption
                    }

                    MouseArea {
                      id: killMouse
                      anchors.fill: parent
                      hoverEnabled: true
                      cursorShape: Qt.PointingHandCursor
                      onClicked: root.killProcess(modelData.pid, modelData.name)
                    }
                  }
                }
              }
            }
          }

          PlainText {
            text: tr.t("proc.hint")
            color: Qt.darker(root.fgColor, 1.5)
            font.pixelSize: Style.font.caption
            horizontalAlignment: Text.AlignHCenter
            width: parent.width
          }

          Button {
            width: parent.width
            iconText: "󰆍"
            text: tr.t("btop.openFull")
            fontSize: Style.font.bodySmall
            iconSize: Style.font.body
            foreground: root.fgColor
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
            bordered: true
            onClicked: root.openBtop()
          }
        }

        // ==================== TAB 2: DISKS & STORAGE ====================
        Column {
          visible: root.activeTab === 2
          width: parent.width
          spacing: Style.space(12)

          // Live Disk Throughput Card
          SurfaceCard {
            Column {
              width: parent.width
              spacing: Style.space(8)

              PlainText {
                text: "󰓅  " + tr.t("disks.ioHeader")
                color: Qt.darker(root.fgColor, 1.35)
                font.pixelSize: Style.font.caption
                font.bold: true
                width: parent.width
              }

              Row {
                width: parent.width
                PlainText {
                  text: "󰁅 " + tr.t("io.read") + ": " + Model.formatRate(root.diskReadRateBps, root.fmtNum)
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                  width: parent.width / 2
                }
                PlainText {
                  text: "󰁝 " + tr.t("io.write") + ": " + Model.formatRate(root.diskWriteRateBps, root.fmtNum)
                  font.pixelSize: Style.font.bodySmall
                  horizontalAlignment: Text.AlignRight
                  width: parent.width / 2
                }
              }

              SparkGraph {
                width: parent.width
                height: Style.space(42)
                values: root.diskReadHistory
                secondaryValues: root.diskWriteHistory
                maxValue: 0
                revision: root.historyRevision
                lineColor: root.accentColor
              }

              PlainText {
                text: tr.t("io.total")
                  .arg(Model.formatBytes(root.diskTotalReadBytes, 1, root.fmtNum))
                  .arg(Model.formatBytes(root.diskTotalWriteBytes, 1, root.fmtNum))
                color: Qt.darker(root.fgColor, 1.4)
                font.pixelSize: Style.font.caption
                width: parent.width
              }
            }
          }

          PanelSectionHeader {
            text: tr.t("disks.mounts")
            foreground: root.fgColor
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          Repeater {
            model: root.details.disks

            SurfaceCard {
              required property var modelData
              clickable: true
              onClicked: root.openDiskFolder(modelData.mount)

              Column {
                width: parent.width
                spacing: Style.space(6)

                Row {
                  width: parent.width
                  spacing: Style.space(8)

                  PlainText {
                    text: "󰋊  " + modelData.mount
                    font.pixelSize: Style.font.body
                    font.bold: true
                    width: parent.width - fsBadge.implicitWidth - pctLabel.implicitWidth - openIcon.implicitWidth - Style.space(24)
                  }

                  Rectangle {
                    id: fsBadge
                    radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
                    color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.1)
                    implicitWidth: fsText.implicitWidth + Style.space(10)
                    implicitHeight: Style.space(18)
                    anchors.verticalCenter: parent.verticalCenter

                    PlainText {
                      id: fsText
                      anchors.centerIn: parent
                      text: modelData.fstype
                      color: Qt.darker(root.fgColor, 1.2)
                      font.pixelSize: Style.font.caption
                    }
                  }

                  PlainText {
                    id: pctLabel
                    text: root.fmtNum(Math.round(modelData.pct)) + "%"
                    color: modelData.pct >= 90 ? root.urgentColor : root.fgColor
                    font.pixelSize: Style.font.body
                    font.bold: true
                    anchors.verticalCenter: parent.verticalCenter
                  }

                  PlainText {
                    id: openIcon
                    text: "\uf07b"
                    color: Qt.darker(root.fgColor, 1.45)
                    font.pixelSize: Style.font.bodySmall
                    anchors.verticalCenter: parent.verticalCenter
                  }
                }

                ProgressBarTrack {
                  width: parent.width
                  height: Style.space(7)
                  fraction: modelData.pct / 100.0
                  fillColor: modelData.pct >= 90 ? root.urgentColor : root.fgColor
                }

                Row {
                  width: parent.width
                  PlainText {
                    text: modelData.device
                    color: Qt.darker(root.fgColor, 1.45)
                    font.pixelSize: Style.font.caption
                    width: parent.width * 0.45
                  }
                  PlainText {
                    text: tr.t("disks.free")
                      .arg(Model.formatBytes(modelData.availBytes, 1, root.fmtNum))
                      .arg(Model.formatBytes(modelData.totalBytes, 1, root.fmtNum))
                    color: Qt.darker(root.fgColor, 1.25)
                    font.pixelSize: Style.font.caption
                    horizontalAlignment: Text.AlignRight
                    width: parent.width * 0.55
                  }
                }
              }
            }
          }
        }

        // ==================== TAB 3: SENSORS & HARDWARE ====================
        Column {
          visible: root.activeTab === 3
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: tr.t("sensors.thermal")
            foreground: root.fgColor
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          SurfaceCard {
            Column {
              width: parent.width
              spacing: Style.space(8)

              Repeater {
                model: root.details.sensors

                Column {
                  required property var modelData
                  width: parent.width
                  spacing: Style.space(3)

                  Row {
                    width: parent.width
                    PlainText {
                      text: modelData.chip + " · " + modelData.label
                      color: Qt.darker(root.fgColor, 1.2)
                      font.pixelSize: Style.font.bodySmall
                      width: parent.width - sensorVal.implicitWidth
                    }
                    PlainText {
                      id: sensorVal
                      text: modelData.value + (modelData.unit ? " " + modelData.unit : "")
                      color: (modelData.unit === "°C" && modelData.numValue >= 80) ? root.urgentColor : root.fgColor
                      font.pixelSize: Style.font.bodySmall
                      font.bold: true
                    }
                  }

                  ProgressBarTrack {
                    visible: modelData.unit === "°C"
                    width: parent.width
                    height: Style.space(4)
                    fraction: Math.min(1.0, Math.max(0.05, modelData.numValue / 100.0))
                    fillColor: modelData.numValue >= 80 ? root.urgentColor : root.fgColor
                  }
                }
              }
            }
          }

          PanelSectionHeader {
            text: tr.t("sensors.hardware")
            foreground: root.fgColor
            fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
          }

          SurfaceCard {
            Column {
              width: parent.width
              spacing: Style.space(6)

              HardwareRow {
                label: tr.t("sensors.cpuModel")
                value: root.details.cpuModel
              }
              HardwareRow {
                label: tr.t("sensors.kernel")
                value: root.details.kernel
              }
              HardwareRow {
                visible: root.details.gpuMaxMhz > 0
                label: tr.t("sensors.gpuClock")
                value: root.fmtNum(root.details.gpuCurMhz) + " / " + root.fmtNum(root.details.gpuMaxMhz) + " MHz"
              }
              HardwareRow {
                label: tr.t("cpu.load")
                value: root.fmtNum(root.load1, 2) + " · " + root.fmtNum(root.load5, 2) + " · " + root.fmtNum(root.load15, 2)
              }
              HardwareRow {
                label: tr.t("sensors.ctxSwitches")
                value: root.fmtNum(root.ctxtRate) + " /s"
              }
              HardwareRow {
                label: tr.t("sensors.uptime")
                value: root.uptimeFormatted
              }
            }
          }
        }

        // ==================== TAB 4: TRANSFERS ====================
        Column {
          visible: root.activeTab === 4
          width: parent.width
          spacing: Style.space(10)

          // Status message pill
          Rectangle {
            visible: root.transferStatusMessage !== ""
            anchors.horizontalCenter: parent.horizontalCenter
            radius: Style.cornerRadius
            color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.2)
            border.width: 1
            border.color: root.accentColor
            implicitWidth: transferStatusLabel.implicitWidth + Style.space(16)
            implicitHeight: Style.space(24)

            PlainText {
              id: transferStatusLabel
              anchors.centerIn: parent
              text: root.transferStatusMessage
              color: root.accentColor
              font.bold: true
              font.pixelSize: Style.font.caption
            }
          }

          // Empty state
          SurfaceCard {
            visible: root.transferList.length === 0
            Column {
              width: parent.width
              spacing: Style.space(4)
              PlainText {
                text: "\uf00c  " + tr.t("transfers.empty")
                font.bold: true
                color: Qt.darker(root.fgColor, 1.3)
                font.pixelSize: Style.font.body
              }
              PlainText {
                text: "Monitora automaticamente rsync, rclone, cp, dd, downloads e backups locais."
                color: Qt.darker(root.fgColor, 1.6)
                font.pixelSize: Style.font.caption
                width: parent.width
                wrapMode: Text.WordWrap
              }
            }
          }

          // Active transfers cards
          Repeater {
            model: root.transferList

            SurfaceCard {
              required property var modelData
              required property int index

              border.color: index === root.selectedTransferIndex
                ? root.accentColor
                : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)
              border.width: index === root.selectedTransferIndex ? 2 : 1

              Column {
                width: parent.width
                spacing: Style.space(8)

                // Header: Tool + PID + State + Speed
                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  // Tool pill
                  Rectangle {
                    radius: Style.cornerRadius
                    color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.16)
                    border.width: 1
                    border.color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.3)
                    implicitWidth: toolRow.implicitWidth + Style.space(10)
                    implicitHeight: Style.space(22)

                    Row {
                      id: toolRow
                      anchors.centerIn: parent
                      spacing: Style.space(4)
                      PlainText {
                        text: Model.toolIcon(modelData.tool)
                        color: root.accentColor
                        font.pixelSize: Style.font.caption
                      }
                      PlainText {
                        text: Model.toolDisplayTitle(modelData.tool)
                        color: root.accentColor
                        font.pixelSize: Style.font.caption
                        font.bold: true
                      }
                    }
                  }

                  // PID pill
                  Rectangle {
                    radius: Style.cornerRadius
                    color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
                    implicitWidth: pidLabel.implicitWidth + Style.space(8)
                    implicitHeight: Style.space(22)

                    PlainText {
                      id: pidLabel
                      anchors.centerIn: parent
                      text: "PID " + modelData.pid + (modelData.tree_pids && modelData.tree_pids.length > 1 ? " (" + modelData.tree_pids.length + ")" : "")
                      color: Qt.darker(root.fgColor, 1.4)
                      font.pixelSize: Style.font.caption
                    }
                  }

                  // State pill
                  Rectangle {
                    radius: Style.cornerRadius
                    color: modelData.state === "paused"
                      ? Qt.rgba(root.urgentColor.r, root.urgentColor.g, root.urgentColor.b, 0.18)
                      : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
                    border.width: modelData.state === "paused" ? 1 : 0
                    border.color: modelData.state === "paused" ? root.urgentColor : "transparent"
                    implicitWidth: stateLabel.implicitWidth + Style.space(8)
                    implicitHeight: Style.space(22)

                    PlainText {
                      id: stateLabel
                      anchors.centerIn: parent
                      text: modelData.state === "disk_wait" ? tr.t("transfers.state.diskWait") :
                            modelData.state === "paused" ? tr.t("transfers.state.paused") :
                            modelData.state === "sleeping" ? tr.t("transfers.state.sleeping") : tr.t("transfers.state.running")
                      color: modelData.state === "paused" ? root.urgentColor : Qt.darker(root.fgColor, 1.2)
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  Item { Layout.fillWidth: true; width: Style.space(10) }

                  // Live speed badge
                  PlainText {
                    anchors.verticalCenter: parent.verticalCenter
                    text: Model.formatSpeed(modelData.active_speed)
                    color: root.accentColor
                    font.pixelSize: Style.font.subtitle
                    font.bold: true
                  }
                }

                // Path Flow: Source -> Destination
                Column {
                  width: parent.width
                  spacing: Style.space(2)

                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    Text {
                      textFormat: Text.PlainText
                      text: tr.t("transfers.source") + ":"
                      color: Qt.darker(root.fgColor, 1.5)
                      font.family: root.bar ? root.bar.fontFamily : Style.font.family
                      font.pixelSize: Style.font.caption
                      Layout.preferredWidth: implicitWidth
                    }
                    PlainText {
                      text: Model.shortenPath(modelData.source, 46)
                      color: root.fgColor
                      font.pixelSize: Style.font.caption
                      Layout.fillWidth: true
                    }
                  }

                  RowLayout {
                    width: parent.width
                    spacing: Style.space(8)
                    Text {
                      textFormat: Text.PlainText
                      text: tr.t("transfers.destination") + ":"
                      color: Qt.darker(root.fgColor, 1.5)
                      font.family: root.bar ? root.bar.fontFamily : Style.font.family
                      font.pixelSize: Style.font.caption
                      Layout.preferredWidth: implicitWidth
                    }
                    PlainText {
                      text: Model.shortenPath(modelData.destination, 46)
                      color: root.fgColor
                      font.pixelSize: Style.font.caption
                      font.bold: true
                      Layout.fillWidth: true
                    }
                  }
                }

                // Overall Sync Progress
                Rectangle {
                  visible: Boolean(modelData.overall_total_bytes > 0)
                  width: parent.width
                  implicitHeight: overallCol.implicitHeight + Style.space(12)
                  radius: Style.cornerRadius
                  color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)
                  border.width: 1
                  border.color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)

                  Column {
                    id: overallCol
                    anchors.fill: parent
                    anchors.margins: Style.space(6)
                    spacing: Style.space(4)

                    Row {
                      width: parent.width
                      spacing: Style.space(6)
                      PlainText {
                        text: "\uf021"
                        color: root.accentColor
                        font.pixelSize: Style.font.bodySmall
                      }
                      PlainText {
                        text: "Progresso geral"
                        font.bold: true
                        font.pixelSize: Style.font.bodySmall
                      }
                    }

                    ProgressBarTrack {
                      width: parent.width
                      height: Style.space(6)
                      fraction: Math.min(1.0, Math.max(0.0, modelData.overall_pct / 100.0))
                      fillColor: root.accentColor
                    }

                    Row {
                      width: parent.width
                      Item { Layout.fillWidth: true; width: 1 }
                      PlainText {
                        text: Model.formatBytes(modelData.total_bytes) + " / " + Model.formatBytes(modelData.overall_total_bytes) + " (" + modelData.overall_pct + "%)" + (modelData.overall_eta ? " · ETA: " + modelData.overall_eta : "")
                        color: Qt.darker(root.fgColor, 1.4)
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }

                // Active file progress
                Rectangle {
                  visible: modelData.active_file !== null
                  width: parent.width
                  implicitHeight: fileCol.implicitHeight + Style.space(12)
                  radius: Style.cornerRadius
                  color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)
                  border.width: 1
                  border.color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)

                  Column {
                    id: fileCol
                    anchors.fill: parent
                    anchors.margins: Style.space(6)
                    spacing: Style.space(4)

                    Row {
                      width: parent.width
                      spacing: Style.space(6)
                      PlainText {
                        text: "\uf15b"
                        color: root.accentColor
                        font.pixelSize: Style.font.bodySmall
                      }
                      PlainText {
                        text: modelData.active_file ? modelData.active_file.name : ""
                        font.bold: true
                        font.pixelSize: Style.font.bodySmall
                        width: parent.width - Style.space(24)
                      }
                    }

                    ProgressBarTrack {
                      width: parent.width
                      height: Style.space(6)
                      fraction: modelData.active_file ? Math.min(1.0, Math.max(0.0, modelData.active_file.pct / 100.0)) : 0
                      fillColor: root.accentColor
                    }

                    Row {
                      width: parent.width
                      Item { Layout.fillWidth: true; width: 1 }
                      PlainText {
                        text: modelData.active_file
                          ? Model.formatBytes(modelData.active_file.pos) + " / " + Model.formatBytes(modelData.active_file.size) + " (" + modelData.active_file.pct + "%)" + (modelData.file_eta ? " · ETA: " + modelData.file_eta : "")
                          : ""
                        color: Qt.darker(root.fgColor, 1.4)
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }

                // Stats row
                Row {
                  width: parent.width
                  spacing: Style.space(12)

                  PlainText {
                    text: "Transferido: " + Model.formatBytes(modelData.total_bytes)
                    color: Qt.darker(root.fgColor, 1.3)
                    font.pixelSize: Style.font.caption
                  }

                  PlainText {
                    text: "Decorrido: " + modelData.elapsed_str
                    color: Qt.darker(root.fgColor, 1.3)
                    font.pixelSize: Style.font.caption
                  }

                  PlainText {
                    visible: modelData.dest_fs !== null
                    text: modelData.dest_fs ? modelData.dest_fs.name + ": " + Model.formatBytes(modelData.dest_fs.free_bytes) + " livre" : ""
                    color: Qt.darker(root.fgColor, 1.3)
                    font.pixelSize: Style.font.caption
                  }
                }

                // Actions toolbar
                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  ActionBtn {
                    text: modelData.state === "paused" ? tr.t("transfers.action.resume") : tr.t("transfers.action.pause")
                    iconText: modelData.state === "paused" ? "\uf04b" : "\uf04c"
                    onClicked: {
                      if (modelData.state === "paused") root.resumeTransfer(modelData.pid)
                      else root.pauseTransfer(modelData.pid)
                    }
                  }

                  ActionBtn {
                    text: tr.t("transfers.action.openDest")
                    iconText: "\uf07b"
                    visible: Boolean(modelData.destination)
                    onClicked: root.openTransferPath(modelData.destination)
                  }

                  ActionBtn {
                    text: tr.t("transfers.action.copyCmd")
                    iconText: "\uf0c5"
                    onClicked: root.copyTransferText(modelData.command)
                  }

                  ActionBtn {
                    text: tr.t("transfers.action.cancel")
                    iconText: "\uf00d"
                    foreground: root.urgentColor
                    onClicked: root.cancelTransfer(modelData.pid)
                  }
                }
              }
            }
          }

          // Recently completed transfers
          Column {
            visible: root.recentTransferList.length > 0
            width: parent.width
            spacing: Style.space(6)

            PlainText {
              text: tr.t("transfers.recent")
              color: Qt.darker(root.fgColor, 1.4)
              font.bold: true
              font.pixelSize: Style.font.caption
            }

            Repeater {
              model: root.recentTransferList

              SurfaceCard {
                required property var modelData

                Row {
                  width: parent.width
                  spacing: Style.space(8)

                  PlainText {
                    text: "\uf00c"
                    color: root.accentColor
                    font.pixelSize: Style.font.bodySmall
                  }

                  PlainText {
                    text: Model.toolDisplayTitle(modelData.tool)
                    font.bold: true
                    font.pixelSize: Style.font.caption
                  }

                  PlainText {
                    text: modelData.total_bytes ? Model.formatBytes(modelData.total_bytes) : ""
                    color: Qt.darker(root.fgColor, 1.3)
                    font.pixelSize: Style.font.caption
                  }

                  Item { Layout.fillWidth: true; width: 1 }

                  PlainText {
                    text: modelData.elapsed_str || ""
                    color: Qt.darker(root.fgColor, 1.5)
                    font.pixelSize: Style.font.caption
                  }
                }
              }
            }
          }
        }
        }
      }
    }
  }

  // ==================== REUSABLE LOCAL COMPONENTS ====================

  component PlainText: Text {
    textFormat: Text.PlainText
    color: root.fgColor
    font.family: root.bar ? root.bar.fontFamily : Style.font.family
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
  }

  component TabButton: Button {
    fontSize: Style.font.caption
    iconSize: Style.font.bodySmall
    foreground: root.fgColor
    fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
    horizontalPadding: Style.space(4)
    verticalPadding: Style.space(6)
    bordered: true
  }

  component ActionBtn: Button {
    fontSize: Style.font.caption
    iconSize: Style.font.bodySmall
    foreground: root.fgColor
    fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
    horizontalPadding: Style.space(8)
    verticalPadding: Style.space(4)
    bordered: true
  }

  component SurfaceCard: Rectangle {
    id: cardRoot
    default property alias cardContent: innerItem.data
    property bool clickable: false
    signal clicked()

    width: parent ? parent.width : 0
    implicitHeight: innerItem.childrenRect.height + Style.space(20)
    radius: Style.cornerRadius
    color: clickable && cardMouse.containsMouse
      ? Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
      : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.04)
    border.width: 1
    border.color: clickable && cardMouse.containsMouse
      ? Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.5)
      : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)

    MouseArea {
      id: cardMouse
      anchors.fill: parent
      enabled: cardRoot.clickable
      hoverEnabled: cardRoot.clickable
      cursorShape: cardRoot.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
      onClicked: cardRoot.clicked()
      onWheel: function(wheel) { wheel.accepted = false }
    }

    Item {
      id: innerItem
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.margins: Style.space(10)
      height: childrenRect.height
    }
  }

  component MiniStatPill: Rectangle {
    property string label: ""
    property string value: ""
    implicitHeight: pillCol.implicitHeight + Style.space(12)
    radius: Style.cornerRadius > 0 ? Math.min(Style.space(6), Style.cornerRadius) : 0
    color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)

    Column {
      id: pillCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.margins: Style.space(6)
      spacing: Style.space(2)

      PlainText {
        text: label
        color: Qt.darker(root.fgColor, 1.4)
        font.pixelSize: Style.font.caption
        width: parent.width
      }
      PlainText {
        text: value
        font.pixelSize: Style.font.caption
        font.bold: true
        width: parent.width
      }
    }
  }

  component ProgressBarTrack: Item {
    property real fraction: 0
    property color fillColor: root.fgColor

    Rectangle {
      id: track
      anchors.fill: parent
      radius: height / 2
      color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)
    }

    Rectangle {
      anchors.left: track.left
      anchors.verticalCenter: track.verticalCenter
      height: track.height
      radius: track.radius
      color: parent.fillColor
      width: Math.max(track.height, track.width * Math.min(1, Math.max(0, parent.fraction)))

      Behavior on width {
        NumberAnimation { duration: Style.duration(220); easing.type: Easing.OutCubic }
      }
    }
  }

  component HardwareRow: Row {
    property string label: ""
    property string value: ""
    width: parent.width
    spacing: Style.space(8)

    PlainText {
      text: label
      color: Qt.darker(root.fgColor, 1.38)
      font.pixelSize: Style.font.bodySmall
      width: parent.width * 0.38
    }
    PlainText {
      text: value
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      horizontalAlignment: Text.AlignRight
      width: parent.width * 0.62 - Style.space(8)
    }
  }

  component SparkGraph: Item {
    id: graphRoot
    property var values: []
    property var secondaryValues: []
    property real maxValue: 100
    property int revision: 0
    property color lineColor: root.accentColor

    onRevisionChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
      color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)
    }

    Canvas {
      id: canvas
      anchors.fill: parent
      anchors.margins: 2

      onPaint: {
        var ctx = getContext("2d")
        var w = width
        var h = height
        ctx.clearRect(0, 0, w, h)
        if (w <= 2 || h <= 2) return

        var pts = Array.isArray(graphRoot.values) ? graphRoot.values : []
        var pts2 = Array.isArray(graphRoot.secondaryValues) ? graphRoot.secondaryValues : []
        if (pts.length === 0) return

        var ceil = graphRoot.maxValue
        if (ceil <= 0) {
          ceil = 1024
          for (var i = 0; i < pts.length; i++) {
            if (pts[i] > ceil) ceil = pts[i]
          }
          for (var j = 0; j < pts2.length; j++) {
            if (pts2[j] > ceil) ceil = pts2[j]
          }
          ceil *= 1.15
        }

        // Subtle horizontal mid-grid line
        ctx.strokeStyle = Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
        ctx.lineWidth = 1
        ctx.beginPath()
        ctx.moveTo(0, h / 2)
        ctx.lineTo(w, h / 2)
        ctx.stroke()

        function drawSeries(series, strokeCol, fillAlpha) {
          if (!series || series.length === 0) return
          var count = Math.max(2, Model.MAX_HISTORY)
          var step = w / (count - 1)
          var startIdx = count - series.length

          ctx.beginPath()
          for (var k = 0; k < series.length; k++) {
            var x = (startIdx + k) * step
            var norm = Math.min(1, Math.max(0, (series[k] || 0) / ceil))
            var y = h - norm * (h - 4) - 2
            if (k === 0) ctx.moveTo(x, y)
            else ctx.lineTo(x, y)
          }
          ctx.strokeStyle = strokeCol
          ctx.lineWidth = 1.6
          ctx.stroke()

          if (fillAlpha > 0) {
            var lastX = (startIdx + series.length - 1) * step
            var firstX = startIdx * step
            ctx.lineTo(lastX, h)
            ctx.lineTo(firstX, h)
            ctx.closePath()
            ctx.fillStyle = Qt.rgba(strokeCol.r, strokeCol.g, strokeCol.b, fillAlpha)
            ctx.fill()
          }
        }

        if (pts2.length > 0) {
          var secColor = Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.45)
          drawSeries(pts2, secColor, 0.08)
        }
        drawSeries(pts, graphRoot.lineColor, 0.22)
      }
    }
  }
}
