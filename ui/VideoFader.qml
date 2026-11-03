import QtQuick
import QtQuick.Layouts

Item {
    id: meter
    property real value: -1
    property string sourceLabel: "视频推子"
    readonly property bool known: value >= 0 && value <= 1
    implicitHeight: 34
    Rectangle {
        x: 0
        y: 3
        width: parent.width
        height: 7
        radius: 3
        color: "#3e4855"
        Rectangle {
            x: parent.width * 0.48
            width: parent.width * 0.04
            height: parent.height
            color: "#5e7591"
        }
        Rectangle {
            visible: meter.known
            x: Math.max(0, Math.min(parent.width - width, parent.width * meter.value - width / 2))
            y: -3
            width: 5
            height: 13
            radius: 2
            color: Theme.accent
        }
    }
    RowLayout {
        anchors.left: parent.left
        anchors.right: parent.right
        y: 16
        Text { text: "A"; color: "#8995a5"; font.pixelSize: 9 }
        Item { Layout.fillWidth: true }
        Text { text: meter.known ? "48–52% 保持侧" : "未收到" + meter.sourceLabel; color: Theme.secondary; font.pixelSize: 9 }
        Item { Layout.fillWidth: true }
        Text { text: "B"; color: "#8995a5"; font.pixelSize: 9 }
    }
}
