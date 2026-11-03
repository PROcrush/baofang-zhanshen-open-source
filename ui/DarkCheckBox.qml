import QtQuick
import QtQuick.Controls

// 暗光复选框：勾选态紫色填充（同歌暮开关），焦点 2px 描边；沿用 CheckBox 的信号与属性。
CheckBox {
    id: control
    property color labelColor: Theme.text
    font.family: "PingFang SC"
    font.pixelSize: 13
    spacing: 9
    padding: 4
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: text
    indicator: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        implicitWidth: 18; implicitHeight: 18
        radius: 6
        color: !control.enabled ? Theme.disabledSurface : control.checked ? Theme.toggle : control.hovered ? Theme.washHover : Theme.wash
        border.color: control.activeFocus ? Theme.focus : control.checked ? Theme.toggle : Theme.hairlineStrong
        border.width: control.activeFocus ? 2 : 1
        Text {
            anchors.centerIn: parent
            visible: control.checked
            text: "✓"
            color: "#ffffff"
            font.pixelSize: 13
            font.weight: Font.Bold
        }
    }
    contentItem: Text {
        text: control.text
        font: control.font
        color: control.enabled ? control.labelColor : Theme.muted
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.Wrap
    }
}
