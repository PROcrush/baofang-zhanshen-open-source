import QtQuick

Rectangle {
    id: pill
    property string text: ""
    property string tone: "muted"
    property bool showDot: tone !== "muted"
    property color ink: Theme.toneInk(tone)
    Accessible.role: Accessible.StaticText
    Accessible.name: text
    implicitWidth: caption.implicitWidth + (showDot ? 30 : 18)
    implicitHeight: 24
    radius: height / 2
    color: Theme.wash
    border.color: Theme.hairline
    border.width: 1
    Rectangle {
        visible: pill.showDot
        width: 6; height: 6; radius: 3
        x: 10; anchors.verticalCenter: parent.verticalCenter
        color: pill.ink
    }
    Text {
        id: caption
        anchors.verticalCenter: parent.verticalCenter
        x: pill.showDot ? 21 : (pill.width - width) / 2
        text: pill.text
        color: pill.ink
        font.family: "PingFang SC"
        font.pixelSize: 11
        font.weight: Font.Normal
    }
}
