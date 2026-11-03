import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// 曲库左栏状态筛选：圆点 + 名称 + 数量，选中浅白底
Button {
    id: row
    property string tone: "muted"
    property int value: 0
    property string caption: ""
    property bool selected: false
    implicitHeight: 36
    hoverEnabled: true; activeFocusOnTab: true
    Accessible.name: text + " " + value + " 首"
    contentItem: RowLayout {
        spacing: 8
        Rectangle { width: 7; height: 7; radius: 4; color: Theme.toneInk(row.tone) }
        Text { Layout.fillWidth: true; text: row.text; color: row.selected ? Theme.text : Theme.secondary; font.pixelSize: 13; elide: Text.ElideRight }
        Text { visible: !!row.caption; text: row.caption; color: Theme.amber; font.pixelSize: 10 }
        Text { text: String(row.value); color: row.value > 0 ? Theme.text : Theme.muted; font.family: Theme.numberFont; font.pixelSize: 13 }
    }
    background: Rectangle { radius: Theme.radiusControl; color: row.selected ? Theme.hairlineStrong : row.hovered ? Theme.wash : "transparent"; border.color: row.activeFocus ? Theme.focus : "transparent"; border.width: 2 }
}
