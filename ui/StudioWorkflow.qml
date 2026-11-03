import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

RowLayout {
    id: workflow
    property var stateData: ({})
    property bool hasPicture: false
    property int currentStep: activeStep
    signal requested(int step)
    readonly property int activeStep: !stateData.audioPath ? 0 : !(stateData.segments || []).length ? 1 : 3
    spacing: 0
    Repeater {
        model: [{title:"选择歌曲",detail:"导入实际演出版本"},{title:"添加歌词",detail:"导入字幕或识别"},{title:"设置画面（可选）",detail:"默认画面可直接使用"},{title:"预览与导出",detail:"先试听，再导出到文件"}]
        delegate: Button {
            id: stepButton
            required property var modelData
            required property int index
            objectName: "studioStep-"+index
            Layout.fillWidth: true; Layout.preferredHeight: 54
            hoverEnabled: true; activeFocusOnTab: true
            enabled: !workflow.stateData.busy && (index===0 || !!workflow.stateData.audioPath)
            Accessible.name: modelData.title+"，"+modelData.detail
            Accessible.description: workflow.currentStep===index ? "当前步骤" : "前往"+modelData.title
            onClicked: workflow.requested(index)
            background: Rectangle { radius:7; color: stepButton.hovered ? Theme.elevated : "transparent"; border.width:stepButton.activeFocus ? 2 : 0; border.color:Theme.accent }
            contentItem: RowLayout {
                spacing: 9
                Rectangle {
                    Layout.preferredWidth: 26; Layout.preferredHeight: 26; radius: 13
                    readonly property bool complete: stepButton.index===0 ? !!workflow.stateData.audioPath : stepButton.index===1 ? (workflow.stateData.segments || []).length>0 : false
                    color: workflow.currentStep===stepButton.index ? Theme.accent : complete ? Theme.accentSoft : Theme.elevated
                    Text { anchors.centerIn: parent; text: parent.complete && workflow.currentStep!==stepButton.index ? "✓" : stepButton.index+1; color: workflow.currentStep===stepButton.index ? Theme.accentText : parent.complete ? Theme.accent : Theme.muted; font.pixelSize: 12; font.weight: Font.Medium }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 3
                    Text { Layout.fillWidth: true; text: stepButton.modelData.title; color: stepButton.enabled ? Theme.text : Theme.muted; font.pixelSize: 12; font.weight: workflow.currentStep===stepButton.index ? Font.Medium : Font.Normal; elide: Text.ElideRight }
                    Text { Layout.fillWidth: true; text: stepButton.modelData.detail; color: Theme.muted; font.pixelSize: 10; elide: Text.ElideRight }
                }
            }
        }
    }
}
