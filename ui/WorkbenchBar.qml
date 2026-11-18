import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: bar
    required property int page
    required property string liveTone
    required property string liveStatus
    required property bool lyricsHidden
    property alias commandHost: commands
    signal navigate(int page)
    signal toggleLyrics()
    signal settingsRequested(Item anchor)

    objectName: "globalNavigation"
    implicitWidth: 900
    implicitHeight: 42
    color: "#141519"
    RowLayout {
        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 4
        AppIcon { name: "prepare"; ink: Theme.accent; Layout.preferredWidth: 22; Layout.preferredHeight: 22; Accessible.name: "包房战神" }
        Repeater {
            model: [{page:5,title:"字幕制作",icon:"studio"}, {page:0,title:"现场联动",icon:"stage"}, {page:1,title:"歌曲库",icon:"library"}]
            delegate: StudioButton {
                required property var modelData
                objectName: "nav-"+modelData.page
                text: modelData.title; iconName: modelData.icon
                selected: bar.page===modelData.page || (bar.page===6 && modelData.page===5)
                implicitHeight: 30; padding: 7
                onClicked: bar.navigate(modelData.page)
            }
        }
        Rectangle { Layout.preferredWidth: 1; Layout.preferredHeight: 19; Layout.margins: 4; color: Theme.strokeStrong }
        Item { id: commands; Layout.fillWidth: true; Layout.fillHeight: true }
        Rectangle { visible: bar.page!==0; Layout.preferredWidth: 6; Layout.preferredHeight: 6; radius: 3; color: Theme.toneInk(bar.liveTone) }
        StudioButton {
            objectName: "globalLiveStatus"; visible: bar.page!==0
            text: bar.liveStatus; hint: "查看现场歌词来源与客屏输出"; font.pixelSize: 11; padding: 5
            onClicked: bar.navigate(0)
        }
        StudioButton {
            objectName: "globalLyricsToggle"; visible: bar.page!==0
            text: bar.lyricsHidden ? "恢复现场歌词" : "隐藏现场歌词"
            padding: 7; iconOnly: bar.width<1220; iconName: "lyrics"; hint: text+" · Ctrl+H"
            selected: bar.lyricsHidden; onClicked: bar.toggleLyrics()
        }
        StudioButton {
            id: settings; objectName: "appSettingsMenu"; text: "设置与帮助"; iconName: "tools"
            padding: 7; iconOnly: bar.width<1220; selected: [2,3,4,6,7].includes(bar.page)
            onClicked: bar.settingsRequested(settings)
        }
    }
    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.stroke }
}
