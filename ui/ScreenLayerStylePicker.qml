import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: picker
    property var styles: []
    property bool logo: false
    property url logoImage: ""
    property string selectedStyle: ""
    signal chosen(var style)
    columns: logo ? 3 : 2
    columnSpacing: 6
    rowSpacing: 6

    Repeater {
        model: picker.styles
        delegate: Button {
            id: tile
            required property var modelData
            objectName: "screenStyle-" + modelData.key
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: picker.logo ? 62 : 82
            padding: 3
            hoverEnabled: true
            text: modelData.label
            checkable: true
            autoExclusive: true
            checked: picker.selectedStyle === modelData.key
            Accessible.name: text
            Accessible.description: modelData.description || ""
            ToolTip.text: modelData.description || ""
            ToolTip.visible: hovered && ToolTip.text.length > 0
            ToolTip.delay: 450
            onClicked: picker.chosen(modelData)
            background: Rectangle {
                radius: 5
                color: tile.hovered ? Theme.elevated : Theme.sunken
                border.color: tile.activeFocus ? Theme.focus : tile.checked ? Theme.accentLine : Theme.hairline
                border.width: tile.activeFocus ? 2 : 1
            }
            contentItem: ColumnLayout {
                spacing: 1
                Rectangle {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    color: "#08090d"
                    clip: true
                    Item {
                        width: 480
                        height: 270
                        anchors.centerIn: parent
                        scale: Math.min(parent.width / width, parent.height / height)
                        Rectangle { x: 30; y: 191; width: 178; height: 6; radius: 3; color: "#50545e" }
                        Rectangle { x: 235; y: 214; width: 215; height: 6; radius: 3; color: "#777b85" }
                        ScreenLayers {
                            anchors.fill: parent
                            stateData: picker.logo
                                ? {screenLayers: {logo: {visible: true, style: tile.modelData.key, size: 30, position: "top-left"}},
                                   logoImageUrl: picker.logoImage.toString() || Qt.resolvedUrl("ScreenLayerSample.svg")}
                                : {screenLayers: {text: {visible: true, style: tile.modelData.key, text: "今晚开场",
                                    fontSize: tile.modelData.fontSize, color: tile.modelData.color, position: tile.modelData.position}}}
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    text: tile.text
                    color: tile.checked ? Theme.text : Theme.secondary
                    font.pixelSize: 11
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                }
            }
        }
    }
}
