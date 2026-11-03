import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: welcome
    property var studioModel: null
    property var stateData: ({})
    readonly property bool hasAudio: !!stateData.audioPath
    readonly property bool compactContent: height<330 || width<520
    readonly property bool editable: !!studioModel && !stateData.busy
    signal subtitlesRequested()
    signal recognitionRequested()
    signal liveRequested()
    signal apiRequested()
    ColumnLayout {
        anchors.centerIn: parent
        width: Math.min(welcome.hasAudio ? 470 : 560,parent.width-40)
        spacing: welcome.compactContent ? 12 : 20
        Text { visible:!welcome.hasAudio; text:"制作歌词视频"; color:Theme.accent; font.pixelSize:13; font.weight:Font.Medium }
        Text {
            Layout.fillWidth:true
            text:welcome.hasAudio ? "接下来，给歌曲添加歌词" : "先选一首歌"
            color:Theme.text; font.pixelSize:welcome.compactContent ? 22 : 30; font.weight:Font.Medium
            horizontalAlignment:welcome.hasAudio ? Text.AlignHCenter : Text.AlignLeft; wrapMode:Text.Wrap
        }
        Text {
            Layout.fillWidth:true
            text:welcome.hasAudio ? "有字幕直接导入；没有字幕，可选择本地或云端识别。" : "选择歌曲、添加歌词，就能直接试听和导出。\n背景和模板随时可以调整。"
            color:Theme.secondary; font.pixelSize:13; lineHeight:1.5
            horizontalAlignment:welcome.hasAudio ? Text.AlignHCenter : Text.AlignLeft; wrapMode:Text.Wrap
        }
        RowLayout {
            visible:!welcome.hasAudio; spacing:12; Layout.topMargin:4; Layout.bottomMargin:4
            Repeater {
                model:["歌曲","歌词","画面","视频"]
                delegate:RowLayout {
                    required property string modelData
                    required property int index
                    spacing:12
                    Text { visible:index>0; text:"→"; color:Theme.muted; font.pixelSize:13 }
                    Text { text:modelData; color:index===0 ? Theme.text : Theme.secondary; font.pixelSize:13 }
                }
            }
        }
        RowLayout {
            Layout.alignment:welcome.hasAudio ? Qt.AlignHCenter : Qt.AlignLeft; spacing:10
            StudioButton { objectName:"studioStartAudio"; visible:!welcome.hasAudio; text:"选择歌曲"; iconName:"audio"; tone:"primary"; implicitWidth:146; implicitHeight:44; enabled:welcome.editable; onClicked:welcome.studioModel.chooseAudio() }
            ActionButton { objectName:"studioWelcomeOpen"; visible:!welcome.hasAudio; text:"打开已有工程"; implicitHeight:44; enabled:welcome.editable; onClicked:welcome.studioModel.openProject() }
            StudioButton { objectName:"studioWelcomeSubtitles"; visible:welcome.hasAudio; text:"导入歌词字幕"; iconName:"lyrics"; tone:"primary"; implicitHeight:40; enabled:welcome.editable; onClicked:welcome.subtitlesRequested() }
            StudioButton { objectName:"studioWelcomeRecognize"; visible:welcome.hasAudio; text:"本地识别歌词"; implicitHeight:40; enabled:welcome.editable; onClicked:welcome.recognitionRequested() }
        }
        Rectangle { visible:!welcome.hasAudio; Layout.fillWidth:true; Layout.preferredHeight:1; Layout.topMargin:8; color:Theme.stroke }
        Text {
            visible: !welcome.hasAudio && (welcome.stateData.recentProjects || []).length>0
            text: "继续最近的工程"; color: Theme.secondary; font.pixelSize: 12
        }
        Repeater {
            model: welcome.hasAudio ? [] : (welcome.stateData.recentProjects || []).slice(0,3)
            delegate: StudioButton {
                required property var modelData
                required property int index
                objectName: "studioRecentProject-"+index
                Layout.fillWidth: true; implicitHeight: 36
                text: modelData.title + (modelData.available ? "" : "（文件不可用）")
                iconName: "project"; hint: modelData.path
                textAlignment: Text.AlignLeft
                enabled: welcome.editable && modelData.available
                onClicked: welcome.studioModel.openProject(modelData.path)
            }
        }
        RowLayout {
            visible:!welcome.hasAudio; Layout.fillWidth:true; spacing:12
            ColumnLayout {
                Layout.fillWidth:true; spacing:4
                Text { text:"先用示例试一遍"; color:Theme.text; font.pixelSize:13 }
                Text { Layout.fillWidth:true; text:"内含歌曲、歌词和背景，可自由修改。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
            }
            StudioButton { objectName:"studioOpenExample"; text:"打开示例体验"; iconName:"play"; selected:true; implicitHeight:38; enabled:welcome.editable; onClicked:welcome.studioModel.openExampleProject() }
        }
        RowLayout {
            visible:!welcome.hasAudio; Layout.fillWidth:true; spacing:8
            Text { Layout.fillWidth:true; text:"现场跟随歌词，无需先生成视频。"; color:Theme.muted; font.pixelSize:12; wrapMode:Text.Wrap }
            StudioButton { objectName:"studioWelcomeLive"; text:"进入现场"; iconName:"stage"; implicitHeight:34; onClicked:welcome.liveRequested() }
        }
        Text { visible:welcome.hasAudio; Layout.fillWidth:true; text:"支持 SRT / LRC / JSON，本地识别不会上传歌曲。"; color:Theme.muted; font.pixelSize:11; horizontalAlignment:Text.AlignHCenter; wrapMode:Text.Wrap }
        StudioButton { objectName:"studioCloudRecognition"; visible:welcome.hasAudio; Layout.alignment:Qt.AlignHCenter; text:"使用国内云端模型"; implicitHeight:34; enabled:!welcome.stateData.busy; onClicked:welcome.apiRequested() }
    }
}
