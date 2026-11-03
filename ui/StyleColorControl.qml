import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: control
    property string label: ""
    property string styleKey: "baseColor"
    property string colorValue: "#FFFFFF"
    readonly property var swatches: ["#FFFFFF", "#FFDC64", "#6AE4FF", "#FF9CCB", "#A6EDB4", "#000000"]
    spacing: 10
    Text { text: control.label; color: Theme.secondary; font.pixelSize: 12; Layout.preferredWidth: 62 }
    Rectangle { width: 24; height: 24; radius: 6; color: control.colorValue; border.color: "#77808B" }
    TextField {
        id: hexInput
        objectName: "style-" + control.styleKey + "Input"
        Layout.preferredWidth: 97
        implicitHeight: 34
        text: control.colorValue
        maximumLength: 7
        color: Theme.text
        font.family: Theme.numberFont
        font.pixelSize: 11
        selectByMouse: true
        Accessible.name: control.label + "十六进制颜色"
        background: Rectangle { color: Theme.elevated; radius: 7; border.color: hexInput.activeFocus ? Theme.focus : Theme.stroke }
        onEditingFinished: {
            if (text !== control.colorValue) controller.setStyle(control.styleKey, text.toUpperCase())
            text = Qt.binding(function() { return control.colorValue })
        }
    }
    Repeater {
        model: control.swatches
        delegate: Button {
            required property string modelData
            objectName: "style-" + control.styleKey + "-" + modelData
            Layout.preferredWidth: 25
            Layout.preferredHeight: 25
            Accessible.name: control.label + " " + modelData
            background: Rectangle {
                color: parent.modelData
                radius: 6
                border.width: control.colorValue.toUpperCase() === parent.modelData ? 2 : 1
                border.color: control.colorValue.toUpperCase() === parent.modelData ? Theme.accent : "#77808B"
            }
            onClicked: controller.setStyle(control.styleKey, modelData)
        }
    }
    Item { Layout.fillWidth: true }
}
