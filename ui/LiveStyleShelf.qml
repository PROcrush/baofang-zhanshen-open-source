import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "PresetPreview.js" as PresetPreview

Rectangle {
    id: shelf
    required property var settings
    required property var presets
    signal presetRequested(string presetId)
    signal detailsRequested()
    property bool expanded: true
    objectName: "liveStyleShelf"
    color: Theme.surface
    implicitHeight: expanded ? Math.max(130, Math.min(204, width * .19)) : 36
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 5; spacing: 3
        RowLayout {
            Layout.fillWidth: true
            Text { text: "字幕风格"; color: Theme.text; font.pixelSize: 13; font.bold: true; Layout.leftMargin: 6 }
            Text { text: shelf.presets.length+" 组"; color: Theme.muted; font.pixelSize: 11 }
            Item { Layout.fillWidth: true }
            StudioButton { text: "字幕特效"; implicitHeight: 25; onClicked: shelf.detailsRequested() }
            StudioButton { objectName: "liveShelfToggle"; text: shelf.expanded ? "收起" : "展开"; implicitHeight: 25; onClicked: shelf.expanded=!shelf.expanded }
        }
        GridView {
            id: grid
            visible: shelf.expanded; Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; reuseItems: true; model: shelf.visible && shelf.expanded ? shelf.presets : []
            cellWidth: width / Math.max(4,Math.floor(width/134))
            cellHeight: 81
            ScrollBar.vertical: ScrollBar {}
            delegate: Button {
                id: tile
                required property var modelData
                objectName: "liveShelfPreset-"+modelData.id
                text: modelData.name; Accessible.name: "应用现场风格 "+text
                width: grid.cellWidth-4; height: grid.cellHeight-4; padding: 3
                hoverEnabled: true; activeFocusOnTab: true
                readonly property bool selected: shelf.settings.presetId===modelData.id
                onClicked: shelf.presetRequested(modelData.id)
                background: Rectangle { radius: 5; color: Theme.sunken; border.width: tile.selected || tile.activeFocus ? 2 : 1; border.color: tile.activeFocus ? Theme.focus : tile.selected ? Theme.accent : tile.hovered ? Theme.strokeHover : Theme.stroke }
                contentItem: ColumnLayout {
                    spacing: 2
                    ProgramView {
                        objectName: "liveStyleThumbnail-"+tile.modelData.id
                        Layout.fillWidth: true; Layout.fillHeight: true
                        compact: true; presetThumbnail: true; radius: 2
                        stateData: PresetPreview.cardState(tile.modelData, true)
                    }
                    Text { text: (tile.selected ? "✓  " : "")+tile.text; color: tile.selected ? Theme.accent : Theme.secondary; font.pixelSize: 10; Layout.leftMargin: 3 }
                }
            }
        }
    }
}
