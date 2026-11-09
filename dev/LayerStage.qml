import QtQuick
import Quickshell
import Quickshell.Wayland

// The capture surface when HEADLESS=0: a background-layer surface, below
// every window, never tiled, no focus.
PanelWindow {
  id: win
  property Component content: null
  WlrLayershell.layer: WlrLayer.Background
  WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
  WlrLayershell.namespace: "gusthx-resources-dev"
  exclusionMode: ExclusionMode.Ignore
  anchors { left: true; bottom: true }
  implicitWidth: layerLoader.item ? layerLoader.item.width : 10
  implicitHeight: layerLoader.item ? layerLoader.item.height : 10
  color: "transparent"
  Loader { id: layerLoader; sourceComponent: win.content }
}
