import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: welcome
    property var studioModel
    property var stateData
    signal subtitlesRequested()
    signal apiRequested()
    readonly property bool ready: !!studioModel && !!stateData.audioPath && !stateData.busy
    ColumnLayout {
        anchors.fill: parent; spacing: 0
        RowLayout {
            Layout.fillWidth: true; spacing: 0
            Text { text: "歌词修改"; color: Theme.accent; font.pixelSize: 13; font.bold: true; Layout.margins: 12 }
            Item { Layout.fillWidth: true }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.stroke }
        RowLayout {
            Layout.fillWidth: true; Layout.margins: 10
            Text { text: "歌词片段   0"; color: Theme.secondary; font.pixelSize: 12; Layout.fillWidth: true }
            StudioButton { objectName: "studioWelcomeSubtitles"; iconName: "import"; text: "导入字幕"; tone: "primary"; enabled: welcome.ready; onClicked: welcome.subtitlesRequested() }
        }
        Item { Layout.fillHeight: true }
        StudioIcon { Layout.alignment: Qt.AlignHCenter; Layout.preferredWidth: 31; Layout.preferredHeight: 31; name: "lyrics"; ink: Theme.accent }
        Text { Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 10; text: "暂无字幕"; color: Theme.text; font.weight: Font.Medium }
        StudioButton { objectName: "studioCloudRecognition"; Layout.alignment: Qt.AlignHCenter; text: "识别歌曲歌词"; iconName: "audio"; enabled: welcome.ready; onClicked: welcome.apiRequested() }
        Item { Layout.fillHeight: true }
    }
}
