import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    property string title
    property string detail
    property int inset: 12
    property color surface: "#16171e"
    default property alias controls: contents.data
    Layout.fillWidth: true
    implicitHeight: contents.implicitHeight + inset * 2
    radius: 12
    color: surface
    border.color: "#3b3c47"
    ColumnLayout {
        id: contents
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: card.inset; spacing: 7
        RowLayout {
            visible: card.title.length > 0
            Layout.fillWidth: true
            Text { text: card.title; Layout.fillWidth: true; color: Theme.text; font.pixelSize: 15; font.weight: Font.DemiBold }
            Text { text: card.detail; color: Theme.muted; font.pixelSize: 11 }
        }
    }
}
