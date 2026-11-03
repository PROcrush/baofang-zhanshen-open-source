import QtQuick
import QtQuick.Controls

// 歌暮左栏的素材卡：圆形图标 + 名称 + 当前内容，右侧紫色动作字；整张卡可点。
// rail 为 true 时收成窄栏图标按钮（图标在上、名称在下），小窗口给预览让位。
Button {
    id: tile
    property string iconName: "audio"
    property string title: ""
    property string detail: ""
    property string actionText: text
    property string tone: "neutral"
    property bool rail: false
    property bool done: false
    hoverEnabled: true
    activeFocusOnTab: true
    Accessible.name: title + "，" + actionText
    Accessible.description: detail
    ToolTip.visible: rail && hovered
    ToolTip.text: actionText + (detail ? " · " + detail : "")
    ToolTip.delay: 400
    implicitWidth: rail ? 60 : 220
    implicitHeight: rail ? 62 : 64
    contentItem: Item {
        Rectangle {
            id: badge
            width: tile.rail ? 32 : 36; height: width; radius: width / 2
            x: tile.rail ? (parent.width - width) / 2 : 0
            y: tile.rail ? 2 : (parent.height - height) / 2
            color: tile.tone === "primary" ? Theme.accentSoft : Theme.elevated
            StudioIcon { anchors.centerIn: parent; width: parent.width * 0.5; height: width; name: tile.iconName; ink: !tile.enabled ? Theme.muted : tile.tone === "primary" || tile.hovered ? Theme.accent : Theme.secondary }
            Rectangle {
                visible: tile.done
                width: 12; height: 12; radius: 6
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: -1
                color: Theme.green; border.color: Theme.surface; border.width: 2
            }
        }
        Text {
            visible: tile.rail
            anchors.horizontalCenter: parent.horizontalCenter
            y: badge.y + badge.height + 5
            width: parent.width
            text: tile.title
            color: !tile.enabled ? Theme.muted : tile.tone === "primary" ? Theme.accent : Theme.secondary
            font.pixelSize: 11
            horizontalAlignment: Text.AlignHCenter
            elide: Text.ElideRight
        }
        Column {
            visible: !tile.rail
            x: badge.width + 12
            width: parent.width - x - action.width - 8
            anchors.verticalCenter: parent.verticalCenter
            spacing: 3
            Text { width: parent.width; text: tile.title; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; elide: Text.ElideRight }
            Text { width: parent.width; text: tile.detail; textFormat: Text.PlainText; color: Theme.muted; font.pixelSize: 11; elide: Text.ElideMiddle }
        }
        Text {
            id: action
            visible: !tile.rail
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            text: tile.actionText
            color: !tile.enabled ? Theme.muted : tile.hovered ? Theme.accentHover : Theme.accent
            font.pixelSize: 12; font.weight: Font.Medium
        }
    }
    padding: rail ? 4 : 12
    background: Rectangle {
        radius: Theme.radiusCard - 2
        color: !tile.enabled ? "transparent" : tile.down ? Theme.accentSoft : tile.hovered ? Theme.hoverSurface : tile.tone === "primary" ? Theme.cardTint : tile.rail ? "transparent" : Theme.canvas
        border.color: tile.activeFocus ? Theme.focus : tile.tone === "primary" && !tile.rail ? Theme.cardTintLine : "transparent"
        border.width: tile.activeFocus ? 2 : 1
        Behavior on color { enabled: !Theme.reduceMotion; ColorAnimation { duration: 140; easing.type: Easing.OutQuart } }
    }
}
