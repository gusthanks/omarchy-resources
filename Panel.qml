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
  moduleName: "gusthx.transfers"
  ipcTarget: "gusthx.transfers"
  manageIpc: false

  Commons.Translations {
    id: tr
    directory: Qt.resolvedUrl("i18n")
  }

  // ---- Settings persisted in shell.json ----
  readonly property bool showWhenIdle: Boolean(setting("showWhenIdle", false))

  // ---- Live Telemetry Data ----
  property var transferData: ({
    count: 0,
    agg_read_speed: 0,
    agg_write_speed: 0,
    agg_speed: 0,
    transfers: [],
    recent_completed: []
  })

  readonly property var transferList: transferData.transfers || []
  readonly property var recentList: transferData.recent_completed || []
  readonly property int transferCount: transferData.count || 0
  readonly property real aggSpeed: transferData.agg_speed || 0
  readonly property bool hasActiveTransfers: transferCount > 0

  // ---- Interaction State ----
  property int activeTab: 0 // 0=Active, 1=Recent, 2=Settings
  property int selectedIndex: 0
  property string statusMessage: ""

  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^(file|qs):\/\//, "").replace(/\/+$/, "")
  readonly property string collectorScript: pluginDir + "/collect-transfers.py"

  readonly property color fgColor: bar ? bar.foreground : Commons.Color.foreground
  readonly property color accentColor: Commons.Color.accent
  readonly property color urgentColor: bar ? bar.urgent : Commons.Color.urgent

  // ---- Bar Visibility & Sizing ----
  visible: hasActiveTransfers || showWhenIdle
  implicitWidth: visible ? button.implicitWidth : 0
  implicitHeight: visible ? button.implicitHeight : 0

  readonly property string barDisplayText: {
    if (!hasActiveTransfers) {
      return "\uf021"
    }
    var speedStr = Model.formatSpeed(aggSpeed)
    if (transferCount > 1) {
      return "\uf021 " + transferCount + " · " + speedStr
    }
    return "\uf021 " + speedStr
  }

  readonly property string barTooltip: {
    if (!hasActiveTransfers) {
      return tr.t("bar.tooltip.idle")
    }
    var summary = tr.t("bar.tooltip.active").arg(transferCount).arg(Model.formatSpeed(aggSpeed))
    if (transferList.length > 0) {
      var lines = [summary, ""]
      for (var i = 0; i < Math.min(3, transferList.length); i++) {
        var it = transferList[i]
        var line = "• " + it.tool + ": " + Model.formatSpeed(it.active_speed)
        if (it.active_file) {
          line += " (" + it.active_file.name + " " + it.active_file.pct + "%)"
        }
        lines.push(line)
      }
      return lines.join("\n")
    }
    return summary
  }

  function toggleShowWhenIdle() {
    var next = !showWhenIdle
    var entry = { id: root.moduleName }
    for (var k in root.settings) {
      if (k !== "id") entry[k] = root.settings[k]
    }
    entry["showWhenIdle"] = next
    root.settings = entry
    if (root.bar && root.bar.shell && typeof root.bar.shell.updateEntryInline === "function") {
      root.bar.shell.updateEntryInline(root.moduleName, entry)
    }
  }

  function pollTransfers() {
    if (!pollProc.running) {
      pollProc.running = true
    }
  }

  // Polling subprocess
  Process {
    id: pollProc
    command: ["python3", root.collectorScript]
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

  // Interactive control action subprocess
  Process {
    id: actionProc
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        root.pollTransfers()
      }
    }
  }

  function runAction(flag, arg) {
    actionProc.command = ["python3", root.collectorScript, flag, String(arg)]
    actionProc.running = true
  }

  function pauseTransfer(pid) {
    runAction("--pause", pid)
  }

  function resumeTransfer(pid) {
    runAction("--resume", pid)
  }

  function cancelTransfer(pid) {
    runAction("--cancel", pid)
  }

  function openPath(path) {
    if (path) runAction("--open", path)
  }

  function copyText(str) {
    if (str) {
      runAction("--copy", str)
      root.statusMessage = "Copiado!"
      statusClearTimer.restart()
    }
  }

  Timer {
    id: statusClearTimer
    interval: 2000
    onTriggered: root.statusMessage = ""
  }

  // Adaptive polling timer
  Timer {
    id: pollTimer
    interval: root.opened ? 1200 : (root.hasActiveTransfers ? 1800 : 3500)
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.pollTransfers()
  }

  onOpenedChanged: {
    if (opened) {
      pollTransfers()
    }
  }

  ShellIpc {
    target: "gusthx.transfers"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
    function refresh(): void { root.pollTransfers() }
  }

  // ---- Bar Button ----
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.barDisplayText
    active: root.hasActiveTransfers
    tooltipText: root.barTooltip
    onPressed: function(b) {
      if (b === Qt.RightButton) {
        root.toggleShowWhenIdle()
      } else if (b === Qt.MiddleButton) {
        root.pollTransfers()
      } else {
        root.toggle()
      }
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
    contentWidth: panel.fittedContentWidth(Style.space(480))
    contentHeight: panel.fittedContentHeight(panel.loadedContentHeight, Style.space(620))

    content: PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      implicitHeight: Math.min(mainColumn.implicitHeight, Style.space(620))

      onCloseRequested: root.close()

      // Standard Omarchy Bar tab navigation: Tab morphs to next bar widget
      onTabRequested: function(direction) {
        root.switchPanel(direction)
      }

      onMoveRequested: function(dx, dy) {
        if (dx !== 0) {
          root.activeTab = (root.activeTab + dx + 3) % 3
        } else if (dy !== 0 && root.activeTab === 0) {
          var count = root.transferList.length
          if (count > 0) {
            root.selectedIndex = Math.max(0, Math.min(count - 1, root.selectedIndex + dy))
          }
        }
      }

      onTextKey: function(t) {
        if (t === "1") root.activeTab = 0
        else if (t === "2") root.activeTab = 1
        else if (t === "3") root.activeTab = 2
        else if (t === "r" || t === "R") root.pollTransfers()
        else if (t === "p" || t === "P") {
          if (root.activeTab === 0 && root.transferList.length > root.selectedIndex) {
            var item = root.transferList[root.selectedIndex]
            if (item.state === "paused") root.resumeTransfer(item.pid)
            else root.pauseTransfer(item.pid)
          }
        } else if (t === "o" || t === "O") {
          if (root.activeTab === 0 && root.transferList.length > root.selectedIndex) {
            var it = root.transferList[root.selectedIndex]
            root.openPath(it.destination || it.source)
          }
        } else if (t === "c" || t === "C") {
          if (root.activeTab === 0 && root.transferList.length > root.selectedIndex) {
            var itm = root.transferList[root.selectedIndex]
            root.copyText(itm.command)
          }
        }
      }

      Flickable {
        id: panelFlick
        anchors.fill: parent
        contentWidth: width
        contentHeight: mainColumn.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        interactive: contentHeight > height
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
          id: mainColumn
          width: panelFlick.width
          spacing: Style.space(12)
          onImplicitHeightChanged: console.log("DEBUG gusthx.transfers mainColumn.implicitHeight =", implicitHeight, "loadedContentHeight =", panel.loadedContentHeight)

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
                text: "\uf021"
                color: root.hasActiveTransfers ? root.accentColor : root.fgColor
                font.pixelSize: Style.font.display
                anchors.verticalCenter: parent.verticalCenter
              }

              Column {
                width: Math.max(Style.space(120), parent.width - Style.space(50))
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(2)

                PlainText {
                  text: tr.t("title")
                  font.pixelSize: Style.font.title
                  font.bold: true
                  width: parent.width
                }

                PlainText {
                  text: root.hasActiveTransfers
                    ? tr.t("subtitle.active").arg(root.transferCount).arg(Model.formatSpeed(root.aggSpeed))
                    : tr.t("subtitle.idle")
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

              // Status message pill
              BorderSurface {
                visible: root.statusMessage !== ""
                anchors.verticalCenter: parent.verticalCenter
                radius: Style.cornerRadius
                color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.2)
                borderSpec: Border.flat(root.accentColor, 1)
                implicitWidth: statusLabel.implicitWidth + Style.space(12)
                implicitHeight: Style.space(26)

                PlainText {
                  id: statusLabel
                  anchors.centerIn: parent
                  text: root.statusMessage
                  color: root.accentColor
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }
              }

              Button {
                iconText: "\uf021"
                iconSize: Style.font.body
                tooltipText: "Atualizar (r)"
                foreground: root.fgColor
                fontFamily: root.bar ? root.bar.fontFamily : Style.font.family
                horizontalPadding: Style.space(8)
                verticalPadding: Style.space(5)
                bordered: true
                onClicked: root.pollTransfers()
              }
            }
          }

          PanelSeparator { width: parent.width }

          // ==================== TABS BAR ====================
          Row {
            width: parent.width
            spacing: Style.space(6)

            TabButton {
              text: tr.t("tab.active") + (root.transferCount > 0 ? " (" + root.transferCount + ")" : "")
              iconText: "\uf021"
              selected: root.activeTab === 0
              accent: root.accentColor
              onClicked: root.activeTab = 0
            }

            TabButton {
              text: tr.t("tab.recent") + (root.recentList.length > 0 ? " (" + root.recentList.length + ")" : "")
              iconText: "\uf00c"
              selected: root.activeTab === 1
              accent: root.accentColor
              onClicked: root.activeTab = 1
            }

            TabButton {
              text: tr.t("tab.settings")
              iconText: "\uf013"
              selected: root.activeTab === 2
              accent: root.accentColor
              onClicked: root.activeTab = 2
            }
          }

          // ==================== TAB 0: ACTIVE TRANSFERS ====================
          Column {
            visible: root.activeTab === 0
            width: parent.width
            spacing: Style.space(10)

            // Empty state
            SurfaceCard {
              visible: root.transferList.length === 0
              PlainText {
                text: "\uf00c  " + tr.t("empty.active")
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

            // Active transfers cards
            Repeater {
              model: root.transferList

              SurfaceCard {
                required property var modelData
                required property int index

                borderSpec: index === root.selectedIndex
                  ? Border.controlSpec("focus", root.accentColor, root.accentColor)
                  : Border.controlSpec("normal", root.fgColor, root.accentColor)

                // Header: Tool + PID + State + Speed
                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  // Tool pill
                  BorderSurface {
                    radius: Style.cornerRadius
                    color: Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.16)
                    borderSpec: Border.flat(Qt.rgba(root.accentColor.r, root.accentColor.g, root.accentColor.b, 0.3), 1)
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
                  BorderSurface {
                    radius: Style.cornerRadius
                    color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
                    borderSpec: Border.none()
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
                  BorderSurface {
                    radius: Style.cornerRadius
                    color: modelData.state === "paused"
                      ? Qt.rgba(root.urgentColor.r, root.urgentColor.g, root.urgentColor.b, 0.18)
                      : Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.08)
                    borderSpec: modelData.state === "paused"
                      ? Border.flat(root.urgentColor, 1)
                      : Border.none()
                    implicitWidth: stateLabel.implicitWidth + Style.space(8)
                    implicitHeight: Style.space(22)

                    PlainText {
                      id: stateLabel
                      anchors.centerIn: parent
                      text: modelData.state === "disk_wait" ? tr.t("state.diskWait") :
                            modelData.state === "paused" ? tr.t("state.paused") :
                            modelData.state === "sleeping" ? tr.t("state.sleeping") : tr.t("state.running")
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

                  Row {
                    width: parent.width
                    spacing: Style.space(4)
                    PlainText {
                      text: tr.t("card.source") + ":"
                      color: Qt.darker(root.fgColor, 1.5)
                      font.pixelSize: Style.font.caption
                      width: Style.space(55)
                    }
                    PlainText {
                      text: Model.shortenPath(modelData.source, 48)
                      color: root.fgColor
                      font.pixelSize: Style.font.caption
                      width: parent.width - Style.space(60)
                    }
                  }

                  Row {
                    width: parent.width
                    spacing: Style.space(4)
                    PlainText {
                      text: tr.t("card.destination") + ":"
                      color: Qt.darker(root.fgColor, 1.5)
                      font.pixelSize: Style.font.caption
                      width: Style.space(55)
                    }
                    PlainText {
                      text: Model.shortenPath(modelData.destination, 48)
                      color: root.fgColor
                      font.pixelSize: Style.font.caption
                      font.bold: true
                      width: parent.width - Style.space(60)
                    }
                  }
                }

                // ==================== OVERALL SYNC PROGRESS ====================
                BorderSurface {
                  visible: Boolean(modelData.overall_total_bytes > 0)
                  width: parent.width
                  radius: Style.cornerRadius
                  color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)
                  borderSpec: Border.flat(Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12), 1)
                  padding: Style.space(6)
                  implicitHeight: overallCol.implicitHeight + contentTopInset + contentBottomInset

                  Column {
                    id: overallCol
                    width: parent.width - parent.contentLeftInset - parent.contentRightInset
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: parent.contentTopInset
                    anchors.leftMargin: parent.contentLeftInset
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
                        text: tr.t("card.overallProgress")
                        font.bold: true
                        font.pixelSize: Style.font.bodySmall
                      }
                    }

                    // Progress track
                    Rectangle {
                      width: parent.width
                      height: Style.space(6)
                      radius: Style.space(3)
                      color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)

                      Rectangle {
                        height: parent.height
                        radius: Style.space(3)
                        color: root.accentColor
                        width: Math.min(parent.width, Math.max(0, parent.width * (modelData.overall_pct / 100.0)))

                        Behavior on width {
                          NumberAnimation {
                            duration: Style.duration(180)
                            easing.type: Easing.OutCubic
                          }
                        }
                      }
                    }

                    // Progress text
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

                // ==================== ACTIVE FILE PROGRESS ====================
                BorderSurface {
                  visible: modelData.active_file !== null
                  width: parent.width
                  radius: Style.cornerRadius
                  color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.05)
                  borderSpec: Border.flat(Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12), 1)
                  padding: Style.space(6)
                  implicitHeight: fileCol.implicitHeight + contentTopInset + contentBottomInset

                  Column {
                    id: fileCol
                    width: parent.width - parent.contentLeftInset - parent.contentRightInset
                    anchors.top: parent.top
                    anchors.left: parent.left
                    anchors.topMargin: parent.contentTopInset
                    anchors.leftMargin: parent.contentLeftInset
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

                    // Progress track
                    Rectangle {
                      width: parent.width
                      height: Style.space(6)
                      radius: Style.space(3)
                      color: Qt.rgba(root.fgColor.r, root.fgColor.g, root.fgColor.b, 0.12)

                      Rectangle {
                        height: parent.height
                        radius: Style.space(3)
                        color: root.accentColor
                        width: modelData.active_file
                          ? Math.min(parent.width, Math.max(0, parent.width * (modelData.active_file.pct / 100.0)))
                          : 0

                        Behavior on width {
                          NumberAnimation {
                            duration: Style.duration(180)
                            easing.type: Easing.OutCubic
                          }
                        }
                      }
                    }

                    // Progress text: 2.1 GB of 4.0 GB (52.5%) · ETA: 22s
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

                // ==================== STATS ROW ====================
                Row {
                  width: parent.width
                  spacing: Style.space(12)

                  PlainText {
                    text: tr.t("card.transferred") + ": " + Model.formatBytes(modelData.total_bytes)
                    color: Qt.darker(root.fgColor, 1.3)
                    font.pixelSize: Style.font.caption
                  }

                  PlainText {
                    text: tr.t("card.elapsed") + ": " + modelData.elapsed_str
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

                // ==================== ACTIONS TOOLBAR ====================
                Row {
                  width: parent.width
                  spacing: Style.space(6)

                  ActionBtn {
                    text: modelData.state === "paused" ? tr.t("action.resume") : tr.t("action.pause")
                    iconText: modelData.state === "paused" ? "\uf04b" : "\uf04c"
                    onClicked: {
                      if (modelData.state === "paused") root.resumeTransfer(modelData.pid)
                      else root.pauseTransfer(modelData.pid)
                    }
                  }

                  ActionBtn {
                    text: tr.t("action.openDest")
                    iconText: "\uf07b"
                    visible: Boolean(modelData.destination)
                    onClicked: root.openPath(modelData.destination)
                  }

                  ActionBtn {
                    text: tr.t("action.copyCmd")
                    iconText: "\uf0c5"
                    onClicked: root.copyText(modelData.command)
                  }

                  ActionBtn {
                    text: tr.t("action.cancel")
                    iconText: "\uf00d"
                    foreground: root.urgentColor
                    onClicked: root.cancelTransfer(modelData.pid)
                  }
                }
              }
            }
          }

          // ==================== TAB 1: RECENT COMPLETED ====================
          Column {
            visible: root.activeTab === 1
            width: parent.width
            spacing: Style.space(8)

            SurfaceCard {
              visible: root.recentList.length === 0
              PlainText {
                text: "\uf00c  " + tr.t("empty.recent")
                color: Qt.darker(root.fgColor, 1.4)
                font.pixelSize: Style.font.body
              }
            }

            Repeater {
              model: root.recentList

              SurfaceCard {
                required property var modelData
                Row {
                  width: parent.width
                  spacing: Style.space(6)
                  PlainText {
                    text: "\uf00c"
                    color: root.accentColor
                    font.pixelSize: Style.font.bodySmall
                  }
                  PlainText {
                    text: modelData.tool
                    font.bold: true
                    font.pixelSize: Style.font.bodySmall
                  }
                  PlainText {
                    text: "· " + Model.formatBytes(modelData.total_bytes) + " em " + modelData.elapsed_str
                    color: Qt.darker(root.fgColor, 1.4)
                    font.pixelSize: Style.font.caption
                  }
                }

                PlainText {
                  text: Model.shortenPath(modelData.destination || modelData.source, 50)
                  color: Qt.darker(root.fgColor, 1.5)
                  font.pixelSize: Style.font.caption
                }
              }
            }
          }

          // ==================== TAB 2: SETTINGS ====================
          Column {
            visible: root.activeTab === 2
            width: parent.width
            spacing: Style.space(8)

            SurfaceCard {
              Toggle {
                width: parent.width
                label: tr.t("setting.showWhenIdle")
                description: "Mantém o ícone visível na barra mesmo quando não houver transferências."
                checked: root.showWhenIdle
                onClicked: root.toggleShowWhenIdle()
              }

              PanelSeparator { width: parent.width }

              PlainText {
                text: tr.t("setting.hint")
                color: Qt.darker(root.fgColor, 1.5)
                font.pixelSize: Style.font.caption
                width: parent.width
                wrapMode: Text.WordWrap
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
    horizontalPadding: Style.space(10)
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

  component SurfaceCard: BorderSurface {
    id: cardRoot
    default property alias cardContent: cardCol.data
    width: parent ? parent.width : 0
    padding: Style.space(12)
    radius: Style.cornerRadius
    color: Style.hoverFillFor(root.fgColor, root.accentColor)
    borderSpec: Border.controlSpec("normal", root.fgColor, root.accentColor)
    implicitHeight: cardCol.implicitHeight + cardRoot.contentTopInset + cardRoot.contentBottomInset

    Column {
      id: cardCol
      width: parent.width - cardRoot.contentLeftInset - cardRoot.contentRightInset
      anchors.top: parent.top
      anchors.left: parent.left
      anchors.topMargin: cardRoot.contentTopInset
      anchors.leftMargin: cardRoot.contentLeftInset
      spacing: Style.space(8)
    }
  }
}
