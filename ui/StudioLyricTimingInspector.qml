import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ScrollView {
    id: timing
    property var studioModel: null
    property var stateData: ({})
    property int selectedSegment: 0
    readonly property var line: (stateData.segments || [])[selectedSegment] || ({})
    readonly property bool editable: !!studioModel && !stateData.busy && !!line.text
    contentWidth: availableWidth; clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: timing.availableWidth; spacing: 10
        Text { text: "句级时码"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            StudioValueField { id: lineStart; Layout.fillWidth: true; label: "开始"; unit: "秒"; editContext: String(timing.selectedSegment); fieldName: "lyricTimingStart"; value: Number(timing.line.startMs || 0)/1000; maximum: Number(timing.stateData.durationMs || 0)/1000; enabled: timing.editable }
            StudioValueField { id: lineEnd; Layout.fillWidth: true; label: "结束"; unit: "秒"; editContext: String(timing.selectedSegment); fieldName: "lyricTimingEnd"; value: Number(timing.line.endMs || 0)/1000; maximum: Number(timing.stateData.durationMs || 0)/1000; enabled: timing.editable }
        }
        StudioButton { objectName: "lyricTimingApply"; Layout.fillWidth: true; text: "应用时码"; enabled: timing.editable; onClicked: timing.studioModel.updateSegmentTiming(timing.selectedSegment,Number(lineStart.inputText)*1000,Number(lineEnd.inputText)*1000) }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            StudioValueField { id: splitTime; Layout.fillWidth: true; label: "分句时刻"; unit: "秒"; editContext: String(timing.selectedSegment); fieldName: "lyricSplitTime"; value: Number(timing.stateData.positionMs || 0)/1000; minimum: Number(timing.line.startMs || 0)/1000; maximum: Number(timing.line.endMs || 0)/1000; enabled: timing.editable }
            StudioValueField { id: splitChar; Layout.fillWidth: true; label: "第几个字后"; editContext: String(timing.selectedSegment); fieldName: "lyricSplitChar"; value: Math.max(1,Math.floor(String(timing.line.text || "").length/2)); minimum: 1; maximum: Math.max(1,String(timing.line.text || "").length-1); decimals: 0; enabled: timing.editable }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            StudioButton { objectName: "lyricSplit"; Layout.fillWidth: true; text: "拆分此句"; enabled: timing.editable && String(timing.line.text || "").length>1; onClicked: timing.studioModel.splitSegment(timing.selectedSegment,Number(splitTime.inputText)*1000,Number(splitChar.inputText)) }
            StudioButton { objectName: "lyricMerge"; Layout.fillWidth: true; text: "合并下一句"; padding: 7; enabled: timing.editable && timing.selectedSegment<(timing.stateData.segments || []).length-1; onClicked: timing.studioModel.mergeSegment(timing.selectedSegment) }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
        Text { text: "逐词 / 字时码"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
        Text { visible: !(timing.line.characters || []).length; Layout.fillWidth: true; text: "这一句没有词或字的时码，可导入包含实际时间信息的 JSON。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
        Repeater {
            model: timing.line.characters || []
            delegate: ColumnLayout {
                required property var modelData
                required property int index
                Layout.fillWidth: true; spacing: 5
                Text { Layout.fillWidth: true; text: (index+1)+" · "+modelData.text; color: Theme.accent; font.pixelSize: 11; elide: Text.ElideRight }
                RowLayout {
                    Layout.fillWidth: true
                    TextField { id: charStart; objectName: "lyricCharStart-"+index; Layout.fillWidth: true; implicitHeight: 29; text: (Number(modelData.startMs)/1000).toFixed(3); color: Theme.text; font.family: Theme.numberFont; font.pixelSize: 10; selectByMouse: true; enabled: timing.editable; Accessible.name: "第"+(index+1)+"词或字开始秒"; validator: DoubleValidator { bottom: 0; top: 100000; decimals: 3; locale: "C" } background: Rectangle { color: Theme.canvas; radius: 7; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
                    TextField { id: charEnd; objectName: "lyricCharEnd-"+index; Layout.fillWidth: true; implicitHeight: 29; text: (Number(modelData.endMs)/1000).toFixed(3); color: Theme.text; font.family: Theme.numberFont; font.pixelSize: 10; selectByMouse: true; enabled: timing.editable; Accessible.name: "第"+(index+1)+"词或字结束秒"; validator: DoubleValidator { bottom: 0; top: 100000; decimals: 3; locale: "C" } background: Rectangle { color: Theme.canvas; radius: 7; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
                    StudioButton { objectName: "lyricCharApply-"+index; text: "应用字时码"; iconName: "check"; iconOnly: true; implicitWidth: 27; implicitHeight: 27; enabled: timing.editable && charStart.acceptableInput && charEnd.acceptableInput; onClicked: timing.studioModel.updateCharacterTiming(timing.selectedSegment,index,Number(charStart.text)*1000,Number(charEnd.text)*1000) }
                }
            }
        }
        Item { Layout.preferredHeight: 8 }
    }
}
