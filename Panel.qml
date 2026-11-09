import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Commons as Commons
import qs.Ui
import "Model.js" as Model

// Bar entry point: the bar button, its popup and IPC. Data and actions live in
// Monitor.qml, the popup's content in ResourcesView.qml.
Panel {
  id: root
  moduleName: "gusthx.resources"
  ipcTarget: "gusthx.resources"
  manageIpc: false

  Commons.Translations {
    id: tr
    directory: Qt.resolvedUrl("i18n")
  }

  Monitor {
    id: mon
    bar: root.bar
    opened: root.opened
    barMode: root.barMode
    onCloseRequested: root.close()
    onCycleModeRequested: root.cycleBarMode()
  }

  // ---- Inline bar settings (persisted in shell.json) ----
  readonly property var barModes: ["compact", "detailed", "full", "icon"]
  readonly property string barMode: {
    var m = String(setting("barMode", "compact"))
    return barModes.indexOf(m) >= 0 ? m : "compact"
  }

  readonly property string barDisplayText: {
    if (button.vertical || barMode === "icon") {
      return "󰍛"
    }
    var fmt = mon.fmtNum
    var cStr = fmt(Math.round(mon.cpuPct)) + "%"
    var mStr = fmt(Math.round(mon.memPct)) + "%"
    if (barMode === "detailed") {
      var tPart = mon.effectiveTempC > 0 ? (" · " + fmt(mon.effectiveTempC) + "°C") : ""
      var mShort = Model.formatShortBytes(mon.memUsedBytes, fmt)
      return "󰍛 " + cStr + tPart + "  󰘚 " + mShort
    }
    if (barMode === "full") {
      var nRate = Model.formatShortRate(mon.netRxRateBps + mon.netTxRateBps, fmt)
      return "󰍛 " + cStr + "  󰘚 " + mStr + "  󰇚 " + nRate
    }
    return "󰍛 " + cStr + "  󰘚 " + mStr
  }

  readonly property string barTooltip: {
    var fmt = mon.fmtNum
    var cStr = fmt(Math.round(mon.cpuPct))
    var mStr = fmt(Math.round(mon.memPct))
    var mBytes = Model.formatBytes(mon.memUsedBytes, 1, fmt) + " / " + Model.formatBytes(mon.memTotalBytes, 0, fmt)
    var mName = mon.modeDisplayName(barMode)
    return Model.neutralizeMarkup(
      tr.t("bar.tooltip").arg(cStr).arg(mStr).arg(mBytes).arg(mName),
      260
    )
  }

  // Read by the bar (Bar.qml) to size the "panel open" underline.
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

  function resetTabScroll() {
    var c = panel.loadedContent
    if (c && typeof c.resetScroll === "function") {
      c.resetScroll()
    }
  }

  onOpenedChanged: {
    if (opened) {
      mon.refreshAll()
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
    active: mon.urgentState
    tooltipText: root.barTooltip
    onPressed: function(b) {
      if (b === Qt.RightButton) root.cycleBarMode()
      else if (b === Qt.MiddleButton) mon.openBtop()
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

    content: ResourcesView {
      anchors.fill: parent
      monitor: mon
    }
  }
}
