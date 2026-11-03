import QtQuick
import QtQuick.Controls

// 歌暮式栏内文字标签：选中白字 + 底部渐变短条
Button {
    id: textTab
    property bool selected: false
    implicitHeight: 40
    hoverEnabled: true; activeFocusOnTab: true
    Accessible.name: text
    contentItem: Text { text: textTab.text; color: textTab.selected || textTab.hovered ? Theme.text : Theme.secondary; font.pixelSize: 13; font.weight: textTab.selected ? Font.Medium : Font.Normal; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
    background: Rectangle {
        color: "transparent"; border.color: textTab.activeFocus ? Theme.focus : "transparent"; border.width: 2
        AccentBar { visible: textTab.selected; anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; width: Math.min(40, parent.width - 12); height: 2 }
    }
}
