import QtQuick
import QtQuick.Controls

// 歌暮式导入卡片：整张卡可点，圆形图标 + 粗标题 + 一行灰色提示。
// 也用作空状态（clickable: false）。
Button {
    id: card
    property string iconName: "audio"
    property string title: text
    property string hint: ""
    property string note: ""
    property bool clickable: true
    property bool compact: false
    enabled: clickable
    hoverEnabled: clickable
    activeFocusOnTab: clickable
    focusPolicy: clickable ? Qt.StrongFocus : Qt.NoFocus
    Accessible.role: clickable ? Accessible.Button : Accessible.StaticText
    Accessible.name: title
    Accessible.description: hint
    implicitWidth: 360
    implicitHeight: body.implicitHeight + (compact ? 32 : 48)
    contentItem: Item {
        Column {
            id: body
            anchors.centerIn: parent
            width: parent.width - 24
            spacing: card.compact ? 6 : 8
            PillFrame {
                anchors.horizontalCenter: parent.horizontalCenter
                width: card.compact ? 44 : 56; height: width
                accentRing: true; fill: Theme.accentSoft
                StudioIcon { anchors.centerIn: parent; width: parent.width * 0.48; height: width; name: card.iconName; ink: Theme.accent }
            }
            Item { width: 1; height: card.compact ? 2 : 4 }
            Text { width: parent.width; text: card.title; color: Theme.text; font.pixelSize: card.compact ? 16 : 20; font.weight: Font.Medium; font.letterSpacing: Theme.titleSpacing; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
            Text { visible: !!card.hint; width: parent.width; text: card.hint; color: Theme.secondary; font.pixelSize: 12; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
            Text { visible: !!card.note; width: parent.width; text: card.note; color: Theme.muted; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap }
        }
    }
    background: Rectangle {
        radius: Theme.radiusSection
        color: !card.clickable ? "transparent" : card.down ? Theme.accentSoft : card.hovered ? Theme.washHover : Theme.glass
        border.color: card.activeFocus ? Theme.focus : !card.clickable ? "transparent" : card.hovered ? Theme.hairlineStrong : Theme.hairline
        border.width: card.activeFocus ? 2 : 1
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 150; easing.type: Easing.OutQuart } }
    }
}
