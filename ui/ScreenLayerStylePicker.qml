import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

GridLayout {
    id: picker
    property var styles: []
    property bool logo: false
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
            implicitHeight: 64
            padding: 3
            hoverEnabled: true
            text: modelData.label
            checkable: true
            autoExclusive: true
            checked: picker.selectedStyle === modelData.key
            Accessible.name: text
            onClicked: picker.chosen(modelData)
            background: Rectangle {
                radius: 5
                color: tile.checked ? Theme.accentSoft : tile.hovered ? Theme.elevated : Theme.sunken
                border.color: tile.activeFocus ? Theme.focus : tile.checked ? Theme.accentLine : Theme.hairline
                border.width: tile.activeFocus ? 2 : 1
            }
            contentItem: ColumnLayout {
                spacing: 1
                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Item {
                        width: 310
                        height: 125
                        anchors.centerIn: parent
                        scale: Math.min(parent.width / width, parent.height / height)
                        ScreenLayers {
                            anchors.fill: parent
                            stateData: picker.logo
                                ? {screenLayers: {logo: {visible: true, style: tile.modelData.key, size: 48, position: "center"}},
                                   logoImageUrl: Qt.resolvedUrl("ScreenLayerSample.svg")}
                                : {screenLayers: {text: {visible: true, style: tile.modelData.key, text: "今晚见",
                                    fontSize: 148, color: tile.modelData.color, position: "center"}}}
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
