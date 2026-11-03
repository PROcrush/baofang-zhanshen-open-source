import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: panel
    property var toolModel: typeof studioTools !== "undefined" ? studioTools : null
    readonly property var toolState: toolModel ? toolModel.state : ({})
    property int toolPage: 0
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 24; spacing: 16
        RowLayout {
            Layout.fillWidth: true
            Repeater {
                model: ["本地素材", "视频字幕修补", "演唱会映射"]
                delegate: ActionButton {
                    required property string modelData
                    required property int index
                    text: modelData; selected: panel.toolPage === index
                    onClicked: panel.toolPage = index
                }
            }
            Item { Layout.fillWidth: true }
            ActionButton { text: "查看结果"; visible: panel.toolPage === 1 ? !!panel.toolState.resultPath : panel.toolPage === 2 && !!panel.toolState.mappingPath; onClicked: panel.toolModel.openResult(panel.toolPage) }
            ActionButton { text: "取消处理"; visible: !!panel.toolState.busy && panel.toolPage === 1; onClicked: panel.toolModel.cancel() }
        }
        Text { Layout.fillWidth: true; text: panel.toolState.status || "选择本地素材开始。"; color: panel.toolState.error ? Theme.red : Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: panel.toolPage
            ColumnLayout {
                spacing: 16
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { text: "选择素材目录"; tone: "primary"; enabled: !!panel.toolModel && !panel.toolState.busy; onClicked: panel.toolModel.chooseFolder() }
                    ActionButton { text: "检查重复内容"; enabled: (panel.toolState.media || []).length > 0 && !panel.toolState.busy; onClicked: panel.toolModel.findDuplicates() }
                    Text { Layout.fillWidth: true; text: panel.toolState.folder || "图片、视频、音频与字幕统一管理"; color: Theme.secondary; font.pixelSize: 11; elide: Text.ElideMiddle }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                    model: panel.toolState.media || []; reuseItems: true; spacing: 2
                    ScrollBar.vertical: ScrollBar {}
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        width: ListView.view.width; height: 44; color: index % 2 ? Theme.canvas : Theme.surface; radius: 9
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14
                            Text { text: ({audio:"音频",video:"视频",image:"图片",subtitle:"字幕"})[modelData.kind] || modelData.kind; color: Theme.accent; font.pixelSize: 11; Layout.preferredWidth: 48 }
                            Text { Layout.fillWidth: true; text: modelData.relativePath || modelData.path; color: Theme.text; font.pixelSize: 12; elide: Text.ElideMiddle }
                            Text { text: (Number(modelData.size || 0)/1048576).toFixed(2)+" MB"; color: Theme.secondary; font.pixelSize: 11 }
                        }
                    }
                }
                Text { Layout.fillWidth: true; text: (panel.toolState.duplicates || []).length ? "重复建议 " + panel.toolState.duplicates.length + " 组，原文件均保留。" : "目录只读索引，重复检查提供整理建议。"; color: Theme.secondary; font.pixelSize: 11 }
            }
            ColumnLayout {
                spacing: 18
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { text: "选择视频"; tone: "primary"; enabled: !!panel.toolModel && !panel.toolState.busy; onClicked: panel.toolModel.chooseVideo() }
                    Text { Layout.fillWidth: true; text: panel.toolState.videoTitle || "选择含固定位置字幕的视频"; color: Theme.text; font.pixelSize: 13; elide: Text.ElideRight }
                    StatusPill { text: panel.toolState.videoWidth ? panel.toolState.videoWidth+" × "+panel.toolState.videoHeight : "待载入"; tone: "muted" }
                }
                Text { text: "字幕区域"; color: Theme.text; font.pixelSize: 18; font.weight: Font.Medium }
                Flow {
                    Layout.fillWidth: true; Layout.preferredHeight: childrenRect.height; spacing: 16
                    Repeater {
                        model: [{id:"x",label:"左侧位置"},{id:"y",label:"顶部位置"},{id:"width",label:"区域宽度"},{id:"height",label:"区域高度"}]
                        delegate: Column {
                            required property var modelData
                            spacing: 7
                            Text { text: modelData.label; color: Theme.secondary; font.pixelSize: 12 }
                            SpinBox { from: 0; to: 10000; editable: true; value: Number((panel.toolState.roi || ({}))[modelData.id] || 0); enabled: !!panel.toolState.videoPath && !panel.toolState.busy; onValueModified: panel.toolModel.setROI(modelData.id, value); Accessible.name: modelData.label }
                        }
                    }
                }
                Text { Layout.fillWidth: true; text: "按视频像素设置固定区域。先生成 5 秒样片检查修补效果，原视频保留。"; color: Theme.secondary; font.pixelSize: 12; wrapMode: Text.Wrap }
                ActionButton { text: "生成去字幕样片"; tone: "primary"; enabled: !!panel.toolState.videoPath && !panel.toolState.busy; onClicked: panel.toolModel.previewRemoval() }
                Item { Layout.fillHeight: true }
            }
            ColumnLayout {
                spacing: 18
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { text: "演唱会歌词"; enabled: !!panel.toolModel && !panel.toolState.busy; onClicked: panel.toolModel.chooseLyrics("source") }
                    Text { Layout.fillWidth: true; text: panel.toolState.sourceLyrics || "选择来源视频对应的 SRT / LRC"; color: Theme.secondary; font.pixelSize: 12; elide: Text.ElideMiddle }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { text: "目标歌曲歌词"; enabled: !!panel.toolModel && !panel.toolState.busy; onClicked: panel.toolModel.chooseLyrics("target") }
                    Text { Layout.fillWidth: true; text: panel.toolState.targetLyrics || "选择目标 Remix 的歌词时间轴"; color: Theme.secondary; font.pixelSize: 12; elide: Text.ElideMiddle }
                }
                RowLayout {
                    Layout.fillWidth: true
                    ActionButton { text: "生成段落映射"; tone: "primary"; enabled: !!panel.toolState.sourceLyrics && !!panel.toolState.targetLyrics && !panel.toolState.busy; onClicked: panel.toolModel.suggestPairing() }
                    Text { Layout.fillWidth: true; text: "候选匹配需复核演唱版本和速度，映射结果可查看。"; color: Theme.secondary; font.pixelSize: 11; wrapMode: Text.Wrap }
                }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
                    model: panel.toolState.pairingRows || []
                    delegate: Rectangle {
                        required property var modelData
                        width: ListView.view.width; height: 52; radius: 12; color: Theme.surface
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 12
                            Text { text: Number(modelData.timelineStart || 0).toFixed(2)+" s"; color: Theme.accent; font.family: Theme.numberFont; font.pixelSize: 12; Layout.preferredWidth: 100 }
                            Text { Layout.fillWidth: true; text: modelData.kind === "lipSyncCandidate" ? "可匹配的演唱段落" : "此段需要补镜或复核"; color: Theme.text; font.pixelSize: 12 }
                            Text { text: modelData.playbackRate ? Number(modelData.playbackRate).toFixed(2)+"×" : "待补镜"; color: Theme.secondary; font.pixelSize: 11 }
                        }
                    }
                }
            }
        }
    }
}
