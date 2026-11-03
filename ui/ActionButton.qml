import QtQuick
import QtQuick.Controls

Button {
    id: control
    property string tone: "neutral"
    property bool selected: false
    property string hint: ""
    implicitHeight: 36
    implicitWidth: Math.max(72, label.implicitWidth + 2 * padding)
    hoverEnabled: true
    Accessible.name: text
    Accessible.description: hint
    ToolTip.visible: hovered && hint.length > 0
    ToolTip.text: hint
    activeFocusOnTab: true
    font.family: "PingFang SC"
    font.pixelSize: 13
    font.weight: tone === "primary" ? Font.Medium : Font.Normal
    padding: 16
    leftPadding: padding
    rightPadding: padding
    topPadding: 8
    bottomPadding: 8
    contentItem: Text {
        id: label
        text: control.text
        color: !control.enabled ? Theme.muted : control.tone === "primary" ? Theme.accentText : control.tone === "danger" ? Theme.red : control.selected ? Theme.accentText : Theme.text
        font: control.font
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    background: PillFrame {
        accentRing: control.enabled && control.tone === "primary"
        focusRing: control.activeFocus
        fill: !control.enabled ? Theme.disabledSurface : control.tone === "primary" ? (control.down || control.hovered ? Theme.accentSoftHover : Theme.accentSoft) : control.selected ? Theme.hairlineStrong : control.tone === "danger" ? (control.down ? Theme.pressedSurface : control.hovered ? Theme.redSoft : Theme.wash) : control.down ? Theme.pressedSurface : control.hovered ? Theme.washHover : Theme.wash
        line: control.tone === "danger" && control.hovered ? Theme.redLine : control.hovered ? Theme.hairlineStrong : Theme.hairline
    }
}
