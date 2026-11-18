import QtQuick

Rectangle {
    property bool accentRing: false
    property color fill: Theme.wash
    property color line: Theme.hairline
    property bool focusRing: false
    property real cornerRadius: Theme.radiusControl

    color: fill
    radius: cornerRadius
    border.width: focusRing ? 2 : 1
    border.color: focusRing ? Theme.focus : accentRing ? Theme.accentLine : line
    Behavior on color {
        enabled: !Theme.reduceMotion
        ColorAnimation { duration: 120 }
    }
}
