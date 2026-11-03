import QtQuick
import QtQuick.Controls

ComboBox {
    id: control
    implicitWidth: 180
    implicitHeight: 34
    font.family: "PingFang SC"
    font.pixelSize: 12
    leftPadding: 12
    rightPadding: 30
    hoverEnabled: true
    Accessible.name: displayText
    contentItem: Text {
        text: control.displayText
        font: control.font
        color: control.enabled ? Theme.text : Theme.muted
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        x: control.width - width - 12
        anchors.verticalCenter: parent.verticalCenter
        text: "⌄"
        font.pixelSize: 14
        color: control.enabled ? Theme.secondary : Theme.muted
    }
    background: Rectangle {
        radius: Theme.radiusControl
        color: !control.enabled ? Theme.disabledSurface : control.pressed ? Theme.pressedSurface : control.hovered ? Theme.hoverSurface : Theme.elevated
        border.color: control.activeFocus ? Theme.focus : control.hovered ? Theme.strokeHover : Theme.strokeStrong
        border.width: control.activeFocus ? 2 : 1
    }
    delegate: ItemDelegate {
        required property var modelData
        required property int index
        width: control.width - 8
        text: control.textRole && modelData && modelData[control.textRole] !== undefined ? String(modelData[control.textRole]) : String(modelData)
        font: control.font
        highlighted: control.highlightedIndex === index
        contentItem: Text { text: parent.text; font: control.font; color: control.currentIndex === index ? Theme.accent : Theme.text; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        background: Rectangle { color: parent.highlighted ? Theme.hoverSurface : "transparent"; radius: 6 }
    }
    popup: Popup {
        y: control.height + 4
        width: control.width
        padding: 4
        implicitHeight: contentItem.implicitHeight + 8
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            ScrollIndicator.vertical: ScrollIndicator {}
        }
        background: Rectangle { color: Theme.elevated; border.color: Theme.strokeStrong; radius: Theme.radiusCard }
    }
}
