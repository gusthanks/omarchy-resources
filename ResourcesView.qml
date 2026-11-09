import QtQuick
import QtQuick.Layouts
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// The popup's content. Everything it shows and does goes through `monitor`
// (Monitor.qml: live data in the shell, mock data in dev/Harness.qml), so it
// can be rendered on its own.
PanelKeyCatcher {
  id: view

  required property var monitor

  Commons.Translations {
    id: tr
    directory: Qt.resolvedUrl("i18n")
  }

  Connections {
    target: view.monitor
    function onActiveTabChanged() { view.resetScroll() }
  }

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

  // Scrolls so a row (a Repeater delegate inside the tab content) is in view.
  function ensureItemVisible(item) {
    if (!item) return
    ensureVisible(item.mapToItem(tabContentColumn, 0, 0).y, item.height)
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

  onCloseRequested: monitor.close()
  onTabRequested: function(direction) {
    monitor.activeTab = (monitor.activeTab + direction + 5) % 5
  }
  onMoveRequested: function(dx, dy) {
    if (dx !== 0) {
      monitor.activeTab = (monitor.activeTab + dx + 5) % 5
    } else if (dy !== 0 && monitor.activeTab === 1) {
      var list = monitor.currentProcList()
      if (list.length > 0) {
        monitor.selectedProcIndex = Math.max(0, Math.min(list.length - 1, monitor.selectedProcIndex + dy))
        view.ensureItemVisible(procRepeater.itemAt(monitor.selectedProcIndex))
      }
    } else if (dy !== 0 && monitor.activeTab === 4) {
      if (monitor.transferList.length > 0) {
        monitor.selectedTransferIndex = Math.max(0, Math.min(monitor.transferList.length - 1, monitor.selectedTransferIndex + dy))
        view.ensureItemVisible(transferRepeater.itemAt(monitor.selectedTransferIndex))
      }
    } else if (dy !== 0) {
      view.scrollBy(dy * Style.space(60))
    }
  }
  onDeleteRequested: {
    if (monitor.activeTab === 1) {
      var list = monitor.currentProcList()
      if (monitor.selectedProcIndex >= 0 && monitor.selectedProcIndex < list.length) {
        var p = list[monitor.selectedProcIndex]
        monitor.killProcess(p.pid, p.name)
      }
    } else if (monitor.activeTab === 4 && monitor.transferList.length > 0) {
      var tObj = monitor.transferList[monitor.selectedTransferIndex] || monitor.transferList[0]
      if (tObj) monitor.cancelTransfer(tObj.pid)
    }
  }
  onTextKey: function(t) {
    if (t === "1") monitor.activeTab = 0
    else if (t === "2") monitor.activeTab = 1
    else if (t === "3") monitor.activeTab = 2
    else if (t === "4") monitor.activeTab = 3
    else if (t === "5") monitor.activeTab = 4
    else if (t === "b" || t === "B") monitor.openBtop()
    else if (t === "m" || t === "M") monitor.cycleBarMode()
    else if ((t === "s" || t === "S") && monitor.activeTab === 1) {
      monitor.procSort = monitor.procSort === "cpu" ? "mem" : "cpu"
    } else if (monitor.activeTab === 4 && monitor.transferList.length > 0) {
      var curT = monitor.transferList[monitor.selectedTransferIndex] || monitor.transferList[0]
      if (t === "p" || t === "P") {
        if (curT.state === "paused") monitor.resumeTransfer(curT.pid)
        else monitor.pauseTransfer(curT.pid)
      } else if (t === "o" || t === "O") {
        monitor.openTransferPath(curT.destination || curT.source)
      } else if (t === "c" || t === "C") {
        monitor.copyTransferText(curT.command)
      } else if (t === "x" || t === "X") {
        monitor.cancelTransfer(curT.pid)
      }
    } else if (t === "r" || t === "R") {
      monitor.sampleFastMetrics()
      monitor.refreshDetails()
      monitor.pollTransfers()
    }
  }

  Keys.onPressed: function(event) {
    if (event.key === Qt.Key_PageDown) {
      view.scrollBy(Style.space(220))
      event.accepted = true
    } else if (event.key === Qt.Key_PageUp) {
      view.scrollBy(-Style.space(220))
      event.accepted = true
    } else if (event.key === Qt.Key_Home) {
      view.resetScroll()
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
          color: monitor.urgentState ? monitor.urgentColor : monitor.fgColor
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
              .arg(monitor.uptimeFormatted)
              .arg(monitor.fmtNum(monitor.load1, 2) + " / " + monitor.fmtNum(monitor.load5, 2))
            color: Qt.darker(monitor.fgColor, 1.4)
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
          visible: monitor.effectiveTempC > 0
          anchors.verticalCenter: parent.verticalCenter
          radius: Style.cornerRadius > 0 ? Math.min(Style.space(6), Style.cornerRadius) : 0
          color: monitor.effectiveTempC >= 80
            ? Qt.rgba(monitor.urgentColor.r, monitor.urgentColor.g, monitor.urgentColor.b, 0.22)
            : Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.08)
          border.width: 1
          border.color: monitor.effectiveTempC >= 80
            ? monitor.urgentColor
            : Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.2)
          implicitWidth: tempLabel.implicitWidth + Style.space(14)
          implicitHeight: Style.space(26)

          PlainText {
            id: tempLabel
            anchors.centerIn: parent
            text: "󰔏 " + monitor.fmtNum(monitor.effectiveTempC) + "°C"
            color: monitor.effectiveTempC >= 80 ? monitor.urgentColor : monitor.fgColor
            font.pixelSize: Style.font.bodySmall
            font.bold: true
          }
        }

        Button {
          iconText: "󰒓"
          iconSize: Style.font.body
          tooltipText: Model.neutralizeMarkup(
            tr.t("mode.buttonTooltip").arg(monitor.modeDisplayName(monitor.barMode)),
            120
          )
          foreground: monitor.fgColor
          fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
          horizontalPadding: Style.space(8)
          verticalPadding: Style.space(5)
          bordered: true
          onClicked: monitor.cycleBarMode()
        }

        Button {
          iconText: "󰆍"
          text: "btop"
          fontSize: Style.font.caption
          iconSize: Style.font.bodySmall
          tooltipText: Model.neutralizeMarkup(tr.t("btop.buttonTooltip"), 120)
          foreground: monitor.fgColor
          fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
          horizontalPadding: Style.space(8)
          verticalPadding: Style.space(5)
          bordered: true
          onClicked: monitor.openBtop()
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
        active: monitor.activeTab === 0
        onClicked: monitor.activeTab = 0
      }
      TabButton {
        width: Math.round(tabRow.remWidth * 0.28)
        iconText: "󰒋"
        text: tr.t("tab.processes")
        active: monitor.activeTab === 1
        onClicked: monitor.activeTab = 1
      }
      TabButton {
        width: Math.round(tabRow.remWidth * 0.20)
        iconText: "󰋊"
        text: tr.t("tab.disks")
        active: monitor.activeTab === 2
        onClicked: monitor.activeTab = 2
      }
      TabButton {
        width: Math.round(tabRow.remWidth * 0.24)
        iconText: "󰔏"
        text: tr.t("tab.sensors")
        active: monitor.activeTab === 3
        onClicked: monitor.activeTab = 3
      }
      TabButton {
        width: tabRow.remWidth - Math.round(tabRow.remWidth * 0.28) - Math.round(tabRow.remWidth * 0.20) - Math.round(tabRow.remWidth * 0.24)
        iconText: "\uf021"
        text: monitor.hasActiveTransfers ? (tr.t("tab.transfers") + " (" + monitor.transferCount + ")") : tr.t("tab.transfers")
        active: monitor.activeTab === 4
        onClicked: monitor.activeTab = 4
      }
    }

    PanelSeparator { foreground: monitor.fgColor }
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
        visible: monitor.activeTab === 0
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
                color: Qt.darker(monitor.fgColor, 1.35)
                font.pixelSize: Style.font.caption
                font.bold: true
                width: parent.width
              }

              PlainText {
                text: monitor.details.cpuModel
                font.pixelSize: Style.font.bodySmall
                width: parent.width
              }
            }

            PlainText {
              id: cpuBigPct
              text: monitor.fmtNum(Math.round(monitor.cpuPct)) + "%"
              color: monitor.cpuPct >= 85 ? monitor.urgentColor : monitor.fgColor
              font.pixelSize: Style.font.heading
              font.bold: true
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          SparkGraph {
            width: parent.width
            height: Style.space(44)
            values: monitor.cpuHistory
            maxValue: 100
            revision: monitor.historyRevision
            lineColor: monitor.cpuPct >= 85 ? monitor.urgentColor : monitor.accentColor
          }

          // Per-core bars (grid of 2 columns)
          Grid {
            visible: monitor.cpuCores.length > 0
            width: parent.width
            columns: 2
            columnSpacing: Style.space(10)
            rowSpacing: Style.space(6)

            Repeater {
              model: monitor.cpuCores
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
                      text: tr.t("cpu.core").arg(monitor.fmtNum(modelData.id))
                        + (monitor.details.coreFreqs[modelData.id]
                          ? " · " + monitor.fmtNum(monitor.details.coreFreqs[modelData.id]) + " MHz"
                          : "")
                      color: Qt.darker(monitor.fgColor, 1.3)
                      font.pixelSize: Style.font.caption
                      width: parent.width - coreVal.implicitWidth
                    }
                    PlainText {
                      id: coreVal
                      text: monitor.fmtNum(Math.round(modelData.pct)) + "%"
                      font.pixelSize: Style.font.caption
                      font.bold: true
                    }
                  }

                  ProgressBarTrack {
                    width: parent.width
                    height: Style.space(5)
                    fraction: modelData.pct / 100.0
                    fillColor: modelData.pct >= 85 ? monitor.urgentColor : monitor.fgColor
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
              label: tr.t("cpu.userSys")
              value: monitor.fmtNum(Math.round(monitor.cpuUserPct)) + "% / " + monitor.fmtNum(Math.round(monitor.cpuSysPct)) + "%"
            }
            MiniStatPill {
              width: (parent.width - Style.space(20)) / 3
              label: tr.t("cpu.freq")
              value: monitor.details.avgFreqMhz > 0
                ? monitor.fmtNum(monitor.details.avgFreqMhz / 1000.0, 2) + " GHz"
                : "—"
            }
            MiniStatPill {
              width: (parent.width - Style.space(20)) / 3
              label: tr.t("cpu.threads")
              value: monitor.fmtNum(monitor.runningThreads) + " / " + monitor.fmtNum(monitor.totalThreads)
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
                color: Qt.darker(monitor.fgColor, 1.35)
                font.pixelSize: Style.font.caption
                font.bold: true
                width: parent.width
              }

              PlainText {
                text: Model.formatBytes(monitor.memUsedBytes, 1, monitor.fmtNum)
                  + " / " + Model.formatBytes(monitor.memTotalBytes, 1, monitor.fmtNum)
                font.pixelSize: Style.font.bodySmall
                width: parent.width
              }
            }

            PlainText {
              id: memBigPct
              text: monitor.fmtNum(Math.round(monitor.memPct)) + "%"
              color: monitor.memPct >= 88 ? monitor.urgentColor : monitor.fgColor
              font.pixelSize: Style.font.heading
              font.bold: true
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          SparkGraph {
            width: parent.width
            height: Style.space(38)
            values: monitor.memHistory
            maxValue: 100
            revision: monitor.historyRevision
            lineColor: monitor.memPct >= 88 ? monitor.urgentColor : monitor.fgColor
          }

          Row {
            width: parent.width
            spacing: Style.space(8)

            MiniStatPill {
              width: (parent.width - Style.space(16)) / 3
              label: tr.t("mem.available")
              value: Model.formatBytes(monitor.memAvailBytes, 1, monitor.fmtNum)
            }
            MiniStatPill {
              width: (parent.width - Style.space(16)) / 3
              label: tr.t("mem.cached")
              value: Model.formatBytes(monitor.memCachedBytes + monitor.memBuffersBytes, 1, monitor.fmtNum)
            }
            MiniStatPill {
              width: (parent.width - Style.space(16)) / 3
              label: tr.t("mem.swap")
              value: monitor.swapTotalBytes > 0
                ? Model.formatBytes(monitor.swapUsedBytes, 1, monitor.fmtNum) + " (" + monitor.fmtNum(Math.round(monitor.swapPct)) + "%)"
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
                color: Qt.darker(monitor.fgColor, 1.35)
                font.pixelSize: Style.font.caption
                font.bold: true
                width: parent.width - netIfaceBadge.implicitWidth
              }
              PlainText {
                id: netIfaceBadge
                text: monitor.netIface
                color: Qt.darker(monitor.fgColor, 1.4)
                font.pixelSize: Style.font.caption
              }
            }

            Row {
              width: parent.width
              PlainText {
                text: "↓ " + Model.formatRate(monitor.netRxRateBps, monitor.fmtNum)
                font.pixelSize: Style.font.bodySmall
                font.bold: true
                width: parent.width / 2
              }
              PlainText {
                text: "↑ " + Model.formatRate(monitor.netTxRateBps, monitor.fmtNum)
                color: Qt.darker(monitor.fgColor, 1.25)
                font.pixelSize: Style.font.bodySmall
                horizontalAlignment: Text.AlignRight
                width: parent.width / 2
              }
            }

            SparkGraph {
              width: parent.width
              height: Style.space(32)
              values: monitor.netRxHistory
              secondaryValues: monitor.netTxHistory
              maxValue: 0
              revision: monitor.historyRevision
              lineColor: monitor.accentColor
            }

            PlainText {
              text: tr.t("net.total")
                .arg(Model.formatShortBytes(monitor.netTotalRxBytes, monitor.fmtNum))
                .arg(Model.formatShortBytes(monitor.netTotalTxBytes, monitor.fmtNum))
              color: Qt.darker(monitor.fgColor, 1.4)
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
              color: Qt.darker(monitor.fgColor, 1.35)
              font.pixelSize: Style.font.caption
              font.bold: true
              width: parent.width
            }

            Row {
              width: parent.width
              PlainText {
                text: "R " + Model.formatRate(monitor.diskReadRateBps, monitor.fmtNum)
                font.pixelSize: Style.font.bodySmall
                font.bold: true
                width: parent.width / 2
              }
              PlainText {
                text: "W " + Model.formatRate(monitor.diskWriteRateBps, monitor.fmtNum)
                color: Qt.darker(monitor.fgColor, 1.25)
                font.pixelSize: Style.font.bodySmall
                horizontalAlignment: Text.AlignRight
                width: parent.width / 2
              }
            }

            SparkGraph {
              width: parent.width
              height: Style.space(32)
              values: monitor.diskReadHistory
              secondaryValues: monitor.diskWriteHistory
              maxValue: 0
              revision: monitor.historyRevision
              lineColor: monitor.fgColor
            }

            PlainText {
              text: tr.t("io.total")
                .arg(Model.formatShortBytes(monitor.diskTotalReadBytes, monitor.fmtNum))
                .arg(Model.formatShortBytes(monitor.diskTotalWriteBytes, monitor.fmtNum))
              color: Qt.darker(monitor.fgColor, 1.4)
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
        onClicked: monitor.activeTab = 4

        Column {
          width: parent.width
          spacing: Style.space(8)

          Row {
            width: parent.width
            spacing: Style.space(8)

            PlainText {
              text: "\uf021  " + tr.t("tab.transfers").toUpperCase()
              color: Qt.darker(monitor.fgColor, 1.35)
              font.pixelSize: Style.font.caption
              font.bold: true
              width: parent.width - (monitor.hasActiveTransfers ? speedBadge.implicitWidth : idleBadge.implicitWidth) - Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
            }

            // Speed badge when active
            Rectangle {
              id: speedBadge
              visible: monitor.hasActiveTransfers
              radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
              color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.16)
              border.width: 1
              border.color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.3)
              implicitWidth: speedText.implicitWidth + Style.space(12)
              implicitHeight: Style.space(20)
              anchors.verticalCenter: parent.verticalCenter

              PlainText {
                id: speedText
                anchors.centerIn: parent
                text: Model.formatSpeed(monitor.aggTransferSpeed)
                color: monitor.accentColor
                font.pixelSize: Style.font.caption
                font.bold: true
              }
            }

            // Status when idle
            PlainText {
              id: idleBadge
              visible: !monitor.hasActiveTransfers
              text: tr.t("transfers.idle")
              color: Qt.darker(monitor.fgColor, 1.45)
              font.pixelSize: Style.font.caption
              anchors.verticalCenter: parent.verticalCenter
            }
          }

          // Active transfers list in Overview
          Repeater {
            model: monitor.transferList

            Column {
              required property var modelData
              width: parent.width
              spacing: Style.space(4)

              RowLayout {
                width: parent.width
                spacing: Style.space(6)

                PlainText {
                  text: Model.toolIcon(modelData.tool)
                  color: monitor.accentColor
                  font.pixelSize: Style.font.bodySmall
                }

                PlainText {
                  text: Model.toolDisplayTitle(modelData.tool, monitor.t)
                  font.bold: true
                  font.pixelSize: Style.font.caption
                  Layout.maximumWidth: implicitWidth
                  Layout.fillWidth: true
                }

                PlainText {
                  text: modelData.batch ? modelData.batch.name : tr.t("transfers.pid").arg(modelData.pid)
                  color: Qt.darker(monitor.fgColor, 1.45)
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideMiddle
                  Layout.fillWidth: true
                }


                PlainText {
                  text: Model.formatSpeed(modelData.active_speed)
                  color: monitor.accentColor
                  font.bold: true
                  font.pixelSize: Style.font.caption
                }
              }

              PlainText {
                visible: Boolean(modelData.destination || modelData.source)
                text: modelData.source && modelData.destination
                  ? (Model.transferPath(modelData.source, monitor.transferData.home) + " → " + Model.transferPath(modelData.destination, monitor.transferData.home))
                  : Model.transferPath(modelData.source || modelData.destination, monitor.transferData.home)
                color: Qt.darker(monitor.fgColor, 1.35)
                font.pixelSize: Style.font.caption
                elide: Text.ElideMiddle
                width: parent.width
              }

              ProgressBarTrack {
                visible: modelData.overall_total_bytes > 0
                width: parent.width
                height: Style.space(5)
                fraction: Math.max(0, Math.min(1, (modelData.overall_pct || 0) / 100.0))
                fillColor: monitor.accentColor
              }
            }
          }

          // If idle but has recent completed transfers: show latest completed
          Row {
            visible: !monitor.hasActiveTransfers && monitor.recentTransferList.length > 0
            width: parent.width
            spacing: Style.space(6)

            PlainText {
              text: "\uf00c"
              color: monitor.accentColor
              font.pixelSize: Style.font.caption
            }

            PlainText {
              text: monitor.recentTransferList[0]
                ? Model.formatRecentTransfer(monitor.recentTransferList[0], monitor.t)
                : ""
              color: Qt.darker(monitor.fgColor, 1.4)
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
              text: tr.t("overview.transfersLink")
              color: Qt.darker(monitor.fgColor, 1.4)
              font.pixelSize: Style.font.caption
            }
          }
        }
      }
    }

    // ==================== TAB 1: PROCESSES ====================
    Column {
      visible: monitor.activeTab === 1
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
          foreground: monitor.fgColor
          fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
          bordered: true
          active: monitor.procSort === "cpu"
          onClicked: {
            monitor.procSort = "cpu"
            monitor.selectedProcIndex = 0
          }
        }

        Button {
          width: (parent.width - Style.space(6)) / 2
          iconText: "󰘚"
          text: tr.t("proc.sortByMem")
          fontSize: Style.font.bodySmall
          iconSize: Style.font.body
          foreground: monitor.fgColor
          fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
          bordered: true
          active: monitor.procSort === "mem"
          onClicked: {
            monitor.procSort = "mem"
            monitor.selectedProcIndex = 0
          }
        }
      }

      // Feedback toast when SIGTERM is sent
      Rectangle {
        visible: monitor.killFeedbackText !== ""
        width: parent.width
        implicitHeight: Style.space(28)
        radius: Style.cornerRadius
        color: Qt.rgba(monitor.urgentColor.r, monitor.urgentColor.g, monitor.urgentColor.b, 0.16)
        border.width: 1
        border.color: monitor.urgentColor

        PlainText {
          anchors.centerIn: parent
          text: "󰄬 " + monitor.killFeedbackText
          color: monitor.fgColor
          font.pixelSize: Style.font.caption
          font.bold: true
        }
      }

      // Process rows
      Column {
        width: parent.width
        spacing: Style.space(4)

        Repeater {
          id: procRepeater
          model: monitor.currentProcList()

          Rectangle {
            id: procRow
            required property var modelData
            required property int index

            readonly property bool isSelected: monitor.selectedProcIndex === index || rowMouse.containsMouse
            readonly property real barFraction: monitor.procSort === "mem"
              ? Math.min(1, modelData.mem / 25.0)
              : Math.min(1, modelData.cpu / 100.0)

            width: parent.width
            implicitHeight: Style.space(32)
            radius: Style.cornerRadius
            color: isSelected
              ? Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.1)
              : Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.04)
            border.width: isSelected ? 1 : 0
            border.color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.25)

            // Subtle proportional fill bar behind the process row
            Rectangle {
              anchors.left: parent.left
              anchors.top: parent.top
              anchors.bottom: parent.bottom
              width: Math.max(0, parent.width * procRow.barFraction)
              radius: parent.radius
              color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.1)
            }

            MouseArea {
              id: rowMouse
              anchors.fill: parent
              hoverEnabled: true
              onEntered: monitor.selectedProcIndex = procRow.index
              onClicked: monitor.selectedProcIndex = procRow.index
            }

            Row {
              anchors.fill: parent
              anchors.leftMargin: Style.space(10)
              anchors.rightMargin: Style.space(8)
              spacing: Style.space(8)

              PlainText {
                text: String(modelData.pid)
                color: Qt.darker(monitor.fgColor, 1.45)
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
                text: monitor.fmtNum(modelData.cpu, 1) + "%"
                color: modelData.cpu >= 50 ? monitor.urgentColor : monitor.fgColor
                font.pixelSize: Style.font.caption
                font.bold: monitor.procSort === "cpu"
                horizontalAlignment: Text.AlignRight
                width: Style.space(44)
                anchors.verticalCenter: parent.verticalCenter
              }

              PlainText {
                text: Model.formatShortBytes(modelData.rssBytes, monitor.fmtNum)
                color: Qt.darker(monitor.fgColor, monitor.procSort === "mem" ? 1.0 : 1.35)
                font.pixelSize: Style.font.caption
                font.bold: monitor.procSort === "mem"
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
                  ? Qt.rgba(monitor.urgentColor.r, monitor.urgentColor.g, monitor.urgentColor.b, 0.28)
                  : "transparent"

                PlainText {
                  anchors.centerIn: parent
                  text: "󰅖"
                  color: killMouse.containsMouse ? monitor.urgentColor : Qt.darker(monitor.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                }

                MouseArea {
                  id: killMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: monitor.killProcess(modelData.pid, modelData.name)
                }
              }
            }
          }
        }
      }

      PlainText {
        text: tr.t("proc.hint")
        color: Qt.darker(monitor.fgColor, 1.5)
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
        foreground: monitor.fgColor
        fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
        bordered: true
        onClicked: monitor.openBtop()
      }
    }

    // ==================== TAB 2: DISKS & STORAGE ====================
    Column {
      visible: monitor.activeTab === 2
      width: parent.width
      spacing: Style.space(12)

      // Live Disk Throughput Card
      SurfaceCard {
        Column {
          width: parent.width
          spacing: Style.space(8)

          PlainText {
            text: "󰓅  " + tr.t("disks.ioHeader")
            color: Qt.darker(monitor.fgColor, 1.35)
            font.pixelSize: Style.font.caption
            font.bold: true
            width: parent.width
          }

          Row {
            width: parent.width
            PlainText {
              text: "󰁅 " + tr.t("io.read") + ": " + Model.formatRate(monitor.diskReadRateBps, monitor.fmtNum)
              font.pixelSize: Style.font.bodySmall
              font.bold: true
              width: parent.width / 2
            }
            PlainText {
              text: "󰁝 " + tr.t("io.write") + ": " + Model.formatRate(monitor.diskWriteRateBps, monitor.fmtNum)
              font.pixelSize: Style.font.bodySmall
              horizontalAlignment: Text.AlignRight
              width: parent.width / 2
            }
          }

          SparkGraph {
            width: parent.width
            height: Style.space(42)
            values: monitor.diskReadHistory
            secondaryValues: monitor.diskWriteHistory
            maxValue: 0
            revision: monitor.historyRevision
            lineColor: monitor.accentColor
          }

          PlainText {
            text: tr.t("io.total")
              .arg(Model.formatBytes(monitor.diskTotalReadBytes, 1, monitor.fmtNum))
              .arg(Model.formatBytes(monitor.diskTotalWriteBytes, 1, monitor.fmtNum))
            color: Qt.darker(monitor.fgColor, 1.4)
            font.pixelSize: Style.font.caption
            width: parent.width
          }
        }
      }

      PanelSectionHeader {
        text: tr.t("disks.mounts")
        foreground: monitor.fgColor
        fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
      }

      Repeater {
        model: monitor.details.disks

        SurfaceCard {
          required property var modelData
          clickable: monitor.canOpenDisk(modelData)
          onClicked: monitor.openDiskFolder(modelData)

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
                width: parent.width - fsBadge.implicitWidth - pctLabel.implicitWidth - (openIcon.visible ? openIcon.implicitWidth + Style.space(8) : 0) - Style.space(16)
              }

              Rectangle {
                id: fsBadge
                radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
                color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.1)
                implicitWidth: fsText.implicitWidth + Style.space(10)
                implicitHeight: Style.space(18)
                anchors.verticalCenter: parent.verticalCenter

                PlainText {
                  id: fsText
                  anchors.centerIn: parent
                  text: modelData.fstype
                  color: Qt.darker(monitor.fgColor, 1.2)
                  font.pixelSize: Style.font.caption
                }
              }

              PlainText {
                id: pctLabel
                text: monitor.fmtNum(Math.round(modelData.pct)) + "%"
                color: modelData.pct >= 90 ? monitor.urgentColor : monitor.fgColor
                font.pixelSize: Style.font.body
                font.bold: true
                anchors.verticalCenter: parent.verticalCenter
              }

              PlainText {
                id: openIcon
                visible: monitor.canOpenDisk(modelData)
                text: "\uf07b"
                color: Qt.darker(monitor.fgColor, 1.45)
                font.pixelSize: Style.font.bodySmall
                anchors.verticalCenter: parent.verticalCenter
              }
            }

            ProgressBarTrack {
              width: parent.width
              height: Style.space(7)
              fraction: modelData.pct / 100.0
              fillColor: modelData.pct >= 90 ? monitor.urgentColor : monitor.fgColor
            }

            Row {
              width: parent.width
              PlainText {
                text: modelData.device
                color: Qt.darker(monitor.fgColor, 1.45)
                font.pixelSize: Style.font.caption
                width: parent.width * 0.45
              }
              PlainText {
                text: tr.t("disks.free")
                  .arg(Model.formatBytes(modelData.availBytes, 1, monitor.fmtNum))
                  .arg(Model.formatBytes(modelData.totalBytes, 1, monitor.fmtNum))
                color: Qt.darker(monitor.fgColor, 1.25)
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
      visible: monitor.activeTab === 3
      width: parent.width
      spacing: Style.space(10)

      PanelSectionHeader {
        text: tr.t("sensors.thermal")
        foreground: monitor.fgColor
        fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
      }

      SurfaceCard {
        Column {
          width: parent.width
          spacing: Style.space(8)

          Repeater {
            model: monitor.details.sensors

            Column {
              required property var modelData
              width: parent.width
              spacing: Style.space(3)

              Row {
                width: parent.width
                PlainText {
                  text: modelData.chip + " · " + modelData.label
                  color: Qt.darker(monitor.fgColor, 1.2)
                  font.pixelSize: Style.font.bodySmall
                  width: parent.width - sensorVal.implicitWidth
                }
                PlainText {
                  id: sensorVal
                  text: modelData.value + (modelData.unit ? " " + modelData.unit : "")
                  color: (modelData.unit === "°C" && modelData.numValue >= 80) ? monitor.urgentColor : monitor.fgColor
                  font.pixelSize: Style.font.bodySmall
                  font.bold: true
                }
              }

              ProgressBarTrack {
                visible: modelData.unit === "°C"
                width: parent.width
                height: Style.space(4)
                fraction: Math.min(1.0, Math.max(0.05, modelData.numValue / 100.0))
                fillColor: modelData.numValue >= 80 ? monitor.urgentColor : monitor.fgColor
              }
            }
          }
        }
      }

      PanelSectionHeader {
        text: tr.t("sensors.hardware")
        foreground: monitor.fgColor
        fontFamily: monitor.bar ? monitor.bar.fontFamily : Style.font.family
      }

      SurfaceCard {
        Column {
          width: parent.width
          spacing: Style.space(6)

          HardwareRow {
            label: tr.t("sensors.cpuModel")
            value: monitor.details.cpuModel
          }
          HardwareRow {
            label: tr.t("sensors.kernel")
            value: monitor.details.kernel
          }
          HardwareRow {
            visible: monitor.details.gpuMaxMhz > 0
            label: tr.t("sensors.gpuClock")
            value: monitor.fmtNum(monitor.details.gpuCurMhz) + " / " + monitor.fmtNum(monitor.details.gpuMaxMhz) + " MHz"
          }
          HardwareRow {
            label: tr.t("cpu.load")
            value: monitor.fmtNum(monitor.load1, 2) + " · " + monitor.fmtNum(monitor.load5, 2) + " · " + monitor.fmtNum(monitor.load15, 2)
          }
          HardwareRow {
            label: tr.t("sensors.ctxSwitches")
            value: monitor.fmtNum(monitor.ctxtRate) + " /s"
          }
          HardwareRow {
            label: tr.t("sensors.uptime")
            value: monitor.uptimeFormatted
          }
        }
      }
    }

    // ==================== TAB 4: TRANSFERS ====================
    Column {
      visible: monitor.activeTab === 4
      width: parent.width
      spacing: Style.space(10)

      // Status message pill
      Rectangle {
        visible: monitor.transferStatusMessage !== ""
        anchors.horizontalCenter: parent.horizontalCenter
        radius: Style.cornerRadius
        color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.2)
        border.width: 1
        border.color: monitor.accentColor
        implicitWidth: transferStatusLabel.implicitWidth + Style.space(16)
        implicitHeight: Style.space(24)

        PlainText {
          id: transferStatusLabel
          anchors.centerIn: parent
          text: monitor.transferStatusMessage
          color: monitor.accentColor
          font.bold: true
          font.pixelSize: Style.font.caption
        }
      }

      // Empty state
      SurfaceCard {
        visible: monitor.transferList.length === 0
        Column {
          width: parent.width
          spacing: Style.space(4)
          PlainText {
            text: "\uf00c  " + tr.t("transfers.empty")
            font.bold: true
            color: Qt.darker(monitor.fgColor, 1.3)
            font.pixelSize: Style.font.body
          }
          PlainText {
            text: tr.t("transfers.emptyHint")
            color: Qt.darker(monitor.fgColor, 1.6)
            font.pixelSize: Style.font.caption
            width: parent.width
            wrapMode: Text.WordWrap
          }
        }
      }

      // Everything at once: count, combined speed and progress, and the
      // scripts (batches) the transfers belong to.
      SurfaceCard {
        visible: monitor.transferList.length > 0

        Column {
          width: parent.width
          spacing: Style.space(6)

          RowLayout {
            width: parent.width
            spacing: Style.space(8)
            PlainText {
              text: tr.t("transfers.all").toUpperCase()
              color: Qt.darker(monitor.fgColor, 1.25)
              font.bold: true
              font.pixelSize: Style.font.caption
            }
            PlainText {
              text: tr.t("transfers.count").arg(monitor.transferCount)
              color: Qt.darker(monitor.fgColor, 1.45)
              font.pixelSize: Style.font.caption
            }
            Item { Layout.fillWidth: true }
            PlainText {
              text: Model.formatSpeed(monitor.aggTransferSpeed)
              color: monitor.accentColor
              font.bold: true
              font.pixelSize: Style.font.bodySmall
            }
          }

          ProgressBarTrack {
            visible: (monitor.transferData.agg_overall_total_bytes || 0) > 0
            width: parent.width
            height: Style.space(6)
            fraction: Math.min(1.0, Math.max(0.0, (monitor.transferData.agg_overall_pct || 0) / 100.0))
            fillColor: monitor.accentColor
          }

          PlainText {
            width: parent.width
            text: (monitor.transferData.agg_overall_total_bytes || 0) > 0
              ? tr.t("transfers.transferredOf").arg(Model.formatBytes(monitor.transferData.agg_total_bytes || 0))
                  .arg((monitor.transferData.agg_overall_is_estimate ? "≈ " : "") + Model.formatBytes(monitor.transferData.agg_overall_total_bytes))
                  .arg(monitor.transferData.agg_overall_pct || 0)
              : tr.t("transfers.transferred").arg(Model.formatBytes(monitor.transferData.agg_total_bytes || 0))
            color: Qt.darker(monitor.fgColor, 1.3)
            font.pixelSize: Style.font.caption
          }

          Repeater {
            model: monitor.transferData.batches || []

            Column {
              required property var modelData
              width: parent.width
              spacing: Style.space(1)

              RowLayout {
                width: parent.width
                spacing: Style.space(6)
                PlainText {
                  text: "\uf0ae"
                  color: monitor.accentColor
                  font.pixelSize: Style.font.caption
                }
                PlainText {
                  text: tr.t("transfers.batch") + ": " + modelData.name
                  font.bold: true
                  font.pixelSize: Style.font.caption
                  elide: Text.ElideMiddle
                  Layout.fillWidth: true
                }
                PlainText {
                  text: modelData.state === "paused" ? tr.t("transfers.batchPaused") : tr.t("transfers.batchSteps").arg(modelData.transfers)
                  color: modelData.state === "paused" ? monitor.urgentColor : Qt.darker(monitor.fgColor, 1.45)
                  font.bold: modelData.state === "paused"
                  font.pixelSize: Style.font.caption
                }
              }
              PlainText {
                width: parent.width
                text: tr.t("transfers.batchInfo").arg(modelData.elapsed_str || "").arg(Model.formatBytes(modelData.total_bytes || 0))
                color: Qt.darker(monitor.fgColor, 1.4)
                font.pixelSize: Style.font.caption
              }
              PlainText {
                width: parent.width
                text: Model.tildePath(modelData.path, monitor.transferData.home)
                color: Qt.darker(monitor.fgColor, 1.6)
                font.pixelSize: Style.font.caption
                elide: Text.ElideMiddle
              }

              // The whole script: its current step and the ones still to come.
              Item { width: 1; height: Style.space(4) }
              Row {
                width: parent.width
                spacing: Style.space(6)

                ActionBtn {
                  text: modelData.state === "paused" ? tr.t("transfers.action.resumeBatch") : tr.t("transfers.action.pauseBatch")
                  iconText: modelData.state === "paused" ? "\uf04b" : "\uf04c"
                  onClicked: {
                    if (modelData.state === "paused") monitor.resumeTransfer(modelData.pid)
                    else monitor.pauseTransfer(modelData.pid)
                  }
                }

                ActionBtn {
                  text: tr.t("transfers.action.cancelBatch")
                  iconText: "\uf00d"
                  foreground: monitor.urgentColor
                  onClicked: monitor.cancelTransfer(modelData.pid)
                }
              }
            }
          }
        }
      }

      // Active transfers cards
      Repeater {
        id: transferRepeater
        model: monitor.transferList

        SurfaceCard {
          required property var modelData
          required property int index

          border.color: index === monitor.selectedTransferIndex
            ? monitor.accentColor
            : Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.12)
          border.width: index === monitor.selectedTransferIndex ? 2 : 1

          Column {
            width: parent.width
            spacing: Style.space(8)

            // Header: Tool + PID + State + Speed
            RowLayout {
              width: parent.width
              spacing: Style.space(6)

              // Tool pill
              Rectangle {
                radius: Style.cornerRadius
                color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.16)
                border.width: 1
                border.color: Qt.rgba(monitor.accentColor.r, monitor.accentColor.g, monitor.accentColor.b, 0.3)
                implicitWidth: toolRow.implicitWidth + Style.space(10)
                implicitHeight: Style.space(22)

                Row {
                  id: toolRow
                  anchors.centerIn: parent
                  spacing: Style.space(4)
                  PlainText {
                    text: Model.toolIcon(modelData.tool)
                    color: monitor.accentColor
                    font.pixelSize: Style.font.caption
                  }
                  PlainText {
                    text: Model.toolDisplayTitle(modelData.tool, monitor.t)
                    color: monitor.accentColor
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                }
              }

              // PID pill
              Rectangle {
                radius: Style.cornerRadius
                color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.08)
                implicitWidth: pidLabel.implicitWidth + Style.space(8)
                implicitHeight: Style.space(22)

                PlainText {
                  id: pidLabel
                  anchors.centerIn: parent
                  text: tr.t("transfers.pid").arg(modelData.pid) + (modelData.tree_pids && modelData.tree_pids.length > 1 ? " (" + modelData.tree_pids.length + ")" : "")
                  color: Qt.darker(monitor.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                }
              }

              // State pill
              Rectangle {
                radius: Style.cornerRadius
                color: modelData.state === "paused"
                  ? Qt.rgba(monitor.urgentColor.r, monitor.urgentColor.g, monitor.urgentColor.b, 0.18)
                  : Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.08)
                border.width: modelData.state === "paused" ? 1 : 0
                border.color: modelData.state === "paused" ? monitor.urgentColor : "transparent"
                implicitWidth: stateLabel.implicitWidth + Style.space(8)
                implicitHeight: Style.space(22)

                PlainText {
                  id: stateLabel
                  anchors.centerIn: parent
                  text: modelData.state === "disk_wait" ? tr.t("transfers.state.diskWait") :
                        modelData.state === "paused" ? tr.t("transfers.state.paused") :
                        modelData.state === "sleeping" ? tr.t("transfers.state.sleeping") : tr.t("transfers.state.running")
                  color: modelData.state === "paused" ? monitor.urgentColor : Qt.darker(monitor.fgColor, 1.2)
                  font.pixelSize: Style.font.caption
                  font.bold: true
                }
              }

              Item { Layout.fillWidth: true }

              // Live speed badge
              PlainText {
                text: Model.formatSpeed(modelData.active_speed)
                color: monitor.accentColor
                font.pixelSize: Style.font.subtitle
                font.bold: true
              }
            }

            // The script this transfer is a step of
            RowLayout {
              visible: Boolean(modelData.batch)
              width: parent.width
              spacing: Style.space(6)
              PlainText {
                text: "\uf0ae"
                color: monitor.accentColor
                font.pixelSize: Style.font.caption
              }
              PlainText {
                text: modelData.batch ? tr.t("transfers.batch") + ": " + modelData.batch.name : ""
                font.bold: true
                font.pixelSize: Style.font.caption
                elide: Text.ElideMiddle
                Layout.fillWidth: true
              }
              PlainText {
                text: modelData.batch ? tr.t("transfers.pid").arg(modelData.batch.pid) : ""
                color: Qt.darker(monitor.fgColor, 1.45)
                font.pixelSize: Style.font.caption
              }
            }

            // Path Flow: Source -> Destination
            Column {
              width: parent.width
              spacing: Style.space(2)

              RowLayout {
                visible: Boolean(modelData.source)
                width: parent.width
                spacing: Style.space(8)
                Text {
                  Layout.alignment: Qt.AlignTop
                  textFormat: Text.PlainText
                  text: tr.t("transfers.source") + ":"
                  color: Qt.darker(monitor.fgColor, 1.5)
                  font.family: monitor.bar ? monitor.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                  Layout.preferredWidth: implicitWidth
                }
                PlainText {
                  // The whole path, on up to two lines: where it is matters.
                  text: Model.transferPath(modelData.source, monitor.transferData.home, 84)
                  color: monitor.fgColor
                  font.pixelSize: Style.font.caption
                  wrapMode: Text.WrapAnywhere
                  maximumLineCount: 2
                  Layout.fillWidth: true
                }
              }

              RowLayout {
                visible: Boolean(modelData.destination)
                width: parent.width
                spacing: Style.space(8)
                Text {
                  Layout.alignment: Qt.AlignTop
                  textFormat: Text.PlainText
                  text: tr.t("transfers.destination") + ":"
                  color: Qt.darker(monitor.fgColor, 1.5)
                  font.family: monitor.bar ? monitor.bar.fontFamily : Style.font.family
                  font.pixelSize: Style.font.caption
                  Layout.preferredWidth: implicitWidth
                }
                PlainText {
                  // The whole path, on up to two lines: where it is matters.
                  text: Model.transferPath(modelData.destination, monitor.transferData.home, 84)
                  color: monitor.fgColor
                  font.pixelSize: Style.font.caption
                  font.bold: true
                  wrapMode: Text.WrapAnywhere
                  maximumLineCount: 2
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
              color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.05)
              border.width: 1
              border.color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.12)

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
                    color: monitor.accentColor
                    font.pixelSize: Style.font.bodySmall
                  }
                  PlainText {
                    text: !modelData.overall_is_estimate ? tr.t("transfers.overall")
                      : modelData.overall_estimate_kind === "source" ? tr.t("transfers.overallEstimateSource") : tr.t("transfers.overallEstimate")
                    font.bold: true
                    font.pixelSize: Style.font.bodySmall
                  }
                }

                ProgressBarTrack {
                  width: parent.width
                  height: Style.space(6)
                  fraction: Math.min(1.0, Math.max(0.0, modelData.overall_pct / 100.0))
                  fillColor: monitor.accentColor
                }

                PlainText {
                  width: parent.width
                  horizontalAlignment: Text.AlignRight
                  text: Model.formatBytes(modelData.total_bytes) + " / " + (modelData.overall_is_estimate ? "≈ " : "")
                    + Model.formatBytes(modelData.overall_total_bytes) + " (" + (modelData.overall_is_estimate ? "≈ " : "") + modelData.overall_pct + "%)"
                    + (modelData.overall_eta ? " · " + tr.t(modelData.overall_is_estimate ? "transfers.etaEstimate" : "transfers.eta").arg(modelData.overall_eta) : "")
                  color: Qt.darker(monitor.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                }
              }
            }

            PlainText {
              visible: Boolean(modelData.overall_scanning)
              width: parent.width
              text: "\uf021  " + tr.t("transfers.scanning")
              color: Qt.darker(monitor.fgColor, 1.4)
              font.pixelSize: Style.font.caption
            }

            // Active file progress
            Rectangle {
              visible: modelData.active_file !== null
              width: parent.width
              implicitHeight: fileCol.implicitHeight + Style.space(12)
              radius: Style.cornerRadius
              color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.05)
              border.width: 1
              border.color: Qt.rgba(monitor.fgColor.r, monitor.fgColor.g, monitor.fgColor.b, 0.12)

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
                    color: monitor.accentColor
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
                  // A file being written has no known final size: no bar.
                  visible: !(modelData.active_file && modelData.active_file.writing)
                  width: parent.width
                  height: Style.space(6)
                  fraction: modelData.active_file ? Math.min(1.0, Math.max(0.0, modelData.active_file.pct / 100.0)) : 0
                  fillColor: monitor.accentColor
                }

                PlainText {
                  width: parent.width
                  horizontalAlignment: Text.AlignRight
                  text: !modelData.active_file ? ""
                    : modelData.active_file.writing ? tr.t("transfers.written").arg(Model.formatBytes(modelData.active_file.size))
                    : Model.formatBytes(modelData.active_file.pos) + " / " + Model.formatBytes(modelData.active_file.size) + " (" + modelData.active_file.pct + "%)" + (modelData.file_eta ? " · " + tr.t("transfers.eta").arg(modelData.file_eta) : "")
                  color: Qt.darker(monitor.fgColor, 1.4)
                  font.pixelSize: Style.font.caption
                }
              }
            }

            // Stats row
            Row {
              width: parent.width
              spacing: Style.space(12)

              PlainText {
                text: tr.t("transfers.transferred").arg(Model.formatBytes(modelData.total_bytes))
                color: Qt.darker(monitor.fgColor, 1.3)
                font.pixelSize: Style.font.caption
              }

              PlainText {
                text: tr.t("transfers.elapsed").arg(modelData.elapsed_str || "")
                color: Qt.darker(monitor.fgColor, 1.3)
                font.pixelSize: Style.font.caption
              }

              PlainText {
                visible: modelData.dest_fs !== null
                text: modelData.dest_fs ? tr.t("transfers.destFree").arg(modelData.dest_fs.name).arg(Model.formatBytes(modelData.dest_fs.free_bytes)) : ""
                color: Qt.darker(monitor.fgColor, 1.3)
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
                  if (modelData.state === "paused") monitor.resumeTransfer(modelData.pid)
                  else monitor.pauseTransfer(modelData.pid)
                }
              }

              ActionBtn {
                text: tr.t("transfers.action.openDest")
                iconText: "\uf07b"
                visible: Boolean(modelData.destination)
                onClicked: monitor.openTransferPath(modelData.destination)
              }

              ActionBtn {
                text: tr.t("transfers.action.copyCmd")
                iconText: "\uf0c5"
                onClicked: monitor.copyTransferText(modelData.command)
              }

              ActionBtn {
                text: tr.t("transfers.action.cancel")
                iconText: "\uf00d"
                foreground: monitor.urgentColor
                onClicked: monitor.cancelTransfer(modelData.pid)
              }
            }
          }
        }
      }

      // Recently completed transfers
      Column {
        visible: monitor.recentTransferList.length > 0
        width: parent.width
        spacing: Style.space(6)

        PlainText {
          text: tr.t("transfers.recent")
          color: Qt.darker(monitor.fgColor, 1.4)
          font.bold: true
          font.pixelSize: Style.font.caption
        }

        Repeater {
          model: monitor.recentTransferList

          SurfaceCard {
            required property var modelData

            RowLayout {
              width: parent.width
              spacing: Style.space(8)

              PlainText {
                text: "\uf00c"
                color: monitor.accentColor
                font.pixelSize: Style.font.bodySmall
              }

              PlainText {
                text: Model.toolDisplayTitle(modelData.tool, monitor.t)
                font.bold: true
                font.pixelSize: Style.font.caption
              }

              PlainText {
                text: modelData.total_bytes ? Model.formatBytes(modelData.total_bytes) : ""
                color: Qt.darker(monitor.fgColor, 1.3)
                font.pixelSize: Style.font.caption
              }

              Item { Layout.fillWidth: true }

              PlainText {
                text: modelData.elapsed_str || ""
                color: Qt.darker(monitor.fgColor, 1.5)
                font.pixelSize: Style.font.caption
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
    color: view.monitor.fgColor
    font.family: view.monitor.bar ? view.monitor.bar.fontFamily : Style.font.family
    font.pixelSize: Style.font.body
    elide: Text.ElideRight
  }

  component TabButton: Button {
    fontSize: Style.font.caption
    iconSize: Style.font.bodySmall
    foreground: view.monitor.fgColor
    fontFamily: view.monitor.bar ? view.monitor.bar.fontFamily : Style.font.family
    horizontalPadding: Style.space(4)
    verticalPadding: Style.space(6)
    bordered: true
  }

  component ActionBtn: Button {
    fontSize: Style.font.caption
    iconSize: Style.font.bodySmall
    foreground: view.monitor.fgColor
    fontFamily: view.monitor.bar ? view.monitor.bar.fontFamily : Style.font.family
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
      ? Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.08)
      : Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.04)
    border.width: 1
    border.color: clickable && cardMouse.containsMouse
      ? Qt.rgba(view.monitor.accentColor.r, view.monitor.accentColor.g, view.monitor.accentColor.b, 0.5)
      : Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.12)

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
    color: Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.05)

    Column {
      id: pillCol
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.margins: Style.space(6)
      spacing: Style.space(2)

      PlainText {
        text: label
        color: Qt.darker(view.monitor.fgColor, 1.4)
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
    property color fillColor: view.monitor.fgColor

    Rectangle {
      id: track
      anchors.fill: parent
      radius: height / 2
      color: Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.12)
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
      color: Qt.darker(view.monitor.fgColor, 1.38)
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
    property color lineColor: view.monitor.accentColor

    onRevisionChanged: canvas.requestPaint()
    onWidthChanged: canvas.requestPaint()
    onHeightChanged: canvas.requestPaint()

    Rectangle {
      anchors.fill: parent
      radius: Style.cornerRadius > 0 ? Math.min(Style.space(4), Style.cornerRadius) : 0
      color: Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.05)
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
        ctx.strokeStyle = Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.08)
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
          var secColor = Qt.rgba(view.monitor.fgColor.r, view.monitor.fgColor.g, view.monitor.fgColor.b, 0.45)
          drawSeries(pts2, secColor, 0.08)
        }
        drawSeries(pts, graphRoot.lineColor, 0.22)
      }
    }
  }
}
