import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: gallery
    property var studioModel: null
    property var stateData: ({})
    property string category: "推荐"
    property string controlPrefix: ""
    property bool largePreviews: false
    readonly property var catalog: stateData.templates || []
    readonly property var rows: catalog.filter(function(row) { return category==="全部" || (category==="推荐" ? !!row.recommended : row.category===category); })
    readonly property string selectedId: (stateData.settings || {}).templateId || "none"
    Layout.fillWidth: true; spacing: 10
    RowLayout {
        Layout.fillWidth: true; spacing: 2
        Repeater {
            model: ["推荐","跟唱","清爽","设计","全部"]
            delegate: StudioButton {
                required property string modelData
                objectName: gallery.controlPrefix+"templateCategory-"+modelData
                Layout.fillWidth: true; implicitWidth: 42; implicitHeight: 30
                text: modelData; font.pixelSize: 11; padding: 4
                selected: gallery.category===modelData
                onClicked: gallery.category=modelData
            }
        }
    }
    Text { text: gallery.rows.length+" 款 · 示例效果，点击应用"; color: Theme.muted; font.pixelSize: 11 }
    GridLayout {
        Layout.fillWidth: true; columns: gallery.largePreviews ? 1 : 2; columnSpacing: 8; rowSpacing: 8
        Repeater {
            model: gallery.rows
            delegate: Button {
                id: tile
                required property var modelData
                objectName: gallery.controlPrefix+"templateTile-"+modelData.id
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredHeight: gallery.largePreviews ? 184 : 108
                padding: 3; hoverEnabled: true; activeFocusOnTab: true
                enabled: !!gallery.studioModel && !gallery.stateData.busy
                Accessible.name: "应用模板："+modelData.label
                Accessible.description: modelData.description
                onClicked: gallery.studioModel.applyTemplate(modelData.id)
                ToolTip.visible: hovered; ToolTip.text: modelData.description || modelData.label
                background: Rectangle { radius: 7; color: Theme.canvas; border.width: gallery.selectedId===tile.modelData.id || tile.activeFocus ? 2 : 1; border.color: gallery.selectedId===tile.modelData.id || tile.activeFocus ? Theme.focus : tile.hovered ? Theme.secondary : Theme.stroke }
                contentItem: ColumnLayout {
                    spacing: 2
                    Image { Layout.fillWidth: true; Layout.fillHeight: true; source: "image://studioPreview/template-thumb/"+tile.modelData.id; sourceSize: Qt.size(640,360); fillMode: Image.PreserveAspectFit }
                    Text { Layout.fillWidth: true; Layout.preferredHeight: 26; text: (gallery.selectedId===tile.modelData.id ? "✓ " : "")+tile.modelData.label; color: gallery.selectedId===tile.modelData.id ? Theme.accent : Theme.text; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                }
            }
        }
    }
}
