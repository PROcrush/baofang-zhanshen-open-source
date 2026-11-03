import QtQuick
import QtQuick.Window

Window {
    id: overlay
    objectName: "seratoLyricOverlay"
    width: 1120
    height: 720
    visible: false
    color: "transparent"
    title: "包房战神 · Serato 字幕叠加"
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    property var appState: controller ? controller.state : ({})
    ProgramView {
        anchors.fill: parent
        stateData: overlay.appState
        transparentStage: true
    }
}
