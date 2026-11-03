import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: output
    property var studioModel: null
    property var stateData: ({})
    property var commitEdits: null
    readonly property bool editable: !!studioModel && !stateData.busy
    property bool advancedExpanded: false
    readonly property bool canRender: editable && !!stateData.audioPath && !!stateData.engineReady && ((stateData.segments || []).length>0 || ((stateData.composition || {}).clips || []).length>0 || ((stateData.composition || {}).texts || []).length>0)
    readonly property var settings: stateData.settings || ({})
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        ScrollView {
            id: exportForm
            Layout.fillWidth: true; Layout.fillHeight: true
            contentWidth: availableWidth; clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: exportForm.availableWidth; spacing: 10
        Text { Layout.fillWidth: true; text: "先试听检查，再选择保存位置。导出完成后自动保存。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
        Text { text: "影片格式"; color: Theme.secondary; font.pixelSize: 10 }
        DarkComboBox { objectName: "studioOutputFormat"; Layout.fillWidth: true; implicitHeight: 32; model: ["MP4 · 含歌曲声音", "MOV · 透明通道"]; currentIndex: output.stateData.outputFormat==="mov-alpha" ? 1 : 0; enabled: output.editable; Accessible.name: "影片格式"; onActivated: function(index) { output.studioModel.setOutputFormat(index===0 ? "mp4" : "mov-alpha") } }
        Text { text: "输出尺寸"; color: Theme.secondary; font.pixelSize: 10 }
        DarkComboBox { objectName: "studioOutputResolution"; Layout.fillWidth: true; implicitHeight: 32; model: ["480p · 快速预览", "720p · 高清", "1080p · 全高清"]; currentIndex: Math.max(0,[854,1280,1920].indexOf(Number(output.settings.width || 854))); enabled: output.editable; Accessible.name: "输出尺寸"; onActivated: function(index) { output.studioModel.setSetting("width",[854,1280,1920][index]) } }
        StudioButton { Layout.fillWidth: true; text: output.advancedExpanded ? "收起更多设置 ▴" : "更多导出设置 ▾"; onClicked: output.advancedExpanded=!output.advancedExpanded }
        RowLayout {
            visible: output.advancedExpanded; Layout.fillWidth: true; spacing: 8
            ColumnLayout { Layout.fillWidth: true; spacing: 5; Text { text: "帧率"; color: Theme.secondary; font.pixelSize: 10 } DarkComboBox { objectName: "studioOutputFPS"; Layout.fillWidth: true; implicitHeight: 31; model: ["24 fps","25 fps","30 fps"]; currentIndex: Math.max(0,[24,25,30].indexOf(Number(output.settings.fps || 24))); enabled: output.editable; Accessible.name: "输出帧率"; onActivated: function(index) { output.studioModel.setSetting("fps",[24,25,30][index]) } } }
            StudioValueField { Layout.fillWidth: true; label: "节拍"; unit: "BPM"; fieldName: "studioBPM"; value: Number(output.settings.bpm || 120); minimum: 40; maximum: 240; decimals: 1; enabled: output.editable; onCommitted: function(value) { output.studioModel.setSetting("bpm",value); } }
        }
        Text { visible: output.advancedExpanded; text: "默认背景颜色"; color: Theme.secondary; font.pixelSize: 10 }
        TextField { visible: output.advancedExpanded; objectName: "studioBackgroundColor"; Layout.fillWidth: true; implicitHeight: 31; text: output.settings.backgroundColor || "#101216"; color: Theme.text; selectByMouse: true; enabled: output.editable; Accessible.name: "默认背景颜色"; validator: RegularExpressionValidator { regularExpression: /^#[0-9a-fA-F]{6}$/ } onEditingFinished: if(acceptableInput) output.studioModel.setSetting("backgroundColor",text); background: Rectangle { radius: 8; color: Theme.canvas; border.color: parent.activeFocus ? Theme.focus : "#39424d" } }
        Text { Layout.fillWidth: true; text: output.stateData.outputFormat==="mov-alpha" ? "透明 MOV 导出字幕与独立文字，不混入音轨。" : "导出完整画面、字幕、独立文字及混合音轨。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
        Text { text: "导出歌词"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
        RowLayout { Layout.fillWidth: true; spacing: 3; Repeater { model: ["srt","lrc","json"]; delegate: StudioButton { required property string modelData; Layout.fillWidth: true; implicitWidth: 48; padding: 6; text: modelData.toUpperCase(); enabled: output.editable && (output.stateData.segments || []).length>0; onClicked: output.studioModel.exportSubtitles(modelData) } } }
        Rectangle { Layout.fillWidth: true; height: 1; color: "#333a45" }
        ColumnLayout { visible: output.advancedExpanded; Layout.fillWidth: true; spacing: 10
        Text { text: "时间轴标记"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium }
        TextField { id: markerName; objectName: "studioMarkerName"; Layout.fillWidth: true; implicitHeight: 31; placeholderText: "标记名称"; color: Theme.text; placeholderTextColor: Theme.muted; enabled: output.editable; selectByMouse: true; background: Rectangle { radius: 8; color: Theme.canvas; border.color: markerName.activeFocus ? Theme.focus : "#39424d" } }
        StudioButton { objectName: "studioAddNamedMarker"; Layout.fillWidth: true; text: "在当前位置添加"; enabled: output.editable && !!output.stateData.audioPath; onClicked: output.studioModel.addMarker(Number(output.stateData.positionMs || 0),markerName.text || "标记") }
        Repeater { model: (output.stateData.composition || {}).markers || []; delegate: RowLayout { required property var modelData; Layout.fillWidth: true; Text { Layout.fillWidth: true; text: modelData.label || "标记"; color: Theme.secondary; font.pixelSize: 10; elide: Text.ElideRight } StudioButton { text: "移除标记"; iconName: "close"; iconOnly: true; implicitWidth: 27; implicitHeight: 27; enabled: output.editable; onClicked: output.studioModel.removeMarker(modelData.id) } } }
        }
        Item { Layout.preferredHeight: 8 }
    }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            StudioButton { objectName: "studioGenerateVideo"; Layout.fillWidth: true; text: "导出到…"; tone: "primary"; implicitHeight: 36; enabled: output.canRender; onClicked: if(!output.commitEdits || output.commitEdits()) output.studioModel.exportVideoTo() }
            StudioButton { objectName: "studioSaveVideo"; Layout.fillWidth: true; text: "另存成片…"; iconName: "save"; implicitHeight: 36; enabled: output.editable && !!output.stateData.resultPath; onClicked: output.studioModel.saveVideoAs() }
        }
        Text { Layout.fillWidth: true; visible: !!output.stateData.savedVideoPath && !!output.stateData.resultPath; text: "已保存："+String(output.stateData.savedVideoPath || "").split('/').pop(); color: Theme.green; font.pixelSize: 11; elide: Text.ElideMiddle }
    }
}
