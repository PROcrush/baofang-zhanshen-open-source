import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: picker
    property string label: ""
    property string controlName: ""
    property var rows: []
    property string selectedId: ""
    property var selection: rows.filter(function(row) { return row.id === selectedId; })[0] || ({})
    signal chosen(string id)
    spacing: 5
    Text { text: picker.label; color: Theme.secondary; font.pixelSize: 10 }
    StudioButton {
        id: selectionButton
        objectName: picker.controlName
        Layout.fillWidth: true; implicitHeight: 32
        text: picker.selection.label || "选择"; selected: false
        background: Rectangle { radius: 9; color: Theme.elevated; border.color: selectionButton.activeFocus ? Theme.focus : "#39424d"; border.width: selectionButton.activeFocus ? 2 : 1 }
        onClicked: { search.text = ""; catalogPopup.open(); search.forceActiveFocus(); }
    }
    Popup {
        id: catalogPopup
        x: 0; y: selectionButton.y + selectionButton.height + 5
        width: Math.max(215,picker.width); height: Math.min(320, 74+picker.rows.length*42)
        padding: 8
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { radius: 12; color: Theme.elevated; border.color: "#4a5564" }
        contentItem: ColumnLayout {
            spacing: 6
            TextField {
                id: search
                objectName: picker.controlName + "Search"
                Layout.fillWidth: true; implicitHeight: 31
                color: Theme.text; placeholderText: "搜索名称或参数"; placeholderTextColor: Theme.muted
                selectByMouse: true; font.family: "PingFang SC"; font.pixelSize: 11
                background: Rectangle { radius: 8; color: Theme.canvas; border.color: search.activeFocus ? Theme.focus : "#3b4552" }
            }
            ListView {
                id: choices
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 2
                model: picker.rows.filter(function(row) { return !search.text || (String(row.label)+" "+String(row.description || "")+" "+String(row.id)+" "+JSON.stringify(row.parameters || {})).toLowerCase().indexOf(search.text.toLowerCase())>=0; })
                ScrollBar.vertical: ScrollBar { width: 4 }
                delegate: Button {
                    id: choice
                    required property var modelData
                    width: choices.width; height: 43
                    objectName: picker.controlName + "Option-" + modelData.id
                    text: modelData.label
                    hoverEnabled: true; activeFocusOnTab: true
                    leftPadding: 8; rightPadding: 8
                    Accessible.name: modelData.label
                    onClicked: { picker.chosen(modelData.id); catalogPopup.close(); }
                    background: Rectangle { radius: 7; color: choice.modelData.id===picker.selectedId ? Theme.accentSoft : choice.hovered ? "#35404f" : "transparent"; border.color: choice.activeFocus ? Theme.focus : "transparent" }
                    contentItem: Column {
                        spacing: 3
                        Text { width: parent.width; text: choice.modelData.label; color: choice.modelData.id===picker.selectedId ? Theme.accent : Theme.text; font.pixelSize: 11; elide: Text.ElideRight }
                        Text { width: parent.width; text: choice.modelData.description || choice.modelData.id; color: Theme.muted; font.pixelSize: 9; elide: Text.ElideRight }
                    }
                }
                Text { anchors.centerIn: parent; visible: choices.count===0; text: "没有匹配项"; color: Theme.muted; font.pixelSize: 11 }
            }
        }
    }
}
