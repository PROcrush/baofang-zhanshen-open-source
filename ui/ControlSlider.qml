import QtQuick
import QtQuick.Controls

Slider {
    id: control
    implicitHeight: 32
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: control.availableWidth
        height: 4
        radius: 2
        color: "#444e5c"
        Rectangle { width: control.visualPosition * parent.width; height: parent.height; radius: 2; color: control.enabled ? Theme.accent : Theme.muted }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + control.availableHeight / 2 - height / 2
        width: 18; height: 18; radius: 9
        color: control.enabled ? Theme.text : Theme.muted
        border.width: control.activeFocus ? 3 : 1
        border.color: control.activeFocus ? Theme.focus : "#7a889b"
    }
}
