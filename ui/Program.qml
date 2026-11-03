import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: outputWindow
    width: 1120
    height: 720
    minimumWidth: 640
    minimumHeight: 420
    visible: true
    color: "#090d13"
    title: "包房战神 · 客屏输出"
    property var appState: controller ? controller.state : ({})
    onClosing: controller.closeOutput()

    ProgramView {
        anchors.fill: parent
        stateData: outputWindow.appState
    }
    MouseArea {
        id: toolbarMouse
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 65
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
    }
    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: 55
        color: "#b90d1118"
        visible: toolbarMouse.containsMouse || maximizeButton.hovered || closeButton.hovered
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 20
            anchors.rightMargin: 20
            Text { text: "Esc 关闭客屏"; color: "#aeb6c2"; font.pixelSize: 12 }
            Item { Layout.fillWidth: true }
            ActionButton {
                id: maximizeButton
                visible: outputWindow.visibility !== Window.FullScreen
                text: outputWindow.visibility === Window.Maximized ? "恢复窗口" : "最大化"
                implicitHeight: 32
                onClicked: outputWindow.visibility === Window.Maximized ? outputWindow.showNormal() : outputWindow.showMaximized()
            }
            ActionButton {
                id: closeButton
                text: "关闭客屏"
                implicitHeight: 32
                onClicked: outputWindow.close()
            }
        }
    }
    Shortcut { sequence: "Esc"; onActivated: outputWindow.close() }
}
