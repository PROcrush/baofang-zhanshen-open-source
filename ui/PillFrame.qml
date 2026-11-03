import QtQuick

// 胶囊底：主操作 / 选中用 1px 渐变描边，普通用极细白边。
Item {
    id: frame
    property bool accentRing: false
    property color fill: Theme.wash
    property color line: Theme.hairline
    property bool focusRing: false
    property real cornerRadius: height / 2
    Rectangle {
        anchors.fill: parent; radius: frame.cornerRadius
        visible: frame.accentRing && !frame.focusRing
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: Theme.gradientStart }
            GradientStop { position: 1; color: Theme.gradientEnd }
        }
    }
    Rectangle {
        anchors.fill: parent
        anchors.margins: frame.accentRing && !frame.focusRing ? 1 : 0
        radius: Math.max(0, frame.cornerRadius - anchors.margins)
        color: frame.fill
        border.color: frame.focusRing ? Theme.focus : frame.accentRing ? "transparent" : frame.line
        border.width: frame.focusRing ? 2 : frame.accentRing ? 0 : 1
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 150; easing.type: Easing.OutQuart } }
    }
}
