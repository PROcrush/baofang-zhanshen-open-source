import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property string iconName: ""
    property bool iconOnly: false
    property bool selected: false
    property string tone: "neutral"
    property string hint: ""
    property int textAlignment: Text.AlignHCenter
    implicitHeight: 34
    implicitWidth: iconOnly ? 34 : Math.max(60, Math.ceil(label.implicitWidth) + (iconName.length > 0 ? 23 : 0) + 2 * padding + 2)
    padding: iconOnly ? 8 : 13
    leftPadding: padding
    rightPadding: padding
    topPadding: 5
    bottomPadding: 5
    font.family: "PingFang SC"
    font.pixelSize: 13
    font.weight: tone === "primary" ? Font.Medium : Font.Normal
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: text
    Accessible.description: hint
    ToolTip.visible: hovered && (iconOnly || hint.length > 0)
    ToolTip.text: hint || text
    contentItem: Item {
        AppIcon {
            id: navigationIcon
            visible: ["stage", "library", "prepare", "style", "diagnostics", "studio"].indexOf(control.iconName)>=0
            width: 17; height: 17
            anchors.verticalCenter: parent.verticalCenter
            ink: !control.enabled ? Theme.muted : (control.tone === "primary" || control.selected) ? Theme.accent : Theme.secondary
            name: control.iconName
        }
        StudioIcon {
            id: buttonIcon
            visible: control.iconName.length > 0 && !navigationIcon.visible
            width: 17; height: 17
            x: control.iconOnly ? (parent.width-width)/2 : 0
            anchors.verticalCenter: parent.verticalCenter
            ink: !control.enabled ? Theme.muted : (control.tone === "primary" || control.selected) ? Theme.accent : Theme.secondary
            name: control.iconName
        }
        Text {
            id: label
            visible: !control.iconOnly
            x: buttonIcon.visible || navigationIcon.visible ? 23 : 0
            width: Math.max(0, parent.width-x); height: parent.height
            text: control.text; font: control.font
            color: !control.enabled ? Theme.muted : (control.tone === "primary" || control.selected) ? Theme.accentText : Theme.text
            horizontalAlignment: control.textAlignment; verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }
    background: PillFrame {
        accentRing: control.enabled && control.tone === "primary"
        focusRing: control.activeFocus
        fill: !control.enabled ? "transparent" : control.tone === "primary" ? (control.hovered ? Theme.accentSoftHover : Theme.accentSoft) : control.selected ? Theme.hairlineStrong : control.down ? Theme.pressedSurface : control.hovered ? Theme.washHover : "transparent"
        line: control.hovered && control.enabled ? Theme.hairline : "transparent"
    }
}
