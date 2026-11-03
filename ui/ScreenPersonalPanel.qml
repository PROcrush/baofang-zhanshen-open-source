import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: personal
    property var libraryData: ({phrases: [], history: []})
    property var draftLayers: ({})
    property int section: 0
    property string editingId: ""
    property var pending: ({})
    property string message: ""
    signal phraseSelected(var phrase)
    signal historySelected(var entry)
    signal profilePrepared(var profile)
    signal profileApplied()
    signal profileCancelled()
    signal requestTop()
    spacing: 7
    onSectionChanged: requestTop()
    function editCurrent(text) { section = 0; editingId = ""; phraseLabel.text = ""; phraseBody.text = text; }
    function applyPending() { if (pending.layers && controller.applyScreenProfile(pending.layers, pending.phrases)) { pending = ({}); profileApplied(); } }
    function cancelPending() { pending = ({}); profileCancelled(); }
    RowLayout {
        Layout.fillWidth: true; spacing: 0
        TextTab { objectName: "screenPersonalPhrases"; Layout.fillWidth: true; text: "常用语"; selected: personal.section === 0; onClicked: personal.section = 0 }
        TextTab { objectName: "screenPersonalHistory"; Layout.fillWidth: true; text: "历史"; selected: personal.section === 1; onClicked: personal.section = 1 }
        TextTab { objectName: "screenPersonalProfile"; Layout.fillWidth: true; text: "配置包"; selected: personal.section === 2; onClicked: personal.section = 2 }
    }
    ColumnLayout {
        visible: personal.section === 0; Layout.fillWidth: true; spacing: 6
        Text { text: (personal.editingId ? "编辑常用语" : "新增常用语") + " · 最多 20 条"; color: Theme.secondary; font.pixelSize: 11 }
        TextField { id: phraseLabel; objectName: "screenPhraseLabel"; Layout.fillWidth: true; implicitHeight: 30; maximumLength: 16; placeholderText: "短标题，如：我的开场"; color: Theme.text; placeholderTextColor: Theme.muted; selectByMouse: true; background: Rectangle { color: Theme.sunken; radius: 5; border.color: Theme.strokeStrong } }
        ScrollView {
            Layout.fillWidth: true; Layout.preferredHeight: 78
            TextArea { id: phraseBody; objectName: "screenPhraseBody"; placeholderText: "常用语正文，可写 {姓名}"; textFormat: TextEdit.PlainText; color: Theme.text; placeholderTextColor: Theme.muted; wrapMode: TextEdit.Wrap; selectByMouse: true; font.pixelSize: 12; background: Rectangle { color: Theme.sunken; radius: 5; border.color: Theme.strokeStrong } }
        }
        Text { Layout.fillWidth: true; text: "{姓名} 在使用时填写，保存不会上屏"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
        RowLayout {
            Layout.fillWidth: true
            StudioButton { objectName: "screenPhraseSave"; Layout.fillWidth: true; text: personal.editingId ? "保存修改" : "保存常用语"; enabled: !!phraseLabel.text.trim() && !!phraseBody.text.trim(); implicitHeight: 28; onClicked: { if (controller.saveScreenPhrase(personal.editingId, phraseLabel.text, phraseBody.text)) { personal.editingId = ""; phraseLabel.text = ""; phraseBody.text = ""; personal.message = "常用语已保存"; } } }
            StudioButton { text: "新增"; implicitHeight: 28; onClicked: { personal.editingId = ""; phraseLabel.text = ""; phraseBody.text = ""; } }
        }
        Repeater {
            model: personal.libraryData.phrases || []
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true; spacing: 3
                Text { Layout.fillWidth: true; text: modelData.label + "：" + modelData.text; textFormat: Text.PlainText; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
                RowLayout {
                    Layout.fillWidth: true; spacing: 3
                    StudioButton { objectName: "screenPhraseUse-" + modelData.id; text: "填入草稿"; implicitHeight: 26; font.pixelSize: 11; onClicked: personal.phraseSelected(modelData) }
                    StudioButton { objectName: "screenPhraseEdit-" + modelData.id; text: "编辑"; implicitHeight: 26; font.pixelSize: 11; onClicked: { personal.editingId = modelData.id; phraseLabel.text = modelData.label; phraseBody.text = modelData.text; } }
                    StudioButton { objectName: "screenPhraseDelete-" + modelData.id; text: "删除"; implicitHeight: 26; font.pixelSize: 11; onClicked: { if (controller.deleteScreenPhrase(modelData.id) && personal.editingId === modelData.id) personal.editingId = ""; } }
                }
            }
        }
    }
    ColumnLayout {
        visible: personal.section === 1; Layout.fillWidth: true; spacing: 8
        Text { Layout.fillWidth: true; text: "最近 10 次成功发送 · 仅保存在本机\n选择后填入草稿，核对 / 改名再发布"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap }
        Repeater {
            model: personal.libraryData.history || []
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true; spacing: 4
                Text { text: modelData.at.slice(5,16).replace("T", " ") + (modelData.source === "guest" ? " · 祝福" : " · 喊话"); color: Theme.muted; font.pixelSize: 10 }
                Text { Layout.fillWidth: true; text: modelData.row.text; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 12; wrapMode: Text.Wrap }
                StudioButton { objectName: "screenHistoryUse-" + modelData.id; text: "编辑后重发…"; implicitHeight: 28; font.pixelSize: 11; onClicked: personal.historySelected(modelData) }
            }
        }
        Text { visible: !(personal.libraryData.history || []).length; text: "发送后记录在这里"; color: Theme.muted; font.pixelSize: 11 }
    }
    ColumnLayout {
        visible: personal.section === 2; Layout.fillWidth: true; spacing: 8
        Text { Layout.fillWidth: true; text: "带走 Logo、布局 / 配色和常用语。\n现场文字、历史、歌曲和 Key 不导出。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
        StudioButton { objectName: "screenProfileExport"; Layout.fillWidth: true; text: "导出我的 DJ 配置…"; implicitHeight: 30; onClicked: controller.exportScreenProfile(personal.draftLayers, "") }
        StudioButton { objectName: "screenProfileImport"; Layout.fillWidth: true; text: "导入配置，先预览…"; implicitHeight: 30; onClicked: { var value = controller.prepareScreenProfile(""); if (value.layers) { personal.pending = value; personal.profilePrepared(value); } } }
        ColumnLayout {
            visible: !!personal.pending.layers; Layout.fillWidth: true; spacing: 7
            Text { Layout.fillWidth: true; text: personal.pending.fileName || ""; textFormat: Text.PlainText; color: Theme.text; font.pixelSize: 11; wrapMode: Text.Wrap }
            Text { Layout.fillWidth: true; text: "常用语 " + (personal.pending.phrases || []).length + " 条 · " + (personal.pending.logoImageUrl ? "标识见左侧预览" : "无标识，将隐藏原 Logo"); color: Theme.amber; font.pixelSize: 10; wrapMode: Text.Wrap }
        }
    }
    Text { Layout.fillWidth: true; visible: !!personal.message; text: personal.message; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
}
