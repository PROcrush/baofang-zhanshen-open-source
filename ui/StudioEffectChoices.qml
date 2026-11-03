import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: effects
    property var studioModel: null
    property var stateData: ({})
    readonly property var settings: stateData.settings || ({})
    readonly property var rows: stateData.effects || []
    readonly property var current: rows.filter(function(row) { return row.id === (settings.effectId || "none"); })[0] || ({})
    Layout.fillWidth: true; spacing: 10
    Text {
        Layout.fillWidth: true; text: "当前：" + (effects.current.label || "无额外动画")
        color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap
    }
    GridLayout {
        Layout.fillWidth: true; columns: 2; columnSpacing: 6; rowSpacing: 6
        Repeater {
            model: effects.rows
            StudioButton {
                required property var modelData
                objectName: "simpleEffect-" + modelData.id
                Layout.fillWidth: true; Layout.minimumWidth: 0; implicitWidth: 100; implicitHeight: 36
                text: modelData.label
                hint: modelData.id === "none" ? "关闭额外文字动画，保留歌词走带开关" : modelData.description
                selected: (effects.settings.effectId || "none") === modelData.id
                enabled: !!effects.studioModel && !effects.stateData.busy
                onClicked: effects.studioModel.setSetting("effectId", modelData.id)
            }
        }
    }
    Text {
        Layout.fillWidth: true; text: "点选后播放查看效果，暂停时保持画面。歌词走带由独立开关控制。"
        color: Theme.muted; font.pixelSize: 11; wrapMode: Text.Wrap; lineHeight: 1.3
    }
}
