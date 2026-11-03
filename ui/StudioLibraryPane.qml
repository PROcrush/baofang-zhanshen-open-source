import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Item {
    id: library
    property var studioModel: null
    property var stateData: ({})
    property var commitEdits: null
    property int selectedPage: 1
    property int selectedSegment: 0
    property string selectedObject: "lyrics"
    property string selectedSourceId: ""
    property int selectedConcertRow: 0
    readonly property bool editable: !!studioModel && !stateData.busy
    readonly property var sources: stateData.materialSources || []
    signal pageSelected(int index)
    signal objectSelected(string kind)
    signal lineSelected(int index)
    signal sourceSelected(string id)
    signal concertSelected(int index)
    function sourceTitle(source) { return source.title || source.label || String((source.identity || {}).path || source.path || "素材").split('/').pop(); }
    function clock(ms) { var seconds=Math.floor(Number(ms || 0)/1000); return Math.floor(seconds/60).toString().padStart(2,"0")+":"+(seconds%60).toString().padStart(2,"0"); }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 14; spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 1
            Repeater { model: ["歌曲素材","歌词","演唱会"]; delegate: StudioButton { required property string modelData; required property int index; Layout.fillWidth: true; implicitWidth: 42; implicitHeight: 29; padding: 5; text: modelData; hint: index===2 ? "演唱会视频与来源字幕对齐" : ""; selected: library.selectedPage===index; onClicked: library.pageSelected(index) } }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 110; radius: 8; color: Theme.elevated
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 7
                RowLayout {
                    StudioIcon { Layout.preferredWidth: 23; Layout.preferredHeight: 23; name: "audio"; ink: Theme.accent }
                    ColumnLayout { Layout.fillWidth: true; spacing: 3
                        Text { text: "歌曲音频"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium }
                        Text { Layout.fillWidth: true; text: library.stateData.audioTitle || "还没有选择歌曲"; color: Theme.secondary; font.pixelSize: 11; elide: Text.ElideMiddle }
                    }
                }
                RowLayout { Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: library.stateData.audioPath ? library.clock(library.stateData.durationMs)+" · "+((library.stateData.segments || []).length ? (library.stateData.segments || []).length+"句歌词" : "待添加歌词") : "支持 MP3、WAV、M4A 等"; color: (library.stateData.segments || []).length ? Theme.green : Theme.muted; font.pixelSize: 10; elide: Text.ElideRight }
                    StudioButton { objectName: "studioChooseAudio"; text: library.stateData.audioPath ? "制作下一首" : "选择歌曲"; tone: library.stateData.audioPath ? "neutral" : "primary"; implicitHeight: 30; enabled: library.editable; onClicked: if(!library.commitEdits || library.commitEdits()) library.studioModel.chooseAudio() }
                }
            }
        }
        RowLayout {
            visible: !!library.stateData.audioPath && !(library.stateData.segments || []).length; Layout.fillWidth: true; spacing: 5
            StudioButton { objectName: "studioLibrarySubtitles"; Layout.fillWidth: true; text: "导入歌词"; tone: "primary"; implicitHeight: 32; enabled: library.editable; onClicked: { library.pageSelected(1); library.studioModel.chooseSubtitles(); } }
            StudioButton { objectName: "studioLibraryRecognize"; Layout.fillWidth: true; text: "本地识别"; implicitHeight: 32; enabled: library.editable; onClicked: library.studioModel.recognizeLocal() }
        }
        StackLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; currentIndex: library.selectedPage
            ScrollView {
                id: materialsScroll
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                width: materialsScroll.availableWidth; spacing: 10
                Button {
                    id: backgroundImport
                    objectName: "studioChooseBackground"
                    Layout.fillWidth: true; Layout.preferredHeight: library.stateData.backgroundPath ? 96 : 104
                    enabled: library.editable && !!library.stateData.audioPath; hoverEnabled: true; opacity: enabled ? 1 : 0.5
                    Accessible.name: library.stateData.backgroundPath ? "更换背景" : "导入背景图片或视频"
                    onClicked: library.studioModel.chooseBackground()
                    background: Rectangle { radius: 8; color: backgroundImport.hovered ? "#383142" : "#2c2735"; border.color: backgroundImport.activeFocus ? Theme.focus : "#4c405b" }
                    contentItem: Item {
                        Image { anchors.fill: parent; visible: !!library.stateData.backgroundIsImage; source: visible ? library.stateData.backgroundUrl || "" : ""; fillMode: Image.PreserveAspectCrop }
                        Rectangle { anchors.fill: parent; color: "#aa211c2a"; visible: !!library.stateData.backgroundIsImage }
                        Column { anchors.centerIn: parent; width: parent.width-16; spacing: 5
                            StudioIcon { anchors.horizontalCenter: parent.horizontalCenter; width: 23; height: 23; name: "background"; ink: Theme.accent }
                            Text { width: parent.width; text: library.stateData.backgroundPath ? "更换背景画面" : "点击导入背景"; color: Theme.text; font.pixelSize: 13; font.weight: Font.Medium; horizontalAlignment: Text.AlignHCenter }
                            Text { width: parent.width; text: library.stateData.backgroundTitle || "选择视频或图片"; color: Theme.secondary; font.pixelSize: 10; horizontalAlignment: Text.AlignHCenter; elide: Text.ElideMiddle }
                        }
                    }
                }
                RowLayout { Layout.fillWidth: true; Text { text: "分镜素材"; color: Theme.text; font.pixelSize: 12; font.weight: Font.Medium } Item { Layout.fillWidth: true } StudioButton { objectName: "studioChooseMaterial"; text: "导入素材"; iconName: "import"; implicitHeight: 28; enabled: library.editable && !!library.stateData.audioPath; onClicked: library.studioModel.chooseMaterial() } }
                TextField { id: materialSearch; objectName: "studioMaterialSearch"; visible: library.sources.length>3; Layout.fillWidth: true; implicitHeight: 30; placeholderText: "搜索素材"; placeholderTextColor: Theme.muted; color: Theme.text; font.pixelSize: 11; selectByMouse: true; background: Rectangle { radius: 6; color: Theme.canvas; border.color: materialSearch.activeFocus ? Theme.focus : Theme.stroke } }
                ListView {
                    id: sourceList
                    objectName: "studioMaterialList"
                    Layout.fillWidth: true; Layout.preferredHeight: Math.max(66,count*58); interactive: false; clip: true; spacing: 4
                    model: library.sources.filter(function(source){return !materialSearch.text || library.sourceTitle(source).toLowerCase().indexOf(materialSearch.text.toLowerCase())>=0;})
                    ScrollBar.vertical: ScrollBar { width: 5 }
                    delegate: Button {
                        id: sourceButton
                        required property var modelData
                        width: sourceList.width; height: 54
                        objectName: "studioMaterial-"+(modelData.sourceId || modelData.id)
                        text: library.sourceTitle(modelData); hoverEnabled: true; activeFocusOnTab: true
                        Accessible.name: text
                        onClicked: library.sourceSelected(modelData.sourceId || modelData.id)
                        background: Rectangle { radius: 6; color: library.selectedSourceId===(sourceButton.modelData.sourceId || sourceButton.modelData.id) && library.selectedObject==="source" ? Theme.accentSoft : sourceButton.hovered ? Theme.elevated : "transparent"; border.color: sourceButton.activeFocus ? Theme.focus : "transparent" }
                        contentItem: RowLayout {
                            spacing: 9
                            Rectangle { Layout.preferredWidth: 42; Layout.preferredHeight: 34; radius: 4; color: Theme.canvas; clip: true
                                Image { anchors.fill: parent; visible: sourceButton.modelData.kind==="image"; source: visible ? sourceButton.modelData.url || "" : ""; fillMode: Image.PreserveAspectCrop }
                                StudioIcon { visible: sourceButton.modelData.kind!=="image"; anchors.centerIn: parent; width: 20; height: 20; name: sourceButton.modelData.kind==="audio" ? "audio" : "film"; ink: Theme.secondary }
                            }
                            ColumnLayout { Layout.fillWidth: true; spacing: 4
                                Text { Layout.fillWidth: true; text: sourceButton.text; color: Theme.text; font.pixelSize: 11; elide: Text.ElideMiddle }
                                Text { text: sourceButton.modelData.kind==="image" ? "图片" : library.clock(sourceButton.modelData.durationMs)+" · "+(sourceButton.modelData.kind==="audio" ? "音频" : "视频"); color: Theme.muted; font.pixelSize: 10 }
                            }
                        }
                    }
                    Text { visible: sourceList.count===0; anchors.fill: parent; text: "需要多段画面时，导入分镜素材。
选择素材后，点击“加入画面轨道”。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.5 }
                }
                GridLayout {
                    Layout.fillWidth: true; columns: 2; columnSpacing: 4; rowSpacing: 4
                    StudioButton { objectName: "studioAddText"; Layout.fillWidth: true; text: "添加文字"; iconName: "lyrics"; implicitHeight: 30; enabled: library.editable && !!library.stateData.audioPath; onClicked: { library.studioModel.addText(); library.objectSelected("text"); } }
                    StudioButton { objectName: "studioAddColor"; Layout.fillWidth: true; text: "纯色分镜"; iconName: "background"; implicitHeight: 30; enabled: library.editable && !!library.stateData.audioPath; onClicked: { library.studioModel.addColorClip(); library.objectSelected("clip"); } }
                    StudioButton { objectName: "studioAutoStoryboard"; Layout.columnSpan: 2; Layout.fillWidth: true; text: "按歌词自动分镜"; iconName: "film"; implicitHeight: 30; enabled: library.editable && (library.stateData.segments || []).length>0; onClicked: { library.studioModel.autoStoryboard(); library.objectSelected("clip"); } }
                }
                }
            }
            ColumnLayout {
                spacing: 6
                Text { text: (library.stateData.segments || []).length+" 句 · 点击定位"; color: Theme.muted; font.pixelSize: 10 }
                ListView {
                    id: lyricList
                    objectName: "studioLyricList"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 3
                    model: library.stateData.segments || []
                    currentIndex: library.selectedSegment
                    ScrollBar.vertical: ScrollBar { width: 4 }
                    delegate: Button {
                        id: lyricRow
                        required property var modelData
                        required property int index
                        width: lyricList.width; height: 62
                        objectName: "studioLyricRow-"+index
                        hoverEnabled: true; activeFocusOnTab: true
                        Accessible.name: "第"+(index+1)+"句："+modelData.text
                        onClicked: library.lineSelected(index)
                        Keys.onUpPressed: library.lineSelected(Math.max(0,index-1))
                        Keys.onDownPressed: library.lineSelected(Math.min(lyricList.count-1,index+1))
                        background: Rectangle { radius: 8; color: library.selectedSegment===lyricRow.index && library.selectedObject==="lyrics" ? Theme.accentSoft : lyricRow.hovered ? "#272e37" : "transparent"; border.color: lyricRow.activeFocus ? Theme.focus : "transparent" }
                        contentItem: ColumnLayout {
                            spacing: 4
                            RowLayout { Layout.fillWidth: true; Text { text: (lyricRow.index+1).toString().padStart(2,"0"); color: library.selectedSegment===lyricRow.index ? Theme.accent : Theme.muted; font.family: Theme.numberFont; font.pixelSize: 10 } Item { Layout.fillWidth: true } Text { text: library.clock(lyricRow.modelData.startMs); color: Theme.muted; font.family: Theme.numberFont; font.pixelSize: 9 } }
                            Text { Layout.fillWidth: true; text: lyricRow.modelData.text; color: library.selectedSegment===lyricRow.index ? Theme.text : Theme.secondary; font.pixelSize: 12; elide: Text.ElideRight }
                        }
                    }
                    Text { visible: lyricList.count===0; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.topMargin: 25; text: "导入字幕后，\n在这里逐句修改。"; color: Theme.muted; font.pixelSize: 11; lineHeight: 1.5; horizontalAlignment: Text.AlignHCenter }
                }
                StudioButton { Layout.fillWidth: true; text: "导入歌词字幕"; iconName: "lyrics"; implicitHeight: 29; enabled: library.editable && !!library.stateData.audioPath; onClicked: library.studioModel.chooseSubtitles() }
            }
            ScrollView {
                id: concertScroll
                contentWidth: availableWidth; clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: concertScroll.availableWidth; spacing: 8
                    StudioButton { objectName: "concertChooseVideo"; Layout.fillWidth: true; text: "1. 导入演唱会视频"; iconName: "film"; implicitHeight: 30; enabled: library.editable && !!library.stateData.audioPath; onClicked: library.studioModel.chooseConcertVideo() }
                    StudioButton { objectName: "concertChooseLyrics"; Layout.fillWidth: true; text: "2. 导入视频对应字幕"; iconName: "lyrics"; implicitHeight: 30; enabled: library.editable && !!library.stateData.concertSourceId; onClicked: library.studioModel.chooseConcertSubtitles() }
                    StudioButton { objectName: "concertAnalyzeMotion"; Layout.fillWidth: true; text: "检查画面运动"; implicitHeight: 30; enabled: library.editable && !!library.stateData.concertSourceId; onClicked: library.studioModel.analyzeConcertVideo() }
                    Text { Layout.fillWidth: true; visible: Number((library.stateData.concertAnalysis || {}).sampledFrames || 0)>0; text: "已检查 "+Number((library.stateData.concertAnalysis || {}).sampledFrames || 0)+" 帧\n静止候选 "+((library.stateData.concertAnalysis || {}).stationaryIntervals || []).length+" · 镜头变化 "+((library.stateData.concertAnalysis || {}).sceneCutsMs || []).length; color: Theme.secondary; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
                    Text { Layout.fillWidth: true; visible: !!(library.stateData.concertAnalysis || {}).notice; text: (library.stateData.concertAnalysis || {}).notice || ""; color: Theme.muted; font.pixelSize: 9; wrapMode: Text.Wrap; lineHeight: 1.4 }
                    StudioCatalogPicker { Layout.fillWidth: true; label: "未匹配段补镜"; controlName: "concertGapSource"; rows: [{id:"",label:"纯色补镜"}].concat(library.sources.filter(function(source){return source.kind!=="audio";}).map(function(source){return {id:source.sourceId || source.id,label:library.sourceTitle(source)};})); selectedId: library.stateData.concertGapSourceId || ""; enabled: library.editable; onChosen: function(id) { library.studioModel.setConcertGapSource(id); } }
                    StudioButton { objectName: "concertGeneratePlan"; Layout.fillWidth: true; text: "3. 生成编排候选"; implicitHeight: 30; enabled: library.editable && !!library.stateData.concertSourceId && !!library.stateData.concertLyricsPath; onClicked: { library.objectSelected("concert"); library.studioModel.generateConcertPlan(); } }
                    Text { Layout.fillWidth: true; text: library.stateData.concertStatus || "先导入演唱会来源。"; color: Theme.muted; font.pixelSize: 10; wrapMode: Text.Wrap; lineHeight: 1.4 }
                    Repeater {
                        model: library.stateData.concertRows || []
                        delegate: Button {
                            id: candidate
                            required property var modelData
                            required property int index
                            objectName: "concertCandidate-"+index
                            Layout.fillWidth: true; implicitHeight: 45
                            hoverEnabled: true; activeFocusOnTab: true; Accessible.name: "演唱会候选"+(index+1)
                            onClicked: library.concertSelected(index)
                            background: Rectangle { radius: 8; color: library.selectedConcertRow===candidate.index && library.selectedObject==="concert" ? "#323c4b" : candidate.hovered ? "#272e37" : "transparent"; border.color: candidate.activeFocus ? Theme.focus : "transparent" }
                            contentItem: Column { spacing: 4; Text { text: library.clock(candidate.modelData.startMs)+" · "+(candidate.modelData.purpose==="paired" ? "匹配" : "补镜"); color: Theme.text; font.pixelSize: 10 } Text { text: candidate.modelData.reviewed ? "已人工复核" : "待人工复核"; color: candidate.modelData.reviewed ? Theme.green : Theme.amber; font.pixelSize: 9 } }
                        }
                    }
                }
            }
        }
    }
}
